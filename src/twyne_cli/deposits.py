"""Atomic underlying deposits for the 1.0.7 collateral vaults."""

import click

from .contracts import asset_zap, collateral_vault, credit_vault, uses_pair_risk


def underlying_deposit_items(
    vault_address: str, receipt_token: str, amount: int, sender: str,
    slippage_bps: int = 10,
) -> list[tuple]:
    """Quote wrapper shares, then encode AssetZap + skim in one EVC batch.

    receipt_token comes from IV.asset() for a new vault, or CV.asset() for an
    existing vault. This also works before CREATE2 deployment. The 10 bps
    default matches the frontend's underlying-deposit quote tolerance.
    """
    if amount <= 0:
        raise click.UsageError("Deposit amount must be greater than zero.")
    if not 0 <= slippage_bps < 10000:
        raise click.UsageError("Slippage must be between 0 and 9999 basis points.")
    if not uses_pair_risk():
        cv = collateral_vault(vault_address)
        return [(vault_address, sender, 0, cv.depositUnderlying.encode_input(amount))]
    wrapper = credit_vault(receipt_token)
    underlying = str(wrapper.asset())
    shares = int(wrapper.previewDeposit(amount))
    minimum = shares * (10000 - slippage_bps) // 10000
    if minimum <= 0:
        raise click.UsageError("Deposit is too small to produce a nonzero minimum share amount.")
    zap = asset_zap()
    cv = collateral_vault(vault_address)
    return [
        (str(zap.address), sender, 0, zap.zapUnderlying.encode_input(underlying, amount, vault_address, minimum)),
        (vault_address, sender, 0, cv.skim.encode_input()),
    ]


def morpho_deposit_items(
    vault_address: str, collateral_token: str, amount: int, sender: str,
    token_in: str | None = None, swap: dict | None = None,
) -> list[tuple]:
    """AssetZap.zap + skim for a Morpho vault, whose asset() is the raw collateral token.

    Without ``token_in`` (or with token_in == collateral) the zap is a plain transfer of
    the collateral token. With another ``token_in``, ``swap`` (from swap.build_swap with
    receiver = AssetZap) routes it to the collateral token; its ``amount_out_min`` is the
    zap's minimum. ``zapUnderlying`` cannot be used: the collateral is not an ERC-4626.
    """
    if amount <= 0:
        raise click.UsageError("Deposit amount must be greater than zero.")
    zap = asset_zap()
    cv = collateral_vault(vault_address)
    if token_in is None or token_in.lower() == collateral_token.lower():
        zap_data = zap.zap.encode_input(collateral_token, amount, [], vault_address, amount)
    else:
        if swap is None:
            raise click.UsageError("A swap route is required to deposit a token other than the collateral.")
        zap_data = zap.zap.encode_input(token_in, amount, swap["swap_data"], vault_address, swap["amount_out_min"])
    return [
        (str(zap.address), sender, 0, zap_data),
        (vault_address, sender, 0, cv.skim.encode_input()),
    ]
