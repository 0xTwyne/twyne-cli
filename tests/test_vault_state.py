"""Tests for the shared CV state reader and the `vault info` / `vault health` commands.

Unit tests cover the pure logic (decode, to_dict, resolvers) and the command
rendering (with `read_cv_state` monkeypatched, no network). One opt-in `live`
test reads the real CV through a public RPC — no anvil needed, since info/health
are read-only.
"""

import os
from unittest.mock import patch

import pytest

from twyne_cli import vault_state as vs

CV = "0x18D62a5E91ecAb839c7E8061B92D252df4258ED0"

# positionStats tuple order: c_nat,c_usd,r_nat,r_usd,b_nat,b_usd,~LTV_t,LTV_t,~LTV_e,LTV_e,extHF,inHF,maxTwyneLTV
RAW = (
    12 * 10**18,
    30_000 * 10**18,
    3 * 10**18,
    7_500 * 10**18,
    20_000 * 10**6,
    20_000 * 10**18,
    7_800,
    6_700,
    9_000,
    5_500,
    int(1.49 * 10**18),
    int(1.20 * 10**18),
    8_500,
)


def make_state(**identity):
    base = dict(
        borrower="0xB0rr0wer000000000000000000000000000000aa",
        collateral_token="0xC011atera1000000000000000000000000000bb",
        debt_token="0xDeb700000000000000000000000000000000ccdd",
        protocol="Euler",
        can_liquidate=False,
        can_rebalance=False,
    )
    base.update(identity)
    return vs.CVState.from_raw(RAW, coll_dec=18, coll_sym="wstETH", debt_dec=6, debt_sym="USDC", **base)


# ---------------------------------------------------------------------------
# CVState
# ---------------------------------------------------------------------------
def test_from_raw_identity_and_units():
    st = make_state()
    assert st.collateral_native == 12.0
    assert st.borrow_native == 20_000.0
    assert st.twyne_ltv_pct == 67.0
    assert st.twyne_liq_ltv_pct == 78.0
    assert st.max_twyne_ltv_pct == 85.0
    assert st.in_hf == pytest.approx(1.20)
    assert st.protocol == "Euler"
    assert st.borrower.startswith("0xB0rr0wer")
    assert st.liquidatable is False


def test_to_dict_includes_liquidatable_and_handles_inf():
    st = make_state()
    d = st.to_dict()
    assert d["liquidatable"] is False
    assert d["in_hf"] == pytest.approx(1.20)
    # zero-debt sentinel → inf → JSON-safe None
    st_inf = vs.CVState.from_raw(
        RAW[:10] + (2**256 - 1,) + RAW[11:], coll_dec=18, coll_sym="X", debt_dec=6, debt_sym="Y"
    )
    assert st_inf.ext_hf == float("inf")
    assert st_inf.to_dict()["ext_hf"] is None


def test_ext_or_inf_clamps_sentinel():
    assert vs.ext_or_inf(2**256 - 1) == float("inf")
    assert vs.ext_or_inf(int(1.5 * 10**18)) == pytest.approx(1.5)


# ---------------------------------------------------------------------------
# Resolvers
# ---------------------------------------------------------------------------
def test_resolve_hsv_default_override_and_unknown():
    assert vs.resolve_hsv(1) == "0x5A919b9A77ee391AB48208A93e0684c24F99B07a"
    assert vs.resolve_hsv(1, "0xOVERRIDE") == "0xOVERRIDE"
    with pytest.raises(RuntimeError):
        vs.resolve_hsv(999999)


def test_resolve_read_rpc_prefers_ctx_url():
    class Ctx:
        rpc_url = "https://my.node/abc"

        class chain:
            chain_id = 1

    assert vs.resolve_read_rpc(Ctx()) == "https://my.node/abc"


def test_resolve_read_rpc_falls_back(monkeypatch):
    monkeypatch.delenv("RPC_URL_1", raising=False)
    monkeypatch.delenv("RPC_URL", raising=False)
    monkeypatch.setattr(vs, "_dotenv_lookup", lambda key: None)

    class Ctx:
        rpc_url = None

        class chain:
            chain_id = 1

    assert vs.resolve_read_rpc(Ctx()) == "https://ethereum-rpc.publicnode.com"


# ---------------------------------------------------------------------------
# `vault info` / `vault health` rendering (read_cv_state monkeypatched)
# ---------------------------------------------------------------------------
def _run(args, state):
    from click.testing import CliRunner

    from twyne_cli.cli import cli

    with (
        patch("twyne_cli.vault_state.read_cv_state", return_value=state),
        patch("twyne_cli.vault_state.resolve_read_rpc", return_value="http://x"),
    ):
        return CliRunner().invoke(cli, args)


def test_info_json_has_health_and_identity():
    res = _run(["--json", "vault", "info", CV], make_state())
    assert res.exit_code == 0, res.output
    import json

    out = json.loads(res.output)
    assert out["vault"] == CV
    assert out["in_hf"] == pytest.approx(1.20)
    assert out["protocol"] == "Euler"
    assert out["liquidatable"] is False
    assert out["can_rebalance"] is False


def test_info_table_shows_hf_not_na():
    with patch("twyne_cli.commands.vault.is_tty", return_value=True):
        res = _run(["vault", "info", CV], make_state())
    assert res.exit_code == 0, res.output
    assert "Internal HF (inHF)" in res.output
    assert "1.2000" in res.output
    assert "N/A" not in res.output  # the old removed-viewer placeholder is gone
    assert "Borrower" in res.output


def test_health_table_shows_risk_and_hf():
    with patch("twyne_cli.commands.vault.is_tty", return_value=True):
        res = _run(["vault", "health", CV], make_state())
    assert res.exit_code == 0, res.output
    assert "Internal HF (inHF)" in res.output
    assert "Risk" in res.output
    assert "healthy" in res.output  # inHF 1.20 → healthy bucket


def test_health_json_flags_liquidatable():
    liq = make_state()
    object.__setattr__(liq, "in_hf", 0.97)  # force liquidatable
    res = _run(["--json", "vault", "health", CV], liq)
    assert res.exit_code == 0, res.output
    import json

    out = json.loads(res.output)
    assert out["liquidatable"] is True
    assert out["risk"] == "LIQUIDATABLE"


# ---------------------------------------------------------------------------
# Live read (opt-in: --live; read-only, no anvil)
# ---------------------------------------------------------------------------
@pytest.mark.live
def test_read_cv_state_live():
    # Invariants only — this CV's live position drifts (debt repaid, etc.), but
    # its identity (tokens, protocol, borrower) is stable across blocks.
    rpc = os.environ.get("RPC_URL_1") or "https://ethereum-rpc.publicnode.com"
    st = vs.read_cv_state(rpc, vs.resolve_hsv(1), CV)
    assert st.collateral_symbol.startswith("waEthPT")
    assert st.debt_symbol == "USDe"
    assert st.protocol in ("Euler", "Aave V3")
    assert int(st.borrower, 16) != 0
    assert st.in_hf > 0  # solvent or zero-debt (∞)
    assert isinstance(st.to_dict(), dict)
