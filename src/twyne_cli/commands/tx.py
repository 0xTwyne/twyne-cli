"""Transaction commands — write operations for Twyne protocol."""

import click

from ..batch import build_batch_items, collect_approval_requirements, parse_batch_file
from ..constants import DEFAULT_SLIPPAGE
from ..context import TwyneContext, pass_ctx
from ..contracts import (
    aave_atoken_wrapper,
    aave_wrapper,
    collateral_vault,
    collateral_vault_factory,
    credit_vault,
    deleverage_operator,
    erc20,
    euler_wrapper,
    get_address,
    leverage_operator,
    resolve_aave_factory_vault,
    teleport_operator,
)
from ..contracts import (
    evc as evc_contract,
)
from ..formatting import format_address
from ..transactions import (
    confirm_prompt,
    display_receipt,
    ensure_allowance,
    execute_through_evc,
    parse_amount,
    resolve_account,
    simulate_through_evc,
    simulate_tx,
)


def _format_sim_address(value) -> str:
    """Convert a simulation return value (bytes or str) to a checksummed address."""
    if isinstance(value, (bytes, bytearray)):
        raw_hex = value.hex()[-40:]
        value = f"0x{raw_hex}"
    addr = str(value)
    # Apply EIP-55 checksum
    try:
        from eth_utils import to_checksum_address
        return to_checksum_address(addr)
    except Exception:
        return addr


def _show_verbose_error(ctx: TwyneContext, sim: dict):
    """If --verbose, display extra revert/trace info from failed simulation."""
    if not getattr(ctx, "verbose", False):
        return
    if sim.get("revert_message"):
        click.echo(f"  Revert reason: {sim['revert_message']}", err=True)
    if sim.get("dev_message"):
        click.echo(f"  Dev message:   {sim['dev_message']}", err=True)
    if sim.get("contract_address"):
        click.echo(f"  Contract:      {sim['contract_address']}", err=True)
    if sim.get("source_traceback"):
        click.echo(f"  Traceback:\n{sim['source_traceback']}", err=True)


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
    f = click.option("--max-approve", is_flag=True, default=False,
                     help="Use max uint256 approval instead of exact amount")(f)
    f = click.option("--skip-approval", is_flag=True, default=False,
                     help="Don't auto-approve; error if allowance insufficient")(f)
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
def deposit(ctx: TwyneContext, vault_address, amount, account_alias, private_key, dry_run, skip_confirm, raw, max_approve, skip_approval):
    """Deposit collateral token into a vault."""
    ctx.connect()
    try:
        account = resolve_account(account_alias, private_key)
        cv = collateral_vault(vault_address)
        decimals = _get_token_decimals(cv)
        raw_amount = parse_amount(amount, decimals, raw=raw)

        # Ensure token approval (cv.asset() → vault)
        asset_addr = cv.asset()
        if not ensure_allowance(asset_addr, vault_address, raw_amount, account,
                                skip_confirm=skip_confirm, skip_approval=skip_approval,
                                max_approve=max_approve):
            click.echo("Approval declined.")
            return

        sim = simulate_tx(cv, "deposit", [raw_amount], sender=account)
        if not sim["success"]:
            click.echo(f"Simulation failed: {sim['error']}", err=True)
            _show_verbose_error(ctx, sim)
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

        receipt = execute_through_evc(cv, "deposit", [raw_amount], account)
        display_receipt(receipt)
    finally:
        ctx.disconnect()


@collateral.command(name="deposit-underlying")
@click.argument("vault_address")
@click.argument("amount")
@tx_options
@pass_ctx
def deposit_underlying(ctx: TwyneContext, vault_address, amount, account_alias, private_key, dry_run, skip_confirm, raw, max_approve, skip_approval):
    """Deposit underlying asset (e.g., raw ETH for a wstETH vault)."""
    ctx.connect()
    try:
        account = resolve_account(account_alias, private_key)
        cv = collateral_vault(vault_address)
        decimals = _get_token_decimals(cv)
        raw_amount = parse_amount(amount, decimals, raw=raw)

        # Ensure token approval (underlying asset → vault)
        underlying_addr = cv.underlyingAsset()
        if not ensure_allowance(underlying_addr, vault_address, raw_amount, account,
                                skip_confirm=skip_confirm, skip_approval=skip_approval,
                                max_approve=max_approve):
            click.echo("Approval declined.")
            return

        sim = simulate_tx(cv, "depositUnderlying", [raw_amount], sender=account)
        if not sim["success"]:
            click.echo(f"Simulation failed: {sim['error']}", err=True)
            _show_verbose_error(ctx, sim)
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

        receipt = execute_through_evc(cv, "depositUnderlying", [raw_amount], account)
        display_receipt(receipt)
    finally:
        ctx.disconnect()


@collateral.command()
@click.argument("vault_address")
@click.argument("amount")
@click.option("--receiver", default=None, help="Receiver address (defaults to sender)")
@tx_options
@pass_ctx
def withdraw(ctx: TwyneContext, vault_address, amount, receiver, account_alias, private_key, dry_run, skip_confirm, raw, **_):
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
            _show_verbose_error(ctx, sim)
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

        receipt = execute_through_evc(cv, "withdraw", [raw_amount, recv], account)
        display_receipt(receipt)
    finally:
        ctx.disconnect()


@collateral.command(name="redeem-underlying")
@click.argument("vault_address")
@click.argument("amount")
@click.option("--receiver", default=None, help="Receiver address (defaults to sender)")
@tx_options
@pass_ctx
def redeem_underlying(ctx: TwyneContext, vault_address, amount, receiver, account_alias, private_key, dry_run, skip_confirm, raw, **_):
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
            _show_verbose_error(ctx, sim)
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

        receipt = execute_through_evc(cv, "redeemUnderlying", [raw_amount, recv], account)
        display_receipt(receipt)
    finally:
        ctx.disconnect()


@collateral.command()
@click.argument("vault_address")
@click.argument("amount")
@click.option("--receiver", default=None, help="Receiver address (defaults to sender)")
@tx_options
@pass_ctx
def borrow(ctx: TwyneContext, vault_address, amount, receiver, account_alias, private_key, dry_run, skip_confirm, raw, **_):
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
            _show_verbose_error(ctx, sim)
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

        receipt = execute_through_evc(cv, "borrow", [raw_amount, recv], account)
        display_receipt(receipt)
    finally:
        ctx.disconnect()


@collateral.command()
@click.argument("vault_address")
@click.argument("amount")
@tx_options
@pass_ctx
def repay(ctx: TwyneContext, vault_address, amount, account_alias, private_key, dry_run, skip_confirm, raw, max_approve, skip_approval):
    """Repay borrowed amount to a collateral vault."""
    ctx.connect()
    try:
        account = resolve_account(account_alias, private_key)
        cv = collateral_vault(vault_address)
        decimals = _get_token_decimals(cv)
        raw_amount = parse_amount(amount, decimals, raw=raw)

        # Ensure token approval (targetAsset → vault)
        # For "max" repay, use maxRepay() + 1% buffer for accrued interest
        target_addr = cv.targetAsset()
        if raw_amount == 2**256 - 1:
            approval_amount = cv.maxRepay() * 101 // 100
        else:
            approval_amount = raw_amount
        if not ensure_allowance(target_addr, vault_address, approval_amount, account,
                                skip_confirm=skip_confirm, skip_approval=skip_approval,
                                max_approve=max_approve):
            click.echo("Approval declined.")
            return

        sim = simulate_tx(cv, "repay", [raw_amount], sender=account)
        if not sim["success"]:
            click.echo(f"Simulation failed: {sim['error']}", err=True)
            _show_verbose_error(ctx, sim)
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

        receipt = execute_through_evc(cv, "repay", [raw_amount], account)
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
            _show_verbose_error(ctx, sim)
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

        receipt = execute_through_evc(cv, "setTwyneLiqLTV", [ltv], account)
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
            _show_verbose_error(ctx, sim)
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

        receipt = execute_through_evc(cv, "liquidate", [], account)
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
            _show_verbose_error(ctx, sim)
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

        receipt = execute_through_evc(cv, "skim", [], account)
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
def credit_deposit(ctx: TwyneContext, iv_address, amount, protocol, account_alias, private_key, dry_run, skip_confirm, raw, max_approve, skip_approval):
    """Deposit underlying asset into an intermediate vault via wrapper."""
    ctx.connect()
    try:
        account = resolve_account(account_alias, private_key)
        wrapper = euler_wrapper() if protocol == "euler" else aave_wrapper()

        # Get decimals from the intermediate vault's asset
        cv = credit_vault(iv_address)  # ERC4626-compatible interface
        decimals = _get_token_decimals(cv)
        raw_amount = parse_amount(amount, decimals, raw=raw)

        # Ensure token approval (underlying → wrapper)
        # IV.asset() → eVault, eVault.asset() → underlying token
        evault_addr = cv.asset()
        underlying_addr = credit_vault(evault_addr).asset()
        if not ensure_allowance(underlying_addr, str(wrapper.address), raw_amount, account,
                                skip_confirm=skip_confirm, skip_approval=skip_approval,
                                max_approve=max_approve):
            click.echo("Approval declined.")
            return

        sim = simulate_tx(wrapper, "depositUnderlyingToIntermediateVault", [iv_address, raw_amount], sender=account)
        if not sim["success"]:
            click.echo(f"Simulation failed: {sim['error']}", err=True)
            _show_verbose_error(ctx, sim)
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
def deposit_underlying_credit(ctx: TwyneContext, iv_address, amount, protocol, account_alias, private_key, dry_run, skip_confirm, raw, max_approve, skip_approval):
    """Deposit underlying asset into intermediate vault (alias for deposit)."""
    ctx.connect()
    try:
        account = resolve_account(account_alias, private_key)
        wrapper = euler_wrapper() if protocol == "euler" else aave_wrapper()

        cv = credit_vault(iv_address)
        decimals = _get_token_decimals(cv)
        raw_amount = parse_amount(amount, decimals, raw=raw)

        # Ensure token approval (underlying → wrapper)
        evault_addr = cv.asset()
        underlying_addr = credit_vault(evault_addr).asset()
        if not ensure_allowance(underlying_addr, str(wrapper.address), raw_amount, account,
                                skip_confirm=skip_confirm, skip_approval=skip_approval,
                                max_approve=max_approve):
            click.echo("Approval declined.")
            return

        sim = simulate_tx(wrapper, "depositUnderlyingToIntermediateVault", [iv_address, raw_amount], sender=account)
        if not sim["success"]:
            click.echo(f"Simulation failed: {sim['error']}", err=True)
            _show_verbose_error(ctx, sim)
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
def deposit_atokens(ctx: TwyneContext, iv_address, amount, account_alias, private_key, dry_run, skip_confirm, raw, max_approve, skip_approval):
    """Deposit Aave aTokens into an intermediate vault via aToken wrapper."""
    ctx.connect()
    try:
        account = resolve_account(account_alias, private_key)
        wrapper = aave_atoken_wrapper()

        cv = credit_vault(iv_address)
        decimals = _get_token_decimals(cv)
        raw_amount = parse_amount(amount, decimals, raw=raw)

        # Ensure token approval (aToken → aToken wrapper)
        atoken_addr = wrapper.aToken()
        if not ensure_allowance(atoken_addr, str(wrapper.address), raw_amount, account,
                                skip_confirm=skip_confirm, skip_approval=skip_approval,
                                max_approve=max_approve):
            click.echo("Approval declined.")
            return

        sim = simulate_tx(wrapper, "depositATokens", [iv_address, raw_amount], sender=account)
        if not sim["success"]:
            click.echo(f"Simulation failed: {sim['error']}", err=True)
            _show_verbose_error(ctx, sim)
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
def credit_withdraw(ctx: TwyneContext, iv_address, amount, receiver, account_alias, private_key, dry_run, skip_confirm, raw, **_):
    """Withdraw assets from an intermediate vault (ERC4626 withdraw)."""
    ctx.connect()
    try:
        account = resolve_account(account_alias, private_key)
        cv = credit_vault(iv_address)  # ERC4626-compatible interface
        decimals = _get_token_decimals(cv)
        raw_amount = parse_amount(amount, decimals, raw=raw)
        recv = receiver or str(account.address)

        sim = simulate_tx(cv, "withdraw", [raw_amount, recv, str(account.address)], sender=account)
        if not sim["success"]:
            click.echo(f"Simulation failed: {sim['error']}", err=True)
            _show_verbose_error(ctx, sim)
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
def credit_redeem(ctx: TwyneContext, iv_address, shares, receiver, account_alias, private_key, dry_run, skip_confirm, raw, **_):
    """Redeem shares from an intermediate vault (ERC4626 redeem)."""
    ctx.connect()
    try:
        account = resolve_account(account_alias, private_key)
        cv = credit_vault(iv_address)  # ERC4626-compatible interface
        decimals = 18  # EVault shares are always 18 decimals
        raw_shares = parse_amount(shares, decimals, raw=raw)
        recv = receiver or str(account.address)

        sim = simulate_tx(cv, "redeem", [raw_shares, recv, str(account.address)], sender=account)
        if not sim["success"]:
            click.echo(f"Simulation failed: {sim['error']}", err=True)
            _show_verbose_error(ctx, sim)
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
            _show_verbose_error(ctx, sim)
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
            _show_verbose_error(ctx, sim)
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
                _show_verbose_error(ctx, sim)
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

            receipt = execute_through_evc(cv, "teleport", [target_vault_address], account)
        else:
            op = teleport_operator()
            details.append(("Operator", str(op.address)))

            sim = simulate_tx(op, "executeTeleport", [vault_address, target_vault_address], sender=account)
            if not sim["success"]:
                click.echo(f"Simulation failed: {sim['error']}", err=True)
                _show_verbose_error(ctx, sim)
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
@click.argument("intermediate_vault")
@click.argument("target_vault")
@click.option("--vault-type", type=int, default=0, help="Vault type: 0=Euler, 1=Aave (default: 0)")
@click.option("--ltv", type=int, default=8500, help="Liquidation LTV in basis points (default: 8500 = 85%)")
@click.option("--target-asset", default=None, help="Debt token address (required for Aave, ignored for Euler)")
@tx_options
@pass_ctx
def create_vault(ctx: TwyneContext, intermediate_vault, target_vault, vault_type, ltv, target_asset,
                 account_alias, private_key, dry_run, skip_confirm, **_):
    """Create a new collateral vault via the factory.

    INTERMEDIATE_VAULT: The Twyne Intermediate Vault (CreditEVault) address.

    TARGET_VAULT: External lending vault (Euler eVault or Aave pool).
    """
    from ..constants import ZERO_ADDRESS

    if vault_type == 1 and not target_asset:
        raise click.UsageError("Aave vaults (--vault-type 1) require --target-asset <debt-token-address>.")

    if vault_type == 1:
        resolved = resolve_aave_factory_vault(intermediate_vault)
        if resolved:
            click.echo(
                f"Note: Aave vaults require the aToken wrapper address for the factory.\n"
                f"  Resolving {intermediate_vault[:10]}...{intermediate_vault[-4:]}"
                f" → {resolved[:10]}...{resolved[-4:]}",
                err=True,
            )
            intermediate_vault = resolved

    ctx.connect()
    try:
        account = resolve_account(account_alias, private_key)
        fct = collateral_vault_factory()
        target_asset = target_asset or ZERO_ADDRESS

        args = [vault_type, intermediate_vault, target_vault, ltv, target_asset]

        sim = simulate_through_evc(fct, "createCollateralVault", args, sender=account)
        if not sim["success"]:
            click.echo(f"Simulation failed: {sim['error']}", err=True)
            _show_verbose_error(ctx, sim)
            raise SystemExit(1)

        vault_type_name = "Euler" if vault_type == 0 else "Aave"
        details = [
            ("Factory", str(fct.address)),
            ("Vault Type", vault_type_name),
            ("Intermediate Vault", intermediate_vault),
            ("Target Vault", target_vault),
            ("Liq LTV", f"{ltv} bp ({ltv/100:.1f}%)"),
            ("Target Asset", target_asset),
            ("Sender", str(account.address)),
        ]

        if dry_run:
            click.echo("Dry run — simulation passed.")
            if sim.get("result"):
                click.echo(f"Predicted vault address: {_format_sim_address(sim['result'])}")
            return

        if not confirm_prompt(f"Create {vault_type_name} collateral vault", details, skip_confirm):
            click.echo("Cancelled.")
            return

        receipt = execute_through_evc(fct, "createCollateralVault", args, account)
        vault_address = _extract_vault_address_from_receipt(receipt, str(fct.address))
        if vault_address:
            click.echo(f"New vault address: {vault_address}")
        display_receipt(receipt)
    finally:
        ctx.disconnect()


# T_CollateralVaultCreated(address indexed vault) — keccak topic stored without
# prefix to avoid secret-pattern false positives (matches conftest convention).
_VAULT_CREATED_TOPIC_HEX = "d5c014427d17eead1b9e8111804901d992255c3982e066ff0b196835c2747e15"


def _extract_vault_address_from_receipt(receipt, factory_address: str) -> str | None:
    """Extract new vault address from T_CollateralVaultCreated event in receipt logs."""
    for log in receipt.logs:
        if str(log.get("address", "")).lower() == factory_address.lower():
            topics = log.get("topics", [])
            if len(topics) >= 2 and topics[0].hex() == _VAULT_CREATED_TOPIC_HEX:
                return "0x" + topics[1].hex()[-40:]
    return None


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
def execute(ctx: TwyneContext, batch_file, evc_address, account_alias, private_key, dry_run, skip_confirm,
            max_approve=False, skip_approval=False, **_):
    """Execute a batch of operations via EVC.batch().

    BATCH_FILE: Path to YAML or JSON batch definition file.
    """
    ctx.connect()
    try:
        account = resolve_account(account_alias, private_key)
        batch_data = parse_batch_file(batch_file)

        # Auto-approve tokens needed by batch operations
        requirements = collect_approval_requirements(batch_data, str(account.address))
        for req in requirements:
            if not ensure_allowance(req["token"], req["spender"], req["amount"], account,
                                    skip_confirm=skip_confirm, skip_approval=skip_approval,
                                    max_approve=max_approve):
                click.echo("Approval declined.")
                return

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
            _show_verbose_error(ctx, sim)
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
            _show_verbose_error(ctx, sim)
            raise SystemExit(1)
    finally:
        ctx.disconnect()
