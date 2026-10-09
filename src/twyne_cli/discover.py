"""Position discovery for Euler V2, Aave V3 and Morpho Blue — finds migratable positions."""

from dataclasses import dataclass

from .constants import MAXFACTOR
from .contracts import (
    aave_v3_pool,
    credit_vault,
    erc20,
    euler_evc,
    get_address,
    intermediate_vaults,
    vault_manager,
)
from .risk import pair_risk

# --------------------------------------------------------------------------- #
# Constants
# --------------------------------------------------------------------------- #

# Vault types for CollateralVaultFactory
EULER_V2 = 0
AAVE_V3 = 1
MORPHO_BLUE = 2

# Scan sub-accounts 0-3 (covers 99% of Euler users)
MAX_SUB_ACCOUNTS = 4

# Aave supported migration pairs (mainnet)
AAVE_MIGRATION_PAIRS = [
    {
        "collateral": "0x7f39C581F595B53c5cb19bD0b3f8dA6c935E2Ca0",  # wstETH
        "debt": "0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2",  # WETH
        "atoken": "0x0B925eD163218f6662a35e0f0371Ac234f9E9371",  # awstETH
    },
]


# --------------------------------------------------------------------------- #
# Data classes
# --------------------------------------------------------------------------- #


@dataclass
class DiscoveredPosition:
    """A migratable position from an external lending protocol."""

    protocol: str  # "euler", "aave" or "morpho"
    collateral_address: str  # eVault or aToken address
    collateral_symbol: str
    collateral_amount: int  # raw amount
    collateral_decimals: int
    debt_address: str  # debt token address
    debt_symbol: str
    debt_amount: int  # raw amount
    debt_decimals: int
    sub_account_id: int  # Euler sub-account (0 for Aave)
    intermediate_vault: str  # Twyne IV that supports this collateral
    ltv_bps: int  # current operating LTV in basis points
    liq_ltv_bps: int  # external liquidation LTV in basis points (eMode-aware for Aave)
    max_twyne_ltv_bps: int  # max Twyne liquidation LTV for this market
    morpho_market_id: str = ""  # Morpho Blue market id (Morpho positions only)


# --------------------------------------------------------------------------- #
# Euler discovery
# --------------------------------------------------------------------------- #


def _euler_sub_account(user: str, sub_id: int) -> str:
    """Compute Euler sub-account address: address ^ subAccountId."""
    user_int = int(user, 16)
    return f"0x{(user_int ^ sub_id):040x}"


def _find_euler_iv_for_evault(evault_addr: str) -> str | None:
    """Check if an eVault address is the asset of a known Twyne intermediate vault."""
    ivs = intermediate_vaults()
    for name, iv_addr in ivs.items():
        if not name.startswith("euler_"):
            continue
        try:
            iv = credit_vault(iv_addr)
            iv_asset = str(iv.asset()).lower()
            if iv_asset == evault_addr.lower():
                return iv_addr
        except Exception:
            continue
    return None


def discover_euler_positions(user_address: str) -> list[DiscoveredPosition]:
    """Discover migratable Euler V2 positions for a wallet address."""
    evc_instance = euler_evc()
    positions = []

    for sub_id in range(MAX_SUB_ACCOUNTS):
        sub_addr = _euler_sub_account(user_address, sub_id)

        try:
            collaterals = evc_instance.getCollaterals(sub_addr)
            controllers = evc_instance.getControllers(sub_addr)
        except Exception:
            continue

        if not collaterals or not controllers:
            continue

        # Only single-controller positions are migratable
        if len(controllers) != 1:
            continue

        # Only single-collateral positions are migratable
        if len(collaterals) != 1:
            continue

        collateral_evault_addr = str(collaterals[0])
        debt_evault_addr = str(controllers[0])

        # Check collateral balance
        try:
            collateral_evault = credit_vault(collateral_evault_addr)
            collateral_shares = collateral_evault.balanceOf(sub_addr)
            if collateral_shares <= 0:
                continue
        except Exception:
            continue

        # Check debt balance
        try:
            debt_evault = credit_vault(debt_evault_addr)
            debt_amount = debt_evault.debtOf(sub_addr)
            if debt_amount <= 0:
                continue
        except Exception:
            continue

        # Check if collateral eVault is supported by a Twyne IV
        iv_addr = _find_euler_iv_for_evault(collateral_evault_addr)
        if not iv_addr:
            continue

        # Get token details
        try:
            underlying_addr = str(collateral_evault.asset())
            underlying = erc20(underlying_addr)
            collateral_symbol = underlying.symbol()
            collateral_decimals = underlying.decimals()

            # Convert shares to assets for display
            collateral_assets = collateral_evault.convertToAssets(collateral_shares)

            debt_underlying_addr = str(debt_evault.asset())
            debt_token = erc20(debt_underlying_addr)
            debt_symbol = debt_token.symbol()
            debt_decimals = debt_token.decimals()
        except Exception:
            continue

        # Calculate current operating LTV in basis points
        if collateral_assets > 0 and debt_amount > 0:
            collateral_value = collateral_assets / 10**collateral_decimals
            debt_value = debt_amount / 10**debt_decimals
            if collateral_value > 0:
                ltv_bps = int((debt_value / collateral_value) * MAXFACTOR)
            else:
                ltv_bps = 0
        else:
            ltv_bps = 0

        # Get external liquidation LTV from debt eVault
        try:
            liq_ltv_bps = debt_evault.LTVLiquidation(collateral_evault_addr)
        except Exception:
            liq_ltv_bps = 0

        # Get max Twyne liq LTV from VaultManager
        # Governance parameters are keyed by IV and debt asset.
        try:
            vm = vault_manager()
            max_twyne_ltv_bps = pair_risk(iv_addr, debt_underlying_addr, vm=vm)["max_twyne_ltv_bps"]
        except Exception:
            max_twyne_ltv_bps = 0

        positions.append(DiscoveredPosition(
            protocol="euler",
            collateral_address=collateral_evault_addr,
            collateral_symbol=collateral_symbol,
            collateral_amount=collateral_shares,  # shares for teleport
            collateral_decimals=collateral_decimals,
            debt_address=debt_evault_addr,
            debt_symbol=debt_symbol,
            debt_amount=debt_amount,
            debt_decimals=debt_decimals,
            sub_account_id=sub_id,
            intermediate_vault=iv_addr,
            ltv_bps=ltv_bps,
            liq_ltv_bps=liq_ltv_bps,
            max_twyne_ltv_bps=max_twyne_ltv_bps,
        ))

    return positions


# --------------------------------------------------------------------------- #
# Aave discovery
# --------------------------------------------------------------------------- #


def discover_aave_positions(user_address: str) -> list[DiscoveredPosition]:
    """Discover migratable Aave V3 positions for a wallet address."""
    pool = aave_v3_pool()
    positions = []

    # Quick check: does user have any Aave debt?
    try:
        account_data = pool.getUserAccountData(user_address)
        total_debt_base = account_data[1]  # totalDebtBase
        if total_debt_base == 0:
            return []
    except Exception:
        return []

    # Get user's eMode category — liquidationThreshold from eMode if active
    try:
        emode_category = pool.getUserEMode(user_address)
    except Exception:
        emode_category = 0

    # Fetch eMode-aware liquidation threshold
    liq_ltv_bps = 0
    if emode_category > 0:
        try:
            emode_data = pool.getEModeCategoryData(emode_category)
            # emode_data = (ltv, liquidationThreshold, liquidationBonus, priceSource, label)
            liq_ltv_bps = emode_data[1]  # liquidationThreshold in bps
        except Exception:
            pass

    if liq_ltv_bps == 0:
        # Fallback: use account-level currentLiquidationThreshold (index 3)
        liq_ltv_bps = account_data[3]

    # Check each supported migration pair
    for pair in AAVE_MIGRATION_PAIRS:
        atoken_addr = pair["atoken"]
        collateral_addr = pair["collateral"]  # underlying (e.g. wstETH)
        debt_addr = pair["debt"]  # underlying debt (e.g. WETH)

        try:
            # Check aToken balance (collateral)
            atoken = erc20(atoken_addr)
            atoken_balance = atoken.balanceOf(user_address)
            if atoken_balance <= 0:
                continue

            # Get debt token address from pool reserve data
            reserve_data = pool.getReserveData(debt_addr)
            # variableDebtTokenAddress is at index 10 in the struct
            vd_token_addr = str(reserve_data[10])
            vd_token = erc20(vd_token_addr)
            debt_balance = vd_token.balanceOf(user_address)
            if debt_balance <= 0:
                continue

            # Get token details
            collateral_token = erc20(collateral_addr)
            collateral_symbol = collateral_token.symbol()
            collateral_decimals = collateral_token.decimals()

            debt_token = erc20(debt_addr)
            debt_symbol = debt_token.symbol()
            debt_decimals = debt_token.decimals()

            # Find matching Twyne IV
            iv_addr = get_address("intermediateVaults.aave_awstETH")
            if not iv_addr:
                continue

            # Get max Twyne liq LTV from VaultManager
            # Governance parameters are keyed by IV and debt asset.
            try:
                vm = vault_manager()
                max_twyne_ltv_bps = pair_risk(iv_addr, debt_addr, vm=vm)["max_twyne_ltv_bps"]
            except Exception:
                max_twyne_ltv_bps = 0

            # Calculate current operating LTV using Aave's USD-denominated values
            # account_data: (totalCollateralBase, totalDebtBase, ...) in 1e8 USD
            total_collateral_base = account_data[0]
            ltv_bps = int((total_debt_base / total_collateral_base) * MAXFACTOR) if total_collateral_base > 0 else 0

            positions.append(DiscoveredPosition(
                protocol="aave",
                collateral_address=atoken_addr,
                collateral_symbol=f"a{collateral_symbol}",
                collateral_amount=atoken_balance,
                collateral_decimals=collateral_decimals,
                debt_address=debt_addr,
                debt_symbol=debt_symbol,
                debt_amount=debt_balance,
                debt_decimals=debt_decimals,
                sub_account_id=0,
                intermediate_vault=iv_addr,
                ltv_bps=ltv_bps,
                liq_ltv_bps=liq_ltv_bps,
                max_twyne_ltv_bps=max_twyne_ltv_bps,
            ))
        except Exception:
            continue

    return positions


# --------------------------------------------------------------------------- #
# Morpho Blue discovery
# --------------------------------------------------------------------------- #


def discover_morpho_positions(user_address: str) -> list[DiscoveredPosition]:
    """Direct Morpho Blue positions of ``user_address`` in markets that a Twyne IV allows.

    Debt is borrowShares converted with Morpho's round-up share math at the last
    accrual; the teleport itself moves the exact live amount.
    """
    from .contracts import morpho, morpho_oracle
    from .morpho import allowed_markets, registered_markets

    blue = morpho()
    positions = []
    ivs = {m.intermediate_vault for m in registered_markets()}
    for iv_addr in ivs:
        for market in allowed_markets(iv_addr):
            try:
                _supply_shares, borrow_shares, collateral = blue.position(market.id, user_address)
                collateral, borrow_shares = int(collateral), int(borrow_shares)
                if collateral == 0:
                    continue
                m = blue.market(market.id)
                total_borrow_assets, total_borrow_shares = int(m[2]), int(m[3])
                # SharesMathLib.toAssetsUp: shares * (totalAssets + 1) / (totalShares + 1e6), rounded up
                num = borrow_shares * (total_borrow_assets + 1)
                den = total_borrow_shares + 10**6
                debt = -(-num // den)
                price = int(morpho_oracle(market.oracle).price())
                collateral_value = collateral * price // 10**36
                ltv_bps = debt * MAXFACTOR // collateral_value if collateral_value else 0
                coll_token, debt_token = erc20(market.collateral_token), erc20(market.loan_token)
                positions.append(DiscoveredPosition(
                    protocol="morpho",
                    collateral_address=market.collateral_token,
                    collateral_symbol=coll_token.symbol(),
                    collateral_amount=collateral,
                    collateral_decimals=int(coll_token.decimals()),
                    debt_address=market.loan_token,
                    debt_symbol=debt_token.symbol(),
                    debt_amount=debt,
                    debt_decimals=int(debt_token.decimals()),
                    sub_account_id=0,
                    intermediate_vault=iv_addr,
                    ltv_bps=ltv_bps,
                    liq_ltv_bps=market.lltv_bps,
                    max_twyne_ltv_bps=pair_risk(iv_addr, market.loan_token)["max_twyne_ltv_bps"],
                    morpho_market_id=market.id,
                ))
            except Exception as exc:  # noqa: BLE001
                import click

                click.echo(f"Warning: could not read Morpho market {market.id}: {type(exc).__name__}", err=True)
                continue
    return positions


# --------------------------------------------------------------------------- #
# Combined discovery
# --------------------------------------------------------------------------- #


def discover_all_positions(user_address: str) -> list[DiscoveredPosition]:
    """Discover all migratable positions across the families the active chain supports."""
    from .chains import active_chain

    chain = active_chain()
    positions = []
    if chain.supports_euler:
        positions.extend(discover_euler_positions(user_address))
    if chain.supports_aave:
        positions.extend(discover_aave_positions(user_address))
    if chain.supports_morpho:
        positions.extend(discover_morpho_positions(user_address))
    return positions
