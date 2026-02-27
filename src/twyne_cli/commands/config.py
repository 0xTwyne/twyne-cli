"""Config commands — persist CLI settings (e.g. custom RPC URL)."""

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


@click.group()
def config():
    """Manage CLI configuration."""


@config.command("set-rpc")
@click.argument("url")
def set_rpc(url):
    """Save a custom RPC URL for all future commands.

    Example: twyne config set-rpc https://eth-mainnet.g.alchemy.com/v2/YOUR_KEY
    """
    cfg = load_config()
    cfg["rpc_url"] = url
    save_config(cfg)
    click.echo(f"RPC URL saved to {CONFIG_FILE}")


@config.command("get-rpc")
def get_rpc():
    """Show the currently configured RPC URL."""
    cfg = load_config()
    rpc = cfg.get("rpc_url")
    if rpc:
        click.echo(rpc)
    else:
        click.echo("No custom RPC configured. Using Ape default provider.")


@config.command("clear-rpc")
def clear_rpc():
    """Remove the saved RPC URL (reverts to Ape default provider)."""
    cfg = load_config()
    if "rpc_url" in cfg:
        del cfg["rpc_url"]
        save_config(cfg)
        click.echo("RPC URL cleared. Will use Ape default provider.")
    else:
        click.echo("No custom RPC was configured.")


@config.command("set-account")
@click.argument("alias")
def set_account(alias):
    """Save a default signing account alias for transaction commands.

    Example: twyne config set-account my-wallet
    """
    cfg = load_config()
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
