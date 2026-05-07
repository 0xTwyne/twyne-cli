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
    def test_aave_iv_passes_through_to_factory(self, mock_factory, mock_resolve, mock_sim):
        """v1.0.5: the Aave IV address is forwarded as-is (no aTokenWrapper rewrite).

        Pre-v1.0.5 the CLI rewrote the IV to the aTokenWrapper before calling the
        factory. v1.0.5's factory expects the IV directly and validates it via
        VaultManager.isIntermediateVault, so the rewrite was removed.
        """
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
        assert "aToken wrapper" not in result.stderr  # rewrite path removed
        # IV passes through unchanged
        call_args = mock_sim.call_args[0][2]
        assert call_args[1] == AAVE_IV

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
    def test_euler_iv_passes_through_to_factory(self, mock_factory, mock_resolve, mock_sim):
        """v1.0.5: Euler IV is forwarded as-is (no eVault-share-token rewrite).

        Pre-v1.0.5 the CLI rewrote the IV to the underlying eVault share token
        before calling the factory. v1.0.5's factory expects the IV directly.
        """
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
        assert "collateral asset" not in result.stderr  # rewrite path removed
        # IV passes through unchanged
        call_args = mock_sim.call_args[0][2]
        assert call_args[1] == FAKE_IV

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
        # v1.0.5: no rewrite — IV passes through to the factory unchanged.
        assert "collateral asset" not in result.stderr
        call_args = mock_sim_evc.call_args[0][2]
        assert call_args[1] == FAKE_IV

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
        # v1.0.5: no aToken-wrapper rewrite for Aave IV.
        assert "aToken wrapper" not in result.stderr
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


# --------------------------------------------------------------------------- #
# Close-position command tests (mocked — no Anvil needed)
# --------------------------------------------------------------------------- #

FAKE_UNDERLYING = "0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2"  # WETH
FAKE_TARGET_ASSET = "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48"  # USDC
FAKE_ASSET_EVAULT = "0x8888888888888888888888888888888888888888"
FAKE_TARGET_VAULT = "0x9999999999999999999999999999999999999999"
FAKE_OPERATOR_ADDR = "0x36b2Bd4E17827E9dEABdB3AD520AC597972196D4"
FAKE_EVC_ADDR = "0xef39D6493884C4C84D38a4bFF879Ce16CEdE702a"
FAKE_IV_ADDR = "0xAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
FAKE_VM_ADDR = "0xBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB"

FAKE_SWAP_QUOTE = {
    "swap": {
        "swapperData": "0xdeadbeef01020304cafebabe05060708",
        "multicallItems": [
            {"data": "0xdeadbeef01020304"},
            {"data": "0xcafebabe05060708"},
        ],
    },
    "amountIn": "1000000000000000000",
    "amountOut": "2500000000",
    "amountOutMin": "2475000000",
}


def _mock_cv_for_close(address):
    """Return a mock CollateralVault with state for close-position."""
    mock = MagicMock()
    mock.totalAssetsDepositedOrReserved.return_value = 2_000_000_000_000_000_000  # 2e18
    mock.maxRelease.return_value = 500_000_000_000_000_000  # 0.5e18
    mock.maxRepay.return_value = 1_500_000_000  # 1500 USDC (6 decimals)
    mock.targetAsset.return_value = FAKE_TARGET_ASSET
    mock.targetVault.return_value = FAKE_TARGET_VAULT
    mock.asset.return_value = FAKE_ASSET_EVAULT
    mock.intermediateVault.return_value = FAKE_IV_ADDR
    mock.twyneVaultManager.return_value = FAKE_VM_ADDR
    mock.twyneLiqLTV.return_value = 8500  # 85% in basis points
    return mock


def _mock_credit_vault_for_close(address):
    """Return a mock credit vault (EVault).

    Handles both the eVault share token (asset()) and the target vault (LTVLiquidation).
    """
    mock = MagicMock()
    if address == FAKE_TARGET_VAULT:
        # Target EVault — provides external liquidation LTV
        mock.LTVLiquidation.return_value = 7500  # 75% external liq LTV
        return mock
    # eVault share token — provides underlying asset and share conversion
    mock.asset.return_value = FAKE_UNDERLYING
    mock.convertToAssets.side_effect = lambda shares: shares  # 1:1 ratio
    return mock


def _mock_erc20_for_close(address):
    """Return a mock ERC20 with decimals and symbol for WETH or USDC."""
    mock = MagicMock()
    if address == FAKE_UNDERLYING:
        mock.decimals.return_value = 18
        mock.symbol.return_value = "WETH"
    elif address == FAKE_TARGET_ASSET:
        mock.decimals.return_value = 6
        mock.symbol.return_value = "USDC"
    else:
        mock.decimals.return_value = 18
        mock.symbol.return_value = "TOKEN"
    return mock


class TestClosePosition:
    """CliRunner tests for `twyne tx operators close-position`."""

    def test_close_position_help(self):
        """--help shows vault_address, --slippage, --protocol."""
        from twyne_cli.cli import cli
        runner = CliRunner()
        result = runner.invoke(cli, ["tx", "operators", "close-position", "--help"])
        assert result.exit_code == 0
        assert "VAULT_ADDRESS" in result.output
        assert "--slippage" in result.output
        assert "--protocol" in result.output

    @patch("twyne_cli.commands.tx.collateral_vault")
    @patch("twyne_cli.commands.tx.resolve_account")
    def test_close_position_no_debt(self, mock_resolve, mock_cv):
        """Vault with 0 debt prints 'No debt to repay'."""
        from twyne_cli.cli import cli

        mock_resolve.return_value = MagicMock(address="0xSENDER")
        cv_mock = MagicMock()
        cv_mock.totalAssetsDepositedOrReserved.return_value = 1_000_000_000_000_000_000
        cv_mock.maxRelease.return_value = 0
        cv_mock.maxRepay.return_value = 0  # No debt
        mock_cv.return_value = cv_mock

        runner = CliRunner()
        with patch("twyne_cli.context.TwyneContext.connect"), \
             patch("twyne_cli.context.TwyneContext.disconnect"):
            result = runner.invoke(cli, [
                "tx", "operators", "close-position",
                "0xVAULT",
                "--private-key", "deadbeef" * 8,
            ])
        assert result.exit_code == 0
        assert "No debt to repay" in result.output

    @patch("twyne_cli.commands.tx.simulate_tx")
    @patch("twyne_cli.commands.tx.evc_contract")
    @patch("twyne_cli.commands.tx.get_swap_quote", return_value=FAKE_SWAP_QUOTE)
    @patch("twyne_cli.commands.tx.deleverage_operator")
    @patch("twyne_cli.commands.tx.vault_manager")
    @patch("twyne_cli.commands.tx.erc20", side_effect=_mock_erc20_for_close)
    @patch("twyne_cli.commands.tx.credit_vault", side_effect=_mock_credit_vault_for_close)
    @patch("twyne_cli.commands.tx.collateral_vault", side_effect=_mock_cv_for_close)
    @patch("twyne_cli.commands.tx.resolve_account")
    def test_close_position_dry_run(
        self, mock_resolve, mock_cv, mock_credit, mock_erc20_fn,
        mock_vm, mock_delev_op, mock_swap_quote,
        mock_evc, mock_sim_tx,
    ):
        """Reads vault state, calls swap API, simulates batch, shows summary."""
        from twyne_cli.cli import cli

        mock_resolve.return_value = MagicMock(address="0xSENDER")
        mock_delev_op.return_value = MagicMock(address=FAKE_OPERATOR_ADDR)
        mock_evc.return_value = MagicMock(address=FAKE_EVC_ADDR)
        mock_vm.return_value = MagicMock(**{"externalLiqBuffers.return_value": 9500})  # 95% buffer
        mock_sim_tx.return_value = {"success": True}

        runner = CliRunner()
        with patch("twyne_cli.context.TwyneContext.connect"), \
             patch("twyne_cli.context.TwyneContext.disconnect"):
            result = runner.invoke(cli, [
                "tx", "operators", "close-position",
                "0xVAULT",
                "--slippage", "1.0",
                "--dry-run",
                "--private-key", "deadbeef" * 8,
            ])
        assert result.exit_code == 0, result.output
        assert "Close Position" in result.output
        assert "WETH" in result.output
        assert "USDC" in result.output
        assert "Dry run" in result.output
        mock_swap_quote.assert_called_once()

    @patch("twyne_cli.commands.tx.simulate_tx")
    @patch("twyne_cli.commands.tx.evc_contract")
    @patch("twyne_cli.commands.tx.get_swap_quote", return_value=FAKE_SWAP_QUOTE)
    @patch("twyne_cli.commands.tx.deleverage_operator")
    @patch("twyne_cli.commands.tx.vault_manager")
    @patch("twyne_cli.commands.tx.erc20", side_effect=_mock_erc20_for_close)
    @patch("twyne_cli.commands.tx.credit_vault", side_effect=_mock_credit_vault_for_close)
    @patch("twyne_cli.commands.tx.collateral_vault", side_effect=_mock_cv_for_close)
    @patch("twyne_cli.commands.tx.resolve_account")
    def test_close_position_batch_structure(
        self, mock_resolve, mock_cv, mock_credit, mock_erc20_fn,
        mock_vm, mock_delev_op, mock_swap_quote,
        mock_evc, mock_sim_tx,
    ):
        """Batch has 4 items: enable operator -> set liqLTV -> deleverage -> disable operator."""
        from twyne_cli.cli import cli

        mock_resolve.return_value = MagicMock(address="0xSENDER")
        mock_delev_op.return_value = MagicMock(address=FAKE_OPERATOR_ADDR)
        evc_mock = MagicMock(address=FAKE_EVC_ADDR)
        mock_evc.return_value = evc_mock
        mock_vm.return_value = MagicMock(**{"externalLiqBuffers.return_value": 9500})
        mock_sim_tx.return_value = {"success": True}

        runner = CliRunner()
        with patch("twyne_cli.context.TwyneContext.connect"), \
             patch("twyne_cli.context.TwyneContext.disconnect"):
            result = runner.invoke(cli, [
                "tx", "operators", "close-position",
                "0xVAULT",
                "--dry-run",
                "--private-key", "deadbeef" * 8,
            ])
        assert result.exit_code == 0, result.output

        # Verify batch was simulated with 4 items:
        # enable operator, setTwyneLiqLTV, deleverage, disable operator
        mock_sim_tx.assert_called_once()
        call_args = mock_sim_tx.call_args
        batch_items = call_args[0][2][0]  # args[2] = [batch_items]
        assert len(batch_items) == 4

    @patch("twyne_cli.commands.tx.simulate_tx")
    @patch("twyne_cli.commands.tx.evc_contract")
    @patch("twyne_cli.commands.tx.get_swap_quote", return_value=FAKE_SWAP_QUOTE)
    @patch("twyne_cli.commands.tx.deleverage_operator")
    @patch("twyne_cli.commands.tx.vault_manager")
    @patch("twyne_cli.commands.tx.erc20", side_effect=_mock_erc20_for_close)
    @patch("twyne_cli.commands.tx.credit_vault", side_effect=_mock_credit_vault_for_close)
    @patch("twyne_cli.commands.tx.collateral_vault", side_effect=_mock_cv_for_close)
    @patch("twyne_cli.commands.tx.resolve_account")
    def test_close_position_simulation_failure(
        self, mock_resolve, mock_cv, mock_credit, mock_erc20_fn,
        mock_vm, mock_delev_op, mock_swap_quote,
        mock_evc, mock_sim_tx,
    ):
        """Batch sim failure exits 1."""
        from twyne_cli.cli import cli

        mock_resolve.return_value = MagicMock(address="0xSENDER")
        mock_delev_op.return_value = MagicMock(address=FAKE_OPERATOR_ADDR)
        mock_evc.return_value = MagicMock(address=FAKE_EVC_ADDR)
        mock_vm.return_value = MagicMock(**{"externalLiqBuffers.return_value": 9500})
        mock_sim_tx.return_value = {"success": False, "error": "InsufficientCollateral"}

        runner = CliRunner()
        with patch("twyne_cli.context.TwyneContext.connect"), \
             patch("twyne_cli.context.TwyneContext.disconnect"):
            result = runner.invoke(cli, [
                "tx", "operators", "close-position",
                "0xVAULT",
                "--private-key", "deadbeef" * 8,
            ])
        assert result.exit_code != 0
        assert "Batch simulation failed" in result.output
        assert "InsufficientCollateral" in result.output
