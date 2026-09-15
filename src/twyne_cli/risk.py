"""Read governance parameters per collateral/debt pair."""

from .constants import MAXFACTOR
from .contracts import (
    aave_v3_pool,
    collateral_vault_factory,
    credit_vault,
    erc20,
    target_vaults,
    uses_pair_risk,
    vault_manager,
)


def pair_risk(iv: str, debt_asset: str, block=None, vm=None) -> dict:
    """Read the three values in VaultManager V5's liqParams return order."""
    manager = vm if vm is not None else vault_manager()
    if uses_pair_risk():
        buffer, maximum, borrow_buffer = manager.liqParams(iv, debt_asset, block_identifier=block)
    else:
        buffer = manager.externalLiqBuffers(iv, block_identifier=block)
        maximum = manager.maxTwyneLTVs(iv, block_identifier=block)
        borrow_buffer = 0
    return {
        "max_twyne_ltv_bps": int(maximum),
        "max_twyne_ltv_pct": int(maximum) / MAXFACTOR * 100,
        "external_liq_buffer_bps": int(buffer),
        "external_liq_buffer_pct": int(buffer) / MAXFACTOR * 100,
        "borrow_buffer_bps": int(borrow_buffer),
        "borrow_buffer_pct": int(borrow_buffer) / MAXFACTOR * 100,
        "beta_safe_bps": int(buffer),
        "beta_safe_pct": int(buffer) / MAXFACTOR * 100,
    }


def allowed_pairs(iv_name: str, iv_addr: str, block=None, vm=None, pool=None) -> list[dict]:
    """Discover permitted pairs and read external parameters at one block."""
    manager = vm if vm is not None else vault_manager()
    iv = credit_vault(iv_addr)
    collateral = str(iv.asset(block_identifier=block))
    common = {"iv_name": iv_name, "intermediate_vault": iv_addr, "collateral_asset": collateral}
    pairs = []
    if iv_name.startswith("euler_"):
        for name, target in target_vaults().items():
            if not name.startswith("euler_") or not manager.isAllowedTargetVault(iv_addr, target, block_identifier=block):
                continue
            vault = credit_vault(target)
            debt = str(vault.asset(block_identifier=block))
            full = vault.LTVFull(collateral, block_identifier=block)
            pairs.append({
                **common, **pair_risk(iv_addr, debt, block, manager),
                "debt_asset": debt, "debt_vault": vault.symbol(block_identifier=block),
                "debt_vault_address": target,
                "ext_liq_ltv_bps": int(vault.LTVLiquidation(collateral, block_identifier=block)),
                "ext_borr_ltv_bps": int(vault.LTVBorrow(collateral, block_identifier=block)),
                "ltv_full": dict(zip(("borrow_ltv_bps", "liquidation_ltv_bps", "initial_liquidation_ltv_bps", "target_timestamp", "ramp_duration"), map(int, full))),
            })
    elif iv_name.startswith("aave_"):
        pool = pool if pool is not None else aave_v3_pool()
        target = str(pool.address)
        factory = collateral_vault_factory()
        underlying = str(credit_vault(collateral).asset(block_identifier=block))
        reserve = pool.getReserveData(underlying, block_identifier=block)
        reserve_id = int(reserve.id)
        configuration = int(pool.getConfiguration(underlying, block_identifier=block).data)
        for debt in pool.getReservesList(block_identifier=block):
            debt = str(debt)
            if not manager.isAllowedTargetAssets(iv_addr, target, debt, block_identifier=block):
                continue
            category = int(factory.categoryId(target, collateral, debt, block_identifier=block))
            borrow_ltv = configuration & 0xFFFF
            liq_ltv = (configuration >> 16) & 0xFFFF
            if category:
                bitmap = int(pool.getEModeCategoryCollateralBitmap(category, block_identifier=block))
                if bitmap & (1 << reserve_id):
                    config = pool.getEModeCategoryCollateralConfig(category, block_identifier=block)
                    borrow_ltv, liq_ltv = int(config[0]), int(config[1])
            pairs.append({
                **common, **pair_risk(iv_addr, debt, block, manager),
                "debt_asset": debt, "debt_vault": erc20(debt).symbol(block_identifier=block),
                "debt_vault_address": target, "emode_id": category,
                "ext_liq_ltv_bps": liq_ltv, "ext_borr_ltv_bps": borrow_ltv,
            })
    else:
        raise ValueError(f"Unknown protocol for {iv_name}.")
    for pair in pairs:
        pair["ext_liq_ltv_pct"] = pair["ext_liq_ltv_bps"] / MAXFACTOR * 100
        pair["ext_borr_ltv_pct"] = pair["ext_borr_ltv_bps"] / MAXFACTOR * 100
    return pairs
