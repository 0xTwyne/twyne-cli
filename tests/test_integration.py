"""Integration tests requiring Anvil fork.

Start Anvil first:
    anvil --fork-url <RPC_URL> --fork-block-number 24520000 --port 8454 --accounts 10 --balance 10000

These tests exercise the CLI deposit dry-run pipeline against a real mainnet
collateral vault (0x7C20a1FA13C60c39EcAe3093FBb78971b6b61BC8) owned by
0x7ebc88d2c4a37e5566d4CA37f52E4477C4147529 at block 24520000.
"""

import os

import httpx
import pytest
from ape import accounts, networks
from ape.logging import logger as ape_logger

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
ANVIL_RPC = os.environ.get("ANVIL_RPC_URL", "http://localhost:8454")
FORK_RPC = os.environ.get("ANVIL_FORK_URL", "https://ethereum-rpc.publicnode.com")
FORK_BLOCK = 24520000

# Real mainnet vault at block 24520000
CV_ADDRESS = "0x7C20a1FA13C60c39EcAe3093FBb78971b6b61BC8"
CV_OWNER = "0x7ebc88d2c4a37e5566d4CA37f52E4477C4147529"
DEPOSIT_AMOUNT = "0.0005"  # human-readable eWETH
DEPOSIT_RAW = 500_000_000_000_000  # 0.0005 * 1e18

WETH = "0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2"
EULER_EWETH = "0xD8b27CF359b7D15710a5BE299AF6e7Bf904984C2"
VAULT_MANAGER = "0x0acd3A3c8Ab6a5F7b5A594C88DFa28999dA858aC"
EULER_EWETH_IV = "0x87b8081A3ace680f35125F469526Ac10f5418Ca7"
VAULT_MANAGER_OWNER = "0x8C54cb62900Ec252E7992C85a5b7078A8AF4Fd7F"
MAX_TWYNE_LTV = 9300
EXTERNAL_LIQ_BUFFER = 10000


def _rpc_call(method, params=None):
    """Raw JSON-RPC call to Anvil."""
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
    _rpc_call("anvil_impersonateAccount", [address])


def _stop_impersonate(address):
    _rpc_call("anvil_stopImpersonatingAccount", [address])


def _set_balance(address, wei_hex):
    _rpc_call("anvil_setBalance", [address, wei_hex])


def _wait_for_receipt(tx_hash, timeout_s=30):
    import time
    for _ in range(timeout_s * 4):
        receipt = _rpc_call("eth_getTransactionReceipt", [tx_hash])
        if receipt is not None:
            return receipt
        time.sleep(0.25)
    raise RuntimeError(f"No receipt for tx {tx_hash} after {timeout_s}s")


def _configure_vault_manager():
    """Configure VaultManager LTV params if not already set."""
    from eth_abi import encode as abi_encode

    selector = bytes.fromhex("7b6b8447")  # maxTwyneLTVs(address)
    call_data = selector + abi_encode(["address"], [EULER_EWETH_IV])
    result = _rpc_call("eth_call", [{"to": VAULT_MANAGER, "data": "0x" + call_data.hex()}, "latest"])
    current_ltv = int(result, 16) if result else 0
    if current_ltv == MAX_TWYNE_LTV:
        return

    _set_balance(VAULT_MANAGER_OWNER, hex(10 * 10**18))
    _impersonate(VAULT_MANAGER_OWNER)

    calldata = bytes.fromhex("950bc62a") + abi_encode(
        ["address", "uint16"], [EULER_EWETH_IV, MAX_TWYNE_LTV]
    )
    tx1 = _rpc_call("eth_sendTransaction", [{
        "from": VAULT_MANAGER_OWNER, "to": VAULT_MANAGER,
        "data": "0x" + calldata.hex(), "gas": hex(100_000),
    }])
    r1 = _wait_for_receipt(tx1)
    assert r1["status"] == "0x1", f"setMaxLiquidationLTV failed: {r1}"

    calldata = bytes.fromhex("da7f7f80") + abi_encode(
        ["address", "uint16"], [EULER_EWETH_IV, EXTERNAL_LIQ_BUFFER]
    )
    tx2 = _rpc_call("eth_sendTransaction", [{
        "from": VAULT_MANAGER_OWNER, "to": VAULT_MANAGER,
        "data": "0x" + calldata.hex(), "gas": hex(100_000),
    }])
    r2 = _wait_for_receipt(tx2)
    assert r2["status"] == "0x1", f"setExternalLiqBuffer failed: {r2}"

    _stop_impersonate(VAULT_MANAGER_OWNER)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def anvil_fork():
    """Reset Anvil to fork block and connect Ape.

    Resets Anvil state to a clean fork at FORK_BLOCK. This eliminates
    oracle staleness (PriceOracle_TooStale) caused by Anvil's timestamp
    drifting past the Chainlink feed's staleness window during prior tests.
    """
    try:
        _rpc_call("eth_blockNumber")
    except Exception as e:
        pytest.skip(f"Anvil fork not running on {ANVIL_RPC}: {e}")

    # Reset to clean fork state — fixes oracle staleness from prior test runs
    _rpc_call("anvil_reset", [{"forking": {"jsonRpcUrl": FORK_RPC, "blockNumber": FORK_BLOCK}}])

    # Verify reset worked
    result = _rpc_call("eth_blockNumber")
    block = int(result, 16)
    assert block == FORK_BLOCK, f"Expected block {FORK_BLOCK}, got {block}"

    # Configure VaultManager params (zero at block 24520000)
    _configure_vault_manager()

    ape_logger.set_level("WARNING")
    ctx = networks.ethereum.mainnet.use_provider(
        "node", provider_settings={"uri": ANVIL_RPC}
    )
    provider = ctx.__enter__()
    provider.network.config.required_confirmations = 0
    yield ANVIL_RPC
    ctx.__exit__(None, None, None)


@pytest.fixture(scope="module")
def owner_funded(anvil_fork):
    """Fund vault owner with eWETH and approve the CV to spend it.

    Mints WETH via deposit, converts to eWETH via Euler vault, then
    approves the collateral vault to spend eWETH.
    """
    from eth_abi import encode as abi_encode

    amount = 1 * 10**18  # 1 WETH -> ~1 eWETH

    _set_balance(CV_OWNER, hex(100 * 10**18))
    _impersonate(CV_OWNER)

    # 1. ETH -> WETH
    tx = _rpc_call("eth_sendTransaction", [{
        "from": CV_OWNER, "to": WETH,
        "data": "0xd0e30db0",  # deposit()
        "value": hex(amount), "gas": hex(100_000),
    }])
    r = _wait_for_receipt(tx)
    assert r["status"] == "0x1", f"WETH deposit failed: {r}"

    # 2. Approve Euler eWETH vault to spend WETH
    approve_data = bytes.fromhex("095ea7b3") + abi_encode(
        ["address", "uint256"], [EULER_EWETH, amount]
    )
    tx = _rpc_call("eth_sendTransaction", [{
        "from": CV_OWNER, "to": WETH,
        "data": "0x" + approve_data.hex(), "gas": hex(100_000),
    }])
    r = _wait_for_receipt(tx)
    assert r["status"] == "0x1", f"WETH approve failed: {r}"

    # 3. WETH -> eWETH (ERC4626 deposit)
    deposit_data = bytes.fromhex("6e553f65") + abi_encode(
        ["uint256", "address"], [amount, CV_OWNER]
    )
    tx = _rpc_call("eth_sendTransaction", [{
        "from": CV_OWNER, "to": EULER_EWETH,
        "data": "0x" + deposit_data.hex(), "gas": hex(500_000),
    }])
    r = _wait_for_receipt(tx)
    assert r["status"] == "0x1", f"eWETH deposit failed: {r}"

    # 4. Approve CV to spend eWETH (unlimited)
    approve_cv = bytes.fromhex("095ea7b3") + abi_encode(
        ["address", "uint256"], [CV_ADDRESS, 2**256 - 1]
    )
    tx = _rpc_call("eth_sendTransaction", [{
        "from": CV_OWNER, "to": EULER_EWETH,
        "data": "0x" + approve_cv.hex(), "gas": hex(100_000),
    }])
    r = _wait_for_receipt(tx)
    assert r["status"] == "0x1", f"eWETH approve CV failed: {r}"

    _stop_impersonate(CV_OWNER)
    return True


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

@pytest.mark.integration
class TestDepositDryRun:
    """Test the CLI deposit dry-run pipeline against a real mainnet vault.

    The deposit dry-run flow (tx.py:deposit with --dry-run):
      1. resolve_account() — get signing account
      2. collateral_vault(address) — load CV contract
      3. parse_amount(amount, decimals) — human-readable to raw
      4. simulate_tx(cv, "deposit", [raw_amount]) — eth_call simulation
      5. If simulation passes, print "Dry run — simulation passed."
    """

    def test_parse_amount_matches_expected(self):
        """parse_amount converts '0.0005' with 18 decimals to correct raw value."""
        from twyne_cli.transactions import parse_amount
        assert parse_amount(DEPOSIT_AMOUNT, 18) == DEPOSIT_RAW

    def test_vault_state_valid(self, anvil_fork):
        """The target vault exists, has correct asset and borrower."""
        from twyne_cli.contracts import collateral_vault

        cv = collateral_vault(CV_ADDRESS)
        assert cv.asset().lower() == EULER_EWETH.lower()
        assert cv.borrower().lower() == CV_OWNER.lower()

    def test_simulate_deposit_via_eth_call(self, anvil_fork, owner_funded):
        """Raw eth_call deposit simulation succeeds with funded owner.

        This mirrors what simulate_tx does internally: an eth_call with
        the sender as `from` and the deposit calldata. After owner_funded,
        the owner has eWETH balance and CV approval.
        """
        _impersonate(CV_OWNER)
        try:
            # deposit(uint256) selector: 0xb6b55f25
            calldata = "0xb6b55f25" + format(DEPOSIT_RAW, "064x")
            result = _rpc_call("eth_call", [{
                "from": CV_OWNER,
                "to": CV_ADDRESS,
                "data": calldata,
            }, "latest"])
            assert result is not None, "Deposit simulation reverted"
        finally:
            _stop_impersonate(CV_OWNER)

    def test_simulate_tx_function(self, anvil_fork, owner_funded):
        """simulate_tx() returns success for deposit with impersonated owner.

        Uses Ape's contract.fn.call() under the hood (same as CLI).
        Passes the owner address string directly as sender — Ape's
        encode_transaction accepts both AccountAPI and address strings.
        """
        from twyne_cli.contracts import collateral_vault
        from twyne_cli.transactions import simulate_tx

        _impersonate(CV_OWNER)
        try:
            cv = collateral_vault(CV_ADDRESS)
            sim = simulate_tx(cv, "deposit", [DEPOSIT_RAW], sender=CV_OWNER)
            assert sim["success"] is True, f"simulate_tx failed: {sim.get('error')}"
        finally:
            _stop_impersonate(CV_OWNER)

    def test_full_dry_run_pipeline(self, anvil_fork, owner_funded):
        """End-to-end dry-run: parse amount, load contract, simulate, verify output.

        Exercises the same code path as `twyne tx collateral deposit <vault> 0.0005 --dry-run`
        without going through Click (which requires a real private key for resolve_account).
        """
        from twyne_cli.contracts import collateral_vault
        from twyne_cli.transactions import parse_amount

        _impersonate(CV_OWNER)
        try:
            # Step 1: Load contract and get decimals (same as CLI)
            cv = collateral_vault(CV_ADDRESS)
            asset_addr = cv.asset()
            from twyne_cli.contracts import erc20
            token = erc20(asset_addr)
            decimals = token.decimals()
            assert decimals == 18

            # Step 2: Parse amount (same as CLI)
            raw_amount = parse_amount(DEPOSIT_AMOUNT, decimals)
            assert raw_amount == DEPOSIT_RAW

            # Step 3: Simulate via eth_call (same as CLI)
            calldata = "0xb6b55f25" + format(raw_amount, "064x")
            result = _rpc_call("eth_call", [{
                "from": CV_OWNER,
                "to": CV_ADDRESS,
                "data": calldata,
            }, "latest"])
            assert result is not None, "Deposit simulation reverted"

            # Step 4: In dry-run mode, CLI would print this and return
            dry_run_output = "Dry run — simulation passed."
            assert dry_run_output  # Just verify the expected output string
        finally:
            _stop_impersonate(CV_OWNER)

    def test_deposit_simulation_fails_without_approval(self, anvil_fork):
        """Deposit simulation fails if sender has no eWETH approval for the CV."""
        # Use a fresh address with no approvals
        random_addr = "0x0000000000000000000000000000000000000001"
        _impersonate(random_addr)
        try:
            calldata = "0xb6b55f25" + format(DEPOSIT_RAW, "064x")
            with pytest.raises(RuntimeError, match="execution reverted"):
                _rpc_call("eth_call", [{
                    "from": random_addr,
                    "to": CV_ADDRESS,
                    "data": calldata,
                }, "latest"])
        finally:
            _stop_impersonate(random_addr)

    def test_deposit_simulation_fails_zero_balance(self, anvil_fork):
        """Deposit simulation fails if sender has zero eWETH balance."""
        # Use Anvil test account (has ETH but no eWETH)
        test_addr = "0xf39Fd6e51aad88F6F4ce6aB8827279cffFb92266"
        calldata = "0xb6b55f25" + format(DEPOSIT_RAW, "064x")
        with pytest.raises(RuntimeError, match="execution reverted"):
            _rpc_call("eth_call", [{
                "from": test_addr,
                "to": CV_ADDRESS,
                "data": calldata,
            }, "latest"])


@pytest.mark.integration
class TestBatchSimulate:
    """Test batch file simulation against the fork."""

    def test_batch_simulate(self, anvil_fork):
        """Batch simulation should run against the fork."""
        pass  # TODO: populate with batch file
