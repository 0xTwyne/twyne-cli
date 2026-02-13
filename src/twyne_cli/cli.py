"""Twyne CLI entry point — Click group with global flags."""

import click

from .commands.config import load_config
from .context import TwyneContext


@click.group()
@click.option("--rpc", envvar="RPC_URL", default=None, help="Ethereum RPC URL (default: $RPC_URL or Ape default)")
@click.option("--json", "force_json", is_flag=True, default=False, help="Force JSON output")
@click.option("--block", type=int, default=None, help="Query at specific block number")
@click.version_option(package_name="twyne-cli")
@click.pass_context
def cli(ctx, rpc, force_json, block):
    """Twyne Protocol CLI — query on-chain state."""
    # Resolution order: --rpc flag > $RPC_URL env var > ~/.config/twyne/config.json > Ape default
    rpc_url = rpc or load_config().get("rpc_url")
    ctx.ensure_object(dict)
    ctx.obj = TwyneContext(rpc_url=rpc_url, force_json=force_json, block=block)


# Import and register subcommands after cli is defined to avoid circular imports
from .commands.config import config  # noqa: E402
from .commands.protocol import protocol  # noqa: E402
from .commands.user import user  # noqa: E402
from .commands.vault import vault  # noqa: E402

cli.add_command(vault)
cli.add_command(protocol)
cli.add_command(user)
cli.add_command(config)
