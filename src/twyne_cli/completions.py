"""Shell completion functions for Twyne CLI.

Self-contained module with zero ape imports — all data loading uses pure JSON
so completions stay fast (no web3/ape initialization).
"""

import json
from importlib import resources
from pathlib import Path
from typing import Any

from click.shell_completion import CompletionItem

# Cache dir matches cache.py but we load JSON directly (no ape dependency)
_CACHE_DIR = Path.home() / ".config" / "twyne" / "cache"

_registry_cache: dict | None = None


def _load_registry() -> dict:
    """Load bundled mainnet.json address registry (cached)."""
    global _registry_cache
    if _registry_cache is None:
        ref = resources.files("twyne_cli") / "addresses" / "mainnet.json"
        _registry_cache = json.loads(ref.read_text())
    return _registry_cache


def _load_vault_cache_addresses() -> list[str]:
    """Load collateral vault addresses from local cache file.

    Returns empty list if cache doesn't exist or is malformed.
    """
    cache_file = _CACHE_DIR / "chain1_vaults.json"
    if not cache_file.exists():
        return []
    try:
        data = json.loads(cache_file.read_text())
        return [v["address"] for v in data.get("vaults", []) if "address" in v]
    except (json.JSONDecodeError, OSError, KeyError):
        return []


def complete_vault_address(ctx: Any, param: Any, incomplete: str) -> list[CompletionItem]:
    """Complete collateral vault addresses from local cache."""
    addresses = _load_vault_cache_addresses()
    incomplete_lower = incomplete.lower()
    return [
        CompletionItem(addr)
        for addr in addresses
        if addr.lower().startswith(incomplete_lower)
    ]


def complete_iv_address(ctx: Any, param: Any, incomplete: str) -> list[CompletionItem]:
    """Complete intermediate vault addresses from bundled registry.

    Matches by address prefix OR human-readable name (case-insensitive).
    Shows name as help text in zsh/fish.
    """
    registry = _load_registry()
    ivs = registry.get("intermediateVaults", {})
    incomplete_lower = incomplete.lower()
    results = []
    for name, addr in ivs.items():
        if addr.lower().startswith(incomplete_lower) or name.lower().startswith(incomplete_lower):
            results.append(CompletionItem(addr, help=name))
    return results


def complete_target_vault(ctx: Any, param: Any, incomplete: str) -> list[CompletionItem]:
    """Complete target vault addresses from bundled registry.

    Matches by address prefix OR human-readable name (case-insensitive).
    Shows name as help text in zsh/fish.
    """
    registry = _load_registry()
    tvs = registry.get("targetVaults", {})
    incomplete_lower = incomplete.lower()
    results = []
    for name, addr in tvs.items():
        if addr.lower().startswith(incomplete_lower) or name.lower().startswith(incomplete_lower):
            results.append(CompletionItem(addr, help=name))
    return results


def complete_asset_or_iv(ctx: Any, param: Any, incomplete: str) -> list[CompletionItem]:
    """Complete for protocol rates command — delegates to IV completion."""
    return complete_iv_address(ctx, param, incomplete)
