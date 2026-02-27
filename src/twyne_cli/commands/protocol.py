"""Protocol commands — overview, rates, ext-ltvs."""

import click

from ..cache import get_vault_cache
from ..constants import MAXFACTOR
from ..context import TwyneContext, pass_ctx
from ..contracts import (
    aave_v3_pool,
    credit_vault,
    intermediate_vaults,
    resolve_aave_factory_vault,
    vault_manager,
)
from ..formatting import (
    format_address,
    format_bps,
    is_tty,
    output_json,
    output_table,
)

# Aave eMode category IDs per Aave intermediate vault.
# Adding a new Aave IV already requires updating mainnet.json and
# aaveIVToFactoryVault, so updating this map at the same time is acceptable.
AAVE_EMODE_MAP: dict[str, int] = {
    "aave_awstETH": 1,
}


@click.group()
def protocol():
    """Query protocol-level state."""
    pass


@protocol.command()
@pass_ctx
def overview(ctx: TwyneContext):
    """Show collateral asset parameters and intermediate vault mappings."""
    ctx.connect()
    try:
        block = ctx.resolve_block()
        vm = vault_manager()
        iv_map = intermediate_vaults()

        # Build reverse map: collateral asset address → IV name.
        # Each IV's asset() returns its collateral token (e.g. eWETH).
        asset_to_iv: dict[str, str] = {}
        for iv_name, iv_addr in iv_map.items():
            try:
                iv_contract = credit_vault(iv_addr)
                asset_addr = str(iv_contract.asset(block_identifier=block)).lower()
                asset_to_iv[asset_addr] = iv_name
            except Exception:
                pass

        # Use cached vault+asset data instead of scanning events
        cache = get_vault_cache(ctx)
        unique_assets = cache.get_unique_assets(up_to_block=block)

        asset_data: list[dict] = []
        for asset_addr_lower, _vault_addrs in unique_assets.items():
            asset_addr = asset_addr_lower

            # Query VaultManager params live (governance-controlled, not cached).
            # Despite ABI param name, these mappings are keyed by collateral asset.
            try:
                max_ltv = vm.maxTwyneLTVs(asset_addr, block_identifier=block)
                ext_buffer = vm.externalLiqBuffers(asset_addr, block_identifier=block)
                iv_name = asset_to_iv.get(asset_addr_lower, "unknown")

                asset_data.append({
                    "collateral_asset": asset_addr,
                    "intermediate_vault": iv_name,
                    "max_twyne_ltv_bps": max_ltv,
                    "max_twyne_ltv_pct": max_ltv / MAXFACTOR * 100,
                    "external_liq_buffer_bps": ext_buffer,
                    "external_liq_buffer_pct": ext_buffer / MAXFACTOR * 100,
                })
            except Exception:
                asset_data.append({
                    "collateral_asset": asset_addr,
                    "error": "Failed to query VaultManager",
                })

        if ctx.force_json or not is_tty():
            output_json({
                "total_collateral_assets": len(asset_data),
                "collateral_assets": asset_data,
            })
        else:
            rows = []
            for a in asset_data:
                if "error" in a:
                    rows.append([format_address(a["collateral_asset"]), "?", "ERR", "ERR"])
                else:
                    rows.append([
                        a["intermediate_vault"],
                        format_address(a["collateral_asset"]),
                        format_bps(a["max_twyne_ltv_bps"]),
                        format_bps(a["external_liq_buffer_bps"]),
                    ])
            output_table(
                ["Intermediate Vault", "Collateral Asset", "Max LTV~", "Ext Liq Buffer"],
                rows,
                title="Protocol Overview — Collateral Asset Parameters",
            )
    finally:
        ctx.disconnect()


@protocol.command()
@click.argument("asset_or_iv_address")
@pass_ctx
def rates(ctx: TwyneContext, asset_or_iv_address: str):
    """Show parameters for a collateral asset or intermediate vault address."""
    ctx.connect()
    try:
        block = ctx.resolve_block()
        vm = vault_manager()

        # Try as collateral asset first — if maxTwyneLTVs returns non-zero, it's an asset
        max_ltv = vm.maxTwyneLTVs(asset_or_iv_address, block_identifier=block)

        if max_ltv > 0:
            # It's a collateral asset address
            ext_buffer = vm.externalLiqBuffers(asset_or_iv_address, block_identifier=block)
            iv_addr = vm.getIntermediateVault(asset_or_iv_address, block_identifier=block)

            rate_data = {
                "collateral_asset": asset_or_iv_address,
                "intermediate_vault": iv_addr,
                "max_twyne_ltv_bps": max_ltv,
                "max_twyne_ltv_pct": max_ltv / MAXFACTOR * 100,
                "external_liq_buffer_bps": ext_buffer,
                "external_liq_buffer_pct": ext_buffer / MAXFACTOR * 100,
            }

            # Try to get target vault count for the IV
            try:
                tv_len = vm.targetVaultLength(iv_addr, block_identifier=block)
                rate_data["target_vault_count"] = tv_len
            except Exception:
                pass

        else:
            # Might be an IV address — query target vault info
            rate_data = {
                "address": asset_or_iv_address,
                "note": "Not a registered collateral asset. If this is an IV, use 'protocol overview' to see all assets.",
            }

            try:
                tv_len = vm.targetVaultLength(asset_or_iv_address, block_identifier=block)
                rate_data["target_vault_count"] = tv_len
                targets = []
                for i in range(tv_len):
                    tv = vm.allowedTargetVaultList(asset_or_iv_address, i, block_identifier=block)
                    targets.append(tv)
                rate_data["allowed_target_vaults"] = targets
            except Exception:
                pass

        if ctx.force_json or not is_tty():
            output_json(rate_data)
        else:
            from ..formatting import output_kv
            pairs = []
            for k, v in rate_data.items():
                if k.endswith("_bps"):
                    pairs.append((k, format_bps(v)))
                elif isinstance(v, list):
                    pairs.append((k, ", ".join(str(x) for x in v)))
                else:
                    pairs.append((k, str(v)))
            output_kv(pairs, title="Protocol Rates")
    finally:
        ctx.disconnect()


# --------------------------------------------------------------------------- #
# ext-ltvs helpers
# --------------------------------------------------------------------------- #


def _fetch_euler_pairs(iv_name: str, iv_addr: str, vm, block) -> list[dict]:
    """Fetch external LTV pairs for an Euler intermediate vault."""
    iv = credit_vault(iv_addr)
    collateral = str(iv.asset(block_identifier=block))

    tv_count = vm.targetVaultLength(iv_addr, block_identifier=block)
    max_twyne = vm.maxTwyneLTVs(collateral, block_identifier=block)
    beta_safe = vm.externalLiqBuffers(collateral, block_identifier=block)

    pairs: list[dict] = []
    for i in range(tv_count):
        tv_addr = str(vm.allowedTargetVaultList(iv_addr, i, block_identifier=block))
        tv = credit_vault(tv_addr)

        liq_ltv = tv.LTVLiquidation(collateral, block_identifier=block)
        borr_ltv = tv.LTVBorrow(collateral, block_identifier=block)
        symbol = tv.symbol(block_identifier=block)
        ltv_full = tv.LTVFull(collateral, block_identifier=block)

        pairs.append({
            "iv_name": iv_name,
            "debt_vault": symbol,
            "debt_vault_address": tv_addr,
            "ext_liq_ltv_bps": liq_ltv,
            "ext_liq_ltv_pct": liq_ltv / MAXFACTOR * 100,
            "ext_borr_ltv_bps": borr_ltv,
            "ext_borr_ltv_pct": borr_ltv / MAXFACTOR * 100,
            "max_twyne_ltv_bps": max_twyne,
            "max_twyne_ltv_pct": max_twyne / MAXFACTOR * 100,
            "beta_safe_bps": beta_safe,
            "beta_safe_pct": beta_safe / MAXFACTOR * 100,
            "ltv_full": {
                "borrow_ltv_bps": ltv_full[0],
                "liquidation_ltv_bps": ltv_full[1],
                "initial_liquidation_ltv_bps": ltv_full[2],
                "target_timestamp": ltv_full[3],
                "ramp_duration": ltv_full[4],
            },
        })

    return pairs


def _fetch_aave_pairs(iv_name: str, iv_addr: str, vm, pool, block) -> list[dict]:
    """Fetch external LTV pairs for an Aave intermediate vault."""
    emode_id = AAVE_EMODE_MAP.get(iv_name)
    if emode_id is None:
        return [{"iv_name": iv_name, "error": f"No eMode mapping for {iv_name}"}]

    emode_data = pool.getEModeCategoryData(emode_id, block_identifier=block)
    # emode_data: (ltv, liquidationThreshold, liquidationBonus, priceSource, label)
    borr_ltv = emode_data[0]   # ltv = borrow LTV in bps
    liq_ltv = emode_data[1]    # liquidationThreshold = liquidation LTV in bps

    wrapper = resolve_aave_factory_vault(iv_addr)
    if wrapper is None:
        return [{"iv_name": iv_name, "error": f"No factory vault mapping for {iv_addr}"}]

    max_twyne = vm.maxTwyneLTVs(wrapper, block_identifier=block)
    beta_safe = vm.externalLiqBuffers(wrapper, block_identifier=block)

    return [{
        "iv_name": iv_name,
        "debt_vault": f"WETH(eMode{emode_id})",
        "emode_id": emode_id,
        "ext_liq_ltv_bps": liq_ltv,
        "ext_liq_ltv_pct": liq_ltv / MAXFACTOR * 100,
        "ext_borr_ltv_bps": borr_ltv,
        "ext_borr_ltv_pct": borr_ltv / MAXFACTOR * 100,
        "max_twyne_ltv_bps": max_twyne,
        "max_twyne_ltv_pct": max_twyne / MAXFACTOR * 100,
        "beta_safe_bps": beta_safe,
        "beta_safe_pct": beta_safe / MAXFACTOR * 100,
    }]


# --------------------------------------------------------------------------- #
# ext-ltvs command
# --------------------------------------------------------------------------- #


@protocol.command("ext-ltvs")
@pass_ctx
def ext_ltvs(ctx: TwyneContext):
    """Show external protocol liquidation LTV parameters for all Twyne pairs."""
    ctx.connect()
    try:
        block = ctx.resolve_block()
        vm = vault_manager()
        iv_map = intermediate_vaults()

        all_pairs: list[dict] = []
        for iv_name, iv_addr in iv_map.items():
            try:
                if iv_name.startswith("euler_"):
                    pairs = _fetch_euler_pairs(iv_name, iv_addr, vm, block)
                elif iv_name.startswith("aave_"):
                    pool = aave_v3_pool()
                    pairs = _fetch_aave_pairs(iv_name, iv_addr, vm, pool, block)
                else:
                    pairs = [{"iv_name": iv_name, "error": f"Unknown protocol prefix for {iv_name}"}]
                all_pairs.extend(pairs)
            except Exception as exc:
                all_pairs.append({"iv_name": iv_name, "error": str(exc)})

        if ctx.force_json or not is_tty():
            output_json({"total_pairs": len(all_pairs), "pairs": all_pairs})
        else:
            rows = []
            for p in all_pairs:
                if "error" in p:
                    rows.append([p["iv_name"], "ERR", "ERR", "ERR", "ERR", p["error"]])
                else:
                    rows.append([
                        p["iv_name"],
                        p["debt_vault"],
                        format_bps(p["ext_liq_ltv_bps"]),
                        format_bps(p["ext_borr_ltv_bps"]),
                        format_bps(p["max_twyne_ltv_bps"]),
                        format_bps(p["beta_safe_bps"]),
                    ])
            output_table(
                ["IV Name", "Debt Vault", "Ext Liq LTV~", "Ext Borr LTV", "Max Twyne~", "Beta Safe"],
                rows,
                title="External Protocol LTV Parameters",
            )
    finally:
        ctx.disconnect()
