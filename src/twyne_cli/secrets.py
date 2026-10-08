"""User-supplied secrets (API keys) — looked up locally, never bundled, never printed.

twyne-cli ships no API keys. A feature that needs one (Enso swap routes) reads
the key from the user's own environment, in this order:

1. The process environment (``export ENSO_API_KEY=...``, or ``op run -- twyne ...``).
2. ``.env`` in the current working directory.
3. ``~/.config/twyne/.env`` (create it with mode 600).

Values are returned to the caller only. Do not log or echo them.
"""

from __future__ import annotations

import os
import stat
from pathlib import Path

USER_ENV_FILE = Path.home() / ".config" / "twyne" / ".env"

ENSO_KEY_HELP = (
    "This command needs an Enso API key for swap routing. twyne-cli does not ship one — "
    "use your own:\n"
    "  1. Get a key at https://developers.enso.build (Enso developer dashboard).\n"
    "  2. Make it available locally, for example:\n"
    "       export ENSO_API_KEY=...                     # current shell only\n"
    "       op run --env-file=<file with op:// ref> -- twyne ...   # 1Password\n"
    "       echo 'ENSO_API_KEY=...' >> ~/.config/twyne/.env && chmod 600 ~/.config/twyne/.env\n"
    "  Never commit the key to a repository."
)


def _read_env_file(path: Path, key: str) -> str | None:
    if not path.is_file():
        return None
    for raw in path.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        k = k.strip()
        if k.startswith("export "):
            k = k[len("export "):].strip()
        if k == key:
            return v.strip().strip('"').strip("'") or None
    return None


def _warn_if_shared(path: Path) -> None:
    try:
        mode = path.stat().st_mode
    except OSError:
        return
    if mode & (stat.S_IRGRP | stat.S_IROTH):
        import click

        click.echo(f"Warning: {path} is readable by other users. Run: chmod 600 {path}", err=True)


def lookup_secret(key: str) -> str | None:
    """Return the value of ``key`` from env, ./.env or ~/.config/twyne/.env (or None)."""
    val = os.environ.get(key)
    if val:
        return val
    val = _read_env_file(Path.cwd() / ".env", key)
    if val:
        return val
    val = _read_env_file(USER_ENV_FILE, key)
    if val:
        _warn_if_shared(USER_ENV_FILE)
    return val


def require_enso_key() -> str:
    """Return the user's Enso API key or stop with setup instructions."""
    key = lookup_secret("ENSO_API_KEY")
    if not key:
        import click

        raise click.ClickException(ENSO_KEY_HELP)
    return key
