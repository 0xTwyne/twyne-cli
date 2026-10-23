"""Unit tests for credit~loan V2 and pooled long-tail reads (no RPC)."""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from click.testing import CliRunner

from twyne_cli import v2
from twyne_cli.cli import cli
from twyne_cli.contracts import _load_abi

# Placeholders only: no V2 contract is deployed. Not real addresses.
VAULT = "0x00000000000000000000000000000000000000A1"
IV = "0x00000000000000000000000000000000000000B2"
TARGET = "0x00000000000000000000000000000000000000C3"
ASSET = "0x00000000000000000000000000000000000000D4"
LOAN = "0x00000000000000000000000000000000000000E5"
CREDIT = "0x00000000000000000000000000000000000000F6"
UOA = "0x0000000000000000000000000000000000000001"
READER = "0x0000000000000000000000000000000000000002"
BORROWER = "0x0000000000000000000000000000000000000003"
ZERO = v2.ZERO_ADDRESS


def fn(value=None, side_effect=None):
    m = MagicMock(return_value=value, side_effect=side_effect)
    return m


def make_vault(**over):
    vals = dict(
        asset=ASSET, targetVault=TARGET, targetAsset=LOAN, reserve=CREDIT, intermediateVault=IV,
        totalAssets=1_000, totalReserved=300, maxRepay=500, maxRelease=340, twyneLiqLTV=8_000,
        maxFeasibleLiqLTV=9_000, canLiquidate=False, isExternallyLiquidated=False, borrower=BORROWER,
    )
    vals.update(over)
    return SimpleNamespace(address=VAULT, **{k: fn(v) for k, v in vals.items()})


def make_world(vault=None, max_age=3_600, fee=500, quotes=None):
    vault = vault or make_vault()
    manager = SimpleNamespace(rebalanceFee=fn(fee), collateralPriceMaxAge=fn(max_age))
    iv = SimpleNamespace(unitOfAccount=fn(UOA), oracle=fn("0xROUTER"))
    # unit of account value = amount * 2 (bid) / amount * 2 (ask): price 2 for every asset
    oracle = SimpleNamespace(getQuotes=MagicMock(side_effect=quotes or (lambda amt, b, q, **kw: (amt * 2, amt * 2))))
    reader = SimpleNamespace(address=READER, priceUpdatedAt=fn(10_000 - 100))
    return vault, manager, iv, oracle, reader


# --------------------------------------------------------------------------- #
# price staleness
# --------------------------------------------------------------------------- #


def test_price_status_no_bound():
    s = v2.price_status(0, ZERO, None, now=1_000)
    assert s["status"] == v2.NO_BOUND and not s["blocked_operations"]


def test_price_status_fresh_and_stale_edges():
    # age == bound is still fresh (the vault requires updatedAt + maxAge >= now)
    assert v2.price_status(100, READER, 900, now=1_000)["status"] == v2.FRESH
    s = v2.price_status(100, READER, 899, now=1_000)
    assert s["status"] == v2.STALE and s["age"] == 101 and s["blocked_operations"]


def test_price_status_fails_closed():
    assert v2.price_status(100, ZERO, None, 1_000)["status"] == v2.READER_MISSING
    assert v2.price_status(100, READER, None, 1_000)["status"] == v2.UNREADABLE
    assert v2.price_status(100, READER, None, 1_000)["blocked_operations"]


# --------------------------------------------------------------------------- #
# V2 vault
# --------------------------------------------------------------------------- #


def test_vault_state_values_and_health():
    vault, manager, iv, oracle, reader = make_world()
    s = v2.vault_v2_state(vault, manager, iv, oracle, reader, None, now=10_000)
    assert (s["c_raw"], s["b_ext_raw"], s["c_lp_raw"]) == (1_000, 500, 300)
    assert s["b_int_raw"] == 40  # maxRelease 340 - totalReserved 300
    assert s["c_value"] == 2_000
    # B_total = B_ext + (1 + 5%) * B_int, price 2 => 1000 + 84
    assert s["b_total_value"] == 1_084
    assert s["operating_ltv_t_bps"] == 5_420
    assert s["health_factor_t"] == pytest.approx(8_000 / 5_420, rel=1e-6)
    assert s["liq_ltv_t_bps"] == 8_000 and s["max_feasible_liq_ltv_t_bps"] == 9_000
    assert s["price_of_c"]["status"] == v2.FRESH and s["price_of_c"]["age"] == 100


def test_vault_state_debt_free_has_no_health():
    vault, manager, iv, oracle, reader = make_world(make_vault(maxRepay=0, maxRelease=300))
    s = v2.vault_v2_state(vault, manager, iv, oracle, reader, None, now=10_000)
    assert s["b_total_value"] == 0 and s["health_factor_t"] is None and s["operating_ltv_t_bps"] == 0


def test_vault_state_b_int_never_negative():
    vault, manager, iv, oracle, reader = make_world(make_vault(maxRelease=100, totalReserved=300))
    assert v2.vault_v2_state(vault, manager, iv, oracle, reader, None, 10_000)["b_int_raw"] == 0


def test_vault_state_price_unavailable_is_null_not_error():
    def boom(*a, **k):
        raise RuntimeError("PriceTooOld")

    vault, manager, iv, oracle, reader = make_world(quotes=boom)
    reader.priceUpdatedAt = fn(side_effect=RuntimeError("revert"))
    s = v2.vault_v2_state(vault, manager, iv, oracle, reader, None, now=10_000)
    assert s["c_value"] is None and s["b_total_value"] is None and s["health_factor_t"] is None
    assert s["price_of_c"]["status"] == v2.UNREADABLE


def test_vault_state_no_reader_with_bound_fails_closed():
    vault, manager, iv, oracle, _ = make_world()
    s = v2.vault_v2_state(vault, manager, iv, oracle, None, None, now=10_000)
    assert s["price_of_c"]["status"] == v2.READER_MISSING and s["price_of_c"]["blocked_operations"]


def test_vault_state_passes_block_to_every_call():
    vault, manager, iv, oracle, reader = make_world()
    v2.vault_v2_state(vault, manager, iv, oracle, reader, 123, now=10_000)
    vault.totalAssets.assert_called_with(block_identifier=123)
    manager.rebalanceFee.assert_called_with(IV, TARGET, LOAN, block_identifier=123)
    oracle.getQuotes.assert_called()
    assert all(c.kwargs == {"block_identifier": 123} for c in oracle.getQuotes.call_args_list)


# --------------------------------------------------------------------------- #
# pooled vault
# --------------------------------------------------------------------------- #


def make_pool(delta_x=300, **over):
    vals = dict(
        intermediateVault=IV, targetVault=TARGET, targetAsset=LOAN, collateralAsset=ASSET, creditAsset=CREDIT,
        residualAccount="0x00000000000000000000000000000000000000A9", openCount=3, cTot=900,
        clpTot=400, postedCredit=380, owedCredit=400, extDebt=700, ivDebt=420, rate=123, maxFeasibleLiqLTV=8_500,
        externalRisk=False, excisionGrowthBound=10_500, pendingSplits=2, settleEventCount=7, isPriceFresh=True,
        priceAge=60, pooledParams=(delta_x, 200), fallbackState=(True, False, False, 50, 40, 5),
        collateralPrice=(True, 2 * 10**18),
    )
    vals.update(over)
    return SimpleNamespace(address="0x00000000000000000000000000000000000000AA", **{k: fn(v) for k, v in vals.items()})


def test_pool_state_band_fallback_and_splits():
    registry = SimpleNamespace(isBandFeasible=fn(True))
    s = v2.pool_state(make_pool(), registry, None, now=0)
    assert s["excision_band"] == {
        "delta_x_bps": 300, "d_max_bps": 200, "enabled": True, "growth_bound_bps": 10_500, "band_feasible": True,
    }
    fb = s["fallback_record"]
    assert fb["open"] and not fb["finalized"] and fb["loss_credit_raw"] == 50 and fb["pending_splits"] == 2
    assert s["open_accounts"] == 3 and s["owed_credit_raw"] == 400 and s["posted_credit_raw"] == 380
    registry.isBandFeasible.assert_called_once_with(IV, TARGET, LOAN)


def test_pool_state_excision_disabled_and_no_registry():
    s = v2.pool_state(make_pool(delta_x=0), None, None, now=0)
    assert not s["excision_band"]["enabled"] and s["excision_band"]["band_feasible"] is None


def test_pool_state_price_age_unreadable_is_null():
    pool = make_pool()
    pool.priceAge = fn(side_effect=RuntimeError("no reader"))
    assert v2.pool_state(pool, None, None, 0)["price_of_c"]["age"] is None


def make_account_pool(is_open=True, price_ok=True, **over):
    def priced(v):
        return fn(v) if price_ok else fn(side_effect=RuntimeError("P_PriceTooOld"))

    pool = make_pool()
    pool.accounts = fn((500, 10, 200, 30, 7_000, 4, is_open))
    pool.isSplitPending = fn(False)
    pool.attributedFor = fn(True)
    pool.pendingLoss = fn(0)
    pool.bExtOf = fn(250)
    pool.bIntOf = fn(15)
    pool.ivDebtOf = fn(215)
    pool.requiredCreditOf = fn(190)
    pool.cValueOf = priced(1_000)
    pool.totalDebtOf = priced(400)
    pool.ltvOf = priced(4_000)
    pool.excessOf = priced(-10)
    pool.incentiveOf = priced(25)
    pool.isLiquidatable = priced(False)
    pool.isExcisable = priced(False)
    for k, v in over.items():
        setattr(pool, k, fn(v))
    return pool


def test_account_state_open():
    s = v2.account_state(make_account_pool(), BORROWER, None)
    assert s["open"] and s["c_raw"] == 500 and s["b_ext_raw"] == 250 and s["b_int_raw"] == 15
    assert s["excision_trigger_ltv_bps"] == 7_300  # liqLTV 7000 + delta_x 300
    assert s["health_factor_t"] == pytest.approx(7_000 / 4_000)
    assert s["excess_credit"] == -10 and s["liquidatable"] is False and s["split_pending"] is False


def test_account_state_price_beyond_liquidation_bound_gives_nulls():
    s = v2.account_state(make_account_pool(price_ok=False), BORROWER, None)
    assert s["c_value"] is None and s["ltv_t_bps"] is None and s["health_factor_t"] is None
    assert s["liquidatable"] is None and s["b_ext_raw"] == 250  # amounts without a price still read


def test_account_state_closed_account_stops_early():
    s = v2.account_state(make_account_pool(is_open=False), BORROWER, None)
    assert not s["open"] and "b_ext_raw" not in s


def test_account_state_debt_free_and_max_ltv():
    pool = make_account_pool(ltvOf=0)
    assert v2.account_state(pool, BORROWER, None)["health_factor_t"] is None
    pool = make_account_pool(ltvOf=2**256 - 1)
    assert v2.account_state(pool, BORROWER, None)["health_factor_t"] is None


def test_residual_state():
    r = SimpleNamespace(address="0x00000000000000000000000000000000000000AB", asset=fn(ASSET), totalAssets=fn(77))
    iv = SimpleNamespace(address=IV, debtOf=fn(55))
    s = v2.residual_state(make_pool(), r, iv, 9)
    assert (s["c_held_raw"], s["iv_debt_raw"]) == (77, 55)
    iv.debtOf.assert_called_once_with(r.address, block_identifier=9)


# --------------------------------------------------------------------------- #
# ABIs and CLI wiring
# --------------------------------------------------------------------------- #

V2_NAMES = {
    "CollateralVaultV2": {"totalAssets", "totalReserved", "maxRepay", "maxRelease", "twyneLiqLTV", "maxFeasibleLiqLTV",
                          "canLiquidate", "internalDebtToSettle", "intermediateVault", "twyneVaultManager"},
    "PooledLongTailVault": {"accounts", "fallbackState", "pendingSplits", "pooledParams", "excisionGrowthBound",
                            "residualAccount", "isExcisable", "priceAge", "attributedLoss", "totalDebtOf"},
    "PooledResidualAccount": {"totalAssets", "asset", "pool"},
    "PooledParamsRegistry": {"isBandFeasible", "pooledParams"},
    "VaultManagerV2": {"rebalanceFee", "collateralPriceMaxAge", "priceFreshnessReader", "boost", "liqParams"},
    "PriceFreshnessReader": {"priceUpdatedAt"},
}


@pytest.mark.parametrize("name", sorted(V2_NAMES))
def test_abis_carry_the_functions_the_reads_use(name):
    abi = _load_abi(name)
    assert V2_NAMES[name] <= {e["name"] for e in abi}
    assert all(e["type"] == "function" and e["stateMutability"] in ("view", "pure") for e in abi)


def test_v2_group_is_registered_with_global_flags_before_subcommand():
    result = CliRunner().invoke(cli, ["--json", "--block", "5", "v2", "--help"])
    assert result.exit_code == 0
    for sub in ("vault", "pool", "account", "residual"):
        assert sub in result.output


def test_vault_command_json_output_at_block(monkeypatch):
    from twyne_cli.commands import v2 as cmd

    seen = {}
    vault, manager, iv, oracle, reader = make_world()
    ctx_stub = MagicMock()
    monkeypatch.setattr("twyne_cli.contracts.collateral_vault_v2", lambda a: vault)
    monkeypatch.setattr("twyne_cli.contracts.vault_manager_v2", lambda a: manager)
    monkeypatch.setattr("twyne_cli.contracts.credit_vault", lambda a: iv)
    monkeypatch.setattr("twyne_cli.contracts.price_freshness_reader", lambda a: reader)
    monkeypatch.setattr("twyne_cli.contracts.oracle_router_at", lambda a: oracle)
    monkeypatch.setattr(cmd, "_block_time", lambda b: seen.setdefault("block", b) and 10_000)
    vault.twyneVaultManager = fn("0x00000000000000000000000000000000000000M1")
    manager.priceFreshnessReader = fn(READER)
    monkeypatch.setattr("twyne_cli.context.TwyneContext.connect", lambda self: ctx_stub)
    monkeypatch.setattr("twyne_cli.context.TwyneContext.disconnect", lambda self: None)

    result = CliRunner().invoke(cli, ["--json", "--block", "42", "v2", "vault", VAULT])
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert data["b_total_value"] == 1_084 and data["price_of_c"]["status"] == "fresh"
    assert seen["block"] == 42
    vault.twyneVaultManager.assert_called_with(block_identifier=42)
