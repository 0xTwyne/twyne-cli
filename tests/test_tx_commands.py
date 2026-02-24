"""Tests for tx command group structure."""

from click.testing import CliRunner


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
