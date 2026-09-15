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
