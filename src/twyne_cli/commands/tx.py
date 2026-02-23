"""Transaction commands — write operations for Twyne protocol."""

import click

from ..batch import build_batch_items, parse_batch_file
from ..constants import DEFAULT_SLIPPAGE
from ..context import TwyneContext, pass_ctx
from ..contracts import (
    aave_atoken_wrapper,
    aave_wrapper,
    collateral_vault,
    collateral_vault_factory,
    deleverage_operator,
    erc20,
    euler_wrapper,
    get_address,
    leverage_operator,
    teleport_operator,
)
from ..contracts import (
    evc as evc_contract,
)
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


# --------------------------------------------------------------------------- #
# Credit vault subgroup (CLP / intermediate vault operations)
# --------------------------------------------------------------------------- #

@tx.group()
def credit():
    """Credit vault operations (deposit, withdraw, redeem for CLPs)."""
    pass


@credit.command(name="deposit")
@click.argument("iv_address")
@click.argument("amount")
@click.option("--protocol", type=click.Choice(["euler", "aave"]), default="euler",
              help="Which wrapper to use (euler or aave)")
@tx_options
@pass_ctx
def credit_deposit(ctx: TwyneContext, iv_address, amount, protocol, account_alias, private_key, dry_run, skip_confirm, raw):
    """Deposit underlying asset into an intermediate vault via wrapper."""
    ctx.connect()
    try:
        account = resolve_account(account_alias, private_key)
        wrapper = euler_wrapper() if protocol == "euler" else aave_wrapper()

        # Get decimals from the intermediate vault's asset
        cv = collateral_vault(iv_address)  # ERC4626-compatible interface
        decimals = _get_token_decimals(cv)
        raw_amount = parse_amount(amount, decimals, raw=raw)

        sim = simulate_tx(wrapper, "depositUnderlyingToIntermediateVault", [iv_address, raw_amount], sender=account)
        if not sim["success"]:
            click.echo(f"Simulation failed: {sim['error']}", err=True)
            raise SystemExit(1)

        details = [
            ("Intermediate Vault", iv_address),
            ("Protocol", protocol),
            ("Amount", f"{amount} (raw: {raw_amount})"),
            ("Sender", str(account.address)),
        ]

        if dry_run:
            click.echo("Dry run — simulation passed.")
            return

        if not confirm_prompt(f"Deposit {amount} into credit vault {format_address(iv_address)}", details, skip_confirm):
            click.echo("Cancelled.")
            return

        receipt = wrapper.depositUnderlyingToIntermediateVault(iv_address, raw_amount, sender=account)
        display_receipt(receipt)
    finally:
        ctx.disconnect()


@credit.command(name="deposit-underlying")
@click.argument("iv_address")
@click.argument("amount")
@click.option("--protocol", type=click.Choice(["euler", "aave"]), default="euler",
              help="Which wrapper to use (euler or aave)")
@tx_options
@pass_ctx
def deposit_underlying_credit(ctx: TwyneContext, iv_address, amount, protocol, account_alias, private_key, dry_run, skip_confirm, raw):
    """Deposit underlying asset into intermediate vault (alias for deposit)."""
    ctx.connect()
    try:
        account = resolve_account(account_alias, private_key)
        wrapper = euler_wrapper() if protocol == "euler" else aave_wrapper()

        cv = collateral_vault(iv_address)
        decimals = _get_token_decimals(cv)
        raw_amount = parse_amount(amount, decimals, raw=raw)

        sim = simulate_tx(wrapper, "depositUnderlyingToIntermediateVault", [iv_address, raw_amount], sender=account)
        if not sim["success"]:
            click.echo(f"Simulation failed: {sim['error']}", err=True)
            raise SystemExit(1)

        details = [
            ("Intermediate Vault", iv_address),
            ("Protocol", protocol),
            ("Amount", f"{amount} (raw: {raw_amount})"),
            ("Sender", str(account.address)),
        ]

        if dry_run:
            click.echo("Dry run — simulation passed.")
            return

        if not confirm_prompt(f"Deposit underlying {amount} into credit vault {format_address(iv_address)}", details, skip_confirm):
            click.echo("Cancelled.")
            return

        receipt = wrapper.depositUnderlyingToIntermediateVault(iv_address, raw_amount, sender=account)
        display_receipt(receipt)
    finally:
        ctx.disconnect()


@credit.command(name="deposit-atokens")
@click.argument("iv_address")
@click.argument("amount")
@tx_options
@pass_ctx
def deposit_atokens(ctx: TwyneContext, iv_address, amount, account_alias, private_key, dry_run, skip_confirm, raw):
    """Deposit Aave aTokens into an intermediate vault via aToken wrapper."""
    ctx.connect()
    try:
        account = resolve_account(account_alias, private_key)
        wrapper = aave_atoken_wrapper()

        cv = collateral_vault(iv_address)
        decimals = _get_token_decimals(cv)
        raw_amount = parse_amount(amount, decimals, raw=raw)

        sim = simulate_tx(wrapper, "depositATokens", [iv_address, raw_amount], sender=account)
        if not sim["success"]:
            click.echo(f"Simulation failed: {sim['error']}", err=True)
            raise SystemExit(1)

        details = [
            ("Intermediate Vault", iv_address),
            ("Amount", f"{amount} (raw: {raw_amount})"),
            ("Sender", str(account.address)),
        ]

        if dry_run:
            click.echo("Dry run — simulation passed.")
            return

        if not confirm_prompt(f"Deposit aTokens {amount} into credit vault {format_address(iv_address)}", details, skip_confirm):
            click.echo("Cancelled.")
            return

        receipt = wrapper.depositATokens(iv_address, raw_amount, sender=account)
        display_receipt(receipt)
    finally:
        ctx.disconnect()


@credit.command(name="withdraw")
@click.argument("iv_address")
@click.argument("amount")
@click.option("--receiver", default=None, help="Receiver address (defaults to sender)")
@tx_options
@pass_ctx
def credit_withdraw(ctx: TwyneContext, iv_address, amount, receiver, account_alias, private_key, dry_run, skip_confirm, raw):
    """Withdraw assets from an intermediate vault (ERC4626 withdraw)."""
    ctx.connect()
    try:
        account = resolve_account(account_alias, private_key)
        cv = collateral_vault(iv_address)  # ERC4626-compatible interface
        decimals = _get_token_decimals(cv)
        raw_amount = parse_amount(amount, decimals, raw=raw)
        recv = receiver or str(account.address)

        sim = simulate_tx(cv, "withdraw", [raw_amount, recv, str(account.address)], sender=account)
        if not sim["success"]:
            click.echo(f"Simulation failed: {sim['error']}", err=True)
            raise SystemExit(1)

        details = [
            ("Intermediate Vault", iv_address),
            ("Amount", f"{amount} (raw: {raw_amount})"),
            ("Receiver", recv),
            ("Sender", str(account.address)),
        ]

        if dry_run:
            click.echo("Dry run — simulation passed.")
            return

        if not confirm_prompt(f"Withdraw {amount} from credit vault {format_address(iv_address)}", details, skip_confirm):
            click.echo("Cancelled.")
            return

        receipt = cv.withdraw(raw_amount, recv, str(account.address), sender=account)
        display_receipt(receipt)
    finally:
        ctx.disconnect()


@credit.command(name="redeem")
@click.argument("iv_address")
@click.argument("shares")
@click.option("--receiver", default=None, help="Receiver address (defaults to sender)")
@tx_options
@pass_ctx
def credit_redeem(ctx: TwyneContext, iv_address, shares, receiver, account_alias, private_key, dry_run, skip_confirm, raw):
    """Redeem shares from an intermediate vault (ERC4626 redeem)."""
    ctx.connect()
    try:
        account = resolve_account(account_alias, private_key)
        cv = collateral_vault(iv_address)  # ERC4626-compatible interface
        decimals = 18  # EVault shares are always 18 decimals
        raw_shares = parse_amount(shares, decimals, raw=raw)
        recv = receiver or str(account.address)

        sim = simulate_tx(cv, "redeem", [raw_shares, recv, str(account.address)], sender=account)
        if not sim["success"]:
            click.echo(f"Simulation failed: {sim['error']}", err=True)
            raise SystemExit(1)

        details = [
            ("Intermediate Vault", iv_address),
            ("Shares", f"{shares} (raw: {raw_shares})"),
            ("Receiver", recv),
            ("Sender", str(account.address)),
        ]

        if dry_run:
            click.echo("Dry run — simulation passed.")
            return

        if not confirm_prompt(f"Redeem {shares} shares from credit vault {format_address(iv_address)}", details, skip_confirm):
            click.echo("Cancelled.")
            return

        receipt = cv.redeem(raw_shares, recv, str(account.address), sender=account)
        display_receipt(receipt)
    finally:
        ctx.disconnect()


# --------------------------------------------------------------------------- #
# Operators subgroup (leverage, deleverage, teleport)
# --------------------------------------------------------------------------- #

@tx.group()
def operators():
    """Operator actions (leverage, deleverage, teleport) via 1inch swaps."""
    pass


@operators.command()
@click.argument("vault_address")
@click.argument("amount")
@click.option("--protocol", type=click.Choice(["euler", "aave"]), default="euler",
              help="Protocol integration to use")
@click.option("--slippage", type=float, default=DEFAULT_SLIPPAGE,
              help=f"Swap slippage tolerance (default: {DEFAULT_SLIPPAGE}%)")
@click.option("--api-key", envvar="ONEINCH_API_KEY", default=None,
              help="1inch API key (or set ONEINCH_API_KEY env var)")
@tx_options
@pass_ctx
def leverage(ctx: TwyneContext, vault_address, amount, protocol, slippage, api_key,
             account_alias, private_key, dry_run, skip_confirm, raw):
    """Execute leverage via flash loan + 1inch swap.

    Borrows, swaps to collateral token, deposits — all atomically.
    """
    ctx.connect()
    try:
        account = resolve_account(account_alias, private_key)
        cv = collateral_vault(vault_address)
        decimals = _get_token_decimals(cv)
        raw_amount = parse_amount(amount, decimals, raw=raw)

        # The operator contract handles the flash loan + swap internally
        op = leverage_operator(protocol)

        details = [
            ("Vault", vault_address),
            ("Protocol", protocol),
            ("Amount", f"{amount} (raw: {raw_amount})"),
            ("Slippage", f"{slippage}%"),
            ("Operator", str(op.address)),
            ("Sender", str(account.address)),
        ]

        sim = simulate_tx(op, "executeLeverage", [vault_address, raw_amount, b""], sender=account)
        if not sim["success"]:
            click.echo(f"Simulation failed: {sim['error']}", err=True)
            raise SystemExit(1)

        if dry_run:
            click.echo("Dry run — simulation passed.")
            return

        if not confirm_prompt(f"Leverage {amount} on {format_address(vault_address)}", details, skip_confirm):
            click.echo("Cancelled.")
            return

        receipt = op.executeLeverage(vault_address, raw_amount, b"", sender=account)
        display_receipt(receipt)
    finally:
        ctx.disconnect()


@operators.command()
@click.argument("vault_address")
@click.argument("amount")
@click.option("--protocol", type=click.Choice(["euler", "aave"]), default="euler",
              help="Protocol integration to use")
@click.option("--slippage", type=float, default=DEFAULT_SLIPPAGE,
              help=f"Swap slippage tolerance (default: {DEFAULT_SLIPPAGE}%)")
@click.option("--api-key", envvar="ONEINCH_API_KEY", default=None,
              help="1inch API key (or set ONEINCH_API_KEY env var)")
@tx_options
@pass_ctx
def deleverage(ctx: TwyneContext, vault_address, amount, protocol, slippage, api_key,
               account_alias, private_key, dry_run, skip_confirm, raw):
    """Execute deleverage via flash loan + 1inch swap.

    Repays debt, withdraws collateral, swaps to debt token — all atomically.
    """
    ctx.connect()
    try:
        account = resolve_account(account_alias, private_key)
        cv = collateral_vault(vault_address)
        decimals = _get_token_decimals(cv)
        raw_amount = parse_amount(amount, decimals, raw=raw)

        op = deleverage_operator(protocol)

        details = [
            ("Vault", vault_address),
            ("Protocol", protocol),
            ("Amount", f"{amount} (raw: {raw_amount})"),
            ("Slippage", f"{slippage}%"),
            ("Operator", str(op.address)),
            ("Sender", str(account.address)),
        ]

        sim = simulate_tx(op, "executeDeleverage", [vault_address, raw_amount, b""], sender=account)
        if not sim["success"]:
            click.echo(f"Simulation failed: {sim['error']}", err=True)
            raise SystemExit(1)

        if dry_run:
            click.echo("Dry run — simulation passed.")
            return

        if not confirm_prompt(f"Deleverage {amount} on {format_address(vault_address)}", details, skip_confirm):
            click.echo("Cancelled.")
            return

        receipt = op.executeDeleverage(vault_address, raw_amount, b"", sender=account)
        display_receipt(receipt)
    finally:
        ctx.disconnect()


@operators.command()
@click.argument("vault_address")
@click.argument("target_vault_address")
@click.option("--protocol", type=click.Choice(["euler", "aave"]), default="euler",
              help="Protocol integration to use")
@tx_options
@pass_ctx
def teleport(ctx: TwyneContext, vault_address, target_vault_address, protocol,
             account_alias, private_key, dry_run, skip_confirm, **_):
    """Teleport a position from one collateral vault to another.

    Euler: calls cv.teleport() directly.
    Aave: uses the teleport operator contract.
    """
    ctx.connect()
    try:
        account = resolve_account(account_alias, private_key)
        cv = collateral_vault(vault_address)

        details = [
            ("Source Vault", vault_address),
            ("Target Vault", target_vault_address),
            ("Protocol", protocol),
            ("Sender", str(account.address)),
        ]

        if protocol == "euler":
            sim = simulate_tx(cv, "teleport", [target_vault_address], sender=account)
            if not sim["success"]:
                click.echo(f"Simulation failed: {sim['error']}", err=True)
                raise SystemExit(1)

            if dry_run:
                click.echo("Dry run — simulation passed.")
                return

            if not confirm_prompt(
                f"Teleport from {format_address(vault_address)} to {format_address(target_vault_address)}",
                details, skip_confirm,
            ):
                click.echo("Cancelled.")
                return

            receipt = cv.teleport(target_vault_address, sender=account)
        else:
            op = teleport_operator()
            details.append(("Operator", str(op.address)))

            sim = simulate_tx(op, "executeTeleport", [vault_address, target_vault_address], sender=account)
            if not sim["success"]:
                click.echo(f"Simulation failed: {sim['error']}", err=True)
                raise SystemExit(1)

            if dry_run:
                click.echo("Dry run — simulation passed.")
                return

            if not confirm_prompt(
                f"Teleport from {format_address(vault_address)} to {format_address(target_vault_address)}",
                details, skip_confirm,
            ):
                click.echo("Cancelled.")
                return

            receipt = op.executeTeleport(vault_address, target_vault_address, sender=account)

        display_receipt(receipt)
    finally:
        ctx.disconnect()


# --------------------------------------------------------------------------- #
# Factory subgroup (create-vault)
# --------------------------------------------------------------------------- #

@tx.group()
def factory():
    """Collateral vault factory operations."""
    pass


@factory.command(name="create-vault")
@click.argument("beacon_address")
@click.option("--salt", type=int, default=0, help="Salt for deterministic vault address (default: 0)")
@tx_options
@pass_ctx
def create_vault(ctx: TwyneContext, beacon_address, salt, account_alias, private_key, dry_run, skip_confirm, **_):
    """Create a new collateral vault via the factory.

    BEACON_ADDRESS: The beacon proxy address for the target vault type.
    """
    ctx.connect()
    try:
        account = resolve_account(account_alias, private_key)
        fct = collateral_vault_factory()

        sim = simulate_tx(fct, "createCollateralVault", [beacon_address, salt], sender=account)
        if not sim["success"]:
            click.echo(f"Simulation failed: {sim['error']}", err=True)
            raise SystemExit(1)

        details = [
            ("Factory", str(fct.address)),
            ("Beacon", beacon_address),
            ("Salt", str(salt)),
            ("Sender", str(account.address)),
        ]

        if dry_run:
            click.echo("Dry run — simulation passed.")
            if sim.get("result"):
                click.echo(f"Predicted vault address: {sim['result']}")
            return

        if not confirm_prompt(f"Create collateral vault (beacon: {format_address(beacon_address)})", details, skip_confirm):
            click.echo("Cancelled.")
            return

        receipt = fct.createCollateralVault(beacon_address, salt, sender=account)
        display_receipt(receipt)
    finally:
        ctx.disconnect()


# --------------------------------------------------------------------------- #
# Batch subgroup (EVC batch execution)
# --------------------------------------------------------------------------- #

@tx.group()
def batch():
    """EVC batch operations (execute or simulate from YAML/JSON file)."""
    pass


@batch.command()
@click.argument("batch_file", type=click.Path(exists=True))
@click.option("--evc-address", default=None, help="EVC address override (defaults to batch file or Twyne EVC)")
@tx_options
@pass_ctx
def execute(ctx: TwyneContext, batch_file, evc_address, account_alias, private_key, dry_run, skip_confirm, **_):
    """Execute a batch of operations via EVC.batch().

    BATCH_FILE: Path to YAML or JSON batch definition file.
    """
    ctx.connect()
    try:
        account = resolve_account(account_alias, private_key)
        batch_data = parse_batch_file(batch_file)

        evc_addr = evc_address or batch_data.get("evc") or get_address("evc")
        evc_instance = evc_contract(evc_addr)

        items = build_batch_items(batch_data, str(account.address))

        # Convert dicts to tuples for the contract call
        batch_items = [
            (item["targetContract"], item["onBehalfOfAccount"], item["value"], item["data"])
            for item in items
        ]

        sim = simulate_tx(evc_instance, "batch", [batch_items], sender=account)
        if not sim["success"]:
            click.echo(f"Simulation failed: {sim['error']}", err=True)
            raise SystemExit(1)

        details = [
            ("Batch File", batch_file),
            ("EVC", evc_addr),
            ("Operations", str(len(items))),
            ("Sender", str(account.address)),
        ]

        for i, op in enumerate(batch_data["operations"]):
            details.append((f"  Op {i}", op["action"]))

        if dry_run:
            click.echo("Dry run — simulation passed.")
            return

        if not confirm_prompt(f"Execute batch ({len(items)} operations)", details, skip_confirm):
            click.echo("Cancelled.")
            return

        receipt = evc_instance.batch(batch_items, sender=account)
        display_receipt(receipt)
    finally:
        ctx.disconnect()


@batch.command()
@click.argument("batch_file", type=click.Path(exists=True))
@click.option("--evc-address", default=None, help="EVC address override (defaults to batch file or Twyne EVC)")
@tx_options
@pass_ctx
def simulate(ctx: TwyneContext, batch_file, evc_address, account_alias, private_key, **_):
    """Simulate a batch of operations via EVC.batchSimulation().

    BATCH_FILE: Path to YAML or JSON batch definition file.
    Runs as eth_call only — no transaction submitted.
    """
    ctx.connect()
    try:
        account = resolve_account(account_alias, private_key)
        batch_data = parse_batch_file(batch_file)

        evc_addr = evc_address or batch_data.get("evc") or get_address("evc")
        evc_instance = evc_contract(evc_addr)

        items = build_batch_items(batch_data, str(account.address))

        batch_items = [
            (item["targetContract"], item["onBehalfOfAccount"], item["value"], item["data"])
            for item in items
        ]

        sim = simulate_tx(evc_instance, "batchSimulation", [batch_items], sender=account)

        if sim["success"]:
            click.echo("Batch simulation: SUCCESS")
            click.echo(f"Operations: {len(items)}")
            if sim.get("result"):
                click.echo(f"Result: {sim['result']}")
        else:
            click.echo(f"Batch simulation: FAILED — {sim['error']}", err=True)
            raise SystemExit(1)
    finally:
        ctx.disconnect()
