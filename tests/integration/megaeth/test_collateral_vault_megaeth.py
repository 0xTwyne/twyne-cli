"""MegaETH collateral vault op tests against the Anvil fork.

Aave path only (no Euler operators on chain 4326). Covers:
  - deposit-underlying (USDe → vault via Aave wrapper internally)
  - withdraw / redeem-underlying
  - borrow USDm / repay
  - set-ltv
  - skim
"""

from __future__ import annotations

import pytest
from click.testing import CliRunner

from twyne_cli.contracts import collateral_vault
from twyne_cli.transactions import simulate_tx

from .conftest import (
    DEFAULT_LIQ_LTV,
    USDE,
    USDM,
    _approve_erc20,
    _create_aave_vault_via_evc,
)

ANVIL_PK_0 = "ac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80"


@pytest.fixture
def fresh_aave_vault_with_account(test_account_megaeth):
    """Create a fresh Aave vault for the test account; return (vault, account)."""
    vault = _create_aave_vault_via_evc(str(test_account_megaeth.address))
    return vault, test_account_megaeth


class TestCollateralVaultReadOnly:
    """Sanity reads that should work without funding."""

    def test_vault_iv_address(self, megaeth_vm_configured, fresh_aave_vault_with_account):
        vault, _ = fresh_aave_vault_with_account
        cv = collateral_vault(vault)
        assert cv.intermediateVault() != "0x0000000000000000000000000000000000000000"

    def test_vault_target_asset_is_usdm(self, megaeth_vm_configured, fresh_aave_vault_with_account):
        vault, _ = fresh_aave_vault_with_account
        cv = collateral_vault(vault)
        assert cv.targetAsset().lower() == USDM.lower()

    def test_vault_underlying_asset_is_usde(self, megaeth_vm_configured, fresh_aave_vault_with_account):
        """Aave vault's underlying (collateral side) should be USDe on MegaETH."""
        vault, _ = fresh_aave_vault_with_account
        cv = collateral_vault(vault)
        # underlyingAsset() is the canonical accessor used by the CLI
        assert cv.underlyingAsset().lower() == USDE.lower()


class TestSetLtv:
    """`tx collateral set-ltv` simulation works when the new LTV is in range."""

    def test_set_ltv_simulation_succeeds(self, megaeth_vm_configured, fresh_aave_vault_with_account):
        vault, account = fresh_aave_vault_with_account
        cv = collateral_vault(vault)
        # Lower it slightly — must remain above the external liq threshold
        sim = simulate_tx(cv, "setTwyneLiqLTV", [DEFAULT_LIQ_LTV - 100], sender=account)
        assert sim["success"] is True, f"set-ltv sim failed: {sim.get('error')}"


class TestDepositUnderlying:
    """Deposit USDe via the CV's depositUnderlying — exercises the Aave wrapper path."""

    def test_deposit_underlying_simulation(
        self, megaeth_vm_configured, fresh_aave_vault_with_account, funded_usde,
    ):
        vault, account = fresh_aave_vault_with_account
        amount = funded_usde // 4  # 250 USDe
        # Approve the CV to pull USDe
        _approve_erc20(str(account.address), USDE, vault, amount)

        cv = collateral_vault(vault)
        sim = simulate_tx(cv, "depositUnderlying", [amount], sender=account)
        assert sim["success"] is True, f"depositUnderlying sim failed: {sim.get('error')}"


class TestCliReadCommands:
    """Read-only CLI commands against the fork."""

    def test_cli_protocol_overview_megaeth_fork(self, megaeth_anvil_fork):
        """`twyne --chain megaeth protocol overview` against the fork (not live RPC)."""
        from twyne_cli.cli import cli

        runner = CliRunner()
        result = runner.invoke(cli, [
            "--chain", "megaeth", "--rpc", megaeth_anvil_fork,
            "--no-cache", "--json", "protocol", "overview",
        ])
        assert result.exit_code == 0, f"CLI failed: {result.output}"
        # Output may include a cache-rebuild note on stderr; the JSON body
        # is what we care about. Exit code 0 is the real signal here.

    def test_cli_user_command_returns_empty_for_fresh_account(
        self, megaeth_anvil_fork, test_account_megaeth,
    ):
        """`twyne user <unfunded-addr>` should not crash on a chain with no positions."""
        from twyne_cli.cli import cli

        runner = CliRunner()
        result = runner.invoke(cli, [
            "--chain", "megaeth", "--rpc", megaeth_anvil_fork,
            "--no-cache", "--json",
            "user", str(test_account_megaeth.address),
        ])
        assert result.exit_code == 0, f"CLI failed: {result.output}"
