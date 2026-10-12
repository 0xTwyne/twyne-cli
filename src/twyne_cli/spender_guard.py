"""Central guard: never grant token authority to an EVC (incident 95524).

An EVC executes caller-chosen calls (`call`, `batch`). An ERC-20 / Permit2
allowance or an operator grant to an EVC therefore lets anyone make the EVC
spend it. Every approval / permit / operator builder in the CLI calls
`assert_safe_spender()` first; it fails closed.
"""

import json
import os
import re
from functools import lru_cache
from importlib import resources

import click

# Every EVC in tech-notes plus the Euler EVC (lowercase). Chain-agnostic on purpose:
# a ban, not a per-chain allowlist.
KNOWN_EVC_ADDRESSES: frozenset[str] = frozenset({
    "0xef39d6493884c4c84d38a4bff879ce16cede702a",  # Twyne EVC (Ethereum, Arbitrum, Robinhood)
    "0xff06f28cf0c44cf1e8f03e6835bb2f3a2a752c5c",  # Twyne EVC (MegaETH)
    "0xa6eb5d620cac284242b03bf498375922882a7876",  # Twyne EVC (whitelist launch)
    "0x0c9a3dd6b8f28529d72d7f9ce918d493519ee383",  # Euler EVC (Ethereum)
    "0x00f3be2c13fb10129e91dff8ef667e503c7a961e",  # Twyne EVC (Base, whitelist Apr 2025)
    "0x05cd7a2eb03c361f0fe18374dc381e5918ff9d74",  # Twyne EVC (Base, private beta Jan-Mar)
    "0xb9ba1745afd92fd77209d7c6961753f2f32cf6f9",  # Twyne EVC (Base, private beta Mar19)
    "0xc36aed7b7816aa21b660a33a637a8f9b9b70ad6c",  # Twyne EVC (Base test, Jun15 / Nov06)
})

_ADDRESS_RE = re.compile(r"^0x[0-9a-fA-F]{40}$")


class UnsafeSpenderError(click.ClickException):
    """Raised when code tries to approve, permit or authorize an EVC."""


def _registry_evcs(node) -> set[str]:
    """Collect every value under a key containing 'evc' in an address registry."""
    found: set[str] = set()
    if isinstance(node, dict):
        for key, value in node.items():
            if "evc" in key.lower() and isinstance(value, str):
                found.add(value.lower())
            else:
                found |= _registry_evcs(value)
    return found


@lru_cache(maxsize=1)
def _bundled_registry_evcs() -> frozenset[str]:
    found: set[str] = set()
    for ref in (resources.files("twyne_cli") / "addresses").iterdir():
        if ref.name.endswith(".json"):
            found |= _registry_evcs(json.loads(ref.read_text()))
    return frozenset(found)


def evc_addresses() -> frozenset[str]:
    """All banned spenders: known EVCs, every registry EVC, and TWYNE_*EVC env overrides."""
    env = {
        v.lower() for k, v in os.environ.items()
        if k.startswith("TWYNE_") and "EVC" in k.upper() and v
    }
    return KNOWN_EVC_ADDRESSES | _bundled_registry_evcs() | frozenset(env)


def assert_safe_spender(spender, what: str = "spender") -> str:
    """Raise UnsafeSpenderError if `spender` is an EVC or not a valid address.

    Returns the spender as a string so callers can use it inline.
    """
    addr = str(getattr(spender, "address", spender) or "")
    if not _ADDRESS_RE.match(addr):
        raise UnsafeSpenderError(f"Refusing token authority: invalid {what} {addr!r}.")
    if addr.lower() in evc_addresses():
        raise UnsafeSpenderError(
            f"Refusing to approve, permit or authorize {what} {addr}: it is an EVC. "
            "Anyone can make an EVC call the token and spend the allowance (incident 95524). "
            "Pull user tokens only through a contract that authenticates the user."
        )
    return addr
