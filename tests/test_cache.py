"""Tests for the vault event cache."""

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from twyne_cli.cache import VaultCache, VaultEntry


@pytest.fixture
def tmp_cache_dir(tmp_path):
    """Override CACHE_DIR to a temp directory."""
    with patch("twyne_cli.cache.CACHE_DIR", tmp_path):
        yield tmp_path


@pytest.fixture
def factory_addr():
    return "0xa1517cCe0bE75700A8838EA1cEE0dc383cd3A332"


@pytest.fixture
def cache(tmp_cache_dir, factory_addr):
    """A fresh VaultCache with patched paths."""
    with patch("twyne_cli.cache.get_address", return_value=factory_addr):
        c = VaultCache(chain_id=1)
        c._cache_file = tmp_cache_dir / "chain1_vaults.json"
        return c


# --------------------------------------------------------------------------- #
# VaultEntry
# --------------------------------------------------------------------------- #


def test_vault_entry_roundtrip():
    entry = VaultEntry(address="0xABC", block=100, asset="0xDEF")
    d = entry.to_dict()
    restored = VaultEntry.from_dict(d)
    assert restored.address == "0xABC"
    assert restored.block == 100
    assert restored.asset == "0xDEF"


# --------------------------------------------------------------------------- #
# Cache load/save
# --------------------------------------------------------------------------- #


def test_load_empty_cache(cache):
    """Load on missing file returns empty state."""
    cache.load()
    assert cache.last_scanned_block == 0
    assert cache.vaults == []


def test_save_and_load(cache, factory_addr):
    """Round-trip: save then load."""
    cache.vaults = [VaultEntry("0x111", 50, "0xAAA"), VaultEntry("0x222", 60, "0xBBB")]
    cache.last_scanned_block = 100
    cache.save()

    # Load into a fresh cache
    with patch("twyne_cli.cache.get_address", return_value=factory_addr):
        c2 = VaultCache(chain_id=1)
        c2._cache_file = cache._cache_file
        c2.load()

    assert c2.last_scanned_block == 100
    assert len(c2.vaults) == 2
    assert c2.vaults[0].address == "0x111"
    assert c2.vaults[1].asset == "0xBBB"


def test_load_mismatched_chain_id_discards(cache, tmp_cache_dir, factory_addr):
    """If chain_id doesn't match, cache is discarded."""
    # Write a cache file with chain_id=42
    data = {
        "chain_id": 42,
        "factory": factory_addr,
        "last_scanned_block": 200,
        "vaults": [{"address": "0xFFF", "block": 10, "asset": "0xEEE"}],
    }
    cache._cache_file.write_text(json.dumps(data))

    cache.load()
    assert cache.last_scanned_block == 0
    assert cache.vaults == []


def test_load_mismatched_factory_discards(cache, tmp_cache_dir):
    """If factory address doesn't match, cache is discarded."""
    data = {
        "chain_id": 1,
        "factory": "0xDEAD",
        "last_scanned_block": 200,
        "vaults": [{"address": "0xFFF", "block": 10, "asset": "0xEEE"}],
    }
    cache._cache_file.write_text(json.dumps(data))

    cache.load()
    assert cache.last_scanned_block == 0
    assert cache.vaults == []


def test_load_corrupt_json_discards(cache):
    """Corrupt JSON is handled gracefully."""
    cache._cache_file.write_text("{not valid json")
    cache.load()
    assert cache.last_scanned_block == 0
    assert cache.vaults == []


# --------------------------------------------------------------------------- #
# get_vaults filtering
# --------------------------------------------------------------------------- #


def test_get_vaults_no_filter(cache):
    cache.vaults = [
        VaultEntry("0xA", 10, "0x1"),
        VaultEntry("0xB", 20, "0x2"),
        VaultEntry("0xC", 30, "0x3"),
    ]
    assert len(cache.get_vaults()) == 3


def test_get_vaults_with_block_filter(cache):
    cache.vaults = [
        VaultEntry("0xA", 10, "0x1"),
        VaultEntry("0xB", 20, "0x2"),
        VaultEntry("0xC", 30, "0x3"),
    ]
    result = cache.get_vaults(up_to_block=20)
    assert len(result) == 2
    assert result[0].address == "0xA"
    assert result[1].address == "0xB"


def test_get_vaults_returns_copy(cache):
    """get_vaults returns a new list, not the internal one."""
    cache.vaults = [VaultEntry("0xA", 10, "0x1")]
    result = cache.get_vaults()
    result.append(VaultEntry("0xB", 20, "0x2"))
    assert len(cache.vaults) == 1


# --------------------------------------------------------------------------- #
# get_unique_assets
# --------------------------------------------------------------------------- #


def test_get_unique_assets(cache):
    cache.vaults = [
        VaultEntry("0xV1", 10, "0xAssetA"),
        VaultEntry("0xV2", 20, "0xAssetA"),
        VaultEntry("0xV3", 30, "0xAssetB"),
    ]
    assets = cache.get_unique_assets()
    assert len(assets) == 2
    assert len(assets["0xasseta"]) == 2  # lowercased
    assert len(assets["0xassetb"]) == 1


def test_get_unique_assets_with_block_filter(cache):
    cache.vaults = [
        VaultEntry("0xV1", 10, "0xAssetA"),
        VaultEntry("0xV2", 20, "0xAssetA"),
        VaultEntry("0xV3", 30, "0xAssetB"),
    ]
    assets = cache.get_unique_assets(up_to_block=20)
    assert "0xasseta" in assets
    assert "0xassetb" not in assets


# --------------------------------------------------------------------------- #
# Atomic write safety
# --------------------------------------------------------------------------- #


def test_save_creates_parent_dirs(cache, tmp_cache_dir):
    """save() creates parent directories if missing."""
    cache._cache_file = tmp_cache_dir / "nested" / "deep" / "cache.json"
    # Override CACHE_DIR for mkdir call
    with patch("twyne_cli.cache.CACHE_DIR", tmp_cache_dir / "nested" / "deep"):
        cache.save()
    assert cache._cache_file.exists()
