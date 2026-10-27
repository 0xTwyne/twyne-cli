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
        selfSettlementMode=0,
    )
    vals.update(over)
    return SimpleNamespace(address=VAULT, **{k: fn(v) for k, v in vals.items()})


CONVERTER = "0x00000000000000000000000000000000000000C9"


def make_world(vault=None, fee=500, quotes=None, buffer=0, max_liq_ltv=9_500, cash=1_000):
    vault = vault or make_vault()
    manager = SimpleNamespace(
        rebalanceFee=fn(fee), borrowBuffer=fn(buffer), liqParams=fn((9_000, max_liq_ltv)),
        settlementConverter=fn(CONVERTER), settlementConverterTolerance=fn(50), MAX_REBALANCE_FEE=fn(1_000),
    )
    iv = SimpleNamespace(unitOfAccount=fn(UOA), oracle=fn("0xROUTER"), cash=fn(cash))
    # unit of account value = amount * 2 (bid) / amount * 2 (ask): price 2 for every asset
    oracle = SimpleNamespace(getQuotes=MagicMock(side_effect=quotes or (lambda amt, b, q, **kw: (amt * 2, amt * 2))))
    return vault, manager, iv, oracle


# --------------------------------------------------------------------------- #
# V2 vault
# --------------------------------------------------------------------------- #


def test_vault_state_values_and_health():
    vault, manager, iv, oracle = make_world()
    s = v2.vault_v2_state(vault, manager, iv, oracle, None)
    assert (s["c_raw"], s["b_ext_raw"], s["c_lp_raw"]) == (1_000, 500, 300)
    assert s["b_int_raw"] == 40  # maxRelease 340 - totalReserved 300
    assert s["c_value"] == 2_000
    # B_total = B_ext + (1 + 5%) * B_int, price 2 => 1000 + 84
    assert s["b_total_value"] == 1_084
    assert s["operating_ltv_t_bps"] == 5_420
    # health is measured against liqLTV_t* = min(8000, 9500) = 8000
    assert s["health_factor_t"] == pytest.approx(8_000 / 5_420, rel=1e-6)
    assert s["liq_ltv_t_bps"] == 8_000 and s["max_feasible_liq_ltv_t_bps"] == 9_000
    assert s["price_of_c"] == {"status": v2.FRESH, "blocked_operations": False}


def test_liq_ltv_star_is_capped_by_max_liq_ltv():
    vault, manager, iv, oracle = make_world(max_liq_ltv=7_000)
    s = v2.vault_v2_state(vault, manager, iv, oracle, None)
    assert (s["liq_ltv_t_bps"], s["max_liq_ltv_t_bps"], s["liq_ltv_t_star_bps"]) == (8_000, 7_000, 7_000)
    assert s["health_factor_t"] == pytest.approx(7_000 / 5_420, rel=1e-6)
    manager.liqParams.assert_called_once_with(IV, TARGET, LOAN)


def test_vault_dyn_liq_ltv_and_reservation_internals_need_the_lens():
    vault, manager, iv, oracle = make_world()
    s = v2.vault_v2_state(vault, manager, iv, oracle, None)
    assert s["dyn_liq_ltv_t_bps"] is None  # TODO(lens)
    assert s["reservation"]["floor_raw"] is None and s["reservation"]["target_raw"] is None  # TODO(lens)
    assert s["can_liquidate"] is False  # the vault's own trigger is read, never re-derived
    assert "external_health_factor" not in s


def test_vault_reservation_cash_cap_is_cash_plus_credit_held_against_iv_debt():
    # reserved 300, IV debt 340 => held 300; cash 1000
    vault, manager, iv, oracle = make_world()
    r = v2.vault_v2_state(vault, manager, iv, oracle, None)["reservation"]
    assert (r["current_raw"], r["cash_cap_raw"]) == (300, 1_300)
    # credit above the IV debt is not held against it
    vault, manager, iv, oracle = make_world(make_vault(maxRelease=100, totalReserved=300))
    assert v2.vault_v2_state(vault, manager, iv, oracle, None)["reservation"]["cash_cap_raw"] == 1_100
    iv.cash = fn(side_effect=RuntimeError("no cash view"))
    assert v2.vault_v2_state(vault, manager, iv, oracle, None)["reservation"]["cash_cap_raw"] is None


def test_vault_state_debt_free_has_no_health():
    vault, manager, iv, oracle = make_world(make_vault(maxRepay=0, maxRelease=300))
    s = v2.vault_v2_state(vault, manager, iv, oracle, None)
    assert s["b_total_value"] == 0 and s["health_factor_t"] is None and s["operating_ltv_t_bps"] == 0


def test_vault_state_b_int_never_negative():
    vault, manager, iv, oracle = make_world(make_vault(maxRelease=100, totalReserved=300))
    assert v2.vault_v2_state(vault, manager, iv, oracle, None)["b_int_raw"] == 0


def test_vault_state_price_unavailable_is_null_not_error():
    def boom(*a, **k):
        raise RuntimeError("PriceTooOld")

    vault, manager, iv, oracle = make_world(quotes=boom)
    s = v2.vault_v2_state(vault, manager, iv, oracle, None)
    assert s["c_value"] is None and s["b_total_value"] is None and s["health_factor_t"] is None
    # fresh price or revert: every operation but a plain repay reverts
    assert s["price_of_c"] == {"status": v2.UNAVAILABLE, "blocked_operations": True}


def test_vault_price_probe_prices_one_unit_when_c_is_zero():
    # CreditLoanV2Checks.collateralPriceUsable quotes max(C, 1): a debt-free, empty vault is still probed
    vault, manager, iv, oracle = make_world(make_vault(totalAssets=0, maxRepay=0, maxRelease=0, totalReserved=0))
    s = v2.vault_v2_state(vault, manager, iv, oracle, None)
    assert s["price_of_c"]["status"] == v2.FRESH
    assert any(c.args[0] == 1 and c.args[1] == ASSET for c in oracle.getQuotes.call_args_list)


def test_removed_manager_settings_are_not_read():
    # setCollateralPriceMaxAge / setPriceFreshnessReader (and the T_liq tier) are gone from the manager
    vault, manager, iv, oracle = make_world()
    assert not hasattr(manager, "collateralPriceMaxAge") and not hasattr(manager, "priceFreshnessReader")
    v2.vault_v2_state(vault, manager, iv, oracle, None)  # reads nothing that the namespace lacks


def test_rebalance_fee_and_its_cap():
    vault, manager, iv, oracle = make_world(fee=250)
    s = v2.vault_v2_state(vault, manager, iv, oracle, None)
    assert (s["rebalance_fee_bps"], s["rebalance_fee_cap_bps"]) == (250, 1_000)
    del manager.MAX_REBALANCE_FEE  # a manager build without the cap
    assert v2.vault_v2_state(vault, manager, iv, oracle, None)["rebalance_fee_cap_bps"] is None


def test_vault_state_passes_block_to_every_call():
    vault, manager, iv, oracle = make_world()
    v2.vault_v2_state(vault, manager, iv, oracle, 123)
    vault.totalAssets.assert_called_with(block_identifier=123)
    manager.rebalanceFee.assert_called_with(IV, TARGET, LOAN, block_identifier=123)
    oracle.getQuotes.assert_called()
    assert all(c.kwargs == {"block_identifier": 123} for c in oracle.getQuotes.call_args_list)


def test_self_settlement_modes():
    for mode, name in ((0, "caller-supplied"), (1, "receipt-token-deposit"), (2, "converter")):
        vault, manager, iv, oracle = make_world(make_vault(selfSettlementMode=mode))
        ss = v2.vault_v2_state(vault, manager, iv, oracle, None)["self_settlement"]
        assert (ss["mode"], ss["name"]) == (mode, name)
        # the converter is read only in mode 2
        assert (ss["converter"], ss["converter_tolerance_bps"]) == ((CONVERTER, 50) if mode == 2 else (None, None))


def test_self_settlement_absent_on_older_vault_is_null():
    vault, manager, iv, oracle = make_world()
    vault.selfSettlementMode = fn(side_effect=RuntimeError("no such selector"))
    ss = v2.vault_v2_state(vault, manager, iv, oracle, None)["self_settlement"]
    assert ss["mode"] is None and ss["name"] is None
    del vault.selfSettlementMode  # ABI without the view at all
    assert v2.vault_v2_state(vault, manager, iv, oracle, None)["self_settlement"]["mode"] is None


def test_borrow_ramp_state():
    vault, manager, iv, oracle = make_world(buffer=250)
    r = v2.vault_v2_state(vault, manager, iv, oracle, None)["borrow_ramp"]
    assert r == {"delta_bps": 250, "enabled": True}
    vault, manager, iv, oracle = make_world(buffer=0)
    assert v2.vault_v2_state(vault, manager, iv, oracle, None)["borrow_ramp"]["enabled"] is False
    manager.borrowBuffer = fn(side_effect=RuntimeError("old manager"))
    r = v2.vault_v2_state(vault, manager, iv, oracle, None)["borrow_ramp"]
    assert r == {"delta_bps": None, "enabled": None}


# --------------------------------------------------------------------------- #
# pooled vault
# --------------------------------------------------------------------------- #


def make_pool(delta_x=300, **over):
    vals = dict(
        intermediateVault=IV, targetVault=TARGET, targetAsset=LOAN, collateralAsset=ASSET, creditAsset=CREDIT,
        residualAccount="0x00000000000000000000000000000000000000A9", openCount=3, cTot=900,
        clpTot=400, postedCredit=380, owedCredit=400, extDebt=700, ivDebt=420, maxFeasibleLiqLTV=8_500,
        externalRisk=False, excisionGrowthBound=10_500, pendingSplits=2, settleEventCount=7, isPriceFresh=True,
        pooledParams=(delta_x, 200), fallbackState=(50, 40, 3, 2, 5),  # loss, repaid, events, unapplied, uncovered
        collateralPrice=(True, 2 * 10**18), postedBookOf=385, residualDebt=9, unbackedLoss=4,
        bucketC=11, bucketDebt=12,
    )
    vals.update(over)
    return SimpleNamespace(address="0x00000000000000000000000000000000000000AA", **{k: fn(v) for k, v in vals.items()})


def test_pool_fallback_record_closed_when_every_account_applied():
    pool = make_pool(fallbackState=(0, 0, 3, 0, 0))
    assert v2.pool_state(pool, None, None, 0)["fallback_record"]["open"] is False


def test_pool_option_b_has_no_bucket():
    pool = make_pool()
    del pool.bucketC, pool.bucketDebt
    assert v2.pool_state(pool, None, None, 0)["bucket"] == {"c_raw": None, "debt_raw": None}


def test_pool_state_band_fallback_and_splits():
    registry = SimpleNamespace(isBandFeasible=fn(True), poolCaps=fn((1_000, 0)))
    s = v2.pool_state(make_pool(), registry, None, now=0)
    assert s["excision_band"] == {
        "delta_x_bps": 300, "d_max_bps": 200, "enabled": True, "growth_bound_bps": 10_500, "band_feasible": True,
    }
    fb = s["fallback_record"]
    assert fb["open"] and fb["events"] == 3 and fb["loss_credit_raw"] == 50 and fb["pending_splits"] == 2
    assert (fb["repaid_debt_raw"], fb["uncovered_loss_raw"]) == (40, 5)
    assert (s["residual_debt_raw"], s["unbacked_loss_raw"], s["posted_book_raw"]) == (9, 4, 385)
    assert s["bucket"] == {"c_raw": 11, "debt_raw": 12}
    assert s["open_accounts"] == 3 and s["owed_credit_raw"] == 400 and s["posted_credit_raw"] == 380
    registry.isBandFeasible.assert_called_once_with(IV, TARGET, LOAN)


def test_pool_caps_zero_means_no_cap_and_reached_flags():
    pool = make_pool(cTot=900, extDebt=700)
    registry = SimpleNamespace(isBandFeasible=fn(True), poolCaps=fn((700, 0)))
    caps = v2.pool_state(pool, registry, None, 0)["caps"]
    assert caps["debt_cap_reached"] is True and caps["collateral_cap_reached"] is False
    assert (caps["debt_used_raw"], caps["collateral_used_raw"]) == (700, 900)
    registry.poolCaps = fn((701, 900))
    caps = v2.pool_state(pool, registry, None, 0)["caps"]
    assert caps["debt_cap_reached"] is False and caps["collateral_cap_reached"] is True
    registry.poolCaps = fn(side_effect=RuntimeError("old registry"))
    assert v2.pool_state(pool, registry, None, 0)["caps"]["debt_cap_reached"] is None


def test_loss_excision_option_a_states():
    f = v2.loss_excision_state
    assert f(0, 0, 100)["status"] == "no-record"
    assert f(50, 2**256 - 1, 100)["status"] == "governance-only"
    waiting = f(50, 150, 100)
    assert waiting["status"] == "waiting" and waiting["seconds_left"] == 50 and waiting["option"] == "A"
    assert f(50, 100, 100)["status"] == "due" and f(50, 90, 100)["seconds_left"] == 0


def test_loss_excision_option_b_is_permissionless_at_any_time():
    f = v2.loss_excision_state
    for recorded in (0, 50):
        le = f(recorded, None, 100)
        assert (le["option"], le["status"], le["due_at"]) == ("B", "permissionless", None)
    pool = make_pool(fallbackRecordedAt=1_000)
    pool.lossExcisionDueAt = fn(side_effect=RuntimeError("no selector"))
    le = v2.pool_state(pool, None, None, 0)["loss_excision"]
    assert le["option"] == "B" and le["status"] == "permissionless" and le["recorded_at"] == 1_000


def test_loss_excision_without_either_view_is_not_applicable():
    pool = make_pool()
    pool.fallbackRecordedAt = fn(side_effect=RuntimeError("no selector"))
    pool.lossExcisionDueAt = fn(side_effect=RuntimeError("no selector"))
    le = v2.pool_state(pool, None, None, 0)["loss_excision"]
    assert le["option"] is None and le["status"] == "not-applicable"


def test_pool_state_option_a_reads_due_time():
    pool = make_pool(fallbackRecordedAt=1_000, lossExcisionDueAt=1_600)
    le = v2.pool_state(pool, None, None, now=1_100)["loss_excision"]
    assert (le["option"], le["status"], le["seconds_left"], le["recorded_at"]) == ("A", "waiting", 500, 1_000)


def test_pool_state_excision_disabled_and_no_registry():
    s = v2.pool_state(make_pool(delta_x=0), None, None, now=0)
    assert not s["excision_band"]["enabled"] and s["excision_band"]["band_feasible"] is None


def test_pool_state_has_no_rate_or_price_age():
    # the pool dropped `rate()` and `priceAge()`; price status is `isPriceFresh` plus the bid
    pool = make_pool()
    s = v2.pool_state(pool, None, None, 0)
    assert "rate" not in s and "age" not in s["price_of_c"]
    assert s["price_of_c"] == {"available": True, "bid": 2 * 10**18, "fresh": True}


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
    pool.capacityCreditOf = priced(260)
    pool.capacityValueOf = priced(600)
    pool.dynLiqLtvOf = priced(6_500)
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
    # health is dyn_liqLTV_t / LTV_t (the one trigger), not liqLTV_t / LTV_t
    assert s["dyn_liq_ltv_t_bps"] == 6_500 and s["health_factor_t"] == pytest.approx(6_500 / 4_000)
    assert (s["required_credit_raw"], s["capacity_credit_raw"], s["capacity_value"]) == (190, 260, 600)
    assert s["excess_credit"] == -10 and s["liquidatable"] is False and s["split_pending"] is False


def test_account_state_price_beyond_liquidation_bound_gives_nulls():
    s = v2.account_state(make_account_pool(price_ok=False), BORROWER, None)
    assert s["c_value"] is None and s["ltv_t_bps"] is None and s["health_factor_t"] is None
    assert s["dyn_liq_ltv_t_bps"] is None and s["capacity_credit_raw"] is None
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
    "CollateralVaultV2": {"selfSettlementMode", "totalAssets", "totalReserved", "maxRepay", "maxRelease", "twyneLiqLTV", "maxFeasibleLiqLTV",
                          "canLiquidate", "internalDebtToSettle", "intermediateVault", "twyneVaultManager"},
    "PooledLongTailVault": {"accounts", "fallbackState", "pendingSplits", "pooledParams", "excisionGrowthBound",
                            "residualAccount", "isExcisable", "attributedLoss", "totalDebtOf", "fallbackRecordedAt",
                            "lossExcisionDueAt", "dynLiqLtvOf", "capacityCreditOf", "capacityValueOf", "requiredCreditOf",
                            "postedBookOf", "residualDebt", "unbackedLoss", "chargeAboveCap", "bucketC", "bucketDebt"},
    "PooledResidualAccount": {"totalAssets", "asset", "pool"},
    "PooledParamsRegistry": {"isBandFeasible", "pooledParams", "poolCaps", "maxFeasibleLiqLTV"},
    "VaultManagerV2": {"rebalanceFee", "MAX_REBALANCE_FEE", "boost", "liqParams", "borrowBuffer",
                       "settlementConverter", "settlementConverterTolerance"},
}

# Removed from the contracts in round 5: the two-tier staleness rule and the pool's rate/price-age/scan views
REMOVED_NAMES = {
    "VaultManagerV2": {"collateralPriceMaxAge", "priceFreshnessReader"},
    "PooledLongTailVault": {"priceAge", "rate", "rMax", "residualAfter", "attributedLossReplayed", "bucketOwedCredit"},
}


@pytest.mark.parametrize("name", sorted(V2_NAMES))
def test_abis_carry_the_functions_the_reads_use(name):
    abi = _load_abi(name)
    assert V2_NAMES[name] <= {e["name"] for e in abi}
    assert all(e["type"] == "function" and e["stateMutability"] in ("view", "pure") for e in abi)


@pytest.mark.parametrize("name", sorted(REMOVED_NAMES))
def test_abis_drop_the_removed_functions(name):
    assert not REMOVED_NAMES[name] & {e["name"] for e in _load_abi(name)}


def test_fallback_state_abi_is_the_five_value_form():
    abi = {e["name"]: e for e in _load_abi("PooledLongTailVault")}
    assert [o["name"] for o in abi["fallbackState"]["outputs"]] == ["loss", "repaid", "events", "unapplied", "uncoveredLoss"]


def test_price_freshness_reader_is_gone():
    from twyne_cli import contracts

    assert not hasattr(contracts, "price_freshness_reader")
    with pytest.raises(Exception):
        _load_abi("PriceFreshnessReader")


def test_v2_group_is_registered_with_global_flags_before_subcommand():
    result = CliRunner().invoke(cli, ["--json", "--block", "5", "v2", "--help"])
    assert result.exit_code == 0
    for sub in ("vault", "pool", "account", "residual"):
        assert sub in result.output


def test_vault_command_json_output_at_block(monkeypatch):
    vault, manager, iv, oracle = make_world()
    ctx_stub = MagicMock()
    monkeypatch.setattr("twyne_cli.contracts.collateral_vault_v2", lambda a: vault)
    monkeypatch.setattr("twyne_cli.contracts.vault_manager_v2", lambda a: manager)
    monkeypatch.setattr("twyne_cli.contracts.credit_vault", lambda a: iv)
    monkeypatch.setattr("twyne_cli.contracts.oracle_router_at", lambda a: oracle)
    vault.twyneVaultManager = fn("0x00000000000000000000000000000000000000M1")
    monkeypatch.setattr("twyne_cli.context.TwyneContext.connect", lambda self: ctx_stub)
    monkeypatch.setattr("twyne_cli.context.TwyneContext.disconnect", lambda self: None)

    result = CliRunner().invoke(cli, ["--json", "--block", "42", "v2", "vault", VAULT])
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert data["b_total_value"] == 1_084 and data["price_of_c"]["status"] == "fresh"
    assert data["dyn_liq_ltv_t_bps"] is None and data["liq_ltv_t_star_bps"] == 8_000
    vault.twyneVaultManager.assert_called_with(block_identifier=42)


def _stub_connection(monkeypatch):
    monkeypatch.setattr("twyne_cli.context.TwyneContext.connect", lambda self: MagicMock())
    monkeypatch.setattr("twyne_cli.context.TwyneContext.disconnect", lambda self: None)
    monkeypatch.setattr("twyne_cli.commands.v2._block_time", lambda b: 10_000)


def test_pool_command_builds_every_row(monkeypatch):
    # the row list is built eagerly, so a renamed state key fails here
    pool = make_pool(fallbackRecordedAt=1_000, lossExcisionDueAt=1_600, pooledParamsRegistry=ZERO)
    monkeypatch.setattr("twyne_cli.contracts.pooled_vault", lambda a: pool)
    _stub_connection(monkeypatch)
    result = CliRunner().invoke(cli, ["--json", "v2", "pool", pool.address])
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert data["fallback_record"]["events"] == 3 and data["bucket"]["debt_raw"] == 12
    assert data["loss_excision"]["status"] == "due"


def test_account_command_builds_every_row(monkeypatch):
    pool = make_account_pool()
    monkeypatch.setattr("twyne_cli.contracts.pooled_vault", lambda a: pool)
    _stub_connection(monkeypatch)
    result = CliRunner().invoke(cli, ["--json", "v2", "account", pool.address, BORROWER])
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert data["dyn_liq_ltv_t_bps"] == 6_500 and data["capacity_credit_raw"] == 260
