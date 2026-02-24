"""Integration tests for credit vault transaction commands (tx credit *).

Tests cover:
- Credit deposit via Euler wrapper
- Credit deposit via direct eWETH→IV ERC4626 deposit
- Credit withdraw (ERC4626 3-arg) on intermediate vault
- Credit redeem (ERC4626 3-arg) on intermediate vault
- Credit deposit-atokens via Aave aToken wrapper
- CLI ABI bugs: wrong ABI for IV withdraw/redeem

The IV supply cap at block 24520000 is ~7 eWETH. The conftest fixture
_increase_iv_supply_cap() raises it to 100 eWETH to prevent
E_SupplyCapExceeded (0x426073f2) errors during test deposit accumulation.
"""

import pytest
from ape import Contract

from twyne_cli.contracts import aave_atoken_wrapper, collateral_vault, credit_vault, euler_wrapper
from twyne_cli.transactions import simulate_tx

from .conftest import (
    AAVE_ATOKEN_WRAPPER,
    ERC20_ABI,
    EULER_EWETH_IV,
    EVAULT_ABI,
    WETH,
    _deposit_eweth_to_iv,
)

# --------------------------------------------------------------------------- #
# credit deposit (euler wrapper)
# --------------------------------------------------------------------------- #


class TestCreditDepositEuler:
    """Test depositUnderlyingToIntermediateVault via Euler wrapper."""

    def test_simulate_deposit_succeeds(self, test_account, funded_weth):
        """Simulation of euler wrapper deposit passes after WETH approval."""
        weth = Contract(WETH, abi=ERC20_ABI)
        wrapper = euler_wrapper()
        amount = 1 * 10**18  # 1 WETH

        # Approve wrapper to spend WETH
        weth.approve(wrapper.address, amount, sender=test_account)

        result = simulate_tx(
            wrapper,
            "depositUnderlyingToIntermediateVault",
            [EULER_EWETH_IV, amount],
            sender=test_account,
        )
        assert result["success"], f"Simulation failed: {result.get('error')}"
        # The wrapper returns shares received (uint256 > 0)
        assert result["result"] > 0

    def test_simulate_deposit_succeeds_without_approval(self, test_account, funded_weth):
        """simulate_tx (eth_call) returns success even without WETH approval.

        KNOWN LIMITATION: eth_call doesn't enforce token approvals for
        EVC-routed operations. This is consistent with the broader simulate_tx
        limitation where eth_call bypasses approval checks.
        """
        wrapper = euler_wrapper()
        amount = 1 * 10**18

        result = simulate_tx(
            wrapper,
            "depositUnderlyingToIntermediateVault",
            [EULER_EWETH_IV, amount],
            sender=test_account,
        )
        # eth_call bypasses approval checks — simulation misleadingly succeeds
        assert result["success"] is True

    def test_execute_deposit_via_wrapper(self, test_account, funded_weth):
        """Actually deposit WETH through the Euler wrapper into the IV.

        Previously xfailed with 0x426073f2 — root cause was E_SupplyCapExceeded.
        The IV supply cap (~7 eWETH) was exceeded at block 24520000. Resolved by
        increasing the cap in conftest._increase_iv_supply_cap().
        """
        weth = Contract(WETH, abi=ERC20_ABI)
        wrapper = euler_wrapper()
        iv = Contract(EULER_EWETH_IV, abi=EVAULT_ABI)
        amount = 5 * 10**18  # 5 WETH
        addr = str(test_account.address)

        iv_balance_before = iv.balanceOf(addr)
        weth_balance_before = weth.balanceOf(addr)

        # Approve and deposit
        weth.approve(wrapper.address, amount, sender=test_account)
        receipt = wrapper.depositUnderlyingToIntermediateVault(
            EULER_EWETH_IV, amount, sender=test_account
        )

        assert receipt.status == 1

        # IV shares received
        iv_balance_after = iv.balanceOf(addr)
        assert iv_balance_after > iv_balance_before

        # WETH spent
        weth_balance_after = weth.balanceOf(addr)
        assert weth_balance_after == weth_balance_before - amount

    def test_deposit_zero_reverts(self, test_account, funded_weth):
        """Depositing zero amount should fail simulation."""
        wrapper = euler_wrapper()
        weth = Contract(WETH, abi=ERC20_ABI)
        weth.approve(wrapper.address, 10**18, sender=test_account)

        result = simulate_tx(
            wrapper,
            "depositUnderlyingToIntermediateVault",
            [EULER_EWETH_IV, 0],
            sender=test_account,
        )
        # Zero deposit should either revert or return 0 shares
        if result["success"]:
            assert result["result"] == 0
        else:
            assert result["error"]


# --------------------------------------------------------------------------- #
# credit deposit (direct eWETH → IV, bypasses wrapper)
# --------------------------------------------------------------------------- #


class TestCreditDepositDirect:
    """Test depositing eWETH directly into IV via ERC4626 deposit.

    Bypasses the Euler wrapper which reverts with 0x426073f2. The IV
    (CreditEVault) accepts direct ERC4626 deposits of its asset (eWETH).
    """

    def test_direct_deposit_returns_shares(self, test_account, funded_iv):
        """Direct eWETH→IV deposit returns positive shares."""
        assert funded_iv > 0

    def test_iv_balance_increases(self, test_account, funded_iv):
        """After deposit, test_account holds IV shares."""
        iv = Contract(EULER_EWETH_IV, abi=EVAULT_ABI)
        balance = iv.balanceOf(str(test_account.address))
        assert balance > 0


# --------------------------------------------------------------------------- #
# credit withdraw (ERC4626 — uses correct EVAULT_ABI, not CLI's CollateralVault ABI)
# --------------------------------------------------------------------------- #


class TestCreditWithdraw:
    """Test ERC4626 withdraw on intermediate vault.

    Uses Contract(IV, abi=EVAULT_ABI) which has the correct 3-arg
    withdraw(uint256, address, address). The CLI's collateral_vault() loads
    CollateralVault ABI which only has 2-arg withdraw.
    """

    def test_simulate_withdraw_succeeds(self, test_account, funded_iv):
        """Simulation of ERC4626 withdraw on IV passes after direct deposit."""
        iv = Contract(EULER_EWETH_IV, abi=EVAULT_ABI)
        addr = str(test_account.address)
        withdraw_amount = 1 * 10**18

        result = simulate_tx(
            iv, "withdraw", [withdraw_amount, addr, addr], sender=test_account
        )
        assert result["success"], f"Simulation failed: {result.get('error')}"

    def test_simulate_withdraw_fails_without_shares(self, test_account_2):
        """Withdraw simulation fails when account has no IV shares."""
        iv = Contract(EULER_EWETH_IV, abi=EVAULT_ABI)
        addr = str(test_account_2.address)

        result = simulate_tx(
            iv, "withdraw", [1 * 10**18, addr, addr], sender=test_account_2
        )
        assert not result["success"]

    def test_execute_withdraw(self, test_account, funded_iv):
        """Actually withdraw assets from the IV after direct deposit."""
        iv = Contract(EULER_EWETH_IV, abi=EVAULT_ABI)
        addr = str(test_account.address)
        shares_before = iv.balanceOf(addr)
        assert shares_before > 0

        withdraw_amount = 1 * 10**18
        receipt = iv.withdraw(withdraw_amount, addr, addr, sender=test_account)

        assert receipt.status == 1
        shares_after = iv.balanceOf(addr)
        assert shares_after < shares_before

    def test_withdraw_to_different_receiver(self, test_account, test_account_2, funded_iv):
        """Withdraw assets to a different receiver address."""
        iv = Contract(EULER_EWETH_IV, abi=EVAULT_ABI)
        addr = str(test_account.address)
        recv = str(test_account_2.address)

        result = simulate_tx(
            iv, "withdraw", [1 * 10**18, recv, addr], sender=test_account
        )
        assert result["success"], f"Simulation failed: {result.get('error')}"

    def test_cli_withdraw_correct_abi(self, test_account, funded_iv):
        """CLI's credit_vault() ABI has 3-arg ERC4626 withdraw."""
        cv = credit_vault(EULER_EWETH_IV)
        addr = str(test_account.address)

        result = simulate_tx(
            cv, "withdraw", [1 * 10**18, addr, addr], sender=test_account
        )
        assert result["success"] is True


# --------------------------------------------------------------------------- #
# credit redeem (ERC4626 — uses correct EVAULT_ABI)
# --------------------------------------------------------------------------- #


class TestCreditRedeem:
    """Test ERC4626 redeem on intermediate vault.

    Uses Contract(IV, abi=EVAULT_ABI) which has the correct 3-arg
    redeem(uint256, address, address). The CLI's collateral_vault() ABI has
    NO redeem function at all.
    """

    def test_simulate_redeem_succeeds(self, test_account, funded_iv):
        """Simulation of ERC4626 redeem on IV passes after direct deposit."""
        iv = Contract(EULER_EWETH_IV, abi=EVAULT_ABI)
        addr = str(test_account.address)
        shares = iv.balanceOf(addr)
        assert shares > 0
        redeem_shares = shares // 2

        result = simulate_tx(
            iv, "redeem", [redeem_shares, addr, addr], sender=test_account
        )
        assert result["success"], f"Simulation failed: {result.get('error')}"
        assert result["result"] > 0

    def test_simulate_redeem_fails_without_shares(self, test_account_2):
        """Redeem simulation fails when account has no IV shares."""
        iv = Contract(EULER_EWETH_IV, abi=EVAULT_ABI)
        addr = str(test_account_2.address)

        result = simulate_tx(
            iv, "redeem", [1 * 10**18, addr, addr], sender=test_account_2
        )
        assert not result["success"]

    def test_execute_redeem(self, test_account, funded_iv):
        """Actually redeem shares from the IV."""
        iv = Contract(EULER_EWETH_IV, abi=EVAULT_ABI)
        addr = str(test_account.address)
        shares = iv.balanceOf(addr)
        assert shares > 0
        # Redeem half the shares (not all — accumulated shares from prior tests
        # may exceed IV cash, causing revert)
        redeem_amount = shares // 2

        receipt = iv.redeem(redeem_amount, addr, addr, sender=test_account)

        assert receipt.status == 1
        shares_after = iv.balanceOf(addr)
        assert shares_after < shares

    def test_redeem_to_different_receiver(self, test_account, test_account_2, funded_iv):
        """Redeem shares and send assets to a different receiver."""
        iv = Contract(EULER_EWETH_IV, abi=EVAULT_ABI)
        addr = str(test_account.address)
        recv = str(test_account_2.address)
        shares = iv.balanceOf(addr)
        assert shares > 0
        redeem_shares = shares // 4

        result = simulate_tx(
            iv, "redeem", [redeem_shares, recv, addr], sender=test_account
        )
        assert result["success"], f"Simulation failed: {result.get('error')}"

    def test_redeem_more_than_balance_fails(self, test_account, funded_iv):
        """Redeeming more shares than owned should fail."""
        iv = Contract(EULER_EWETH_IV, abi=EVAULT_ABI)
        addr = str(test_account.address)
        shares = iv.balanceOf(addr)
        assert shares > 0
        excessive_shares = shares * 2

        result = simulate_tx(
            iv, "redeem", [excessive_shares, addr, addr], sender=test_account
        )
        assert not result["success"]

    def test_cli_redeem_correct_abi(self, test_account, funded_iv):
        """CLI's credit_vault() ABI has ERC4626 redeem."""
        cv = credit_vault(EULER_EWETH_IV)
        addr = str(test_account.address)

        result = simulate_tx(
            cv, "redeem", [10**18, addr, addr], sender=test_account
        )
        assert result["success"] is True


# --------------------------------------------------------------------------- #
# credit deposit-atokens (aave atoken wrapper)
# --------------------------------------------------------------------------- #


class TestCreditDepositATokens:
    """Test depositATokens via Aave aToken wrapper (simulation only)."""

    def test_aave_atoken_wrapper_is_callable(self, test_account):
        """Verify the aToken wrapper contract can be instantiated."""
        wrapper = aave_atoken_wrapper()
        assert wrapper.address == AAVE_ATOKEN_WRAPPER

    def test_simulate_deposit_atokens_returns_result(self, test_account):
        """simulate_tx returns a dict even when the call fails (no aToken balance)."""
        wrapper = aave_atoken_wrapper()
        amount = 1 * 10**18

        # We don't have aTokens, so this will fail — but simulate_tx should
        # still return a structured result dict instead of raising.
        result = simulate_tx(
            wrapper,
            "depositATokens",
            [amount, str(test_account.address)],
            sender=test_account,
        )
        assert isinstance(result, dict)
        assert "success" in result
        # Expected to fail since test_account has no aTokens approved
        if not result["success"]:
            assert "error" in result

    def test_atoken_wrapper_has_atoken_view(self, test_account):
        """The aToken wrapper exposes an aToken() view that returns an address."""
        wrapper = aave_atoken_wrapper()
        atoken_addr = wrapper.aToken()
        # Should be a valid non-zero address
        assert str(atoken_addr) != "0x" + "00" * 20
