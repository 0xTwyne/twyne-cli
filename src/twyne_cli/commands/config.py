"""Config commands — persist CLI settings (e.g. custom RPC URL).

Multi-chain config schema:

    {
        "rpc_urls": {"mainnet": "https://...", "megaeth": "https://..."},
        "default_chain": "mainnet",
        "default_account": "<alias>"
    }

Back-compat: a top-level "rpc_url" (legacy single-chain) is read as the mainnet
RPC and migrated to rpc_urls on the next save.
"""

import json
from pathlib import Path

import click

CONFIG_DIR = Path.home() / ".config" / "twyne"
CONFIG_FILE = CONFIG_DIR / "config.json"


def load_config() -> dict:
    """Load config from disk, returning empty dict if missing."""
    if CONFIG_FILE.exists():
        return json.loads(CONFIG_FILE.read_text())
    return {}


def save_config(cfg: dict) -> None:
    """Write config to disk."""
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    CONFIG_FILE.write_text(json.dumps(cfg, indent=2) + "\n")


def _migrate_legacy(cfg: dict) -> dict:
    """Translate legacy {'rpc_url': ...} into the multi-chain schema."""
    if "rpc_url" in cfg and "rpc_urls" not in cfg:
        cfg["rpc_urls"] = {"mainnet": cfg.pop("rpc_url")}
    return cfg


def resolve_rpc_for_chain(cfg: dict, chain) -> str | None:
    """Return the saved RPC URL for the given ChainSpec, or None."""
    if "rpc_urls" in cfg and isinstance(cfg["rpc_urls"], dict):
        return cfg["rpc_urls"].get(chain.slug)
    if chain.chain_id == 1:
        return cfg.get("rpc_url")
    return None


def _chain_option(default: str | None = None):
    from ..chains import supported_slugs

    return click.option(
        "--chain",
        "chain_slug",
        type=click.Choice(supported_slugs(), case_sensitive=False),
        default=default,
        help="Chain to apply this setting to (default: mainnet)",
    )


@click.group()
def config():
    """Manage CLI configuration."""


@config.command("set-rpc")
@click.argument("url")
@_chain_option(default="mainnet")
def set_rpc(url, chain_slug):
    """Save a custom RPC URL for the given chain.

    Example: twyne config set-rpc https://eth-mainnet.g.alchemy.com/v2/YOUR_KEY
             twyne config set-rpc --chain megaeth https://mainnet.megaeth.com/rpc
    """
    cfg = _migrate_legacy(load_config())
    cfg.setdefault("rpc_urls", {})[chain_slug] = url
    save_config(cfg)
    click.echo(f"RPC URL for '{chain_slug}' saved to {CONFIG_FILE}")


@config.command("get-rpc")
@_chain_option(default=None)
def get_rpc(chain_slug):
    """Show the configured RPC URL for one or all chains."""
    cfg = _migrate_legacy(load_config())
    rpc_urls = cfg.get("rpc_urls", {})

    if chain_slug:
        rpc = rpc_urls.get(chain_slug)
        if rpc:
            click.echo(rpc)
        else:
            click.echo(f"No custom RPC configured for '{chain_slug}'.")
        return

    if not rpc_urls:
        click.echo("No custom RPCs configured.")
        return
    for slug, url in rpc_urls.items():
        click.echo(f"  {slug:10s} {url}")


@config.command("clear-rpc")
@_chain_option(default=None)
def clear_rpc(chain_slug):
    """Remove a saved RPC URL (one chain or all)."""
    cfg = _migrate_legacy(load_config())
    rpc_urls = cfg.get("rpc_urls", {})

    if chain_slug:
        if chain_slug in rpc_urls:
            del rpc_urls[chain_slug]
            cfg["rpc_urls"] = rpc_urls
            save_config(cfg)
            click.echo(f"RPC URL for '{chain_slug}' cleared.")
        else:
            click.echo(f"No RPC was configured for '{chain_slug}'.")
        return

    if rpc_urls:
        cfg.pop("rpc_urls", None)
        save_config(cfg)
        click.echo("All saved RPC URLs cleared.")
    else:
        click.echo("No custom RPCs were configured.")


@config.command("set-default-chain")
@click.argument("chain_slug")
def set_default_chain(chain_slug):
    """Set the default chain used when --chain is omitted.

    Example: twyne config set-default-chain mainnet
    """
    from ..chains import resolve_chain
    from ..exceptions import ChainNotSupportedError

    try:
        chain = resolve_chain(chain_slug)
    except ChainNotSupportedError as e:
        raise click.UsageError(str(e)) from None

    cfg = _migrate_legacy(load_config())
    cfg["default_chain"] = chain.slug
    save_config(cfg)
    click.echo(f"Default chain set to '{chain.slug}'.")


@config.command("get-default-chain")
def get_default_chain():
    """Show the default chain (mainnet if unset)."""
    cfg = load_config()
    click.echo(cfg.get("default_chain", "mainnet"))


@config.command("set-account")
@click.argument("alias")
def set_account(alias):
    """Save a default signing account alias for transaction commands.

    Example: twyne config set-account my-wallet
    """
    cfg = _migrate_legacy(load_config())
    cfg["default_account"] = alias
    save_config(cfg)
    click.echo(f"Default account set to '{alias}' (saved to {CONFIG_FILE})")


@config.command("get-account")
def get_account():
    """Show the currently configured default signing account."""
    cfg = load_config()
    acct = cfg.get("default_account")
    if acct:
        click.echo(acct)
    else:
        click.echo("No default account configured. Use 'twyne config set-account <alias>'.")
