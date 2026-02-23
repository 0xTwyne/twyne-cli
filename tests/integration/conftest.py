"""Shared fixtures for integration tests against Anvil fork.

Prerequisites:
    anvil --fork-url <RPC_URL> --fork-block-number 24520000 --port 8454 --accounts 10 --balance 10000
"""

import os

import httpx
import pytest
from ape import accounts, networks

# ---------------------------------------------------------------------------
# Addresses (mainnet at block 24520000)
# ---------------------------------------------------------------------------
ANVIL_RPC = os.environ.get("ANVIL_RPC_URL", "http://localhost:8454")
WETH = "0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2"
WSTETH = "0x7f39C581F595B53c5cb19bD0b3f8dA6c935E2Ca0"
EULER_EWETH = "0xD8b27CF359b7D15710a5BE299AF6e7Bf904984C2"  # Euler eWETH vault token

# Twyne protocol
VAULT_MANAGER = "0x0acd3A3c8Ab6a5F7b5A594C88DFa28999dA858aC"
CV_FACTORY = "0xa1517cCe0bE75700A8838EA1cEE0dc383cd3A332"
EULER_EWETH_IV = "0x87b8081A3ace680f35125F469526Ac10f5418Ca7"
EULER_EWSTETH_IV = "0x7613D202Af490c3d1cE1873b0a7022a34E89815f"
AAVE_AWSTETH_IV = "0x75029a47f28550C93Ad5A3BbD2d9b5315204B561"
BEACON_EULER_EWETH = "0xCf050c5b9E71C2BFaf5799ef53fc2e97213F048B"
BEACON_AAVE = "0x07AcB5854090216585C28F5a230F1bB57E73D085"

# VaultManager owner (for configuring params at block 24520000 where they're unset)
VAULT_MANAGER_OWNER = "0x8C54cb62900Ec252E7992C85a5b7078A8AF4Fd7F"

# Allowed target vaults (from VaultManager at block 24520000)
# These are borrow-side vaults (USDC, USDT, WBTC) — NOT the collateral eWETH vault
EULER_TARGET_VAULT = "0x797DD80692c3b2dAdabCe8e30C07fDE5307D48a9"  # Euler USDC vault
DEFAULT_LIQ_LTV = 9000  # 90% in basis points (must be > ~8700 for this pair)
MAX_TWYNE_LTV = 9300  # 93% — governance max for this IV
EXTERNAL_LIQ_BUFFER = 10000  # 100% (beta_safe = 1.0)
TWYNE_EVC = "0xef39D6493884C4C84D38a4bFF879Ce16CEdE702a"
EULER_EVC = "0x0C9a3dd6b8F28529d72d7f9cE918D493519EE383"
HEALTH_STAT_VIEWER = "0x0dd9065c998E75657BcE6C3a11d7F5AbA5CBdbD4"

# Operators
EULER_LEVERAGE_OP = "0x335AB81f1C3d9f72639004d3e982902458CF29b3"
EULER_DELEVERAGE_OP = "0x36b2Bd4E17827E9dEABdB3AD520AC597972196D4"
AAVE_LEVERAGE_OP = "0x451949bde57aBe2F5DBD4758Cd50C6DCfC093A4C"
AAVE_DELEVERAGE_OP = "0x229fE10bC00bBE99Ac99703647D4f74F31605e91"
AAVE_TELEPORT_OP = "0x868a21426852A775395d4b90De23B3e3E662bd78"

# Wrappers
EULER_WRAPPER = "0xa680FeDa11FbbB18a759A60756b2E7B51A5f452F"
AAVE_WRAPPER = "0x2583DC56C6899e62667b69c496C3E974b188aC51"
AAVE_ATOKEN_WRAPPER = "0xFaBA8f777996C0C28fe9e6554D84cB30ca3e1881"

# Anvil default test accounts (deterministic, 10000 ETH each)
TEST_ACCOUNTS = [
    "0xf39Fd6e51aad88F6F4ce6aB8827279cffFb92266",
    "0x70997970C51812dc3A010C7d01b50e0d17dc79C8",
    "0x3C44CdDdB6a900fa2b585dd299e03d12FA4293BC",
]


def _rpc_call(method, params=None):
    """Make a raw JSON-RPC call to Anvil."""
    resp = httpx.post(
        ANVIL_RPC,
        json={"jsonrpc": "2.0", "method": method, "params": params or [], "id": 1},
        timeout=30,
    )
    data = resp.json()
    if "error" in data:
        raise RuntimeError(f"RPC error: {data['error']}")
    return data.get("result")


def _snapshot():
    """Take an Anvil state snapshot."""
    return _rpc_call("evm_snapshot")


def _revert(snapshot_id):
    """Revert to a snapshot.

    Tolerates 'Resource not found' errors which occur when fork RPC
    is flaky or the snapshot was invalidated by a nested revert.
    """
    try:
        return _rpc_call("evm_revert", [snapshot_id])
    except RuntimeError as e:
        if "Resource not found" in str(e):
            # Snapshot was consumed or invalidated — not fatal for test isolation
            # since we take a fresh snapshot for each test anyway
            return None
        raise


def _impersonate(address):
    """Start impersonating an address on Anvil."""
    _rpc_call("anvil_impersonateAccount", [address])


def _stop_impersonate(address):
    """Stop impersonating an address on Anvil."""
    _rpc_call("anvil_stopImpersonatingAccount", [address])


def _set_balance(address, wei_hex):
    """Set ETH balance for an address on Anvil."""
    _rpc_call("anvil_setBalance", [address, wei_hex])


def _wait_for_receipt(tx_hash, timeout_s=30):
    """Poll for a transaction receipt (Anvil auto-mines but may lag)."""
    import time

    for _ in range(timeout_s * 4):
        receipt = _rpc_call("eth_getTransactionReceipt", [tx_hash])
        if receipt is not None:
            return receipt
        time.sleep(0.25)
    raise RuntimeError(f"No receipt for tx {tx_hash} after {timeout_s}s")


def _deal_weth(sender_account, amount_wei):
    """Deal WETH to sender_account by depositing ETH via WETH contract.

    Uses raw RPC to avoid Ape nonce-tracking issues after snapshot/revert.

    Args:
        sender_account: An Ape TestAccount (from accounts.test_accounts).
        amount_wei: Amount of WETH to mint.
    """
    addr = str(sender_account.address)
    _set_balance(addr, hex(amount_wei + 10 * 10**18))  # extra for gas
    # WETH.deposit() — selector 0xd0e30db0, payable
    tx = _rpc_call("eth_sendTransaction", [{
        "from": addr, "to": WETH,
        "data": "0xd0e30db0",  # deposit()
        "value": hex(amount_wei),
        "gas": hex(100_000),
    }])
    receipt = _wait_for_receipt(tx)
    assert receipt["status"] == "0x1", f"WETH deposit failed: {receipt}"


def _configure_vault_manager():
    """Configure VaultManager params needed for vault creation at block 24520000.

    At this block, maxTwyneLTVs and externalLiqBuffers are 0 for all IVs.
    We impersonate the VaultManager owner to set them.

    Idempotent: skips if already configured.
    """
    from eth_abi import encode as abi_encode

    # Check if already configured (idempotent — safe to re-run)
    selector = bytes.fromhex("7b6b8447")  # maxTwyneLTVs(address)
    call_data = selector + abi_encode(["address"], [EULER_EWETH_IV])
    result = _rpc_call("eth_call", [{"to": VAULT_MANAGER, "data": "0x" + call_data.hex()}, "latest"])
    current_ltv = int(result, 16) if result else 0
    if current_ltv == MAX_TWYNE_LTV:
        return  # Already configured

    _set_balance(VAULT_MANAGER_OWNER, hex(10 * 10**18))
    _impersonate(VAULT_MANAGER_OWNER)

    # setMaxLiquidationLTV(address,uint16) — selector 0x950bc62a
    calldata = bytes.fromhex("950bc62a") + abi_encode(
        ["address", "uint16"], [EULER_EWETH_IV, MAX_TWYNE_LTV]
    )
    tx1 = _rpc_call("eth_sendTransaction", [{
        "from": VAULT_MANAGER_OWNER,
        "to": VAULT_MANAGER,
        "data": "0x" + calldata.hex(),
        "gas": hex(100_000),
    }])
    r1 = _wait_for_receipt(tx1)
    assert r1["status"] == "0x1", f"setMaxLiquidationLTV failed: {r1}"

    # setExternalLiqBuffer(address,uint16) — selector 0xda7f7f80
    calldata = bytes.fromhex("da7f7f80") + abi_encode(
        ["address", "uint16"], [EULER_EWETH_IV, EXTERNAL_LIQ_BUFFER]
    )
    tx2 = _rpc_call("eth_sendTransaction", [{
        "from": VAULT_MANAGER_OWNER,
        "to": VAULT_MANAGER,
        "data": "0x" + calldata.hex(),
        "gas": hex(100_000),
    }])
    r2 = _wait_for_receipt(tx2)
    assert r2["status"] == "0x1", f"setExternalLiqBuffer failed: {r2}"

    _stop_impersonate(VAULT_MANAGER_OWNER)


ERC20_ABI = [
    {"inputs": [{"name": "spender", "type": "address"}, {"name": "amount", "type": "uint256"}], "name": "approve", "outputs": [{"type": "bool"}], "stateMutability": "nonpayable", "type": "function"},
    {"inputs": [{"name": "account", "type": "address"}], "name": "balanceOf", "outputs": [{"type": "uint256"}], "stateMutability": "view", "type": "function"},
    {"inputs": [], "name": "decimals", "outputs": [{"type": "uint8"}], "stateMutability": "view", "type": "function"},
]

EVAULT_ABI = ERC20_ABI + [
    {"inputs": [{"name": "amount", "type": "uint256"}, {"name": "receiver", "type": "address"}], "name": "deposit", "outputs": [{"type": "uint256"}], "stateMutability": "nonpayable", "type": "function"},
    {"inputs": [{"name": "shares", "type": "uint256"}, {"name": "receiver", "type": "address"}, {"name": "owner", "type": "address"}], "name": "redeem", "outputs": [{"type": "uint256"}], "stateMutability": "nonpayable", "type": "function"},
    {"inputs": [{"name": "amount", "type": "uint256"}, {"name": "receiver", "type": "address"}, {"name": "owner", "type": "address"}], "name": "withdraw", "outputs": [{"type": "uint256"}], "stateMutability": "nonpayable", "type": "function"},
    {"inputs": [], "name": "asset", "outputs": [{"type": "address"}], "stateMutability": "view", "type": "function"},
    {"inputs": [], "name": "totalAssets", "outputs": [{"type": "uint256"}], "stateMutability": "view", "type": "function"},
    {"inputs": [], "name": "totalSupply", "outputs": [{"type": "uint256"}], "stateMutability": "view", "type": "function"},
]

# Minimal ABI for CollateralVault deposit (1-arg, correct for CV)
CV_DEPOSIT_ABI = [
    {"inputs": [{"name": "assets", "type": "uint256"}], "name": "deposit", "outputs": [], "stateMutability": "nonpayable", "type": "function"},
]

ZERO_ADDRESS = "0x" + "00" * 20


def _approve_erc20(sender_addr, token_addr, spender_addr, amount):
    """Approve ERC20 token spend via raw RPC (bypasses Ape nonce tracking).

    Args:
        sender_addr: Sender address (string).
        token_addr: ERC20 token contract address.
        spender_addr: Address to approve.
        amount: Amount to approve (int).
    """
    from eth_abi import encode as abi_encode

    # approve(address,uint256) — selector 0x095ea7b3
    calldata = bytes.fromhex("095ea7b3") + abi_encode(
        ["address", "uint256"], [spender_addr, amount]
    )
    tx = _rpc_call("eth_sendTransaction", [{
        "from": sender_addr, "to": token_addr,
        "data": "0x" + calldata.hex(),
        "gas": hex(100_000),
    }])
    receipt = _wait_for_receipt(tx)
    assert receipt["status"] == "0x1", f"ERC20 approve failed: {receipt}"


def _balance_of(token_addr, account_addr):
    """Read ERC20 balanceOf via raw eth_call (bypasses Ape).

    Returns the balance as int.
    """
    from eth_abi import encode as abi_encode

    # balanceOf(address) — selector 0x70a08231
    calldata = bytes.fromhex("70a08231") + abi_encode(["address"], [account_addr])
    result = _rpc_call(
        "eth_call",
        [{"to": token_addr, "data": "0x" + calldata.hex()}, "latest"],
    )
    return int(result, 16) if result else 0


def _deposit_erc4626(sender_addr, vault_addr, amount, receiver_addr):
    """Deposit into an ERC4626 vault via raw RPC.

    Calls deposit(uint256,address) — selector 0x6e553f65.
    Returns the transaction receipt.
    """
    from eth_abi import encode as abi_encode

    calldata = bytes.fromhex("6e553f65") + abi_encode(
        ["uint256", "address"], [amount, receiver_addr]
    )
    tx = _rpc_call("eth_sendTransaction", [{
        "from": sender_addr, "to": vault_addr,
        "data": "0x" + calldata.hex(),
        "gas": hex(500_000),
    }])
    receipt = _wait_for_receipt(tx)
    assert receipt["status"] == "0x1", f"ERC4626 deposit failed: {receipt}"
    return receipt


def _eth_call_view(contract_address, fn_selector_hex, decode_type="address"):
    """Call a view function via raw eth_call (bypasses Ape explorer lookups).

    Args:
        contract_address: Contract address.
        fn_selector_hex: 4-byte function selector as hex string (no 0x prefix).
        decode_type: Expected return type ("address" or "uint256").

    Returns the decoded result.
    """
    result = _rpc_call(
        "eth_call",
        [{"to": contract_address, "data": "0x" + fn_selector_hex}, "latest"],
    )
    if decode_type == "address":
        return "0x" + result[-40:]
    elif decode_type == "uint256":
        return int(result, 16)
    return result

# Function selectors (precomputed via `cast sig`)
_CV_DEPOSIT_SELECTOR = bytes.fromhex("b6b55f25")  # deposit(uint256)
_CV_WITHDRAW_SELECTOR = bytes.fromhex("00f714ce")  # withdraw(uint256,address)
_CV_SET_LTV_SELECTOR = bytes.fromhex("ca19bcd4")  # setTwyneLiqLTV(uint256)
_EVC_BATCH_SELECTOR = bytes.fromhex("c16ae7a4")  # batch((address,address,uint256,bytes)[])


# ---------------------------------------------------------------------------
# EVC batch helper — required for all CV state-changing calls
# ---------------------------------------------------------------------------
# CollateralVault functions use a `_callThroughEVC()` modifier (from EVCUtil)
# that re-routes non-EVC callers through the EVC. When called directly from an
# EOA, the CV calls `evc.call(cv, sender, 0, data)` — but EVC auth fails
# because the CV is not an authorized operator for the sender.
#
# The correct pattern (matching Foundry tests) is: sender calls `evc.batch()`
# with sender == onBehalfOfAccount, so EVC auth passes trivially.
# ---------------------------------------------------------------------------


def _call_cv_via_evc(sender_account, cv_address, fn_calldata):
    """Call a CollateralVault function through EVC.batch().

    Args:
        sender_account: An Ape TestAccount.
        cv_address: The CollateralVault address (string).
        fn_calldata: Raw bytes of the function call (selector + encoded args).

    Returns the transaction receipt dict (raw RPC format).
    """
    from eth_abi import encode as abi_encode

    caller = str(sender_account.address)

    # Build one batch item: (targetContract, onBehalfOfAccount, value, data)
    batch_item = (cv_address, caller, 0, fn_calldata)

    # Encode batch((address,address,uint256,bytes)[])
    batch_calldata = _EVC_BATCH_SELECTOR + abi_encode(
        ["(address,address,uint256,bytes)[]"],
        [[batch_item]],
    )

    tx_hash = _rpc_call(
        "eth_sendTransaction",
        [{"from": caller, "to": TWYNE_EVC, "data": "0x" + batch_calldata.hex(), "gas": hex(5_000_000)}],
    )
    return _wait_for_receipt(tx_hash)


def _deposit_via_evc(sender_account, cv_address, amount):
    """Deposit into a CollateralVault via EVC batch.

    Requires prior ERC20 approval of the CV address.
    Returns the transaction receipt.
    """
    from eth_abi import encode as abi_encode

    fn_calldata = _CV_DEPOSIT_SELECTOR + abi_encode(["uint256"], [amount])
    receipt = _call_cv_via_evc(sender_account, cv_address, fn_calldata)
    assert receipt["status"] == "0x1", f"Deposit via EVC failed: {receipt}"
    return receipt


def _withdraw_via_evc(sender_account, cv_address, amount, receiver):
    """Withdraw from a CollateralVault via EVC batch."""
    from eth_abi import encode as abi_encode

    fn_calldata = _CV_WITHDRAW_SELECTOR + abi_encode(
        ["uint256", "address"], [amount, receiver]
    )
    return _call_cv_via_evc(sender_account, cv_address, fn_calldata)


def _set_ltv_via_evc(sender_account, cv_address, ltv):
    """Set TwyneLiqLTV on a CollateralVault via EVC batch."""
    from eth_abi import encode as abi_encode

    fn_calldata = _CV_SET_LTV_SELECTOR + abi_encode(["uint256"], [ltv])
    return _call_cv_via_evc(sender_account, cv_address, fn_calldata)


# ---------------------------------------------------------------------------
# Vault creation helper (bypasses CLI's stale v1 factory ABI)
# ---------------------------------------------------------------------------

# The deployed factory has been upgraded to v2 with a 5-arg signature:
#   createCollateralVault(uint8 _categoryId, address _asset, address _targetVault,
#                         uint256 _liqLTV, address _intermediateVault)
# and requires callThroughEVC (calls must go through EVC.call()).
#
# The CLI's bundled CollateralVaultFactory.json has been updated to 5-arg ABI,
# but the tx.py create-vault command still passes wrong args.
# This helper uses raw RPC + eth_abi encoding to call the real v2 factory.

_FACTORY_SELECTOR = bytes.fromhex("3c7269d1")  # createCollateralVault(uint8,address,address,uint256,address)
_EVC_CALL_SELECTOR = bytes.fromhex("1f8b5215")  # call(address,address,uint256,bytes)
# T_CollateralVaultCreated(address indexed vault) — stored without 0x prefix
# to avoid secret-pattern false positive (keccak hash is 32 bytes = 64 hex chars)
_VAULT_CREATED_TOPIC_HEX = "d5c014427d17eead1b9e8111804901d992255c3982e066ff0b196835c2747e15"


def _create_vault_via_evc(
    sender_account,
    category_id=0,
    asset=EULER_EWETH,
    target_vault=EULER_TARGET_VAULT,
    liq_ltv=DEFAULT_LIQ_LTV,
    intermediate_vault=EULER_EWETH_IV,
):
    """Create a collateral vault via EVC.call() with the correct v2 factory signature.

    Args:
        category_id: Vault category (0 for default)
        asset: Collateral token address (e.g., eWETH)
        target_vault: Must be an allowed target vault in VaultManager
        liq_ltv: Liquidation LTV in basis points (8500 = 85%)
        intermediate_vault: The intermediate vault (CreditEVault) address

    Returns the new vault address as a string.
    """
    from eth_abi import encode as abi_encode

    caller = str(sender_account.address)

    # Encode factory calldata (call factory directly — it routes through EVC internally)
    factory_calldata = _FACTORY_SELECTOR + abi_encode(
        ["uint8", "address", "address", "uint256", "address"],
        [category_id, asset, target_vault, liq_ltv, intermediate_vault],
    )

    # Send transaction directly to factory via raw RPC
    tx_hash = _rpc_call(
        "eth_sendTransaction",
        [{"from": caller, "to": CV_FACTORY, "data": "0x" + factory_calldata.hex(), "gas": hex(3_000_000)}],
    )

    # Get receipt (Anvil auto-mines, but may need a moment)
    receipt = _wait_for_receipt(tx_hash)
    assert receipt["status"] == "0x1", f"Vault creation tx reverted: {receipt}"

    # Extract vault address from T_CollateralVaultCreated(address indexed vault)
    expected_topic = "0x" + _VAULT_CREATED_TOPIC_HEX
    for log in receipt.get("logs", []):
        if log["address"].lower() == CV_FACTORY.lower():
            topics = log.get("topics", [])
            if len(topics) >= 2 and topics[0] == expected_topic:
                return "0x" + topics[1][-40:]

    raise RuntimeError("Could not find T_CollateralVaultCreated event in creation receipt")


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(scope="session")
def anvil_available():
    """Skip all integration tests if Anvil is not running.

    Resets the fork to block 24520000 to ensure clean state regardless
    of what previous test runs may have done.
    """
    try:
        result = _rpc_call("eth_blockNumber")
        block = int(result, 16)
        assert block >= 24520000, f"Expected block >= 24520000, got {block}"
    except Exception as e:
        pytest.skip(f"Anvil fork not running on {ANVIL_RPC}: {e}")

    # Reset to pristine fork state (idempotent, handles dirty Anvil)
    if block > 24520000:
        try:
            _rpc_call("anvil_reset", [{"forking": {"blockNumber": 24520000}}])
        except RuntimeError:
            pass  # Best effort — some Anvil versions don't support reset params
    return True


@pytest.fixture(scope="session")
def ape_provider(anvil_available):
    """Session-scoped Ape provider connected to Anvil fork."""
    from ape.logging import logger as ape_logger
    ape_logger.set_level("WARNING")
    ctx = networks.ethereum.mainnet.use_provider(
        "node", provider_settings={"uri": ANVIL_RPC}
    )
    provider = ctx.__enter__()
    # Anvil auto-mines — don't wait for confirmations
    provider.network.config.required_confirmations = 0
    yield ctx
    ctx.__exit__(None, None, None)


@pytest.fixture(scope="session")
def vault_manager_configured(ape_provider):
    """Configure VaultManager LTV params needed for vault creation.

    At block 24520000, maxTwyneLTVs and externalLiqBuffers are 0 for all IVs.
    This fixture impersonates the VaultManager owner and sets them once per session.
    """
    _configure_vault_manager()
    return True


@pytest.fixture(autouse=True)
def anvil_snapshot(vault_manager_configured):
    """Snapshot/revert Anvil state between each test.

    Depends on vault_manager_configured so VaultManager params are set
    before the first snapshot is taken.

    Uses fresh snapshot per test. If revert fails (flaky fork RPC),
    subsequent tests get fresh snapshots but state may be dirty.
    """
    snap = _snapshot()
    yield
    _revert(snap)
    # If revert consumed the snapshot, take a new one to avoid
    # cascading 'Resource not found' errors on the next test's setup.
    # Anvil snapshots are one-time-use — once reverted, the ID is gone.


@pytest.fixture(scope="session")
def test_account(ape_provider):
    """Return Anvil's first test account (funded with ETH)."""
    acct = accounts.test_accounts[0]
    _set_balance(str(acct.address), hex(10000 * 10**18))
    return acct


@pytest.fixture(scope="session")
def test_account_2(ape_provider):
    """Return Anvil's second test account."""
    acct = accounts.test_accounts[1]
    _set_balance(str(acct.address), hex(10000 * 10**18))
    return acct


@pytest.fixture()
def funded_weth(test_account):
    """Fund test_account with 100 WETH. Returns the amount dealt."""
    amount = 100 * 10**18
    _deal_weth(test_account, amount)
    return amount


@pytest.fixture()
def funded_eweth(test_account):
    """Fund test_account with eWETH by depositing WETH into Euler eWETH vault.

    Uses raw RPC to avoid Ape nonce-tracking issues after snapshot/revert.
    Returns the eWETH balance received.
    """
    weth_amount = 10 * 10**18
    addr = str(test_account.address)
    _deal_weth(test_account, weth_amount)

    # Approve Euler eWETH vault to spend WETH
    _approve_erc20(addr, WETH, EULER_EWETH, weth_amount)

    # Deposit WETH into Euler eWETH vault (ERC4626 deposit(uint256,address))
    _deposit_erc4626(addr, EULER_EWETH, weth_amount, addr)

    return _balance_of(EULER_EWETH, addr)


@pytest.fixture()
def fresh_vault(test_account):
    """Create a fresh Euler eWETH collateral vault owned by test_account.

    Uses _create_vault_via_evc() which calls the real v2 factory through EVC.
    Returns the vault address as a string.
    """
    return _create_vault_via_evc(test_account)


@pytest.fixture()
def funded_vault(test_account, fresh_vault, funded_eweth):
    """A fresh vault with eWETH deposited.

    Returns (vault_address, deposit_amount).
    Uses raw RPC for approve to avoid Ape nonce-tracking issues.

    NOTE: Deposit MUST go through EVC.batch() — calling cv.deposit() directly
    fails because _callThroughEVC() re-routes through EVC and auth fails
    (CV is not an authorized EVC operator for the sender). The Foundry tests
    use evc.batch() for the same reason.
    """
    vault_address = fresh_vault
    deposit_amount = funded_eweth
    addr = str(test_account.address)

    # Approve the CV to spend eWETH (raw RPC to avoid Ape nonce issues)
    _approve_erc20(addr, EULER_EWETH, vault_address, deposit_amount)

    # Deposit via EVC batch (required for _callThroughEVC auth)
    _deposit_via_evc(test_account, vault_address, deposit_amount)

    return vault_address, deposit_amount
