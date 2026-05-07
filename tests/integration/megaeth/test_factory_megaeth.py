"""MegaETH factory integration tests against Anvil fork on chain 4326.

Aave-only — MegaETH has no Euler operator deployment. Verifies that
`createCollateralVault` works end-to-end with the v1.0.5 IV-passthrough
arg order and that the CLI's `tx factory create-vault` / `open-position`
commands wire through cleanly when the user supplies the IV address directly.
"""

from __future__ import annotations

import pytest
from click.testing import CliRunner

from twyne_cli.contracts import collateral_vault, collateral_vault_factory
from twyne_cli.transactions import simulate_through_evc, simulate_tx

from .conftest import (
    AAVE_INTERMEDIATE_VAULT,
    AAVE_POOL,
    ANVIL_RPC_MEGA,
    DEFAULT_LIQ_LTV,
    USDE,
    USDM,
    ZERO_ADDRESS,
    _create_aave_vault_via_evc,
)

# Anvil's first deterministic test account private key
ANVIL_PK_0 = "ac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80"


class TestFactoryAaveCreateVault:
    """Direct factory tests via Ape contract calls + raw RPC fallback."""

    def test_simulate_aave_create(self, megaeth_vm_configured, test_account_megaeth):
        """`createCollateralVault` Aave simulation succeeds with v1.0.5 args."""
        factory = collateral_vault_factory()
        sim = simulate_tx(
            factory,
            "createCollateralVault",
            [1, AAVE_INTERMEDIATE_VAULT, AAVE_POOL, DEFAULT_LIQ_LTV, USDM],
            sender=test_account_megaeth,
        )
        assert sim["success"] is True, f"simulation failed: {sim.get('error')}"
        assert sim["result"]  # predicted vault address

    def test_simulate_aave_create_through_evc(self, megaeth_vm_configured, test_account_megaeth):
        """EVC-routed simulation also succeeds (this is the path the CLI takes)."""
        factory = collateral_vault_factory()
        sim = simulate_through_evc(
            factory,
            "createCollateralVault",
            [1, AAVE_INTERMEDIATE_VAULT, AAVE_POOL, DEFAULT_LIQ_LTV, USDM],
            sender=test_account_megaeth,
        )
        assert sim["success"] is True, f"EVC simulation failed: {sim.get('error')}"
        assert sim["result"]

    def test_create_aave_vault_via_evc(self, megaeth_vm_configured, test_account_megaeth):
        """End-to-end: EVC.batch executes the factory call and returns a vault."""
        vault_addr = _create_aave_vault_via_evc(str(test_account_megaeth.address))
        assert vault_addr != ZERO_ADDRESS
        assert len(vault_addr) == 42

    def test_created_vault_has_iv_set(self, megaeth_vm_configured, test_account_megaeth):
        """The new vault's intermediateVault() returns the registered IV."""
        vault_addr = _create_aave_vault_via_evc(str(test_account_megaeth.address))
        cv = collateral_vault(vault_addr)
        assert cv.intermediateVault().lower() == AAVE_INTERMEDIATE_VAULT.lower()

    def test_created_vault_borrower_is_sender(self, megaeth_vm_configured, test_account_megaeth):
        """The new vault's borrower() returns the EVC msg.sender."""
        sender = str(test_account_megaeth.address).lower()
        vault_addr = _create_aave_vault_via_evc(sender)
        cv = collateral_vault(vault_addr)
        assert cv.borrower().lower() == sender

    def test_created_vault_has_target_asset(self, megaeth_vm_configured, test_account_megaeth):
        """Aave vault stores the explicit targetAsset (USDm)."""
        vault_addr = _create_aave_vault_via_evc(str(test_account_megaeth.address))
        cv = collateral_vault(vault_addr)
        assert cv.targetAsset().lower() == USDM.lower()


class TestFactoryAaveErrors:
    """Negative paths — wrong inputs should fail simulation, not crash."""

    def test_zero_target_asset_fails(self, megaeth_vm_configured, test_account_megaeth):
        """Aave path with target_asset = 0x0 reverts (no allowed mapping)."""
        factory = collateral_vault_factory()
        sim = simulate_through_evc(
            factory,
            "createCollateralVault",
            [1, AAVE_INTERMEDIATE_VAULT, AAVE_POOL, DEFAULT_LIQ_LTV, ZERO_ADDRESS],
            sender=test_account_megaeth,
        )
        assert sim["success"] is False
        assert sim.get("error")

    def test_bogus_iv_fails(self, megaeth_vm_configured, test_account_megaeth):
        """Passing a non-IV address (USDe token) yields IntermediateVaultNotSet."""
        factory = collateral_vault_factory()
        sim = simulate_through_evc(
            factory,
            "createCollateralVault",
            [1, USDE, AAVE_POOL, DEFAULT_LIQ_LTV, USDM],
            sender=test_account_megaeth,
        )
        assert sim["success"] is False
        assert sim.get("error")


class TestCliCreateVaultMegaETH:
    """Exercise `twyne --chain megaeth tx factory create-vault` against the fork."""

    def test_cli_dry_run_aave(self, megaeth_vm_configured, test_account_megaeth):
        from twyne_cli.cli import cli

        runner = CliRunner()
        result = runner.invoke(cli, [
            "--chain", "megaeth",
            "--rpc", ANVIL_RPC_MEGA,
            "tx", "factory", "create-vault",
            AAVE_INTERMEDIATE_VAULT, AAVE_POOL,
            "--vault-type", "1",
            "--ltv", str(DEFAULT_LIQ_LTV),
            "--target-asset", USDM,
            "--dry-run",
            "--private-key", ANVIL_PK_0,
        ])
        assert result.exit_code == 0, f"CLI failed: {result.output}\n{result.exception}"
        assert "Dry run" in result.output
        assert "Predicted vault address" in result.output

    def test_cli_execution_aave(self, megaeth_vm_configured, test_account_megaeth):
        from twyne_cli.cli import cli

        runner = CliRunner()
        result = runner.invoke(cli, [
            "--chain", "megaeth",
            "--rpc", ANVIL_RPC_MEGA,
            "tx", "factory", "create-vault",
            AAVE_INTERMEDIATE_VAULT, AAVE_POOL,
            "--vault-type", "1",
            "--ltv", str(DEFAULT_LIQ_LTV),
            "--target-asset", USDM,
            "--yes",
            "--private-key", ANVIL_PK_0,
        ])
        assert result.exit_code == 0, f"CLI failed: {result.output}\n{result.exception}"
        assert "New vault address:" in result.output or "Status" in result.output

    def test_cli_rejects_protocol_euler(self, megaeth_vm_configured):
        """Capability gating: --protocol euler at credit deposit fails fast on MegaETH."""
        from twyne_cli.cli import cli

        runner = CliRunner()
        result = runner.invoke(cli, [
            "--chain", "megaeth",
            "--rpc", ANVIL_RPC_MEGA,
            "tx", "credit", "deposit",
            AAVE_INTERMEDIATE_VAULT, "1",
            "--protocol", "euler",
            "--private-key", ANVIL_PK_0,
        ])
        assert result.exit_code != 0
        assert "Euler protocol is not available" in result.output
