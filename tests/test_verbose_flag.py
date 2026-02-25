"""Tests for the --verbose / -v global flag."""

from unittest.mock import MagicMock, patch

from click.testing import CliRunner

from twyne_cli.context import TwyneContext

FAKE_IV = "0x1111111111111111111111111111111111111111"
FAKE_TV = "0x2222222222222222222222222222222222222222"


class TestVerboseFlag:
    """Tests for --verbose flag behavior."""

    def test_verbose_flag_in_help(self):
        """--help shows --verbose / -v option."""
        from twyne_cli.cli import cli
        runner = CliRunner()
        result = runner.invoke(cli, ["--help"])
        assert result.exit_code == 0
        assert "--verbose" in result.output
        assert "-v" in result.output

    def test_verbose_context_false_by_default(self):
        """TwyneContext defaults verbose=False."""
        ctx = TwyneContext(rpc_url=None, force_json=False, block=None)
        assert ctx.verbose is False

    def test_verbose_context_passed(self):
        """TwyneContext accepts verbose=True."""
        ctx = TwyneContext(rpc_url=None, force_json=False, block=None, verbose=True)
        assert ctx.verbose is True

    @patch("twyne_cli.commands.tx.simulate_through_evc")
    @patch("twyne_cli.commands.tx.resolve_account")
    @patch("twyne_cli.commands.tx.collateral_vault_factory")
    def test_verbose_off_hides_details(self, mock_factory, mock_resolve, mock_sim):
        """Without -v, only 'Simulation failed:' is shown, no revert details."""
        from twyne_cli.cli import cli

        mock_resolve.return_value = MagicMock(address="0xSENDER")
        mock_factory.return_value = MagicMock(address="0xFACTORY")
        mock_sim.return_value = {
            "success": False,
            "error": "Transaction failed.",
            "revert_message": "EVC_EmptyError",
            "contract_address": "0xDEAD",
        }

        runner = CliRunner(mix_stderr=False)
        with patch("twyne_cli.context.TwyneContext.connect"), \
             patch("twyne_cli.context.TwyneContext.disconnect"):
            result = runner.invoke(cli, [
                "tx", "factory", "create-vault",
                FAKE_IV, FAKE_TV,
                "--private-key", "deadbeef" * 8,
            ])
        assert result.exit_code != 0
        assert "Simulation failed" in result.stderr
        assert "Revert reason" not in result.stderr
        assert "Contract:" not in result.stderr

    @patch("twyne_cli.commands.tx.simulate_through_evc")
    @patch("twyne_cli.commands.tx.resolve_account")
    @patch("twyne_cli.commands.tx.collateral_vault_factory")
    def test_verbose_on_shows_revert_message(self, mock_factory, mock_resolve, mock_sim):
        """-v shows 'Revert reason:' from simulation error."""
        from twyne_cli.cli import cli

        mock_resolve.return_value = MagicMock(address="0xSENDER")
        mock_factory.return_value = MagicMock(address="0xFACTORY")
        mock_sim.return_value = {
            "success": False,
            "error": "Transaction failed.",
            "revert_message": "EVC_EmptyError",
        }

        runner = CliRunner(mix_stderr=False)
        with patch("twyne_cli.context.TwyneContext.connect"), \
             patch("twyne_cli.context.TwyneContext.disconnect"):
            result = runner.invoke(cli, [
                "-v",
                "tx", "factory", "create-vault",
                FAKE_IV, FAKE_TV,
                "--private-key", "deadbeef" * 8,
            ])
        assert result.exit_code != 0
        assert "Revert reason: EVC_EmptyError" in result.stderr

    @patch("twyne_cli.commands.tx.simulate_through_evc")
    @patch("twyne_cli.commands.tx.resolve_account")
    @patch("twyne_cli.commands.tx.collateral_vault_factory")
    def test_verbose_on_shows_contract_address(self, mock_factory, mock_resolve, mock_sim):
        """-v shows 'Contract:' from simulation error."""
        from twyne_cli.cli import cli

        mock_resolve.return_value = MagicMock(address="0xSENDER")
        mock_factory.return_value = MagicMock(address="0xFACTORY")
        mock_sim.return_value = {
            "success": False,
            "error": "Transaction failed.",
            "contract_address": "0xDEADBEEF",
        }

        runner = CliRunner(mix_stderr=False)
        with patch("twyne_cli.context.TwyneContext.connect"), \
             patch("twyne_cli.context.TwyneContext.disconnect"):
            result = runner.invoke(cli, [
                "-v",
                "tx", "factory", "create-vault",
                FAKE_IV, FAKE_TV,
                "--private-key", "deadbeef" * 8,
            ])
        assert result.exit_code != 0
        assert "Contract:      0xDEADBEEF" in result.stderr
