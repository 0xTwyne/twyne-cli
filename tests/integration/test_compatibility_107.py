"""Execute CLI transaction paths against the post-1.0.7 mainnet fork."""

from unittest.mock import patch

import pytest
from click.testing import CliRunner

from twyne_cli.cli import cli
from twyne_cli.commands.tx import _format_sim_address
from twyne_cli.contracts import collateral_vault, collateral_vault_factory, credit_vault, erc20, get_address
from twyne_cli.risk import allowed_pairs
from twyne_cli.transactions import simulate_through_evc

from .conftest import (
    AAVE_AWSTETH_IV,
    AAVE_V3_POOL,
    EULER_EWETH_IV,
    EULER_TARGET_VAULT,
    WETH,
    WSTETH,
    _deal_weth,
    _rpc_call,
    _wait_for_receipt,
)


def invoke(account, args):
    with patch("twyne_cli.context.TwyneContext.connect"), \
         patch("twyne_cli.context.TwyneContext.disconnect"), \
         patch("twyne_cli.commands.tx.resolve_account", return_value=account):
        result = CliRunner().invoke(cli, args)
    assert result.exit_code == 0, f"{result.output}\n{result.exception}"
    return result


@pytest.mark.parametrize("kind", ["euler", "aave"])
def test_open_and_deposit_underlying(test_account, funded_iv, kind):
    owner = str(test_account.address)
    fct = collateral_vault_factory()
    if kind == "euler":
        _deal_weth(test_account, 10**18)
        iv, target, debt, ltv = EULER_EWETH_IV, EULER_TARGET_VAULT, str(credit_vault(EULER_TARGET_VAULT).asset()), 9000
        create_fn, create_args = "createEulerCollateralVault", [iv, target, ltv]
        options = []
        deposit, borrow = "0.02", "1"
    else:
        tx = _rpc_call("eth_sendTransaction", [{"from": owner, "to": WSTETH, "value": hex(10**18), "gas": hex(500000)}])
        assert _wait_for_receipt(tx)["status"] == "0x1"
        iv, target, debt, ltv = AAVE_AWSTETH_IV, AAVE_V3_POOL, WETH, 9700
        create_fn, create_args = "createAaveV3CollateralVault", [iv, target, ltv, debt]
        options = ["--vault-type", "1", "--target-asset", debt]
        deposit, borrow = "0.02", "0.001"
    predicted = simulate_through_evc(fct, create_fn, create_args, sender=test_account)
    assert predicted["success"], predicted
    cv = collateral_vault(_format_sim_address(predicted["result"]))
    before = int(erc20(debt).balanceOf(owner))
    invoke(test_account, ["tx", "factory", "open-position", iv, target, "--ltv", str(ltv),
                          "--deposit", deposit, "--borrow", borrow, "--yes", "--gas-limit", "3000000", *options])
    assert str(cv.borrower()) == owner
    assert str(cv.targetAsset()) == debt
    assert int(cv.maxRepay()) > 0
    assert int(erc20(debt).balanceOf(owner)) > before
    collateral_before = int(cv.totalAssetsDepositedOrReserved())
    invoke(test_account, ["tx", "collateral", "deposit-underlying", str(cv.address), "0.005", "--yes", "--gas-limit", "2000000"])
    assert int(cv.totalAssetsDepositedOrReserved()) > collateral_before
    assert int(erc20(str(cv.asset())).balanceOf(get_address("assetZap"))) == 0
    extra_debt = "0.1" if kind == "euler" else "0.0001"
    debt_before = int(cv.maxRepay())
    invoke(test_account, ["tx", "collateral", "borrow", str(cv.address), extra_debt, "--yes", "--gas-limit", "2000000"])
    assert int(cv.maxRepay()) > debt_before
    debt_before = int(cv.maxRepay())
    invoke(test_account, ["tx", "collateral", "repay", str(cv.address), extra_debt, "--yes", "--gas-limit", "2000000"])
    assert int(cv.maxRepay()) < debt_before
    # An unapproved dry run must never submit an approval or mutate the vault.
    nonce_before = _rpc_call("eth_getTransactionCount", [owner, "latest"])
    state_before = int(cv.totalAssetsDepositedOrReserved())
    with patch("twyne_cli.context.TwyneContext.connect"), \
         patch("twyne_cli.context.TwyneContext.disconnect"), \
         patch("twyne_cli.commands.tx.resolve_account", return_value=test_account):
        dry = CliRunner().invoke(cli, ["tx", "collateral", "deposit-underlying", str(cv.address), "0.005", "--dry-run", "--yes"])
    assert dry.exit_code == 2, dry.output
    assert "Insufficient allowance" in dry.output
    assert _rpc_call("eth_getTransactionCount", [owner, "latest"]) == nonce_before
    assert int(cv.totalAssetsDepositedOrReserved()) == state_before


def test_october_risk_and_factory_pairs(test_account):
    name = "aave_aPT-srUSDe-22OCT2026"
    iv = get_address(f"intermediateVaults.{name}")
    pairs = allowed_pairs(name, iv)
    assert {p["debt_vault"] for p in pairs} == {"USDC", "USDT", "USDe"}
    for pair in pairs:
        expected = (9450, 47, 9000, 9200) if pair["debt_vault"] in {"USDC", "USDT"} else (9800, 48, 9200, 9400)
        assert tuple(pair[k] for k in ("max_twyne_ltv_bps", "emode_id", "ext_borr_ltv_bps", "ext_liq_ltv_bps")) == expected
        assert pair["borrow_buffer_bps"] == 200
        sim = simulate_through_evc(collateral_vault_factory(), "createAaveV3CollateralVault",
                                   [iv, AAVE_V3_POOL, expected[0], pair["debt_asset"]], sender=test_account)
        assert sim["success"], sim
