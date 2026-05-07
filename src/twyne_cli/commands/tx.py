"""Transaction commands — write operations for Twyne protocol."""

import click

from ..batch import build_batch_items, collect_approval_requirements, parse_batch_file
from ..chains import active_chain  # noqa: E402
from ..completions import complete_iv_address, complete_target_vault, complete_vault_address
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
from ..discover import (
    AAVE_V3,
    EULER_V2,
    discover_aave_positions,
    discover_all_positions,
    discover_euler_positions,
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


def _check_protocol_supported(protocol: str | None) -> None:
    """Raise UsageError if --protocol euler is selected on a non-Euler chain."""
    if protocol == "euler":
        chain = active_chain()
        if not chain.supports_euler:
            raise click.UsageError(
                f"Euler protocol is not available on {chain.name} (chain {chain.chain_id}). "
                "This deployment is Aave-only — use --protocol aave."
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
    f = click.option("--gas-limit", "gas_limit", type=int, default=None,
                     help="Fixed gas limit (bypasses gas estimation entirely)")(f)
    return f


def _build_gas_kwargs(gas_multiplier: float | None = None, priority_fee: str | None = None, gas_limit: int | None = None, **extras) -> dict:
    """Build gas-related kwargs for Ape contract calls.

    Can be called with explicit params or by unpacking **_ from a command:
        gas_kwargs = _build_gas_kwargs(**_)           # from catch-all kwargs
        gas_kwargs = _build_gas_kwargs(gas_multiplier=2.0, priority_fee="3")

    gas_multiplier: Override for gas limit multiplier. When set, manually estimates
        gas and applies the multiplier (bypassing ape-config.yaml's auto setting).
        The default 1.5x multiplier is set in ape-config.yaml; this flag overrides it.
    priority_fee: Max priority fee in gwei (e.g. "2.5" → 2_500_000_000 wei).
    gas_limit: Fixed gas limit in gas units. Bypasses gas estimation entirely.
    """
    kwargs: dict = {}
    if priority_fee is not None:
        kwargs["max_priority_fee"] = int(float(priority_fee) * 1e9)
    if gas_limit is not None:
        kwargs["gas"] = gas_limit
    elif gas_multiplier is not None:
        kwargs["_gas_multiplier"] = gas_multiplier
    return kwargs


def _send_tx(contract_fn, args: list, sender, gas_kwargs: dict):
    """Send a transaction with optional gas multiplier override.

    If _gas_multiplier is in gas_kwargs, manually estimates gas and applies
    the multiplier before sending. Otherwise, relies on ape-config.yaml defaults
    (1.5x gas multiplier).

    When gas estimation fails (common with complex EVC batches), falls back to
    a fixed gas limit so Ape doesn't re-attempt estimation during send.

    Catches ContractLogicError and decodes common Twyne error selectors for
    user-friendly diagnostics before re-raising.
    """
    from ape.exceptions import ContractLogicError

    extra = {k: v for k, v in gas_kwargs.items() if k != "_gas_multiplier"}
    multiplier = gas_kwargs.get("_gas_multiplier")
    if "gas" in extra:
        click.echo(f"  Using fixed gas limit: {extra['gas']:,}", err=True)
    elif multiplier is not None:
        try:
            estimate = contract_fn.estimate_gas_cost(*args, sender=sender, **extra)
            extra["gas"] = int(estimate * multiplier)
        except Exception:
            # Gas estimation failed (e.g. EVC_EmptyError on complex batches).
            # Set a fallback so Ape doesn't re-attempt estimation during send.
            extra["gas"] = 5_000_000
            click.echo("  Gas estimation failed; using fallback 5,000,000 gas limit.", err=True)
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
@click.argument("vault_address", shell_complete=complete_vault_address)
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
@click.argument("vault_address", shell_complete=complete_vault_address)
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
@click.argument("vault_address", shell_complete=complete_vault_address)
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
@click.argument("vault_address", shell_complete=complete_vault_address)
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
@click.argument("vault_address", shell_complete=complete_vault_address)
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
@click.argument("vault_address", shell_complete=complete_vault_address)
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
@click.argument("vault_address", shell_complete=complete_vault_address)
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
@click.argument("vault_address", shell_complete=complete_vault_address)
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
@click.argument("vault_address", shell_complete=complete_vault_address)
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
@click.argument("iv_address", shell_complete=complete_iv_address)
@click.argument("amount")
@click.option("--protocol", type=click.Choice(["euler", "aave"]), default="euler",
              help="Which wrapper to use (euler or aave)")
@tx_options
@pass_ctx
def credit_deposit(ctx: TwyneContext, iv_address, amount, protocol, account_alias, private_key, dry_run, skip_confirm, raw, max_approve, skip_approval, **_):
    """Deposit underlying asset into an intermediate vault via wrapper."""
    _check_protocol_supported(protocol)
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
@click.argument("iv_address", shell_complete=complete_iv_address)
@click.argument("amount")
@click.option("--protocol", type=click.Choice(["euler", "aave"]), default="euler",
              help="Which wrapper to use (euler or aave)")
@tx_options
@pass_ctx
def deposit_underlying_credit(ctx: TwyneContext, iv_address, amount, protocol, account_alias, private_key, dry_run, skip_confirm, raw, max_approve, skip_approval, **_):
    """Deposit underlying asset into intermediate vault (alias for deposit)."""
    _check_protocol_supported(protocol)
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
@click.argument("iv_address", shell_complete=complete_iv_address)
@click.argument("amount")
@tx_options
@pass_ctx
def deposit_atokens(ctx: TwyneContext, iv_address, amount, account_alias, private_key, dry_run, skip_confirm, raw, max_approve, skip_approval, **_):
    """Deposit Aave aTokens into an intermediate vault via aToken wrapper."""
    ctx.connect()
    try:
        account = resolve_account(account_alias, private_key)
        wrapper = aave_atoken_wrapper(iv_address)

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
@click.argument("iv_address", shell_complete=complete_iv_address)
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
@click.argument("iv_address", shell_complete=complete_iv_address)
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
    chain = active_chain()
    if not chain.supports_operators:
        raise click.UsageError(
            f"Operator commands are not supported on {chain.name} (chain {chain.chain_id}). "
            "Operators have not been deployed on this chain yet."
        )


@operators.command()
@click.argument("vault_address", shell_complete=complete_vault_address)
@click.argument("amount")
@click.option("--protocol", type=click.Choice(["euler", "aave"]), default="euler",
              help="Protocol integration to use")
@click.option("--slippage", type=float, default=DEFAULT_SLIPPAGE,
              help=f"Swap slippage tolerance (default: {DEFAULT_SLIPPAGE}%)")
@click.option("--underlying-deposit", "underlying_deposit", default="0",
              help="Additional underlying collateral to deposit from wallet (default: 0)")
@tx_options
@pass_ctx
def leverage(ctx: TwyneContext, vault_address, amount, protocol, slippage,
             underlying_deposit,
             account_alias, private_key, dry_run, skip_confirm, raw, **_):
    """Execute leverage via Morpho flash loan + Euler swap.

    Flash borrows target asset, swaps to collateral, deposits into vault,
    then borrows to repay the flash loan — all atomically in an EVC batch.

    AMOUNT is the flash loan size in target asset units (e.g., USDC).
    """
    import time

    from ..constants import DEFAULT_DEADLINE_OFFSET, ZERO_ADDRESS

    ctx.connect()
    try:
        account = resolve_account(account_alias, private_key)
        cv = collateral_vault(vault_address)

        # Derive token addresses from vault state
        collateral_addr = str(cv.asset())       # eVault share token (e.g. eWETH)
        target_asset_addr = str(cv.targetAsset())  # debt token (e.g. USDC)
        evault = credit_vault(collateral_addr)
        underlying_addr = str(evault.asset())   # underlying collateral (e.g. WETH)

        # Get token info for amount parsing and display
        target_token = erc20(target_asset_addr)
        target_decimals = target_token.decimals()
        target_symbol = target_token.symbol()
        underlying_token = erc20(underlying_addr)
        underlying_decimals = underlying_token.decimals()
        underlying_symbol = underlying_token.symbol()

        # Parse flash loan amount in target asset units (e.g. USDC with 6 decimals)
        raw_flashloan = parse_amount(amount, target_decimals, raw=raw)

        # Parse optional additional underlying collateral deposit from wallet
        raw_underlying = 0
        if underlying_deposit != "0":
            raw_underlying = parse_amount(underlying_deposit, underlying_decimals, raw=raw)

        op = leverage_operator(protocol)
        deadline = int(time.time()) + DEFAULT_DEADLINE_OFFSET

        # Get the target vault (e.g. eUSDC) — needed by swap API for deposit cleanup
        target_vault_addr = str(cv.targetVault())

        # Get swap quote from Euler Swap API: target asset → underlying collateral
        # receiver = eVault (collateral_addr) since underlying must land there for skim
        # vault_in = target vault (eUSDC) for deposit cleanup of unused input tokens
        quote = get_swap_quote(
            chain_id=active_chain().chain_id,
            token_in=target_asset_addr,       # selling target asset (e.g. USDC)
            token_out=underlying_addr,         # buying underlying collateral (e.g. WETH)
            amount=raw_flashloan,
            receiver=collateral_addr,          # Euler eVault receives output for skim
            origin=str(account.address),
            slippage=slippage,
            deadline=deadline,
            vault_in=target_vault_addr,        # target vault for input token dust cleanup
        )

        # Extract swap data — swapperData format (single pre-compiled multicall blob)
        # Frontend wraps this as 1-element bytes[] array for ISwapper.multicall
        swapper_data_hex = quote["swap"]["swapperData"]
        swap_data = [bytes.fromhex(swapper_data_hex[2:])] if swapper_data_hex != "0x" else []

        # Minimum collateral output from swap (slippage already applied by API)
        min_amount_out = int(quote["amountOutMin"])

        # Handle approval for additional underlying deposit (operator pulls from user)
        if raw_underlying > 0:
            gas_kw = _build_gas_kwargs(**_)
            if not ensure_allowance(underlying_addr, str(op.address), raw_underlying,
                                    account, skip_confirm=skip_confirm, **gas_kw):
                click.echo("Approval declined.")
                return

        # Build EVC batch: enable operator → executeLeverage → disable operator
        evc_instance = evc_contract()
        sender_addr = str(account.address)
        items = []

        # Item 1: Enable operator (EVC self-call → onBehalfOfAccount = zero)
        enable_data = evc_instance.setAccountOperator.encode_input(
            sender_addr, str(op.address), True
        )
        items.append((str(evc_instance.address), ZERO_ADDRESS, 0, enable_data))

        # Item 2: Execute leverage (operator call → onBehalfOfAccount = sender)
        # 7-param Morpho interface: (collateralVault, underlyingCollateralAmount, collateralAmount,
        #   flashloanAmount, minAmountOut, deadline, swapData[])
        leverage_data = op.executeLeverage.encode_input(
            vault_address, raw_underlying, 0, raw_flashloan,
            min_amount_out, deadline, swap_data
        )
        items.append((str(op.address), sender_addr, 0, leverage_data))

        # Item 3: Disable operator (EVC self-call → onBehalfOfAccount = zero)
        disable_data = evc_instance.setAccountOperator.encode_input(
            sender_addr, str(op.address), False
        )
        items.append((str(evc_instance.address), ZERO_ADDRESS, 0, disable_data))

        # Display summary
        human_flashloan = raw_flashloan / 10**target_decimals
        human_min_out = min_amount_out / 10**underlying_decimals

        click.echo(f"\nLeverage: {vault_address}")
        click.echo(f"  Flash loan:         {human_flashloan:.6f} {target_symbol}")
        click.echo(f"  Min collateral out: {human_min_out:.6f} {underlying_symbol} (after {slippage}% slippage)")
        if raw_underlying > 0:
            human_underlying = raw_underlying / 10**underlying_decimals
            click.echo(f"  Extra deposit:      {human_underlying:.6f} {underlying_symbol} from wallet")

        details = [
            ("Vault", vault_address),
            ("Protocol", protocol),
            ("Flash loan", f"{human_flashloan:.6f} {target_symbol} (raw: {raw_flashloan})"),
            ("Min collateral out", f"{human_min_out:.6f} {underlying_symbol}"),
            ("Slippage", f"{slippage}%"),
            ("Operator", str(op.address)),
            ("Sender", sender_addr),
        ]

        # Simulate batch
        sim = simulate_tx(evc_instance, "batch", [items], sender=account)
        if not sim["success"]:
            click.echo(f"\nBatch simulation failed: {sim['error']}", err=True)
            _show_verbose_error(ctx, sim)
            raise SystemExit(1)

        if dry_run:
            click.echo("Dry run — simulation passed.")
            return

        # Confirm and execute
        gas_kwargs = _build_gas_kwargs(**_)
        if not confirm_prompt(f"Leverage {human_flashloan:.6f} {target_symbol} on {format_address(vault_address)}",
                              details, skip_confirm):
            click.echo("Cancelled.")
            return

        receipt = _send_tx(evc_instance.batch, [items], account, gas_kwargs)
        display_receipt(receipt)
    finally:
        ctx.disconnect()


@operators.command()
@click.argument("vault_address", shell_complete=complete_vault_address)
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
            chain_id=active_chain().chain_id,
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
@click.argument("vault_address", shell_complete=complete_vault_address)
@click.argument("target_vault_address", shell_complete=complete_target_vault)
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
@click.argument("vault_address", shell_complete=complete_vault_address)
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
        # VaultManager maps externalLiqBuffers and maxTwyneLTVs by IV address (v1.0.5+).
        vm = vault_manager()
        iv_addr = str(cv.intermediateVault())
        ext_liq_buffer = vm.externalLiqBuffers(iv_addr)
        target_evault = credit_vault(target_vault_addr)
        ext_liq_ltv = target_evault.LTVLiquidation(asset_addr)
        from ..constants import MAXFACTOR
        min_liq_ltv = (ext_liq_ltv * ext_liq_buffer + MAXFACTOR - 1) // MAXFACTOR  # ceil division

        # 5. Get swap data from Euler Swap API
        op = deleverage_operator(protocol)
        quote = get_swap_quote(
            chain_id=active_chain().chain_id,
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
@click.argument("intermediate_vault", shell_complete=complete_iv_address)
@click.argument("target_vault", shell_complete=complete_target_vault)
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

    ctx.connect()
    try:
        account = resolve_account(account_alias, private_key)

        # v1.0.5: factory.createCollateralVault takes the IV address directly as
        # _intermediateVault (verified via vaultManager.isIntermediateVault). Pre-v1.0.5
        # this slot held the eVault share token / aTokenWrapper, hence the resolve helpers
        # in contracts.py — no longer needed in this path.
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
@click.argument("intermediate_vault", shell_complete=complete_iv_address)
@click.argument("target_vault", shell_complete=complete_target_vault)
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

    # v1.0.5: the IV passed by the user is exactly what the factory expects.
    original_iv = intermediate_vault

    ctx.connect()
    try:
        account = resolve_account(account_alias, private_key)

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


# --------------------------------------------------------------------------- #
# Position migration commands (discover + migrate)
# --------------------------------------------------------------------------- #


@tx.command(name="discover-positions")
@click.argument("wallet_address")
@click.option("--protocol", type=click.Choice(["euler", "aave"]), default=None,
              help="Only discover from a specific protocol (default: both)")
@pass_ctx
def discover_positions(ctx: TwyneContext, wallet_address, protocol):
    """Discover migratable positions from Euler V2 and Aave V3.

    WALLET_ADDRESS: The wallet to scan for migratable lending positions.
    """
    from ..formatting import output_table

    _check_protocol_supported(protocol)
    chain = active_chain()

    ctx.connect()
    try:
        if protocol == "euler":
            positions = discover_euler_positions(wallet_address)
        elif protocol == "aave":
            positions = discover_aave_positions(wallet_address)
        elif not chain.supports_euler:
            # Non-Euler chains: default to Aave-only when --protocol is unspecified.
            positions = discover_aave_positions(wallet_address)
        else:
            positions = discover_all_positions(wallet_address)

        if not positions:
            click.echo(f"No migratable positions found for {format_address(wallet_address)}.")
            return

        click.echo(f"\nMigratable Positions for {format_address(wallet_address)}")

        headers = ["#", "Protocol", "Collateral", "Debt", "Current LTV", "Liq LTV", "Max Twyne LTV"]
        rows = []
        for i, pos in enumerate(positions, 1):
            collateral_human = pos.collateral_amount / 10**pos.collateral_decimals
            debt_human = pos.debt_amount / 10**pos.debt_decimals
            liq_ltv_str = f"{pos.liq_ltv_bps / 100:.1f}%" if pos.liq_ltv_bps else "N/A"
            max_twyne_str = f"{pos.max_twyne_ltv_bps / 100:.1f}%" if pos.max_twyne_ltv_bps else "N/A"
            rows.append([
                str(i),
                pos.protocol.capitalize(),
                f"{collateral_human:.4f} {pos.collateral_symbol}",
                f"{debt_human:.4f} {pos.debt_symbol}",
                f"{pos.ltv_bps / 100:.1f}%",
                liq_ltv_str,
                max_twyne_str,
            ])

        output_table(headers, rows)
    finally:
        ctx.disconnect()


@tx.command(name="migrate-position")
@click.argument("wallet_address")
@click.option("--protocol", type=click.Choice(["euler", "aave"]), default=None,
              help="Override protocol detection")
@click.option("--ltv", type=int, default=8500,
              help="Liquidation LTV in basis points (default: 8500 = 85%)")
@click.option("--position", "position_num", type=int, default=None,
              help="Position number from discover-positions (skip interactive selection)")
@tx_options
@pass_ctx
def migrate_position(ctx: TwyneContext, wallet_address, protocol, ltv, position_num,
                     account_alias, private_key, dry_run, skip_confirm, **_):
    """Migrate an existing Euler/Aave position to Twyne in one transaction.

    WALLET_ADDRESS: The wallet holding the lending position to migrate.

    First discovers migratable positions, then builds and executes the
    migration transaction (create vault + teleport).
    """
    _check_protocol_supported(protocol)
    chain = active_chain()

    ctx.connect()
    try:
        account = resolve_account(account_alias, private_key)

        # 1. Discover positions
        if protocol == "euler":
            positions = discover_euler_positions(wallet_address)
        elif protocol == "aave":
            positions = discover_aave_positions(wallet_address)
        elif not chain.supports_euler:
            positions = discover_aave_positions(wallet_address)
        else:
            positions = discover_all_positions(wallet_address)

        if not positions:
            click.echo("No migratable positions found.")
            return

        # 2. Select position
        if position_num is not None:
            if position_num < 1 or position_num > len(positions):
                raise click.UsageError(f"Invalid position number {position_num}. Valid: 1-{len(positions)}")
            selected = positions[position_num - 1]
        elif len(positions) == 1:
            selected = positions[0]
            click.echo(f"Found 1 migratable position ({selected.protocol.capitalize()}).")
        else:
            # Show positions and prompt
            for i, pos in enumerate(positions, 1):
                c_human = pos.collateral_amount / 10**pos.collateral_decimals
                d_human = pos.debt_amount / 10**pos.debt_decimals
                click.echo(f"  {i}. [{pos.protocol.capitalize()}] "
                           f"{c_human:.4f} {pos.collateral_symbol} / "
                           f"{d_human:.4f} {pos.debt_symbol} "
                           f"(LTV: {pos.ltv_bps / 100:.1f}%)")
            choice = click.prompt("Select position", type=int)
            if choice < 1 or choice > len(positions):
                raise click.UsageError(f"Invalid choice {choice}.")
            selected = positions[choice - 1]

        click.echo(f"\nMigrating {selected.protocol.capitalize()} position:")
        c_human = selected.collateral_amount / 10**selected.collateral_decimals
        d_human = selected.debt_amount / 10**selected.debt_decimals
        click.echo(f"  Collateral: {c_human:.4f} {selected.collateral_symbol}")
        click.echo(f"  Debt:       {d_human:.4f} {selected.debt_symbol}")
        click.echo(f"  Target LTV: {ltv / 100:.1f}%")

        # 3. Route to protocol-specific migration
        if selected.protocol == "euler":
            _migrate_euler(ctx, selected, ltv, account, dry_run, skip_confirm, **_)
        else:
            _migrate_aave(ctx, selected, ltv, account, dry_run, skip_confirm, **_)
    finally:
        ctx.disconnect()


def _migrate_euler(ctx, position, ltv, account, dry_run, skip_confirm, **gas_extra):
    """Execute Euler position migration: createCV + teleport."""
    fct = collateral_vault_factory()
    sender = str(account.address)

    # Resolve IV → eVault share token for factory
    iv_addr = position.intermediate_vault
    factory_vault = resolve_euler_factory_vault(iv_addr)
    if factory_vault:
        click.echo(f"  Resolved IV → eVault share: {format_address(factory_vault)}", err=True)
    else:
        factory_vault = iv_addr

    # Derive target vault (the debt eVault is the target vault for Euler)
    target_vault = position.debt_address
    target_asset = "0x0000000000000000000000000000000000000000"

    # 1. Simulate createCollateralVault to predict CV address
    create_args = [EULER_V2, factory_vault, target_vault, ltv, target_asset]
    sim = simulate_through_evc(fct, "createCollateralVault", create_args, sender=account)
    if not sim["success"]:
        click.echo(f"Create vault simulation failed: {sim['error']}", err=True)
        _show_verbose_error(ctx, sim)
        raise SystemExit(1)
    predicted_cv = _format_sim_address(sim["result"])
    click.echo(f"  Predicted vault: {predicted_cv}")

    # 2. Approve eVault shares to the new CV
    gas_kwargs = _build_gas_kwargs(**gas_extra)
    if not dry_run:
        if not ensure_allowance(
            position.collateral_address, predicted_cv, position.collateral_amount, account,
            skip_confirm=skip_confirm, **gas_kwargs,
        ):
            click.echo("Approval declined.")
            return

    # 3. Build EVC batch: [createCV, teleport]
    cv = collateral_vault(predicted_cv)
    # teleport(toDeposit, toBorrow, subAccountId)
    # uint256.max for toBorrow = auto-repay all debt
    uint256_max = 2**256 - 1
    teleport_data = cv.teleport.encode_input(
        position.collateral_amount, uint256_max, position.sub_account_id,
    )
    create_data = fct.createCollateralVault.encode_input(*create_args)

    batch_items = [
        (str(fct.address), sender, 0, create_data),
        (predicted_cv, sender, 0, teleport_data),
    ]

    # 4. Simulate batch
    evc_instance = evc_contract()
    sim_batch = simulate_tx(evc_instance, "batch", [batch_items], sender=account)
    if not sim_batch["success"]:
        err_msg = sim_batch.get("error", "")
        click.echo(f"\nBatch simulation failed: {err_msg}", err=True)
        _show_verbose_error(ctx, sim_batch)
        raise SystemExit(1)

    details = [
        ("Protocol", "Euler V2"),
        ("Intermediate Vault", iv_addr),
        ("Target Vault", target_vault),
        ("Liq LTV", f"{ltv} bp ({ltv / 100:.1f}%)"),
        ("Collateral", f"{position.collateral_amount} shares"),
        ("Sub-account", str(position.sub_account_id)),
        ("Predicted CV", predicted_cv),
        ("Sender", sender),
    ]

    if dry_run:
        click.echo("\nDry run — batch simulation passed.")
        return

    # 5. Confirm and execute
    if not confirm_prompt("Migrate Euler position to Twyne", details, skip=skip_confirm):
        click.echo("Cancelled.")
        return

    receipt = _send_tx(evc_instance.batch, [batch_items], account, gas_kwargs)
    vault_address = _extract_vault_address_from_receipt(receipt, str(fct.address))
    if vault_address:
        click.echo(f"New Twyne vault: {vault_address}")
    display_receipt(receipt)


def _migrate_aave(ctx, position, ltv, account, dry_run, skip_confirm, **gas_extra):
    """Execute Aave position migration: createCV + enable operator + teleport + disable operator."""
    fct = collateral_vault_factory()
    sender = str(account.address)

    iv_addr = position.intermediate_vault
    click.echo(f"  Intermediate vault: {format_address(iv_addr)}", err=True)

    # On mainnet the factory's _intermediateVault param expects the aToken wrapper,
    # which is registered in VaultManager (not the IV address itself).
    wrapper_addr = resolve_aave_factory_vault(iv_addr)
    if not wrapper_addr:
        click.echo(f"Cannot resolve aToken wrapper for IV {iv_addr}", err=True)
        raise SystemExit(1)
    click.echo(f"  aToken wrapper: {format_address(wrapper_addr)}", err=True)

    # Target vault for Aave is the Aave V3 Pool itself (not an Euler eVault)
    from ..constants import AAVE_V3_POOL
    target_vault = AAVE_V3_POOL
    target_asset = position.debt_address  # WETH

    # 1. Simulate createCollateralVault to predict CV address
    create_args = [AAVE_V3, wrapper_addr, target_vault, ltv, target_asset]
    sim = simulate_through_evc(fct, "createCollateralVault", create_args, sender=account)
    if not sim["success"]:
        click.echo(f"Create vault simulation failed: {sim['error']}", err=True)
        _show_verbose_error(ctx, sim)
        raise SystemExit(1)
    predicted_cv = _format_sim_address(sim["result"])
    click.echo(f"  Predicted vault: {predicted_cv}")

    # 2. Approve aTokens to teleport operator
    op = teleport_operator()
    op_addr = str(op.address)
    gas_kwargs = _build_gas_kwargs(**gas_extra)

    if not dry_run:
        if not ensure_allowance(
            position.collateral_address, op_addr, position.collateral_amount, account,
            skip_confirm=skip_confirm, max_approve=True, **gas_kwargs,
        ):
            click.echo("Approval declined.")
            return

    # 3. Build EVC batch: [createCV, enableOperator, executeTeleport, disableOperator]
    # Matches the pattern from AaveOperatorsTest.t.sol:test_AaveV3TeleportOperator_SingleBatch
    evc_instance = evc_contract()
    evc_addr = str(evc_instance.address)
    zero_addr = "0x0000000000000000000000000000000000000000"

    create_data = fct.createCollateralVault.encode_input(*create_args)

    # onBehalfOfAccount must be address(0) when targeting the EVC itself
    enable_op_data = evc_instance.setAccountOperator.encode_input(sender, op_addr, True)
    disable_op_data = evc_instance.setAccountOperator.encode_input(sender, op_addr, False)

    # Use type(uint256).max for both amounts — the contract reads current
    # on-chain balances at execution time (AaveV3TeleportOperator lines 71-75).
    # This eliminates timing/staleness issues between simulation and execution.
    uint256_max = 2**256 - 1
    teleport_data = op.executeTeleport.encode_input(
        predicted_cv, uint256_max, uint256_max,
    )

    batch_items = [
        (str(fct.address), sender, 0, create_data),       # createCV
        (evc_addr, zero_addr, 0, enable_op_data),          # enableOp (address(0))
        (op_addr, sender, 0, teleport_data),               # executeTeleport
        (evc_addr, zero_addr, 0, disable_op_data),         # disableOp (address(0))
    ]

    details = [
        ("Protocol", "Aave V3"),
        ("Intermediate Vault", iv_addr),
        ("aToken Wrapper", wrapper_addr),
        ("Target Vault", target_vault),
        ("Liq LTV", f"{ltv} bp ({ltv / 100:.1f}%)"),
        ("Collateral", f"{position.collateral_amount} aTokens (contract reads current balance)"),
        ("Debt", f"{position.debt_amount} (contract reads current balance)"),
        ("Teleport Operator", op_addr),
        ("Predicted CV", predicted_cv),
        ("Sender", sender),
    ]

    if dry_run:
        # Full batch simulation requires aToken approval to teleport operator,
        # which hasn't been granted yet (approval is done before the batch in
        # non-dry-run mode). The createCV simulation above already validates
        # the factory arguments.
        click.echo("\nDry run — createCV simulation passed.")
        click.echo("Batch items (4):")
        click.echo("  1. createCollateralVault (validated)")
        click.echo("  2. setAccountOperator(enable)")
        click.echo("  3. executeTeleport")
        click.echo("  4. setAccountOperator(disable)")
        for label, value in details:
            click.echo(f"  {label}: {value}")
        return

    # 4. Simulate batch (approval has been granted, so full simulation works)
    sim_batch = simulate_tx(evc_instance, "batch", [batch_items], sender=account)
    if not sim_batch["success"]:
        err_msg = sim_batch.get("error", "")
        click.echo(f"\nBatch simulation failed: {err_msg}", err=True)
        _show_verbose_error(ctx, sim_batch)
        raise SystemExit(1)

    # 5. Confirm and execute
    if not confirm_prompt("Migrate Aave position to Twyne", details, skip=skip_confirm):
        click.echo("Cancelled.")
        return

    receipt = _send_tx(evc_instance.batch, [batch_items], account, gas_kwargs)
    vault_address = _extract_vault_address_from_receipt(receipt, str(fct.address))
    if vault_address:
        click.echo(f"New Twyne vault: {vault_address}")
    display_receipt(receipt)
