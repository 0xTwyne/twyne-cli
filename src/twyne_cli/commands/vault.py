"""Vault commands — health, info, list."""

import click
from ape.exceptions import ContractLogicError
from ape_ethereum.multicall import Call

from ..completions import complete_vault_address
from ..context import TwyneContext, pass_ctx
from ..constants import MAXFACTOR, USD_ADDRESS, WAD
from ..cache import get_vault_cache
from ..contracts import (
    aave_oracle,
    aave_v3_pool,
    collateral_vault,
    euler_oracle,
    health_stat_viewer,
)
from ..formatting import (
    format_address,
    format_bps,
    format_hf,
    format_usd,
    is_tty,
    output_json,
    output_kv,
    output_table,
    risk_level,
)


def _detect_protocol(cv_contract, block: int | None) -> str:
    """Detect whether a collateral vault is Aave or Euler based."""
    try:
        atoken = cv_contract.aToken(block_identifier=block)
        if atoken and int(atoken, 16) != 0:
            return "aave"
        return "euler"
    except (ContractLogicError, Exception):
        return "euler"


@click.group()
def vault():
    """Query collateral vault state."""
    pass


@vault.command()
@click.argument("address", shell_complete=complete_vault_address)
@pass_ctx
def health(ctx: TwyneContext, address: str):
    """Show health factors for a collateral vault."""
    ctx.connect()
    try:
        block = ctx.resolve_block()
        hsv = health_stat_viewer()

        ext_hf, in_hf, ext_debt, int_debt = hsv.health(address, block_identifier=block)

        if ctx.force_json or not is_tty():
            output_json({
                "vault": address,
                "external_hf": str(ext_hf),
                "internal_hf": str(in_hf),
                "external_hf_display": format_hf(ext_hf),
                "internal_hf_display": format_hf(in_hf),
                "external_debt_value": str(ext_debt),
                "internal_debt_value": str(int_debt),
                "external_debt_usd": ext_debt / WAD,
                "internal_debt_usd": int_debt / WAD,
                "risk_level": risk_level(min(ext_hf, in_hf)),
            })
        else:
            min_hf = min(ext_hf, in_hf)
            output_kv([
                ("Vault", address),
                ("External HF", f"{format_hf(ext_hf)}  (protocol liquidation proximity)"),
                ("Internal HF", f"{format_hf(in_hf)}  (Twyne liquidation proximity)"),
                ("External Debt", format_usd(ext_debt / WAD)),
                ("Internal Debt", format_usd(int_debt / WAD)),
                ("Risk Level", risk_level(min_hf)),
            ], title="Vault Health")
    finally:
        ctx.disconnect()


@vault.command()
@click.argument("address", shell_complete=complete_vault_address)
@pass_ctx
def info(ctx: TwyneContext, address: str):
    """Show full state of a collateral vault."""
    ctx.connect()
    try:
        block = ctx.resolve_block()
        cv = collateral_vault(address)

        # Phase 1: basic vault state via multicall
        call1 = Call()
        call1.add(cv.totalAssetsDepositedOrReserved)
        call1.add(cv.maxRelease)
        call1.add(cv.maxRepay)
        call1.add(cv.asset)
        call1.add(cv.targetAsset)
        call1.add(cv.twyneLiqLTV)
        call1.add(cv.borrower)
        call1.add(cv.canLiquidate)
        call1.add(cv.canRebalance)

        results1 = list(call1(block_identifier=block))
        total_assets = results1[0]
        max_release = results1[1]  # credit reserved (C_LP)
        max_repay = results1[2]    # debt (B)
        asset_addr = results1[3]
        target_asset_addr = results1[4]
        twyne_liq_ltv = results1[5]
        borrower = results1[6]
        can_liquidate = results1[7]
        can_rebalance = results1[8]

        user_collateral = total_assets - max_release

        # Detect protocol and get USD values
        is_aave = _detect_protocol(cv, block) == "aave"

        if is_aave:
            oracle = aave_oracle()
            pool = aave_v3_pool()

            call2 = Call()
            call2.add(oracle.getQuote, total_assets, asset_addr, USD_ADDRESS)
            call2.add(oracle.getQuote, max_release, asset_addr, USD_ADDRESS)
            call2.add(oracle.getQuote, user_collateral, asset_addr, USD_ADDRESS)
            call2.add(pool.getUserAccountData, address)

            results2 = list(call2(block_identifier=block))
            total_assets_usd = results2[0] / WAD
            credit_usd = results2[1] / WAD
            user_coll_usd = results2[2] / WAD
            debt_usd = results2[3].totalDebtBase / 1e8
        else:
            oracle = euler_oracle()

            call2 = Call()
            call2.add(oracle.getQuote, total_assets, asset_addr, USD_ADDRESS)
            call2.add(oracle.getQuote, max_release, asset_addr, USD_ADDRESS)
            call2.add(oracle.getQuote, user_collateral, asset_addr, USD_ADDRESS)
            call2.add(oracle.getQuote, max_repay, target_asset_addr, USD_ADDRESS)

            results2 = list(call2(block_identifier=block))
            total_assets_usd = results2[0] / WAD
            credit_usd = results2[1] / WAD
            user_coll_usd = results2[2] / WAD
            debt_usd = results2[3] / WAD

        # Health factors
        hsv = health_stat_viewer()
        ext_hf, in_hf, _, _ = hsv.health(address, block_identifier=block)

        # Operating LTV = debt / user_collateral (in USD terms)
        operating_ltv = (debt_usd / user_coll_usd * 100) if user_coll_usd > 0 else 0.0

        protocol_type = "Aave V3" if is_aave else "Euler"

        if ctx.force_json or not is_tty():
            output_json({
                "vault": address,
                "borrower": borrower,
                "protocol": protocol_type,
                "collateral_asset": asset_addr,
                "borrow_asset": target_asset_addr,
                "total_assets_raw": str(total_assets),
                "credit_reserved_raw": str(max_release),
                "user_collateral_raw": str(user_collateral),
                "debt_raw": str(max_repay),
                "total_assets_usd": total_assets_usd,
                "credit_reserved_usd": credit_usd,
                "user_collateral_usd": user_coll_usd,
                "debt_usd": debt_usd,
                "twyne_liq_ltv_bps": twyne_liq_ltv,
                "twyne_liq_ltv_pct": twyne_liq_ltv / 100,
                "operating_ltv_pct": operating_ltv,
                "external_hf": str(ext_hf),
                "internal_hf": str(in_hf),
                "can_liquidate": can_liquidate,
                "can_rebalance": can_rebalance,
                "risk_level": risk_level(min(ext_hf, in_hf)),
            })
        else:
            output_kv([
                ("Vault", address),
                ("Borrower", borrower),
                ("Protocol", protocol_type),
                ("Collateral Asset", asset_addr),
                ("Borrow Asset", target_asset_addr),
                ("", ""),
                ("User Collateral (C)", format_usd(user_coll_usd)),
                ("Credit Reserved (C_LP)", format_usd(credit_usd)),
                ("Total Assets (C+C_LP)", format_usd(total_assets_usd)),
                ("Debt (B)", format_usd(debt_usd)),
                ("", ""),
                ("Liquidation LTV", format_bps(twyne_liq_ltv)),
                ("Operating LTV", f"{operating_ltv:.2f}%"),
                ("External HF", format_hf(ext_hf)),
                ("Internal HF", format_hf(in_hf)),
                ("Risk Level", risk_level(min(ext_hf, in_hf))),
                ("", ""),
                ("Can Liquidate", str(can_liquidate)),
                ("Can Rebalance", str(can_rebalance)),
            ], title="Vault Info")
    finally:
        ctx.disconnect()


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
