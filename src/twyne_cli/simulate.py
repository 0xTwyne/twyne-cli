"""Twyne collateral-vault transaction simulator.

Given a candidate transaction (calldata + to/from/value) and a collateral vault
(CV) address, fork the chain with anvil, snapshot the CV's full state *before*,
execute the calldata on the throwaway fork, snapshot the state *after*, and
report a before/after diff including USD values, LTVs, and health factors so the
operator can judge how safe the transaction is before broadcasting it.

State is read via the shared :func:`twyne_cli.vault_state.read_cv_state` (which
reads the on-chain ``HealthStatViewer.positionStats``), so no protocol math is
reimplemented here (CLAUDE.md §2.1). The tool mutates only a local anvil fork.
"""

from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import time
from dataclasses import asdict, dataclass
from pathlib import Path

import httpx
from eth_utils import function_signature_to_4byte_selector

# Re-exported so `simulate.CVState` etc. keep working for existing callers/tests.
from .vault_state import (  # noqa: F401
    DEFAULT_HSV,
    HF_INFINITE,
    MAXFACTOR,
    USD,
    CVState,
    _load_abi,
    ext_or_inf,
    read_cv_state,
    resolve_hsv,
    resolve_rpc,
)

# Back-compat alias: the simulator historically called this resolve_fork_url.
resolve_fork_url = resolve_rpc

# Foundry may install under ~/.config/.foundry or ~/.foundry depending on env.
_FOUNDRY_BIN_CANDIDATES = (
    Path.home() / ".config" / ".foundry" / "bin",
    Path.home() / ".foundry" / "bin",
)


# ---------------------------------------------------------------------------
# Process / fork helpers
# ---------------------------------------------------------------------------
def _redact(text: str, *secrets: str) -> str:
    """Strip secret substrings (e.g. fork URL with API key) from error text."""
    for s in secrets:
        if s:
            text = text.replace(s, "<fork-url>")
    return text


def _anvil_path() -> str:
    found = shutil.which("anvil")
    if found:
        return found
    for d in _FOUNDRY_BIN_CANDIDATES:
        cand = d / "anvil"
        if cand.is_file():
            return str(cand)
    raise RuntimeError("anvil not found. Install foundry: curl -L https://foundry.paradigm.xyz | bash && foundryup")


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


# ---------------------------------------------------------------------------
# Anvil fork lifecycle
# ---------------------------------------------------------------------------
class AnvilFork:
    """Context manager that launches (or attaches to) an anvil mainnet fork."""

    def __init__(self, fork_url: str | None, *, block: int | None = None, attach_rpc: str | None = None):
        self._fork_url = fork_url
        self._block = block
        self._attach_rpc = attach_rpc
        self._proc: subprocess.Popen | None = None
        self._log_path: Path | None = None
        self.rpc_url: str = attach_rpc or ""

    def __enter__(self) -> "AnvilFork":
        if self._attach_rpc:
            self.rpc_url = self._attach_rpc
            self._wait_ready()
            return self
        port = _free_port()
        self.rpc_url = f"http://127.0.0.1:{port}"
        cmd = [_anvil_path(), "--fork-url", self._fork_url, "--port", str(port), "--silent"]
        if self._block is not None:
            cmd += ["--fork-block-number", str(self._block)]
        # anvil echoes the fork URL (with key) at startup; keep its output in a
        # file we never surface verbatim.
        self._log_path = Path(os.environ.get("TMPDIR", "/tmp")) / f"twyne-anvil-{port}.log"
        with open(self._log_path, "wb") as logf:
            self._proc = subprocess.Popen(cmd, stdout=logf, stderr=subprocess.STDOUT)
        try:
            self._wait_ready()
        except Exception:
            self.__exit__(None, None, None)
            raise
        return self

    def __exit__(self, *exc) -> None:
        if self._proc is not None:
            self._proc.terminate()
            try:
                self._proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self._proc.kill()
            self._proc = None

    def _wait_ready(self, timeout_s: int = 45) -> None:
        deadline = time.time() + timeout_s
        last_err = ""
        while time.time() < deadline:
            if self._proc is not None and self._proc.poll() is not None:
                tail = ""
                if self._log_path and self._log_path.is_file():
                    tail = _redact(self._log_path.read_text()[-400:], self._fork_url or "")
                raise RuntimeError(f"anvil exited during startup. log tail:\n{tail}")
            try:
                bn = self.rpc("eth_blockNumber")
                if bn:
                    return
            except Exception as e:  # noqa: BLE001
                last_err = _redact(str(e), self._fork_url or "")
            time.sleep(0.3)
        raise RuntimeError(f"anvil did not become ready in {timeout_s}s. last: {last_err}")

    def rpc(self, method: str, params: list | None = None):
        resp = httpx.post(
            self.rpc_url,
            json={"jsonrpc": "2.0", "method": method, "params": params or [], "id": 1},
            timeout=120,
        )
        data = resp.json()
        if "error" in data:
            raise RuntimeError(f"RPC {method} error: {data['error']}")
        return data.get("result")

    @property
    def block_number(self) -> int:
        return int(self.rpc("eth_blockNumber"), 16)


# ---------------------------------------------------------------------------
# Revert decoding
# ---------------------------------------------------------------------------
def _build_error_selectors() -> dict[str, str]:
    """Map 4-byte selector (hex) → error name, from bundled ABIs."""
    table = {"08c379a0": "Error(string)", "4e487b71": "Panic(uint256)"}
    for abi_name in ("EVC", "CollateralVault", "EVault", "HealthStatViewer"):
        try:
            abi = _load_abi(abi_name)
        except Exception:  # noqa: BLE001
            continue
        for e in abi:
            if e.get("type") != "error":
                continue
            sig = f"{e['name']}({','.join(i['type'] for i in e.get('inputs', []))})"
            sel = function_signature_to_4byte_selector(sig).hex()
            table.setdefault(sel, e["name"])
    return table


def decode_revert(data_hex: str | None, message: str | None) -> str:
    """Best-effort human-readable revert reason."""
    if data_hex:
        h = data_hex[2:] if data_hex.startswith("0x") else data_hex
        sel = h[:8].lower()
        if sel == "08c379a0":
            try:
                from eth_abi import decode

                reason = decode(["string"], bytes.fromhex(h[8:]))[0]
                return f'Error("{reason}")'
            except Exception:  # noqa: BLE001
                pass
        if sel == "4e487b71":
            try:
                code = int(h[8:72], 16)
                return f"Panic(0x{code:02x})"
            except Exception:  # noqa: BLE001
                pass
        name = _build_error_selectors().get(sel)
        if name:
            return f"{name} [0x{sel}]"
        return f"custom error 0x{sel} (data: 0x{h[:72]}…)"
    return message or "execution reverted (no reason)"


@dataclass
class ExecResult:
    reverted: bool
    revert_reason: str | None
    gas_used: int | None
    tx_hash: str | None


def execute_calldata(fork: AnvilFork, tx: dict) -> ExecResult:
    """Revert-check via eth_call, then (if ok) impersonate + send to mutate state."""
    from web3.exceptions import ContractLogicError

    from .vault_state import _w3

    w3 = _w3(fork.rpc_url)
    sender = w3.to_checksum_address(tx["from"])
    to = w3.to_checksum_address(tx["to"])
    value = tx.get("value", 0)
    if isinstance(value, str):
        value = int(value, 16) if value.startswith("0x") else int(value)
    call = {"from": sender, "to": to, "data": tx["data"], "value": value, "gas": 30_000_000}

    # 1) Revert detection + reason (no state change).
    try:
        w3.eth.call(call, block_identifier="latest")
    except ContractLogicError as e:
        data = getattr(e, "data", None)
        if isinstance(data, dict):  # some providers nest under {'data': '0x..'}
            data = data.get("data")
        return ExecResult(True, decode_revert(data if isinstance(data, str) else None, str(e)), None, None)
    except Exception as e:  # noqa: BLE001
        raw = getattr(e, "args", [None])[0]
        data = raw.get("data") if isinstance(raw, dict) else None
        return ExecResult(True, decode_revert(data if isinstance(data, str) else None, str(e)), None, None)

    # 2) Apply on the fork: impersonate sender, fund gas, send, mine.
    fork.rpc("anvil_impersonateAccount", [sender])
    fork.rpc("anvil_setBalance", [sender, hex(10**20)])
    tx_hash = fork.rpc(
        "eth_sendTransaction",
        [{"from": sender, "to": to, "data": tx["data"], "value": hex(value), "gas": hex(30_000_000)}],
    )
    receipt = _wait_receipt(fork, tx_hash)
    fork.rpc("anvil_stopImpersonatingAccount", [sender])
    status = int(receipt["status"], 16)
    gas_used = int(receipt["gasUsed"], 16)
    if status == 0:
        return ExecResult(True, "reverted on send (eth_call passed)", gas_used, tx_hash)
    return ExecResult(False, None, gas_used, tx_hash)


def _wait_receipt(fork: AnvilFork, tx_hash: str, timeout_s: int = 30) -> dict:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        r = fork.rpc("eth_getTransactionReceipt", [tx_hash])
        if r is not None:
            return r
        time.sleep(0.25)
    raise RuntimeError(f"no receipt for {tx_hash} in {timeout_s}s")


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------
def load_tx_file(path: str) -> dict:
    raw = json.loads(Path(path).read_text())
    missing = [k for k in ("to", "data") if k not in raw]
    if missing:
        raise ValueError(f"tx file missing keys: {missing}")
    return raw


def simulate(
    *,
    cv_address: str,
    tx: dict,
    hsv_address: str | None = None,
    fork_url: str | None = None,
    attach_rpc: str | None = None,
    block: int | None = None,
) -> dict:
    """Run the full before→execute→after pipeline. Returns a result dict."""
    chain_id = int(tx.get("chainId", 1))
    hsv_address = resolve_hsv(chain_id, hsv_address)
    if attach_rpc is None and fork_url is None:
        fork_url = resolve_rpc(chain_id)

    with AnvilFork(fork_url, block=block, attach_rpc=attach_rpc) as fork:
        fork_block = fork.block_number
        before = read_cv_state(fork.rpc_url, hsv_address, cv_address, "latest")
        exec_result = execute_calldata(fork, tx)
        after = read_cv_state(fork.rpc_url, hsv_address, cv_address, "latest")

    return {
        "cv": cv_address,
        "hsv": hsv_address,
        "chain_id": chain_id,
        "fork_block": fork_block,
        "tx": {"to": tx.get("to"), "from": tx.get("from"), "value": tx.get("value", 0)},
        "exec": asdict(exec_result),
        "before": asdict(before),
        "after": asdict(after),
        "_before_obj": before,
        "_after_obj": after,
        "_exec_obj": exec_result,
    }


# ---------------------------------------------------------------------------
# Human-readable report
# ---------------------------------------------------------------------------
def _hf(v: float) -> str:
    return "∞" if v == float("inf") else f"{v:.4f}"


def _hf_delta(b: float, a: float) -> str:
    if b == float("inf") or a == float("inf"):
        return "—" if b == a else ("↑" if a == float("inf") else "↓")
    d = a - b
    arrow = "↑" if d > 1e-9 else ("↓" if d < -1e-9 else "")
    return f"{d:+.4f} {arrow}".rstrip()


def render_report(result: dict) -> str:
    before: CVState = result["_before_obj"]
    after: CVState = result["_after_obj"]
    ex: ExecResult = result["_exec_obj"]
    cs, ds = before.collateral_symbol, before.debt_symbol

    lines: list[str] = []
    lines.append(f"Twyne CV Simulation — {result['cv']}")
    lines.append(
        f"  chain {result['chain_id']} · fork block {result['fork_block']:,} · "
        f"{before.protocol or '?'} · collateral {cs} · debt {ds}"
    )
    lines.append(f"  tx: from {result['tx']['from']} → to {result['tx']['to']}")
    lines.append("")

    if ex.reverted:
        lines.append(f"  ⛔ TX WOULD REVERT: {ex.revert_reason}")
        lines.append("  (state below is unchanged — the transaction does not apply)")
    else:
        gas = f"{ex.gas_used:,}" if ex.gas_used else "?"
        lines.append(f"  ✅ TX SUCCEEDS · gas {gas}")
    lines.append("")

    def num(b, a, unit, *, money=False):
        if money:
            bs, as_ = f"${b:,.2f}", f"${a:,.2f}"
            ds_ = f"{a - b:+,.2f}"
        else:
            bs, as_ = f"{b:,.4f} {unit}", f"{a:,.4f} {unit}"
            ds_ = f"{a - b:+,.4f}"
        return bs, as_, ds_

    def pct(b, a):
        return f"{b:.2f}%", f"{a:.2f}%", f"{a - b:+.2f}pp"

    rows: list[tuple[str, str, str, str]] = []
    rows.append(("C  (collateral)", *num(before.collateral_native, after.collateral_native, cs)))
    rows.append(("   collateral $", *num(before.collateral_usd, after.collateral_usd, "", money=True)))
    rows.append(("C_LP (reserved)", *num(before.reserved_native, after.reserved_native, cs)))
    rows.append(("   reserved $", *num(before.reserved_usd, after.reserved_usd, "", money=True)))
    rows.append(("B  (borrow)", *num(before.borrow_native, after.borrow_native, ds)))
    rows.append(("   borrow $", *num(before.borrow_usd, after.borrow_usd, "", money=True)))
    rows.append(("LTV_t  (B/C)", *pct(before.twyne_ltv_pct, after.twyne_ltv_pct)))
    rows.append(("~LTV_t (cap)", *pct(before.twyne_liq_ltv_pct, after.twyne_liq_ltv_pct)))
    rows.append(("LTV_e", *pct(before.external_ltv_pct, after.external_ltv_pct)))
    rows.append(("~LTV_e", *pct(before.external_liq_ltv_pct, after.external_liq_ltv_pct)))
    rows.append(("inHF (Twyne)", _hf(before.in_hf), _hf(after.in_hf), _hf_delta(before.in_hf, after.in_hf)))
    rows.append(("extHF (external)", _hf(before.ext_hf), _hf(after.ext_hf), _hf_delta(before.ext_hf, after.ext_hf)))
    rows.append(("liquidatable", "yes" if before.liquidatable else "no", "yes" if after.liquidatable else "no", ""))

    w0 = max(len(r[0]) for r in rows)
    w1 = max(len(r[1]) for r in rows + [("", "Before", "", "")])
    w2 = max(len(r[2]) for r in rows + [("", "", "After", "")])
    header = f"  {'Metric':<{w0}}  {'Before':>{w1}}  {'After':>{w2}}  Δ"
    lines.append(header)
    lines.append("  " + "-" * (len(header) - 2))
    for name, b, a, d in rows:
        lines.append(f"  {name:<{w0}}  {b:>{w1}}  {a:>{w2}}  {d}")

    lines.append("")
    lines.append(f"  Verdict: {_verdict(before, after, ex)}")
    return "\n".join(lines)


def _verdict(before: CVState, after: CVState, ex: ExecResult) -> str:
    if ex.reverted:
        return "transaction reverts — no state change; not executable as-is"
    if after.liquidatable and not before.liquidatable:
        return "⚠️  UNSAFE — position becomes liquidatable (inHF < 1) after this tx"
    if after.liquidatable:
        return "⚠️  still liquidatable after this tx"
    if after.in_hf > before.in_hf:
        return f"safer — inHF {_hf(before.in_hf)} → {_hf(after.in_hf)} (not liquidatable)"
    if after.in_hf < before.in_hf:
        return f"riskier but solvent — inHF {_hf(before.in_hf)} → {_hf(after.in_hf)}"
    return "no material change to health"
