"""Transaction commands — write operations for Twyne protocol."""

import click

from ..context import TwyneContext, pass_ctx
from ..contracts import collateral_vault, erc20
from ..formatting import format_address
from ..transactions import (
    confirm_prompt,
    display_receipt,
    parse_amount,
    resolve_account,
    simulate_tx,
)


# --------------------------------------------------------------------------- #
# Shared options for all tx commands
# --------------------------------------------------------------------------- #

def tx_options(f):
    """Shared options for all transaction commands."""
    f = click.option("--account", "account_alias", default=None,
                     help="Ape account alias (from 'ape accounts import')")(f)
    f = click.option("--private-key", "private_key", default=None,
                     help="Raw private key (or set PRIVATE_KEY env var)")(f)
    f = click.option("--dry-run", is_flag=True, default=False,
                     help="Simulate only, don't submit")(f)
    f = click.option("--yes", "skip_confirm", is_flag=True, default=False,
                     help="Skip confirmation prompt")(f)
    f = click.option("--raw", is_flag=True, default=False,
                     help="Treat amount as raw wei (no decimal conversion)")(f)
    return f


def _get_token_decimals(contract_instance, block=None) -> int:
    """Get decimals for the vault's asset token."""
    asset_addr = contract_instance.asset(block_identifier=block)
    token = erc20(asset_addr)
    return token.decimals(block_identifier=block)


# --------------------------------------------------------------------------- #
# Top-level tx group
# --------------------------------------------------------------------------- #

@click.group()
def tx():
    """Submit transactions to Twyne protocol contracts."""
    pass


# --------------------------------------------------------------------------- #
# Collateral vault subgroup
# --------------------------------------------------------------------------- #

@tx.group()
def collateral():
    """Collateral vault operations (deposit, withdraw, borrow, repay, etc.)."""
    pass


@collateral.command()
@click.argument("vault_address")
@click.argument("amount")
@tx_options
@pass_ctx
def deposit(ctx: TwyneContext, vault_address, amount, account_alias, private_key, dry_run, skip_confirm, raw):
    """Deposit collateral token into a vault."""
    ctx.connect()
    try:
        account = resolve_account(account_alias, private_key)
        cv = collateral_vault(vault_address)
        decimals = _get_token_decimals(cv)
        raw_amount = parse_amount(amount, decimals, raw=raw)

        sim = simulate_tx(cv, "deposit", [raw_amount], sender=account)
        if not sim["success"]:
            click.echo(f"Simulation failed: {sim['error']}", err=True)
            raise SystemExit(1)

        details = [
            ("Vault", vault_address),
            ("Amount", f"{amount} (raw: {raw_amount})"),
            ("Sender", str(account.address)),
        ]

        if dry_run:
            click.echo("Dry run — simulation passed.")
            return

        if not confirm_prompt(f"Deposit {amount} into {format_address(vault_address)}", details, skip_confirm):
            click.echo("Cancelled.")
            return

        receipt = cv.deposit(raw_amount, sender=account)
        display_receipt(receipt)
    finally:
        ctx.disconnect()


@collateral.command(name="deposit-underlying")
@click.argument("vault_address")
@click.argument("amount")
@tx_options
@pass_ctx
def deposit_underlying(ctx: TwyneContext, vault_address, amount, account_alias, private_key, dry_run, skip_confirm, raw):
    """Deposit underlying asset (e.g., raw ETH for a wstETH vault)."""
    ctx.connect()
    try:
        account = resolve_account(account_alias, private_key)
        cv = collateral_vault(vault_address)
        decimals = _get_token_decimals(cv)
        raw_amount = parse_amount(amount, decimals, raw=raw)

        sim = simulate_tx(cv, "depositUnderlying", [raw_amount], sender=account)
        if not sim["success"]:
            click.echo(f"Simulation failed: {sim['error']}", err=True)
            raise SystemExit(1)

        details = [
            ("Vault", vault_address),
            ("Amount", f"{amount} (raw: {raw_amount})"),
            ("Sender", str(account.address)),
        ]

        if dry_run:
            click.echo("Dry run — simulation passed.")
            return

        if not confirm_prompt(f"Deposit underlying {amount} into {format_address(vault_address)}", details, skip_confirm):
            click.echo("Cancelled.")
            return

        receipt = cv.depositUnderlying(raw_amount, sender=account)
        display_receipt(receipt)
    finally:
        ctx.disconnect()


@collateral.command()
@click.argument("vault_address")
@click.argument("amount")
@click.option("--receiver", default=None, help="Receiver address (defaults to sender)")
@tx_options
@pass_ctx
def withdraw(ctx: TwyneContext, vault_address, amount, receiver, account_alias, private_key, dry_run, skip_confirm, raw):
    """Withdraw collateral from a vault."""
    ctx.connect()
    try:
        account = resolve_account(account_alias, private_key)
        cv = collateral_vault(vault_address)
        decimals = _get_token_decimals(cv)
        raw_amount = parse_amount(amount, decimals, raw=raw)
        recv = receiver or str(account.address)

        sim = simulate_tx(cv, "withdraw", [raw_amount, recv], sender=account)
        if not sim["success"]:
            click.echo(f"Simulation failed: {sim['error']}", err=True)
            raise SystemExit(1)

        details = [
            ("Vault", vault_address),
            ("Amount", f"{amount} (raw: {raw_amount})"),
            ("Receiver", recv),
            ("Sender", str(account.address)),
        ]

        if dry_run:
            click.echo("Dry run — simulation passed.")
            return

        if not confirm_prompt(f"Withdraw {amount} from {format_address(vault_address)}", details, skip_confirm):
            click.echo("Cancelled.")
            return

        receipt = cv.withdraw(raw_amount, recv, sender=account)
        display_receipt(receipt)
    finally:
        ctx.disconnect()


@collateral.command(name="redeem-underlying")
@click.argument("vault_address")
@click.argument("amount")
@click.option("--receiver", default=None, help="Receiver address (defaults to sender)")
@tx_options
@pass_ctx
def redeem_underlying(ctx: TwyneContext, vault_address, amount, receiver, account_alias, private_key, dry_run, skip_confirm, raw):
    """Withdraw as underlying asset from a vault."""
    ctx.connect()
    try:
        account = resolve_account(account_alias, private_key)
        cv = collateral_vault(vault_address)
        decimals = _get_token_decimals(cv)
        raw_amount = parse_amount(amount, decimals, raw=raw)
        recv = receiver or str(account.address)

        sim = simulate_tx(cv, "redeemUnderlying", [raw_amount, recv], sender=account)
        if not sim["success"]:
            click.echo(f"Simulation failed: {sim['error']}", err=True)
            raise SystemExit(1)

        details = [
            ("Vault", vault_address),
            ("Amount", f"{amount} (raw: {raw_amount})"),
            ("Receiver", recv),
            ("Sender", str(account.address)),
        ]

        if dry_run:
            click.echo("Dry run — simulation passed.")
            return

        if not confirm_prompt(f"Redeem underlying {amount} from {format_address(vault_address)}", details, skip_confirm):
            click.echo("Cancelled.")
            return

        receipt = cv.redeemUnderlying(raw_amount, recv, sender=account)
        display_receipt(receipt)
    finally:
        ctx.disconnect()


@collateral.command()
@click.argument("vault_address")
@click.argument("amount")
@click.option("--receiver", default=None, help="Receiver address (defaults to sender)")
@tx_options
@pass_ctx
def borrow(ctx: TwyneContext, vault_address, amount, receiver, account_alias, private_key, dry_run, skip_confirm, raw):
    """Borrow from the external protocol via a collateral vault."""
    ctx.connect()
    try:
        account = resolve_account(account_alias, private_key)
        cv = collateral_vault(vault_address)
        decimals = _get_token_decimals(cv)
        raw_amount = parse_amount(amount, decimals, raw=raw)
        recv = receiver or str(account.address)

        sim = simulate_tx(cv, "borrow", [raw_amount, recv], sender=account)
        if not sim["success"]:
            click.echo(f"Simulation failed: {sim['error']}", err=True)
            raise SystemExit(1)

        details = [
            ("Vault", vault_address),
            ("Amount", f"{amount} (raw: {raw_amount})"),
            ("Receiver", recv),
            ("Sender", str(account.address)),
        ]

        if dry_run:
            click.echo("Dry run — simulation passed.")
            return

        if not confirm_prompt(f"Borrow {amount} from {format_address(vault_address)}", details, skip_confirm):
            click.echo("Cancelled.")
            return

        receipt = cv.borrow(raw_amount, recv, sender=account)
        display_receipt(receipt)
    finally:
        ctx.disconnect()


@collateral.command()
@click.argument("vault_address")
@click.argument("amount")
@tx_options
@pass_ctx
def repay(ctx: TwyneContext, vault_address, amount, account_alias, private_key, dry_run, skip_confirm, raw):
    """Repay borrowed amount to a collateral vault."""
    ctx.connect()
    try:
        account = resolve_account(account_alias, private_key)
        cv = collateral_vault(vault_address)
        decimals = _get_token_decimals(cv)
        raw_amount = parse_amount(amount, decimals, raw=raw)

        sim = simulate_tx(cv, "repay", [raw_amount], sender=account)
        if not sim["success"]:
            click.echo(f"Simulation failed: {sim['error']}", err=True)
            raise SystemExit(1)

        details = [
            ("Vault", vault_address),
            ("Amount", f"{amount} (raw: {raw_amount})"),
            ("Sender", str(account.address)),
        ]

        if dry_run:
            click.echo("Dry run — simulation passed.")
            return

        if not confirm_prompt(f"Repay {amount} to {format_address(vault_address)}", details, skip_confirm):
            click.echo("Cancelled.")
            return

        receipt = cv.repay(raw_amount, sender=account)
        display_receipt(receipt)
    finally:
        ctx.disconnect()


@collateral.command(name="set-ltv")
@click.argument("vault_address")
@click.argument("ltv", type=int)
@tx_options
@pass_ctx
def set_ltv(ctx: TwyneContext, vault_address, ltv, account_alias, private_key, dry_run, skip_confirm, **_):
    """Set liquidation LTV on a collateral vault (basis points, e.g., 8500 = 85%)."""
    ctx.connect()
    try:
        account = resolve_account(account_alias, private_key)
        cv = collateral_vault(vault_address)

        sim = simulate_tx(cv, "setTwyneLiqLTV", [ltv], sender=account)
        if not sim["success"]:
            click.echo(f"Simulation failed: {sim['error']}", err=True)
            raise SystemExit(1)

        details = [
            ("Vault", vault_address),
            ("LTV", f"{ltv} bps ({ltv / 100:.2f}%)"),
            ("Sender", str(account.address)),
        ]

        if dry_run:
            click.echo("Dry run — simulation passed.")
            return

        if not confirm_prompt(f"Set LTV to {ltv / 100:.2f}% on {format_address(vault_address)}", details, skip_confirm):
            click.echo("Cancelled.")
            return

        receipt = cv.setTwyneLiqLTV(ltv, sender=account)
        display_receipt(receipt)
    finally:
        ctx.disconnect()


@collateral.command()
@click.argument("vault_address")
@tx_options
@pass_ctx
def liquidate(ctx: TwyneContext, vault_address, account_alias, private_key, dry_run, skip_confirm, **_):
    """Liquidate an unhealthy collateral vault position."""
    ctx.connect()
    try:
        account = resolve_account(account_alias, private_key)
        cv = collateral_vault(vault_address)

        sim = simulate_tx(cv, "liquidate", [], sender=account)
        if not sim["success"]:
            click.echo(f"Simulation failed: {sim['error']}", err=True)
            raise SystemExit(1)

        details = [
            ("Vault", vault_address),
            ("Sender", str(account.address)),
        ]

        if dry_run:
            click.echo("Dry run — simulation passed.")
            return

        if not confirm_prompt(f"Liquidate vault {format_address(vault_address)}", details, skip_confirm):
            click.echo("Cancelled.")
            return

        receipt = cv.liquidate(sender=account)
        display_receipt(receipt)
    finally:
        ctx.disconnect()


@collateral.command()
@click.argument("vault_address")
@tx_options
@pass_ctx
def skim(ctx: TwyneContext, vault_address, account_alias, private_key, dry_run, skip_confirm, **_):
    """Skim excess tokens from a collateral vault."""
    ctx.connect()
    try:
        account = resolve_account(account_alias, private_key)
        cv = collateral_vault(vault_address)

        sim = simulate_tx(cv, "skim", [], sender=account)
        if not sim["success"]:
            click.echo(f"Simulation failed: {sim['error']}", err=True)
            raise SystemExit(1)

        details = [
            ("Vault", vault_address),
            ("Sender", str(account.address)),
        ]

        if dry_run:
            click.echo("Dry run — simulation passed.")
            return

        if not confirm_prompt(f"Skim {format_address(vault_address)}", details, skip_confirm):
            click.echo("Cancelled.")
            return

        receipt = cv.skim(sender=account)
        display_receipt(receipt)
    finally:
        ctx.disconnect()
