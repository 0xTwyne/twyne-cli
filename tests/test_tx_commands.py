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
FAKE_DEPOSIT_TOKEN = "0x4444444444444444444444444444444444444444"
FAKE_BORROW_TOKEN = "0x5555555555555555555555555555555555555555"
FAKE_EVAULT_SHARE = "0x6666666666666666666666666666666666666666"
PREDICTED_VAULT = "0x7777777777777777777777777777777777777777"


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

    @patch("twyne_cli.commands.tx.resolve_euler_factory_vault", return_value=None)
    @patch("twyne_cli.commands.tx.simulate_through_evc")
    @patch("twyne_cli.commands.tx.resolve_account")
    @patch("twyne_cli.commands.tx.collateral_vault_factory")
    @patch.object(MagicMock, "connect", create=True)
    def test_create_vault_simulation_failure_exits_nonzero(
        self, _mock_connect, mock_factory, mock_resolve, mock_sim, mock_euler_resolve,
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

    @patch("twyne_cli.commands.tx.resolve_euler_factory_vault", return_value=None)
    @patch("twyne_cli.commands.tx.simulate_through_evc")
    @patch("twyne_cli.commands.tx.resolve_account")
    @patch("twyne_cli.commands.tx.collateral_vault_factory")
    def test_create_vault_dry_run_shows_predicted_address(
        self, mock_factory, mock_resolve, mock_sim, mock_euler_resolve,
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

    @patch("twyne_cli.commands.tx.resolve_euler_factory_vault", return_value=None)
    @patch("twyne_cli.commands.tx.simulate_through_evc")
    @patch("twyne_cli.commands.tx.resolve_account")
    @patch("twyne_cli.commands.tx.collateral_vault_factory")
    def test_create_vault_dry_run_formats_bytes_as_address(
        self, mock_factory, mock_resolve, mock_sim, mock_euler_resolve,
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

    @patch("twyne_cli.commands.tx.resolve_euler_factory_vault", return_value=None)
    @patch("twyne_cli.commands.tx.confirm_prompt", return_value=False)
    @patch("twyne_cli.commands.tx.simulate_through_evc")
    @patch("twyne_cli.commands.tx.resolve_account")
    @patch("twyne_cli.commands.tx.collateral_vault_factory")
    def test_create_vault_cancelled_on_no_confirm(
        self, mock_factory, mock_resolve, mock_sim, mock_confirm, mock_euler_resolve,
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

    @patch("twyne_cli.commands.tx.resolve_euler_factory_vault", return_value=FAKE_EVAULT_SHARE)
    @patch("twyne_cli.commands.tx.simulate_through_evc")
    @patch("twyne_cli.commands.tx.resolve_account")
    @patch("twyne_cli.commands.tx.collateral_vault_factory")
    def test_euler_vault_resolved_to_collateral_asset(self, mock_factory, mock_resolve, mock_sim, mock_euler_resolve):
        """Euler vault type (0) resolves IV to collateral asset (eVault share token)."""
        from twyne_cli.cli import cli

        mock_resolve.return_value = MagicMock(address="0xSENDER")
        mock_factory.return_value = MagicMock(address="0xFACTORY")
        mock_sim.return_value = {"success": True, "result": "0xNEWVAULT"}

        runner = CliRunner(mix_stderr=False)
        with patch("twyne_cli.context.TwyneContext.connect"), \
             patch("twyne_cli.context.TwyneContext.disconnect"):
            result = runner.invoke(cli, [
                "tx", "factory", "create-vault",
                FAKE_IV, FAKE_TV,
                "--vault-type", "0",
                "--dry-run",
                "--private-key", "deadbeef" * 8,
            ])
        assert result.exit_code == 0
        # Resolution note should appear on stderr
        assert "collateral asset" in result.stderr
        assert FAKE_EVAULT_SHARE[-4:] in result.stderr
        # Resolved address should be passed to simulate_through_evc
        call_args = mock_sim.call_args[0][2]
        assert call_args[1] == FAKE_EVAULT_SHARE

    @patch("twyne_cli.commands.tx.resolve_euler_factory_vault", return_value=None)
    @patch("twyne_cli.commands.tx.simulate_through_evc")
    @patch("twyne_cli.commands.tx.resolve_account")
    @patch("twyne_cli.commands.tx.collateral_vault_factory")
    def test_euler_vault_no_resolution_when_already_correct(self, mock_factory, mock_resolve, mock_sim, mock_euler_resolve):
        """Euler vault type (0) with no resolution passes address through unchanged."""
        from twyne_cli.cli import cli

        mock_resolve.return_value = MagicMock(address="0xSENDER")
        mock_factory.return_value = MagicMock(address="0xFACTORY")
        mock_sim.return_value = {"success": True, "result": "0xNEWVAULT"}

        runner = CliRunner(mix_stderr=False)
        with patch("twyne_cli.context.TwyneContext.connect"), \
             patch("twyne_cli.context.TwyneContext.disconnect"):
            result = runner.invoke(cli, [
                "tx", "factory", "create-vault",
                FAKE_IV, FAKE_TV,
                "--vault-type", "0",
                "--dry-run",
                "--private-key", "deadbeef" * 8,
            ])
        assert result.exit_code == 0
        # No resolution note
        assert "collateral asset" not in result.stderr
        # Original address passed through
        call_args = mock_sim.call_args[0][2]
        assert call_args[1] == FAKE_IV


# --------------------------------------------------------------------------- #
# Factory open-position command tests (mocked — no Anvil needed)
# --------------------------------------------------------------------------- #

def _mock_erc20(address):
    """Return a mock ERC20 with decimals=18, a symbol, and large balance."""
    mock = MagicMock()
    mock.decimals.return_value = 18
    symbols = {
        FAKE_DEPOSIT_TOKEN: "wstETH",
        FAKE_BORROW_TOKEN: "USDC",
    }
    mock.symbol.return_value = symbols.get(address, "TOKEN")
    mock.balanceOf.return_value = 10**30  # Always enough balance for tests
    return mock


class TestOpenPosition:
    """CliRunner tests for `twyne tx factory open-position`."""

    def test_open_position_help(self):
        """--help shows --deposit, --borrow, and positional args."""
        from twyne_cli.cli import cli
        runner = CliRunner()
        result = runner.invoke(cli, ["tx", "factory", "open-position", "--help"])
        assert result.exit_code == 0
        assert "INTERMEDIATE_VAULT" in result.output
        assert "TARGET_VAULT" in result.output
        assert "--deposit" in result.output
        assert "--borrow" in result.output

    def test_open_position_requires_deposit(self):
        """Missing --deposit fails with usage error."""
        from twyne_cli.cli import cli
        runner = CliRunner()
        result = runner.invoke(cli, [
            "tx", "factory", "open-position",
            FAKE_IV, FAKE_TV,
            "--private-key", "deadbeef" * 8,
        ])
        assert result.exit_code != 0
        assert "deposit" in result.output.lower() or "required" in result.output.lower()

    @patch("twyne_cli.commands.tx.resolve_euler_factory_vault", return_value=FAKE_EVAULT_SHARE)
    @patch("twyne_cli.commands.tx.erc20", side_effect=_mock_erc20)
    @patch("twyne_cli.commands.tx.credit_vault")
    @patch("twyne_cli.commands.tx.simulate_through_evc")
    @patch("twyne_cli.commands.tx.resolve_account")
    @patch("twyne_cli.commands.tx.collateral_vault_factory")
    def test_open_position_dry_run_euler(
        self, mock_factory, mock_resolve, mock_sim_evc,
        mock_credit_vault, mock_erc20_fn, mock_euler_resolve,
    ):
        """Euler: resolves IV, predicts address, derives tokens, shows summary."""
        from twyne_cli.cli import cli

        mock_resolve.return_value = MagicMock(address="0xSENDER")
        mock_factory.return_value = MagicMock(address="0xFACTORY")
        mock_sim_evc.return_value = {"success": True, "result": PREDICTED_VAULT}

        # credit_vault calls: IV → eVault share, eVault share → underlying, target → borrow
        # (used by _derive_deposit_underlying with original_iv=FAKE_IV)
        def credit_vault_side_effect(addr):
            m = MagicMock()
            if addr == FAKE_IV:
                m.asset.return_value = FAKE_EVAULT_SHARE
            elif addr == FAKE_EVAULT_SHARE:
                m.asset.return_value = FAKE_DEPOSIT_TOKEN
            elif addr == FAKE_TV:
                m.asset.return_value = FAKE_BORROW_TOKEN
            return m
        mock_credit_vault.side_effect = credit_vault_side_effect

        runner = CliRunner(mix_stderr=False)
        with patch("twyne_cli.context.TwyneContext.connect"), \
             patch("twyne_cli.context.TwyneContext.disconnect"):
            result = runner.invoke(cli, [
                "tx", "factory", "open-position",
                FAKE_IV, FAKE_TV,
                "--deposit", "1.5",
                "--borrow", "1.0",
                "--dry-run",
                "--private-key", "deadbeef" * 8,
            ])
        assert result.exit_code == 0, result.output
        assert "Predicted vault" in result.output
        assert PREDICTED_VAULT in result.output
        assert "wstETH" in result.output
        assert "USDC" in result.output
        assert "Dry run" in result.output
        # Euler IV resolution note on stderr
        assert "collateral asset" in result.stderr
        # Resolved address used in factory args
        call_args = mock_sim_evc.call_args[0][2]
        assert call_args[1] == FAKE_EVAULT_SHARE

    @patch("twyne_cli.commands.tx.erc20", side_effect=_mock_erc20)
    @patch("twyne_cli.commands.tx.credit_vault")
    @patch("twyne_cli.commands.tx.simulate_through_evc")
    @patch("twyne_cli.commands.tx.resolve_account")
    @patch("twyne_cli.commands.tx.collateral_vault_factory")
    def test_open_position_dry_run_aave(
        self, mock_factory, mock_resolve, mock_sim_evc,
        mock_credit_vault, mock_erc20_fn,
    ):
        """Aave: auto-resolves IV, requires --target-asset, shows summary."""
        from twyne_cli.cli import cli

        mock_resolve.return_value = MagicMock(address="0xSENDER")
        mock_factory.return_value = MagicMock(address="0xFACTORY")
        mock_sim_evc.return_value = {"success": True, "result": PREDICTED_VAULT}

        # For Aave, credit_vault(IV).asset() = underlying directly
        def credit_vault_side_effect(addr):
            m = MagicMock()
            m.asset.return_value = FAKE_DEPOSIT_TOKEN
            return m
        mock_credit_vault.side_effect = credit_vault_side_effect

        runner = CliRunner(mix_stderr=False)
        with patch("twyne_cli.context.TwyneContext.connect"), \
             patch("twyne_cli.context.TwyneContext.disconnect"):
            result = runner.invoke(cli, [
                "tx", "factory", "open-position",
                AAVE_IV, FAKE_TV,
                "--vault-type", "1",
                "--target-asset", FAKE_BORROW_TOKEN,
                "--deposit", "1.0",
                "--borrow", "0.5",
                "--dry-run",
                "--private-key", "deadbeef" * 8,
            ])
        assert result.exit_code == 0, result.output
        assert "aToken wrapper" in result.stderr
        assert "Dry run" in result.output

    @patch("twyne_cli.commands.tx.resolve_euler_factory_vault", return_value=FAKE_EVAULT_SHARE)
    @patch("twyne_cli.commands.tx.erc20", side_effect=_mock_erc20)
    @patch("twyne_cli.commands.tx.credit_vault")
    @patch("twyne_cli.commands.tx.simulate_through_evc")
    @patch("twyne_cli.commands.tx.resolve_account")
    @patch("twyne_cli.commands.tx.collateral_vault_factory")
    def test_open_position_deposit_only(
        self, mock_factory, mock_resolve, mock_sim_evc,
        mock_credit_vault, mock_erc20_fn, mock_euler_resolve,
    ):
        """Without --borrow, dry-run output omits borrow token line."""
        from twyne_cli.cli import cli

        mock_resolve.return_value = MagicMock(address="0xSENDER")
        mock_factory.return_value = MagicMock(address="0xFACTORY")
        mock_sim_evc.return_value = {"success": True, "result": PREDICTED_VAULT}

        def credit_vault_side_effect(addr):
            m = MagicMock()
            if addr == FAKE_IV:
                m.asset.return_value = FAKE_EVAULT_SHARE
            elif addr == FAKE_EVAULT_SHARE:
                m.asset.return_value = FAKE_DEPOSIT_TOKEN
            return m
        mock_credit_vault.side_effect = credit_vault_side_effect

        runner = CliRunner()
        with patch("twyne_cli.context.TwyneContext.connect"), \
             patch("twyne_cli.context.TwyneContext.disconnect"):
            result = runner.invoke(cli, [
                "tx", "factory", "open-position",
                FAKE_IV, FAKE_TV,
                "--deposit", "2.0",
                "--dry-run",
                "--private-key", "deadbeef" * 8,
            ])
        assert result.exit_code == 0, result.output
        assert "Borrow token" not in result.output
        assert "Borrow" not in result.output.split("Dry run")[0].split("Deposit")[1]

    @patch("twyne_cli.commands.tx.resolve_euler_factory_vault", return_value=FAKE_EVAULT_SHARE)
    @patch("twyne_cli.commands.tx.simulate_tx")
    @patch("twyne_cli.commands.tx.ensure_allowance", return_value=True)
    @patch("twyne_cli.commands.tx.erc20", side_effect=_mock_erc20)
    @patch("twyne_cli.commands.tx.credit_vault")
    @patch("twyne_cli.commands.tx.collateral_vault")
    @patch("twyne_cli.commands.tx.evc_contract")
    @patch("twyne_cli.commands.tx.simulate_through_evc")
    @patch("twyne_cli.commands.tx.resolve_account")
    @patch("twyne_cli.commands.tx.collateral_vault_factory")
    def test_open_position_batch_simulation_failure(
        self, mock_factory, mock_resolve, mock_sim_evc,
        mock_evc, mock_cv, mock_credit_vault, mock_erc20_fn,
        mock_allowance, mock_sim_tx, mock_euler_resolve,
    ):
        """Batch simulation failure (after approval) prints error and exits 1."""
        from twyne_cli.cli import cli

        mock_resolve.return_value = MagicMock(address="0xSENDER")
        mock_factory.return_value = MagicMock(address="0xFACTORY")
        # Vault prediction succeeds
        mock_sim_evc.return_value = {"success": True, "result": PREDICTED_VAULT}

        def credit_vault_side_effect(addr):
            m = MagicMock()
            if addr == FAKE_IV:
                m.asset.return_value = FAKE_EVAULT_SHARE
            elif addr == FAKE_EVAULT_SHARE:
                m.asset.return_value = FAKE_DEPOSIT_TOKEN
            elif addr == FAKE_TV:
                m.asset.return_value = FAKE_BORROW_TOKEN
            return m
        mock_credit_vault.side_effect = credit_vault_side_effect

        mock_cv.return_value = MagicMock()
        mock_evc.return_value = MagicMock()
        # Batch simulation fails
        mock_sim_tx.return_value = {"success": False, "error": "InsufficientCollateral"}

        runner = CliRunner()
        with patch("twyne_cli.context.TwyneContext.connect"), \
             patch("twyne_cli.context.TwyneContext.disconnect"):
            result = runner.invoke(cli, [
                "tx", "factory", "open-position",
                FAKE_IV, FAKE_TV,
                "--deposit", "0.01",
                "--borrow", "100.0",
                "--private-key", "deadbeef" * 8,
            ])
        assert result.exit_code != 0
        assert "Batch simulation failed" in result.output
        assert "InsufficientCollateral" in result.output

    @patch("twyne_cli.commands.tx.resolve_euler_factory_vault", return_value=FAKE_EVAULT_SHARE)
    @patch("twyne_cli.commands.tx.credit_vault")
    @patch("twyne_cli.commands.tx.simulate_through_evc")
    @patch("twyne_cli.commands.tx.resolve_account")
    @patch("twyne_cli.commands.tx.collateral_vault_factory")
    def test_open_position_insufficient_balance(
        self, mock_factory, mock_resolve, mock_sim_evc,
        mock_credit_vault, mock_euler_resolve,
    ):
        """Insufficient token balance exits with helpful error before sending any tx."""
        from twyne_cli.cli import cli

        mock_resolve.return_value = MagicMock(address="0xSENDER")
        mock_factory.return_value = MagicMock(address="0xFACTORY")
        mock_sim_evc.return_value = {"success": True, "result": PREDICTED_VAULT}

        def credit_vault_side_effect(addr):
            m = MagicMock()
            if addr == FAKE_IV:
                m.asset.return_value = FAKE_EVAULT_SHARE
            elif addr == FAKE_EVAULT_SHARE:
                m.asset.return_value = FAKE_DEPOSIT_TOKEN
            return m
        mock_credit_vault.side_effect = credit_vault_side_effect

        # ERC20 mock with ZERO balance
        def mock_erc20_zero_balance(address):
            mock = MagicMock()
            mock.decimals.return_value = 18
            mock.symbol.return_value = "WETH"
            mock.balanceOf.return_value = 0  # Zero balance
            return mock

        runner = CliRunner(mix_stderr=False)
        with patch("twyne_cli.commands.tx.erc20", side_effect=mock_erc20_zero_balance), \
             patch("twyne_cli.context.TwyneContext.connect"), \
             patch("twyne_cli.context.TwyneContext.disconnect"):
            result = runner.invoke(cli, [
                "tx", "factory", "open-position",
                FAKE_IV, FAKE_TV,
                "--deposit", "1.0",
                "--private-key", "deadbeef" * 8,
            ])
        assert result.exit_code != 0
        assert "Insufficient WETH balance" in result.stderr
        assert "wrap eth" in result.stderr.lower()
