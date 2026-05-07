"""MegaETH integration test fixtures.

Two modes:

- **Anvil fork** (default for fork tests): start with
      anvil --fork-url https://mainnet.megaeth.com/rpc --chain-id 4326 --port 8455

- **Live RPC** (`@pytest.mark.live`, opt-in via `--live`): hit
  https://mainnet.megaeth.com/rpc directly for read-only verification.

This conftest also overrides the parent `tests/integration/conftest.py`'s
mainnet-Anvil-tied autouse fixture so MegaETH tests aren't gated on the
mainnet fork being up.
"""

from __future__ import annotations

import os

import httpx
import pytest

# ---------------------------------------------------------------------------
# Connection
# ---------------------------------------------------------------------------
ANVIL_RPC_MEGA = os.environ.get("ANVIL_RPC_URL_MEGAETH", "http://localhost:8455")
LIVE_RPC_MEGA = os.environ.get("RPC_URL_4326", "https://mainnet.megaeth.com/rpc")
CHAIN_ID = 4326

# ---------------------------------------------------------------------------
# Twyne core (from
# repos/tech-notes/megaeth-launch-addresses/MegaethDeployment_Apr30_2026/
# TwyneAddresses_output_megaeth.json)
# ---------------------------------------------------------------------------
VAULT_MANAGER = "0x91BB674Fcc7CA44ecF97d8330738f8c806318017"
COLLATERAL_VAULT_FACTORY = "0x65E83e6F11c28c2bAEe42c01Be57575d8dfF0037"
EVC = "0xFF06F28cf0c44Cf1E8F03E6835bB2F3a2a752C5C"
PROTOCOL_CONFIG = "0x84101fdEC409E6446263c4738c3AE11166EAa392"
GENERIC_FACTORY = "0x577D42B0e234a64925Ed0C73486959C95D240405"

# ---------------------------------------------------------------------------
# Aave (from TwyneAaveAddresses_megaeth.json)
# ---------------------------------------------------------------------------
AAVE_POOL = "0x7e324AbC5De01d112AfC03a584966ff199741C28"
AAVE_V3_WRAPPER = "0x95FcAe04fCD438A82D3E7c2c9c17F5462771baB6"
AAVE_INTERMEDIATE_VAULT = "0xcA883E66FC22792461E039d92350BeC04228f9F8"
ATOKEN_WRAPPER = "0x8db3a8Be0584E97A8634dBEb0110dE55fe504141"
ORACLE_ROUTER = "0x6a93bAFC66D05E8f3c13060c752976D8b2a49972"

# ---------------------------------------------------------------------------
# Override the parent mainnet-tied autouse fixture
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def anvil_clean_state():
    """No-op: MegaETH tests don't need the mainnet-Anvil setup chain."""
    yield


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _post(rpc_url: str, method: str, params=None, timeout: int = 15):
    resp = httpx.post(
        rpc_url,
        json={"jsonrpc": "2.0", "method": method, "params": params or [], "id": 1},
        timeout=timeout,
    )
    resp.raise_for_status()
    body = resp.json()
    if "error" in body:
        raise RuntimeError(f"{method} failed: {body['error']}")
    return body.get("result")


@pytest.fixture(scope="session")
def megaeth_anvil_fork():
    """Skip the test unless an Anvil fork is running on ANVIL_RPC_URL_MEGAETH."""
    try:
        result = _post(ANVIL_RPC_MEGA, "eth_chainId")
        chain_id = int(result, 16)
        if chain_id != CHAIN_ID:
            pytest.skip(
                f"Anvil reachable at {ANVIL_RPC_MEGA} but chain_id={chain_id} "
                f"(expected {CHAIN_ID}). Start with --chain-id 4326."
            )
    except Exception:
        pytest.skip(
            f"Anvil fork not running at {ANVIL_RPC_MEGA}. Start with: "
            f"anvil --fork-url {LIVE_RPC_MEGA} --chain-id {CHAIN_ID} --port 8455"
        )
    return ANVIL_RPC_MEGA


@pytest.fixture(scope="session")
def megaeth_live_rpc():
    """Probe the live MegaETH RPC; skip if unreachable."""
    try:
        result = _post(LIVE_RPC_MEGA, "eth_chainId", timeout=10)
        chain_id = int(result, 16)
        if chain_id != CHAIN_ID:
            pytest.skip(f"Live RPC returned chain_id={chain_id} (expected {CHAIN_ID}).")
    except Exception as e:
        pytest.skip(f"Live MegaETH RPC unreachable: {e}")
    return LIVE_RPC_MEGA
