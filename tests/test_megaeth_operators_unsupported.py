"""MegaETH: every operator subcommand must exit non-zero with a clean message.

These tests do not require a fork or live RPC — the capability gate fires before
any RPC call. The integration-tier placement is intentional: this is a release
gate verifying that the user-facing UX never crashes with a stack trace on a
chain that lacks operator deployments.
"""

from __future__ import annotations

import pytest
from click.testing import CliRunner

ZERO = "0x0000000000000000000000000000000000000000"


@pytest.mark.parametrize(
    "args",
    [
        ["tx", "operators", "leverage", ZERO, "1"],
        ["tx", "operators", "leverage", ZERO, "1", "--protocol", "aave"],
        ["tx", "operators", "deleverage", ZERO, "1"],
        ["tx", "operators", "deleverage", ZERO, "1", "--protocol", "aave"],
        ["tx", "operators", "teleport", ZERO, ZERO],
        ["tx", "operators", "teleport", ZERO, ZERO, "--protocol", "aave"],
        ["tx", "operators", "close-position", ZERO],
        ["tx", "operators", "close-position", ZERO, "--protocol", "aave"],
    ],
    ids=[
        "leverage-default",
        "leverage-aave",
        "deleverage-default",
        "deleverage-aave",
        "teleport-default",
        "teleport-aave",
        "close-position-default",
        "close-position-aave",
    ],
)
def test_operator_subcommand_blocked(args):
    """Every operator subcommand on MegaETH exits non-zero with the expected message."""
    from twyne_cli.cli import cli

    runner = CliRunner()
    result = runner.invoke(cli, ["--chain", "megaeth", *args])
    assert result.exit_code != 0, f"unexpectedly succeeded: {result.output}"
    assert "Operator commands are not supported on MegaETH" in result.output
    assert "chain 4326" in result.output


def test_operators_help_renders_on_megaeth():
    """--help bypasses the group callback (standard click behaviour).

    Subcommands appear in help; the capability gate fires only when a subcommand
    is invoked — see test_operator_subcommand_blocked for that path.
    """
    from twyne_cli.cli import cli

    runner = CliRunner()
    result = runner.invoke(cli, ["--chain", "megaeth", "tx", "operators", "--help"])
    assert result.exit_code == 0
    for sub in ("leverage", "deleverage", "teleport", "close-position"):
        assert sub in result.output


def test_operators_unblocked_on_mainnet():
    """Sanity: --chain mainnet should NOT trigger the gate."""
    from twyne_cli.cli import cli

    runner = CliRunner()
    result = runner.invoke(cli, ["--chain", "mainnet", "tx", "operators", "--help"])
    assert result.exit_code == 0
    for sub in ("leverage", "deleverage", "teleport", "close-position"):
        assert sub in result.output
