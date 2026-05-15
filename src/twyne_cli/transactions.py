"""Shared transaction helpers — account resolution, simulation, submission."""

import os
import stat
import sys

import click


def _read_key_file(path: str) -> str:
    """Read a private key from a file, enforcing mode 0600.

    Refuses files readable by group or other (any of `g+rwx` / `o+rwx` set).
    Strips trailing whitespace/newline and an optional leading `0x`.
    """
    st = os.stat(path)
    if st.st_mode & 0o077:
        actual = stat.filemode(st.st_mode)
        raise click.UsageError(
            f"--private-key-file refuses to read {path}: insecure permissions ({actual}). "
            f"Fix with: chmod 600 {path}"
        )
    with open(path) as f:
        return f.readline().strip()


def _prompt_for_key() -> str:
    """Prompt interactively for a private key, no-echo, or read one line from piped stdin."""
    if sys.stdin.isatty():
        return click.prompt("Private key", hide_input=True).strip()
    line = sys.stdin.readline().strip()
    if not line:
        raise click.UsageError(
            "No signing key on stdin. Pipe a key (echo $KEY | twyne tx ...) or run interactively."
        )
    return line


def resolve_account(
    account_alias: str | None,
    private_key: str | None,
    private_key_file: str | None = None,
):
    """Resolve a signing account.

    Precedence:
      1. --account <alias>             (Ape keystore — most secure persistent option)
      2. --private-key-file <path>     (file, mode 0600 required)
      3. --private-key <key>           (DEPRECATED — leaks via shell history / ps / log aggregators)
      4. PRIVATE_KEY env var
      5. Interactive TTY prompt (no echo), or one-line stdin read when piped
      6. config default_account

    Returns an Ape AccountAPI instance.
    """
    from ape import accounts

    if account_alias:
        return accounts.load(account_alias)

    pk: str | None = None
    if private_key_file:
        pk = _read_key_file(private_key_file)
    elif private_key:
        click.echo(
            "WARNING: --private-key is deprecated. The flag exposes the key to shell history, "
            "process listings (ps), and log aggregators. Prefer --account (Ape keystore), "
            "--private-key-file (chmod 600), the PRIVATE_KEY env var (set via 'read -s'), "
            "or the interactive prompt (omit all key flags).",
            err=True,
        )
        pk = private_key
    elif env_pk := os.environ.get("PRIVATE_KEY"):
        pk = env_pk
    else:
        # Fall back to config default_account before prompting
        from .commands.config import load_config

        default = load_config().get("default_account")
        if default:
            return accounts.load(default)
        pk = _prompt_for_key()

    if not pk:
        raise click.UsageError(
            "No signing account specified. Use --account <alias>, --private-key-file <path>, "
            "set PRIVATE_KEY env var, or run interactively to be prompted.\n"
            "Tip: run 'twyne init' to set up a default account."
        )

    if not pk.startswith("0x"):
        pk = "0x" + pk
    from ape_test.accounts import TestAccount
    from eth_account import Account as EthAccount

    eth_acct = EthAccount.from_key(pk)
    return TestAccount(index=0, address_str=eth_acct.address, private_key=pk)


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


def _format_error_details(e) -> dict:
    """Extract structured debug info from a ContractLogicError."""
    info = {"error": str(e)}
    if getattr(e, "revert_message", None):
        info["revert_message"] = e.revert_message
    if getattr(e, "dev_message", None):
        info["dev_message"] = e.dev_message
    if getattr(e, "contract_address", None):
        info["contract_address"] = str(e.contract_address)
    if getattr(e, "source_traceback", None):
        info["source_traceback"] = str(e.source_traceback)
    return info


def simulate_tx(contract, fn_name: str, args: list, sender=None) -> dict:
    """Simulate a transaction via eth_call. Returns gas estimate or raises on revert."""
    from ape.exceptions import ContractLogicError

    fn = getattr(contract, fn_name)
    try:
        result = fn.call(*args, sender=sender)
        return {"success": True, "result": result}
    except ContractLogicError as e:
        return {"success": False, **_format_error_details(e)}
    except Exception as e:
        # Ape's trace enrichment can crash (e.g. ValueError in _enrich_calldata)
        # before ContractLogicError is raised. Catch broadly to surface the revert.
        msg = f"execution reverted (detail unavailable: {type(e).__name__}: {e})"
        return {"success": False, "error": msg}


def simulate_through_evc(contract, fn_name: str, args: list, sender) -> dict:
    """Simulate a contract call routed through the Twyne EVC (eth_call).

    Same routing as execute_through_evc but read-only. Required for contracts
    with callThroughEVC modifier (CollateralVault, CollateralVaultFactory).
    Direct eth_call may work for simple cases but fails when initialization
    logic requires proper EVC authentication context (e.g., Aave vaults).
    """
    from ape.exceptions import ContractLogicError

    from .contracts import evc as evc_contract

    evc_instance = evc_contract()
    calldata = getattr(contract, fn_name).encode_input(*args)
    evc_call_fn = getattr(evc_instance, "call")
    try:
        result = evc_call_fn.call(
            str(contract.address), str(sender.address), 0, calldata, sender=sender,
        )
        return {"success": True, "result": result}
    except ContractLogicError as e:
        return {"success": False, **_format_error_details(e)}
    except Exception as e:
        msg = f"execution reverted (detail unavailable: {type(e).__name__}: {e})"
        return {"success": False, "error": msg}


def execute_through_evc(contract, fn_name: str, args: list, sender, **gas_kwargs):
    """Execute a contract function routed through the Twyne EVC.

    Twyne contracts with the callThroughEVC modifier (CollateralVault,
    CollateralVaultFactory) require msg.sender == EVC. Direct calls revert
    with EVC_EmptyError. This helper encodes the calldata and routes it
    via evc.call() using the Twyne EVC (not Euler's EVC).

    Accepts optional gas_kwargs: max_priority_fee, gas (gas limit).
    Internal key _gas_multiplier is handled automatically.
    """
    from .contracts import evc as evc_contract

    # Filter internal keys and apply gas multiplier if present
    extra = {k: v for k, v in gas_kwargs.items() if k != "_gas_multiplier"}
    multiplier = gas_kwargs.get("_gas_multiplier")

    evc_instance = evc_contract()  # Uses Twyne EVC from address registry
    calldata = getattr(contract, fn_name).encode_input(*args)

    if multiplier is not None:
        try:
            estimate = evc_instance.call.estimate_gas_cost(
                str(contract.address), str(sender.address), 0, calldata, sender=sender, **extra,
            )
            extra["gas"] = int(estimate * multiplier)
        except Exception:
            pass  # Fall back to ape-config.yaml defaults

    return evc_instance.call(
        str(contract.address), str(sender.address), 0, calldata,
        sender=sender, **extra,
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
    **gas_kwargs,
) -> bool:
    """Check token allowance and send approval tx if insufficient.

    Approvals are sent as DIRECT transactions (not EVC-routed) because
    msg.sender must be the user's EOA for the allowance to be set correctly.

    Returns True if allowance is sufficient (already or after approval).
    Returns False if user declined the approval prompt.
    Raises click.UsageError if skip_approval=True and allowance is insufficient.

    Accepts optional gas_kwargs: max_priority_fee, gas (gas limit).
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

    # Filter internal keys and apply gas multiplier if present
    extra = {k: v for k, v in gas_kwargs.items() if k != "_gas_multiplier"}
    multiplier = gas_kwargs.get("_gas_multiplier")
    if multiplier is not None:
        try:
            estimate = token.approve.estimate_gas_cost(spender, approve_amount, sender=sender, **extra)
            extra["gas"] = int(estimate * multiplier)
        except Exception:
            pass

    receipt = token.approve(spender, approve_amount, sender=sender, **extra)
    click.echo("\nApproval Result:")
    click.echo("-" * 40)
    click.echo(format_receipt(receipt))
    click.echo()
    return True
