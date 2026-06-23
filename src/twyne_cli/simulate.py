"""Twyne collateral-vault transaction simulator.

Given a candidate transaction (calldata + to/from/value) and a collateral vault
(CV) address, fork the chain with anvil, snapshot the CV's full state *before*,
execute the calldata on the throwaway fork, snapshot the state *after*, and
report a before/after diff including USD values, LTVs, and health factors so the
operator can judge how safe the transaction is before broadcasting it.

State is read from the on-chain ``HealthStatViewer.positionStats(cv)`` — the
protocol's own computation — so no protocol math is re-implemented here
(CLAUDE.md §2.1). The tool mutates only a local anvil fork, never a live chain.
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

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
MAXFACTOR = 10_000  # bps precision for LTVs (1e4 = 100%)
USD = 10**18  # *Usd fields and health factors are 1e18 scaled
HF_INFINITE = 10**24  # positionStats returns ~uint256.max for zero-debt; clamp display

_ABI_DIR = Path(__file__).parent / "abis"

# HealthStatViewer (v1.0.5+) — source of truth: tech-notes
# public-launch-addresses/TwyneAddresses_current_1.json
DEFAULT_HSV = {
    1: "0x5A919b9A77ee391AB48208A93e0684c24F99B07a",
}

# Foundry may install under ~/.config/.foundry or ~/.foundry depending on env.
_FOUNDRY_BIN_CANDIDATES = (
    Path.home() / ".config" / ".foundry" / "bin",
    Path.home() / ".foundry" / "bin",
)


# ---------------------------------------------------------------------------
# ABI loading (bundled with the package)
# ---------------------------------------------------------------------------
def _load_abi(name: str) -> list:
    return json.loads((_ABI_DIR / f"{name}.json").read_text())


# Minimal fragments for the reads we issue. Full ABIs are bundled but we only
# need a handful of getters; inline fragments keep decoding self-contained.
_ERC20_META_ABI = [
    {"name": "symbol", "type": "function", "stateMutability": "view", "inputs": [], "outputs": [{"type": "string"}]},
    {"name": "decimals", "type": "function", "stateMutability": "view", "inputs": [], "outputs": [{"type": "uint8"}]},
]


# ---------------------------------------------------------------------------
# RPC URL resolution (env → .env → public fallback). Never logs the value.
# ---------------------------------------------------------------------------
def _dotenv_lookup(key: str) -> str | None:
    """Find ``key`` in the nearest .env walking up from this package.

    Mirrors the integration-test loader. The value is returned to the caller for
    use as a fork URL but is never printed — it may embed an API key.
    """
    here = Path(__file__).resolve()
    for parent in here.parents:
        env_file = parent / ".env"
        if env_file.is_file():
            for raw in env_file.read_text().splitlines():
                line = raw.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, _, v = line.partition("=")
                if k.strip() == key:
                    return v.strip().strip('"').strip("'")
            return None
    return None


def resolve_fork_url(chain_id: int) -> str:
    """Resolve an upstream RPC to fork. Priority: env → .env → public fallback."""
    for key in (f"RPC_URL_{chain_id}", "RPC_URL" if chain_id == 1 else ""):
        if key and os.environ.get(key):
            return os.environ[key]
    val = _dotenv_lookup(f"RPC_URL_{chain_id}")
    if not val and chain_id == 1:
        val = _dotenv_lookup("RPC_URL")
    if val:
        return val
    if chain_id == 1:
        return "https://ethereum-rpc.publicnode.com"
    raise RuntimeError(f"No RPC URL for chain {chain_id}. Set RPC_URL_{chain_id} or pass --fork-url.")


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
# eth_call / eth_abi helpers via web3
# ---------------------------------------------------------------------------
def _w3(rpc_url: str):
    from web3 import Web3

    return Web3(Web3.HTTPProvider(rpc_url, request_kwargs={"timeout": 120}))


def _erc20_meta(w3, token: str) -> tuple[str, int]:
    """Return (symbol, decimals); resilient to non-standard tokens."""
    c = w3.eth.contract(address=w3.to_checksum_address(token), abi=_ERC20_META_ABI)
    try:
        decimals = c.functions.decimals().call()
    except Exception:  # noqa: BLE001
        decimals = 18
    try:
        symbol = c.functions.symbol().call()
    except Exception:  # noqa: BLE001
        symbol = token[:8]
    return symbol, int(decimals)


# ---------------------------------------------------------------------------
# CV state snapshot
# ---------------------------------------------------------------------------
@dataclass
class CVState:
    collateral_native: float
    collateral_usd: float
    reserved_native: float
    reserved_usd: float
    borrow_native: float
    borrow_usd: float
    twyne_ltv_pct: float  # LTV_t = B/C (unbounded)
    twyne_liq_ltv_pct: float  # ~LTV_t (capped param)
    external_ltv_pct: float  # LTV_e = B/(C+C_LP)
    external_liq_ltv_pct: float  # ~LTV_e
    max_twyne_ltv_pct: float  # ~LTV_t^max (protocol cap)
    in_hf: float
    ext_hf: float
    collateral_symbol: str
    debt_symbol: str

    @property
    def liquidatable(self) -> bool:
        return self.in_hf < 1.0

    @classmethod
    def from_raw(cls, raw, *, coll_dec, coll_sym, debt_dec, debt_sym) -> "CVState":
        (c_nat, c_usd, r_nat, r_usd, b_nat, b_usd, liq_t, ltv_t, liq_e, ltv_e, ext_hf, in_hf, max_liq_t) = raw
        cdiv, ddiv = 10**coll_dec, 10**debt_dec
        return cls(
            collateral_native=c_nat / cdiv,
            collateral_usd=c_usd / USD,
            reserved_native=r_nat / cdiv,
            reserved_usd=r_usd / USD,
            borrow_native=b_nat / ddiv,
            borrow_usd=b_usd / USD,
            twyne_ltv_pct=ltv_t / MAXFACTOR * 100,
            twyne_liq_ltv_pct=liq_t / MAXFACTOR * 100,
            external_ltv_pct=ltv_e / MAXFACTOR * 100,
            external_liq_ltv_pct=liq_e / MAXFACTOR * 100,
            max_twyne_ltv_pct=max_liq_t / MAXFACTOR * 100,
            in_hf=ext_or_inf(in_hf),
            ext_hf=ext_or_inf(ext_hf),
            collateral_symbol=coll_sym,
            debt_symbol=debt_sym,
        )


def ext_or_inf(hf_raw: int) -> float:
    """Scale a 1e18 health factor; clamp the zero-debt sentinel to infinity."""
    val = hf_raw / USD
    return float("inf") if val >= HF_INFINITE else val


def read_cv_state(rpc_url: str, hsv_addr: str, cv_addr: str, block: str | int = "latest") -> CVState:
    w3 = _w3(rpc_url)
    cv = w3.to_checksum_address(cv_addr)
    hsv = w3.eth.contract(address=w3.to_checksum_address(hsv_addr), abi=_load_abi("HealthStatViewer"))
    raw = hsv.functions.positionStats(cv).call(block_identifier=block)

    cvc = w3.eth.contract(address=cv, abi=_load_abi("CollateralVault"))
    coll_token = cvc.functions.asset().call(block_identifier=block)
    debt_token = cvc.functions.targetAsset().call(block_identifier=block)
    coll_sym, coll_dec = _erc20_meta(w3, coll_token)
    debt_sym, debt_dec = _erc20_meta(w3, debt_token)
    return CVState.from_raw(raw, coll_dec=coll_dec, coll_sym=coll_sym, debt_dec=debt_dec, debt_sym=debt_sym)


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
        # web3 may raise ValueError with embedded revert data
        raw = getattr(e, "args", [None])[0]
        data = raw.get("data") if isinstance(raw, dict) else None
        return ExecResult(True, decode_revert(data if isinstance(data, str) else None, str(e)), None, None)

    # 2) Apply on the fork: impersonate sender, fund gas, send, mine.
    fork.rpc("anvil_impersonateAccount", [sender])
    fork.rpc("anvil_setBalance", [sender, hex(10**20)])
    tx_hash = fork.rpc(
        "eth_sendTransaction",
        [
            {
                "from": sender,
                "to": to,
                "data": tx["data"],
                "value": hex(value),
                "gas": hex(30_000_000),
            }
        ],
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
    if hsv_address is None:
        hsv_address = DEFAULT_HSV.get(chain_id)
        if hsv_address is None:
            raise RuntimeError(f"No HealthStatViewer known for chain {chain_id}; pass --hsv")
    if attach_rpc is None and fork_url is None:
        fork_url = resolve_fork_url(chain_id)

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
    lines.append(f"  chain {result['chain_id']} · fork block {result['fork_block']:,} · collateral {cs} · debt {ds}")
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
            d = a - b
            ds_ = f"{d:+,.2f}"
        else:
            bs, as_ = f"{b:,.4f} {unit}", f"{a:,.4f} {unit}"
            d = a - b
            ds_ = f"{d:+,.4f}"
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

    # Safety verdict
    lines.append("")
    verdict = _verdict(before, after, ex)
    lines.append(f"  Verdict: {verdict}")
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
