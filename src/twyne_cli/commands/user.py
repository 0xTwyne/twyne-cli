"""User command — portfolio across all vaults owned by a wallet."""

import click

from ..context import TwyneContext, pass_ctx
from ..constants import WAD
from ..contracts import collateral_vault, collateral_vault_factory, health_stat_viewer
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
        factory = collateral_vault_factory()

        click.echo(f"Scanning factory events for vaults owned by {wallet}...")

        # Get all vault creation events
        events = list(factory.T_CollateralVaultCreated.range(0, None))

        # Filter to vaults owned by this wallet
        owned_vaults = []
        for e in events:
            try:
                cv = collateral_vault(str(e.vault))
                borrower = cv.borrower(block_identifier=block)
                if borrower.lower() == wallet.lower():
                    owned_vaults.append(str(e.vault))
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
