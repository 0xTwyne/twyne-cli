"""Unit tests for Twyne-on-Morpho writes: Enso swaps, user-supplied keys, CLI gating (no RPC)."""

from __future__ import annotations

from unittest.mock import MagicMock

import click
import pytest
from click.testing import CliRunner
from eth_abi import decode

from twyne_cli.chains import CHAINS, set_active_chain

ARB = CHAINS[42161]
MAINNET = CHAINS[1]


@pytest.fixture()
def arbitrum():
    set_active_chain(ARB)
    yield ARB
    set_active_chain(MAINNET)


# --------------------------------------------------------------------------- #
# Secrets: user-provided only
# --------------------------------------------------------------------------- #


def test_enso_key_missing_explains_setup(monkeypatch, tmp_path):
    import twyne_cli.secrets as secrets

    monkeypatch.delenv("ENSO_API_KEY", raising=False)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(secrets, "USER_ENV_FILE", tmp_path / "missing.env")
    with pytest.raises(click.ClickException) as exc:
        secrets.require_enso_key()
    assert "does not ship one" in exc.value.message
    assert "Never commit" in exc.value.message


def test_enso_key_lookup_order(monkeypatch, tmp_path):
    import twyne_cli.secrets as secrets

    user_env = tmp_path / "user.env"
    user_env.write_text("ENSO_API_KEY=from-user-file\n")
    user_env.chmod(0o600)
    monkeypatch.setattr(secrets, "USER_ENV_FILE", user_env)
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("ENSO_API_KEY", raising=False)
    assert secrets.lookup_secret("ENSO_API_KEY") == "from-user-file"
    (tmp_path / ".env").write_text("export ENSO_API_KEY='from-cwd'\n")
    assert secrets.lookup_secret("ENSO_API_KEY") == "from-cwd"
    monkeypatch.setenv("ENSO_API_KEY", "from-env")
    assert secrets.lookup_secret("ENSO_API_KEY") == "from-env"



# --------------------------------------------------------------------------- #
# Enso → Swapper calldata
# --------------------------------------------------------------------------- #

SWAPPER = "0x6eE488A00A2ef1E2764cD7245F8a77C40060A7C7"
ROUTER = "0xF75584eF6673aD213a685a1B58Cc0330B8eA22Cf"
USDG = "0x004B506865409877C9fA29bfb1ebA929984B9bbC"
SYRUP = "0xE1B0dC5A21b10f1634fBfbE9976fBA2Ee2e1c762"
OPERATOR = "0x48b21c492DF5359C433381CC2557072d5ba3fD4E"


def _route(router=ROUTER, min_out="990"):
    return {
        "amountOut": "1000", "minAmountOut": min_out, "priceImpact": 3,
        "tx": {"to": router, "data": "0xdeadbeef", "from": SWAPPER, "value": "0"},
    }


def _fake_get(route, status=200):
    resp = MagicMock(status_code=status, text="err")
    resp.json.return_value = route
    return MagicMock(return_value=resp)


def test_enso_swap_encodes_generic_handler_item(monkeypatch):
    import twyne_cli.swap as swap

    get = _fake_get(_route())
    monkeypatch.setattr(swap.httpx, "get", get)
    out = swap.enso_swap(42161, SWAPPER, USDG, SYRUP, 1000, OPERATOR, OPERATOR, 0.5, "k")
    assert out["amount_out_min"] == 990 and out["provider"] == "enso"
    (item,) = out["swap_data"]
    assert item[:4].hex() == "f71679d0"
    (params,) = decode([swap._SWAP_PARAMS_TYPE], item[4:])
    handler, mode, _account, token_in, token_out, _vin, _ain, receiver, _amt, data = params
    assert handler.rstrip(b"\0") == b"Generic" and mode == 0
    assert (token_in.lower(), token_out.lower(), receiver.lower()) == (USDG.lower(), SYRUP.lower(), OPERATOR.lower())
    target, payload = decode(["address", "bytes"], data)
    assert target.lower() == ROUTER.lower() and payload == bytes.fromhex("deadbeef")
    sent = get.call_args.kwargs
    assert sent["params"]["fromAddress"] == SWAPPER and sent["params"]["slippage"] == "50"
    assert sent["headers"]["Authorization"] == "Bearer k"


def test_enso_rejects_untrusted_router(monkeypatch):
    import twyne_cli.swap as swap

    monkeypatch.setattr(swap.httpx, "get", _fake_get(_route(router="0x" + "12" * 20)))
    with pytest.raises(RuntimeError, match="untrusted router"):
        swap.enso_swap(42161, SWAPPER, USDG, SYRUP, 1000, OPERATOR, OPERATOR, 0.5, "k")


def test_enso_rejects_zero_min_out(monkeypatch):
    import twyne_cli.swap as swap

    monkeypatch.setattr(swap.httpx, "get", _fake_get(_route(min_out="0")))
    with pytest.raises(RuntimeError, match="zero minAmountOut"):
        swap.enso_swap(42161, SWAPPER, USDG, SYRUP, 1000, OPERATOR, OPERATOR, 0.5, "k")


def test_enso_retries_rate_limit(monkeypatch):
    import twyne_cli.swap as swap

    ok, limited = MagicMock(status_code=200), MagicMock(status_code=429, text="1rps")
    ok.json.return_value = _route()
    get = MagicMock(side_effect=[limited, ok])
    monkeypatch.setattr(swap.httpx, "get", get)
    monkeypatch.setattr(swap.time, "sleep", lambda s: None)
    assert swap.enso_swap(42161, SWAPPER, USDG, SYRUP, 1000, OPERATOR, OPERATOR, 0.5, "k")["amount_out"] == 1000
    assert get.call_count == 2



# --------------------------------------------------------------------------- #
# CLI gating
# --------------------------------------------------------------------------- #


def _invoke(*args):
    from twyne_cli.cli import cli

    return CliRunner().invoke(cli, list(args))


def test_morpho_rejected_on_mainnet():
    res = _invoke("tx", "credit", "deposit", "0x" + "11" * 20, "1", "--protocol", "morpho", "--yes")
    assert res.exit_code == 2
    assert "Morpho protocol is not available on Ethereum" in res.output


def test_aave_rejected_on_arbitrum():
    res = _invoke("--chain", "arbitrum", "tx", "credit", "deposit-atokens", "0x" + "11" * 20, "1", "--yes")
    assert res.exit_code == 2
    assert "use --protocol morpho" in res.output


def test_vault_type_and_protocol_must_agree():
    res = _invoke("--chain", "arbitrum", "tx", "factory", "create-vault", "0x63DaC9b214906c30E5EBf77cf774e0dCB7638Bf5",
                  "--protocol", "morpho", "--vault-type", "0", "--yes")
    assert res.exit_code == 2
    assert "disagree" in res.output
