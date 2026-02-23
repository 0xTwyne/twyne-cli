"""Test configuration for twyne-cli."""

import os

import pytest


@pytest.fixture(scope="session")
def anvil_fork():
    """Check if Anvil fork is running on port 8454 (matches twyne-sdk test setup)."""
    rpc_url = os.environ.get("ANVIL_RPC_URL", "http://localhost:8454")
    try:
        import httpx
        resp = httpx.post(rpc_url, json={"jsonrpc": "2.0", "method": "eth_blockNumber", "params": [], "id": 1}, timeout=5)
        if resp.status_code == 200:
            return rpc_url
    except Exception:
        pass
    pytest.skip("Anvil fork not running on port 8454. Start with: anvil --fork-url <RPC> --port 8454")
