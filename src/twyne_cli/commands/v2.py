"""Credit~loan V2 and pooled long-tail vault queries (read-only)."""

import click

from ..context import TwyneContext, pass_ctx
from ..formatting import is_tty, output_json, output_kv

ZERO = "0x0000000000000000000000000000000000000000"


def _kw(block: int | None) -> dict:
    return {"block_identifier": block} if block is not None else {}


def _bps(v: int | None) -> str:
    return "N/A" if v is None else f"{v / 100:.2f}% ({v} bps)"


def _hf(v: float | None) -> str:
    return "N/A (no debt)" if v is None else f"{v:.4f}"


def _val(v: int | None) -> str:
    return "N/A (price unavailable)" if v is None else str(v)


def _block_time(block: int | None) -> int:
    from ape import chain

    return int(chain.blocks[block if block is not None else "latest"].timestamp)


def _emit(ctx: TwyneContext, data: dict, rows: list[tuple[str, str]], title: str):
    if ctx.force_json or not is_tty():
        output_json(data)
    else:
        output_kv(rows, title=title)


@click.group("v2")
def v2():
    """Credit~loan V2 vaults and pooled long-tail vaults (no V2 contract is deployed yet)."""


@v2.command("vault")
@click.argument("address")
@pass_ctx
def vault_cmd(ctx: TwyneContext, address: str):
    """Show C, B_ext, B_int, B_total, C_LP, liqLTV_t, health, max feasible liqLTV_t and price staleness."""
    from .. import v2 as v2mod
    from ..contracts import (
        collateral_vault_v2,
        credit_vault,
        price_freshness_reader,
        vault_manager_v2,
    )

    ctx.connect()
    try:
        block = ctx.resolve_block()
        vault = collateral_vault_v2(address)
        manager = vault_manager_v2(vault.twyneVaultManager(**_kw(block)))
        iv = credit_vault(vault.intermediateVault(**_kw(block)))
        reader_addr = manager.priceFreshnessReader(**_kw(block))
        reader = None if reader_addr == ZERO else price_freshness_reader(reader_addr)
        oracle = _router(iv, block)
        s = v2mod.vault_v2_state(vault, manager, iv, oracle, reader, block, _block_time(block))
        p = s["price_of_c"]
        _emit(
            ctx,
            s,
            [
                ("Vault", s["vault"]),
                ("Borrower", s["borrower"]),
                ("Collateral Asset", s["collateral_asset"]),
                ("Loan Asset", s["target_asset"]),
                ("Credit Asset", s["credit_asset"]),
                ("", ""),
                ("C (raw)", str(s["c_raw"])),
                ("B_ext (raw)", str(s["b_ext_raw"])),
                ("B_int (raw)", str(s["b_int_raw"])),
                ("C_LP (raw)", str(s["c_lp_raw"])),
                ("C (value)", _val(s["c_value"])),
                ("B_total (value)", _val(s["b_total_value"])),
                ("", ""),
                ("liqLTV_t", _bps(s["liq_ltv_t_bps"])),
                ("Operating LTV_t", _bps(s["operating_ltv_t_bps"])),
                ("Health (liqLTV_t / LTV_t)", _hf(s["health_factor_t"])),
                ("Max feasible liqLTV_t", _bps(s["max_feasible_liq_ltv_t_bps"])),
                ("Can Liquidate", str(s["can_liquidate"])),
                ("Externally Liquidated", str(s["externally_liquidated"])),
                ("", ""),
                ("Price of C", p["status"]),
                ("Price age / bound (s)", f"{p['age']} / {p['max_age']}"),
                ("Risk-adding ops blocked", str(p["blocked_operations"])),
            ],
            "Credit~Loan V2 Vault",
        )
    finally:
        ctx.disconnect()


def _router(iv, block: int | None):
    """The intermediate vault's router (quotes C, B_ext and B_int in its unit of account)."""
    from ..contracts import oracle_router_at

    return oracle_router_at(iv.oracle(**_kw(block)))


@v2.command("pool")
@click.argument("address")
@pass_ctx
def pool_cmd(ctx: TwyneContext, address: str):
    """Show a pooled vault: totals, excision band, open fallback record, pending splits, residual account."""
    from .. import v2 as v2mod
    from ..contracts import pooled_params_registry, pooled_vault

    ctx.connect()
    try:
        block = ctx.resolve_block()
        pool = pooled_vault(address)
        reg_addr = pool.pooledParamsRegistry(**_kw(block))
        registry = None if reg_addr == ZERO else pooled_params_registry(reg_addr)
        s = v2mod.pool_state(pool, registry, block, _block_time(block))
        band, fb, px = s["excision_band"], s["fallback_record"], s["price_of_c"]
        _emit(
            ctx,
            s,
            [
                ("Pool", s["pool"]),
                ("Intermediate Vault", s["intermediate_vault"]),
                ("Target Vault", s["target_vault"]),
                ("Collateral / Credit / Loan", f"{s['collateral_asset']} / {s['credit_asset']} / {s['loan_asset']}"),
                ("Residual Account", s["residual_account"]),
                ("", ""),
                ("Open Accounts", str(s["open_accounts"])),
                ("C total (raw)", str(s["c_tot_raw"])),
                ("C_LP owed / posted (raw)", f"{s['owed_credit_raw']} / {s['posted_credit_raw']}"),
                ("B_ext pool / IV debt (raw)", f"{s['b_ext_pool_raw']} / {s['iv_debt_raw']}"),
                ("Max feasible liqLTV_t", _bps(s["max_feasible_liq_ltv_t_bps"])),
                ("External risk", str(s["external_risk"])),
                ("", ""),
                ("Excision delta_x", _bps(band["delta_x_bps"]) + ("" if band["enabled"] else " (disabled)")),
                ("Excision d_max", _bps(band["d_max_bps"])),
                ("Excision growth bound g^max", _bps(band["growth_bound_bps"])),
                ("Band feasible", str(band["band_feasible"])),
                ("", ""),
                ("Fallback record open", str(fb["open"])),
                ("Fallback finalized / scan open", f"{fb['finalized']} / {fb['scan_open']}"),
                ("Fallback loss / repaid (raw)", f"{fb['loss_credit_raw']} / {fb['repaid_debt_raw']}"),
                ("Pending splits", str(fb["pending_splits"])),
                ("", ""),
                ("Price of C available / fresh", f"{px['available']} / {px['fresh']}"),
                ("Price of C age (s)", str(px["age"])),
            ],
            "Pooled Long-Tail Vault",
        )
    finally:
        ctx.disconnect()


@v2.command("account")
@click.argument("pool")
@click.argument("borrower")
@pass_ctx
def account_cmd(ctx: TwyneContext, pool: str, borrower: str):
    """Show one borrower account of a pooled vault: debts, LTV, health, split and excision status."""
    from .. import v2 as v2mod
    from ..contracts import pooled_vault

    ctx.connect()
    try:
        block = ctx.resolve_block()
        s = v2mod.account_state(pooled_vault(pool), borrower, block)
        rows = [
            ("Pool", s["pool"]),
            ("Borrower", s["borrower"]),
            ("Open", str(s["open"])),
            ("C (raw)", str(s["c_raw"])),
            ("liqLTV_t", _bps(s["liq_ltv_t_bps"])),
            ("Excision trigger LTV", _bps(s["excision_trigger_ltv_bps"])),
            ("Split pending", str(s["split_pending"])),
            ("Attributed / pending loss (raw)", f"{s['attributed']} / {s['pending_loss_raw']}"),
        ]
        if s["open"]:
            rows += [
                ("B_ext (raw)", str(s["b_ext_raw"])),
                ("B_int (raw)", str(s["b_int_raw"])),
                ("C_LP (raw)", str(s["c_lp_raw"])),
                ("Required credit (raw)", _val(s["required_credit_raw"])),
                ("C (value)", _val(s["c_value"])),
                ("B_total (value)", _val(s["b_total_value"])),
                ("LTV_t", _bps(s["ltv_t_bps"])),
                ("Health (liqLTV_t / LTV_t)", _hf(s["health_factor_t"])),
                ("Excess credit", _val(s["excess_credit"])),
                ("Liquidatable", str(s["liquidatable"])),
                ("Excisable", str(s["excisable"])),
            ]
        _emit(ctx, s, rows, "Pooled Account")
    finally:
        ctx.disconnect()


@v2.command("residual")
@click.argument("pool")
@pass_ctx
def residual_cmd(ctx: TwyneContext, pool: str):
    """Show the residual account R of a pooled vault (unsold C and the debt it owes the intermediate vault)."""
    from .. import v2 as v2mod
    from ..contracts import credit_vault, pooled_residual_account, pooled_vault

    ctx.connect()
    try:
        block = ctx.resolve_block()
        p = pooled_vault(pool)
        r = pooled_residual_account(p.residualAccount(**_kw(block)))
        iv = credit_vault(p.intermediateVault(**_kw(block)))
        s = v2mod.residual_state(p, r, iv, block)
        _emit(
            ctx,
            s,
            [
                ("Pool", s["pool"]),
                ("Residual Account", s["residual_account"]),
                ("Collateral Asset", s["collateral_asset"]),
                ("C held (raw)", str(s["c_held_raw"])),
                ("IV debt owed (raw)", str(s["iv_debt_raw"])),
            ],
            "Pooled Residual Account",
        )
    finally:
        ctx.disconnect()
