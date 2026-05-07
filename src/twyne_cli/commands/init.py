"""Interactive onboarding — guided first-time setup for Twyne CLI."""

import os
import shutil
import subprocess

import click

from ..chains import resolve_chain, supported_slugs
from .config import CONFIG_FILE, load_config, save_config


@click.command()
@click.pass_context
def init(ctx):
    """Interactive setup for Twyne CLI."""
    click.echo("Welcome to Twyne CLI setup!\n")

    cfg = load_config()
    if "rpc_url" in cfg and "rpc_urls" not in cfg:
        # Migrate legacy single-chain config in place.
        cfg["rpc_urls"] = {"mainnet": cfg.pop("rpc_url")}
        save_config(cfg)

    # --- Step 1: Default chain ---
    click.echo("Step 1: Default chain")
    click.echo("-" * 30)

    existing_default = cfg.get("default_chain", "mainnet")
    click.echo(f"  Current default chain: {existing_default}")
    if click.confirm("  Change default chain?", default=False):
        chain_slug = click.prompt(
            "  Default chain",
            type=click.Choice(supported_slugs(), case_sensitive=False),
            default=existing_default,
        )
        cfg["default_chain"] = resolve_chain(chain_slug).slug
        save_config(cfg)
        click.echo(f"  Default chain set to '{cfg['default_chain']}'.\n")
    else:
        click.echo()

    # --- Step 2: RPC URL per chain ---
    click.echo("Step 2: RPC URLs")
    click.echo("-" * 30)

    rpc_urls = cfg.get("rpc_urls", {})
    for slug in supported_slugs():
        existing = rpc_urls.get(slug)
        chain = resolve_chain(slug)
        if existing:
            click.echo(f"  [{slug}] Current: {existing}")
            if click.confirm(f"  Change RPC for {slug}?", default=False):
                _prompt_rpc_for_chain(cfg, slug, chain.default_rpc)
        else:
            default_hint = f" (default: {chain.default_rpc})" if chain.default_rpc else ""
            click.echo(f"  [{slug}] No RPC configured{default_hint}.")
            if click.confirm(f"  Configure RPC for {slug}?", default=False):
                _prompt_rpc_for_chain(cfg, slug, chain.default_rpc)
    click.echo()

    # --- Step 3: Ape accounts ---
    click.echo("Step 3: Signing Account")
    click.echo("-" * 30)

    try:
        from ape import accounts

        aliases = list(accounts.aliases)
    except Exception:
        aliases = []

    if aliases:
        click.echo(f"  Found {len(aliases)} existing account(s): {', '.join(aliases)}")
        existing_default_acct = cfg.get("default_account")
        if existing_default_acct:
            click.echo(f"  Default account: {existing_default_acct}")
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

    click.echo(f"  Default chain:   {cfg.get('default_chain', 'mainnet')}")
    rpc_urls = cfg.get("rpc_urls", {})
    if rpc_urls:
        click.echo("  RPC URLs:")
        for slug, url in rpc_urls.items():
            click.echo(f"    {slug:10s} {url}")
    else:
        click.echo("  RPC URLs:        (Ape defaults / env vars)")
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


def _prompt_rpc_for_chain(cfg: dict, slug: str, default: str | None) -> None:
    """Prompt for an RPC URL for a chain, validate, save."""
    url = click.prompt("  RPC URL", type=str, default=default or "", show_default=bool(default))
    if not url:
        click.echo("  RPC not changed.\n")
        return
    if not url.startswith(("http://", "https://")):
        click.echo("  Warning: URL should start with http:// or https://")
        if not click.confirm("  Save anyway?", default=False):
            click.echo("  RPC not changed.\n")
            return
    cfg.setdefault("rpc_urls", {})[slug] = url
    save_config(cfg)
    click.echo(f"  RPC URL for '{slug}' saved.\n")


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
