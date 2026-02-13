"""User command — portfolio across all vaults owned by a wallet."""

import click

from ..cache import get_vault_cache
from ..context import TwyneContext, pass_ctx
from ..constants import WAD
from ..contracts import collateral_vault, health_stat_viewer
from ..formatting import (
    format_address,
    format_hf,
    format_usd,
    is_tty,
    output_json,
    output_table,
    risk_level,
)


@click.command()
@click.argument("wallet")
@pass_ctx
def user(ctx: TwyneContext, wallet: str):
    """Show all vaults owned by a wallet address."""
    ctx.connect()
    try:
        block = ctx.resolve_block()

        # Use cached vault list, query borrower() live (mutable via liquidation)
        cache = get_vault_cache(ctx)
        cached_vaults = cache.get_vaults(up_to_block=block)

        click.echo(f"Checking {len(cached_vaults)} vaults for owner {wallet}...", err=True)

        owned_vaults = []
        for v in cached_vaults:
            try:
                cv = collateral_vault(v.address)
                borrower = cv.borrower(block_identifier=block)
                if borrower.lower() == wallet.lower():
                    owned_vaults.append(v.address)
            except Exception:
                continue

        if not owned_vaults:
            if ctx.force_json or not is_tty():
                output_json({"wallet": wallet, "vaults": [], "total_vaults": 0})
            else:
                click.echo(f"\nNo vaults found for {wallet}")
            return

        click.echo(f"Found {len(owned_vaults)} vault(s). Fetching health data...")

        # Batch health queries
        hsv = health_stat_viewer()
        vault_summaries = []
        for vault_addr in owned_vaults:
            try:
                ext_hf, in_hf, ext_debt, int_debt = hsv.health(vault_addr, block_identifier=block)
                vault_summaries.append({
                    "vault": vault_addr,
                    "external_hf": str(ext_hf),
                    "internal_hf": str(in_hf),
                    "external_hf_display": format_hf(ext_hf),
                    "internal_hf_display": format_hf(in_hf),
                    "external_debt_usd": ext_debt / WAD,
                    "internal_debt_usd": int_debt / WAD,
                    "risk_level": risk_level(min(ext_hf, in_hf)),
                })
            except Exception as e:
                vault_summaries.append({
                    "vault": vault_addr,
                    "error": str(e),
                })

        if ctx.force_json or not is_tty():
            output_json({
                "wallet": wallet,
                "total_vaults": len(owned_vaults),
                "vaults": vault_summaries,
            })
        else:
            rows = []
            for v in vault_summaries:
                if "error" in v:
                    rows.append([format_address(v["vault"]), "ERROR", "ERROR", "N/A", "N/A"])
                else:
                    rows.append([
                        format_address(v["vault"]),
                        v["external_hf_display"],
                        v["internal_hf_display"],
                        format_usd(v["external_debt_usd"]),
                        v["risk_level"],
                    ])

            output_table(
                ["Vault", "Ext HF", "Int HF", "Ext Debt", "Risk"],
                rows,
                title=f"User Portfolio — {wallet}",
            )
    finally:
        ctx.disconnect()
