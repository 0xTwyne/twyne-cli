"""TTY-aware output formatting — tables for humans, JSON when piped."""

import json
import sys

import click


def is_tty() -> bool:
    """Check if stdout is a terminal."""
    return sys.stdout.isatty()


def _default_serializer(obj):
    """JSON serializer for types not handled by default."""
    if isinstance(obj, int) and obj > 2**53:
        return str(obj)
    return str(obj)


def output_json(data: dict | list):
    """Print data as formatted JSON."""
    click.echo(json.dumps(data, indent=2, default=_default_serializer))


def output_table(headers: list[str], rows: list[list[str]], title: str | None = None):
    """Print a simple aligned table using click.echo."""
    if title:
        click.echo(f"\n{title}")
        click.echo("=" * max(len(title), 40))

    if not rows:
        click.echo("  (no data)")
        return

    # Calculate column widths
    widths = [len(h) for h in headers]
    for row in rows:
        for i, cell in enumerate(row):
            if i < len(widths):
                widths[i] = max(widths[i], len(str(cell)))

    # Header
    header_line = "  ".join(h.ljust(widths[i]) for i, h in enumerate(headers))
    click.echo(f"\n  {header_line}")
    click.echo(f"  {'  '.join('-' * w for w in widths)}")

    # Rows
    for row in rows:
        line = "  ".join(str(cell).ljust(widths[i]) for i, cell in enumerate(row))
        click.echo(f"  {line}")

    click.echo()


def output_kv(pairs: list[tuple[str, str]], title: str | None = None):
    """Print key-value pairs aligned."""
    if title:
        click.echo(f"\n{title}")
        click.echo("=" * max(len(title), 40))

    if not pairs:
        return

    max_key = max(len(k) for k, _ in pairs)
    for key, value in pairs:
        click.echo(f"  {key.ljust(max_key)}  {value}")

    click.echo()


def format_hf(raw: int) -> str:
    """Format a raw 1e18 health factor for display."""
    if raw == 2**256 - 1:
        return "inf (no debt)"
    val = raw / 1e18
    if val >= 100:
        return f"{val:,.0f}"
    return f"{val:.4f}"


def format_usd(value: float) -> str:
    """Format a USD value."""
    if abs(value) < 0.01:
        return "$0.00"
    return f"${value:,.2f}"


def format_bps(raw: int) -> str:
    """Format basis points (1e4) as percentage."""
    return f"{raw / 100:.2f}%"


def format_address(addr: str) -> str:
    """Shorten an address for table display."""
    if len(addr) >= 42:
        return f"{addr[:6]}...{addr[-4:]}"
    return addr


def risk_level(hf_raw: int) -> str:
    """Classify health factor into risk level."""
    if hf_raw == 2**256 - 1:
        return "SAFE"
    hf = hf_raw / 1e18
    if hf >= 2.0:
        return "SAFE"
    if hf >= 1.5:
        return "LOW"
    if hf >= 1.2:
        return "MEDIUM"
    if hf >= 1.0:
        return "HIGH"
    return "LIQUIDATABLE"
