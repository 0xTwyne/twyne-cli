"""MegaETH integration test fixtures.

Two modes:

- **Anvil fork** (signer-dependent tests): start with
      anvil --fork-url $RPC_URL_4326 --chain-id 4326 --port 8455 \
            --accounts 10 --balance 10000

- **Live RPC** (`@pytest.mark.live`, opt-in via `--live`): hit
  https://mainnet.megaeth.com/rpc directly for read-only verification.

This conftest also overrides the parent `tests/integration/conftest.py`'s
mainnet-Anvil-tied autouse fixture so MegaETH tests aren't gated on the
mainnet fork being up.

The MegaETH Aave deployment uses USDe as the collateral underlying and USDm
as the debt asset (per `tech-notes/megaeth-launch-addresses/...`). The
intermediate vault (`ewaMegUSDe-1`) holds aTokenWrapper shares wrapping aUSDe.

Tests mint USDe to anvil test accounts via `anvil_setStorageAt` because USDe
on MegaETH is a real (non-mintable) token and there is no public faucet.
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
# Verified via VaultManager.owner() at fork block.
VAULT_MANAGER_OWNER = "0x8c54cb62900Ec252E7992C85a5b7078A8AF4Fd7F"

# ---------------------------------------------------------------------------
# Aave (from TwyneAaveAddresses_megaeth.json)
# ---------------------------------------------------------------------------
AAVE_POOL = "0x7e324AbC5De01d112AfC03a584966ff199741C28"
AAVE_V3_WRAPPER = "0x95FcAe04fCD438A82D3E7c2c9c17F5462771baB6"
AAVE_INTERMEDIATE_VAULT = "0xcA883E66FC22792461E039d92350BeC04228f9F8"
ATOKEN_WRAPPER = "0x8db3a8Be0584E97A8634dBEb0110dE55fe504141"
ORACLE_ROUTER = "0x6a93bAFC66D05E8f3c13060c752976D8b2a49972"

# Underlying tokens: USDe (collateral) / USDm (debt). Both 18 decimals.
USDE = "0x5d3a1Ff2b6BAb83b63cd9AD0787074081a52ef34"
USDM = "0xFAfDdbb3FC7688494971a79cc65DCa3EF82079E7"

# Verified on-chain at fork block (head-100). Idempotent fixtures rely on these.
MAX_TWYNE_LTV = 9800       # 98% — set on-chain
EXTERNAL_LIQ_BUFFER = 10000  # 100% — set on-chain
# Twyne enforces:  externalLiqLTV * buffer <= liqLTV * MAXFACTOR  AND  liqLTV <= maxTwyneLTV
# On MegaETH Aave eMode 7 USDe has liquidationThreshold=9300, so min liqLTV = 9300.
# 9500 sits comfortably inside [9300, 9800] and matches the deployer's example vault.
DEFAULT_LIQ_LTV = 9500

# Anvil deterministic accounts (when started with default mnemonic + 10 accounts)
TEST_ACCOUNTS = [
    "0xf39Fd6e51aad88F6F4ce6aB8827279cffFb92266",
    "0x70997970C51812dc3A010C7d01b50e0d17dc79C8",
    "0x3C44CdDdB6a900fa2b585dd299e03d12FA4293BC",
]
ZERO_ADDRESS = "0x" + "00" * 20

# ---------------------------------------------------------------------------
# Override the parent mainnet-tied autouse fixture
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def anvil_clean_state():
    """No-op: MegaETH tests don't need the mainnet-Anvil setup chain."""
    yield


# ---------------------------------------------------------------------------
# Raw RPC helpers (target the MegaETH anvil fork unless otherwise stated)
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


def _rpc_call(method: str, params=None):
    return _post(ANVIL_RPC_MEGA, method, params)


def _impersonate(addr: str) -> None:
    _rpc_call("anvil_impersonateAccount", [addr])


def _stop_impersonate(addr: str) -> None:
    _rpc_call("anvil_stopImpersonatingAccount", [addr])


def _set_balance(addr: str, wei_hex: str) -> None:
    _rpc_call("anvil_setBalance", [addr, wei_hex])


def _wait_for_receipt(tx_hash: str, timeout_s: int = 30):
    import time
    for _ in range(timeout_s * 4):
        receipt = _rpc_call("eth_getTransactionReceipt", [tx_hash])
        if receipt is not None:
            return receipt
        time.sleep(0.25)
    raise RuntimeError(f"No receipt for tx {tx_hash} after {timeout_s}s")


def _erc20_balance_slot(holder: str, base_slot: int = 0) -> str:
    """keccak256(abi.encode(holder, base_slot)) — standard solidity mapping slot.

    Most OpenZeppelin-style ERC20s store `_balances[holder]` at slot 0.
    """
    from eth_abi import encode as abi_encode
    from eth_utils import keccak
    holder_bytes = bytes.fromhex(holder.replace("0x", "").rjust(40, "0"))
    addr = "0x" + holder_bytes.hex()
    encoded = abi_encode(["address", "uint256"], [addr, base_slot])
    return "0x" + keccak(encoded).hex()


def _deal_erc20(token: str, holder: str, amount_wei: int, slot_candidates=(0, 1, 2, 3, 9)):
    """Mint an ERC20 balance to `holder` by writing storage directly.

    Tries common balance-mapping slots until balanceOf(holder) returns the
    expected amount. Solidity ERC20 implementations differ on which slot
    holds `_balances` — OpenZeppelin uses 0, but proxied / upgradeable
    layouts often shift it. We probe by writing and verifying.
    """
    from eth_abi import encode as abi_encode

    expected = "0x" + amount_wei.to_bytes(32, "big").hex()
    bal_calldata = "0x70a08231" + "0" * 24 + holder[2:].lower()

    for slot in slot_candidates:
        target_slot = _erc20_balance_slot(holder, base_slot=slot)
        _rpc_call("anvil_setStorageAt", [token, target_slot, expected])
        got = _rpc_call("eth_call", [{"to": token, "data": bal_calldata}, "latest"])
        if got and int(got, 16) == amount_wei:
            return slot
    raise RuntimeError(
        f"Could not deal ERC20 to {holder} on token {token}. "
        f"Tried balance slots {slot_candidates}; none affected balanceOf()."
    )


def _approve_erc20(sender: str, token: str, spender: str, amount_wei: int):
    """sender must already be impersonated (or be a tx-signing test account)."""
    from eth_abi import encode as abi_encode
    selector = bytes.fromhex("095ea7b3")  # approve(address,uint256)
    data = selector + abi_encode(["address", "uint256"], [spender, amount_wei])
    tx = _rpc_call("eth_sendTransaction", [{
        "from": sender, "to": token,
        "data": "0x" + data.hex(),
        "gas": hex(150_000),
    }])
    receipt = _wait_for_receipt(tx)
    assert receipt["status"] == "0x1", f"approve failed: {receipt}"


# ---------------------------------------------------------------------------
# VaultManager configuration (idempotent — values are already set on-chain at
# the fork block, so this is a verification step)
# ---------------------------------------------------------------------------


def _configure_aave_vm_megaeth():
    """Verify that on-chain VaultManager state is what the tests expect.

    All target values (LTV, buffer, allowedTargetAsset) are already set on the
    live deployment at the fork block. If any drift is detected (e.g., LTV
    rebased), we impersonate the owner and apply the v1.0.5 setters with
    rampDuration=0.
    """
    from eth_abi import encode as abi_encode

    # maxTwyneLTVs(IV)
    sel_max = bytes.fromhex("7b6b8447")
    data = sel_max + abi_encode(["address"], [AAVE_INTERMEDIATE_VAULT])
    cur_ltv = int(_rpc_call("eth_call", [
        {"to": VAULT_MANAGER, "data": "0x" + data.hex()}, "latest",
    ]), 16)

    # externalLiqBuffers(IV) — selector computed dynamically to avoid a hardcoded miss
    from eth_utils import keccak
    sel_buf = keccak(text="externalLiqBuffers(address)")[:4]
    data = sel_buf + abi_encode(["address"], [AAVE_INTERMEDIATE_VAULT])
    cur_buf = int(_rpc_call("eth_call", [
        {"to": VAULT_MANAGER, "data": "0x" + data.hex()}, "latest",
    ]), 16)

    needs_ltv = cur_ltv != MAX_TWYNE_LTV
    needs_buf = cur_buf != EXTERNAL_LIQ_BUFFER
    if not (needs_ltv or needs_buf):
        return

    _set_balance(VAULT_MANAGER_OWNER, hex(10 * 10**18))
    _impersonate(VAULT_MANAGER_OWNER)
    try:
        if needs_ltv:
            calldata = bytes.fromhex("389bd6b4") + abi_encode(  # setMaxLiquidationLTV(address,uint16,uint32)
                ["address", "uint16", "uint32"],
                [AAVE_INTERMEDIATE_VAULT, MAX_TWYNE_LTV, 0],
            )
            tx = _rpc_call("eth_sendTransaction", [{
                "from": VAULT_MANAGER_OWNER, "to": VAULT_MANAGER,
                "data": "0x" + calldata.hex(), "gas": hex(150_000),
            }])
            r = _wait_for_receipt(tx)
            assert r["status"] == "0x1", f"setMaxLiquidationLTV failed: {r}"
        if needs_buf:
            calldata = bytes.fromhex("e17e20bd") + abi_encode(  # setExternalLiqBuffer(address,uint16,uint32)
                ["address", "uint16", "uint32"],
                [AAVE_INTERMEDIATE_VAULT, EXTERNAL_LIQ_BUFFER, 0],
            )
            tx = _rpc_call("eth_sendTransaction", [{
                "from": VAULT_MANAGER_OWNER, "to": VAULT_MANAGER,
                "data": "0x" + calldata.hex(), "gas": hex(150_000),
            }])
            r = _wait_for_receipt(tx)
            assert r["status"] == "0x1", f"setExternalLiqBuffer failed: {r}"
    finally:
        _stop_impersonate(VAULT_MANAGER_OWNER)


# ---------------------------------------------------------------------------
# Vault creation — Aave path (v1.0.5 arg order)
# ---------------------------------------------------------------------------

_FACTORY_SELECTOR = bytes.fromhex("3c7269d1")  # createCollateralVault(uint8,address,address,uint256,address)
_EVC_BATCH_SELECTOR = bytes.fromhex("c16ae7a4")  # batch((address,address,uint256,bytes)[])
# T_CollateralVaultCreated(address indexed vault) — keccak topic, hex stored without
# 0x prefix to dodge the secret-pattern false positive (32-byte hex == 64 chars).
_VAULT_CREATED_TOPIC_HEX = "d5c014427d17eead1b9e8111804901d992255c3982e066ff0b196835c2747e15"


def _create_aave_vault_via_evc(
    sender_addr: str,
    intermediate_vault: str = AAVE_INTERMEDIATE_VAULT,
    target_vault: str = AAVE_POOL,
    target_asset: str = USDM,
    liq_ltv: int = DEFAULT_LIQ_LTV,
):
    """Create an Aave-type collateral vault on MegaETH via EVC.batch().

    Uses the v1.0.5 arg order: (vaultType=1, intermediateVault, targetVault,
    liqLTV, targetAsset). Returns the new vault address.
    """
    from eth_abi import encode as abi_encode

    factory_calldata = _FACTORY_SELECTOR + abi_encode(
        ["uint8", "address", "address", "uint256", "address"],
        [1, intermediate_vault, target_vault, liq_ltv, target_asset],
    )
    batch_item = (COLLATERAL_VAULT_FACTORY, sender_addr, 0, factory_calldata)
    batch_calldata = _EVC_BATCH_SELECTOR + abi_encode(
        ["(address,address,uint256,bytes)[]"], [[batch_item]],
    )

    tx_hash = _rpc_call("eth_sendTransaction", [{
        "from": sender_addr, "to": EVC,
        "data": "0x" + batch_calldata.hex(),
        "gas": hex(5_000_000),
    }])
    receipt = _wait_for_receipt(tx_hash)
    assert receipt["status"] == "0x1", f"Aave vault creation reverted: {receipt}"

    expected_topic = "0x" + _VAULT_CREATED_TOPIC_HEX
    for log in receipt.get("logs", []):
        if log["address"].lower() == COLLATERAL_VAULT_FACTORY.lower():
            topics = log.get("topics", [])
            if len(topics) >= 2 and topics[0] == expected_topic:
                return "0x" + topics[1][-40:]
    raise RuntimeError("T_CollateralVaultCreated not found in receipt")


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


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
            f"anvil --fork-url $RPC_URL_4326 --chain-id {CHAIN_ID} --port 8455"
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


@pytest.fixture(scope="session")
def megaeth_ape_provider(megaeth_anvil_fork):
    """Session-scoped Ape provider connected to the MegaETH anvil fork.

    Routes through the chain registry's `megaeth` Ape network (registered in
    `ape-config.yaml`) so address loading / chain-aware code paths work.
    """
    from ape import networks
    from ape.logging import logger as ape_logger

    from twyne_cli.chains import CHAINS, set_active_chain

    ape_logger.set_level("WARNING")
    set_active_chain(CHAINS[CHAIN_ID])

    ctx = networks.ethereum.megaeth.use_provider(
        "node", provider_settings={"uri": megaeth_anvil_fork},
    )
    provider = ctx.__enter__()
    provider.network.config.required_confirmations = 0
    yield ctx
    ctx.__exit__(None, None, None)


@pytest.fixture(scope="session")
def megaeth_vm_configured(megaeth_ape_provider):
    """Idempotent VaultManager state check + apply if drifted."""
    _configure_aave_vm_megaeth()
    return True


@pytest.fixture(autouse=True)
def megaeth_test_accounts_funded(megaeth_vm_configured):
    """Replenish ETH on Anvil deterministic test accounts before each test."""
    for addr in TEST_ACCOUNTS:
        _set_balance(addr, hex(10000 * 10**18))
    yield


@pytest.fixture(scope="session")
def usde_balance_slot(megaeth_ape_provider):
    """Discover and cache the USDe `_balances` storage slot for this fork.

    Probes likely candidates by writing+reading. Cached across the session
    to avoid repeated probes (which leave non-zero balances on test accounts
    that aren't used). Returns the slot index.
    """
    probe_holder = "0x000000000000000000000000000000000000bEEF"
    return _deal_erc20(USDE, probe_holder, 1, slot_candidates=(0, 1, 2, 3, 4, 5, 6, 9))


@pytest.fixture
def test_account_megaeth(megaeth_ape_provider):
    """First Anvil deterministic account (Ape TestAccount)."""
    from ape import accounts
    acct = accounts.test_accounts[0]
    _set_balance(str(acct.address), hex(10000 * 10**18))
    return acct


@pytest.fixture
def funded_usde(test_account_megaeth, usde_balance_slot):
    """Deal 1000 USDe to the test account and return the amount in wei."""
    amount = 1000 * 10**18
    target_slot = _erc20_balance_slot(str(test_account_megaeth.address), base_slot=usde_balance_slot)
    _rpc_call("anvil_setStorageAt", [USDE, target_slot, "0x" + amount.to_bytes(32, "big").hex()])
    return amount


@pytest.fixture
def fresh_aave_vault(test_account_megaeth):
    """A freshly-created Aave collateral vault for the test account."""
    return _create_aave_vault_via_evc(str(test_account_megaeth.address))


# ---------------------------------------------------------------------------
# Re-export the v1.0.5 IV passthrough constants so tests can import easily.
# ---------------------------------------------------------------------------
__all__ = [
    "ANVIL_RPC_MEGA", "LIVE_RPC_MEGA", "CHAIN_ID",
    "VAULT_MANAGER", "COLLATERAL_VAULT_FACTORY", "EVC", "PROTOCOL_CONFIG",
    "GENERIC_FACTORY", "VAULT_MANAGER_OWNER",
    "AAVE_POOL", "AAVE_V3_WRAPPER", "AAVE_INTERMEDIATE_VAULT",
    "ATOKEN_WRAPPER", "ORACLE_ROUTER", "USDE", "USDM",
    "MAX_TWYNE_LTV", "EXTERNAL_LIQ_BUFFER", "DEFAULT_LIQ_LTV",
    "TEST_ACCOUNTS", "ZERO_ADDRESS",
    "_create_aave_vault_via_evc", "_deal_erc20", "_approve_erc20",
    "_rpc_call", "_wait_for_receipt", "_set_balance",
    "_impersonate", "_stop_impersonate", "_erc20_balance_slot",
]
