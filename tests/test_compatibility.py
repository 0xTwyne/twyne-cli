"""Regressions for the 1.0.7 transaction and risk interfaces."""

from unittest.mock import MagicMock, patch

import click
import pytest

from twyne_cli.batch import encode_operation
from twyne_cli.chains import CHAINS, active_chain, set_active_chain
from twyne_cli.commands.tx import _create_vault_call
from twyne_cli.contracts import _load_abi, get_address
from twyne_cli.deposits import underlying_deposit_items
from twyne_cli.risk import pair_risk


def test_deposit_quote_uses_wrapper_shares_and_atomic_skim():
    wrapper, zap, cv = MagicMock(), MagicMock(), MagicMock()
    wrapper.asset.return_value = "underlying"
    wrapper.previewDeposit.return_value = 800_000
    zap.address = "zap"
    zap.zapUnderlying.encode_input.return_value = b"zap-call"
    cv.skim.encode_input.return_value = b"skim-call"
    with patch("twyne_cli.deposits.credit_vault", return_value=wrapper), \
         patch("twyne_cli.deposits.asset_zap", return_value=zap), \
         patch("twyne_cli.deposits.collateral_vault", return_value=cv):
        items = underlying_deposit_items("cv", "receipt", 1_000_000, "owner")
    wrapper.previewDeposit.assert_called_once_with(1_000_000)
    zap.zapUnderlying.encode_input.assert_called_once_with("underlying", 1_000_000, "cv", 799_200)
    assert items == [("zap", "owner", 0, b"zap-call"), ("cv", "owner", 0, b"skim-call")]


@pytest.mark.parametrize("amount,shares", [(0, 10), (-1, 10), (1, 0), (1, 1)])
def test_deposit_rejects_zero_output(amount, shares):
    wrapper = MagicMock()
    wrapper.previewDeposit.return_value = shares
    with patch("twyne_cli.deposits.credit_vault", return_value=wrapper), \
         pytest.raises(click.UsageError):
        underlying_deposit_items("cv", "receipt", amount, "owner")


def test_pair_risk_preserves_debt_key_and_return_order():
    vm = MagicMock()
    vm.liqParams.side_effect = [(9999, 9450, 200), (9999, 9800, 200)]
    usdc = pair_risk("same-iv", "usdc", 123, vm)
    usde = pair_risk("same-iv", "usde", 123, vm)
    assert usdc["max_twyne_ltv_bps"] == 9450
    assert usde["max_twyne_ltv_bps"] == 9800
    assert usdc["borrow_buffer_bps"] == 200
    assert usdc["external_liq_buffer_bps"] == 9999
    assert vm.liqParams.call_args_list[0].args == ("same-iv", "usdc")
    assert vm.liqParams.call_args_list[0].kwargs == {"block_identifier": 123}


def test_failed_risk_read_is_not_a_zero_buffer():
    vm = MagicMock()
    vm.liqParams.side_effect = RuntimeError("RPC unavailable")
    with pytest.raises(RuntimeError, match="RPC unavailable"):
        pair_risk("iv", "debt", vm=vm)


def test_megaeth_retains_legacy_factory_and_risk():
    previous = active_chain()
    try:
        set_active_chain(CHAINS[4326])
        iv = get_address("intermediateVaults.aave_aWETH")
        fn, args = _create_vault_call(1, iv, get_address("aavePool"), 9700, get_address("targetAsset"))
        assert fn == "createCollateralVault"
        assert args[:2] == [1, iv]
        assert any(a.get("name") == fn for a in _load_abi("CollateralVaultFactory"))
        vm = MagicMock()
        vm.maxTwyneLTVs.return_value = 9800
        vm.externalLiqBuffers.return_value = 9999
        assert pair_risk(iv, "debt", vm=vm)["borrow_buffer_bps"] == 0
        vm.liqParams.assert_not_called()
        assert any(a.get("name") == "depositUnderlying" for a in _load_abi("CollateralVault"))
    finally:
        set_active_chain(previous)


def test_unknown_factory_type_is_rejected():
    with pytest.raises(click.UsageError):
        _create_vault_call(2, "iv", "pool", 9000)


@pytest.mark.parametrize("action", ["borrow", "repay"])
def test_batch_debt_amount_uses_debt_decimals(action):
    cv, debt = MagicMock(), MagicMock()
    cv.targetAsset.return_value = "usdc"
    debt.decimals.return_value = 6
    with patch("twyne_cli.batch.collateral_vault", return_value=cv), \
         patch("twyne_cli.batch.erc20", return_value=debt) as token:
        encode_operation({"action": f"collateral.{action}", "vault": "cv", "amount": "1.25"}, "owner")
    token.assert_called_once_with("usdc")
    assert getattr(cv, action).encode_input.call_args.args[0] == 1_250_000
