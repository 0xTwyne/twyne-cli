"""MegaETH live-RPC smoke tests (read-only).

Hits https://mainnet.megaeth.com/rpc directly. Verifies that the on-chain state
matches our pinned addresses and that read-only CLI commands work end-to-end.

Run: `uv run pytest tests/integration/megaeth/test_smoke.py --live`
"""

from __future__ import annotations

import json

import httpx
import pytest
from click.testing import CliRunner

from .conftest import (
    AAVE_INTERMEDIATE_VAULT,
    AAVE_POOL,
    CHAIN_ID,
    COLLATERAL_VAULT_FACTORY,
    EVC,
    VAULT_MANAGER,
)

pytestmark = pytest.mark.live


def _eth_call(rpc: str, to: str, data: str) -> str:
    resp = httpx.post(
        rpc,
        json={"jsonrpc": "2.0", "method": "eth_call", "params": [{"to": to, "data": data}, "latest"], "id": 1},
        timeout=20,
    )
    resp.raise_for_status()
    body = resp.json()
    if "error" in body:
        raise RuntimeError(body["error"])
    return body["result"]


def _eth_get_code(rpc: str, addr: str) -> str:
    resp = httpx.post(
        rpc,
        json={"jsonrpc": "2.0", "method": "eth_getCode", "params": [addr, "latest"], "id": 1},
        timeout=20,
    )
    resp.raise_for_status()
    return resp.json()["result"]


# ---------------------------------------------------------------------------
# Direct RPC checks (no CLI)
# ---------------------------------------------------------------------------


def test_chain_id_matches(megaeth_live_rpc):
    """Live RPC reports chain id 4326."""
    resp = httpx.post(
        megaeth_live_rpc,
        json={"jsonrpc": "2.0", "method": "eth_chainId", "params": [], "id": 1},
        timeout=10,
    )
    assert int(resp.json()["result"], 16) == CHAIN_ID


@pytest.mark.parametrize(
    "label,address",
    [
        ("VaultManager", VAULT_MANAGER),
        ("CollateralVaultFactory", COLLATERAL_VAULT_FACTORY),
        ("EVC", EVC),
        ("AavePool", AAVE_POOL),
        ("AaveIntermediateVault", AAVE_INTERMEDIATE_VAULT),
    ],
)
def test_address_has_code(megaeth_live_rpc, label, address):
    """Each pinned address must be a deployed contract (non-empty bytecode)."""
    code = _eth_get_code(megaeth_live_rpc, address)
    assert code and code != "0x", f"{label} ({address}) has no bytecode on chain {CHAIN_ID}"


# ---------------------------------------------------------------------------
# CLI checks via Click runner against the live RPC
# ---------------------------------------------------------------------------


def _extract_json(output: str):
    """Find the first JSON value in mixed stdout/stderr output."""
    for i, ch in enumerate(output):
        if ch in "[{":
            return json.loads(output[i:])
    raise AssertionError(f"No JSON value found in: {output!r}")


def test_cli_protocol_overview_megaeth(megaeth_live_rpc):
    """`twyne --chain megaeth protocol overview --json` succeeds against live RPC."""
    from twyne_cli.cli import cli

    runner = CliRunner()
    result = runner.invoke(
        cli,
        ["--chain", "megaeth", "--rpc", megaeth_live_rpc, "--json", "protocol", "overview"],
        catch_exceptions=False,
    )
    assert result.exit_code == 0, result.output
    payload = _extract_json(result.output)
    # Aave-only deployment — at least one IV with the known aWETH wrapper as collateral.
    assets = payload["collateral_assets"]
    assert any("aave" in a["intermediate_vault"].lower() for a in assets), payload


def test_cli_vault_list_megaeth(megaeth_live_rpc, tmp_path, monkeypatch):
    """`twyne --chain megaeth vault list --no-cache --json` returns a JSON array."""
    from twyne_cli import cache as cache_mod
    from twyne_cli.cli import cli

    monkeypatch.setattr(cache_mod, "CACHE_DIR", tmp_path)

    runner = CliRunner()
    result = runner.invoke(
        cli,
        ["--chain", "megaeth", "--rpc", megaeth_live_rpc, "--no-cache", "--json", "vault", "list"],
        catch_exceptions=False,
    )
    assert result.exit_code == 0, result.output
    payload = _extract_json(result.output)
    # vault list returns {total_vaults: N, vaults: [...]}
    assert "vaults" in payload and isinstance(payload["vaults"], list), payload
    assert payload["total_vaults"] == len(payload["vaults"])


def test_cli_operators_blocked_on_live(megaeth_live_rpc):
    """Live-chain run still rejects operator commands (capability gate is chain-static)."""
    from twyne_cli.cli import cli

    runner = CliRunner()
    result = runner.invoke(
        cli,
        ["--chain", "megaeth", "--rpc", megaeth_live_rpc,
         "tx", "operators", "leverage",
         "0x0000000000000000000000000000000000000000", "1"],
    )
    assert result.exit_code != 0
    assert "not supported on MegaETH" in result.output
