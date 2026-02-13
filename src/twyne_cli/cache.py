"""Incremental event cache for vault discovery.

Caches immutable data (vault addresses, creation blocks, asset addresses) from
T_CollateralVaultCreated factory events. Only scans new blocks since last checkpoint.
"""

import json
import os
import tempfile
from pathlib import Path

import click
from ape import Contract

from .contracts import _load_abi, get_address

CACHE_DIR = Path.home() / ".config" / "twyne" / "cache"

# First block with Twyne-related events on mainnet — no factory events exist before this.
MAINNET_START_BLOCK = 23_233_276


class VaultEntry:
    """A single cached vault."""

    __slots__ = ("address", "block", "asset")

    def __init__(self, address: str, block: int, asset: str):
        self.address = address
        self.block = block
        self.asset = asset

    def to_dict(self) -> dict:
        return {"address": self.address, "block": self.block, "asset": self.asset}

    @classmethod
    def from_dict(cls, d: dict) -> "VaultEntry":
        return cls(address=d["address"], block=d["block"], asset=d["asset"])


class VaultCache:
    """Incremental cache of collateral vault creation events.

    Stores immutable data: vault address, creation block, asset address.
    Scans only from last_scanned_block + 1 on subsequent calls.
    """

    def __init__(self, chain_id: int = 1):
        self.chain_id = chain_id
        self.factory_addr = get_address("collateralVaultFactory")
        self.last_scanned_block = 0
        self.vaults: list[VaultEntry] = []
        self._cache_file = CACHE_DIR / f"chain{chain_id}_vaults.json"

    def load(self) -> "VaultCache":
        """Load cache from disk. Discards if chain_id or factory don't match."""
        if not self._cache_file.exists():
            return self

        try:
            data = json.loads(self._cache_file.read_text())
        except (json.JSONDecodeError, OSError):
            return self

        if (
            data.get("chain_id") != self.chain_id
            or data.get("factory", "").lower() != self.factory_addr.lower()
        ):
            return self

        self.last_scanned_block = data.get("last_scanned_block", 0)
        self.vaults = [VaultEntry.from_dict(v) for v in data.get("vaults", [])]
        return self

    def save(self) -> None:
        """Atomic write cache to disk (temp file + rename)."""
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        data = {
            "chain_id": self.chain_id,
            "factory": self.factory_addr,
            "last_scanned_block": self.last_scanned_block,
            "vaults": [v.to_dict() for v in self.vaults],
        }
        fd, tmp = tempfile.mkstemp(dir=CACHE_DIR, suffix=".tmp")
        closed = False
        try:
            os.write(fd, json.dumps(data, indent=2).encode())
            os.close(fd)
            closed = True
            os.replace(tmp, self._cache_file)
        except Exception:
            if not closed:
                os.close(fd)
            if os.path.exists(tmp):
                os.unlink(tmp)
            raise

    def update(self, to_block: int | None = None) -> int:
        """Scan new events from last_scanned_block + 1 to to_block.

        Returns the number of new vaults discovered.
        """
        from ape import chain

        current_block = to_block if to_block is not None else chain.blocks.height
        from_block = self.last_scanned_block + 1 if self.last_scanned_block > 0 else MAINNET_START_BLOCK

        if from_block > current_block:
            return 0

        factory = Contract(self.factory_addr, abi=_load_abi("CollateralVaultFactory"))
        cv_abi = _load_abi("CollateralVault")

        # Existing addresses for dedup
        existing = {v.address.lower() for v in self.vaults}

        if from_block == MAINNET_START_BLOCK:
            click.echo("Building vault cache (first run — scanning from factory deployment)...", err=True)
        else:
            gap = current_block - from_block + 1
            click.echo(f"Updating vault cache ({gap} new blocks to scan)...", err=True)

        events = list(factory.T_CollateralVaultCreated.range(from_block, current_block))

        new_count = 0
        for evt in events:
            vault_addr = str(evt.vault)
            if vault_addr.lower() in existing:
                continue

            # Query immutable asset() for this vault
            try:
                cv = Contract(vault_addr, abi=cv_abi)
                asset_addr = str(cv.asset())
            except Exception:
                asset_addr = ""

            self.vaults.append(VaultEntry(
                address=vault_addr,
                block=evt.block_number,
                asset=asset_addr,
            ))
            existing.add(vault_addr.lower())
            new_count += 1

        self.last_scanned_block = current_block
        return new_count

    def get_vaults(self, up_to_block: int | None = None) -> list[VaultEntry]:
        """Return vault list, optionally filtered to those created at or before a block."""
        if up_to_block is None:
            return list(self.vaults)
        return [v for v in self.vaults if v.block <= up_to_block]

    def get_unique_assets(self, up_to_block: int | None = None) -> dict[str, list[str]]:
        """Return deduplicated mapping: asset_addr → [vault_addrs].

        Useful for protocol overview to discover collateral asset types.
        """
        vaults = self.get_vaults(up_to_block)
        assets: dict[str, list[str]] = {}
        for v in vaults:
            key = v.asset.lower()
            if key not in assets:
                assets[key] = []
            assets[key].append(v.address)
        return assets


def get_vault_cache(ctx_obj) -> VaultCache:
    """Convenience: load (or rebuild) the vault cache based on CLI context.

    Handles --no-cache flag and --block flag.
    """
    cache = VaultCache(chain_id=1)

    if not ctx_obj.no_cache:
        cache.load()

    block = ctx_obj.resolve_block()
    cache.update(to_block=block)

    if not ctx_obj.no_cache:
        cache.save()

    return cache
