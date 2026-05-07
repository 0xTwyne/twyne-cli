"""User command — portfolio across all vaults owned by a wallet."""

import click

from ..cache import get_vault_cache
from ..context import TwyneContext, pass_ctx
from ..contracts import collateral_vault
from ..formatting import (
    format_address,
    is_tty,
    output_json,
    output_table,
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

        click.echo(f"Found {len(owned_vaults)} vault(s).", err=True)
        click.echo(
            "Note: Health factors unavailable — HealthStatViewer removed in v1.0.5.",
            err=True,
        )

        vault_summaries = []
        for vault_addr in owned_vaults:
            vault_summaries.append({
                "vault": vault_addr,
                "note": "Health factors unavailable — HealthStatViewer removed in v1.0.5",
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
                rows.append([
                    format_address(v["vault"]),
                    "N/A",
                    "N/A",
                    "N/A",
                    "N/A",
                ])

            output_table(
                ["Vault", "Ext HF", "Int HF", "Ext Debt", "Risk"],
                rows,
                title=f"User Portfolio — {wallet}",
            )
    finally:
        ctx.disconnect()
