"""Read-only state of credit~loan V2 collateral vaults and pooled long-tail vaults.

Symbols follow the V2 specification: C collateral, B_ext external debt, B_int accrued internal debt
(`maxRelease() - totalReserved`), B_total = B_ext + (1 + phi) * B_int, C_LP reserved credit.
Raw amounts are in token units of their asset. B_total and C values are in the intermediate
vault's unit of account (C at the bid, liabilities at the ask), as the vault values them.

Each function takes already-built contract objects so the tests need no RPC.
"""

from __future__ import annotations

from typing import Any

from .constants import BPS

# Price-of-C staleness status
FRESH = "fresh"
STALE = "stale"
NO_BOUND = "no-bound"  # the key has no normal bound (collateralPriceMaxAge == 0)
READER_MISSING = "reader-missing"  # a bound is set but no reader: the vault fails closed
UNREADABLE = "unreadable"  # the reader reverted (unknown adapter type): the vault fails closed

ZERO_ADDRESS = "0x0000000000000000000000000000000000000000"


def _call(fn, block: int | None, *args):
    return fn(*args, block_identifier=block) if block is not None else fn(*args)


def _try(fn, block: int | None, *args) -> Any | None:
    """Call a view; None when it reverts (e.g. a price read beyond the liquidation bound)."""
    try:
        return _call(fn, block, *args)
    except Exception:  # noqa: BLE001
        return None


def price_status(
    max_age: int, reader: str, updated_at: int | None, now: int
) -> dict:
    """Status of the router's price of C against the key's normal bound (mirrors the vault's gate)."""
    if max_age == 0:
        return {"status": NO_BOUND, "max_age": 0, "age": None, "blocked_operations": False}
    if reader == ZERO_ADDRESS:
        return {"status": READER_MISSING, "max_age": max_age, "age": None, "blocked_operations": True}
    if updated_at is None:
        return {"status": UNREADABLE, "max_age": max_age, "age": None, "blocked_operations": True}
    age = max(0, now - updated_at)
    stale = updated_at + max_age < now
    return {
        "status": STALE if stale else FRESH,
        "max_age": max_age,
        "age": age,
        "updated_at": updated_at,
        # stale: borrow, withdraw with debt, settle, rebalance are blocked; repay, deposit, liquidate stay open
        "blocked_operations": stale,
    }


def vault_v2_state(vault, manager, iv, oracle, reader, block: int | None, now: int) -> dict:
    """State of one V2 collateral vault.

    `vault`, `manager`, `iv` (intermediate vault, EVault ABI), `oracle` (its router) and `reader` are
    contract objects. `reader` may be None when the manager has no reader.
    """
    asset = _call(vault.asset, block)
    target_vault = _call(vault.targetVault, block)
    target_asset = _call(vault.targetAsset, block)
    reserve = _call(vault.reserve, block)
    iv_addr = _call(vault.intermediateVault, block)
    uoa = _call(iv.unitOfAccount, block)

    c = int(_call(vault.totalAssets, block))
    c_lp = int(_call(vault.totalReserved, block))
    b_ext = int(_call(vault.maxRepay, block))
    iv_debt = int(_call(vault.maxRelease, block))
    b_int = max(0, iv_debt - c_lp)
    liq_ltv = int(_call(vault.twyneLiqLTV, block))
    phi = int(_call(manager.rebalanceFee, block, iv_addr, target_vault, target_asset))
    max_feasible = int(_call(vault.maxFeasibleLiqLTV, block))
    can_liquidate = bool(_call(vault.canLiquidate, block))
    externally_liquidated = bool(_call(vault.isExternallyLiquidated, block))

    def quote(amount: int, base: str, side: int) -> int | None:
        if amount == 0:
            return 0
        res = _try(oracle.getQuotes, block, amount, base, uoa)
        return None if res is None else int(res[side])

    c_value = quote(c, asset, 0)  # bid
    b_ext_value = quote(b_ext, target_asset, 1)  # ask
    b_int_value = quote(b_int, reserve, 1)
    b_total_value = None
    health = None
    operating_ltv_bps = None
    if None not in (c_value, b_ext_value, b_int_value):
        b_total_value = b_ext_value + b_int_value * (BPS + phi) // BPS
        if b_total_value == 0:
            health = None  # debt-free: no finite health factor
            operating_ltv_bps = 0
        elif c_value > 0:
            operating_ltv_bps = b_total_value * BPS // c_value
            health = liq_ltv * c_value / (b_total_value * BPS)  # liqLTV_t / LTV_t
        else:
            operating_ltv_bps = None
            health = 0.0

    max_age = int(_call(manager.collateralPriceMaxAge, block, iv_addr, target_vault, target_asset))
    updated_at = None
    reader_addr = ZERO_ADDRESS
    if reader is not None:
        reader_addr = reader.address if hasattr(reader, "address") else str(reader)
        router = _call(iv.oracle, block)
        updated_at = _try(reader.priceUpdatedAt, block, router, asset, uoa)
        updated_at = None if updated_at is None else int(updated_at)

    return {
        "vault": vault.address,
        "borrower": _call(vault.borrower, block),
        "collateral_asset": asset,
        "target_asset": target_asset,
        "credit_asset": reserve,
        "intermediate_vault": iv_addr,
        "unit_of_account": uoa,
        "c_raw": c,
        "b_ext_raw": b_ext,
        "b_int_raw": b_int,
        "c_lp_raw": c_lp,
        "rebalance_fee_bps": phi,
        "c_value": c_value,
        "b_ext_value": b_ext_value,
        "b_int_value": b_int_value,
        "b_total_value": b_total_value,
        "liq_ltv_t_bps": liq_ltv,
        "max_feasible_liq_ltv_t_bps": max_feasible,
        "operating_ltv_t_bps": operating_ltv_bps,
        "health_factor_t": health,
        "can_liquidate": can_liquidate,
        "externally_liquidated": externally_liquidated,
        "price_of_c": price_status(max_age, reader_addr, updated_at, now),
    }


def pool_state(pool, registry, block: int | None, now: int) -> dict:
    """Pool-level state of a pooled long-tail vault: totals, excision band, open fallback record.

    `registry` is the PooledParamsRegistry contract (None when the pool has none).
    """
    iv = _call(pool.intermediateVault, block)
    target_vault = _call(pool.targetVault, block)
    target_asset = _call(pool.targetAsset, block)
    delta_x, d_max = (int(x) for x in _call(pool.pooledParams, block))
    active, finalized, scan, loss, repaid, neg_tot = _call(pool.fallbackState, block)
    price_ok, p_c = _call(pool.collateralPrice, block)
    band_feasible = None
    if registry is not None:
        band_feasible = bool(_call(registry.isBandFeasible, block, iv, target_vault, target_asset))
    age = _try(pool.priceAge, block)
    return {
        "pool": pool.address,
        "intermediate_vault": iv,
        "target_vault": target_vault,
        "collateral_asset": _call(pool.collateralAsset, block),
        "credit_asset": _call(pool.creditAsset, block),
        "loan_asset": target_asset,
        "residual_account": _call(pool.residualAccount, block),
        "open_accounts": int(_call(pool.openCount, block)),
        "c_tot_raw": int(_call(pool.cTot, block)),
        "c_lp_tot_raw": int(_call(pool.clpTot, block)),
        "posted_credit_raw": int(_call(pool.postedCredit, block)),
        "owed_credit_raw": int(_call(pool.owedCredit, block)),
        "b_ext_pool_raw": int(_call(pool.extDebt, block)),
        "iv_debt_raw": int(_call(pool.ivDebt, block)),
        "rate": int(_call(pool.rate, block)),
        "max_feasible_liq_ltv_t_bps": int(_call(pool.maxFeasibleLiqLTV, block)),
        "external_risk": bool(_call(pool.externalRisk, block)),
        "excision_band": {
            "delta_x_bps": delta_x,
            "d_max_bps": d_max,
            "enabled": delta_x != 0,
            "growth_bound_bps": int(_call(pool.excisionGrowthBound, block)),
            "band_feasible": band_feasible,
        },
        "fallback_record": {
            "open": bool(active),
            "finalized": bool(finalized),
            "scan_open": bool(scan),
            "loss_credit_raw": int(loss),
            "repaid_debt_raw": int(repaid),
            "negative_total_raw": int(neg_tot),
            "pending_splits": int(_call(pool.pendingSplits, block)),
        },
        "price_of_c": {
            "available": bool(price_ok),
            "bid": int(p_c),
            "fresh": bool(_call(pool.isPriceFresh, block)),
            "age": None if age is None else int(age),
        },
        "settle_events": int(_call(pool.settleEventCount, block)),
    }


def account_state(pool, borrower: str, block: int | None) -> dict:
    """One borrower account of a pooled long-tail vault."""
    c, ext_shares, clp, iv_shares, liq_ltv, ev, is_open = _call(pool.accounts, block, borrower)
    delta_x = int(_call(pool.pooledParams, block)[0])
    out = {
        "pool": pool.address,
        "borrower": borrower,
        "open": bool(is_open),
        "c_raw": int(c),
        "ext_shares": int(ext_shares),
        "c_lp_raw": int(clp),
        "iv_shares": int(iv_shares),
        "liq_ltv_t_bps": int(liq_ltv),
        "settle_events_replayed": int(ev),
        "excision_trigger_ltv_bps": int(liq_ltv) + delta_x if delta_x else None,
        "split_pending": bool(_call(pool.isSplitPending, block, borrower)),
        "attributed": bool(_call(pool.attributedFor, block, borrower)),
        "pending_loss_raw": int(_call(pool.pendingLoss, block, borrower)),
    }
    if not is_open:
        return out
    # Amount views that need no price of C
    out["b_ext_raw"] = int(_call(pool.bExtOf, block, borrower))
    out["b_int_raw"] = int(_call(pool.bIntOf, block, borrower))
    out["iv_debt_raw"] = int(_call(pool.ivDebtOf, block, borrower))
    out["required_credit_raw"] = _opt_int(_try(pool.requiredCreditOf, block, borrower))
    # Views that read the price of C revert beyond the liquidation bound: report null, not an error
    out["c_value"] = _opt_int(_try(pool.cValueOf, block, borrower))
    out["b_total_value"] = _opt_int(_try(pool.totalDebtOf, block, borrower))
    out["ltv_t_bps"] = _opt_int(_try(pool.ltvOf, block, borrower))
    out["excess_credit"] = _opt_int(_try(pool.excessOf, block, borrower))
    out["incentive_bps"] = _opt_int(_try(pool.incentiveOf, block, borrower))
    out["liquidatable"] = _opt_bool(_try(pool.isLiquidatable, block, borrower))
    out["excisable"] = _opt_bool(_try(pool.isExcisable, block, borrower))
    ltv = out["ltv_t_bps"]
    out["health_factor_t"] = None if not ltv or ltv >= 2**255 else out["liq_ltv_t_bps"] / ltv
    return out


def residual_state(pool, residual, iv, block: int | None) -> dict:
    """The residual account R of loss excision: unsold C held as collateral, IV debt it owes.

    `iv` is the intermediate vault contract (EVault ABI).
    """
    return {
        "pool": pool.address,
        "residual_account": residual.address,
        "collateral_asset": _call(residual.asset, block),
        "c_held_raw": int(_call(residual.totalAssets, block)),
        "iv_debt_raw": int(_call(iv.debtOf, block, residual.address)),
        "intermediate_vault": iv.address,
    }


def _opt_int(v) -> int | None:
    return None if v is None else int(v)


def _opt_bool(v) -> bool | None:
    return None if v is None else bool(v)
