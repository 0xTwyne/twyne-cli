"""Integration tests for automatic token approval handling.

Tests ensure_allowance() helper and collect_approval_requirements() for batch
operations against a real Anvil fork at block 24520000.

Covers:
- ensure_allowance: sufficient allowance (no tx), insufficient (sends approval),
  max approve (uint256 max), skip_approval (raises on insufficient)
- collect_approval_requirements: deposit generates requirement, repay generates
  requirement, explicit token.approve skips that pair
- batch execute with auto-approval end-to-end
"""

import pytest

from twyne_cli.batch import collect_approval_requirements
from twyne_cli.contracts import collateral_vault, erc20
from twyne_cli.transactions import ensure_allowance

from .conftest import (
    DEFAULT_LIQ_LTV,
    ERC20_ABI,
    EULER_EWETH,
    EULER_EWETH_IV,
    EULER_TARGET_VAULT,
    _approve_erc20,
    _balance_of,
    _create_vault_via_evc,
    _deposit_via_evc,
)


# ---------------------------------------------------------------------------
# 1. ensure_allowance — unit-level tests against Anvil
# ---------------------------------------------------------------------------


class TestEnsureAllowanceSufficient:
    """When allowance is already sufficient, no approval tx is sent."""

    def test_returns_true_when_already_approved(self, test_account, fresh_vault, funded_eweth):
        """ensure_allowance returns True if current allowance >= amount."""
        vault_address = fresh_vault
        amount = funded_eweth

        # Pre-approve via raw RPC
        _approve_erc20(str(test_account.address), EULER_EWETH, vault_address, amount)

        result = ensure_allowance(
            EULER_EWETH, vault_address, amount, test_account,
            skip_confirm=True,
        )
        assert result is True

    def test_no_tx_when_already_approved(self, test_account, fresh_vault, funded_eweth):
        """No approval tx is sent when allowance is already sufficient."""
        vault_address = fresh_vault
        amount = funded_eweth

        # Pre-approve
        _approve_erc20(str(test_account.address), EULER_EWETH, vault_address, amount)

        token = erc20(EULER_EWETH)
        allowance_before = token.allowance(str(test_account.address), vault_address)

        ensure_allowance(
            EULER_EWETH, vault_address, amount, test_account,
            skip_confirm=True,
        )

        # Allowance unchanged — no new approve tx was sent
        allowance_after = token.allowance(str(test_account.address), vault_address)
        assert allowance_after == allowance_before


class TestEnsureAllowanceSendsApproval:
    """When allowance is insufficient, an approval tx is sent."""

    def test_approves_exact_amount(self, test_account, fresh_vault, funded_eweth):
        """ensure_allowance sends an approval for the exact amount."""
        vault_address = fresh_vault
        amount = funded_eweth

        token = erc20(EULER_EWETH)
        allowance_before = token.allowance(str(test_account.address), vault_address)
        assert allowance_before < amount  # No pre-approval

        result = ensure_allowance(
            EULER_EWETH, vault_address, amount, test_account,
            skip_confirm=True,
        )
        assert result is True

        allowance_after = token.allowance(str(test_account.address), vault_address)
        assert allowance_after >= amount


class TestEnsureAllowanceMaxApprove:
    """--max-approve sends uint256 max approval."""

    def test_max_approval(self, test_account, fresh_vault, funded_eweth):
        """ensure_allowance with max_approve=True approves max uint256."""
        vault_address = fresh_vault
        amount = funded_eweth

        result = ensure_allowance(
            EULER_EWETH, vault_address, amount, test_account,
            skip_confirm=True, max_approve=True,
        )
        assert result is True

        token = erc20(EULER_EWETH)
        allowance = token.allowance(str(test_account.address), vault_address)
        assert allowance == 2**256 - 1


class TestEnsureAllowanceSkipRaises:
    """--skip-approval raises UsageError when allowance is insufficient."""

    def test_raises_on_insufficient(self, test_account, fresh_vault, funded_eweth):
        """skip_approval=True raises click.UsageError."""
        import click

        vault_address = fresh_vault
        amount = funded_eweth

        with pytest.raises(click.UsageError, match="Insufficient allowance"):
            ensure_allowance(
                EULER_EWETH, vault_address, amount, test_account,
                skip_approval=True,
            )

    def test_skip_approval_ok_when_sufficient(self, test_account, fresh_vault, funded_eweth):
        """skip_approval=True returns True when allowance is already sufficient."""
        vault_address = fresh_vault
        amount = funded_eweth

        # Pre-approve
        _approve_erc20(str(test_account.address), EULER_EWETH, vault_address, amount)

        result = ensure_allowance(
            EULER_EWETH, vault_address, amount, test_account,
            skip_approval=True,
        )
        assert result is True


# ---------------------------------------------------------------------------
# 2. collect_approval_requirements — batch analysis
# ---------------------------------------------------------------------------


class TestCollectApprovalRequirementsDeposit:
    """Batch with collateral.deposit generates correct approval requirement."""

    def test_deposit_requirement(self, test_account, fresh_vault):
        """collateral.deposit in batch returns approval for cv.asset()."""
        batch_data = {
            "operations": [
                {"action": "collateral.deposit", "vault": fresh_vault, "amount": "1.0"},
            ]
        }
        reqs = collect_approval_requirements(batch_data, str(test_account.address))
        assert len(reqs) == 1
        req = reqs[0]
        assert req["token"].lower() == EULER_EWETH.lower()
        assert req["spender"].lower() == fresh_vault.lower()
        assert req["amount"] == 1 * 10**18


class TestCollectApprovalRequirementsRepay:
    """Batch with collateral.repay generates correct approval requirement."""

    def test_repay_requirement(self, test_account, fresh_vault):
        """collateral.repay returns approval for cv.targetAsset()."""
        batch_data = {
            "operations": [
                {"action": "collateral.repay", "vault": fresh_vault, "amount": "100.0"},
            ]
        }
        cv = collateral_vault(fresh_vault)
        target_asset = cv.targetAsset()

        reqs = collect_approval_requirements(batch_data, str(test_account.address))
        assert len(reqs) == 1
        req = reqs[0]
        assert req["token"].lower() == target_asset.lower()
        assert req["spender"].lower() == fresh_vault.lower()


class TestCollectApprovalRequirementsSkipsExplicit:
    """Batch with explicit token.approve skips that (token, spender) pair."""

    def test_explicit_approve_skipped(self, test_account, fresh_vault):
        """When batch has token.approve for the same pair, no requirement generated."""
        batch_data = {
            "operations": [
                {
                    "action": "token.approve",
                    "token": EULER_EWETH,
                    "spender": fresh_vault,
                    "amount": "10.0",
                },
                {"action": "collateral.deposit", "vault": fresh_vault, "amount": "1.0"},
            ]
        }
        reqs = collect_approval_requirements(batch_data, str(test_account.address))
        assert len(reqs) == 0

    def test_different_pair_not_skipped(self, test_account, fresh_vault):
        """Explicit approve for a different pair doesn't skip the deposit pair."""
        batch_data = {
            "operations": [
                {
                    "action": "token.approve",
                    "token": "0x0000000000000000000000000000000000000001",
                    "spender": fresh_vault,
                    "amount": "10.0",
                },
                {"action": "collateral.deposit", "vault": fresh_vault, "amount": "1.0"},
            ]
        }
        reqs = collect_approval_requirements(batch_data, str(test_account.address))
        assert len(reqs) == 1


class TestCollectApprovalRequirementsAggregation:
    """Multiple deposits to the same vault aggregate amounts."""

    def test_aggregates_same_pair(self, test_account, fresh_vault):
        """Two deposits to the same vault produce one requirement with summed amount."""
        batch_data = {
            "operations": [
                {"action": "collateral.deposit", "vault": fresh_vault, "amount": "1.0"},
                {"action": "collateral.deposit", "vault": fresh_vault, "amount": "2.0"},
            ]
        }
        reqs = collect_approval_requirements(batch_data, str(test_account.address))
        assert len(reqs) == 1
        assert reqs[0]["amount"] == 3 * 10**18


# ---------------------------------------------------------------------------
# 3. End-to-end: deposit with auto-approval via ensure_allowance
# ---------------------------------------------------------------------------


class TestDepositWithAutoApproval:
    """End-to-end: ensure_allowance + deposit via EVC succeeds."""

    def test_approve_and_deposit(self, test_account, fresh_vault, funded_eweth):
        """Auto-approve then deposit succeeds without manual approval."""
        vault_address = fresh_vault
        deposit_amount = min(funded_eweth, 5 * 10**17)  # 0.5 eWETH

        # No pre-approval — ensure_allowance handles it
        result = ensure_allowance(
            EULER_EWETH, vault_address, deposit_amount, test_account,
            skip_confirm=True,
        )
        assert result is True

        # Now deposit should succeed (approval was set by ensure_allowance)
        receipt = _deposit_via_evc(test_account, vault_address, deposit_amount)
        assert receipt["status"] == "0x1"

        # Verify deposit landed
        cv = collateral_vault(vault_address)
        total = cv.totalAssetsDepositedOrReserved()
        assert total > 0
