"""Vault commands — health, info, list, simulate.

`info` and `health` read the shared `vault_state.read_cv_state()` (backed by the
on-chain HealthStatViewer), so they report the same numbers as `simulate`.
"""

import click

from ..cache import get_vault_cache
from ..completions import complete_vault_address
from ..context import TwyneContext, pass_ctx
from ..formatting import (
    format_address,
    is_tty,
    output_json,
    output_kv,
    output_table,
)


def _amt(native: float, usd: float, sym: str) -> str:
    return f"{native:,.4f} {sym}  (${usd:,.2f})"


def _hf_str(v: float) -> str:
    return "∞" if v == float("inf") else f"{v:.4f}"


def _risk_level(state) -> str:
    """Qualitative bucket of the on-chain inHF (display label, not protocol math)."""
    if state.liquidatable:
        return "LIQUIDATABLE"
    if state.in_hf < 1.05:
        return "critical"
    if state.in_hf < 1.20:
        return "elevated"
    return "healthy"


def _read_state(ctx: TwyneContext, address: str, hsv: str | None):
    """Resolve RPC + HSV and read CV state; exit(1) with a message on failure."""
    from .. import vault_state as vs

    rpc = vs.resolve_read_rpc(ctx)
    hsv_addr = vs.resolve_hsv(ctx.chain.chain_id, hsv)
    block = ctx.resolve_block()
    try:
        return vs.read_cv_state(rpc, hsv_addr, address, block if block is not None else "latest"), hsv_addr
    except Exception as e:  # noqa: BLE001
        click.echo(f"Error: {e}", err=True)
        raise SystemExit(1) from e


@click.group()
def vault():
    """Query collateral vault state."""
    pass


@vault.command()
@click.argument("address", shell_complete=complete_vault_address)
@click.option("--hsv", default=None, help="HealthStatViewer address override")
@pass_ctx
def health(ctx: TwyneContext, address: str, hsv: str | None):
    """Show health factors, LTVs, and risk for a collateral vault."""
    st, hsv_addr = _read_state(ctx, address, hsv)
    risk = _risk_level(st)

    if ctx.force_json or not is_tty():
        output_json({"vault": address, "hsv": hsv_addr, "risk": risk, **st.to_dict()})
        return

    output_kv(
        [
            ("Vault", address),
            ("Protocol", st.protocol),
            ("", ""),
            ("Internal HF (inHF)", _hf_str(st.in_hf)),
            ("External HF (extHF)", _hf_str(st.ext_hf)),
            ("", ""),
            ("Operating LTV (LTV_t)", f"{st.twyne_ltv_pct:.2f}%"),
            ("Liquidation LTV (~LTV_t)", f"{st.twyne_liq_ltv_pct:.2f}%"),
            ("External LTV (LTV_e)", f"{st.external_ltv_pct:.2f}%"),
            ("External Liq LTV (~LTV_e)", f"{st.external_liq_ltv_pct:.2f}%"),
            ("", ""),
            ("Liquidatable", "yes" if st.liquidatable else "no"),
            ("Risk", risk),
        ],
        title="Vault Health",
    )


@vault.command()
@click.argument("address", shell_complete=complete_vault_address)
@click.option("--hsv", default=None, help="HealthStatViewer address override")
@pass_ctx
def info(ctx: TwyneContext, address: str, hsv: str | None):
    """Show full state of a collateral vault (collateral, debt, LTVs, health)."""
    st, hsv_addr = _read_state(ctx, address, hsv)

    if ctx.force_json or not is_tty():
        output_json({"vault": address, "hsv": hsv_addr, **st.to_dict()})
        return

    total_native = st.collateral_native + st.reserved_native
    total_usd = st.collateral_usd + st.reserved_usd
    output_kv(
        [
            ("Vault", address),
            ("Borrower", st.borrower),
            ("Protocol", st.protocol),
            ("Collateral Asset", st.collateral_token),
            ("Borrow Asset", st.debt_token),
            ("", ""),
            ("User Collateral (C)", _amt(st.collateral_native, st.collateral_usd, st.collateral_symbol)),
            ("Credit Reserved (C_LP)", _amt(st.reserved_native, st.reserved_usd, st.collateral_symbol)),
            ("Total Assets (C+C_LP)", _amt(total_native, total_usd, st.collateral_symbol)),
            ("Debt (B)", _amt(st.borrow_native, st.borrow_usd, st.debt_symbol)),
            ("", ""),
            ("Operating LTV (LTV_t)", f"{st.twyne_ltv_pct:.2f}%"),
            ("Liquidation LTV (~LTV_t)", f"{st.twyne_liq_ltv_pct:.2f}%  (cap {st.max_twyne_ltv_pct:.2f}%)"),
            ("External LTV (LTV_e)", f"{st.external_ltv_pct:.2f}%"),
            ("External Liq LTV (~LTV_e)", f"{st.external_liq_ltv_pct:.2f}%"),
            ("", ""),
            ("Internal HF (inHF)", _hf_str(st.in_hf)),
            ("External HF (extHF)", _hf_str(st.ext_hf)),
            ("Liquidatable", "yes" if st.liquidatable else "no"),
            ("Can Liquidate", str(st.can_liquidate)),
            ("Can Rebalance", str(st.can_rebalance)),
        ],
        title="Vault Info",
    )


@vault.command()
@click.argument("address", shell_complete=complete_vault_address)
@click.option("--tx-file", type=click.Path(exists=True), help="JSON tx file {to,from,data,value,chainId} to simulate")
@click.option("--to", "to_addr", help="Target contract (inline tx; overrides --tx-file)")
@click.option("--data", "data_hex", help="Calldata hex (inline tx)")
@click.option("--from", "from_addr", help="Sender to impersonate (inline tx)")
@click.option("--value", default=0, help="Wei value (inline tx)")
@click.option("--block", type=int, default=None, help="Fork at a historical block (default: latest)")
@click.option("--fork-url", default=None, help="Upstream RPC to fork (default: resolved per chain)")
@click.option("--rpc", "attach_rpc", default=None, help="Attach to an already-running fork instead of launching anvil")
@click.option("--hsv", default=None, help="HealthStatViewer address override")
@pass_ctx
def simulate(
    ctx: TwyneContext,
    address: str,
    tx_file: str | None,
    to_addr: str | None,
    data_hex: str | None,
    from_addr: str | None,
    value,
    block: int | None,
    fork_url: str | None,
    attach_rpc: str | None,
    hsv: str | None,
):
    """Simulate a tx against a collateral vault and show before/after state.

    Forks the chain with anvil, snapshots the CV's full state (collateral, debt,
    LTVs, USD values, health factors) before and after executing the calldata,
    and prints a side-by-side diff plus a safety verdict.

        twyne vault simulate <cv> --tx-file failing-repay-tx.json
    """
    from .. import simulate as sim

    if to_addr and data_hex:
        tx = {
            "to": to_addr,
            "from": from_addr or "0x0000000000000000000000000000000000000000",
            "data": data_hex,
            "value": value,
            "chainId": ctx.chain.chain_id,
        }
    elif tx_file:
        tx = sim.load_tx_file(tx_file)
    else:
        raise click.UsageError("provide --tx-file or both --to and --data")

    try:
        result = sim.simulate(
            cv_address=address, tx=tx, hsv_address=hsv, fork_url=fork_url, attach_rpc=attach_rpc, block=block
        )
    except Exception as e:  # noqa: BLE001
        click.echo(f"Error: {e}", err=True)
        raise SystemExit(1) from e

    if ctx.force_json or not is_tty():
        for k in ("_before_obj", "_after_obj", "_exec_obj"):
            result.pop(k, None)
        output_json(result)
    else:
        click.echo(sim.render_report(result))


@vault.command("list")
@pass_ctx
def list_vaults(ctx: TwyneContext):
    """List all collateral vaults (uses incremental cache)."""
    ctx.connect()
    try:
        cache = get_vault_cache(ctx)
        block = ctx.resolve_block()
        vaults = cache.get_vaults(up_to_block=block)

        if ctx.force_json or not is_tty():
            output_json({
                "total_vaults": len(vaults),
                "vaults": [
                    {"address": v.address, "block": v.block}
                    for v in vaults
                ],
            })
        else:
            rows = [
                [str(i + 1), format_address(v.address), v.address, str(v.block)]
                for i, v in enumerate(vaults)
            ]
            output_table(
                ["#", "Short", "Address", "Created Block"],
                rows,
                title=f"Collateral Vaults ({len(vaults)} total)",
            )
    finally:
        ctx.disconnect()
