"""Interactive onboarding — guided first-time setup for Twyne CLI."""

import os
import shutil
import subprocess

import click

from .config import CONFIG_FILE, load_config, save_config


@click.command()
@click.pass_context
def init(ctx):
    """Interactive setup for Twyne CLI."""
    click.echo("Welcome to Twyne CLI setup!\n")

    cfg = load_config()

    # --- Step 1: RPC URL ---
    click.echo("Step 1: RPC URL")
    click.echo("-" * 30)

    existing_rpc = cfg.get("rpc_url")
    if existing_rpc:
        click.echo(f"  Current RPC: {existing_rpc}")
        if click.confirm("  Change RPC URL?", default=False):
            _prompt_rpc(cfg)
    else:
        click.echo("  No custom RPC configured (using Ape default: MEV Blocker RPC).")
        if click.confirm("  Set a custom RPC URL?", default=False):
            _prompt_rpc(cfg)
        else:
            click.echo("  Keeping default RPC.\n")

    # --- Step 2: Ape accounts ---
    click.echo("Step 2: Signing Account")
    click.echo("-" * 30)

    try:
        from ape import accounts

        aliases = list(accounts.aliases)
    except Exception:
        aliases = []

    if aliases:
        click.echo(f"  Found {len(aliases)} existing account(s): {', '.join(aliases)}")
        existing_default = cfg.get("default_account")
        if existing_default:
            click.echo(f"  Default account: {existing_default}")
        choices = ["use-existing", "import", "generate", "skip"]
        choice = click.prompt(
            "  What would you like to do?",
            type=click.Choice(choices, case_sensitive=False),
            default="use-existing",
        )
        if choice == "use-existing":
            _prompt_default_account(cfg, aliases)
        elif choice == "skip":
            click.echo("  Skipping account setup.\n")
        else:
            alias = click.prompt("  Account alias", type=str)
            _run_ape_accounts(choice, alias)
            _set_default_after_create(cfg, alias)
    else:
        click.echo("  No Ape accounts found.")
        choice = click.prompt(
            "  What would you like to do?",
            type=click.Choice(["import", "generate", "skip"], case_sensitive=False),
            default="skip",
        )
        if choice == "skip":
            click.echo("  Skipping account setup.\n")
        else:
            alias = click.prompt("  Account alias", type=str)
            _run_ape_accounts(choice, alias)
            _set_default_after_create(cfg, alias)

    # --- Summary ---
    cfg = load_config()  # Re-read in case ape modified something
    click.echo("Setup Complete!")
    click.echo("=" * 30)

    rpc = cfg.get("rpc_url")
    click.echo(f"  RPC URL:         {rpc or '(Ape default)'}")
    click.echo(f"  Default account: {cfg.get('default_account') or '(none)'}")
    click.echo(f"  Config file:     {CONFIG_FILE}")
    click.echo()

    shell = _detect_shell()
    completion_line = _completion_instructions(shell)
    click.echo("Next steps:")
    click.echo(f"  {completion_line}")
    click.echo("      ^ add this to your shell config for tab-completion")
    click.echo("  twyne protocol overview    — view protocol parameters")
    click.echo("  twyne config --help        — manage settings")


def _prompt_rpc(cfg: dict) -> None:
    """Prompt for an RPC URL, validate, and save."""
    url = click.prompt("  RPC URL", type=str)
    if not url.startswith(("http://", "https://")):
        click.echo("  Warning: URL should start with http:// or https://")
        if not click.confirm("  Save anyway?", default=False):
            click.echo("  RPC not changed.\n")
            return
    cfg["rpc_url"] = url
    save_config(cfg)
    click.echo("  RPC URL saved.\n")


def _prompt_default_account(cfg: dict, aliases: list[str]) -> None:
    """Prompt to pick a default account from existing aliases."""
    alias = click.prompt(
        "  Account alias",
        type=click.Choice(aliases, case_sensitive=False),
    )
    cfg["default_account"] = alias
    save_config(cfg)
    click.echo(f"  Default account set to '{alias}'.\n")


def _set_default_after_create(cfg: dict, alias: str) -> None:
    """After import/generate, check if the alias exists and save as default."""
    try:
        from ape import accounts

        aliases = list(accounts.aliases)
    except Exception:
        aliases = []
    if alias in aliases:
        cfg["default_account"] = alias
        save_config(cfg)
        click.echo(f"  Default account set to '{alias}'.\n")
    else:
        click.echo()


def _detect_shell() -> str:
    """Detect the user's current shell."""
    shell_path = os.environ.get("SHELL", "")
    basename = os.path.basename(shell_path)
    if basename in ("bash", "zsh", "fish"):
        return basename
    return "bash"


def _completion_instructions(shell: str) -> str:
    """Return the setup line for the given shell."""
    if shell == "fish":
        return "twyne completion fish > ~/.config/fish/completions/twyne.fish"
    return f'eval "$(twyne completion {shell})"'


def _run_ape_accounts(action: str, alias: str) -> None:
    """Delegate to `ape accounts import|generate <alias>` via subprocess."""
    ape_bin = shutil.which("ape")
    if not ape_bin:
        click.echo("  Error: 'ape' not found on PATH. Install it with: pip install eth-ape")
        return
    cmd = [ape_bin, "accounts", action, alias]
    click.echo(f"  Running: ape accounts {action} {alias}")
    click.echo("  (Follow the prompts below)\n")
    subprocess.run(cmd, check=False)
