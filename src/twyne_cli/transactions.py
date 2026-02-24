"""Shared transaction helpers — account resolution, simulation, submission."""

import os

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
        from ape_test.accounts import TestAccount
        from eth_account import Account as EthAccount

        eth_acct = EthAccount.from_key(pk)
        return TestAccount(index=0, address_str=eth_acct.address, private_key=pk)

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


def execute_through_evc(contract, fn_name: str, args: list, sender):
    """Execute a contract function routed through the Twyne EVC.

    Twyne contracts with the callThroughEVC modifier (CollateralVault,
    CollateralVaultFactory) require msg.sender == EVC. Direct calls revert
    with EVC_EmptyError. This helper encodes the calldata and routes it
    via evc.call() using the Twyne EVC (not Euler's EVC).
    """
    from .contracts import evc as evc_contract

    evc_instance = evc_contract()  # Uses Twyne EVC from address registry
    calldata = getattr(contract, fn_name).encode_input(*args)
    return evc_instance.call(
        str(contract.address), str(sender.address), 0, calldata, sender=sender
    )


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


def ensure_allowance(
    token_address: str,
    spender: str,
    amount: int,
    sender,
    skip_confirm: bool = False,
    skip_approval: bool = False,
    max_approve: bool = False,
) -> bool:
    """Check token allowance and send approval tx if insufficient.

    Approvals are sent as DIRECT transactions (not EVC-routed) because
    msg.sender must be the user's EOA for the allowance to be set correctly.

    Returns True if allowance is sufficient (already or after approval).
    Returns False if user declined the approval prompt.
    Raises click.UsageError if skip_approval=True and allowance is insufficient.
    """
    from .contracts import erc20

    token = erc20(token_address)
    current = token.allowance(str(sender.address), spender)

    if current >= amount:
        return True

    if skip_approval:
        symbol = token.symbol()
        raise click.UsageError(
            f"Insufficient allowance for {symbol} ({token_address}). "
            f"Approve spender {spender} manually or remove --skip-approval."
        )

    symbol = token.symbol()
    decimals = token.decimals()
    approve_amount = 2**256 - 1 if max_approve else amount

    # Format human-readable amount
    if max_approve:
        display_amount = "unlimited (max uint256)"
    else:
        whole = amount // 10**decimals
        frac = amount % 10**decimals
        display_amount = f"{whole}.{str(frac).zfill(decimals).rstrip('0') or '0'}"

    details = [
        ("Token", f"{symbol} ({token_address})"),
        ("Spender", spender),
        ("Amount", f"{display_amount} (raw: {approve_amount})"),
    ]

    if not confirm_prompt("Token Approval", details, skip=skip_confirm):
        return False

    receipt = token.approve(spender, approve_amount, sender=sender)
    click.echo("\nApproval Result:")
    click.echo("-" * 40)
    click.echo(format_receipt(receipt))
    click.echo()
    return True
