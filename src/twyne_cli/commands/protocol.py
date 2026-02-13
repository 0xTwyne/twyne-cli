"""Protocol commands — overview, rates."""

import click
from ape import Contract
from ape_ethereum.multicall import Call

from ..context import TwyneContext, pass_ctx
from ..constants import MAXFACTOR, WAD
from ..contracts import intermediate_vaults, vault_manager, _load_abi
from ..formatting import (
    format_bps,
    format_usd,
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
    """Show intermediate vaults, TVL, and protocol parameters."""
    ctx.connect()
    try:
        block = ctx.resolve_block()
        iv_map = intermediate_vaults()
        vm = vault_manager()

        # For each intermediate vault, query parameters
        vault_data = []
        evault_abi = _load_abi("CollateralVault")  # EVaults share similar view interface

        for name, addr in iv_map.items():
            try:
                iv_contract = Contract(addr, abi=evault_abi)

                # Query VaultManager params — keyed by intermediate vault address
                max_ltv = vm.maxTwyneLTVs(addr, block_identifier=block)
                ext_buffer = vm.externalLiqBuffers(addr, block_identifier=block)

                # Try to get total assets from the intermediate vault
                try:
                    total_assets = iv_contract.totalAssetsDepositedOrReserved(block_identifier=block)
                except Exception:
                    total_assets = None

                vault_data.append({
                    "name": name,
                    "address": addr,
                    "max_twyne_ltv_bps": max_ltv,
                    "external_liq_buffer_bps": ext_buffer,
                    "total_assets_raw": str(total_assets) if total_assets is not None else "N/A",
                })
            except Exception as e:
                vault_data.append({
                    "name": name,
                    "address": addr,
                    "error": str(e),
                })

        if ctx.force_json or not is_tty():
            output_json({"intermediate_vaults": vault_data})
        else:
            rows = []
            for v in vault_data:
                if "error" in v:
                    rows.append([v["name"], v["address"][:10] + "...", "ERROR", "ERROR", "N/A"])
                else:
                    rows.append([
                        v["name"],
                        v["address"][:10] + "...",
                        format_bps(v["max_twyne_ltv_bps"]),
                        format_bps(v["external_liq_buffer_bps"]),
                        v["total_assets_raw"],
                    ])
            output_table(
                ["Name", "Address", "Max LTV", "Ext Buffer", "Total Assets (raw)"],
                rows,
                title="Protocol Overview — Intermediate Vaults",
            )
    finally:
        ctx.disconnect()


@protocol.command()
@click.argument("iv_address")
@pass_ctx
def rates(ctx: TwyneContext, iv_address: str):
    """Show interest rates and utilization for an intermediate vault."""
    ctx.connect()
    try:
        block = ctx.resolve_block()
        vm = vault_manager()

        # Query VaultManager params
        max_ltv = vm.maxTwyneLTVs(iv_address, block_identifier=block)
        ext_buffer = vm.externalLiqBuffers(iv_address, block_identifier=block)

        # Try to get EVault-specific rate data (CreditEVault extends EVault)
        # EVault has interestRate(), totalBorrows(), totalAssets(), etc.
        evault_abi = _load_abi("CollateralVault")
        iv = Contract(iv_address, abi=evault_abi)

        rate_data = {
            "intermediate_vault": iv_address,
            "max_twyne_ltv_bps": max_ltv,
            "external_liq_buffer_bps": ext_buffer,
        }

        # Try various EVault view functions — these may not all exist
        for fn_name in ["totalAssetsDepositedOrReserved", "maxRelease", "maxRepay"]:
            try:
                val = getattr(iv, fn_name)(block_identifier=block)
                rate_data[fn_name] = str(val)
            except Exception:
                rate_data[fn_name] = "N/A"

        if ctx.force_json or not is_tty():
            output_json(rate_data)
        else:
            pairs = [
                ("Intermediate Vault", iv_address),
                ("Max Twyne LTV", format_bps(max_ltv)),
                ("External Liq Buffer", format_bps(ext_buffer)),
            ]
            for key in ["totalAssetsDepositedOrReserved", "maxRelease", "maxRepay"]:
                if rate_data.get(key) != "N/A":
                    pairs.append((key, rate_data[key]))

            from ..formatting import output_kv
            output_kv(pairs, title="Intermediate Vault Rates")
    finally:
        ctx.disconnect()
