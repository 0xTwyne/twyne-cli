"""Tests for position discovery (Euler V2 and Aave V3)."""

from unittest.mock import MagicMock, patch

# --------------------------------------------------------------------------- #
# Euler discovery tests
# --------------------------------------------------------------------------- #

FAKE_EVAULT = "0x1111111111111111111111111111111111111111"
FAKE_DEBT_EVAULT = "0x2222222222222222222222222222222222222222"
FAKE_IV = "0x7613D202Af490c3d1cE1873b0a7022a34E89815f"
FAKE_UNDERLYING = "0x3333333333333333333333333333333333333333"
FAKE_DEBT_UNDERLYING = "0x4444444444444444444444444444444444444444"
FAKE_USER = "0xAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"


def _mock_euler_evc(collaterals=None, controllers=None):
    """Return a mock Euler EVC."""
    mock = MagicMock()
    mock.getCollaterals.return_value = collaterals or []
    mock.getControllers.return_value = controllers or []
    return mock


def _mock_credit_vault_for_discovery(address):
    """Return a mock credit vault with asset/balance methods."""
    mock = MagicMock()
    addr_lower = address.lower()

    if addr_lower == FAKE_EVAULT.lower():
        mock.balanceOf.return_value = 1_500_000_000_000_000_000  # 1.5e18
        mock.asset.return_value = FAKE_UNDERLYING
        mock.convertToAssets.return_value = 1_500_000_000_000_000_000
        mock.debtOf.return_value = 0
    elif addr_lower == FAKE_DEBT_EVAULT.lower():
        mock.debtOf.return_value = 2_000_000_000  # 2000 (6 decimals)
        mock.asset.return_value = FAKE_DEBT_UNDERLYING
        mock.LTVLiquidation.return_value = 8600  # 86% in bps
    elif addr_lower == FAKE_IV.lower():
        mock.asset.return_value = FAKE_EVAULT
    else:
        mock.asset.return_value = "0x0000000000000000000000000000000000000000"
    return mock


def _mock_erc20_for_discovery(address):
    """Return a mock ERC20 with symbol and decimals."""
    mock = MagicMock()
    if address == FAKE_UNDERLYING:
        mock.symbol.return_value = "wstETH"
        mock.decimals.return_value = 18
    elif address == FAKE_DEBT_UNDERLYING:
        mock.symbol.return_value = "USDC"
        mock.decimals.return_value = 6
    else:
        mock.symbol.return_value = "TOKEN"
        mock.decimals.return_value = 18
    return mock


class TestEulerDiscovery:
    """Tests for discover_euler_positions."""

    @patch("twyne_cli.discover.vault_manager")
    @patch("twyne_cli.discover.erc20", side_effect=_mock_erc20_for_discovery)
    @patch("twyne_cli.discover.credit_vault", side_effect=_mock_credit_vault_for_discovery)
    @patch("twyne_cli.discover.intermediate_vaults", return_value={"euler_ewstETH": FAKE_IV})
    @patch("twyne_cli.discover.euler_evc")
    def test_discover_euler_positions(self, mock_evc, mock_ivs, mock_cv, mock_erc20_fn, mock_vm):
        """Discovers a single Euler position with collateral + debt."""
        from twyne_cli.discover import _euler_sub_account, discover_euler_positions

        mock_vm.return_value = MagicMock(**{"maxTwyneLTVs.return_value": 9800})

        # Only sub-account 0 has collaterals/controllers
        sub0 = _euler_sub_account(FAKE_USER, 0)
        evc_mock = MagicMock()

        def get_collaterals(addr):
            if addr.lower() == sub0.lower():
                return [FAKE_EVAULT]
            return []

        def get_controllers(addr):
            if addr.lower() == sub0.lower():
                return [FAKE_DEBT_EVAULT]
            return []

        evc_mock.getCollaterals.side_effect = get_collaterals
        evc_mock.getControllers.side_effect = get_controllers
        mock_evc.return_value = evc_mock

        positions = discover_euler_positions(FAKE_USER)
        assert len(positions) == 1
        pos = positions[0]
        assert pos.protocol == "euler"
        assert pos.collateral_symbol == "wstETH"
        assert pos.debt_symbol == "USDC"
        assert pos.collateral_amount == 1_500_000_000_000_000_000
        assert pos.debt_amount == 2_000_000_000
        assert pos.sub_account_id == 0
        assert pos.intermediate_vault == FAKE_IV
        assert pos.max_twyne_ltv_bps == 9800

    @patch("twyne_cli.discover.euler_evc")
    def test_discover_euler_no_positions(self, mock_evc):
        """Returns empty list when no collaterals/controllers."""
        mock_evc.return_value = _mock_euler_evc()

        from twyne_cli.discover import discover_euler_positions
        positions = discover_euler_positions(FAKE_USER)
        assert positions == []

    @patch("twyne_cli.discover.vault_manager")
    @patch("twyne_cli.discover.erc20", side_effect=_mock_erc20_for_discovery)
    @patch("twyne_cli.discover.credit_vault", side_effect=_mock_credit_vault_for_discovery)
    @patch("twyne_cli.discover.intermediate_vaults", return_value={"euler_ewstETH": FAKE_IV})
    @patch("twyne_cli.discover.euler_evc")
    def test_discover_euler_sub_accounts(self, mock_evc, mock_ivs, mock_cv, mock_erc20_fn, mock_vm):
        """Scans sub-accounts 0-3 and finds position on sub-account 0."""
        from twyne_cli.discover import discover_euler_positions

        mock_vm.return_value = MagicMock(**{"maxTwyneLTVs.return_value": 9800})
        evc_mock = MagicMock()

        def get_collaterals(addr):
            # Only sub-account 0 has collaterals
            sub0 = f"0x{int(FAKE_USER, 16):040x}"
            if addr.lower() == sub0.lower():
                return [FAKE_EVAULT]
            return []

        def get_controllers(addr):
            sub0 = f"0x{int(FAKE_USER, 16):040x}"
            if addr.lower() == sub0.lower():
                return [FAKE_DEBT_EVAULT]
            return []

        evc_mock.getCollaterals.side_effect = get_collaterals
        evc_mock.getControllers.side_effect = get_controllers
        mock_evc.return_value = evc_mock

        positions = discover_euler_positions(FAKE_USER)
        assert len(positions) == 1
        assert positions[0].sub_account_id == 0

    @patch("twyne_cli.discover.credit_vault")
    @patch("twyne_cli.discover.intermediate_vaults", return_value={"euler_ewstETH": FAKE_IV})
    @patch("twyne_cli.discover.euler_evc")
    def test_discover_euler_multi_collateral_skipped(self, mock_evc, mock_ivs, mock_cv):
        """Multi-collateral positions are skipped (not migratable)."""
        from twyne_cli.discover import discover_euler_positions

        mock_evc.return_value = _mock_euler_evc(
            collaterals=[FAKE_EVAULT, "0x5555555555555555555555555555555555555555"],
            controllers=[FAKE_DEBT_EVAULT],
        )

        positions = discover_euler_positions(FAKE_USER)
        assert positions == []

    @patch("twyne_cli.discover.credit_vault", side_effect=_mock_credit_vault_for_discovery)
    @patch("twyne_cli.discover.intermediate_vaults", return_value={})  # No matching IVs
    @patch("twyne_cli.discover.euler_evc")
    def test_discover_euler_unsupported_evault(self, mock_evc, mock_ivs, mock_cv):
        """Positions with unsupported eVault (no Twyne IV) are filtered out."""
        from twyne_cli.discover import discover_euler_positions

        mock_evc.return_value = _mock_euler_evc(
            collaterals=[FAKE_EVAULT],
            controllers=[FAKE_DEBT_EVAULT],
        )

        positions = discover_euler_positions(FAKE_USER)
        assert positions == []


# --------------------------------------------------------------------------- #
# Aave discovery tests
# --------------------------------------------------------------------------- #

AAVE_AWSTETH = "0x0B925eD163218f6662a35e0f0371Ac234f9E9371"
AAVE_WSTETH = "0x7f39C581F595B53c5cb19bD0b3f8dA6c935E2Ca0"
AAVE_WETH = "0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2"
AAVE_VD_WETH = "0x6666666666666666666666666666666666666666"
AAVE_IV = "0x75029a47f28550C93Ad5A3BbD2d9b5315204B561"


class TestAaveDiscovery:
    """Tests for discover_aave_positions."""

    @patch("twyne_cli.discover.vault_manager")
    @patch("twyne_cli.discover.get_address", return_value=AAVE_IV)
    @patch("twyne_cli.discover.erc20")
    @patch("twyne_cli.discover.aave_v3_pool")
    def test_discover_aave_positions(self, mock_pool, mock_erc20_fn, mock_get_addr, mock_vm):
        """Discovers Aave wstETH/WETH position with eMode-aware liqLTV."""
        from twyne_cli.discover import discover_aave_positions

        mock_vm.return_value = MagicMock(**{"maxTwyneLTVs.return_value": 9800})

        # Pool returns non-zero debt
        pool_mock = MagicMock()
        pool_mock.getUserAccountData.return_value = (
            10_000_000_000,  # totalCollateralBase
            5_000_000_000,   # totalDebtBase
            3_000_000_000,   # availableBorrowsBase
            8000,            # currentLiquidationThreshold
            7500,            # ltv
            1_200_000_000_000_000_000,  # healthFactor
        )
        # eMode category 1 with 95% liquidation threshold
        pool_mock.getUserEMode.return_value = 1
        pool_mock.getEModeCategoryData.return_value = (
            9300,  # ltv
            9500,  # liquidationThreshold
            10200,  # liquidationBonus
            "0x0000000000000000000000000000000000000000",  # priceSource
            "ETH correlated",  # label
        )
        # Reserve data — variableDebtTokenAddress at index 10
        reserve_data = [None] * 16
        reserve_data[10] = AAVE_VD_WETH
        pool_mock.getReserveData.return_value = reserve_data
        mock_pool.return_value = pool_mock

        def erc20_side_effect(address):
            mock = MagicMock()
            if address == AAVE_AWSTETH:
                mock.balanceOf.return_value = 3_200_000_000_000_000_000  # 3.2 aTokens
            elif address == AAVE_VD_WETH:
                mock.balanceOf.return_value = 2_100_000_000_000_000_000  # 2.1 debt
            elif address == AAVE_WSTETH:
                mock.symbol.return_value = "wstETH"
                mock.decimals.return_value = 18
            elif address == AAVE_WETH:
                mock.symbol.return_value = "WETH"
                mock.decimals.return_value = 18
            return mock
        mock_erc20_fn.side_effect = erc20_side_effect

        positions = discover_aave_positions(FAKE_USER)
        assert len(positions) == 1
        pos = positions[0]
        assert pos.protocol == "aave"
        assert pos.collateral_symbol == "awstETH"
        assert pos.debt_symbol == "WETH"
        assert pos.collateral_amount == 3_200_000_000_000_000_000
        assert pos.debt_amount == 2_100_000_000_000_000_000
        assert pos.sub_account_id == 0
        assert pos.intermediate_vault == AAVE_IV
        assert pos.ltv_bps == 5000  # 5B debt / 10B collateral in USD base
        assert pos.liq_ltv_bps == 9500  # eMode liquidation threshold
        assert pos.max_twyne_ltv_bps == 9800

    @patch("twyne_cli.discover.aave_v3_pool")
    def test_discover_aave_no_debt(self, mock_pool):
        """Returns empty when user has no Aave debt."""
        from twyne_cli.discover import discover_aave_positions

        pool_mock = MagicMock()
        pool_mock.getUserAccountData.return_value = (
            10_000_000_000, 0, 5_000_000_000, 8000, 7500, 2**256 - 1,
        )
        mock_pool.return_value = pool_mock

        positions = discover_aave_positions(FAKE_USER)
        assert positions == []


# --------------------------------------------------------------------------- #
# Combined discovery
# --------------------------------------------------------------------------- #


class TestCombinedDiscovery:
    """Tests for discover_all_positions."""

    @patch("twyne_cli.discover.discover_aave_positions", return_value=[])
    @patch("twyne_cli.discover.discover_euler_positions")
    def test_discover_combined(self, mock_euler, mock_aave):
        """Combined discovery aggregates both protocols."""
        from twyne_cli.discover import DiscoveredPosition, discover_all_positions

        euler_pos = DiscoveredPosition(
            protocol="euler", collateral_address=FAKE_EVAULT,
            collateral_symbol="wstETH", collateral_amount=10**18,
            collateral_decimals=18, debt_address=FAKE_DEBT_EVAULT,
            debt_symbol="USDC", debt_amount=2000 * 10**6,
            debt_decimals=6, sub_account_id=0,
            intermediate_vault=FAKE_IV, ltv_bps=7500,
            liq_ltv_bps=8600, max_twyne_ltv_bps=9800,
        )
        mock_euler.return_value = [euler_pos]

        positions = discover_all_positions(FAKE_USER)
        assert len(positions) == 1
        assert positions[0].protocol == "euler"
