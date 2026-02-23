"""Shared transaction helpers — account resolution, simulation, submission."""

import os
import sys

import click
from ape.exceptions import ContractLogicError


def resolve_account(account_alias: str | None, private_key: str | None):
    """Resolve a signing account from alias or private key.

    Priority: --account alias > --private-key flag > PRIVATE_KEY env var.
    Returns an Ape AccountAPI instance.
    """
    from ape import accounts

    if account_alias:
        return accounts.load(account_alias)

    pk = private_key or os.environ.get("PRIVATE_KEY")
    if pk:
        if not pk.startswith("0x"):
            pk = "0x" + pk
        from eth_account import Account as EthAccount
        eth_acct = EthAccount.from_key(pk)
        for acct in accounts:
            if acct.address.lower() == eth_acct.address.lower():
                return acct
        from ape_accounts import import_account_from_private_key
        alias = f"_cli_ephemeral_{eth_acct.address[:8]}"
        try:
            return accounts.load(alias)
        except Exception:
            return import_account_from_private_key(alias, "", pk)

    raise click.UsageError(
        "No signing account specified. Use --account <alias> or --private-key <key> or set PRIVATE_KEY env var."
    )


def parse_amount(value: str, decimals: int, raw: bool = False) -> int:
    """Parse a human-readable amount string to raw integer.

    - "1.5" with 18 decimals -> 1500000000000000000
    - "max" -> uint256 max value
    - raw=True -> parse as raw integer
    """
    if value.lower() == "max":
        return 2**256 - 1

    if raw:
        return int(value)

    if "." in value:
        integer_part, frac_part = value.split(".", 1)
    else:
        integer_part, frac_part = value, ""

    frac_part = frac_part[:decimals].ljust(decimals, "0")
    return int(integer_part + frac_part)


def simulate_tx(contract, fn_name: str, args: list, sender=None) -> dict:
    """Simulate a transaction via eth_call. Returns gas estimate or raises on revert."""
    fn = getattr(contract, fn_name)
    try:
        result = fn.call(*args, sender=sender)
        return {"success": True, "result": result}
    except ContractLogicError as e:
        return {"success": False, "error": str(e)}


def confirm_prompt(action: str, details: list[tuple[str, str]], skip: bool = False) -> bool:
    """Display transaction details and prompt for confirmation."""
    click.echo(f"\nTransaction: {action}")
    click.echo("-" * 40)
    max_key = max(len(k) for k, _ in details) if details else 0
    for key, value in details:
        click.echo(f"  {key.ljust(max_key)}  {value}")
    click.echo()

    if skip:
        return True

    return click.confirm("Submit this transaction?", default=False)


def format_receipt(receipt) -> str:
    """Format a transaction receipt for display."""
    status = "Success" if receipt.status == 1 else "FAILED"
    lines = [
        f"  Status:       {status}",
        f"  Tx Hash:      {receipt.txn_hash}",
        f"  Block:        {receipt.block_number}",
        f"  Gas Used:     {receipt.gas_used:,}",
    ]
    return "\n".join(lines)


def display_receipt(receipt):
    """Print formatted transaction receipt."""
    click.echo("\nTransaction Result:")
    click.echo("=" * 40)
    click.echo(format_receipt(receipt))
    click.echo()
