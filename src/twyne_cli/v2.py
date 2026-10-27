"""Read-only state of credit~loan V2 collateral vaults and pooled long-tail vaults.

Symbols follow the V2 specification: C collateral, B_ext external debt, B_int accrued internal debt
(`maxRelease() - totalReserved`), B_total = B_ext + (1 + phi) * B_int, C_LP reserved credit.
Raw amounts are in token units of their asset. B_total and C values are in the intermediate
vault's unit of account (C at the bid, liabilities at the ask), as the vault values them.

There is one liquidation trigger (`canLiquidate`): B_tot > dyn_liqLTV_t * C, with
dyn_liqLTV_t = min(liqLTV_t*, cap / C) and liqLTV_t* = min(liqLTV_t, max_liqLTV_t). A per-vault build
exposes neither `cap` nor dyn_liqLTV_t nor the reservation floor and target (integration-specific
internals); those come from the periphery lens (`TODO(lens)`). A pooled vault exposes them per account.

Each function takes already-built contract objects so the tests need no RPC.
"""

from __future__ import annotations

from typing import Any

from .constants import BPS

# Price-of-C status ("fresh price or revert": the router adapter for C is the one staleness bound)
FRESH = "fresh"
UNAVAILABLE = "unavailable"  # no fresh price of C: every operation but a plain repay reverts (CollateralPriceStale)

# VaultV2.selfSettlementMode(): how the borrower's own touches settle B_int
SELF_SETTLEMENT_MODES = {0: "caller-supplied", 1: "receipt-token-deposit", 2: "converter"}

ZERO_ADDRESS = "0x0000000000000000000000000000000000000000"


def _call(fn, block: int | None, *args):
    return fn(*args, block_identifier=block) if block is not None else fn(*args)


def _try(fn, block: int | None, *args) -> Any | None:
    """Call a view; None when it reverts (e.g. a price read beyond the liquidation bound)."""
    try:
        return _call(fn, block, *args)
    except Exception:  # noqa: BLE001
        return None


def vault_v2_state(vault, manager, iv, oracle, block: int | None) -> dict:
    """State of one V2 collateral vault.

    `vault`, `manager`, `iv` (intermediate vault, EVault ABI) and `oracle` (its router) are contract
    objects. `can_liquidate` is the vault's own single trigger; the health factor is measured against
    liqLTV_t*, which is an upper bound of dyn_liqLTV_t, so it never reads lower than the trigger does.
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
    _buffer, max_liq_ltv = (int(x) for x in _call(manager.liqParams, block, iv_addr, target_vault, target_asset))
    liq_ltv_star = min(liq_ltv, max_liq_ltv)
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
    # Estimate: the vault sums B_int in loan units, adds one unit and quotes once at the ask
    # (TODO(lens): the exact B_tot). The trigger itself is `can_liquidate`.
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
            health = liq_ltv_star * c_value / (b_total_value * BPS)  # liqLTV_t* / LTV_t
        else:
            operating_ltv_bps = None
            health = 0.0

    # Fresh price or revert: the router must quote C now (mirrors CreditLoanV2Checks.collateralPriceUsable)
    fresh = _try(oracle.getQuotes, block, c or 1, asset, uoa) is not None

    # Cap of a reservation (CreditLoanV2Checks.reservationRule): IV cash plus the credit held against the IV debt
    cash = _opt_int(_try(iv.cash, block))
    cash_cap = None if cash is None else cash + min(c_lp, iv_debt)

    # Newer than the base the vault was built on: absent views read as None, not as an error
    mode = _try(vault.selfSettlementMode, block) if hasattr(vault, "selfSettlementMode") else None
    mode = None if mode is None else int(mode)
    converter = tolerance = None
    if mode == 2:
        converter = _try(manager.settlementConverter, block, iv_addr, target_vault, target_asset)
        tolerance = _opt_int(_try(manager.settlementConverterTolerance, block, iv_addr, target_vault, target_asset))
    borrow_buffer = _opt_int(_try(manager.borrowBuffer, block, iv_addr, target_vault, target_asset))
    fee_cap = _opt_int(_try(manager.MAX_REBALANCE_FEE, block)) if hasattr(manager, "MAX_REBALANCE_FEE") else None

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
        "rebalance_fee_cap_bps": fee_cap,
        "c_value": c_value,
        "b_ext_value": b_ext_value,
        "b_int_value": b_int_value,
        "b_total_value": b_total_value,
        "liq_ltv_t_bps": liq_ltv,
        "max_liq_ltv_t_bps": max_liq_ltv,
        "liq_ltv_t_star_bps": liq_ltv_star,
        "dyn_liq_ltv_t_bps": None,  # TODO(lens): min(liqLTV_t*, cap / C); `cap` is not a vault view
        "max_feasible_liq_ltv_t_bps": max_feasible,
        "operating_ltv_t_bps": operating_ltv_bps,
        "health_factor_t": health,
        "can_liquidate": can_liquidate,
        "externally_liquidated": externally_liquidated,
        "price_of_c": {
            "status": FRESH if fresh else UNAVAILABLE,
            # without a fresh price every operation reverts with CollateralPriceStale, except a plain repay
            "blocked_operations": not fresh,
        },
        # Reservation of the borrower's own touch: max(floor, min(target, cash cap)). The floor (for B_ext)
        # and the target (capacity at liqLTV_t* * C) are integration-specific internals of the vault.
        "reservation": {
            "current_raw": c_lp,
            "floor_raw": None,  # TODO(lens)
            "target_raw": None,  # TODO(lens)
            "cash_cap_raw": cash_cap,
        },
        "self_settlement": {
            "mode": mode,
            "name": None if mode is None else SELF_SETTLEMENT_MODES.get(mode, "unknown"),
            "converter": converter,
            "converter_tolerance_bps": tolerance,
        },
        # Max-borrow ramp delta (1e4). 0 = no ramp. The ramp reads dyn_liqLTV_t. Risk-adding operations
        # must end at or below the ramped borrow LTV; the vault checks it, this read shows only whether it is on.
        "borrow_ramp": {
            "delta_bps": borrow_buffer,
            "enabled": None if borrow_buffer is None else borrow_buffer != 0,
        },
    }


def loss_excision_state(recorded_at, due_at, now: int) -> dict:
    """Who may call `exciseLoss`, and from when.

    Option A (has `lossExcisionDueAt`): from `due_at` anyone may call it; `due_at` 0 = no fallback record;
    2**256-1 = only governance may (`residualAfter` = 0). Option B (has `fallbackRecordedAt` only): anyone,
    at any time. Neither view: the ABI predates both, so nothing is known.
    """
    if recorded_at is None and due_at is None:
        return {"option": None, "recorded_at": None, "due_at": None, "status": "not-applicable", "seconds_left": None}
    recorded_at = int(recorded_at or 0)
    if due_at is None:
        return {"option": "B", "recorded_at": recorded_at, "due_at": None, "status": "permissionless", "seconds_left": 0}
    due_at = int(due_at)
    if due_at == 0:
        status, left = "no-record", None
    elif due_at >= 2**255:
        status, left = "governance-only", None
    elif now >= due_at:
        status, left = "due", 0
    else:
        status, left = "waiting", due_at - now
    return {"option": "A", "recorded_at": recorded_at, "due_at": due_at, "status": status, "seconds_left": left}


def pool_state(pool, registry, block: int | None, now: int) -> dict:
    """Pool-level state of a pooled long-tail vault: totals, excision band, open fallback record.

    `registry` is the PooledParamsRegistry contract (None when the pool has none).
    """
    iv = _call(pool.intermediateVault, block)
    target_vault = _call(pool.targetVault, block)
    target_asset = _call(pool.targetAsset, block)
    delta_x, d_max = (int(x) for x in _call(pool.pooledParams, block))
    loss, repaid, events, unapplied, uncovered = _call(pool.fallbackState, block)
    price_ok, p_c = _call(pool.collateralPrice, block)
    band_feasible = None
    if registry is not None:
        band_feasible = bool(_call(registry.isBandFeasible, block, iv, target_vault, target_asset))
    caps = _try(registry.poolCaps, block, iv, target_vault, target_asset) if registry is not None else None
    c_tot = int(_call(pool.cTot, block))
    b_ext_pool = int(_call(pool.extDebt, block))
    debt_cap, coll_cap = (None, None) if caps is None else (int(caps[0]), int(caps[1]))
    recorded_at = _try(pool.fallbackRecordedAt, block) if hasattr(pool, "fallbackRecordedAt") else None
    due_at = _try(pool.lossExcisionDueAt, block) if hasattr(pool, "lossExcisionDueAt") else None
    return {
        "pool": pool.address,
        "intermediate_vault": iv,
        "target_vault": target_vault,
        "collateral_asset": _call(pool.collateralAsset, block),
        "credit_asset": _call(pool.creditAsset, block),
        "loan_asset": target_asset,
        "residual_account": _call(pool.residualAccount, block),
        "open_accounts": int(_call(pool.openCount, block)),
        "c_tot_raw": c_tot,
        "c_lp_tot_raw": int(_call(pool.clpTot, block)),
        "posted_credit_raw": int(_call(pool.postedCredit, block)),
        "owed_credit_raw": int(_call(pool.owedCredit, block)),
        "b_ext_pool_raw": b_ext_pool,
        "iv_debt_raw": int(_call(pool.ivDebt, block)),
        "posted_book_raw": _opt_int(_try(pool.postedBookOf, block)),
        "residual_debt_raw": _opt_int(_try(pool.residualDebt, block)),
        "unbacked_loss_raw": _opt_int(_try(pool.unbackedLoss, block)),
        # Option A only (claim bucket); Option B has no bucket
        "bucket": {
            "c_raw": _opt_int(_try(pool.bucketC, block)) if hasattr(pool, "bucketC") else None,
            "debt_raw": _opt_int(_try(pool.bucketDebt, block)) if hasattr(pool, "bucketDebt") else None,
        },
        "max_feasible_liq_ltv_t_bps": int(_call(pool.maxFeasibleLiqLTV, block)),
        "external_risk": bool(_call(pool.externalRisk, block)),
        "excision_band": {
            "delta_x_bps": delta_x,
            "d_max_bps": d_max,
            "enabled": delta_x != 0,
            "growth_bound_bps": int(_call(pool.excisionGrowthBound, block)),
            "band_feasible": band_feasible,
        },
        # The open record: sums over its events. It is open while an account has not applied it.
        "fallback_record": {
            "open": int(unapplied) != 0,
            "events": int(events),
            "loss_credit_raw": int(loss),
            "repaid_debt_raw": int(repaid),
            "uncovered_loss_raw": int(uncovered),  # charges above the caps, not yet rebased
            "pending_splits": int(_call(pool.pendingSplits, block)),
        },
        # Per-pool size caps of the key (registry; 0 = no cap). None: the registry has no poolCaps.
        "caps": {
            "debt_cap_raw": debt_cap,
            "collateral_cap_raw": coll_cap,
            "debt_used_raw": b_ext_pool,
            "collateral_used_raw": c_tot,
            "debt_cap_reached": None if debt_cap is None else debt_cap != 0 and b_ext_pool >= debt_cap,
            "collateral_cap_reached": None if coll_cap is None else coll_cap != 0 and c_tot >= coll_cap,
        },
        "loss_excision": loss_excision_state(recorded_at, due_at, now),
        "price_of_c": {
            "available": bool(price_ok),
            "bid": int(p_c),
            "fresh": bool(_call(pool.isPriceFresh, block)),
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
    # Reservation of the account's own touch: max(floor, capacity target). The floor is for B_ext; the target
    # is the capacity at liqLTV_t* * C (needs the price of C). The pool reserves the target best effort.
    out["required_credit_raw"] = _opt_int(_try(pool.requiredCreditOf, block, borrower))
    out["capacity_credit_raw"] = _opt_int(_try(pool.capacityCreditOf, block, borrower))
    # Views that read the price of C revert beyond the liquidation bound: report null, not an error
    out["c_value"] = _opt_int(_try(pool.cValueOf, block, borrower))
    out["b_total_value"] = _opt_int(_try(pool.totalDebtOf, block, borrower))
    out["ltv_t_bps"] = _opt_int(_try(pool.ltvOf, block, borrower))
    # dyn_liqLTV_t = min(liqLTV_t*, capacity / C): the one trigger is LTV_t > dyn_liqLTV_t (or external risk)
    out["dyn_liq_ltv_t_bps"] = _opt_int(_try(pool.dynLiqLtvOf, block, borrower))
    out["capacity_value"] = _opt_int(_try(pool.capacityValueOf, block, borrower))
    out["excess_credit"] = _opt_int(_try(pool.excessOf, block, borrower))
    out["incentive_bps"] = _opt_int(_try(pool.incentiveOf, block, borrower))
    out["liquidatable"] = _opt_bool(_try(pool.isLiquidatable, block, borrower))
    out["excisable"] = _opt_bool(_try(pool.isExcisable, block, borrower))
    ltv, dyn = out["ltv_t_bps"], out["dyn_liq_ltv_t_bps"]
    out["health_factor_t"] = None if not ltv or ltv >= 2**255 or dyn is None else dyn / ltv  # dyn_liqLTV_t / LTV_t
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
