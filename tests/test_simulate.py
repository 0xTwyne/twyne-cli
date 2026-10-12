"""Tests for the collateral-vault transaction simulator (`twyne vault simulate`).

Unit tests cover the pure logic (revert decoding, state decoding, rendering)
with no network. A single opt-in `live` test forks mainnet and runs the bundled
failing-repay fixture end-to-end, asserting the revert is surfaced.
"""

import json
import os
from pathlib import Path

import pytest

from twyne_cli import simulate as s

FIXTURE = Path(__file__).parent / "fixtures" / "failing-repay-tx.json"
CV = "0x18D62a5E91ecAb839c7E8061B92D252df4258ED0"

# A realistic positionStats tuple (matches HSV field order):
# (c_nat, c_usd, r_nat, r_usd, b_nat, b_usd, ~LTV_t, LTV_t, ~LTV_e, LTV_e, extHF, inHF, maxTwyneLTV)
RAW = (
    12 * 10**18,
    30_000 * 10**18,  # C native (18dp), C usd (1e18)
    3 * 10**18,
    7_500 * 10**18,  # C_LP native, usd
    20_000 * 10**6,
    20_000 * 10**18,  # B native (6dp USDC), usd
    7_800,
    6_700,
    9_000,
    5_500,  # ~LTV_t, LTV_t, ~LTV_e, LTV_e (bps)
    int(1.49 * 10**18),
    int(1.20 * 10**18),  # extHF, inHF (1e18)
    8_500,  # maxTwyneLTV bps
)


def _state(over=None):
    raw = list(RAW)
    for idx, val in (over or {}).items():
        raw[idx] = val
    return s.CVState.from_raw(tuple(raw), coll_dec=18, coll_sym="wstETH", debt_dec=6, debt_sym="USDC")


# ---------------------------------------------------------------------------
# Revert decoding
# ---------------------------------------------------------------------------
def test_decode_revert_error_string():
    data = (
        "0x08c379a0"
        "0000000000000000000000000000000000000000000000000000000000000020"
        "0000000000000000000000000000000000000000000000000000000000000005"
        "68656c6c6f000000000000000000000000000000000000000000000000000000"
    )
    assert s.decode_revert(data, None) == 'Error("hello")'


def test_decode_revert_panic():
    data = "0x4e487b71" + "0000000000000000000000000000000000000000000000000000000000000011"
    assert s.decode_revert(data, None) == "Panic(0x11)"


def test_decode_revert_unknown_selector():
    out = s.decode_revert("0xdeadbeef" + "00" * 32, None)
    assert "0xdeadbeef" in out


def test_decode_revert_falls_back_to_message():
    assert s.decode_revert(None, "execution reverted") == "execution reverted"


def test_error_selector_table_includes_standard():
    table = s._build_error_selectors()
    assert table["08c379a0"] == "Error(string)"
    assert table["4e487b71"] == "Panic(uint256)"
    # Bundled ABIs (EVC/CollateralVault/EVault) contribute custom errors too.
    assert len(table) > 5


# ---------------------------------------------------------------------------
# State decoding
# ---------------------------------------------------------------------------
def test_cvstate_field_mapping_and_units():
    st = _state()
    assert st.collateral_native == 12.0  # 12e18 / 1e18
    assert st.collateral_usd == 30_000.0  # 1e18 scaled
    assert st.borrow_native == 20_000.0  # 6dp debt token
    assert st.twyne_ltv_pct == 67.0  # 6700 bps
    assert st.twyne_liq_ltv_pct == 78.0  # 7800 bps
    assert st.in_hf == pytest.approx(1.20)
    assert st.liquidatable is False


def test_cvstate_liquidatable_when_inhf_below_one():
    st = _state({11: int(0.97 * 10**18)})  # inHF = 0.97
    assert st.liquidatable is True


def test_ext_or_inf_clamps_zero_debt_sentinel():
    assert s.ext_or_inf(2**256 - 1) == float("inf")
    assert s.ext_or_inf(int(1.5 * 10**18)) == pytest.approx(1.5)


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------
def _result(before, after, exec_result):
    return {
        "cv": CV,
        "hsv": "0xHSV",
        "chain_id": 1,
        "fork_block": 25_000_000,
        "tx": {"to": "0xef39", "from": "0xA865", "value": 0},
        "_before_obj": before,
        "_after_obj": after,
        "_exec_obj": exec_result,
    }


def test_render_success_shows_deltas_and_safer_verdict():
    before = _state()
    after = _state({4: 10_000 * 10**6, 5: 10_000 * 10**18, 7: 3_400, 11: int(2.50 * 10**18)})
    out = s.render_report(_result(before, after, s.ExecResult(False, None, 1_234_567, "0xh")))
    assert "✅ TX SUCCEEDS" in out
    assert "gas 1,234,567" in out
    assert "-10,000.00" in out  # borrow USD delta
    assert "safer" in out


def test_render_revert_shows_reason_and_no_change():
    before = _state()
    ex = s.ExecResult(True, 'Error("transferFrom reverted")', None, None)
    out = s.render_report(_result(before, before, ex))
    assert "⛔ TX WOULD REVERT" in out
    assert "transferFrom reverted" in out
    assert "no state change" in out


def test_verdict_flags_new_liquidatability():
    before = _state()  # inHF 1.20, safe
    after = _state({11: int(0.95 * 10**18)})  # inHF 0.95, liquidatable
    v = s._verdict(before, after, s.ExecResult(False, None, 1, "0xh"))
    assert "UNSAFE" in v


# ---------------------------------------------------------------------------
# tx-file loading
# ---------------------------------------------------------------------------
def test_load_tx_file_reads_fixture():
    tx = s.load_tx_file(str(FIXTURE))
    assert tx["to"].lower() == "0xef39d6493884c4c84d38a4bff879ce16cede702a"
    assert tx["data"].startswith("0xc16ae7a4")  # EVC.batch selector
    assert int(tx["chainId"]) == 1


def test_load_tx_file_rejects_missing_keys(tmp_path):
    p = tmp_path / "bad.json"
    p.write_text(json.dumps({"from": "0x0"}))
    with pytest.raises(ValueError):
        s.load_tx_file(str(p))


# ---------------------------------------------------------------------------
# Live end-to-end (opt-in: --live, needs anvil + a working archive RPC)
# ---------------------------------------------------------------------------
@pytest.mark.live
def test_simulate_failing_repay_fork():
    fork_url = os.environ.get("ANVIL_FORK_URL") or os.environ.get("RPC_URL_1") or "https://ethereum-rpc.publicnode.com"
    tx = s.load_tx_file(str(FIXTURE))
    res = s.simulate(cv_address=CV, tx=tx, fork_url=fork_url)

    # The fixture is a known-failing repay batch: it must revert, leaving state
    # unchanged, and the CV must decode as the Pendle PT / USDe position.
    assert res["exec"]["reverted"] is True
    assert "transferFrom" in res["exec"]["revert_reason"]
    assert res["before"] == res["after"]
    assert res["before"]["collateral_symbol"].startswith("waEthPT")
    assert res["before"]["debt_symbol"] == "USDe"
    assert res["before"]["in_hf"] > 1.0  # tight but solvent
