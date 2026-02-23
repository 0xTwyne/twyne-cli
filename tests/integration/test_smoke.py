"""Smoke test — verify Anvil connection and fixtures work."""

from ape import Contract

from .conftest import ERC20_ABI, EULER_EWETH_IV, EVAULT_ABI, WETH, _rpc_call


def test_anvil_connected(ape_provider):
    """Ape is connected to Anvil fork at correct block."""
    result = _rpc_call("eth_blockNumber")
    assert int(result, 16) >= 24520000


def test_weth_has_decimals(ape_provider):
    """Can query WETH decimals on fork."""
    weth = Contract(WETH, abi=ERC20_ABI)
    assert weth.decimals() == 18


def test_intermediate_vault_has_assets(ape_provider):
    """Euler eWETH IV has deposited assets at block 24520000."""
    iv = Contract(EULER_EWETH_IV, abi=EVAULT_ABI)
    assert iv.totalAssets() > 0


def test_test_account_has_eth(test_account):
    """Test account is funded with ETH."""
    assert test_account.balance > 10**18  # at least 1 ETH


def test_funded_weth_fixture(test_account, funded_weth):
    """funded_weth fixture deals WETH to test account."""
    weth = Contract(WETH, abi=ERC20_ABI)
    balance = weth.balanceOf(test_account.address)
    assert balance >= funded_weth
