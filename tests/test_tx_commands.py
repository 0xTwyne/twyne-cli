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
