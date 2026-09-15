"""Protocol commands — overview, rates, ext-ltvs, tvl."""

import click

from ..completions import complete_asset_or_iv
from ..constants import DEFILLAMA_API_URL, DEFILLAMA_TWYNE_SLUG
from ..context import TwyneContext, pass_ctx
from ..contracts import (
    aave_v3_pool,
    credit_vault,
    intermediate_vaults,
    vault_manager,
)
from ..formatting import (
    format_address,
    format_bps,
    format_usd,
    is_tty,
    output_json,
    output_kv,
    output_table,
)
from ..risk import allowed_pairs


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

        asset_data = []
        for iv_name, iv_addr in iv_map.items():
            try:
                asset_data.extend(allowed_pairs(iv_name, iv_addr, block, vm))
            except Exception as exc:
                asset_data.append({"intermediate_vault": iv_addr, "error": str(exc)})

        if ctx.force_json or not is_tty():
            output_json({
                "total_collateral_assets": len({p.get("collateral_asset") for p in asset_data if "error" not in p}),
                "total_pairs": len(asset_data),
                "collateral_assets": asset_data,
            })
        else:
            rows = []
            for a in asset_data:
                if "error" in a:
                    rows.append([a["intermediate_vault"], "?", "?", "ERR", "ERR"])
                else:
                    rows.append([
                        a["intermediate_vault"],
                        format_address(a["collateral_asset"]),
                        format_address(a["debt_asset"]),
                        format_bps(a["max_twyne_ltv_bps"]),
                        format_bps(a["external_liq_buffer_bps"]),
                    ])
            output_table(
                ["Intermediate Vault", "Collateral Asset", "Debt Asset", "Max LTV~", "Ext Liq Buffer"],
                rows,
                title="Protocol Overview — Collateral Asset Parameters",
            )
    finally:
        ctx.disconnect()


@protocol.command()
@click.argument("asset_or_iv_address", shell_complete=complete_asset_or_iv)
@pass_ctx
def rates(ctx: TwyneContext, asset_or_iv_address: str):
    """Show parameters for a collateral asset or intermediate vault address."""
    ctx.connect()
    try:
        block = ctx.resolve_block()
        vm = vault_manager()

        # First check if the input is an IV address directly.
        is_iv = False
        try:
            is_iv = vm.isIntermediateVault(asset_or_iv_address, block_identifier=block)
        except Exception:
            pass

        if is_iv:
            iv_addr = asset_or_iv_address
        else:
            # Input might be a collateral asset — resolve to IV address
            iv_addr = None
            iv_map = intermediate_vaults()
            for _iv_name, _iv_addr in iv_map.items():
                try:
                    iv_contract = credit_vault(_iv_addr)
                    asset = str(iv_contract.asset(block_identifier=block))
                    if asset.lower() == asset_or_iv_address.lower():
                        iv_addr = _iv_addr
                        break
                except Exception:
                    continue

        if iv_addr:
            name = next((name for name, address in intermediate_vaults().items() if address.lower() == iv_addr.lower()), None)
            if name is None:
                raise click.ClickException("The intermediate vault is not in this chain's address registry.")
            pairs = allowed_pairs(name, iv_addr, block, vm)
            rate_data = {"intermediate_vault": iv_addr, "pairs": pairs}
            if len(pairs) == 1:
                rate_data.update(pairs[0])
            if not is_iv:
                rate_data["collateral_asset"] = asset_or_iv_address

        else:
            rate_data = {
                "address": asset_or_iv_address,
                "note": "Not a registered intermediate vault or collateral asset. Use 'protocol overview' to see all assets.",
            }

        if ctx.force_json or not is_tty():
            output_json(rate_data)
        else:
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
# tvl command (DefiLlama — no RPC needed)
# --------------------------------------------------------------------------- #


@protocol.command()
@pass_ctx
def tvl(ctx: TwyneContext):
    """Show Twyne TVL from DefiLlama (no RPC required)."""
    import httpx

    url = f"{DEFILLAMA_API_URL}/protocol/{DEFILLAMA_TWYNE_SLUG}"
    resp = httpx.get(url, timeout=15)
    resp.raise_for_status()
    data = resp.json()

    chain_tvls = data.get("currentChainTvls", {})
    eth_tvl = chain_tvls.get("Ethereum", 0)
    borrowed = chain_tvls.get("borrowed", 0)

    # Extract latest token breakdown
    tokens_usd = []
    chain_detail = data.get("chainTvls", {}).get("Ethereum", {})
    usd_entries = chain_detail.get("tokensInUsd", [])
    native_entries = chain_detail.get("tokens", [])

    latest_usd = usd_entries[-1]["tokens"] if usd_entries else {}
    latest_native = native_entries[-1]["tokens"] if native_entries else {}

    for symbol, usd_val in sorted(latest_usd.items(), key=lambda x: x[1], reverse=True):
        native_val = latest_native.get(symbol)
        tokens_usd.append({
            "symbol": symbol,
            "usd": usd_val,
            "amount": native_val,
        })

    if ctx.force_json or not is_tty():
        output_json({
            "tvl_usd": eth_tvl,
            "borrowed_usd": borrowed,
            "tokens": tokens_usd,
        })
    else:
        output_kv(
            [
                ("TVL (Ethereum)", format_usd(eth_tvl)),
                ("Borrowed", format_usd(borrowed)),
            ],
            title="Twyne Protocol TVL (DefiLlama)",
        )
        if tokens_usd:
            rows = []
            for t in tokens_usd:
                amt = f"{t['amount']:,.4f}" if t["amount"] is not None else "—"
                rows.append([t["symbol"], format_usd(t["usd"]), amt])
            output_table(
                ["Token", "USD Value", "Amount"],
                rows,
                title="Token Breakdown",
            )


# --------------------------------------------------------------------------- #
# ext-ltvs helpers
# --------------------------------------------------------------------------- #


def _fetch_euler_pairs(iv_name: str, iv_addr: str, vm, block) -> list[dict]:
    """Read permitted Euler pairs with debt-specific governance parameters."""
    return allowed_pairs(iv_name, iv_addr, block, vm)


def _fetch_aave_pairs(iv_name: str, iv_addr: str, vm, pool, block) -> list[dict]:
    """Read permitted Aave debt assets and the factory's current eMode IDs."""
    return allowed_pairs(iv_name, iv_addr, block, vm, pool)


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
