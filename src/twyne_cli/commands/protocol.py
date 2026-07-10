"""Protocol commands — overview, rates, ext-ltvs, tvl."""

import click

from ..cache import get_vault_cache
from ..completions import complete_asset_or_iv
from ..constants import DEFILLAMA_API_URL, DEFILLAMA_TWYNE_SLUG, MAXFACTOR
from ..context import TwyneContext, pass_ctx
from ..contracts import (
    aave_v3_pool,
    credit_vault,
    intermediate_vaults,
    target_vaults,
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

        # Build maps: asset → IV name, asset → IV address.
        # Each IV's asset() returns its collateral token (e.g. eWETH).
        asset_to_iv: dict[str, str] = {}
        asset_to_iv_addr: dict[str, str] = {}
        for iv_name, iv_addr in iv_map.items():
            try:
                iv_contract = credit_vault(iv_addr)
                asset_addr = str(iv_contract.asset(block_identifier=block)).lower()
                asset_to_iv[asset_addr] = iv_name
                asset_to_iv_addr[asset_addr] = iv_addr
            except Exception:
                pass

        # Use cached vault+asset data instead of scanning events
        cache = get_vault_cache(ctx)
        unique_assets = cache.get_unique_assets(up_to_block=block)

        asset_data: list[dict] = []
        for asset_addr_lower, _vault_addrs in unique_assets.items():
            asset_addr = asset_addr_lower

            # Query VaultManager params live (governance-controlled, not cached).
            # v1.0.5+: mappings are keyed by intermediate vault address.
            try:
                iv_addr = asset_to_iv_addr.get(asset_addr_lower)
                iv_name = asset_to_iv.get(asset_addr_lower, "unknown")
                if not iv_addr:
                    asset_data.append({
                        "collateral_asset": asset_addr,
                        "intermediate_vault": iv_name,
                        "error": "No IV mapping found",
                    })
                    continue

                max_ltv = vm.maxTwyneLTVs(iv_addr, block_identifier=block)
                ext_buffer = vm.externalLiqBuffers(iv_addr, block_identifier=block)

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
@click.argument("asset_or_iv_address", shell_complete=complete_asset_or_iv)
@pass_ctx
def rates(ctx: TwyneContext, asset_or_iv_address: str):
    """Show parameters for a collateral asset or intermediate vault address."""
    ctx.connect()
    try:
        block = ctx.resolve_block()
        vm = vault_manager()

        # v1.0.5+: maxTwyneLTVs and externalLiqBuffers are keyed by IV address.
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
            max_ltv = vm.maxTwyneLTVs(iv_addr, block_identifier=block)
            ext_buffer = vm.externalLiqBuffers(iv_addr, block_identifier=block)

            rate_data = {
                "intermediate_vault": iv_addr,
                "max_twyne_ltv_bps": max_ltv,
                "max_twyne_ltv_pct": max_ltv / MAXFACTOR * 100,
                "external_liq_buffer_bps": ext_buffer,
                "external_liq_buffer_pct": ext_buffer / MAXFACTOR * 100,
            }
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
    """Fetch external LTV pairs for an Euler intermediate vault.

    Target debt vaults are sourced from the chain's ``targetVaults`` address
    registry. Per-pair external LTVs are read directly from each target EVault.
    """
    iv = credit_vault(iv_addr)
    collateral = str(iv.asset(block_identifier=block))

    # maxTwyneLTVs/externalLiqBuffers are still keyed by IV address.
    max_twyne = vm.maxTwyneLTVs(iv_addr, block_identifier=block)
    beta_safe = vm.externalLiqBuffers(iv_addr, block_identifier=block)

    pairs: list[dict] = []
    for tv_name, tv_addr in target_vaults().items():
        if not tv_name.startswith("euler_"):
            continue  # aave_pool etc. are not Euler target EVaults
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

    # v1.0.5+: keyed by IV address, not wrapper/collateral asset
    max_twyne = vm.maxTwyneLTVs(iv_addr, block_identifier=block)
    beta_safe = vm.externalLiqBuffers(iv_addr, block_identifier=block)

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
