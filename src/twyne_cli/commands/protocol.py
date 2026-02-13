"""Protocol commands — overview, rates."""

import click
from ape import Contract

from ..context import TwyneContext, pass_ctx
from ..constants import MAXFACTOR
from ..contracts import (
    collateral_vault,
    collateral_vault_factory,
    intermediate_vaults,
    vault_manager,
    _load_abi,
)
from ..formatting import (
    format_address,
    format_bps,
    is_tty,
    output_json,
    output_table,
)


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
        factory = collateral_vault_factory()
        iv_map = intermediate_vaults()

        # Reverse map: IV address → IV name
        iv_addr_to_name = {addr.lower(): name for name, addr in iv_map.items()}

        # Scan factory events to discover unique collateral assets
        click.echo("Scanning factory events to discover collateral assets...", err=True)
        from ape import chain
        stop_block = block if block else chain.blocks.height
        events = list(factory.T_CollateralVaultCreated.range(0, stop_block))

        # Collect unique assets from a sample of vaults
        cv_abi = _load_abi("CollateralVault")
        seen_assets: set[str] = set()
        asset_data: list[dict] = []

        for evt in events:
            vault_addr = evt.vault
            try:
                cv = Contract(vault_addr, abi=cv_abi)
                asset_addr = cv.asset(block_identifier=block)
            except Exception:
                continue

            if asset_addr.lower() in seen_assets:
                continue
            seen_assets.add(asset_addr.lower())

            # Query VaultManager params keyed by collateral asset address
            try:
                max_ltv = vm.maxTwyneLTVs(asset_addr, block_identifier=block)
                ext_buffer = vm.externalLiqBuffers(asset_addr, block_identifier=block)
                iv_addr = vm.getIntermediateVault(asset_addr, block_identifier=block)
                iv_name = iv_addr_to_name.get(iv_addr.lower(), format_address(iv_addr))

                asset_data.append({
                    "collateral_asset": asset_addr,
                    "intermediate_vault": iv_name,
                    "iv_address": iv_addr,
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
                        format_address(a["collateral_asset"]),
                        a["intermediate_vault"],
                        format_bps(a["max_twyne_ltv_bps"]),
                        format_bps(a["external_liq_buffer_bps"]),
                    ])
            output_table(
                ["Collateral Asset", "Intermediate Vault", "Max LTV", "Ext Liq Buffer"],
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
