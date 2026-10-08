"""User command — portfolio across all vaults owned by a wallet."""

import click

from ..cache import get_vault_cache
from ..context import TwyneContext, pass_ctx
from ..contracts import collateral_vault
from ..formatting import (
    format_hf,
    format_value,
    hf_or_none,
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

        click.echo(f"Found {len(owned_vaults)} vault(s).", err=True)
        from .. import lens
        from ..morpho import detect_protocol

        vault_summaries = []
        for vault_addr in owned_vaults:
            try:
                cv = collateral_vault(vault_addr)
                protocol = detect_protocol(cv, block)
                unit = lens.value_unit(protocol, cv, block)
                h = lens.health(vault_addr, block)
                vault_summaries.append({
                    "vault": vault_addr,
                    "protocol": protocol,
                    "external_hf": hf_or_none(h["ext_hf_raw"]),
                    "internal_hf": hf_or_none(h["in_hf_raw"]),
                    "_ext_raw": h["ext_hf_raw"],
                    "_in_raw": h["in_hf_raw"],
                    "external_debt_value": h["external_debt_value"],
                    "value_unit": unit,
                    "risk": risk_level(min(h["ext_hf_raw"], h["in_hf_raw"])),
                })
            except Exception as exc:
                vault_summaries.append({"vault": vault_addr, "error": str(exc)})

        if ctx.force_json or not is_tty():
            output_json({
                "wallet": wallet,
                "total_vaults": len(owned_vaults),
                "vaults": [{k: v for k, v in s.items() if not k.startswith("_")} for s in vault_summaries],
            })
        else:
            rows = []
            for v in vault_summaries:
                if "error" in v:
                    rows.append([v["vault"], "ERR", "ERR", "ERR", "ERR"])
                    continue
                rows.append([
                    v["vault"],
                    format_hf(v["_ext_raw"]),
                    format_hf(v["_in_raw"]),
                    format_value(v["external_debt_value"], v["value_unit"]),
                    v["risk"],
                ])
            output_table(
                ["Vault", "Ext HF", "Int HF", "Ext Debt", "Risk"],
                rows,
                title=f"User Portfolio — {wallet}",
            )
    finally:
        ctx.disconnect()
