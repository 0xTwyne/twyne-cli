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
    resolve_euler_factory_vault,
    teleport_operator,
    vault_manager,
)
from ..contracts import (
    evc as evc_contract,
)
from ..formatting import format_address
from ..swap import extract_multicall_data, get_swap_quote
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
    f = click.option("--gas-multiplier", "gas_multiplier", type=float, default=None,
                     help="Gas limit multiplier (overrides default 1.5x from config)")(f)
    f = click.option("--priority-fee", "priority_fee", default=None,
                     help="Max priority fee in gwei (e.g. '2.5' for 2.5 gwei)")(f)
    return f


def _build_gas_kwargs(gas_multiplier: float | None = None, priority_fee: str | None = None, **extras) -> dict:
    """Build gas-related kwargs for Ape contract calls.

    Can be called with explicit params or by unpacking **_ from a command:
        gas_kwargs = _build_gas_kwargs(**_)           # from catch-all kwargs
        gas_kwargs = _build_gas_kwargs(gas_multiplier=2.0, priority_fee="3")

    gas_multiplier: Override for gas limit multiplier. When set, manually estimates
        gas and applies the multiplier (bypassing ape-config.yaml's auto setting).
        The default 1.5x multiplier is set in ape-config.yaml; this flag overrides it.
    priority_fee: Max priority fee in gwei (e.g. "2.5" → 2_500_000_000 wei).
    """
    kwargs: dict = {}
    if priority_fee is not None:
        kwargs["max_priority_fee"] = int(float(priority_fee) * 1e9)
    if gas_multiplier is not None:
        kwargs["_gas_multiplier"] = gas_multiplier
    return kwargs


def _send_tx(contract_fn, args: list, sender, gas_kwargs: dict):
    """Send a transaction with optional gas multiplier override.

    If _gas_multiplier is in gas_kwargs, manually estimates gas and applies
    the multiplier before sending. Otherwise, relies on ape-config.yaml defaults
    (1.5x gas multiplier).

    Catches ContractLogicError and decodes common Twyne error selectors for
    user-friendly diagnostics before re-raising.
    """
    from ape.exceptions import ContractLogicError

    extra = {k: v for k, v in gas_kwargs.items() if k != "_gas_multiplier"}
    multiplier = gas_kwargs.get("_gas_multiplier")
    if multiplier is not None:
        try:
            estimate = contract_fn.estimate_gas_cost(*args, sender=sender, **extra)
            extra["gas"] = int(estimate * multiplier)
        except Exception:
            pass  # Fall back to ape-config.yaml defaults
    try:
        return contract_fn(*args, sender=sender, **extra)
    except ContractLogicError as e:
        _diagnose_revert(e)
        raise
    except Exception as e:
        # Ape trace enrichment can crash before raising ContractLogicError
        err_str = str(e)
        if "0x9773bb71" in err_str or "E_TransferFromFailed" in err_str:
            click.echo("\nTransaction reverted: token transferFrom failed.", err=True)
            click.echo("The vault tried to pull tokens from your wallet but the transfer was rejected.", err=True)
            click.echo("Common causes:", err=True)
            click.echo("  1. Insufficient token balance (do you have the token, not just ETH?)", err=True)
            click.echo("  2. Token approval pointing to wrong address", err=True)
            click.echo("  3. Predicted vault address mismatch", err=True)
        raise


def _diagnose_revert(e):
    """Print user-friendly diagnostics for known Twyne contract revert errors."""
    err_str = str(e)
    revert_msg = getattr(e, "revert_message", None) or ""
    # E_TransferFromFailed — vault couldn't pull tokens from user
    if "E_TransferFromFailed" in err_str or "0x9773bb71" in err_str:
        click.echo("\nTransaction reverted: token transferFrom failed.", err=True)
        click.echo("The vault tried to pull tokens from your wallet but the transfer was rejected.", err=True)
        click.echo("Common causes:", err=True)
        click.echo("  1. Insufficient token balance (do you have the token, not just ETH?)", err=True)
        click.echo("     - For WETH positions: wrap ETH first via the WETH contract deposit()", err=True)
        click.echo("  2. Token approval pointing to wrong address", err=True)
        click.echo("  3. Predicted vault address doesn't match actual created vault", err=True)
    elif "EVC_EmptyError" in err_str or "0x" in revert_msg:
        click.echo(f"\nTransaction reverted: {revert_msg or err_str}", err=True)
    else:
        click.echo(f"\nTransaction reverted: {e}", err=True)


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
@click.option("--max-debt", type=int, default=0,
              help="Max remaining debt after deleverage (default: 0 = repay all)")
@click.option("--withdraw", "withdraw_amount", default=None,
              help="Collateral to withdraw (raw units; default: same as flash loan amount)")
@tx_options
@pass_ctx
def deleverage(ctx: TwyneContext, vault_address, amount, protocol, slippage,
               max_debt, withdraw_amount,
               account_alias, private_key, dry_run, skip_confirm, raw, **_):
    """Execute deleverage via flash loan + Euler swap.

    Flash loans collateral, swaps to debt token, repays debt, withdraws collateral.
    Uses the Euler Swap API for swap routing.
    """
    ctx.connect()
    try:
        account = resolve_account(account_alias, private_key)
        cv = collateral_vault(vault_address)
        decimals = _get_token_decimals(cv)
        raw_amount = parse_amount(amount, decimals, raw=raw)

        # Derive token addresses for swap
        target_asset_addr = str(cv.targetAsset())
        asset_addr = str(cv.asset())
        underlying_addr = str(credit_vault(asset_addr).asset())

        op = deleverage_operator(protocol)

        # Default withdraw amount = flash loan amount
        raw_withdraw = int(withdraw_amount) if withdraw_amount else raw_amount

        # Get swap data from Euler Swap API
        quote = get_swap_quote(
            chain_id=1,
            token_in=underlying_addr,
            token_out=target_asset_addr,
            amount=raw_amount,
            receiver=str(op.address),
            origin=str(account.address),
            slippage=slippage,
        )
        swap_data = extract_multicall_data(quote)

        details = [
            ("Vault", vault_address),
            ("Protocol", protocol),
            ("Flash loan amount", f"{amount} (raw: {raw_amount})"),
            ("Max remaining debt", str(max_debt)),
            ("Withdraw amount", str(raw_withdraw)),
            ("Slippage", f"{slippage}%"),
            ("Operator", str(op.address)),
            ("Sender", str(account.address)),
        ]

        # 5-param interface: (collateralVault, flashloanAmount, maxDebt, withdrawCollateralAmount, swapData[])
        deleverage_args = [vault_address, raw_amount, max_debt, raw_withdraw, swap_data]

        sim = simulate_tx(op, "executeDeleverage", deleverage_args, sender=account)
        if not sim["success"]:
            click.echo(f"Simulation failed: {sim['error']}", err=True)
            _show_verbose_error(ctx, sim)
            raise SystemExit(1)

        if dry_run:
            click.echo("Dry run — simulation passed.")
            return

        gas_kwargs = _build_gas_kwargs(**_)
        if not confirm_prompt(f"Deleverage {amount} on {format_address(vault_address)}", details, skip_confirm):
            click.echo("Cancelled.")
            return

        receipt = _send_tx(op.executeDeleverage, deleverage_args, account, gas_kwargs)
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
# Close-position command (deleverage entire position via EVC batch)
# --------------------------------------------------------------------------- #


def _build_close_position_batch(
    evc_instance, cv_instance, operator, vault_address: str,
    flashloan_amount: int, max_debt: int, withdraw_collateral_amount: int,
    swap_data: list[bytes], sender: str, min_liq_ltv: int,
) -> list[tuple]:
    """Build EVC batch: enable operator -> lower liqLTV -> deleverage -> disable operator.

    When liqLTV > external LTV, credit is reserved from the intermediate vault.
    Even after external debt is repaid, this credit reservation blocks withdrawal.
    Lowering liqLTV to the minimum releases the reserved credit, enabling full withdrawal.
    All vault status checks are deferred to the end of the EVC batch.

    Per the EVC spec, EVC self-calls (setAccountOperator) use onBehalfOfAccount=zeroAddress.
    CV calls and operator calls use onBehalfOfAccount=sender.
    """
    from ..constants import ZERO_ADDRESS

    items = []

    # Item 1: Enable operator (EVC self-call → onBehalfOfAccount = zero)
    enable_data = evc_instance.setAccountOperator.encode_input(
        sender, str(operator.address), True
    )
    items.append((str(evc_instance.address), ZERO_ADDRESS, 0, enable_data))

    # Item 2: Lower liqLTV to release credit reservation from intermediate vault.
    # This calls _handleExcessCredit() inside the CV, returning reserved credit to the IV
    # and increasing maxWithdraw so the deleverage operator can withdraw all collateral.
    set_ltv_data = cv_instance.setTwyneLiqLTV.encode_input(min_liq_ltv)
    items.append((vault_address, sender, 0, set_ltv_data))

    # Item 3: Execute deleverage (operator call → onBehalfOfAccount = sender)
    deleverage_data = operator.executeDeleverage.encode_input(
        vault_address, flashloan_amount, max_debt, withdraw_collateral_amount, swap_data
    )
    items.append((str(operator.address), sender, 0, deleverage_data))

    # Item 4: Disable operator (EVC self-call → onBehalfOfAccount = zero)
    disable_data = evc_instance.setAccountOperator.encode_input(
        sender, str(operator.address), False
    )
    items.append((str(evc_instance.address), ZERO_ADDRESS, 0, disable_data))

    return items


@operators.command(name="close-position")
@click.argument("vault_address")
@click.option("--slippage", type=float, default=1.0,
              help="Swap slippage tolerance in percent (default: 1.0%)")
@click.option("--protocol", type=click.Choice(["euler", "aave"]), default="euler",
              help="Protocol integration to use")
@tx_options
@pass_ctx
def close_position(ctx: TwyneContext, vault_address, slippage, protocol,
                   account_alias, private_key, dry_run, skip_confirm, **_):
    """Close an entire position by selling collateral to repay debt.

    Uses the deleverage operator with a flash loan + swap to atomically
    repay all debt, withdraw all collateral, and return remaining tokens.

    VAULT_ADDRESS: The collateral vault to close.
    """
    ctx.connect()
    try:
        account = resolve_account(account_alias, private_key)
        cv = collateral_vault(vault_address)

        # 1. Read vault state
        total_assets = cv.totalAssetsDepositedOrReserved()
        max_release = cv.maxRelease()
        borrower_collateral = total_assets - max_release  # user's own collateral
        max_repay = cv.maxRepay()  # current debt in target asset units

        if max_repay == 0:
            click.echo("No debt to repay. Use 'withdraw' instead.")
            return

        # 2. Derive token addresses
        target_asset_addr = str(cv.targetAsset())
        target_vault_addr = str(cv.targetVault())
        asset_addr = str(cv.asset())  # eVault share token (e.g. eWETH)
        evault = credit_vault(asset_addr)
        underlying_addr = str(evault.asset())  # underlying (e.g. WETH)

        underlying_token = erc20(underlying_addr)
        underlying_symbol = underlying_token.symbol()
        underlying_decimals = underlying_token.decimals()
        target_token = erc20(target_asset_addr)
        target_symbol = target_token.symbol()
        target_decimals = target_token.decimals()

        # 3. Convert eVault shares to underlying amount for flash loan.
        # totalAssetsDepositedOrReserved and maxRelease are in eVault share units.
        # The deleverage operator's flashloanAmount is in underlying (e.g. WETH) units,
        # but withdrawCollateralAmount is in eVault share units (passed to redeemUnderlying).
        underlying_amount = evault.convertToAssets(borrower_collateral)

        # 4. Calculate deleverage parameters
        flashloan_amount = underlying_amount      # underlying units (WETH) for Morpho flash loan
        max_debt = 0                               # close entirely — require zero remaining debt
        # Use type(uint256).max sentinel: redeemUnderlying treats this as "withdraw maxWithdraw"
        # at execution time. Pre-calculating the exact amount fails due to interest accrual
        # between simulation and execution blocks shifting maxRelease().
        withdraw_collateral_amount = 2**256 - 1

        # 5. Compute minimum liqLTV to release credit reservation before withdrawal.
        # When liqLTV > extLiqLTV * buffer / MAXFACTOR, credit is reserved from the IV.
        # Lowering liqLTV to the minimum releases all credit, allowing full withdrawal.
        # Min liqLTV = ceil(extLiqLTV * buffer / MAXFACTOR)
        # NOTE: The deployed VaultManager maps externalLiqBuffers and maxTwyneLTVs
        # by collateral asset address (eVault share token), not by IV address.
        vm = vault_manager()
        ext_liq_buffer = vm.externalLiqBuffers(asset_addr)
        target_evault = credit_vault(target_vault_addr)
        ext_liq_ltv = target_evault.LTVLiquidation(asset_addr)
        from ..constants import MAXFACTOR
        min_liq_ltv = (ext_liq_ltv * ext_liq_buffer + MAXFACTOR - 1) // MAXFACTOR  # ceil division

        # 5. Get swap data from Euler Swap API
        op = deleverage_operator(protocol)
        quote = get_swap_quote(
            chain_id=1,
            token_in=underlying_addr,
            token_out=target_asset_addr,
            amount=flashloan_amount,
            receiver=str(op.address),
            origin=str(account.address),
            slippage=slippage,
        )
        # Frontend uses [swapperData] — single element wrapping the full swapper calldata
        swapper_data_hex = quote["swap"]["swapperData"]
        swap_data = [bytes.fromhex(swapper_data_hex[2:])] if swapper_data_hex != "0x" else []

        # 6. Display summary
        human_collateral = underlying_amount / 10**underlying_decimals
        human_debt = max_repay / 10**target_decimals
        human_swap_out = int(quote["amountOutMin"]) / 10**target_decimals

        click.echo(f"\nClose Position: {vault_address}")
        click.echo(f"  Collateral:    {human_collateral:.6f} {underlying_symbol}")
        click.echo(f"  Debt:          {human_debt:.6f} {target_symbol}")
        click.echo(f"  Swap output:   ~{human_swap_out:.6f} {target_symbol} (min, after {slippage}% slippage)")

        current_liq_ltv = cv.twyneLiqLTV()
        click.echo(f"  LiqLTV:        {current_liq_ltv / MAXFACTOR * 100:.2f}% -> {min_liq_ltv / MAXFACTOR * 100:.2f}% (lowered to release credit)")

        details = [
            ("Vault", vault_address),
            ("Protocol", protocol),
            ("Operator", str(op.address)),
            ("Flash loan", f"{human_collateral:.6f} {underlying_symbol}"),
            ("Debt to repay", f"{human_debt:.6f} {target_symbol}"),
            ("Max remaining debt", "0 (full close)"),
            ("LiqLTV change", f"{current_liq_ltv / MAXFACTOR * 100:.2f}% -> {min_liq_ltv / MAXFACTOR * 100:.2f}%"),
            ("Slippage", f"{slippage}%"),
            ("Sender", str(account.address)),
        ]

        # 7. Build EVC batch
        evc_instance = evc_contract()
        batch_items = _build_close_position_batch(
            evc_instance, cv, op, vault_address,
            flashloan_amount, max_debt, withdraw_collateral_amount,
            swap_data, str(account.address), min_liq_ltv,
        )

        # 7. Simulate batch
        sim = simulate_tx(evc_instance, "batch", [batch_items], sender=account)
        if not sim["success"]:
            click.echo(f"\nBatch simulation failed: {sim['error']}", err=True)
            _show_verbose_error(ctx, sim)
            raise SystemExit(1)

        if dry_run:
            click.echo("Dry run — simulation passed.")
            return

        # 8. Confirm and execute
        gas_kwargs = _build_gas_kwargs(**_)
        if not confirm_prompt(f"Close position {format_address(vault_address)}", details, skip_confirm):
            click.echo("Cancelled.")
            return

        receipt = _send_tx(evc_instance.batch, [batch_items], account, gas_kwargs)
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

        # For Euler vaults, the factory expects the collateral asset (eVault share token),
        # not the Twyne CreditEVault IV address. The VaultManager stores maxTwyneLTVs
        # and externalLiqBuffers keyed by the collateral asset address.
        if vault_type == 0:
            resolved = resolve_euler_factory_vault(intermediate_vault)
            if resolved:
                click.echo(
                    f"Note: Euler factory expects the collateral asset (eVault share token).\n"
                    f"  Resolving {intermediate_vault[:10]}...{intermediate_vault[-4:]}"
                    f" → {resolved[:10]}...{resolved[-4:]}",
                    err=True,
                )
                intermediate_vault = resolved

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

        gas_kwargs = _build_gas_kwargs(**_)
        receipt = execute_through_evc(fct, "createCollateralVault", args, account, **gas_kwargs)
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
# Open-position command (create vault + deposit + optional borrow in one batch)
# --------------------------------------------------------------------------- #


def _derive_deposit_underlying(intermediate_vault: str, vault_type: int) -> str:
    """Derive the underlying deposit token from the intermediate vault.

    Euler: IV.asset() = eVault share token → eVault.asset() = raw underlying (e.g. wstETH).
    Aave: IV.asset() = underlying token directly.
    """
    iv = credit_vault(intermediate_vault)
    if vault_type == 0:  # Euler
        evault_addr = iv.asset()
        return str(credit_vault(str(evault_addr)).asset())
    else:  # Aave
        return str(iv.asset())


def _derive_borrow_token(target_vault: str, target_asset: str | None, vault_type: int) -> str:
    """Derive the borrow/debt token address.

    Euler: target vault is an eVault, its asset() = borrow token.
    Aave: user provides --target-asset explicitly.
    """
    if vault_type == 1 and target_asset:  # Aave
        return target_asset
    # Euler — target vault asset
    return str(credit_vault(target_vault).asset())


def _build_open_position_batch(
    factory, create_args: list, predicted_address: str,
    raw_deposit: int, raw_borrow: int | None, sender: str,
) -> list[tuple]:
    """Build EVC batch items for create-vault + deposit + (optional) borrow."""
    items = []

    # Item 1: create vault
    create_data = factory.createCollateralVault.encode_input(*create_args)
    items.append((str(factory.address), sender, 0, create_data))

    # Item 2: deposit underlying
    cv = collateral_vault(predicted_address)
    deposit_data = cv.depositUnderlying.encode_input(raw_deposit)
    items.append((predicted_address, sender, 0, deposit_data))

    # Item 3 (optional): borrow
    if raw_borrow is not None:
        borrow_data = cv.borrow.encode_input(raw_borrow, sender)
        items.append((predicted_address, sender, 0, borrow_data))

    return items


@factory.command(name="open-position")
@click.argument("intermediate_vault")
@click.argument("target_vault")
@click.option("--vault-type", type=int, default=0, help="Vault type: 0=Euler, 1=Aave (default: 0)")
@click.option("--ltv", type=int, default=8500, help="Liquidation LTV in basis points (default: 8500 = 85%)")
@click.option("--target-asset", default=None, help="Debt token address (required for Aave, ignored for Euler)")
@click.option("--deposit", "deposit_amount", required=True, help="Amount of underlying token to deposit")
@click.option("--borrow", "borrow_amount", default=None, help="Amount to borrow (omit for deposit-only)")
@tx_options
@pass_ctx
def open_position(ctx: TwyneContext, intermediate_vault, target_vault, vault_type, ltv,
                  target_asset, deposit_amount, borrow_amount,
                  account_alias, private_key, dry_run, skip_confirm, **_):
    """Create a vault, deposit collateral, and optionally borrow — in one atomic EVC batch.

    INTERMEDIATE_VAULT: The Twyne Intermediate Vault (CreditEVault) address.

    TARGET_VAULT: External lending vault (Euler eVault or Aave pool).

    Uses depositUnderlying() so --deposit is in raw token units (e.g. wstETH, WETH),
    not receipt token units (ewstETH, awstETH).
    """
    from ..constants import ZERO_ADDRESS

    if vault_type == 1 and not target_asset:
        raise click.UsageError("Aave vaults (--vault-type 1) require --target-asset <debt-token-address>.")

    # Auto-resolve Aave IV → aToken wrapper
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

    # Save original IV for token derivation (Euler double-hop needs the CreditEVault)
    original_iv = intermediate_vault

    ctx.connect()
    try:
        account = resolve_account(account_alias, private_key)

        # For Euler vaults, the factory expects the collateral asset (eVault share token),
        # not the Twyne CreditEVault IV address. The VaultManager stores maxTwyneLTVs
        # and externalLiqBuffers keyed by the collateral asset address.
        if vault_type == 0:
            resolved = resolve_euler_factory_vault(intermediate_vault)
            if resolved:
                click.echo(
                    f"Note: Euler factory expects the collateral asset (eVault share token).\n"
                    f"  Resolving {intermediate_vault[:10]}...{intermediate_vault[-4:]}"
                    f" → {resolved[:10]}...{resolved[-4:]}",
                    err=True,
                )
                intermediate_vault = resolved

        fct = collateral_vault_factory()
        target_asset_addr = target_asset or ZERO_ADDRESS

        # 1. Predict vault address via simulation
        create_args = [vault_type, intermediate_vault, target_vault, ltv, target_asset_addr]
        sim = simulate_through_evc(fct, "createCollateralVault", create_args, sender=account)
        if not sim["success"]:
            click.echo(f"Simulation failed: {sim['error']}", err=True)
            _show_verbose_error(ctx, sim)
            raise SystemExit(1)
        predicted_address = _format_sim_address(sim["result"])

        # 2. Derive deposit token (underlying) and borrow token
        # Use original IV for Euler token derivation (needs CreditEVault for double-hop)
        deposit_token_addr = _derive_deposit_underlying(original_iv, vault_type)
        deposit_token = erc20(deposit_token_addr)
        deposit_decimals = deposit_token.decimals()
        deposit_symbol = deposit_token.symbol()

        borrow_token_addr = None
        borrow_symbol = None
        borrow_decimals = None
        if borrow_amount:
            borrow_token_addr = _derive_borrow_token(target_vault, target_asset, vault_type)
            borrow_token = erc20(borrow_token_addr)
            borrow_decimals = borrow_token.decimals()
            borrow_symbol = borrow_token.symbol()

        # 3. Parse amounts and check balance
        raw_deposit = parse_amount(deposit_amount, deposit_decimals)
        raw_borrow = parse_amount(borrow_amount, borrow_decimals) if borrow_amount else None

        # Check user has enough deposit token
        user_balance = deposit_token.balanceOf(str(account.address))
        if user_balance < raw_deposit:
            human_balance = user_balance / 10**deposit_decimals
            click.echo(f"Error: Insufficient {deposit_symbol} balance.", err=True)
            click.echo(f"  Required: {deposit_amount} {deposit_symbol}", err=True)
            click.echo(f"  Balance:  {human_balance} {deposit_symbol}", err=True)
            # Check if this is WETH and user might need to wrap ETH
            if deposit_symbol == "WETH":
                click.echo("\nHint: You may need to wrap ETH → WETH first:", err=True)
                click.echo(f"  cast send 0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2 "
                           f"--value {deposit_amount}ether 'deposit()' --private-key $PRIVATE_KEY", err=True)
            raise SystemExit(1)

        # 4. Display summary
        click.echo(f"\nPredicted vault: {predicted_address}")
        click.echo(f"Deposit token:   {deposit_symbol} ({deposit_token_addr})")
        if borrow_amount:
            click.echo(f"Borrow token:    {borrow_symbol} ({borrow_token_addr})")

        vault_type_name = "Euler" if vault_type == 0 else "Aave"
        details = [
            ("Factory", str(fct.address)),
            ("Vault Type", vault_type_name),
            ("Intermediate Vault", intermediate_vault),
            ("Target Vault", target_vault),
            ("Liq LTV", f"{ltv} bp ({ltv/100:.1f}%)"),
            ("Deposit", f"{deposit_amount} {deposit_symbol} (underlying)"),
        ]
        if borrow_amount:
            details.append(("Borrow", f"{borrow_amount} {borrow_symbol}"))
        details.append(("Sender", str(account.address)))

        if dry_run:
            click.echo("Dry run — vault creation simulation passed.")
            return

        # 5. Build gas kwargs from CLI flags
        gas_kwargs = _build_gas_kwargs(**_)

        # 6. Handle token approval (must happen before batch simulation,
        #    since depositUnderlying does transferFrom which needs allowance)
        if not ensure_allowance(deposit_token_addr, predicted_address, raw_deposit, account,
                                skip_confirm=skip_confirm, **gas_kwargs):
            click.echo("Approval declined.")
            return

        # 7. Build and simulate full batch (approval now in place)
        batch_items = _build_open_position_batch(
            fct, create_args, predicted_address,
            raw_deposit, raw_borrow, str(account.address),
        )
        evc_instance = evc_contract()
        sim_batch = simulate_tx(evc_instance, "batch", [batch_items], sender=account)
        if not sim_batch["success"]:
            err_msg = sim_batch.get("error", "")
            click.echo(f"\nBatch simulation failed: {err_msg}", err=True)
            # Detect token transfer failures specifically
            if "E_TransferFromFailed" in err_msg or "0x9773bb71" in err_msg:
                click.echo("The vault's depositUnderlying could not pull tokens from your wallet.", err=True)
                click.echo("Verify you have sufficient token balance (not just ETH).", err=True)
                raise SystemExit(1)
            click.echo("Note: vault creation simulation passed. The batch revert may be "
                       "due to Ape trace bugs or transient state.", err=True)
            _show_verbose_error(ctx, sim_batch)
            if not click.confirm("Proceed with on-chain submission anyway?", default=False):
                raise SystemExit(1)

        # 8. Confirm and execute
        if not confirm_prompt(f"Open {vault_type_name} position", details, skip_confirm):
            click.echo("Cancelled.")
            return

        receipt = _send_tx(evc_instance.batch, [batch_items], account, gas_kwargs)
        vault_address = _extract_vault_address_from_receipt(receipt, str(fct.address))
        if vault_address:
            click.echo(f"New vault address: {vault_address}")
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
def execute(ctx: TwyneContext, batch_file, evc_address, account_alias, private_key, dry_run, skip_confirm,
            max_approve=False, skip_approval=False, **_):
    """Execute a batch of operations via EVC.batch().

    BATCH_FILE: Path to YAML or JSON batch definition file.
    """
    ctx.connect()
    try:
        account = resolve_account(account_alias, private_key)
        batch_data = parse_batch_file(batch_file)
        gas_kwargs = _build_gas_kwargs(**_)

        # Auto-approve tokens needed by batch operations
        requirements = collect_approval_requirements(batch_data, str(account.address))
        for req in requirements:
            if not ensure_allowance(req["token"], req["spender"], req["amount"], account,
                                    skip_confirm=skip_confirm, skip_approval=skip_approval,
                                    max_approve=max_approve, **gas_kwargs):
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

        receipt = _send_tx(evc_instance.batch, [batch_items], account, gas_kwargs)
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
