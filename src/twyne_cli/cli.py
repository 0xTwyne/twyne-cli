"""Twyne CLI entry point — Click group with global flags."""

import os

import click

from .chains import resolve_chain, set_active_chain, supported_slugs
from .commands.config import load_config, resolve_rpc_for_chain
from .context import TwyneContext
from .exceptions import ChainNotSupportedError


def _validate_chain(ctx, param, value):
    if value is None:
        return None
    try:
        return resolve_chain(value)
    except ChainNotSupportedError as e:
        raise click.BadParameter(str(e), ctx=ctx, param=param) from None


@click.group()
@click.option(
    "--chain",
    "chain_value",
    callback=_validate_chain,
    default=None,
    help=f"Target chain — slug ({'/'.join(supported_slugs())}) or chain id (default: mainnet)",
)
@click.option("--rpc", default=None, help="RPC URL (overrides env/config for the active chain)")
@click.option("--json", "force_json", is_flag=True, default=False, help="Force JSON output")
@click.option("--block", type=int, default=None, help="Query at specific block number")
@click.option("--no-cache", is_flag=True, default=False, help="Bypass vault cache, force full rescan")
@click.option("--verbose", "-v", is_flag=True, default=False, help="Show detailed error info (revert reasons, call traces)")
@click.version_option(package_name="twyne-cli")
@click.pass_context
def cli(ctx, chain_value, rpc, force_json, block, no_cache, verbose):
    """Twyne Protocol CLI — query on-chain state."""
    cfg = load_config()
    chain = chain_value or resolve_chain(cfg.get("default_chain"))
    set_active_chain(chain)

    # Resolution: --rpc flag > RPC_URL_<chain_id> > legacy RPC_URL (mainnet only) > config > chain default
    rpc_url = rpc or os.environ.get(chain.env_var)
    if not rpc_url and chain.chain_id == 1:
        rpc_url = os.environ.get("RPC_URL")
    if not rpc_url:
        rpc_url = resolve_rpc_for_chain(cfg, chain)
    if not rpc_url and chain.default_rpc:
        rpc_url = chain.default_rpc

    ctx.ensure_object(dict)
    ctx.obj = TwyneContext(
        rpc_url=rpc_url,
        force_json=force_json,
        block=block,
        no_cache=no_cache,
        verbose=verbose,
        chain=chain,
    )


# Import and register subcommands after cli is defined to avoid circular imports
from .commands.completion import completion  # noqa: E402
from .commands.config import config  # noqa: E402
from .commands.init import init  # noqa: E402
from .commands.protocol import protocol  # noqa: E402
from .commands.tx import tx  # noqa: E402
from .commands.user import user  # noqa: E402
from .commands.vault import vault  # noqa: E402

cli.add_command(completion)
cli.add_command(vault)
cli.add_command(protocol)
cli.add_command(user)
cli.add_command(config)
cli.add_command(tx)
cli.add_command(init)
