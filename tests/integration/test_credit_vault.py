"""Integration tests for credit vault transaction commands (tx credit *).

BUG DOCUMENTED: The CLI's tx.py credit-withdraw and credit-redeem commands use
collateral_vault(iv_address) which loads the CollateralVault ABI. That ABI has:
  - withdraw(uint256, address) — 2-arg, NOT the ERC4626 3-arg signature
  - NO redeem function at all
The IV (CreditEVault) is an ERC4626 vault needing:
  - withdraw(uint256, address, address) — 3-arg
  - redeem(uint256, address, address) — 3-arg

Tests below use the correct EVAULT_ABI directly to test the actual IV operations,
and mark CLI-path tests as xfail where the wrong ABI is used.
"""

import pytest
from ape import Contract

from twyne_cli.contracts import aave_atoken_wrapper, collateral_vault, euler_wrapper
from twyne_cli.transactions import simulate_tx

from .conftest import (
    AAVE_ATOKEN_WRAPPER,
    ERC20_ABI,
    EULER_EWETH_IV,
    EVAULT_ABI,
    WETH,
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

    @pytest.mark.xfail(
        reason="BUG: simulate_tx (eth_call) returns success for wrapper deposit even "
        "without WETH approval. This is consistent with the broader simulate_tx bug "
        "where eth_call doesn't enforce token approvals for EVC-routed operations.",
        strict=True,
    )
    def test_simulate_deposit_fails_without_approval(self, test_account, funded_weth):
        """Simulation fails when wrapper has no WETH allowance."""
        wrapper = euler_wrapper()
        amount = 1 * 10**18

        result = simulate_tx(
            wrapper,
            "depositUnderlyingToIntermediateVault",
            [EULER_EWETH_IV, amount],
            sender=test_account,
        )
        assert not result["success"]
        assert result["error"]  # should contain a revert reason

    @pytest.mark.xfail(
        reason="BUG: Euler wrapper depositUnderlyingToIntermediateVault simulation "
        "succeeds but execution reverts with unknown error 0x426073f2 (not in any "
        "bundled ABI). The wrapper contract may have additional requirements at "
        "block 24520000 that are not surfaced by simulate_tx.",
        strict=True,
    )
    def test_execute_deposit(self, test_account, funded_weth):
        """Actually deposit WETH through the Euler wrapper into the IV."""
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
# credit withdraw (ERC4626 — uses correct EVAULT_ABI, not CLI's CollateralVault ABI)
# --------------------------------------------------------------------------- #


class TestCreditWithdraw:
    """Test ERC4626 withdraw on intermediate vault.

    Uses Contract(IV, abi=EVAULT_ABI) which has the correct 3-arg
    withdraw(uint256, address, address). The CLI's collateral_vault() loads
    CollateralVault ABI which only has 2-arg withdraw.
    """

    def _deposit_to_iv(self, test_account):
        """Helper: deposit 5 WETH through the wrapper and return IV shares."""
        weth = Contract(WETH, abi=ERC20_ABI)
        wrapper = euler_wrapper()
        iv = Contract(EULER_EWETH_IV, abi=EVAULT_ABI)
        amount = 5 * 10**18
        addr = str(test_account.address)

        weth.approve(wrapper.address, amount, sender=test_account)
        wrapper.depositUnderlyingToIntermediateVault(
            EULER_EWETH_IV, amount, sender=test_account
        )
        return iv.balanceOf(addr)

    @pytest.mark.xfail(
        reason="BLOCKED: Depends on _deposit_to_iv() which calls wrapper "
        "depositUnderlyingToIntermediateVault — fails with 0x426073f2.",
        strict=True,
    )
    def test_simulate_withdraw_succeeds(self, test_account, funded_weth):
        """Simulation of ERC4626 withdraw on IV passes after deposit."""
        shares = self._deposit_to_iv(test_account)
        assert shares > 0

        iv = Contract(EULER_EWETH_IV, abi=EVAULT_ABI)
        addr = str(test_account.address)
        withdraw_amount = 1 * 10**18

        result = simulate_tx(
            iv, "withdraw", [withdraw_amount, addr, addr], sender=test_account
        )
        assert result["success"], f"Simulation failed: {result.get('error')}"

    def test_simulate_withdraw_fails_without_shares(self, test_account):
        """Withdraw simulation fails when account has no IV shares."""
        iv = Contract(EULER_EWETH_IV, abi=EVAULT_ABI)
        addr = str(test_account.address)

        result = simulate_tx(
            iv, "withdraw", [1 * 10**18, addr, addr], sender=test_account
        )
        assert not result["success"]

    @pytest.mark.xfail(
        reason="BLOCKED: Depends on _deposit_to_iv() which calls wrapper "
        "depositUnderlyingToIntermediateVault — fails with 0x426073f2.",
        strict=True,
    )
    def test_execute_withdraw(self, test_account, funded_weth):
        """Actually withdraw assets from the IV."""
        self._deposit_to_iv(test_account)

        iv = Contract(EULER_EWETH_IV, abi=EVAULT_ABI)
        addr = str(test_account.address)
        shares_before = iv.balanceOf(addr)
        assert shares_before > 0

        withdraw_amount = 1 * 10**18
        receipt = iv.withdraw(withdraw_amount, addr, addr, sender=test_account)

        assert receipt.status == 1
        shares_after = iv.balanceOf(addr)
        assert shares_after < shares_before

    @pytest.mark.xfail(
        reason="BLOCKED: Depends on _deposit_to_iv() which calls wrapper "
        "depositUnderlyingToIntermediateVault — fails with 0x426073f2.",
        strict=True,
    )
    def test_withdraw_to_different_receiver(self, test_account, test_account_2, funded_weth):
        """Withdraw assets to a different receiver address."""
        self._deposit_to_iv(test_account)

        iv = Contract(EULER_EWETH_IV, abi=EVAULT_ABI)
        addr = str(test_account.address)
        recv = str(test_account_2.address)

        result = simulate_tx(
            iv, "withdraw", [1 * 10**18, recv, addr], sender=test_account
        )
        assert result["success"], f"Simulation failed: {result.get('error')}"

    @pytest.mark.xfail(
        reason="BUG: CLI uses collateral_vault(iv) which loads CollateralVault ABI "
        "with 2-arg withdraw(uint256, address). IV needs ERC4626 3-arg "
        "withdraw(uint256, address, address).",
        strict=True,
    )
    def test_cli_withdraw_wrong_abi(self, test_account, funded_weth):
        """CLI's collateral_vault() ABI doesn't have 3-arg ERC4626 withdraw."""
        self._deposit_to_iv(test_account)
        cv = collateral_vault(EULER_EWETH_IV)
        addr = str(test_account.address)

        # This should fail because collateral_vault ABI has withdraw(uint256, address)
        # not withdraw(uint256, address, address)
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

    def _deposit_to_iv(self, test_account):
        """Helper: deposit 5 WETH through the wrapper and return IV shares."""
        weth = Contract(WETH, abi=ERC20_ABI)
        wrapper = euler_wrapper()
        iv = Contract(EULER_EWETH_IV, abi=EVAULT_ABI)
        amount = 5 * 10**18
        addr = str(test_account.address)

        weth.approve(wrapper.address, amount, sender=test_account)
        wrapper.depositUnderlyingToIntermediateVault(
            EULER_EWETH_IV, amount, sender=test_account
        )
        return iv.balanceOf(addr)

    @pytest.mark.xfail(
        reason="BLOCKED: Depends on _deposit_to_iv() which calls wrapper "
        "depositUnderlyingToIntermediateVault — fails with 0x426073f2.",
        strict=True,
    )
    def test_simulate_redeem_succeeds(self, test_account, funded_weth):
        """Simulation of ERC4626 redeem on IV passes after deposit."""
        shares = self._deposit_to_iv(test_account)
        assert shares > 0

        iv = Contract(EULER_EWETH_IV, abi=EVAULT_ABI)
        addr = str(test_account.address)
        redeem_shares = shares // 2

        result = simulate_tx(
            iv, "redeem", [redeem_shares, addr, addr], sender=test_account
        )
        assert result["success"], f"Simulation failed: {result.get('error')}"
        assert result["result"] > 0

    def test_simulate_redeem_fails_without_shares(self, test_account):
        """Redeem simulation fails when account has no IV shares."""
        iv = Contract(EULER_EWETH_IV, abi=EVAULT_ABI)
        addr = str(test_account.address)

        result = simulate_tx(
            iv, "redeem", [1 * 10**18, addr, addr], sender=test_account
        )
        assert not result["success"]

    @pytest.mark.xfail(
        reason="BLOCKED: Depends on _deposit_to_iv() which calls wrapper "
        "depositUnderlyingToIntermediateVault — fails with 0x426073f2.",
        strict=True,
    )
    def test_execute_redeem(self, test_account, funded_weth):
        """Actually redeem shares from the IV."""
        shares = self._deposit_to_iv(test_account)
        assert shares > 0

        iv = Contract(EULER_EWETH_IV, abi=EVAULT_ABI)
        addr = str(test_account.address)

        receipt = iv.redeem(shares, addr, addr, sender=test_account)

        assert receipt.status == 1
        shares_after = iv.balanceOf(addr)
        assert shares_after == 0

    @pytest.mark.xfail(
        reason="BLOCKED: Depends on _deposit_to_iv() which calls wrapper "
        "depositUnderlyingToIntermediateVault — fails with 0x426073f2.",
        strict=True,
    )
    def test_redeem_to_different_receiver(self, test_account, test_account_2, funded_weth):
        """Redeem shares and send assets to a different receiver."""
        shares = self._deposit_to_iv(test_account)
        assert shares > 0

        iv = Contract(EULER_EWETH_IV, abi=EVAULT_ABI)
        addr = str(test_account.address)
        recv = str(test_account_2.address)
        redeem_shares = shares // 4

        result = simulate_tx(
            iv, "redeem", [redeem_shares, recv, addr], sender=test_account
        )
        assert result["success"], f"Simulation failed: {result.get('error')}"

    @pytest.mark.xfail(
        reason="BLOCKED: Depends on _deposit_to_iv() which calls wrapper "
        "depositUnderlyingToIntermediateVault — fails with 0x426073f2.",
        strict=True,
    )
    def test_redeem_more_than_balance_fails(self, test_account, funded_weth):
        """Redeeming more shares than owned should fail."""
        shares = self._deposit_to_iv(test_account)
        assert shares > 0

        iv = Contract(EULER_EWETH_IV, abi=EVAULT_ABI)
        addr = str(test_account.address)
        excessive_shares = shares * 2

        result = simulate_tx(
            iv, "redeem", [excessive_shares, addr, addr], sender=test_account
        )
        assert not result["success"]

    @pytest.mark.xfail(
        reason="BUG: CLI uses collateral_vault(iv) which loads CollateralVault ABI "
        "that has NO redeem function. IV needs ERC4626 redeem(uint256, address, address).",
        strict=True,
    )
    def test_cli_redeem_wrong_abi(self, test_account, funded_weth):
        """CLI's collateral_vault() ABI doesn't have ERC4626 redeem."""
        self._deposit_to_iv(test_account)
        cv = collateral_vault(EULER_EWETH_IV)
        addr = str(test_account.address)

        # This should fail because collateral_vault ABI has no redeem function
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
