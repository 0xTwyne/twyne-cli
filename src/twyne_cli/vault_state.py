"""Shared collateral-vault state reader.

`read_cv_state()` is the single source of truth for a CV's full position —
collateral / reserved credit / debt (native + USD), the four LTVs, both health
factors, plus identity fields (borrower, tokens, protocol, liquidatable flags).
It reads the on-chain ``HealthStatViewer.positionStats(cv)`` (the protocol's own
computation, so no protocol math is reimplemented — CLAUDE.md §2.1) over web3.

Consumed by `vault info`, `vault health`, and `vault simulate` so all three
report identical state.
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass
from pathlib import Path

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


# ---------------------------------------------------------------------------
# ABI loading
# ---------------------------------------------------------------------------
def _load_abi(name: str) -> list:
    return json.loads((_ABI_DIR / f"{name}.json").read_text())


_ERC20_META_ABI = [
    {"name": "symbol", "type": "function", "stateMutability": "view", "inputs": [], "outputs": [{"type": "string"}]},
    {"name": "decimals", "type": "function", "stateMutability": "view", "inputs": [], "outputs": [{"type": "uint8"}]},
]


# ---------------------------------------------------------------------------
# RPC resolution (env → .env → public fallback). Never logs the value.
# ---------------------------------------------------------------------------
def _dotenv_lookup(key: str) -> str | None:
    """Find ``key`` in the nearest .env walking up from this package.

    The value is returned for use as an RPC URL but never printed — it may embed
    an API key.
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


def resolve_rpc(chain_id: int) -> str:
    """Resolve an RPC URL for a chain. Priority: env → .env → public fallback."""
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
    raise RuntimeError(f"No RPC URL for chain {chain_id}. Set RPC_URL_{chain_id} or pass --rpc.")


def resolve_read_rpc(ctx) -> str:
    """RPC for read-only commands: the CLI-resolved URL, else a per-chain fallback."""
    if getattr(ctx, "rpc_url", None):
        return ctx.rpc_url
    return resolve_rpc(ctx.chain.chain_id)


def resolve_hsv(chain_id: int, override: str | None = None) -> str:
    if override:
        return override
    addr = DEFAULT_HSV.get(chain_id)
    if addr is None:
        raise RuntimeError(f"No HealthStatViewer known for chain {chain_id}; pass --hsv")
    return addr


# ---------------------------------------------------------------------------
# web3 helpers
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


def ext_or_inf(hf_raw: int) -> float:
    """Scale a 1e18 health factor; clamp the zero-debt sentinel to infinity."""
    val = hf_raw / USD
    return float("inf") if val >= HF_INFINITE else val


# ---------------------------------------------------------------------------
# CV state snapshot
# ---------------------------------------------------------------------------
@dataclass
class CVState:
    # Numeric position (diffable)
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
    # Identity / status (not diffed)
    borrower: str = ""
    collateral_token: str = ""
    debt_token: str = ""
    protocol: str = ""  # "Aave V3" | "Euler"
    can_liquidate: bool = False
    can_rebalance: bool = False

    @property
    def liquidatable(self) -> bool:
        return self.in_hf < 1.0

    @classmethod
    def from_raw(cls, raw, *, coll_dec, coll_sym, debt_dec, debt_sym, **identity) -> "CVState":
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
            **identity,
        )

    def to_dict(self) -> dict:
        d = asdict(self)
        d["liquidatable"] = self.liquidatable
        # JSON cannot encode inf; surface the sentinel explicitly.
        for k in ("in_hf", "ext_hf"):
            if d[k] == float("inf"):
                d[k] = None
        return d


def _detect_protocol(cvc, block) -> str:
    try:
        atoken = cvc.functions.aToken().call(block_identifier=block)
        if atoken and int(atoken, 16) != 0:
            return "Aave V3"
    except Exception:  # noqa: BLE001
        pass
    return "Euler"


def _try_bool(fn, block) -> bool:
    try:
        return bool(fn.call(block_identifier=block))
    except Exception:  # noqa: BLE001
        return False


def read_cv_state(rpc_url: str, hsv_addr: str, cv_addr: str, block: str | int = "latest") -> CVState:
    """Read the full CV position from HealthStatViewer.positionStats + CV getters."""
    w3 = _w3(rpc_url)
    cv = w3.to_checksum_address(cv_addr)
    hsv = w3.eth.contract(address=w3.to_checksum_address(hsv_addr), abi=_load_abi("HealthStatViewer"))
    raw = hsv.functions.positionStats(cv).call(block_identifier=block)

    cvc = w3.eth.contract(address=cv, abi=_load_abi("CollateralVault"))
    coll_token = cvc.functions.asset().call(block_identifier=block)
    debt_token = cvc.functions.targetAsset().call(block_identifier=block)
    borrower = cvc.functions.borrower().call(block_identifier=block)
    can_liq = _try_bool(cvc.functions.canLiquidate(), block)
    can_rebal = _try_bool(cvc.functions.canRebalance(), block)
    protocol = _detect_protocol(cvc, block)

    coll_sym, coll_dec = _erc20_meta(w3, coll_token)
    debt_sym, debt_dec = _erc20_meta(w3, debt_token)
    return CVState.from_raw(
        raw,
        coll_dec=coll_dec,
        coll_sym=coll_sym,
        debt_dec=debt_dec,
        debt_sym=debt_sym,
        borrower=borrower,
        collateral_token=coll_token,
        debt_token=debt_token,
        protocol=protocol,
        can_liquidate=can_liq,
        can_rebalance=can_rebal,
    )
