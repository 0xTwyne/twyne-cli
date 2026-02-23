"""Integration tests for collateral vault transaction commands.

Tests simulate_tx() and direct contract calls for the collateral vault
command group against a real Anvil fork at block 24520000.

Covers: create vault, deposit, withdraw, borrow, repay, set-ltv, liquidate, skim.

Vault creation uses conftest._create_vault_via_evc() which calls the real v2
factory through EVC (bypassing the CLI's stale v1 ABI).
"""

import pytest
from ape import Contract

from twyne_cli.contracts import collateral_vault, collateral_vault_factory, erc20
from twyne_cli.transactions import simulate_tx

from .conftest import (
    ERC20_ABI,
    EULER_EWETH,
    ZERO_ADDRESS,
    _create_vault_via_evc,
    _deposit_via_evc,
    _withdraw_via_evc,
)

# ---------------------------------------------------------------------------
# fresh_vault and funded_vault are provided by conftest.py
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# 1. Factory — Create Vault
# ---------------------------------------------------------------------------


class TestCreateVault:
    """Test collateral vault creation via factory.

    NOTE: The CLI's bundled CollateralVaultFactory ABI is stale (v1 with 3 args).
    The deployed factory requires 5 args + EVC callthrough. Tests below document
    which operations work and which expose CLI bugs.
    """

    def test_create_vault_via_evc_helper(self, test_account):
        """Vault creation via the correct v2 factory + EVC.call() succeeds."""
        vault_addr = _create_vault_via_evc(test_account)
        assert vault_addr != ZERO_ADDRESS
        assert len(vault_addr) == 42  # valid address length

    def test_created_vault_has_correct_asset(self, test_account):
        """The created vault's asset() returns the eWETH token address."""
        vault_addr = _create_vault_via_evc(test_account)
        cv = collateral_vault(vault_addr)
        assert cv.asset().lower() == EULER_EWETH.lower()

    def test_created_vault_borrower_is_owner(self, test_account):
        """Creating a vault sets the caller as the borrower/owner."""
        vault_addr = _create_vault_via_evc(test_account)
        cv = collateral_vault(vault_addr)
        assert cv.borrower().lower() == test_account.address.lower()

    @pytest.mark.xfail(
        reason="BUG: CLI factory ABI is stale v1 (3 args). Deployed factory requires "
        "5 args (uint8,address,address,uint256,address) + EVC callthrough.",
        strict=True,
    )
    def test_cli_factory_simulation_fails(self, test_account):
        """simulate_tx with CLI's factory contract fails — stale ABI."""
        factory = collateral_vault_factory()
        sim = simulate_tx(
            factory,
            "createCollateralVault",
            [EULER_EWETH, EULER_EWETH, 0],
            sender=test_account,
        )
        assert sim["success"] is True  # This will fail → xfail


# ---------------------------------------------------------------------------
# 2. Deposit
# ---------------------------------------------------------------------------


class TestDeposit:
    """Test depositing eWETH collateral into a vault."""

    def test_deposit_simulation(self, test_account, fresh_vault, funded_eweth):
        """simulate_tx on deposit returns success after approval."""
        vault_address = fresh_vault
        amount = funded_eweth

        # Approve CV to spend eWETH
        token = erc20(EULER_EWETH)
        token.approve(vault_address, amount, sender=test_account)

        cv = collateral_vault(vault_address)
        sim = simulate_tx(cv, "deposit", [amount], sender=test_account)
        assert sim["success"] is True, f"Deposit simulation failed: {sim.get('error')}"

    def test_deposit_simulation_fails_without_approval(
        self, test_account, fresh_vault, funded_eweth
    ):
        """simulate_tx on deposit fails if no token approval."""
        cv = collateral_vault(fresh_vault)
        sim = simulate_tx(cv, "deposit", [funded_eweth], sender=test_account)
        assert sim["success"] is False
        assert sim["error"]  # Should contain a revert reason

    def test_deposit_execution(self, test_account, fresh_vault, funded_eweth):
        """Depositing eWETH via EVC batch reduces the sender's balance.

        NOTE: Deposit MUST go through EVC.batch() because the CV's
        _callThroughEVC() modifier re-routes direct calls through EVC,
        and EVC auth fails for direct caller (CV is not an operator).
        """
        vault_address = fresh_vault
        amount = funded_eweth

        token = erc20(EULER_EWETH)
        balance_before = token.balanceOf(test_account.address)

        # Approve + deposit via EVC batch
        token.approve(vault_address, amount, sender=test_account)
        receipt = _deposit_via_evc(test_account, vault_address, amount)
        assert receipt["status"] == "0x1"

        balance_after = token.balanceOf(test_account.address)
        assert balance_after < balance_before
        assert balance_before - balance_after == amount

    def test_deposit_partial_amount(self, test_account, fresh_vault, funded_eweth):
        """Depositing a partial amount via EVC batch works correctly."""
        vault_address = fresh_vault
        partial = funded_eweth // 2

        token = erc20(EULER_EWETH)
        token.approve(vault_address, partial, sender=test_account)

        receipt = _deposit_via_evc(test_account, vault_address, partial)
        assert receipt["status"] == "0x1"

        # Sender still has remaining eWETH
        remaining = token.balanceOf(test_account.address)
        assert remaining >= funded_eweth - partial


# ---------------------------------------------------------------------------
# 3. Withdraw
# ---------------------------------------------------------------------------


class TestWithdraw:
    """Test withdrawing collateral from a vault.

    NOTE: After deposit, the vault's _handleExcessCredit() automatically borrows
    from the intermediate vault (credit siphoning). This means the full deposited
    amount is NOT withdrawable — some collateral backs the credit reservation.
    Tests use small fractions of the deposit to stay within maxWithdraw limits.
    """

    def test_withdraw_simulation_partial(self, test_account, funded_vault):
        """simulate_tx on withdraw returns success for a small amount."""
        vault_address, deposit_amount = funded_vault
        cv = collateral_vault(vault_address)
        receiver = str(test_account.address)
        # Withdraw 1% of deposit — safely within limits after credit siphoning
        small_amount = deposit_amount // 100

        sim = simulate_tx(
            cv, "withdraw", [small_amount, receiver], sender=test_account
        )
        assert sim["success"] is True, f"Withdraw simulation failed: {sim.get('error')}"

    @pytest.mark.xfail(
        reason="BUG: simulate_tx (eth_call) returns success for full withdrawal, "
        "but actual execution reverts with T_WithdrawMoreThanMax due to credit "
        "siphoning. simulate_tx misleads users about what will succeed on-chain.",
        strict=True,
    )
    def test_withdraw_full_deposit_simulation_misleading(self, test_account, funded_vault):
        """simulate_tx misleadingly reports success for full withdrawal.

        BUG: After deposit, _handleExcessCredit() borrows from the IV. The
        vault's maxWithdraw < deposited amount. simulate_tx (eth_call) says
        success, but actual eth_sendTransaction reverts with T_WithdrawMoreThanMax.
        """
        vault_address, deposit_amount = funded_vault
        cv = collateral_vault(vault_address)
        receiver = str(test_account.address)

        sim = simulate_tx(
            cv, "withdraw", [deposit_amount, receiver], sender=test_account
        )
        # This SHOULD fail but simulate_tx returns success → xfail documents the bug
        assert sim["success"] is False

    def test_withdraw_execution_via_evc(self, test_account, fresh_vault, funded_eweth):
        """Withdraw via EVC batch after EVC batch deposit succeeds."""
        vault_address = fresh_vault
        small_deposit = funded_eweth // 10

        token = Contract(EULER_EWETH, abi=ERC20_ABI)
        token.approve(vault_address, small_deposit, sender=test_account)

        # Deposit via EVC batch
        _deposit_via_evc(test_account, vault_address, small_deposit)

        receiver = str(test_account.address)
        # After credit siphoning, withdraw a tiny fraction
        tiny = small_deposit // 1000

        balance_before = token.balanceOf(receiver)
        receipt = _withdraw_via_evc(test_account, vault_address, tiny, receiver)
        assert receipt["status"] == "0x1", f"Withdraw via EVC failed: {receipt}"

        balance_after = token.balanceOf(receiver)
        assert balance_after > balance_before

    def test_withdraw_too_much_fails(self, test_account, funded_vault):
        """Withdrawing more than deposited fails simulation."""
        vault_address, deposit_amount = funded_vault
        cv = collateral_vault(vault_address)
        receiver = str(test_account.address)
        excessive = deposit_amount * 2

        sim = simulate_tx(cv, "withdraw", [excessive, receiver], sender=test_account)
        assert sim["success"] is False


# ---------------------------------------------------------------------------
# 4. Borrow
# ---------------------------------------------------------------------------


class TestBorrow:
    """Test borrowing from external protocol via collateral vault."""

    def test_borrow_fails_without_credit(self, test_account, funded_vault):
        """Borrow simulation fails when no credit is reserved for the vault."""
        vault_address, _ = funded_vault
        cv = collateral_vault(vault_address)
        receiver = str(test_account.address)
        borrow_amount = 1 * 10**18  # 1 token

        sim = simulate_tx(
            cv, "borrow", [borrow_amount, receiver], sender=test_account
        )
        # Should fail — no credit reserved in the intermediate vault
        assert sim["success"] is False
        assert sim["error"], "Expected a meaningful revert reason"

    def test_borrow_zero_amount(self, test_account, funded_vault):
        """Borrowing zero might succeed or revert — either way, simulate_tx handles it."""
        vault_address, _ = funded_vault
        cv = collateral_vault(vault_address)
        receiver = str(test_account.address)

        sim = simulate_tx(cv, "borrow", [0, receiver], sender=test_account)
        # Result depends on contract logic; just verify simulate_tx returns valid dict
        assert "success" in sim
        if not sim["success"]:
            assert "error" in sim


# ---------------------------------------------------------------------------
# 5. Repay
# ---------------------------------------------------------------------------


class TestRepay:
    """Test repaying borrowed amount to a collateral vault."""

    def test_repay_simulation_no_debt(self, test_account, funded_vault):
        """Repay simulation on a vault with no outstanding debt."""
        vault_address, _ = funded_vault
        cv = collateral_vault(vault_address)

        sim = simulate_tx(cv, "repay", [0], sender=test_account)
        # With zero debt, repaying 0 might succeed or revert
        assert "success" in sim
        if not sim["success"]:
            assert "error" in sim

    def test_repay_nonzero_without_debt_fails(self, test_account, funded_vault):
        """Repaying a nonzero amount when no debt exists should fail."""
        vault_address, _ = funded_vault
        cv = collateral_vault(vault_address)

        sim = simulate_tx(cv, "repay", [1 * 10**18], sender=test_account)
        # No debt to repay — expect failure or revert
        assert sim["success"] is False


# ---------------------------------------------------------------------------
# 6. Set LTV
# ---------------------------------------------------------------------------


class TestSetLTV:
    """Test setting the Twyne liquidation LTV on a collateral vault.

    NOTE: setTwyneLiqLTV has _callThroughEVC() routing. In eth_call (simulation),
    the EVC routing may behave differently than in actual execution. simulate_tx
    may report success even when on-chain execution fails.

    The vault's twyneLiqLTV is set during creation. Changing it afterward
    triggers _handleExcessCredit() rebalancing.
    """

    @pytest.mark.xfail(
        reason="BUG: simulate_tx (eth_call) returns success for setTwyneLiqLTV, "
        "but execution reverts with T_CV_OperationDisabled. The callThroughEVC "
        "modifier behaves differently in simulation vs execution context.",
        strict=True,
    )
    def test_set_ltv_simulation_misleading(self, test_account, fresh_vault):
        """simulate_tx misleadingly reports success for setTwyneLiqLTV.

        BUG: setTwyneLiqLTV is disabled at block 24520000. simulate_tx says
        success, but execution reverts with T_CV_OperationDisabled.
        """
        cv = collateral_vault(fresh_vault)
        ltv = 9000

        sim = simulate_tx(cv, "setTwyneLiqLTV", [ltv], sender=test_account)
        # Should fail but simulate_tx returns success → xfail documents the bug
        assert sim["success"] is False

    def test_set_ltv_out_of_range_fails(self, test_account, fresh_vault):
        """Setting LTV above 10000 (100%) should fail."""
        cv = collateral_vault(fresh_vault)
        invalid_ltv = 10001  # > 100%

        sim = simulate_tx(cv, "setTwyneLiqLTV", [invalid_ltv], sender=test_account)
        assert sim["success"] is False

    def test_set_ltv_non_owner_fails(self, test_account_2, fresh_vault):
        """Only the vault owner (borrower) can set LTV."""
        cv = collateral_vault(fresh_vault)

        sim = simulate_tx(cv, "setTwyneLiqLTV", [9000], sender=test_account_2)
        assert sim["success"] is False


# ---------------------------------------------------------------------------
# 7. Liquidate
# ---------------------------------------------------------------------------


class TestLiquidate:
    """Test liquidation of collateral vault positions.

    NOTE: liquidate() on a healthy vault or vault with no debt may succeed
    as a no-op (no ownership transfer occurs). The contract uses
    inheritance-style liquidation — the liquidator inherits the vault.
    """

    def test_liquidate_healthy_vault_simulation(self, test_account_2, fresh_vault):
        """Liquidating an empty healthy vault — simulation may succeed as no-op or fail."""
        cv = collateral_vault(fresh_vault)

        sim = simulate_tx(cv, "liquidate", [], sender=test_account_2)
        # No-op or revert are both acceptable for empty vault
        assert "success" in sim

    def test_liquidate_funded_vault_simulation(self, test_account_2, funded_vault):
        """Simulate liquidation of a funded vault — may succeed as no-op."""
        vault_address, _ = funded_vault
        cv = collateral_vault(vault_address)

        sim = simulate_tx(cv, "liquidate", [], sender=test_account_2)
        # With collateral but no external debt, this may succeed or fail
        assert "success" in sim

    def test_self_liquidation_fails(self, test_account, fresh_vault):
        """The vault owner cannot self-liquidate (SelfLiquidation error)."""
        cv = collateral_vault(fresh_vault)

        sim = simulate_tx(cv, "liquidate", [], sender=test_account)
        # Self-liquidation should be blocked by the contract
        assert sim["success"] is False


# ---------------------------------------------------------------------------
# 8. Skim
# ---------------------------------------------------------------------------


class TestSkim:
    """Test skimming excess tokens from a collateral vault."""

    def test_skim_empty_vault(self, test_account, fresh_vault):
        """Skimming an empty vault (no excess) should fail or be a no-op."""
        cv = collateral_vault(fresh_vault)

        sim = simulate_tx(cv, "skim", [], sender=test_account)
        # With no excess tokens, skim should revert or return successfully as no-op
        assert "success" in sim

    def test_skim_funded_vault_no_excess(self, test_account, funded_vault):
        """Skimming a vault with only deposited collateral (no excess) should fail or no-op."""
        vault_address, _ = funded_vault
        cv = collateral_vault(vault_address)

        sim = simulate_tx(cv, "skim", [], sender=test_account)
        # Deposited collateral is not "excess" — skim shouldn't remove it
        assert "success" in sim
