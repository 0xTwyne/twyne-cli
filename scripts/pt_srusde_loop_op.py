#!/usr/bin/env python3
"""
pt_srusde_loop_op.py — drive the StrataPendleLoopOperator (NO flashloan) to build a leveraged
PT-srUSDe position from ONLY the user's own USDe, on an anvil fork, with on-chain zero leak.

Leverage comes from recycling each loop's borrowed USDe into the next deposit — there is no
flashloan and nothing to repay, so it can't hit the "can't borrow enough to repay the loan"
wall the flashloan operator does near the LTV ceiling. The Solidity operator does the swaps
from balanceOf, so amounts chain exactly (zero residual in the operator or the EVC).

This driver: deploys the operator on a fork, computes the per-loop borrow schedule for a target
HF (reusing the loop-script discovery), builds the
`EVC.batch([setAccountOperator(true), executeLoop(params), setAccountOperator(false)])`, runs it,
and prints a zero-leak report.
"""

from __future__ import annotations

import json
import os
import sys

import click

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)
import pt_srusde_loop as base  # noqa: E402
import pt_srusde_loop_n as loopn  # noqa: E402
from pt_srusde_loop import (  # noqa: E402
    EVC,
    PT_SRUSDE,
    SRUSDE,
    STRATA_DEPOSITOR,
    WAD,
    AnvilFork,
    Chain,
    abi_encode,
    cd,
    cs,
    read_cv_config,
    read_cv_state,
    sel,
)

PENDLE_ROUTER = base.PENDLE_ROUTER
PENDLE_MARKET = base.PENDLE_MARKET
FACTORY = cs("0xa1517cCe0bE75700A8838EA1cEE0dc383cd3A332")
PERMIT2 = cs("0x000000000022D473030F116dDEE9F6B43aC78BA3")
ART = os.path.join(
    _HERE, "..", "..", "twyne-contracts", "out", "StrataPendleLoopOperator.sol", "StrataPendleLoopOperator.json"
)
_APPROX_T = "(uint256,uint256,uint256,uint256,uint256)"
_STEP_T = f"(uint256,uint256,{_APPROX_T})"
_PARAMS_T = f"(address,uint256,address,uint256,{_STEP_T}[])"
_DEPLOYER = cs("0x000000000000000000000000000000000000D314")
_USDE_WHALE = cs("0x9D39A5DE30e57443BfF2A8307A4256c8797A3497")


def deploy_operator(ch: Chain, deployer: str) -> str:
    art = json.load(open(ART))
    args = abi_encode(["address"] * 7, [EVC, STRATA_DEPOSITOR, SRUSDE, PENDLE_ROUTER, FACTORY, PERMIT2, base.USDe])
    data = art["bytecode"]["object"] + args.hex()
    ch.rpc("anvil_impersonateAccount", [cs(deployer)])
    ch.rpc("anvil_setBalance", [cs(deployer), hex(10**20)])
    h = ch.rpc("eth_sendTransaction", [{"from": cs(deployer), "data": data, "gas": hex(9_000_000)}])
    rc = ch.wait_receipt(h)
    ch.rpc("anvil_stopImpersonatingAccount", [cs(deployer)])
    if int(rc["status"], 16) != 1:
        raise click.ClickException("operator deployment failed")
    return cs(rc["contractAddress"])


def _fund_usde(ch: Chain, who: str, amount: int) -> None:
    have = ch.erc20_balance(base.USDe, who)
    if have < amount:
        ch.send_impersonated(
            _USDE_WHALE, base.USDe, cd("transfer(address,uint256)", ["address", "uint256"], [cs(who), amount - have])
        )


def _steps(iters: list[dict]) -> list:
    out = []
    for it in iters:
        guess_max = max(int(it["pt_out"]) * 2, int(it["borrow"]) * 2, 10**18)
        out.append((it["borrow"], it["min_pt"], (0, guess_max, 0, 256, 10**14)))
    return out


def _exec_calldata(params) -> str:
    return "0x" + (sel(f"executeLoop({_PARAMS_T})") + abi_encode([_PARAMS_T], [params])).hex()


def _set_op(borrower: str, op: str, on: bool) -> str:
    return cd("setAccountOperator(address,address,bool)", ["address", "address", "bool"], [cs(borrower), cs(op), on])


def use_or_deploy_operator(ch: Chain, operator: str | None) -> str:
    if operator:
        op = cs(operator)
        if len(ch.rpc("eth_getCode", [op, "latest"])) <= 2:
            raise click.ClickException(f"no contract deployed at --operator {op}")
        return op
    return deploy_operator(ch, _DEPLOYER)


def compute_iters(ch: Chain, cfg, *, seed: int, loops: int, target_hf: float, slippage: float) -> list[dict]:
    """Per-loop borrow schedule for the target HF, via the raw-flow discovery (operator-independent)."""
    b = cfg.borrower
    ch.rpc("anvil_setBalance", [b, hex(10**20)])
    bal = ch.erc20_balance(cfg.usde, b)
    if bal < seed:
        raise click.ClickException(f"borrower holds {bal / WAD:.6f} USDe < seed {seed / WAD:.6f} — fund it first")
    base.ensure_fork_approvals(ch, cfg, seed)
    ch.send_impersonated(b, cfg.usde, base._approve_cd(STRATA_DEPOSITOR, base.BIG_APPROVE))
    T = ch.latest_ts + 1
    return loopn.discover_loops(
        ch,
        cfg,
        start=seed,
        loops=loops,
        T=T,
        slippage=slippage,
        targets=dict(target_hf=target_hf, target_ltv=None, borrow_explicit=None),
    )


def build_batch(cfg, op: str, *, seed: int, iters: list[dict], deadline: int) -> str:
    params = (cfg.cv, seed, PENDLE_MARKET, deadline, _steps(iters))
    b = cfg.borrower
    items = [
        (EVC, "0x" + "00" * 20, 0, _set_op(b, op, True)),
        (op, b, 0, _exec_calldata(params)),
        (EVC, "0x" + "00" * 20, 0, _set_op(b, op, False)),
    ]
    return base._encode_batch(items)


def print_plan(cfg, op: str, *, seed: int, iters: list[dict], target_hf: float):
    click.echo("\n── Loop-leverage plan (no flashloan) " + "─" * 28)
    click.echo(f"  CV        {cfg.cv}")
    click.echo(f"  borrower  {cfg.borrower}")
    click.echo(f"  operator  {op}")
    click.echo(f"  seed      {seed / WAD:.6f} USDe   ·   {len(iters)} loop(s)   ·   target HF {target_hf}")
    dep = seed
    for i, it in enumerate(iters):
        tail = "→ borrower" if i == len(iters) - 1 else "→ recycle"
        click.echo(f"  loop {i + 1}: deposit {dep / WAD:.6f} USDe → borrow {it['borrow'] / WAD:.6f} USDe {tail}")
        dep = it["borrow"]


def _leak_report(ch: Chain, cfg, op: str):
    dust = {
        "op.USDe": ch.erc20_balance(cfg.usde, op),
        "op.srUSDe": ch.erc20_balance(SRUSDE, op),
        "op.PT": ch.erc20_balance(PT_SRUSDE, op),
        "evc.USDe": ch.erc20_balance(cfg.usde, EVC),
        "evc.srUSDe": ch.erc20_balance(SRUSDE, EVC),
        "evc.PT": ch.erc20_balance(PT_SRUSDE, EVC),
    }
    leaked = any(v > 0 for v in dust.values())
    click.echo("  residuals: " + "  ".join(f"{k}={v}" for k, v in dust.items()))
    click.echo("  " + ("✗ LEAK" if leaked else "✓ zero-leak (operator + EVC both clean)"))


def run_on_fork(ch: Chain, cfg, *, seed, loops, target_hf, slippage, deadline, operator):
    """Preview: run the batch on a fork (impersonating the borrower) with the deployed/fresh operator."""
    b = cfg.borrower
    op = use_or_deploy_operator(ch, operator)
    iters = compute_iters(ch, cfg, seed=seed, loops=loops, target_hf=target_hf, slippage=slippage)
    batch = build_batch(cfg, op, seed=seed, iters=iters, deadline=deadline)
    ch.send_impersonated(b, cfg.usde, base._approve_cd(op, base.BIG_APPROVE))  # seed-pull approval (fork)
    print_plan(cfg, op, seed=seed, iters=iters, target_hf=target_hf)

    before = read_cv_state(ch.rpc_url, base.HSV, cfg.cv, "latest")
    ch.rpc("anvil_setBalance", [b, hex(10**20)])
    try:
        ch.call(EVC, batch, frm=b)
    except Exception as e:
        click.echo(f"\n  ✗ batch would revert: {repr(e)[:240]}")
        raise click.ClickException("batch reverted in simulation")
    rc = ch.send_impersonated(b, EVC, batch)
    after = read_cv_state(ch.rpc_url, base.HSV, cfg.cv, "latest")
    click.echo("\n── Result (fork preview) " + "─" * 40)
    click.echo(f"  batch status: {'OK' if int(rc['status'], 16) else 'FAILED'}  gas={int(rc['gasUsed'], 16)}")
    base.print_position("before", before)
    base.print_position("after", after)
    _leak_report(ch, cfg, op)


def run_live(ch: Chain, cfg, *, seed, loops, target_hf, slippage, operator, fork_url, yes):
    """Broadcast the loop to LIVE mainnet, signing with $PRIVATE_KEY (== borrower)."""
    op = use_or_deploy_operator(ch, operator)  # must already be deployed on the target chain
    signer = base._resolve_signer(cfg.borrower)
    bal = ch.erc20_balance(cfg.usde, cfg.borrower)
    if bal < seed:
        raise click.ClickException(f"borrower holds {bal / WAD:.6f} USDe < seed {seed / WAD:.6f}")

    # size the per-loop borrow schedule on an ephemeral fork of the live chain
    with AnvilFork(fork_url, block=None) as fork:
        fch = Chain(fork.rpc_url)
        fcfg = read_cv_config(fch, cfg.cv)
        iters = compute_iters(fch, fcfg, seed=seed, loops=loops, target_hf=target_hf, slippage=slippage)
    deadline = ch.latest_ts + 1800
    batch = build_batch(cfg, op, seed=seed, iters=iters, deadline=deadline)
    print_plan(cfg, op, seed=seed, iters=iters, target_hf=target_hf)

    # 1) seed-pull approval (only if insufficient), confirmed on-chain before the batch
    if ch.erc20_allowance(cfg.usde, cfg.borrower, op) < seed:
        click.echo("\n  approving USDe -> operator (seed pull)…")
        base._broadcast_signed(
            ch, signer, cfg.usde, base._approve_cd(op, seed), label="USDe->operator", require_success=True
        )
        base._confirm_allowances(ch, cfg.borrower, [(cfg.usde, op, seed)])

    # 2) pre-check the batch against live state before spending gas
    before = read_cv_state(ch.rpc_url, base.HSV, cfg.cv, "latest")
    try:
        ch.call(EVC, batch, frm=cfg.borrower)
    except Exception as e:
        raise click.ClickException(f"batch pre-check reverted (nothing broadcast): {repr(e)[:240]}")
    if not yes and not click.confirm("\nSubmit the EVC loop batch to LIVE mainnet?", default=False):
        click.echo("Aborted.")
        return

    rc = base._broadcast_signed(ch, signer, EVC, batch, label="EVC loop batch")
    status = int(rc["status"], 16)
    click.echo(
        f"\n  batch tx: {rc['transactionHash']}  status={'OK' if status else 'FAILED'}  gas={int(rc['gasUsed'], 16)}"
    )
    after = read_cv_state(ch.rpc_url, base.HSV, cfg.cv, "latest")
    base.print_position("before", before)
    base.print_position("after", after)


@click.command(context_settings=dict(help_option_names=["-h", "--help"]))
@click.option("--cv", required=True, help="PT-srUSDe Collateral Vault (you must be its borrower)")
@click.option("--seed", type=float, default=None, help="Seed USDe (default: borrower's full USDe balance)")
@click.option("--loops", type=int, required=True, help="Number of recycle loops")
@click.option("--target-hf", type=float, required=True, help="Per-loop borrow sized to this internal HF")
@click.option("--slippage", type=float, default=0.5, help="Pendle minPtOut slippage tolerance, percent (default 0.5)")
@click.option(
    "--operator", default=None, help="Deployed operator address (required for --execute; else deployed on the fork)"
)
@click.option(
    "--execute", "do_execute", is_flag=True, help="Broadcast to LIVE mainnet (signs with $PRIVATE_KEY == borrower)"
)
@click.option("--yes", is_flag=True, help="Skip the live confirmation prompt")
@click.option("--rpc", default=base.DEFAULT_RPC, help="Mainnet RPC (broadcast for --execute; fork source otherwise)")
@click.option("--fork-url", default=None, help="Upstream RPC to fork (default: --rpc)")
@click.option("--fork-block", type=int, default=None, help="Pin the preview fork block (default: latest)")
def main(cv, seed, loops, target_hf, slippage, operator, do_execute, yes, rpc, fork_url, fork_block):
    """Drive the no-flashloan StrataPendleLoopOperator: --dry-run preview on a fork, or --execute live."""
    if loops < 1:
        raise click.ClickException("--loops must be >= 1")

    if do_execute:
        if not operator:
            raise click.ClickException("--execute requires --operator <deployed address>")
        ch = Chain(rpc)
        cfg = read_cv_config(ch, cv)
        seed_wei = int(seed * WAD) if seed is not None else ch.erc20_balance(cfg.usde, cfg.borrower)
        if seed_wei <= 0:
            raise click.ClickException("seed is 0; pass --seed")
        run_live(
            ch,
            cfg,
            seed=seed_wei,
            loops=loops,
            target_hf=target_hf,
            slippage=slippage / 100.0,
            operator=operator,
            fork_url=fork_url or rpc,
            yes=yes,
        )
        return

    with AnvilFork(fork_url or rpc, block=fork_block) as fork:
        ch = Chain(fork.rpc_url)
        cfg = read_cv_config(ch, cv)
        seed_wei = int(seed * WAD) if seed is not None else ch.erc20_balance(cfg.usde, cfg.borrower)
        if seed_wei <= 0:
            raise click.ClickException("seed is 0; pass --seed")
        run_on_fork(
            ch,
            cfg,
            seed=seed_wei,
            loops=loops,
            target_hf=target_hf,
            slippage=slippage / 100.0,
            deadline=ch.latest_ts + 3600,
            operator=operator,
        )


if __name__ == "__main__":
    main()
