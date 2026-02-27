"""Tests for shell completion functions and the completion command."""

import json
import os
import tempfile
from unittest.mock import patch

import pytest
from click.shell_completion import CompletionItem

from twyne_cli.completions import (
    complete_asset_or_iv,
    complete_iv_address,
    complete_target_vault,
    complete_vault_address,
)


# --------------------------------------------------------------------------- #
# Vault address completion (from cache)
# --------------------------------------------------------------------------- #


@pytest.fixture
def vault_cache_dir(tmp_path):
    """Create a temporary vault cache directory with test data."""
    cache_file = tmp_path / "chain1_vaults.json"
    cache_file.write_text(json.dumps({
        "chain_id": 1,
        "factory": "0xa1517cCe0bE75700A8838EA1cEE0dc383cd3A332",
        "last_scanned_block": 99999,
        "vaults": [
            {"address": "0xABCD1234000000000000000000000000DEADBEEF", "block": 100, "asset": "0x1"},
            {"address": "0xABCD5678000000000000000000000000CAFEBABE", "block": 200, "asset": "0x2"},
            {"address": "0x1111222233334444555566667777888899990000", "block": 300, "asset": "0x3"},
        ],
    }))
    return tmp_path


def test_vault_completion_with_cache(vault_cache_dir):
    with patch("twyne_cli.completions._CACHE_DIR", vault_cache_dir):
        results = complete_vault_address(None, None, "0xABCD")
    assert len(results) == 2
    assert all(isinstance(r, CompletionItem) for r in results)
    addresses = [r.value for r in results]
    assert "0xABCD1234000000000000000000000000DEADBEEF" in addresses
    assert "0xABCD5678000000000000000000000000CAFEBABE" in addresses


def test_vault_completion_case_insensitive(vault_cache_dir):
    with patch("twyne_cli.completions._CACHE_DIR", vault_cache_dir):
        results = complete_vault_address(None, None, "0xabcd")
    assert len(results) == 2


def test_vault_completion_no_match(vault_cache_dir):
    with patch("twyne_cli.completions._CACHE_DIR", vault_cache_dir):
        results = complete_vault_address(None, None, "0xFFFF")
    assert len(results) == 0


def test_vault_completion_empty_prefix(vault_cache_dir):
    with patch("twyne_cli.completions._CACHE_DIR", vault_cache_dir):
        results = complete_vault_address(None, None, "")
    assert len(results) == 3


def test_vault_completion_missing_cache(tmp_path):
    with patch("twyne_cli.completions._CACHE_DIR", tmp_path):
        results = complete_vault_address(None, None, "0x")
    assert results == []


def test_vault_completion_malformed_cache(tmp_path):
    cache_file = tmp_path / "chain1_vaults.json"
    cache_file.write_text("not json")
    with patch("twyne_cli.completions._CACHE_DIR", tmp_path):
        results = complete_vault_address(None, None, "0x")
    assert results == []


# --------------------------------------------------------------------------- #
# IV address completion (from bundled mainnet.json)
# --------------------------------------------------------------------------- #


def test_iv_completion_by_address_prefix():
    # Real addresses from mainnet.json — euler_eWETH starts with 0x87b8
    results = complete_iv_address(None, None, "0x87b8")
    assert len(results) >= 1
    assert any("euler_eWETH" in r.help for r in results)


def test_iv_completion_by_name():
    results = complete_iv_address(None, None, "euler_e")
    assert len(results) >= 2  # euler_eWETH and euler_ewstETH
    addresses = [r.value for r in results]
    assert all(a.startswith("0x") for a in addresses)


def test_iv_completion_case_insensitive_name():
    results = complete_iv_address(None, None, "EULER_E")
    assert len(results) >= 2


def test_iv_completion_empty_prefix():
    results = complete_iv_address(None, None, "")
    assert len(results) == 3  # euler_eWETH, euler_ewstETH, aave_awstETH


def test_iv_completion_no_match():
    results = complete_iv_address(None, None, "nonexistent")
    assert results == []


# --------------------------------------------------------------------------- #
# Target vault completion
# --------------------------------------------------------------------------- #


def test_target_vault_completion_by_address():
    # euler_eUSDC starts with 0x797D
    results = complete_target_vault(None, None, "0x797D")
    assert len(results) >= 1
    assert any("euler_eUSDC" in r.help for r in results)


def test_target_vault_completion_by_name():
    results = complete_target_vault(None, None, "euler_eU")
    assert len(results) >= 2  # euler_eUSDC, euler_eUSDT


def test_target_vault_completion_empty():
    results = complete_target_vault(None, None, "")
    assert len(results) >= 4  # eUSDC, eUSDT, eWBTC, eWETH, aave_pool


# --------------------------------------------------------------------------- #
# complete_asset_or_iv delegates to complete_iv_address
# --------------------------------------------------------------------------- #


def test_asset_or_iv_delegates():
    # Should return same results as IV completion
    iv_results = complete_iv_address(None, None, "euler")
    asset_results = complete_asset_or_iv(None, None, "euler")
    assert len(iv_results) == len(asset_results)
    assert [r.value for r in iv_results] == [r.value for r in asset_results]


# --------------------------------------------------------------------------- #
# Completion command (generates shell scripts)
# --------------------------------------------------------------------------- #


def test_completion_command_bash():
    from click.testing import CliRunner
    from twyne_cli.commands.completion import completion

    runner = CliRunner()
    result = runner.invoke(completion, ["bash"])
    assert result.exit_code == 0
    assert "_TWYNE_COMPLETE" in result.output


def test_completion_command_zsh():
    from click.testing import CliRunner
    from twyne_cli.commands.completion import completion

    runner = CliRunner()
    result = runner.invoke(completion, ["zsh"])
    assert result.exit_code == 0
    assert "_TWYNE_COMPLETE" in result.output


def test_completion_command_fish():
    from click.testing import CliRunner
    from twyne_cli.commands.completion import completion

    runner = CliRunner()
    result = runner.invoke(completion, ["fish"])
    assert result.exit_code == 0
    assert "_TWYNE_COMPLETE" in result.output


def test_completion_command_invalid_shell():
    from click.testing import CliRunner
    from twyne_cli.commands.completion import completion

    runner = CliRunner()
    result = runner.invoke(completion, ["powershell"])
    assert result.exit_code != 0
