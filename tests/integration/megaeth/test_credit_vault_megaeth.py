"""MegaETH intermediate vault (CLP) op tests against the Anvil fork.

The MegaETH IV (`ewaMegUSDe-1`) holds aTokenWrapper shares wrapping aUSDe.
Users deposit USDe via `aaveV3Wrapper.depositUnderlyingToIntermediateVault`,
which routes USDe → Aave (gets aUSDe) → aTokenWrapper (gets shares) → IV.
"""

from __future__ import annotations

from click.testing import CliRunner
from eth_abi import encode as abi_encode

from twyne_cli.contracts import aave_wrapper, credit_vault, erc20
from twyne_cli.transactions import simulate_tx

from .conftest import (
    AAVE_INTERMEDIATE_VAULT,
    AAVE_V3_WRAPPER,
    ANVIL_RPC_MEGA,
    USDE,
    _approve_erc20,
    _rpc_call,
    _wait_for_receipt,
)

ANVIL_PK_0 = "ac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80"


class TestCreditVaultReads:
    """Read-only IV state checks."""

    def test_iv_asset_is_atoken_wrapper(self, megaeth_vm_configured):
        """IV.asset() returns the aTokenWrapper (wraps aUSDe)."""
        from .conftest import ATOKEN_WRAPPER
        iv = credit_vault(AAVE_INTERMEDIATE_VAULT)
        assert iv.asset().lower() == ATOKEN_WRAPPER.lower()

    def test_iv_total_assets_readable(self, megaeth_vm_configured):
        iv = credit_vault(AAVE_INTERMEDIATE_VAULT)
        # Should be a uint, may be 0 on a fresh fork
        total = iv.totalAssets()
        assert isinstance(total, int)
        assert total >= 0


class TestDepositUnderlyingToIv:
    """Deposit USDe → IV via the aaveV3Wrapper helper."""

    def test_simulate_deposit_underlying(
        self, megaeth_vm_configured, test_account_megaeth, funded_usde,
    ):
        """Simulating wrapper.depositUnderlyingToIntermediateVault succeeds."""
        amount = funded_usde // 10  # 100 USDe
        # Approve wrapper to pull USDe
        _approve_erc20(str(test_account_megaeth.address), USDE, AAVE_V3_WRAPPER, amount)

        wrapper = aave_wrapper()
        sim = simulate_tx(
            wrapper,
            "depositUnderlyingToIntermediateVault",
            [AAVE_INTERMEDIATE_VAULT, amount],
            sender=test_account_megaeth,
        )
        assert sim["success"] is True, f"deposit sim failed: {sim.get('error')}"

    def test_execute_deposit_via_raw_rpc(
        self, megaeth_vm_configured, test_account_megaeth, funded_usde,
    ):
        """Execute the deposit and verify the test account ends with IV shares."""
        amount = funded_usde // 10
        sender = str(test_account_megaeth.address)

        # Approve
        _approve_erc20(sender, USDE, AAVE_V3_WRAPPER, amount)

        # Call wrapper.depositUnderlyingToIntermediateVault(IV, amount)
        # selector = keccak("depositUnderlyingToIntermediateVault(address,uint256)")[:4]
        from eth_utils import keccak
        sel = keccak(text="depositUnderlyingToIntermediateVault(address,uint256)")[:4]
        data = sel + abi_encode(["address", "uint256"], [AAVE_INTERMEDIATE_VAULT, amount])

        tx = _rpc_call("eth_sendTransaction", [{
            "from": sender, "to": AAVE_V3_WRAPPER,
            "data": "0x" + data.hex(),
            "gas": hex(2_000_000),
        }])
        receipt = _wait_for_receipt(tx)
        assert receipt["status"] == "0x1", f"deposit reverted: {receipt}"

        # IV should now hold non-zero shares for the sender
        iv_token = erc20(AAVE_INTERMEDIATE_VAULT)
        balance = iv_token.balanceOf(sender)
        assert balance > 0, "expected non-zero IV shares after deposit"


class TestCliCreditDeposit:
    """`twyne tx credit deposit` end-to-end on MegaETH (Aave path only)."""

    def test_cli_credit_deposit_dry_run_aave(
        self, megaeth_vm_configured, test_account_megaeth, funded_usde,
    ):
        """`--dry-run` simulation reaches the simulate step.

        Pre-approves USDe via raw RPC so the auto-approval prompt isn't hit
        (CliRunner with no input would otherwise reject it). `--skip-approval`
        is also passed to short-circuit the prompt entirely.
        """
        from twyne_cli.cli import cli

        amount = funded_usde // 10
        sender = str(test_account_megaeth.address)
        _approve_erc20(sender, USDE, AAVE_V3_WRAPPER, amount)

        runner = CliRunner()
        result = runner.invoke(cli, [
            "--chain", "megaeth", "--rpc", ANVIL_RPC_MEGA,
            "tx", "credit", "deposit",
            AAVE_INTERMEDIATE_VAULT, str(amount),
            "--protocol", "aave",
            "--raw",
            "--dry-run",
            "--skip-approval",
            "--private-key", ANVIL_PK_0,
        ])
        assert result.exit_code == 0, f"CLI failed: {result.output}\n{result.exception}"
        assert "Dry run" in result.output
