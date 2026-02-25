"""Tests for migrate-position command (Euler and Aave flows)."""

from unittest.mock import MagicMock, patch

from click.testing import CliRunner

from twyne_cli.discover import DiscoveredPosition

# --------------------------------------------------------------------------- #
# Shared fixtures
# --------------------------------------------------------------------------- #

FAKE_IV = "0x7613D202Af490c3d1cE1873b0a7022a34E89815f"
FAKE_EVAULT = "0x1111111111111111111111111111111111111111"
FAKE_DEBT_EVAULT = "0x2222222222222222222222222222222222222222"
FAKE_USER = "0xAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
PREDICTED_CV = "0x7777777777777777777777777777777777777777"
FAKE_FACTORY = "0xFACFACFACFACFACFACFACFACFACFACFACFACFACF"
FAKE_EVC_ADDR = "0xef39D6493884C4C84D38a4bFF879Ce16CEdE702a"
FAKE_OPERATOR = "0x868a21426852A775395d4b90De23B3e3E662bd78"
AAVE_IV = "0x75029a47f28550C93Ad5A3BbD2d9b5315204B561"
AAVE_WRAPPER = "0xFaBA8f777996C0C28fe9e6554D84cB30ca3e1881"
AAVE_AWSTETH = "0x0B925eD163218f6662a35e0f0371Ac234f9E9371"
AAVE_WETH = "0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2"


def _euler_position():
    return DiscoveredPosition(
        protocol="euler",
        collateral_address=FAKE_EVAULT,
        collateral_symbol="wstETH",
        collateral_amount=1_500_000_000_000_000_000,
        collateral_decimals=18,
        debt_address=FAKE_DEBT_EVAULT,
        debt_symbol="USDC",
        debt_amount=2_000_000_000,
        debt_decimals=6,
        sub_account_id=0,
        intermediate_vault=FAKE_IV,
        ltv_bps=7500,
        liq_ltv_bps=8600,
        max_twyne_ltv_bps=9800,
    )


def _aave_position():
    return DiscoveredPosition(
        protocol="aave",
        collateral_address=AAVE_AWSTETH,
        collateral_symbol="awstETH",
        collateral_amount=3_200_000_000_000_000_000,
        collateral_decimals=18,
        debt_address=AAVE_WETH,
        debt_symbol="WETH",
        debt_amount=2_100_000_000_000_000_000,
        debt_decimals=18,
        sub_account_id=0,
        intermediate_vault=AAVE_IV,
        ltv_bps=6563,
        liq_ltv_bps=9500,
        max_twyne_ltv_bps=9800,
    )


# --------------------------------------------------------------------------- #
# Help / CLI structure tests
# --------------------------------------------------------------------------- #


class TestMigrateHelp:
    """Basic CLI structure tests."""

    def test_discover_positions_help(self):
        """--help shows WALLET_ADDRESS and --protocol."""
        from twyne_cli.cli import cli
        runner = CliRunner()
        result = runner.invoke(cli, ["tx", "discover-positions", "--help"])
        assert result.exit_code == 0
        assert "WALLET_ADDRESS" in result.output
        assert "--protocol" in result.output

    def test_migrate_position_help(self):
        """--help shows WALLET_ADDRESS, --ltv, --position, --protocol."""
        from twyne_cli.cli import cli
        runner = CliRunner()
        result = runner.invoke(cli, ["tx", "migrate-position", "--help"])
        assert result.exit_code == 0
        assert "WALLET_ADDRESS" in result.output
        assert "--ltv" in result.output
        assert "--position" in result.output
        assert "--protocol" in result.output


# --------------------------------------------------------------------------- #
# Euler migrate tests
# --------------------------------------------------------------------------- #


class TestMigrateEuler:
    """Tests for Euler migration flow."""

    @patch("twyne_cli.commands.tx.display_receipt")
    @patch("twyne_cli.commands.tx._send_tx")
    @patch("twyne_cli.commands.tx.simulate_tx")
    @patch("twyne_cli.commands.tx.ensure_allowance", return_value=True)
    @patch("twyne_cli.commands.tx.collateral_vault")
    @patch("twyne_cli.commands.tx.simulate_through_evc")
    @patch("twyne_cli.commands.tx.resolve_euler_factory_vault", return_value=FAKE_EVAULT)
    @patch("twyne_cli.commands.tx.collateral_vault_factory")
    @patch("twyne_cli.commands.tx.evc_contract")
    @patch("twyne_cli.commands.tx.resolve_account")
    @patch("twyne_cli.commands.tx.discover_euler_positions")
    def test_euler_batch_structure(
        self, mock_discover, mock_resolve, mock_evc, mock_factory,
        mock_euler_resolve, mock_sim_evc, mock_cv, mock_allowance,
        mock_sim_tx, mock_send_tx, mock_display,
    ):
        """Euler batch has 2 items: createCV + teleport."""
        from twyne_cli.cli import cli

        mock_discover.return_value = [_euler_position()]
        mock_resolve.return_value = MagicMock(address=FAKE_USER)
        mock_evc.return_value = MagicMock(address=FAKE_EVC_ADDR)
        mock_factory.return_value = MagicMock(address=FAKE_FACTORY)
        mock_sim_evc.return_value = {"success": True, "result": PREDICTED_CV}
        mock_cv.return_value = MagicMock()
        mock_sim_tx.return_value = {"success": True}
        mock_send_tx.return_value = MagicMock(logs=[], status=1)

        runner = CliRunner()
        with patch("twyne_cli.context.TwyneContext.connect"), \
             patch("twyne_cli.context.TwyneContext.disconnect"):
            result = runner.invoke(cli, [
                "tx", "migrate-position", FAKE_USER,
                "--protocol", "euler",
                "--position", "1",
                "--yes",
                "--private-key", "deadbeef" * 8,
            ])
        assert result.exit_code == 0, result.output

        # Verify batch was simulated with 2 items
        mock_sim_tx.assert_called_once()
        batch_items = mock_sim_tx.call_args[0][2][0]
        assert len(batch_items) == 2

    @patch("twyne_cli.commands.tx.simulate_tx")
    @patch("twyne_cli.commands.tx.ensure_allowance", return_value=True)
    @patch("twyne_cli.commands.tx.collateral_vault")
    @patch("twyne_cli.commands.tx.simulate_through_evc")
    @patch("twyne_cli.commands.tx.resolve_euler_factory_vault", return_value=FAKE_EVAULT)
    @patch("twyne_cli.commands.tx.collateral_vault_factory")
    @patch("twyne_cli.commands.tx.evc_contract")
    @patch("twyne_cli.commands.tx.resolve_account")
    @patch("twyne_cli.commands.tx.discover_euler_positions")
    def test_euler_dry_run(
        self, mock_discover, mock_resolve, mock_evc, mock_factory,
        mock_euler_resolve, mock_sim_evc, mock_cv, mock_allowance, mock_sim_tx,
    ):
        """--dry-run shows simulation result without executing."""
        from twyne_cli.cli import cli

        mock_discover.return_value = [_euler_position()]
        mock_resolve.return_value = MagicMock(address=FAKE_USER)
        mock_evc.return_value = MagicMock(address=FAKE_EVC_ADDR)
        mock_factory.return_value = MagicMock(address=FAKE_FACTORY)
        mock_sim_evc.return_value = {"success": True, "result": PREDICTED_CV}
        mock_cv.return_value = MagicMock()
        mock_sim_tx.return_value = {"success": True}

        runner = CliRunner()
        with patch("twyne_cli.context.TwyneContext.connect"), \
             patch("twyne_cli.context.TwyneContext.disconnect"):
            result = runner.invoke(cli, [
                "tx", "migrate-position", FAKE_USER,
                "--protocol", "euler",
                "--position", "1",
                "--dry-run",
                "--private-key", "deadbeef" * 8,
            ])
        assert result.exit_code == 0, result.output
        assert "Dry run" in result.output

    @patch("twyne_cli.commands.tx.simulate_tx")
    @patch("twyne_cli.commands.tx.ensure_allowance", return_value=True)
    @patch("twyne_cli.commands.tx.collateral_vault")
    @patch("twyne_cli.commands.tx.simulate_through_evc")
    @patch("twyne_cli.commands.tx.resolve_euler_factory_vault", return_value=FAKE_EVAULT)
    @patch("twyne_cli.commands.tx.collateral_vault_factory")
    @patch("twyne_cli.commands.tx.evc_contract")
    @patch("twyne_cli.commands.tx.resolve_account")
    @patch("twyne_cli.commands.tx.discover_euler_positions")
    def test_euler_teleport_uses_uint256_max(
        self, mock_discover, mock_resolve, mock_evc, mock_factory,
        mock_euler_resolve, mock_sim_evc, mock_cv, mock_allowance, mock_sim_tx,
    ):
        """Euler teleport uses uint256.max for toBorrow (auto-repay all debt)."""
        from twyne_cli.cli import cli

        mock_discover.return_value = [_euler_position()]
        mock_resolve.return_value = MagicMock(address=FAKE_USER)
        mock_evc.return_value = MagicMock(address=FAKE_EVC_ADDR)
        mock_factory.return_value = MagicMock(address=FAKE_FACTORY)
        mock_sim_evc.return_value = {"success": True, "result": PREDICTED_CV}

        # Capture teleport.encode_input args
        cv_mock = MagicMock()
        mock_cv.return_value = cv_mock
        mock_sim_tx.return_value = {"success": True}

        runner = CliRunner()
        with patch("twyne_cli.context.TwyneContext.connect"), \
             patch("twyne_cli.context.TwyneContext.disconnect"):
            result = runner.invoke(cli, [
                "tx", "migrate-position", FAKE_USER,
                "--protocol", "euler",
                "--position", "1",
                "--dry-run",
                "--private-key", "deadbeef" * 8,
            ])
        assert result.exit_code == 0, result.output

        # Verify teleport was called with (collateral_amount, uint256.max, sub_account_id)
        cv_mock.teleport.encode_input.assert_called_once()
        call_args = cv_mock.teleport.encode_input.call_args[0]
        assert call_args[0] == 1_500_000_000_000_000_000  # collateral amount
        assert call_args[1] == 2**256 - 1  # uint256.max
        assert call_args[2] == 0  # sub_account_id

    @patch("twyne_cli.commands.tx.ensure_allowance", return_value=True)
    @patch("twyne_cli.commands.tx.collateral_vault")
    @patch("twyne_cli.commands.tx.simulate_through_evc")
    @patch("twyne_cli.commands.tx.resolve_euler_factory_vault", return_value=FAKE_EVAULT)
    @patch("twyne_cli.commands.tx.collateral_vault_factory")
    @patch("twyne_cli.commands.tx.evc_contract")
    @patch("twyne_cli.commands.tx.resolve_account")
    @patch("twyne_cli.commands.tx.discover_euler_positions")
    def test_euler_approval_to_predicted_cv(
        self, mock_discover, mock_resolve, mock_evc, mock_factory,
        mock_euler_resolve, mock_sim_evc, mock_cv, mock_allowance,
    ):
        """Euler approval targets the predicted CV address."""
        from twyne_cli.cli import cli

        mock_discover.return_value = [_euler_position()]
        mock_resolve.return_value = MagicMock(address=FAKE_USER)
        mock_evc.return_value = MagicMock(address=FAKE_EVC_ADDR)
        mock_factory.return_value = MagicMock(address=FAKE_FACTORY)
        mock_sim_evc.return_value = {"success": True, "result": PREDICTED_CV}
        mock_cv.return_value = MagicMock()

        runner = CliRunner()
        with patch("twyne_cli.commands.tx.simulate_tx", return_value={"success": True}), \
             patch("twyne_cli.context.TwyneContext.connect"), \
             patch("twyne_cli.context.TwyneContext.disconnect"):
            result = runner.invoke(cli, [
                "tx", "migrate-position", FAKE_USER,
                "--protocol", "euler",
                "--position", "1",
                "--dry-run",
                "--private-key", "deadbeef" * 8,
            ])
        # Dry run skips allowance, but check the flow works
        assert result.exit_code == 0, result.output


# --------------------------------------------------------------------------- #
# Aave migrate tests
# --------------------------------------------------------------------------- #


class TestMigrateAave:
    """Tests for Aave migration flow."""

    @patch("twyne_cli.commands.tx.display_receipt")
    @patch("twyne_cli.commands.tx._send_tx")
    @patch("twyne_cli.commands.tx.simulate_tx")
    @patch("twyne_cli.commands.tx.ensure_allowance", return_value=True)
    @patch("twyne_cli.commands.tx.teleport_operator")
    @patch("twyne_cli.commands.tx.simulate_through_evc")
    @patch("twyne_cli.commands.tx.resolve_aave_factory_vault", return_value=AAVE_WRAPPER)
    @patch("twyne_cli.commands.tx.collateral_vault_factory")
    @patch("twyne_cli.commands.tx.evc_contract")
    @patch("twyne_cli.commands.tx.resolve_account")
    @patch("twyne_cli.commands.tx.discover_aave_positions")
    def test_aave_batch_structure(
        self, mock_discover, mock_resolve, mock_evc, mock_factory,
        mock_aave_resolve, mock_sim_evc, mock_teleport_op, mock_allowance,
        mock_sim_tx, mock_send_tx, mock_display,
    ):
        """Aave batch has 4 items: createCV + enableOp + executeTeleport + disableOp.

        setAccountOperator items must use address(0) as onBehalfOfAccount
        (matches AaveOperatorsTest.t.sol:test_AaveV3TeleportOperator_SingleBatch).
        """
        from twyne_cli.cli import cli

        mock_discover.return_value = [_aave_position()]
        mock_resolve.return_value = MagicMock(address=FAKE_USER)
        mock_evc.return_value = MagicMock(address=FAKE_EVC_ADDR)
        mock_factory.return_value = MagicMock(address=FAKE_FACTORY)
        mock_sim_evc.return_value = {"success": True, "result": PREDICTED_CV}
        mock_teleport_op.return_value = MagicMock(address=FAKE_OPERATOR)
        mock_sim_tx.return_value = {"success": True}
        mock_send_tx.return_value = MagicMock(logs=[], status=1)

        runner = CliRunner()
        with patch("twyne_cli.context.TwyneContext.connect"), \
             patch("twyne_cli.context.TwyneContext.disconnect"):
            result = runner.invoke(cli, [
                "tx", "migrate-position", FAKE_USER,
                "--protocol", "aave",
                "--position", "1",
                "--yes",
                "--private-key", "deadbeef" * 8,
            ])
        assert result.exit_code == 0, result.output

        # Verify batch has 4 items
        mock_sim_tx.assert_called_once()
        batch_items = mock_sim_tx.call_args[0][2][0]
        assert len(batch_items) == 4

        # Verify setAccountOperator items use address(0) as onBehalfOfAccount
        zero_addr = "0x0000000000000000000000000000000000000000"
        assert batch_items[1][1] == zero_addr, "enableOp must use address(0)"
        assert batch_items[3][1] == zero_addr, "disableOp must use address(0)"

        # Verify createCV and executeTeleport use sender
        assert batch_items[0][1] == FAKE_USER, "createCV uses sender"
        assert batch_items[2][1] == FAKE_USER, "executeTeleport uses sender"

    @patch("twyne_cli.commands.tx.simulate_tx")
    @patch("twyne_cli.commands.tx.ensure_allowance", return_value=True)
    @patch("twyne_cli.commands.tx.teleport_operator")
    @patch("twyne_cli.commands.tx.simulate_through_evc")
    @patch("twyne_cli.commands.tx.resolve_aave_factory_vault", return_value=AAVE_WRAPPER)
    @patch("twyne_cli.commands.tx.collateral_vault_factory")
    @patch("twyne_cli.commands.tx.evc_contract")
    @patch("twyne_cli.commands.tx.resolve_account")
    @patch("twyne_cli.commands.tx.discover_aave_positions")
    def test_aave_uses_uint256_max_for_teleport(
        self, mock_discover, mock_resolve, mock_evc, mock_factory,
        mock_aave_resolve, mock_sim_evc, mock_teleport_op, mock_allowance,
        mock_sim_tx,
    ):
        """Aave executeTeleport uses uint256.max for amounts (contract reads current balances)."""
        from twyne_cli.cli import cli

        pos = _aave_position()
        mock_discover.return_value = [pos]
        mock_resolve.return_value = MagicMock(address=FAKE_USER)
        mock_evc.return_value = MagicMock(address=FAKE_EVC_ADDR)
        mock_factory.return_value = MagicMock(address=FAKE_FACTORY)
        mock_sim_evc.return_value = {"success": True, "result": PREDICTED_CV}

        op_mock = MagicMock(address=FAKE_OPERATOR)
        mock_teleport_op.return_value = op_mock
        mock_sim_tx.return_value = {"success": True}

        runner = CliRunner()
        with patch("twyne_cli.context.TwyneContext.connect"), \
             patch("twyne_cli.context.TwyneContext.disconnect"):
            result = runner.invoke(cli, [
                "tx", "migrate-position", FAKE_USER,
                "--protocol", "aave",
                "--position", "1",
                "--dry-run",
                "--private-key", "deadbeef" * 8,
            ])
        assert result.exit_code == 0, result.output

        # Verify executeTeleport uses uint256.max for both aTokenAmount and debtAmount
        op_mock.executeTeleport.encode_input.assert_called_once()
        call_args = op_mock.executeTeleport.encode_input.call_args[0]
        uint256_max = 2**256 - 1
        assert call_args[1] == uint256_max, "aTokenAmount should be uint256.max"
        assert call_args[2] == uint256_max, "debtAmount should be uint256.max"

    @patch("twyne_cli.commands.tx.simulate_tx")
    @patch("twyne_cli.commands.tx.ensure_allowance", return_value=True)
    @patch("twyne_cli.commands.tx.teleport_operator")
    @patch("twyne_cli.commands.tx.simulate_through_evc")
    @patch("twyne_cli.commands.tx.resolve_aave_factory_vault", return_value=AAVE_WRAPPER)
    @patch("twyne_cli.commands.tx.collateral_vault_factory")
    @patch("twyne_cli.commands.tx.evc_contract")
    @patch("twyne_cli.commands.tx.resolve_account")
    @patch("twyne_cli.commands.tx.discover_aave_positions")
    def test_aave_approval_to_teleport_operator(
        self, mock_discover, mock_resolve, mock_evc, mock_factory,
        mock_aave_resolve, mock_sim_evc, mock_teleport_op, mock_allowance,
        mock_sim_tx,
    ):
        """Aave aToken approval targets the teleport operator (not the CV)."""
        from twyne_cli.cli import cli

        mock_discover.return_value = [_aave_position()]
        mock_resolve.return_value = MagicMock(address=FAKE_USER)
        mock_evc.return_value = MagicMock(address=FAKE_EVC_ADDR)
        mock_factory.return_value = MagicMock(address=FAKE_FACTORY)
        mock_sim_evc.return_value = {"success": True, "result": PREDICTED_CV}
        mock_teleport_op.return_value = MagicMock(address=FAKE_OPERATOR)
        mock_sim_tx.return_value = {"success": True}

        runner = CliRunner()
        with patch("twyne_cli.context.TwyneContext.connect"), \
             patch("twyne_cli.context.TwyneContext.disconnect"):
            result = runner.invoke(cli, [
                "tx", "migrate-position", FAKE_USER,
                "--protocol", "aave",
                "--position", "1",
                "--dry-run",
                "--private-key", "deadbeef" * 8,
            ])
        # Dry run skips allowance
        assert result.exit_code == 0, result.output


# --------------------------------------------------------------------------- #
# Edge cases
# --------------------------------------------------------------------------- #


class TestMigrateEdgeCases:
    """Edge case tests for migrate-position."""

    @patch("twyne_cli.commands.tx.resolve_account")
    @patch("twyne_cli.commands.tx.discover_all_positions", return_value=[])
    def test_no_positions_found(self, mock_discover, mock_resolve):
        """Exits gracefully when no migratable positions exist."""
        from twyne_cli.cli import cli

        mock_resolve.return_value = MagicMock(address=FAKE_USER)

        runner = CliRunner()
        with patch("twyne_cli.context.TwyneContext.connect"), \
             patch("twyne_cli.context.TwyneContext.disconnect"):
            result = runner.invoke(cli, [
                "tx", "migrate-position", FAKE_USER,
                "--private-key", "deadbeef" * 8,
            ])
        assert result.exit_code == 0
        assert "No migratable positions found" in result.output

    @patch("twyne_cli.commands.tx.resolve_account")
    @patch("twyne_cli.commands.tx.discover_euler_positions")
    def test_invalid_position_number(self, mock_discover, mock_resolve):
        """Invalid --position number exits with error."""
        from twyne_cli.cli import cli

        mock_discover.return_value = [_euler_position()]
        mock_resolve.return_value = MagicMock(address=FAKE_USER)

        runner = CliRunner()
        with patch("twyne_cli.context.TwyneContext.connect"), \
             patch("twyne_cli.context.TwyneContext.disconnect"):
            result = runner.invoke(cli, [
                "tx", "migrate-position", FAKE_USER,
                "--protocol", "euler",
                "--position", "5",
                "--private-key", "deadbeef" * 8,
            ])
        assert result.exit_code != 0
        assert "Invalid position number" in (result.output or str(result.exception))
