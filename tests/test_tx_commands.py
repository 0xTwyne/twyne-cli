"""Tests for tx command group structure and factory create-vault command."""

from unittest.mock import MagicMock, patch

from click.testing import CliRunner

# --------------------------------------------------------------------------- #
# Group existence tests
# --------------------------------------------------------------------------- #


def test_tx_group_exists():
    """The 'tx' command group should be registered on the CLI."""
    from twyne_cli.cli import cli
    runner = CliRunner()
    result = runner.invoke(cli, ["tx", "--help"])
    assert result.exit_code == 0
    assert "collateral" in result.output


def test_tx_collateral_group_exists():
    """The 'tx collateral' subgroup should list its commands."""
    from twyne_cli.cli import cli
    runner = CliRunner()
    result = runner.invoke(cli, ["tx", "collateral", "--help"])
    assert result.exit_code == 0
    assert "deposit" in result.output
    assert "borrow" in result.output
    assert "withdraw" in result.output
    assert "repay" in result.output
    assert "set-ltv" in result.output
    assert "liquidate" in result.output
    assert "skim" in result.output


def test_tx_credit_group_exists():
    """The 'tx credit' subgroup should list its commands."""
    from twyne_cli.cli import cli
    runner = CliRunner()
    result = runner.invoke(cli, ["tx", "credit", "--help"])
    assert result.exit_code == 0
    assert "deposit" in result.output
    assert "deposit-underlying" in result.output
    assert "deposit-atokens" in result.output
    assert "withdraw" in result.output
    assert "redeem" in result.output


def test_tx_operators_group_exists():
    """The 'tx operators' subgroup should list its commands."""
    from twyne_cli.cli import cli
    runner = CliRunner()
    result = runner.invoke(cli, ["tx", "operators", "--help"])
    assert result.exit_code == 0
    assert "leverage" in result.output
    assert "deleverage" in result.output
    assert "teleport" in result.output


def test_tx_factory_group_exists():
    """The 'tx factory' subgroup should list its commands."""
    from twyne_cli.cli import cli
    runner = CliRunner()
    result = runner.invoke(cli, ["tx", "factory", "--help"])
    assert result.exit_code == 0
    assert "create-vault" in result.output


def test_tx_batch_group_exists():
    """The 'tx batch' subgroup should list its commands."""
    from twyne_cli.cli import cli
    runner = CliRunner()
    result = runner.invoke(cli, ["tx", "batch", "--help"])
    assert result.exit_code == 0
    assert "execute" in result.output
    assert "simulate" in result.output


# --------------------------------------------------------------------------- #
# Factory create-vault command tests (mocked — no Anvil needed)
# --------------------------------------------------------------------------- #

FAKE_IV = "0x1111111111111111111111111111111111111111"
FAKE_TV = "0x2222222222222222222222222222222222222222"
FAKE_ASSET = "0x3333333333333333333333333333333333333333"


class TestCreateVaultCommand:
    """CliRunner tests for `twyne tx factory create-vault`."""

    def test_create_vault_help(self):
        """--help shows INTERMEDIATE_VAULT and TARGET_VAULT positional args."""
        from twyne_cli.cli import cli
        runner = CliRunner()
        result = runner.invoke(cli, ["tx", "factory", "create-vault", "--help"])
        assert result.exit_code == 0
        assert "INTERMEDIATE_VAULT" in result.output
        assert "TARGET_VAULT" in result.output

    def test_create_vault_aave_requires_target_asset(self):
        """--vault-type 1 without --target-asset fails before connecting."""
        from twyne_cli.cli import cli
        runner = CliRunner()
        result = runner.invoke(cli, [
            "tx", "factory", "create-vault",
            FAKE_IV, FAKE_TV,
            "--vault-type", "1",
            "--private-key", "deadbeef" * 8,
        ])
        assert result.exit_code != 0
        assert "target-asset" in result.output.lower() or "target-asset" in str(result.exception).lower()

    @patch("twyne_cli.commands.tx.simulate_through_evc")
    @patch("twyne_cli.commands.tx.resolve_account")
    @patch("twyne_cli.commands.tx.collateral_vault_factory")
    @patch.object(MagicMock, "connect", create=True)
    def test_create_vault_simulation_failure_exits_nonzero(
        self, _mock_connect, mock_factory, mock_resolve, mock_sim,
    ):
        """Simulation error is shown and command exits with code 1."""
        from twyne_cli.cli import cli

        mock_resolve.return_value = MagicMock(address="0xSENDER")
        mock_factory.return_value = MagicMock(address="0xFACTORY")
        mock_sim.return_value = {"success": False, "error": "NotIntermediateVault"}

        runner = CliRunner()
        with patch("twyne_cli.context.TwyneContext.connect"), \
             patch("twyne_cli.context.TwyneContext.disconnect"):
            result = runner.invoke(cli, [
                "tx", "factory", "create-vault",
                FAKE_IV, FAKE_TV,
                "--private-key", "deadbeef" * 8,
            ])
        assert result.exit_code != 0
        assert "NotIntermediateVault" in (result.output or "")

    @patch("twyne_cli.commands.tx.simulate_through_evc")
    @patch("twyne_cli.commands.tx.resolve_account")
    @patch("twyne_cli.commands.tx.collateral_vault_factory")
    def test_create_vault_dry_run_shows_predicted_address(
        self, mock_factory, mock_resolve, mock_sim,
    ):
        """--dry-run displays predicted vault address from simulation."""
        from twyne_cli.cli import cli

        mock_resolve.return_value = MagicMock(address="0xSENDER")
        mock_factory.return_value = MagicMock(address="0xFACTORY")
        mock_sim.return_value = {"success": True, "result": "0xNEWVAULT"}

        runner = CliRunner()
        with patch("twyne_cli.context.TwyneContext.connect"), \
             patch("twyne_cli.context.TwyneContext.disconnect"):
            result = runner.invoke(cli, [
                "tx", "factory", "create-vault",
                FAKE_IV, FAKE_TV,
                "--dry-run",
                "--private-key", "deadbeef" * 8,
            ])
        assert result.exit_code == 0
        assert "Dry run" in result.output
        assert "0xNEWVAULT" in result.output

    @patch("twyne_cli.commands.tx.simulate_through_evc")
    @patch("twyne_cli.commands.tx.resolve_account")
    @patch("twyne_cli.commands.tx.collateral_vault_factory")
    def test_create_vault_dry_run_formats_bytes_as_address(
        self, mock_factory, mock_resolve, mock_sim,
    ):
        """--dry-run converts raw bytes result to a checksummed hex address."""
        from twyne_cli.cli import cli

        mock_resolve.return_value = MagicMock(address="0xSENDER")
        mock_factory.return_value = MagicMock(address="0xFACTORY")
        # Simulate the raw bytes that EVC.call() actually returns
        raw_bytes = b'\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00' \
                    b'\x8f\x96NM\xa2\x9c_nh\xd2\xf6\x8e\xd3\xd4\xc1\xc1w\x98\x87\x15'
        mock_sim.return_value = {"success": True, "result": raw_bytes}

        runner = CliRunner()
        with patch("twyne_cli.context.TwyneContext.connect"), \
             patch("twyne_cli.context.TwyneContext.disconnect"):
            result = runner.invoke(cli, [
                "tx", "factory", "create-vault",
                FAKE_IV, FAKE_TV,
                "--dry-run",
                "--private-key", "deadbeef" * 8,
            ])
        assert result.exit_code == 0
        assert "0x8F964e4DA29c5f6e68d2f68eD3D4c1c177988715" in result.output
        assert "b'" not in result.output  # No raw bytes representation

    @patch("twyne_cli.commands.tx.confirm_prompt", return_value=False)
    @patch("twyne_cli.commands.tx.simulate_through_evc")
    @patch("twyne_cli.commands.tx.resolve_account")
    @patch("twyne_cli.commands.tx.collateral_vault_factory")
    def test_create_vault_cancelled_on_no_confirm(
        self, mock_factory, mock_resolve, mock_sim, mock_confirm,
    ):
        """Declining the confirmation prompt prints 'Cancelled.'."""
        from twyne_cli.cli import cli

        mock_resolve.return_value = MagicMock(address="0xSENDER")
        mock_factory.return_value = MagicMock(address="0xFACTORY")
        mock_sim.return_value = {"success": True, "result": "0xNEWVAULT"}

        runner = CliRunner()
        with patch("twyne_cli.context.TwyneContext.connect"), \
             patch("twyne_cli.context.TwyneContext.disconnect"):
            result = runner.invoke(cli, [
                "tx", "factory", "create-vault",
                FAKE_IV, FAKE_TV,
                "--private-key", "deadbeef" * 8,
            ])
        assert result.exit_code == 0
        assert "Cancelled" in result.output


# --------------------------------------------------------------------------- #
# Aave IV → aToken wrapper auto-resolution tests
# --------------------------------------------------------------------------- #

AAVE_IV = "0x75029a47f28550C93Ad5A3BbD2d9b5315204B561"
AAVE_WRAPPER = "0xFaBA8f777996C0C28fe9e6554D84cB30ca3e1881"


class TestAaveIVResolution:
    """Tests for automatic Aave intermediate vault → aToken wrapper resolution."""

    @patch("twyne_cli.commands.tx.simulate_through_evc")
    @patch("twyne_cli.commands.tx.resolve_account")
    @patch("twyne_cli.commands.tx.collateral_vault_factory")
    def test_aave_iv_resolved_to_wrapper(self, mock_factory, mock_resolve, mock_sim):
        """Passing the Aave IV address auto-resolves to the wrapper and prints a note."""
        from twyne_cli.cli import cli

        mock_resolve.return_value = MagicMock(address="0xSENDER")
        mock_factory.return_value = MagicMock(address="0xFACTORY")
        mock_sim.return_value = {"success": True, "result": "0xNEWVAULT"}

        runner = CliRunner(mix_stderr=False)
        with patch("twyne_cli.context.TwyneContext.connect"), \
             patch("twyne_cli.context.TwyneContext.disconnect"):
            result = runner.invoke(cli, [
                "tx", "factory", "create-vault",
                AAVE_IV, FAKE_TV,
                "--vault-type", "1",
                "--target-asset", FAKE_ASSET,
                "--dry-run",
                "--private-key", "deadbeef" * 8,
            ])
        assert result.exit_code == 0
        # The note should appear on stderr
        assert "aToken wrapper" in result.stderr
        assert AAVE_WRAPPER[-4:] in result.stderr
        # The resolved address should be passed to simulate_through_evc
        call_args = mock_sim.call_args[0][2]  # args list
        assert call_args[1] == AAVE_WRAPPER

    @patch("twyne_cli.commands.tx.simulate_through_evc")
    @patch("twyne_cli.commands.tx.resolve_account")
    @patch("twyne_cli.commands.tx.collateral_vault_factory")
    def test_aave_wrapper_address_unchanged(self, mock_factory, mock_resolve, mock_sim):
        """Passing the wrapper address directly works without resolution."""
        from twyne_cli.cli import cli

        mock_resolve.return_value = MagicMock(address="0xSENDER")
        mock_factory.return_value = MagicMock(address="0xFACTORY")
        mock_sim.return_value = {"success": True, "result": "0xNEWVAULT"}

        runner = CliRunner(mix_stderr=False)
        with patch("twyne_cli.context.TwyneContext.connect"), \
             patch("twyne_cli.context.TwyneContext.disconnect"):
            result = runner.invoke(cli, [
                "tx", "factory", "create-vault",
                AAVE_WRAPPER, FAKE_TV,
                "--vault-type", "1",
                "--target-asset", FAKE_ASSET,
                "--dry-run",
                "--private-key", "deadbeef" * 8,
            ])
        assert result.exit_code == 0
        # No resolution note on stderr
        assert "aToken wrapper" not in result.stderr
        # Address passed through as-is
        call_args = mock_sim.call_args[0][2]
        assert call_args[1] == AAVE_WRAPPER

    @patch("twyne_cli.commands.tx.simulate_through_evc")
    @patch("twyne_cli.commands.tx.resolve_account")
    @patch("twyne_cli.commands.tx.collateral_vault_factory")
    def test_euler_vault_not_resolved(self, mock_factory, mock_resolve, mock_sim):
        """Euler vault type (0) doesn't trigger any resolution."""
        from twyne_cli.cli import cli

        mock_resolve.return_value = MagicMock(address="0xSENDER")
        mock_factory.return_value = MagicMock(address="0xFACTORY")
        mock_sim.return_value = {"success": True, "result": "0xNEWVAULT"}

        runner = CliRunner(mix_stderr=False)
        with patch("twyne_cli.context.TwyneContext.connect"), \
             patch("twyne_cli.context.TwyneContext.disconnect"):
            result = runner.invoke(cli, [
                "tx", "factory", "create-vault",
                AAVE_IV, FAKE_TV,
                "--vault-type", "0",
                "--dry-run",
                "--private-key", "deadbeef" * 8,
            ])
        assert result.exit_code == 0
        # No resolution note — Euler vaults don't resolve
        assert "aToken wrapper" not in result.stderr
        # Original address passed through
        call_args = mock_sim.call_args[0][2]
        assert call_args[1] == AAVE_IV
