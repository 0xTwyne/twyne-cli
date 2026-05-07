"""Chain registry — single source of truth for supported chains.

Each ChainSpec carries chain metadata plus capability flags so commands can
adapt at runtime without per-call switches. The active chain is selected at
CLI parse time and stashed via set_active_chain(); all downstream code (address
loader, contract factories, cache, swap) reads it through active_chain().
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ChainSpec:
    """Static metadata + capabilities for a supported chain."""

    chain_id: int
    slug: str
    name: str
    default_rpc: str
    env_var: str
    supports_euler: bool
    supports_operators: bool
    ape_ecosystem: str
    ape_network: str
    start_block: int

    @property
    def addresses_file(self) -> str:
        return f"{self.slug}.json"


CHAINS: dict[int, ChainSpec] = {
    1: ChainSpec(
        chain_id=1,
        slug="mainnet",
        name="Ethereum",
        default_rpc="",  # falls back to Ape's default provider (MEV Blocker)
        env_var="RPC_URL_1",
        supports_euler=True,
        supports_operators=True,
        ape_ecosystem="ethereum",
        ape_network="mainnet",
        start_block=23_233_276,
    ),
    4326: ChainSpec(
        chain_id=4326,
        slug="megaeth",
        name="MegaETH",
        default_rpc="https://mainnet.megaeth.com/rpc",
        env_var="RPC_URL_4326",
        supports_euler=False,
        supports_operators=False,
        ape_ecosystem="ethereum",
        ape_network="megaeth",
        start_block=14_706_831,
    ),
}


_BY_SLUG: dict[str, ChainSpec] = {c.slug: c for c in CHAINS.values()}


def all_chains() -> list[ChainSpec]:
    """Return all registered chains in deterministic order (by chain_id)."""
    return [CHAINS[k] for k in sorted(CHAINS)]


def supported_slugs() -> list[str]:
    return [c.slug for c in all_chains()]


def resolve_chain(value: str | int | None) -> ChainSpec:
    """Resolve a chain by slug ('mainnet'), chain id ('1' / 1), or None (→ default)."""
    from .exceptions import ChainNotSupportedError

    if value is None or value == "":
        return CHAINS[1]
    if isinstance(value, int):
        if value not in CHAINS:
            raise ChainNotSupportedError(value)
        return CHAINS[value]
    s = str(value).strip().lower()
    if s in _BY_SLUG:
        return _BY_SLUG[s]
    if s.isdigit():
        cid = int(s)
        if cid in CHAINS:
            return CHAINS[cid]
        raise ChainNotSupportedError(cid)
    raise ChainNotSupportedError(value)


_ACTIVE: ChainSpec = CHAINS[1]


def set_active_chain(chain: ChainSpec) -> None:
    """Set the active chain for the current invocation. Called once by the CLI entrypoint."""
    global _ACTIVE
    _ACTIVE = chain


def active_chain() -> ChainSpec:
    return _ACTIVE
