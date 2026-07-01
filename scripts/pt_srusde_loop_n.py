#!/usr/bin/env python3
"""
pt_srusde_loop_n.py — N-times leverage loop: PT-srUSDe ⇆ USDe, in ONE EVC batch.

Each loop is the single-iteration operation from `pt_srusde_loop.py`:

    USDe -> srUSDe (Strata) -> PT-srUSDe (Pendle) -> CV.depositUnderlying -> CV.borrow USDe

The borrowed USDe comes out as the *same asset* as the input, only smaller (by the
liquidation-LTV factor). So the next loop deposits that borrowed USDe, borrows again, and so
on — building geometric leverage. With `--loops N` all N iterations are bundled into a single
`EVC.batch(...)`:

  - The EVC transiently custodies the recycled USDe between loops (intermediate borrows are
    sent to the EVC; the final loop's borrow is delivered to the borrower).
  - Each loop's downstream amounts are derived from that loop's input via the same
    timestamp-pinned discovery pass (srUSDe is yield-bearing — pinning block.timestamp makes
    discovery byte-identical to execution), so the static batch is exact and leaks no value.

Same interface as the single-loop script, plus `--loops N`. Borrow per loop is sized to the
target HF / LTV (required, since each loop must borrow to recycle).

    pt_srusde_loop_n.py --cv <CV> [--amount <USDe>] --loops 4 --target-hf 1.04 [--dry-run|--execute]
"""

from __future__ import annotations

import os
import sys

import click

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)
import pt_srusde_loop as base  # noqa: E402  reuse Chain, helpers, constants
from pt_srusde_loop import (  # noqa: E402
    EVC,
    PT_SRUSDE,
    SRUSDE,
    STRATA_DEPOSITOR,
    WAD,
    AnvilFork,
    Chain,
    CVConfig,
    cd,
    read_cv_config,
    read_cv_state,
)


# =======================================================================================
# N-loop discovery (recycle each loop's borrow into the next loop's deposit)
# =======================================================================================
def discover_loops(
    ch: Chain, cfg: CVConfig, *, start: int, loops: int, T: int, slippage: float, targets: dict
) -> list[dict]:
    """Pinned-to-T discovery of every loop, inside a snapshot (reverted before returning).

    Returns one dict per executed loop: deposit / srusde_out / pt_out / min_pt / pendle_to /
    pendle_data / borrow. The borrow of loop i is the deposit of loop i+1 (recycled).
    """
    assert ch.controllable
    b = cfg.borrower
    out: list[dict] = []
    snap = ch.rpc("evm_snapshot")
    try:
        amt = start
        for i in range(loops):
            if amt <= 0:
                break
            sr0 = ch.erc20_balance(SRUSDE, b)
            ch.send_impersonated(b, STRATA_DEPOSITOR, base._strata_deposit_cd(cfg, amt, b), ts=T)
            srusde_out = ch.erc20_balance(SRUSDE, b) - sr0
            if srusde_out <= 0:
                raise click.ClickException(f"loop {i + 1}: Strata produced 0 srUSDe")

            pendle_to, pendle_data, _pt_q, min_pt = base._pendle_quote(srusde_out, b, slippage)
            pt0 = ch.erc20_balance(PT_SRUSDE, b)
            ch.send_impersonated(b, pendle_to, pendle_data, ts=T)
            pt_out = ch.erc20_balance(PT_SRUSDE, b) - pt0
            if pt_out <= 0:
                raise click.ClickException(f"loop {i + 1}: Pendle produced 0 PT")

            dep = base._encode_batch([(cfg.cv, b, 0, cd("depositUnderlying(uint256)", ["uint256"], [pt_out]))])
            ch.send_impersonated(b, EVC, dep, ts=T)
            post = read_cv_state(ch.rpc_url, base.HSV, cfg.cv, "latest")
            borrow = base._size_borrow(ch, cfg, post, **targets)
            # actually borrow so the position evolves for the next loop's sizing/recycling
            if borrow > 0:
                bb = base._encode_batch(
                    [(cfg.cv, b, 0, cd("borrow(uint256,address)", ["uint256", "address"], [borrow, b]))]
                )
                ch.send_impersonated(b, EVC, bb, ts=T)

            out.append(
                dict(
                    deposit=amt,
                    srusde_out=srusde_out,
                    pt_out=pt_out,
                    min_pt=min_pt,
                    pendle_to=pendle_to,
                    pendle_data=pendle_data,
                    borrow=borrow,
                )
            )
            amt = borrow  # recycle the borrowed USDe into the next loop
    finally:
        ch.rpc("evm_revert", [snap])
    if not out:
        raise click.ClickException("no loops executed — check amount / target")
    return out


def build_n_items(cfg: CVConfig, iters: list[dict], *, exact: bool):
    """Assemble the EVC batch items for all loops.

    Loop 1 pulls the borrower's USDe into the EVC; later loops reuse the recycled USDe already
    in the EVC. Intermediate borrows go to the EVC; the final loop's borrow goes to the borrower.
    """
    b = cfg.borrower
    n = len(iters)
    items: list[tuple[str, str, int, str]] = []
    for i, it in enumerate(iters):
        dep_usde = it["deposit"]
        if i == 0:
            items.append(
                (
                    cfg.usde,
                    b,
                    0,
                    cd("transferFrom(address,address,uint256)", ["address", "address", "uint256"], [b, EVC, dep_usde]),
                )
            )
        items.append((cfg.usde, b, 0, base._approve_cd(STRATA_DEPOSITOR, dep_usde)))
        items.append((STRATA_DEPOSITOR, b, 0, base._strata_deposit_cd(cfg, dep_usde, EVC)))
        items.append((SRUSDE, b, 0, base._approve_cd(it["pendle_to"], it["srusde_out"])))
        items.append((it["pendle_to"], b, 0, it["pendle_data"]))
        deposit_pt = it["pt_out"] if exact else it["min_pt"]
        items.append((cfg.cv, b, 0, cd("depositUnderlying(uint256)", ["uint256"], [deposit_pt])))
        if it["borrow"] > 0:
            receiver = b if i == n - 1 else EVC  # last borrow -> borrower; earlier -> recycle in EVC
            items.append(
                (cfg.cv, b, 0, cd("borrow(uint256,address)", ["uint256", "address"], [it["borrow"], receiver]))
            )
    return items


# =======================================================================================
# Reporting
# =======================================================================================
def print_loops(cfg: CVConfig, iters: list[dict], *, start: int, target_desc: str):
    click.echo("\n── Leverage loop plan " + "─" * 43)
    click.echo(f"  CV            {cfg.cv}")
    click.echo(f"  borrower      {cfg.borrower}")
    click.echo(f"  start         {start / WAD:.6f} USDe   ·   {len(iters)} loop(s)   ·   {target_desc}")
    total_dep = 0
    for i, it in enumerate(iters):
        total_dep += it["deposit"]
        tail = "→ borrower" if i == len(iters) - 1 else "→ recycle"
        click.echo(
            f"  loop {i + 1}: deposit {it['deposit'] / WAD:.6f} USDe → {it['pt_out'] / WAD:.6f} PT → "
            f"borrow {it['borrow'] / WAD:.6f} USDe {tail}"
        )
    lev = total_dep / start if start else 0
    click.echo(f"  total deposited {total_dep / WAD:.6f} USDe  →  ~{lev:.2f}x the {start / WAD:.6f} USDe input")


# =======================================================================================
# Engine
# =======================================================================================
def run_on_fork(ch: Chain, cfg: CVConfig, *, start: int, loops: int, slippage: float, targets: dict, max_approve: bool):
    b = cfg.borrower
    ch.rpc("anvil_setBalance", [b, hex(10**20)])
    before = read_cv_state(ch.rpc_url, base.HSV, cfg.cv, "latest")
    base.ensure_fork_approvals(ch, cfg, start)
    # discovery deposits to Strata once per loop -> needs a large USDe->Strata allowance
    ch.send_impersonated(b, cfg.usde, base._approve_cd(STRATA_DEPOSITOR, base.BIG_APPROVE))

    T = ch.latest_ts + 1
    iters = discover_loops(ch, cfg, start=start, loops=loops, T=T, slippage=slippage, targets=targets)
    print_loops(cfg, iters, start=start, target_desc=_target_desc(targets))

    batch = base._encode_batch(build_n_items(cfg, iters, exact=True))
    usde0 = ch.erc20_balance(cfg.usde, b)
    ch.set_next_ts(T)
    try:
        ch.call(EVC, batch, frm=b)
    except Exception as e:
        click.echo(f"\n  ✗ batch would revert: {repr(e)[:240]}")
        raise click.ClickException("batch reverted in simulation")
    rc = ch.send_impersonated(b, EVC, batch, ts=T)
    after = read_cv_state(ch.rpc_url, base.HSV, cfg.cv, "latest")

    click.echo("\n── Result (fork) " + "─" * 48)
    click.echo(
        f"  batch status: {'OK' if int(rc['status'], 16) else 'FAILED'}  gas={int(rc['gasUsed'], 16)}  items={len(build_n_items(cfg, iters, exact=True))}"
    )
    base.print_position("before", before)
    base.print_position("after", after)
    usde_delta = (ch.erc20_balance(cfg.usde, b) - usde0) / WAD
    click.echo(f"  borrower USDe net: {usde_delta:+.6f}  (final-loop borrow − {start / WAD:.6f} start)")
    base.leak_report(ch, cfg)


def run_live(
    ch: Chain,
    cfg: CVConfig,
    *,
    start: int,
    loops: int,
    slippage: float,
    targets: dict,
    fork_url: str,
    max_approve: bool,
    yes: bool,
):
    signer = base._resolve_signer(cfg.borrower)
    with AnvilFork(fork_url, block=None) as fork:
        fch = Chain(fork.rpc_url)
        fcfg = read_cv_config(fch, cfg.cv)
        fch.rpc("anvil_setBalance", [fcfg.borrower, hex(10**20)])
        base.ensure_fork_approvals(fch, fcfg, start)
        fch.send_impersonated(fcfg.borrower, fcfg.usde, base._approve_cd(STRATA_DEPOSITOR, base.BIG_APPROVE))
        T = fch.latest_ts + 1
        iters = discover_loops(fch, fcfg, start=start, loops=loops, T=T, slippage=slippage, targets=targets)
    # Live can't pin block.timestamp: re-quote each loop's Pendle with a shaved srUSDe input
    # (base.LIVE_SRUSDE_BUFFER_BPS) so no swap comes up short, and deposit each loop's floor.
    # Recycled USDe amounts (CV.borrow outputs) are exact, so the per-loop deposit chain holds.
    live_iters = []
    for it in iters:
        shave = max(int(it["srusde_out"] * base.LIVE_SRUSDE_BUFFER_BPS / 10_000), 1)
        srusde_in = it["srusde_out"] - shave
        ptt, pdata, pt_q, min_pt = base._pendle_quote(srusde_in, cfg.borrower, slippage)
        live_iters.append(dict(it, srusde_out=srusde_in, pt_out=pt_q, min_pt=min_pt, pendle_to=ptt, pendle_data=pdata))
    print_loops(cfg, live_iters, start=start, target_desc=_target_desc(targets))
    click.echo(f"  live drift buffer: {base.LIVE_SRUSDE_BUFFER_BPS:g} bps srUSDe per loop")

    apps = base.needed_approvals(
        ch, cfg, base.Plan(start, 0, 0, 0, 0, base.PENDLE_ROUTER, "0x", ""), deposit_amt=start, max_approve=max_approve
    )
    apps = [a for a in apps if a["spender"] != cfg.cv]  # PT approval must cover the SUM of per-loop deposits
    pt_need = sum(it["min_pt"] for it in live_iters)
    if ch.erc20_allowance(cfg.pt, cfg.borrower, cfg.cv) < pt_need:
        apps.append(
            dict(
                token=cfg.pt,
                spender=cfg.cv,
                amount=(base.MAX_UINT if max_approve else pt_need),
                label="PT-srUSDe -> CV (depositUnderlying, all loops)",
            )
        )
    if apps:
        click.echo("\n  Approvals required:")
        for a in apps:
            click.echo(f"    - {a['label']}")
    if not yes and not click.confirm("\nSubmit approvals + EVC batch to the live chain?", default=False):
        click.echo("Aborted.")
        return

    before = read_cv_state(ch.rpc_url, base.HSV, cfg.cv, "latest")
    for a in apps:
        base._broadcast_signed(
            ch, signer, a["token"], base._approve_cd(a["spender"], a["amount"]), label=a["label"], require_success=True
        )
    base._confirm_allowances(ch, cfg.borrower, [(cfg.usde, EVC, start), (cfg.pt, cfg.cv, pt_need)])

    batch = base._encode_batch(build_n_items(cfg, live_iters, exact=False))
    try:
        ch.call(EVC, batch, frm=cfg.borrower)  # decoded pre-check before spending gas
    except Exception as e:
        raise click.ClickException(f"batch pre-check reverted (nothing broadcast): {repr(e)[:240]}")
    rc = base._broadcast_signed(ch, signer, EVC, batch, label="EVC batch")
    status = int(rc["status"], 16)
    click.echo(
        f"\n  batch tx: {rc['transactionHash']}  status={'OK' if status else 'FAILED'}  gas={int(rc['gasUsed'], 16)}"
    )
    after = read_cv_state(ch.rpc_url, base.HSV, cfg.cv, "latest")
    base.print_position("before", before)
    base.print_position("after", after)


def _target_desc(targets: dict) -> str:
    if targets["target_hf"] is not None:
        return f"target HF {targets['target_hf']}"
    if targets["target_ltv"] is not None:
        return f"target LTV {targets['target_ltv']:.2%}"
    return "explicit borrow"


def run(*, cv, amount, loops, slippage, targets, do_execute, impersonate, rpc, fork_url, fork_block, max_approve, yes):
    if not do_execute:
        with AnvilFork(fork_url, block=fork_block) as fork:
            ch = Chain(fork.rpc_url)
            cfg = read_cv_config(ch, cv)
            start = base._resolve_start(ch, cfg, amount)
            run_on_fork(ch, cfg, start=start, loops=loops, slippage=slippage, targets=targets, max_approve=max_approve)
        return

    ch = Chain(rpc)
    cfg = read_cv_config(ch, cv)
    start = base._resolve_start(ch, cfg, amount)
    if impersonate or ch.controllable:
        if not ch.controllable:
            raise click.ClickException("--impersonate requires the --rpc endpoint to be a controllable anvil fork")
        run_on_fork(ch, cfg, start=start, loops=loops, slippage=slippage, targets=targets, max_approve=max_approve)
    else:
        run_live(
            ch,
            cfg,
            start=start,
            loops=loops,
            slippage=slippage,
            targets=targets,
            fork_url=fork_url,
            max_approve=max_approve,
            yes=yes,
        )


# =======================================================================================
# CLI
# =======================================================================================
@click.command(context_settings=dict(help_option_names=["-h", "--help"]))
@click.option("--cv", required=True, help="Collateral Vault address (a PT-srUSDe market CV you own)")
@click.option("--amount", type=float, default=None, help="Starting USDe (default: borrower's full USDe balance)")
@click.option("--loops", type=int, required=True, help="Number of leverage loops to bundle into one batch")
@click.option("--target-hf", type=float, default=None, help="Per-loop borrow sized to this internal health factor")
@click.option("--target-ltv", type=float, default=None, help="Per-loop borrow sized to this internal LTV (percent)")
@click.option("--slippage", type=float, default=0.5, help="Pendle swap slippage tolerance, percent (default 0.5)")
@click.option("--execute", "do_execute", is_flag=True, help="Broadcast for real (default is --dry-run)")
@click.option("--dry-run", "dry_run", is_flag=True, help="Simulate on a fork and report (default)")
@click.option("--rpc", default=base.DEFAULT_RPC, help="RPC endpoint for --execute (live chain or local anvil)")
@click.option("--fork-url", default=None, help="Upstream RPC to fork for --dry-run / live discovery (default: --rpc)")
@click.option("--fork-block", type=int, default=None, help="Pin the dry-run fork to a block (default: latest)")
@click.option("--impersonate", is_flag=True, help="Execute via anvil impersonation (fork testing; no key needed)")
@click.option("--max-approve", is_flag=True, help="Approve max uint256 instead of the exact amount")
@click.option("--yes", is_flag=True, help="Skip the live-execute confirmation prompt")
def main(
    cv,
    amount,
    loops,
    target_hf,
    target_ltv,
    slippage,
    do_execute,
    dry_run,
    rpc,
    fork_url,
    fork_block,
    impersonate,
    max_approve,
    yes,
):
    """Build N geometric leverage loops (PT-srUSDe ⇆ USDe) in a single EVC batch."""
    if do_execute and dry_run:
        raise click.ClickException("--dry-run and --execute are mutually exclusive")
    if loops < 1:
        raise click.ClickException("--loops must be >= 1")
    if (target_hf is None) == (target_ltv is None):
        raise click.ClickException(
            "provide exactly one of --target-hf / --target-ltv (each loop must borrow to recycle)"
        )
    targets = dict(
        target_hf=target_hf,
        target_ltv=(target_ltv / 100.0) if target_ltv is not None else None,
        borrow_explicit=None,
    )
    run(
        cv=cv,
        amount=amount,
        loops=loops,
        slippage=slippage / 100.0,
        targets=targets,
        do_execute=do_execute,
        impersonate=impersonate,
        rpc=rpc,
        fork_url=fork_url or rpc,
        fork_block=fork_block,
        max_approve=max_approve,
        yes=yes,
    )


if __name__ == "__main__":
    main()
