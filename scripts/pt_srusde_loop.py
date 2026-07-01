#!/usr/bin/env python3
"""
pt_srusde_loop.py — Complete a full PT-srUSDe ⇆ USDe loop on Twyne in ONE EVC batch.

The loop, mirroring four production transactions but bundled atomically:

    1. USDe        -> srUSDe      (Strata TrancheDepositor.deposit)
    2. srUSDe      -> PT-srUSDe   (Pendle Router V4.swapExactTokenForPt)
    3. PT-srUSDe   -> collateral  (CollateralVault.depositUnderlying, wraps to Aave waToken)
    4. borrow USDe                (CollateralVault.borrow)

All four are submitted as items of a single `EVC.batch(...)`. Because the EVC is
`msg.sender` for the non-EVC-aware legs (Strata, Pendle), it transiently custodies the
intermediate USDe -> srUSDe; Pendle delivers PT back to the borrower's EOA so the CV can
pull it (CV ops pull collateral from `borrower`). Every downstream amount is derived from
the first USDe deposit by a timestamp-pinned *discovery* pass on an anvil fork, so the
static batch calldata is exact and no value leaks (srUSDe is yield-bearing — pinning
block.timestamp freezes its exchange rate, making the discovery byte-identical to execution).

Modes
-----
  --dry-run   (default)  Fork the chain, run the real batch on the fork, print the
                          before/after position and a zero-leak report. Nothing is broadcast.
  --execute              Run approvals + broadcast the real EVC.batch. Against a controllable
                          anvil fork (the test target) it impersonates the borrower and pins
                          the timestamp for exactness. Against a live chain it signs with
                          $PRIVATE_KEY (must equal the CV's borrower) and uses the Pendle
                          floor (minPtOut) to absorb block-time drift.

Borrow sizing
-------------
  --target-hf X | --target-ltv P   size the borrow to a target internal HF or LTV (percent)
  --borrow A                        borrow an explicit USDe amount
  (none)                            deposit-only (no borrow)

Addresses come from tech-notes / the on-chain CV; protocol math uses twyne_calculator.py (§2.1).
"""

from __future__ import annotations

import os
import sys
import time
from dataclasses import dataclass

import click
import httpx

# --- twyne-cli reuse -------------------------------------------------------------------
_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, "..", "src"))
from eth_abi import encode as abi_encode  # noqa: E402
from eth_utils import function_signature_to_4byte_selector as _sel4  # noqa: E402
from eth_utils import to_checksum_address as cs  # noqa: E402

from twyne_cli.simulate import AnvilFork  # noqa: E402
from twyne_cli.vault_state import read_cv_state  # noqa: E402

# --- protocol math (CLAUDE.md §2.1 — never hand-roll) ----------------------------------
sys.path.insert(0, os.path.join(_HERE, "..", "..", "internal-twyne-docs", "FAQ-Knowledge-Base", "tools"))
try:
    from twyne_calculator import max_borrow as _max_borrow  # noqa: E402
except Exception:  # pragma: no cover - keep runnable if repo layout differs

    def _max_borrow(C: float, liq_ltv: float) -> float:  # B_max = liq_ltv * C
        return liq_ltv * C


# --- addresses (source: repos/tech-notes/public-launch-addresses/TwyneAddresses_current_1.json
#     + PT-srUSDe-22OCT2026 listing notes; all verified on-chain) -------------------------
EVC = cs("0xef39D6493884C4C84D38a4bFF879Ce16CEdE702a")
HSV = cs("0x5A919b9A77ee391AB48208A93e0684c24F99B07a")  # HealthStatViewer (current)
USDe = cs("0x4c9EDD5852cd905f086C759E8383e09bff1E68B3")
SRUSDE = cs("0x3d7d6fdf07EE548B939A80edbc9B2256d0cdc003")  # Strata srUSDe metavault
STRATA_DEPOSITOR = cs("0x7a6AD3753b51cB0161405e93645B5E1f95b6e50F")  # TrancheDepositor (proxy)
PENDLE_ROUTER = cs("0x888888888889758F76e7103c6CbF23ABbF58F946")  # Pendle Router V4
PENDLE_MARKET = cs("0x66Ec657C59cdcaf171aB43B83da3942758bF8a97")  # PT-srUSDe-22OCT2026 market
PT_SRUSDE = cs("0x59bC9FaE5D62B19d4f8d07D758047aCb9EE19d34")  # the loop's target PT

ZERO_ADDR = "0x" + "00" * 20
MAX_UINT = 2**256 - 1
BIG_APPROVE = 2**255
WAD = 10**18
DEFAULT_RPC = os.environ.get("RPC_URL_1") or "https://ethereum-rpc.publicnode.com"
PENDLE_API = "https://api-v2.pendle.finance/core/v2/sdk/1/convert"
# Live can't pin block.timestamp; srUSDe is yield-bearing so it mints marginally fewer shares
# at inclusion than during discovery, and Pendle pulls an EXACT input. We shave the Pendle input
# by this many bps so the swap can never come up short (leaves negligible srUSDe dust in the EVC).
LIVE_SRUSDE_BUFFER_BPS = 1.0

# Strata TrancheDepositor.deposit(vault, asset, amount, receiver, (swapDeadline, swapAmountOutMin, swapTokenOut, minShares))
_STRATA_PARAMS_T = "(uint256,uint256,address,uint256)"
_STRATA_DEPOSIT_SIG = f"deposit(address,address,uint256,address,{_STRATA_PARAMS_T})"


# =======================================================================================
# Low-level helpers
# =======================================================================================
def sel(sig: str) -> bytes:
    return _sel4(sig)


def cd(sig: str, types: list[str], args: list) -> str:
    """ABI-encode a function call -> 0x-hex calldata."""
    return "0x" + (sel(sig) + abi_encode(types, args)).hex()


def _approve_cd(spender: str, amount: int) -> str:
    return cd("approve(address,uint256)", ["address", "uint256"], [cs(spender), amount])


class Chain:
    """Thin JSON-RPC wrapper over an anvil fork (spawned) or a live/attached endpoint."""

    def __init__(self, rpc_url: str):
        self.rpc_url = rpc_url
        self._client = httpx.Client(timeout=120)
        self._controllable: bool | None = None

    def rpc(self, method: str, params: list | None = None):
        r = self._client.post(self.rpc_url, json={"jsonrpc": "2.0", "method": method, "params": params or [], "id": 1})
        data = r.json()
        if "error" in data:
            raise RuntimeError(f"RPC {method}: {data['error']}")
        return data["result"]

    # --- reads ---
    def call(self, to: str, data: str, block: str = "latest", frm: str | None = None) -> str:
        tx = {"to": cs(to), "data": data}
        if frm is not None:
            tx["from"] = cs(frm)
        return self.rpc("eth_call", [tx, block])

    def addr_ret(self, to: str, sig: str) -> str:
        return cs("0x" + self.call(to, "0x" + sel(sig).hex())[-40:])

    def uint_ret(self, to: str, sig: str) -> int:
        return int(self.call(to, "0x" + sel(sig).hex()), 16)

    def erc20_balance(self, token: str, who: str) -> int:
        return int(self.call(token, cd("balanceOf(address)", ["address"], [cs(who)])), 16)

    def erc20_allowance(self, token: str, owner: str, spender: str) -> int:
        return int(
            self.call(token, cd("allowance(address,address)", ["address", "address"], [cs(owner), cs(spender)])), 16
        )

    @property
    def latest_ts(self) -> int:
        return int(self.rpc("eth_getBlockByNumber", ["latest", False])["timestamp"], 16)

    @property
    def controllable(self) -> bool:
        """True if this endpoint supports anvil cheatcodes (snapshot / pin timestamp / impersonate)."""
        if self._controllable is None:
            try:
                snap = self.rpc("evm_snapshot")
                self.rpc("evm_revert", [snap])
                self._controllable = True
            except Exception:
                self._controllable = False
        return self._controllable

    # --- writes ---
    def set_next_ts(self, t: int) -> None:
        self.rpc("evm_setNextBlockTimestamp", [t])

    def wait_receipt(self, tx_hash: str, timeout_s: int = 180) -> dict:
        deadline = time.time() + timeout_s
        while time.time() < deadline:
            rc = self.rpc("eth_getTransactionReceipt", [tx_hash])
            if rc:
                return rc
            time.sleep(0.2)
        raise RuntimeError(f"no receipt for {tx_hash}")

    def send_impersonated(self, frm: str, to: str, data: str, *, ts: int | None = None, gas: int = 12_000_000) -> dict:
        if ts is not None:
            self.set_next_ts(ts)
        self.rpc("anvil_impersonateAccount", [cs(frm)])
        self.rpc("anvil_setBalance", [cs(frm), hex(10**20)])
        h = self.rpc(
            "eth_sendTransaction", [{"from": cs(frm), "to": cs(to), "data": data, "value": "0x0", "gas": hex(gas)}]
        )
        rc = self.wait_receipt(h)
        self.rpc("anvil_stopImpersonatingAccount", [cs(frm)])
        return rc


# =======================================================================================
# CV config
# =======================================================================================
@dataclass
class CVConfig:
    cv: str
    borrower: str
    wrapper: str  # CV.asset()
    pt: str  # wrapper.asset() — the underlying PT pulled by depositUnderlying
    usde: str  # CV.targetAsset() — the borrowed/debt asset
    iv: str  # intermediateVault()


def read_cv_config(ch: Chain, cv: str) -> CVConfig:
    cv = cs(cv)
    wrapper = ch.addr_ret(cv, "asset()")
    cfg = CVConfig(
        cv=cv,
        borrower=ch.addr_ret(cv, "borrower()"),
        wrapper=wrapper,
        pt=ch.addr_ret(wrapper, "asset()"),
        usde=ch.addr_ret(cv, "targetAsset()"),
        iv=ch.addr_ret(cv, "intermediateVault()"),
    )
    if cfg.pt.lower() != PT_SRUSDE.lower():
        raise click.ClickException(
            f"CV {cv} collateral underlying is {cfg.pt}, not PT-srUSDe ({PT_SRUSDE}). "
            "This script implements the PT-srUSDe <> USDe loop only."
        )
    if cfg.usde.lower() != USDe.lower():
        raise click.ClickException(f"CV debt asset is {cfg.usde}, expected USDe ({USDe}).")
    return cfg


# =======================================================================================
# Plan + discovery
# =======================================================================================
@dataclass
class Plan:
    start_usde: int
    srusde_out: int
    pt_out: int
    min_pt_out: int
    borrow_amt: int
    pendle_to: str
    pendle_data: str
    target_desc: str


def _strata_deposit_cd(cfg: CVConfig, amount: int, receiver: str) -> str:
    return cd(
        _STRATA_DEPOSIT_SIG,
        ["address", "address", "uint256", "address", _STRATA_PARAMS_T],
        [SRUSDE, cfg.usde, amount, cs(receiver), (0, 0, ZERO_ADDR, 0)],
    )


def _pendle_quote(amount_in: int, receiver: str, slippage: float) -> tuple[str, str, int, int]:
    """Return (router, calldata, pt_out, min_pt_out) for an exact srUSDe -> PT swap."""
    q = httpx.get(
        PENDLE_API,
        params=dict(
            receiver=cs(receiver),
            slippage=str(slippage),
            tokensIn=SRUSDE,
            amountsIn=str(amount_in),
            tokensOut=PT_SRUSDE,
            enableAggregator="true",
        ),
        timeout=90,
    ).json()
    route = q["routes"][0]
    router = cs(route["tx"]["to"])
    if router != PENDLE_ROUTER:
        raise click.ClickException(f"Pendle returned unexpected router {router}")
    pt_out = int(route["outputs"][0]["amount"])
    return router, route["tx"]["data"], pt_out, int(pt_out * (1 - slippage))


def ensure_fork_approvals(ch: Chain, cfg: CVConfig, start: int) -> None:
    """Force (impersonated, un-pinned) the borrower approvals discovery + the batch rely on.

    Must run BEFORE the execution timestamp is chosen, since un-pinned blocks advance the clock.
    """
    b = cfg.borrower
    for token, spender, amt in (
        (cfg.usde, STRATA_DEPOSITOR, start),  # Strata pulls USDe (discovery + EVC item 3 approve)
        (cfg.usde, EVC, start),  # batch item 1 transferFrom
        (SRUSDE, PENDLE_ROUTER, BIG_APPROVE),  # Pendle pulls srUSDe
        (cfg.pt, cfg.cv, BIG_APPROVE),  # CV.depositUnderlying pulls PT
    ):
        if ch.erc20_allowance(token, b, spender) < amt:
            ch.send_impersonated(b, token, _approve_cd(spender, amt))


def discover_plan(
    ch: Chain, cfg: CVConfig, *, start: int, T: int, slippage: float, target_hf, target_ltv, borrow_explicit
) -> Plan:
    """Pinned-to-T discovery inside a snapshot (reverted before returning).

    Approvals must already be in place (see ensure_fork_approvals). Pins every value-moving
    tx to the single timestamp T so the discovered srUSDe/PT amounts equal the batch's.
    """
    assert ch.controllable
    b = cfg.borrower
    snap = ch.rpc("evm_snapshot")
    try:
        sr0 = ch.erc20_balance(SRUSDE, b)
        ch.send_impersonated(b, STRATA_DEPOSITOR, _strata_deposit_cd(cfg, start, b), ts=T)
        srusde_out = ch.erc20_balance(SRUSDE, b) - sr0
        if srusde_out <= 0:
            raise click.ClickException("Strata deposit produced 0 srUSDe — check amount/state")

        pendle_to, pendle_data, pt_out_q, min_pt_out = _pendle_quote(srusde_out, b, slippage)
        pt0 = ch.erc20_balance(PT_SRUSDE, b)
        ch.send_impersonated(b, pendle_to, pendle_data, ts=T)
        pt_out = ch.erc20_balance(PT_SRUSDE, b) - pt0
        if pt_out <= 0:
            raise click.ClickException("Pendle swap produced 0 PT")

        dep_batch = _encode_batch([(cfg.cv, b, 0, cd("depositUnderlying(uint256)", ["uint256"], [pt_out]))])
        ch.send_impersonated(b, EVC, dep_batch, ts=T)
        post = read_cv_state(ch.rpc_url, HSV, cfg.cv, "latest")
        borrow_amt = _size_borrow(
            ch, cfg, post, target_hf=target_hf, target_ltv=target_ltv, borrow_explicit=borrow_explicit
        )
    finally:
        ch.rpc("evm_revert", [snap])

    tdesc = (
        f"target HF {target_hf}"
        if target_hf is not None
        else f"target LTV {target_ltv:.2%}"
        if target_ltv is not None
        else f"explicit {borrow_explicit / WAD:.6f} USDe"
        if borrow_explicit is not None
        else "deposit-only"
    )
    return Plan(start, srusde_out, pt_out, min_pt_out, borrow_amt, pendle_to, pendle_data, tdesc)


def _size_borrow(ch: Chain, cfg: CVConfig, st, *, target_hf, target_ltv, borrow_explicit) -> int:
    if borrow_explicit is not None:
        borrow_amt = borrow_explicit
    elif target_hf is None and target_ltv is None:
        return 0
    else:
        C, B = st.collateral_usd, st.borrow_usd
        liq = st.twyne_liq_ltv_pct / 100.0
        if target_hf is not None:
            b_target = _max_borrow(C, liq) / target_hf  # B_max = liq*C ; HF = liq*C/B
        else:
            b_target = target_ltv * C
        add_usd = max(0.0, b_target - B)
        usde_price = (st.borrow_usd / st.borrow_native) if st.borrow_native > 1e-9 else 1.0
        borrow_amt = int((add_usd / usde_price) * WAD)
    try:
        iv_cash = ch.uint_ret(cfg.iv, "cash()")
        if borrow_amt > iv_cash:
            click.echo(f"  ! borrow {borrow_amt / WAD:.6f} exceeds IV cash {iv_cash / WAD:.6f}; clamping", err=True)
            borrow_amt = iv_cash
    except Exception:
        pass
    return max(0, borrow_amt)


# =======================================================================================
# Batch assembly
# =======================================================================================
def _encode_batch(items: list[tuple[str, str, int, str]]) -> str:
    tuples = [(cs(t), cs(o), v, bytes.fromhex(d[2:])) for (t, o, v, d) in items]
    return (
        "0x"
        + (
            sel("batch((address,address,uint256,bytes)[])")
            + abi_encode(["(address,address,uint256,bytes)[]"], [tuples])
        ).hex()
    )


def build_loop_items(cfg: CVConfig, plan: Plan, *, deposit_amt: int) -> list[tuple[str, str, int, str]]:
    b = cfg.borrower
    items = [
        (
            cfg.usde,
            b,
            0,
            cd("transferFrom(address,address,uint256)", ["address", "address", "uint256"], [b, EVC, plan.start_usde]),
        ),
        (cfg.usde, b, 0, _approve_cd(STRATA_DEPOSITOR, plan.start_usde)),
        (STRATA_DEPOSITOR, b, 0, _strata_deposit_cd(cfg, plan.start_usde, EVC)),
        (SRUSDE, b, 0, _approve_cd(plan.pendle_to, plan.srusde_out)),
        (plan.pendle_to, b, 0, plan.pendle_data),
        (cfg.cv, b, 0, cd("depositUnderlying(uint256)", ["uint256"], [deposit_amt])),
    ]
    if plan.borrow_amt > 0:
        items.append((cfg.cv, b, 0, cd("borrow(uint256,address)", ["uint256", "address"], [plan.borrow_amt, b])))
    return items


def needed_approvals(ch: Chain, cfg: CVConfig, plan: Plan, *, deposit_amt: int, max_approve: bool) -> list[dict]:
    """Pre-batch ERC20 approvals the borrower must really hold: USDe->EVC and PT->CV."""
    out = []
    for token, spender, amount, label in (
        (cfg.usde, EVC, plan.start_usde, "USDe -> EVC (batch transferFrom)"),
        (cfg.pt, cfg.cv, deposit_amt, "PT-srUSDe -> CV (depositUnderlying)"),
    ):
        if ch.erc20_allowance(token, cfg.borrower, spender) < amount:
            out.append(dict(token=token, spender=spender, amount=(MAX_UINT if max_approve else amount), label=label))
    return out


# =======================================================================================
# Reporting
# =======================================================================================
def _fmt_hf(v: float) -> str:
    return "∞" if v == float("inf") else f"{v:.4f}"


def print_plan(cfg: CVConfig, plan: Plan, *, deposit_amt: int):
    click.echo("\n── Loop plan " + "─" * 52)
    click.echo(f"  CV            {cfg.cv}")
    click.echo(f"  borrower      {cfg.borrower}")
    click.echo(f"  1. deposit    {plan.start_usde / WAD:.6f} USDe")
    click.echo(f"  2. Strata  →  {plan.srusde_out / WAD:.9f} srUSDe")
    click.echo(f"  3. Pendle  →  {plan.pt_out / WAD:.9f} PT-srUSDe   (min {plan.min_pt_out / WAD:.9f})")
    click.echo(f"  4. deposit →  {deposit_amt / WAD:.9f} PT into CV")
    if plan.borrow_amt > 0:
        click.echo(f"  5. borrow  →  {plan.borrow_amt / WAD:.6f} USDe   ({plan.target_desc})")
    else:
        click.echo(f"  5. borrow  →  (none — {plan.target_desc})")


def print_position(tag: str, st):
    click.echo(
        f"  {tag:<7} collateral={st.collateral_native:.6f} {st.collateral_symbol}  "
        f"debt={st.borrow_native:.6f} {st.debt_symbol}  "
        f"LTV_t={st.twyne_ltv_pct:.2f}%  HF_t={_fmt_hf(st.in_hf)}"
    )


def leak_report(ch: Chain, cfg: CVConfig):
    dust = {
        "USDe": ch.erc20_balance(cfg.usde, EVC) / WAD,
        "srUSDe": ch.erc20_balance(SRUSDE, EVC) / WAD,
        "PT": ch.erc20_balance(PT_SRUSDE, EVC) / WAD,
    }
    leaked = any(v > 1e-12 for v in dust.values())
    click.echo(
        f"  EVC residual: USDe={dust['USDe']:.12f} srUSDe={dust['srUSDe']:.12f} PT={dust['PT']:.12f}"
        + ("  ✗ LEAK" if leaked else "  ✓ zero-leak")
    )


# =======================================================================================
# Fork engine (dry-run and impersonated execute share this exact-amount path)
# =======================================================================================
def run_on_fork(ch: Chain, cfg: CVConfig, *, start: int, slippage: float, targets: dict, max_approve: bool):
    b = cfg.borrower
    ch.rpc("anvil_setBalance", [b, hex(10**20)])
    before = read_cv_state(ch.rpc_url, HSV, cfg.cv, "latest")

    # Show what real approvals the user would need, then force them on the fork.
    real_apps = needed_approvals(
        ch, cfg, Plan(start, 0, 0, 0, 0, PENDLE_ROUTER, "0x", ""), deposit_amt=start, max_approve=max_approve
    )
    ensure_fork_approvals(ch, cfg, start)

    T = ch.latest_ts + 1  # single timestamp for discovery AND execution
    plan = discover_plan(ch, cfg, start=start, T=T, slippage=slippage, **targets)
    deposit_amt = plan.pt_out  # pinned fork is deterministic -> exact, zero leftover

    print_plan(cfg, plan, deposit_amt=deposit_amt)
    if real_apps:
        click.echo("\n  Approvals the borrower must hold (auto-set on this fork):")
        for a in real_apps:
            click.echo(f"    - {a['label']}")

    batch = _encode_batch(build_loop_items(cfg, plan, deposit_amt=deposit_amt))
    usde0 = ch.erc20_balance(cfg.usde, b)
    ch.set_next_ts(T)
    try:
        ch.call(EVC, batch, frm=b)  # revert pre-check (msg.sender must be the borrower)
    except Exception as e:
        click.echo(f"\n  ✗ batch would revert: {repr(e)[:240]}")
        raise click.ClickException("batch reverted in simulation")
    rc = ch.send_impersonated(b, EVC, batch, ts=T)
    after = read_cv_state(ch.rpc_url, HSV, cfg.cv, "latest")

    click.echo("\n── Result (fork) " + "─" * 48)
    click.echo(f"  batch status: {'OK' if int(rc['status'], 16) else 'FAILED'}  gas={int(rc['gasUsed'], 16)}")
    print_position("before", before)
    print_position("after", after)
    usde_delta = (ch.erc20_balance(cfg.usde, b) - usde0) / WAD
    click.echo(
        f"  borrower USDe net: {usde_delta:+.6f}  (borrow {plan.borrow_amt / WAD:.6f} − deposit {plan.start_usde / WAD:.6f})"
    )
    leak_report(ch, cfg)


# =======================================================================================
# Live execute (non-controllable target: discover on a fork, broadcast signed txs)
# =======================================================================================
def run_live(
    ch: Chain, cfg: CVConfig, *, start: int, slippage: float, targets: dict, fork_url: str, max_approve: bool, yes: bool
):
    signer = _resolve_signer(cfg.borrower)
    with AnvilFork(fork_url, block=None) as fork:
        fch = Chain(fork.rpc_url)
        fcfg = read_cv_config(fch, cfg.cv)
        fch.rpc("anvil_setBalance", [fcfg.borrower, hex(10**20)])
        ensure_fork_approvals(fch, fcfg, start)
        T = fch.latest_ts + 1
        plan = discover_plan(fch, fcfg, start=start, T=T, slippage=slippage, **targets)

    # Re-quote Pendle with a shaved srUSDe input (see LIVE_SRUSDE_BUFFER_BPS) so the swap can't
    # come up short against the slightly-smaller srUSDe that mints at inclusion. Deposit the
    # Pendle floor (min_pt) and scale the borrow to the actually-deposited collateral.
    shave = max(int(plan.srusde_out * LIVE_SRUSDE_BUFFER_BPS / 10_000), 1)
    srusde_in = plan.srusde_out - shave
    pendle_to, pendle_data, pt_q, min_pt = _pendle_quote(srusde_in, cfg.borrower, slippage)
    deposit_amt = min_pt
    live_borrow = (plan.borrow_amt * deposit_amt // plan.pt_out) if plan.pt_out else plan.borrow_amt
    live_plan = Plan(plan.start_usde, srusde_in, pt_q, min_pt, live_borrow, pendle_to, pendle_data, plan.target_desc)
    print_plan(cfg, live_plan, deposit_amt=deposit_amt)
    click.echo(f"  live drift buffer: {LIVE_SRUSDE_BUFFER_BPS:g} bps (~{shave / WAD:.9f} srUSDe left as EVC dust)")

    apps = needed_approvals(ch, cfg, live_plan, deposit_amt=deposit_amt, max_approve=max_approve)
    if apps:
        click.echo("\n  Approvals required:")
        for a in apps:
            click.echo(f"    - {a['label']}")
    if not yes and not click.confirm("\nSubmit approvals + EVC batch to the live chain?", default=False):
        click.echo("Aborted.")
        return

    before = read_cv_state(ch.rpc_url, HSV, cfg.cv, "latest")
    for a in apps:
        _broadcast_signed(
            ch, signer, a["token"], _approve_cd(a["spender"], a["amount"]), label=a["label"], require_success=True
        )
    _confirm_allowances(ch, cfg.borrower, [(cfg.usde, EVC, live_plan.start_usde), (cfg.pt, cfg.cv, deposit_amt)])

    batch = _encode_batch(build_loop_items(cfg, live_plan, deposit_amt=deposit_amt))
    try:
        ch.call(EVC, batch, frm=cfg.borrower)  # decoded pre-check before spending gas
    except Exception as e:
        raise click.ClickException(f"batch pre-check reverted (nothing broadcast): {repr(e)[:240]}")
    rc = _broadcast_signed(ch, signer, EVC, batch, label="EVC batch")
    status = int(rc["status"], 16)
    click.echo(
        f"\n  batch tx: {rc['transactionHash']}  status={'OK' if status else 'FAILED'}  gas={int(rc['gasUsed'], 16)}"
    )
    after = read_cv_state(ch.rpc_url, HSV, cfg.cv, "latest")
    print_position("before", before)
    print_position("after", after)


def _resolve_signer(borrower: str):
    pk = os.environ.get("PRIVATE_KEY")
    if not pk:
        raise click.ClickException("live --execute requires $PRIVATE_KEY (or use --impersonate against an anvil fork)")
    from eth_account import Account

    acct = Account.from_key(pk if pk.startswith("0x") else "0x" + pk)
    if acct.address.lower() != borrower.lower():
        raise click.ClickException(f"$PRIVATE_KEY address {acct.address} != CV borrower {borrower}")
    return acct


def _broadcast_signed(
    ch: Chain, signer, to: str, data: str, *, label: str = "tx", require_success: bool = False
) -> dict:
    frm = signer.address
    nonce = int(ch.rpc("eth_getTransactionCount", [cs(frm), "pending"]), 16)
    chain_id = int(ch.rpc("eth_chainId"), 16)
    try:
        gas = int(ch.rpc("eth_estimateGas", [{"from": cs(frm), "to": cs(to), "data": data}]), 16)
    except Exception as e:
        raise click.ClickException(f"{label}: gas estimation reverted (nothing broadcast) — {repr(e)[:240]}")
    base = int(ch.rpc("eth_gasPrice"), 16)
    tx = {
        "to": cs(to),
        "data": data,
        "value": 0,
        "nonce": nonce,
        "gas": int(gas * 1.3),
        "maxFeePerGas": base * 2,
        "maxPriorityFeePerGas": min(base, 2_000_000_000),
        "chainId": chain_id,
    }
    signed = signer.sign_transaction(tx)
    h = ch.rpc("eth_sendRawTransaction", ["0x" + signed.raw_transaction.hex()])
    click.echo(f"    {label}: {h}")
    rc = ch.wait_receipt(h)
    if require_success and int(rc["status"], 16) != 1:
        raise click.ClickException(f"{label} reverted on-chain: {h}")
    return rc


def _confirm_allowances(ch: Chain, owner: str, needs: list[tuple[str, str, int]], *, timeout_s: int = 120) -> None:
    """Poll until each (token, spender) allowance reflects >= amount.

    Public/load-balanced RPCs are eventually consistent: a just-mined approval may not be visible
    to the node that serves the batch's gas estimate, which then reverts with 'insufficient
    allowance'. Block here until every approval is observable before broadcasting the batch.
    """
    pending = list(needs)
    deadline = time.time() + timeout_s
    while pending and time.time() < deadline:
        pending = [(t, s, a) for (t, s, a) in pending if ch.erc20_allowance(t, owner, s) < a]
        if pending:
            time.sleep(2)
    if pending:
        miss = ", ".join(f"{t}->{s}" for t, s, _ in pending)
        raise click.ClickException(f"approvals not visible on the RPC after {timeout_s}s (lag?): {miss}")


# =======================================================================================
# Orchestration
# =======================================================================================
def run(*, cv, amount, slippage, targets, do_execute, impersonate, rpc, fork_url, fork_block, max_approve, yes):
    if not do_execute:
        # DRY-RUN: spawn an ephemeral fork and run the real batch on it.
        with AnvilFork(fork_url, block=fork_block) as fork:
            ch = Chain(fork.rpc_url)
            cfg = read_cv_config(ch, cv)
            start = _resolve_start(ch, cfg, amount)
            run_on_fork(ch, cfg, start=start, slippage=slippage, targets=targets, max_approve=max_approve)
        return

    ch = Chain(rpc)
    cfg = read_cv_config(ch, cv)
    start = _resolve_start(ch, cfg, amount)
    if impersonate or ch.controllable:
        if not ch.controllable:
            raise click.ClickException("--impersonate requires the --rpc endpoint to be a controllable anvil fork")
        run_on_fork(ch, cfg, start=start, slippage=slippage, targets=targets, max_approve=max_approve)
    else:
        run_live(
            ch,
            cfg,
            start=start,
            slippage=slippage,
            targets=targets,
            fork_url=fork_url,
            max_approve=max_approve,
            yes=yes,
        )


def _resolve_start(ch: Chain, cfg: CVConfig, amount) -> int:
    bal = ch.erc20_balance(cfg.usde, cfg.borrower)
    if amount is not None:
        start = int(amount * WAD)
        if start > bal:
            raise click.ClickException(
                f"--amount {amount} USDe exceeds borrower {cfg.borrower} balance {bal / WAD:.6f} USDe"
            )
        return start
    if bal == 0:
        raise click.ClickException(f"borrower {cfg.borrower} holds 0 USDe; pass --amount or fund it")
    return bal


# =======================================================================================
# CLI
# =======================================================================================
@click.command(context_settings=dict(help_option_names=["-h", "--help"]))
@click.option("--cv", required=True, help="Collateral Vault address (a PT-srUSDe market CV you own)")
@click.option("--amount", type=float, default=None, help="Starting USDe amount (default: borrower's full USDe balance)")
@click.option("--target-hf", type=float, default=None, help="Size the borrow to this internal health factor")
@click.option("--target-ltv", type=float, default=None, help="Size the borrow to this internal LTV (percent, e.g. 90)")
@click.option("--borrow", "borrow_explicit", type=float, default=None, help="Borrow an explicit USDe amount instead")
@click.option("--slippage", type=float, default=0.5, help="Pendle swap slippage tolerance, percent (default 0.5)")
@click.option("--execute", "do_execute", is_flag=True, help="Broadcast for real (default is --dry-run)")
@click.option("--dry-run", "dry_run", is_flag=True, help="Simulate on a fork and report (default)")
@click.option("--rpc", default=DEFAULT_RPC, help="RPC endpoint for --execute (live chain or local anvil)")
@click.option("--fork-url", default=None, help="Upstream RPC to fork for --dry-run / live discovery (default: --rpc)")
@click.option("--fork-block", type=int, default=None, help="Pin the dry-run fork to a block (default: latest)")
@click.option("--impersonate", is_flag=True, help="Execute via anvil impersonation (fork testing; no key needed)")
@click.option("--max-approve", is_flag=True, help="Approve max uint256 instead of the exact amount")
@click.option("--yes", is_flag=True, help="Skip the live-execute confirmation prompt")
def main(
    cv,
    amount,
    target_hf,
    target_ltv,
    borrow_explicit,
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
    """Complete a PT-srUSDe <> USDe loop on Twyne in a single EVC batch."""
    if do_execute and dry_run:
        raise click.ClickException("--dry-run and --execute are mutually exclusive")
    if sum(x is not None for x in (target_hf, target_ltv, borrow_explicit)) > 1:
        raise click.ClickException("choose at most one of --target-hf / --target-ltv / --borrow")
    targets = dict(
        target_hf=target_hf,
        target_ltv=(target_ltv / 100.0) if target_ltv is not None else None,
        borrow_explicit=int(borrow_explicit * WAD) if borrow_explicit is not None else None,
    )
    run(
        cv=cv,
        amount=amount,
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
