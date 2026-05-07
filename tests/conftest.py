"""Test configuration for twyne-cli."""

import os

import pytest


def pytest_addoption(parser):
    parser.addoption(
        "--live",
        action="store_true",
        default=False,
        help="Run tests marked @pytest.mark.live (live read-only RPC tests).",
    )


def pytest_configure(config):
    config.addinivalue_line(
        "markers",
        "live: read-only tests that hit a live RPC endpoint (opt-in via --live).",
    )
    config.addinivalue_line(
        "markers",
        "integration: integration tests requiring a local Anvil fork.",
    )


def pytest_collection_modifyitems(config, items):
    if config.getoption("--live"):
        return
    skip_live = pytest.mark.skip(reason="live test — pass --live to run")
    for item in items:
        if "live" in item.keywords:
            item.add_marker(skip_live)


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
