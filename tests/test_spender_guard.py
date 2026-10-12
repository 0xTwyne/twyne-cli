"""Incident 95524: no code path may approve, permit or authorize an EVC."""

import re
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from twyne_cli.spender_guard import (
    KNOWN_EVC_ADDRESSES,
    UnsafeSpenderError,
    assert_safe_spender,
    evc_addresses,
)

TWYNE_EVC = "0xef39D6493884C4C84D38a4bFF879Ce16CEdE702a"
REQUIRED_EVCS = [
    TWYNE_EVC,
    "0xFF06F28cf0c44Cf1E8F03E6835bB2F3a2a752C5C",  # MegaETH Twyne EVC
    "0xa6Eb5d620Cac284242b03bF498375922882A7876",  # whitelist-launch Twyne EVC
    "0x0C9a3dd6b8F28529d72d7f9cE918D493519EE383",  # Euler EVC
]
SAFE = "0x335AB81f1C3d9f72639004d3e982902458CF29b3"  # Euler Leverage Operator
REPO = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("addr", REQUIRED_EVCS + sorted(KNOWN_EVC_ADDRESSES))
@pytest.mark.parametrize("case", [str, str.lower])
def test_guard_rejects_every_evc(addr, case):
    with pytest.raises(UnsafeSpenderError, match="EVC"):
        assert_safe_spender(case(addr))


def test_guard_rejects_object_with_evc_address():
    with pytest.raises(UnsafeSpenderError):
        assert_safe_spender(MagicMock(address=TWYNE_EVC), "operator")


def test_registry_evcs_are_banned():
    import json
    for f in (REPO / "src/twyne_cli/addresses").glob("*.json"):
        data = json.loads(f.read_text())
        for key in ("evc", "eulerEvc"):
            if key in data:
                assert data[key].lower() in evc_addresses(), f"{f.name}:{key}"


def test_env_override_evc_is_banned(monkeypatch):
    other = "0x1111111111111111111111111111111111111111"
    assert_safe_spender(other)
    monkeypatch.setenv("TWYNE_EVC", other)
    with pytest.raises(UnsafeSpenderError):
        assert_safe_spender(other)


@pytest.mark.parametrize("bad", ["", None, "0x1234", "not-an-address"])
def test_guard_fails_closed_on_invalid(bad):
    with pytest.raises(UnsafeSpenderError):
        assert_safe_spender(bad)


def test_guard_allows_normal_spender():
    assert assert_safe_spender(SAFE) == SAFE


@pytest.mark.parametrize("evc", [TWYNE_EVC, TWYNE_EVC.lower()])
def test_ensure_allowance_raises_before_building_tx(evc):
    from twyne_cli.transactions import ensure_allowance

    erc20 = MagicMock()
    with patch("twyne_cli.contracts.erc20", erc20):
        with pytest.raises(UnsafeSpenderError):
            ensure_allowance("0x4c9EDD5852cd905f086C759E8383e09bff1E68B3", evc, 10**18,
                             MagicMock(), skip_confirm=True, max_approve=True)
    erc20.assert_not_called()


def test_batch_token_approve_rejects_evc():
    from twyne_cli.batch import _encode_token_op

    token = MagicMock()
    token.decimals.return_value = 18
    with patch("twyne_cli.batch.erc20", return_value=token):
        with pytest.raises(UnsafeSpenderError):
            _encode_token_op("approve", {"token": SAFE, "amount": "1", "spender": TWYNE_EVC}, SAFE)
    token.approve.encode_input.assert_not_called()


# --------------------------------------------------------------------------- #
# Static scan: no literal approve / permit / authorization to an EVC.
# --------------------------------------------------------------------------- #
_GRANT_FIRST_ARG = re.compile(
    r"(?:\.|\b_)(approve|increaseAllowance|permit|setAuthorization)(?:_cd|\.encode_input)?\s*\(\s*([^,)]+)")
_GRANT_SECOND_ARG = re.compile(
    r"\.(setAccountOperator|setOperator)(?:\.encode_input)?\s*\(\s*[^,)]+,\s*([^,)]+)")
_EVC_EXPR = re.compile(r"evc|0x[0-9a-fA-F]{40}", re.IGNORECASE)
# Data-driven grants: an (owner/token, EVC, amount) tuple, e.g. an approval-plan row
# `(usde, EVC, amt)` or transferFrom args `[borrower, EVC, amt]` that move tokens into an EVC.
_EVC_TUPLE = re.compile(r"(?:(?<![\w.])\(|\[)\s*[\w.]+\s*,\s*[A-Z_]*EVC[A-Z_]*\s*,")


def _grant_args(text: str):
    for rx in (_GRANT_FIRST_ARG, _GRANT_SECOND_ARG):
        for m in rx.finditer(text):
            yield m.group(1), m.group(2).strip(), text.count("\n", 0, m.start()) + 1


def test_no_literal_grant_to_evc_in_source():
    banned = evc_addresses()
    offenders = []
    for root in ("src", "scripts"):
        for f in (REPO / root).rglob("*.py"):
            for fn, arg, line in _grant_args(f.read_text()):
                hit = _EVC_EXPR.search(arg)
                if hit and (not hit.group(0).startswith("0x") or hit.group(0).lower() in banned):
                    offenders.append(f"{f.relative_to(REPO)}:{line}: {fn}({arg}, ...)")
            for n, text in enumerate(f.read_text().splitlines(), 1):
                code = text.split("#")[0]
                if '"""' not in code and _EVC_TUPLE.search(code):
                    offenders.append(f"{f.relative_to(REPO)}:{n}: {text.strip()}")
    assert not offenders, "Grant to an EVC:\n" + "\n".join(offenders)


def test_static_scan_catches_offender():
    sample = 'usde.approve(EVC, amount)\n(usde, b, 0, base._approve_cd(EVC, amt))\n' \
        'evc.setAccountOperator.encode_input(me, evc_addr, True)\n'
    args = [a for _, a, _ in _grant_args(sample)]
    assert args == ["EVC", "EVC", "evc_addr"]
    assert _EVC_TUPLE.search("        (cfg.usde, EVC, plan.start_usde, 'USDe -> EVC'),")
    assert _EVC_TUPLE.search('cd("transferFrom(address,address,uint256)", T, [b, EVC, amt])')
    assert not _EVC_TUPLE.search("ch.send_impersonated(b, EVC, batch, ts=T)")
