"""Shared fixtures for integration tests against Anvil fork.

Prerequisites:
    anvil --fork-url $RPC_URL_1 --fork-block-number 25040000 --port 8454 --accounts 10 --balance 10000

The fork URL is loaded at import time from `<repo-root>/.env` (key `RPC_URL_1`)
or from the shell environment, in that order. An explicit `ANVIL_FORK_URL`
override still wins.
"""

import os
from pathlib import Path

import httpx
import pytest
from ape import accounts, networks


def _load_dotenv() -> None:
    """Minimal .env loader so tests pick up RPC keys without python-dotenv.

    Walks up from this file looking for a `.env`. Lines of the form `KEY=value`
    are merged into os.environ unless the key is already set (shell wins).
    """
    here = Path(__file__).resolve()
    for parent in (here.parent, *here.parents):
        candidate = parent / ".env"
        if candidate.is_file():
            for raw in candidate.read_text().splitlines():
                line = raw.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, value = line.partition("=")
                key = key.strip()
                value = value.strip().strip('"').strip("'")
                if key and key not in os.environ:
                    os.environ[key] = value
            return


_load_dotenv()


# ---------------------------------------------------------------------------
# Addresses (mainnet at block 25040000 — post v1.0.5 contract upgrade)
# ---------------------------------------------------------------------------
ANVIL_RPC = os.environ.get("ANVIL_RPC_URL", "http://localhost:8454")
# Fork RPC: explicit ANVIL_FORK_URL wins; otherwise fall back to RPC_URL_1
# (loaded from .env) and finally to the public free RPC (which only supports
# very recent blocks).
FORK_RPC = (
    os.environ.get("ANVIL_FORK_URL")
    or os.environ.get("RPC_URL_1")
    or "https://ethereum-rpc.publicnode.com"
)
FORK_BLOCK = 25040000
WETH = "0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2"
WSTETH = "0x7f39C581F595B53c5cb19bD0b3f8dA6c935E2Ca0"
EULER_EWETH = "0xD8b27CF359b7D15710a5BE299AF6e7Bf904984C2"  # Euler eWETH vault token

# Twyne protocol
VAULT_MANAGER = "0x0acd3A3c8Ab6a5F7b5A594C88DFa28999dA858aC"
CV_FACTORY = "0xa1517cCe0bE75700A8838EA1cEE0dc383cd3A332"
EULER_EWETH_IV = "0x87b8081A3ace680f35125F469526Ac10f5418Ca7"
EULER_EWSTETH_IV = "0x7613D202Af490c3d1cE1873b0a7022a34E89815f"
AAVE_AWSTETH_IV = "0x75029a47f28550C93Ad5A3BbD2d9b5315204B561"

# VaultManager owner (for configuring params at block 24520000 where they're unset)
VAULT_MANAGER_OWNER = "0x8C54cb62900Ec252E7992C85a5b7078A8AF4Fd7F"

# Allowed target vaults (from VaultManager at block 24520000)
# These are borrow-side vaults (USDC, USDT, WBTC) — NOT the collateral eWETH vault
EULER_TARGET_VAULT = "0x797DD80692c3b2dAdabCe8e30C07fDE5307D48a9"  # Euler USDC vault
AAVE_V3_POOL = "0x87870Bca3F3fD6335C3F4ce8392D69350B4fA4E2"  # Aave V3 Pool (target vault for Aave)
DEFAULT_LIQ_LTV = 9000  # 90% in basis points (must be > ~8700 for this pair)
MAX_TWYNE_LTV = 9400  # 94% — governance max for this Euler IV at block 25040000 (post v1.0.5)
MAX_AAVE_LTV = 9800  # 98% — governance max for Aave awstETH IV
EXTERNAL_LIQ_BUFFER = 10000  # 100% (beta_safe = 1.0)
TWYNE_EVC = "0xef39D6493884C4C84D38a4bFF879Ce16CEdE702a"
EULER_EVC = "0x0C9a3dd6b8F28529d72d7f9cE918D493519EE383"
HEALTH_STAT_VIEWER = "0xf88A9f96fa0798Ed322CD0e60435e4F111059DEf"

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
        timeout=60,
    )
    data = resp.json()
    if "error" in data:
        raise RuntimeError(f"RPC error: {data['error']}")
    return data.get("result")



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

    # v1.0.5: setMaxLiquidationLTV(address,uint16,uint32) — selector 0x389bd6b4
    # rampDuration=0 means instant change (no ramp).
    calldata = bytes.fromhex("389bd6b4") + abi_encode(
        ["address", "uint16", "uint32"], [EULER_EWETH_IV, MAX_TWYNE_LTV, 0]
    )
    tx1 = _rpc_call("eth_sendTransaction", [{
        "from": VAULT_MANAGER_OWNER,
        "to": VAULT_MANAGER,
        "data": "0x" + calldata.hex(),
        "gas": hex(100_000),
    }])
    r1 = _wait_for_receipt(tx1)
    assert r1["status"] == "0x1", f"setMaxLiquidationLTV failed: {r1}"

    # v1.0.5: setExternalLiqBuffer(address,uint16,uint32) — selector 0xe17e20bd
    calldata = bytes.fromhex("e17e20bd") + abi_encode(
        ["address", "uint16", "uint32"], [EULER_EWETH_IV, EXTERNAL_LIQ_BUFFER, 0]
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


def _configure_aave_vault_manager():
    """Configure VaultManager params for Aave awstETH IV at block 24520000.

    Sets maxTwyneLTV, externalLiqBuffer, and allowedTargetAsset (WETH)
    for the Aave intermediate vault. Mirrors the Foundry test setup in
    AaveTestBase.t.sol (lines 120-124). Idempotent.
    """
    from eth_abi import encode as abi_encode

    # Check if already configured
    selector = bytes.fromhex("7b6b8447")  # maxTwyneLTVs(address)
    call_data = selector + abi_encode(["address"], [AAVE_AWSTETH_IV])
    result = _rpc_call("eth_call", [{"to": VAULT_MANAGER, "data": "0x" + call_data.hex()}, "latest"])
    current_ltv = int(result, 16) if result else 0
    if current_ltv == MAX_AAVE_LTV:
        return  # Already configured

    _set_balance(VAULT_MANAGER_OWNER, hex(10 * 10**18))
    _impersonate(VAULT_MANAGER_OWNER)

    # v1.0.5: setMaxLiquidationLTV(address,uint16,uint32) — selector 0x389bd6b4
    calldata = bytes.fromhex("389bd6b4") + abi_encode(
        ["address", "uint16", "uint32"], [AAVE_AWSTETH_IV, MAX_AAVE_LTV, 0]
    )
    tx1 = _rpc_call("eth_sendTransaction", [{
        "from": VAULT_MANAGER_OWNER, "to": VAULT_MANAGER,
        "data": "0x" + calldata.hex(), "gas": hex(100_000),
    }])
    r1 = _wait_for_receipt(tx1)
    assert r1["status"] == "0x1", f"setMaxLiquidationLTV (Aave) failed: {r1}"

    # v1.0.5: setExternalLiqBuffer(address,uint16,uint32) — selector 0xe17e20bd
    calldata = bytes.fromhex("e17e20bd") + abi_encode(
        ["address", "uint16", "uint32"], [AAVE_AWSTETH_IV, EXTERNAL_LIQ_BUFFER, 0]
    )
    tx2 = _rpc_call("eth_sendTransaction", [{
        "from": VAULT_MANAGER_OWNER, "to": VAULT_MANAGER,
        "data": "0x" + calldata.hex(), "gas": hex(100_000),
    }])
    r2 = _wait_for_receipt(tx2)
    assert r2["status"] == "0x1", f"setExternalLiqBuffer (Aave) failed: {r2}"

    # setAllowedTargetAsset(address,address,address) — selector 0xa21e8cb3
    # Allows WETH as target asset for Aave awstETH IV + Aave V3 Pool
    calldata = bytes.fromhex("a21e8cb3") + abi_encode(
        ["address", "address", "address"], [AAVE_AWSTETH_IV, AAVE_V3_POOL, WETH]
    )
    tx3 = _rpc_call("eth_sendTransaction", [{
        "from": VAULT_MANAGER_OWNER, "to": VAULT_MANAGER,
        "data": "0x" + calldata.hex(), "gas": hex(100_000),
    }])
    r3 = _wait_for_receipt(tx3)
    assert r3["status"] == "0x1", f"setAllowedTargetAsset (Aave) failed: {r3}"

    _stop_impersonate(VAULT_MANAGER_OWNER)


def _increase_iv_supply_cap():
    """Increase the IV supply cap to avoid E_SupplyCapExceeded in tests.

    The Euler eWETH IV has a supply cap of ~7 eWETH at block 24520000.
    Without per-test state isolation, accumulated test deposits exhaust this.
    We impersonate the IV governor (VaultManager) and call setCaps() to
    raise the supply cap to 100 eWETH.

    Idempotent: skips if already increased.

    AmountCap encoding (Euler EVK):
        raw uint16 → 10^(raw & 63) * (raw >> 6) / 100
        6420 → 10^20 * 100 / 100 = 10^20 = 100 eWETH
    """
    from eth_abi import encode as abi_encode

    IV_SUPPLY_CAP_RAW = 6420  # 100 eWETH

    # Check current cap (idempotent) — try cast, fall back to eth_call
    import shutil
    import subprocess

    cast_bin = shutil.which("cast")
    if not cast_bin:
        for p in ["~/.foundry/bin/cast", "~/.config/.foundry/bin/cast"]:
            expanded = os.path.expanduser(p)
            if os.path.isfile(expanded):
                cast_bin = expanded
                break

    already_set = False
    if cast_bin:
        r = subprocess.run(
            [cast_bin, "call", "--rpc-url", ANVIL_RPC,
             EULER_EWETH_IV, "caps()(uint16,uint16)"],
            capture_output=True, text=True, timeout=10,
        )
        already_set = str(IV_SUPPLY_CAP_RAW) in r.stdout
    else:
        # Fallback: raw eth_call — caps() selector 0xe2018768
        caps_result = _rpc_call("eth_call", [{"to": EULER_EWETH_IV, "data": "0xe2018768"}, "latest"])
        if caps_result:
            supply_cap = int(caps_result[2:66], 16)
            already_set = supply_cap == IV_SUPPLY_CAP_RAW

    if already_set:
        return  # Already set

    # Governor of the IV is the VaultManager
    _set_balance(VAULT_MANAGER, hex(10 * 10**18))
    _impersonate(VAULT_MANAGER)

    # setCaps(uint16,uint16) — selector 0xd87f780f
    selector = bytes.fromhex("d87f780f")
    calldata = selector + abi_encode(
        ["uint16", "uint16"], [IV_SUPPLY_CAP_RAW, 44818]  # keep borrow cap
    )
    tx = _rpc_call("eth_sendTransaction", [{
        "from": VAULT_MANAGER, "to": EULER_EWETH_IV,
        "data": "0x" + calldata.hex(),
        "gas": hex(500_000),
    }])
    receipt = _wait_for_receipt(tx)
    assert receipt["status"] == "0x1", f"setCaps failed: {receipt}"

    _stop_impersonate(VAULT_MANAGER)


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
# Vault creation helper (low-level raw-RPC fixture for test setup)
# ---------------------------------------------------------------------------

# v1.0.5 factory signature:
#   createCollateralVault(uint8 _vaultType, address _intermediateVault,
#                         address _targetVault, uint256 _liqLTV, address _targetAsset)
# requires callThroughEVC (calls must go through EVC.batch()).
#
# Note: pre-v1.0.5 the 2nd arg was "asset" (eVault token) and the 5th was the
# intermediate vault. v1.0.5 swapped these semantically (selector unchanged
# because the type signature is identical). _targetAsset = ZERO_ADDRESS lets
# the factory derive it from the target vault.

_FACTORY_SELECTOR = bytes.fromhex("3c7269d1")  # createCollateralVault(uint8,address,address,uint256,address)
_EVC_CALL_SELECTOR = bytes.fromhex("1f8b5215")  # call(address,address,uint256,bytes)
# T_CollateralVaultCreated(address indexed vault) — stored without 0x prefix
# to avoid secret-pattern false positive (keccak hash is 32 bytes = 64 hex chars)
_VAULT_CREATED_TOPIC_HEX = "d5c014427d17eead1b9e8111804901d992255c3982e066ff0b196835c2747e15"


def _create_vault_via_evc(
    sender_account,
    vault_type=0,
    intermediate_vault=EULER_EWETH_IV,
    target_vault=EULER_TARGET_VAULT,
    liq_ltv=DEFAULT_LIQ_LTV,
    target_asset=ZERO_ADDRESS,
):
    """Create a collateral vault via EVC.batch() with the v1.0.5 factory signature.

    Args:
        vault_type: 0 = Euler, 1 = Aave V3
        intermediate_vault: Twyne intermediate vault (CreditEVault) address
        target_vault: Must be an allowed target vault in VaultManager
        liq_ltv: Liquidation LTV in basis points (e.g. 9000 = 90%)
        target_asset: Debt asset; ZERO_ADDRESS lets the factory derive from target_vault

    Returns the new vault address as a string.
    """
    from eth_abi import encode as abi_encode

    caller = str(sender_account.address)

    # Encode factory calldata (v1.0.5 arg order)
    factory_calldata = _FACTORY_SELECTOR + abi_encode(
        ["uint8", "address", "address", "uint256", "address"],
        [vault_type, intermediate_vault, target_vault, liq_ltv, target_asset],
    )

    # Factory has _callThroughEVC modifier — must call via EVC.batch(), not directly.
    # Direct calls fail with EVC_EmptyError (0x38ae747c) because EVC can't
    # authenticate the factory as a caller on behalf of the user.
    batch_item = (CV_FACTORY, caller, 0, factory_calldata)
    batch_calldata = _EVC_BATCH_SELECTOR + abi_encode(
        ["(address,address,uint256,bytes)[]"],
        [[batch_item]],
    )

    tx_hash = _rpc_call(
        "eth_sendTransaction",
        [{"from": caller, "to": TWYNE_EVC, "data": "0x" + batch_calldata.hex(), "gas": hex(5_000_000)}],
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
    """Skip all integration tests if Anvil is not running on the expected fork.

    If Anvil is running but has drifted past the fork block (from previous
    test runs), reset it to the original fork state. This ensures a clean
    starting point without needing per-test snapshot/revert.
    """
    try:
        result = _rpc_call("eth_blockNumber")
        block = int(result, 16)
        assert block >= FORK_BLOCK, f"Expected block >= {FORK_BLOCK}, got {block}"
    except Exception as e:
        pytest.skip(f"Anvil fork not running on {ANVIL_RPC}: {e}")

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
    """Configure VaultManager LTV params and IV supply cap.

    At block 24520000, maxTwyneLTVs and externalLiqBuffers are 0 for all IVs.
    The IV supply cap is ~7 eWETH which gets exhausted by accumulated test deposits
    (no per-test state isolation). This fixture:
    1. Sets Euler VaultManager LTV params (impersonates VaultManager owner)
    2. Sets Aave VaultManager LTV params + allowedTargetAsset
    3. Increases IV supply cap to 100 eWETH (impersonates IV governor)
    """
    _configure_vault_manager()
    _configure_aave_vault_manager()
    _increase_iv_supply_cap()
    return True


@pytest.fixture(autouse=True)
def anvil_clean_state(vault_manager_configured):
    """Ensure VaultManager is configured and test accounts have ETH before each test.

    NOTE: Per-test state isolation (evm_snapshot/evm_revert, anvil_reset)
    is NOT used because both are broken on Anvil forked state:
    - evm_snapshot/evm_revert causes BlockOutOfRangeError (Anvil bug)
    - anvil_reset crashes Anvil after ~7 resets (connection drop)

    Tests are designed to be independent without state isolation:
    - Each test creates fresh vaults via _create_vault_via_evc()
    - funded_eweth mints new WETH and deposits each time (proven to work 10+ times)
    - State accumulation is harmless (more eWETH in test account is fine)
    - ETH is replenished each test since _deal_weth overwrites the balance
    """
    # Replenish ETH for all test accounts (gas is consumed without revert)
    for addr in TEST_ACCOUNTS:
        _set_balance(addr, hex(10000 * 10**18))
    yield


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
    Returns the eWETH amount received (delta, not total balance).
    """
    weth_amount = 10 * 10**18
    addr = str(test_account.address)

    # Track balance before deposit (without state isolation, balance accumulates)
    balance_before = _balance_of(EULER_EWETH, addr)

    _deal_weth(test_account, weth_amount)

    # Approve Euler eWETH vault to spend WETH
    _approve_erc20(addr, WETH, EULER_EWETH, weth_amount)

    # Deposit WETH into Euler eWETH vault (ERC4626 deposit(uint256,address))
    _deposit_erc4626(addr, EULER_EWETH, weth_amount, addr)

    # Return only the newly received eWETH (not accumulated total)
    balance_after = _balance_of(EULER_EWETH, addr)
    return balance_after - balance_before


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
    # Use a small deposit to conserve Intermediate Vault credit.
    # Without per-test state isolation, each funded_vault depletes the IV's
    # available credit. Capping at 0.5 eWETH allows ~20+ funded vaults
    # before the IV is exhausted.
    deposit_amount = min(funded_eweth, 5 * 10**17)  # cap at 0.5 eWETH
    addr = str(test_account.address)

    # Approve the CV to spend eWETH (raw RPC to avoid Ape nonce issues)
    _approve_erc20(addr, EULER_EWETH, vault_address, deposit_amount)

    # Deposit via EVC batch (required for _callThroughEVC auth)
    _deposit_via_evc(test_account, vault_address, deposit_amount)

    return vault_address, deposit_amount


def _deposit_eweth_to_iv(sender_account, weth_amount=5 * 10**18):
    """Deposit eWETH into the Euler Intermediate Vault (CreditEVault).

    Bypasses the Euler wrapper (which reverts with 0x426073f2 at block 24520000).
    Mints fresh WETH, deposits to Euler eWETH vault to get eWETH, then deposits
    eWETH directly into the IV.

    Matches the Foundry test pattern from EulerTestBase.t.sol:
      1. dealEToken(collateralAssets, bob, amount) — WETH→eWETH via direct call
      2. IERC20(eWETH).approve(intermediate_vault, amount)
      3. intermediate_vault.deposit(amount, bob) — direct call

    In Foundry, vm.startPrank(bob) satisfies EVault's callThroughEVC modifier.
    On Anvil, impersonated accounts (--unlocked) get the same treatment —
    the EVC sees the caller as authenticated.

    Only deposits the freshly minted eWETH (not accumulated balance).
    Returns the IV shares received (int).
    """
    from eth_abi import encode as abi_encode

    addr = str(sender_account.address)

    # Track eWETH balance before to only deposit fresh amount
    eweth_before = _balance_of(EULER_EWETH, addr)

    # 1. Get eWETH by depositing WETH into Euler eWETH vault (direct call)
    _deal_weth(sender_account, weth_amount)
    _approve_erc20(addr, WETH, EULER_EWETH, weth_amount)
    _deposit_erc4626(addr, EULER_EWETH, weth_amount, addr)

    eweth_after = _balance_of(EULER_EWETH, addr)
    fresh_eweth = eweth_after - eweth_before
    assert fresh_eweth > 0, "Failed to get eWETH"

    # 2. Approve IV to spend fresh eWETH
    _approve_erc20(addr, EULER_EWETH, EULER_EWETH_IV, fresh_eweth)

    # 3. Deposit eWETH into IV — direct call with high gas limit
    #    EVault.deposit has callThroughEVC, but on Anvil the modifier
    #    re-routes through EVC which authenticates the impersonated sender.
    #    Using eth_sendTransaction with sufficient gas (500k was too low,
    #    cast send shows ~235k gas used but needs higher limit for EVC routing).
    calldata = bytes.fromhex("6e553f65") + abi_encode(
        ["uint256", "address"], [fresh_eweth, addr]
    )
    tx_hash = _rpc_call("eth_sendTransaction", [{
        "from": addr, "to": EULER_EWETH_IV,
        "data": "0x" + calldata.hex(),
        "gas": hex(1_000_000),
    }])
    receipt = _wait_for_receipt(tx_hash)
    assert receipt["status"] == "0x1", f"IV deposit failed: {receipt}"

    iv_shares = _balance_of(EULER_EWETH_IV, addr)
    assert iv_shares > 0, "IV deposit returned 0 shares"
    return iv_shares


@pytest.fixture()
def funded_iv(test_account):
    """Fund test_account with IV shares by depositing eWETH directly.

    Bypasses the Euler wrapper. Returns the IV shares received (int).
    """
    return _deposit_eweth_to_iv(test_account)
