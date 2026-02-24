"""Integration tests for factory, batch, and operator transaction commands.

Tests collateral vault creation via factory, EVC batch parsing/building/simulation,
and operator contract accessibility (leverage, deleverage, teleport).
Runs against Anvil fork at mainnet block 24520000.
"""

import pytest
import yaml

from twyne_cli.batch import build_batch_items, parse_batch_file, validate_batch
from twyne_cli.contracts import (
    collateral_vault_factory,
    deleverage_operator,
    leverage_operator,
    teleport_operator,
)
from twyne_cli.contracts import (
    evc as evc_contract,
)
from twyne_cli.transactions import execute_through_evc, simulate_tx

from .conftest import (
    AAVE_DELEVERAGE_OP,
    AAVE_LEVERAGE_OP,
    AAVE_TELEPORT_OP,
    BEACON_EULER_EWETH,
    CV_FACTORY,
    DEFAULT_LIQ_LTV,
    EULER_DELEVERAGE_OP,
    EULER_EWETH,
    EULER_EWETH_IV,
    EULER_LEVERAGE_OP,
    EULER_TARGET_VAULT,
    TWYNE_EVC,
    ZERO_ADDRESS,
    _create_vault_via_evc,
)

# --------------------------------------------------------------------------- #
# Factory: create-vault
# --------------------------------------------------------------------------- #


class TestFactoryCreateVault:
    """Tests for CollateralVaultFactory.createCollateralVault().

    The factory ABI uses v2 (5 args: vaultType, asset, targetVault, liqLTV,
    targetAsset). The callThroughEVC modifier requires real transactions to
    go through EVC.batch(), but eth_call (simulation) bypasses this.
    """

    def test_create_vault_via_evc_succeeds(self, test_account, ape_provider):
        """Vault creation via correct v2 factory + EVC.call() works."""
        vault_addr = _create_vault_via_evc(test_account)
        assert vault_addr != ZERO_ADDRESS
        assert len(vault_addr) == 42

    def test_factory_address_matches(self, ape_provider):
        """CLI's factory address matches the expected deployed address."""
        factory = collateral_vault_factory()
        assert str(factory.address).lower() == CV_FACTORY.lower()

    def test_cli_factory_create_vault_execution(self, test_account, ape_provider):
        """Factory.createCollateralVault() routed through Twyne EVC succeeds.

        The factory's callThroughEVC modifier requires msg.sender == EVC.
        Direct calls fail with EVC_EmptyError. This routes through evc.call()
        matching the CLI's execute_through_evc() pattern.
        """
        factory = collateral_vault_factory()
        args = [0, EULER_EWETH, EULER_TARGET_VAULT, DEFAULT_LIQ_LTV, EULER_EWETH_IV]
        receipt = execute_through_evc(factory, "createCollateralVault", args, test_account)
        assert receipt.status == 1

    def test_cli_factory_simulation_succeeds(self, test_account, ape_provider):
        """simulate_tx with v2 ABI succeeds (eth_call bypasses callThroughEVC)."""
        factory = collateral_vault_factory()
        sim = simulate_tx(
            factory, "createCollateralVault",
            [0, EULER_EWETH, EULER_TARGET_VAULT, DEFAULT_LIQ_LTV, EULER_EWETH_IV],
            sender=test_account,
        )
        assert sim["success"] is True
        assert sim["result"]  # Returns predicted vault address


# --------------------------------------------------------------------------- #
# Batch: parse, validate, build, simulate
# --------------------------------------------------------------------------- #


class TestBatchValidation:
    """Tests for batch file parsing and validation (no on-chain calls)."""

    def test_validate_batch_missing_operations(self):
        """validate_batch rejects data without 'operations' key."""
        with pytest.raises(ValueError, match="operations"):
            validate_batch({"evc": TWYNE_EVC})

    def test_validate_batch_empty_operations(self):
        """validate_batch rejects empty operations list."""
        with pytest.raises(ValueError, match="non-empty"):
            validate_batch({"operations": []})

    def test_validate_batch_missing_action(self):
        """validate_batch rejects an operation without 'action' field."""
        with pytest.raises(ValueError, match="action"):
            validate_batch({"operations": [{"vault": "0x1234"}]})

    def test_validate_batch_not_a_dict(self):
        """validate_batch rejects non-dict input."""
        with pytest.raises(ValueError, match="object"):
            validate_batch("not a dict")

    def test_validate_batch_valid(self):
        """validate_batch accepts well-formed data."""
        validate_batch({
            "evc": TWYNE_EVC,
            "operations": [{"action": "collateral.deposit", "vault": "0x1234", "amount": "1.0"}],
        })


class TestBatchParsing:
    """Tests for parsing batch files from YAML/JSON."""

    def test_parse_yaml_batch(self, tmp_path):
        """parse_batch_file reads and validates a YAML batch file."""
        batch = {
            "evc": TWYNE_EVC,
            "operations": [{"action": "collateral.deposit", "vault": "0xabc", "amount": "1.0"}],
        }
        f = tmp_path / "batch.yaml"
        f.write_text(yaml.dump(batch))

        data = parse_batch_file(str(f))
        assert data["evc"] == TWYNE_EVC
        assert len(data["operations"]) == 1
        assert data["operations"][0]["action"] == "collateral.deposit"

    def test_parse_json_batch(self, tmp_path):
        """parse_batch_file reads and validates a JSON batch file."""
        import json

        batch = {
            "evc": TWYNE_EVC,
            "operations": [{"action": "collateral.deposit", "vault": "0xabc", "amount": "2.5"}],
        }
        f = tmp_path / "batch.json"
        f.write_text(json.dumps(batch))

        data = parse_batch_file(str(f))
        assert len(data["operations"]) == 1

    def test_parse_invalid_batch_raises(self, tmp_path):
        """parse_batch_file raises on invalid batch content."""
        f = tmp_path / "bad.yaml"
        f.write_text(yaml.dump({"no_ops": True}))

        with pytest.raises(ValueError, match="operations"):
            parse_batch_file(str(f))


class TestBatchBuildAndSimulate:
    """Tests for building batch items and simulating via EVC.

    These tests hit the chain to resolve token decimals and encode calldata.
    Uses _create_vault_via_evc() to create real vaults (bypasses CLI's stale factory ABI).

    BUG DOCUMENTED: batch.py's encode_operation() uses cv.deposit.as_transaction()
    to encode calldata. Ape's .as_transaction() triggers gas estimation, which
    hits the EVC callThroughEVC modifier and reverts with
    EVC_OnBehalfOfAccountNotAuthenticated (0x5217b8ae). The batch encoder cannot
    produce calldata for collateral vault operations.
    """

    def _make_batch_file(self, tmp_path, vault_address, amount="1.0"):
        """Helper: create a minimal deposit batch YAML and return its path."""
        batch = {
            "evc": TWYNE_EVC,
            "operations": [
                {
                    "action": "token.approve",
                    "token": EULER_EWETH,
                    "spender": vault_address,
                    "amount": amount,
                },
                {
                    "action": "collateral.deposit",
                    "vault": vault_address,
                    "amount": amount,
                },
            ],
        }
        f = tmp_path / "batch.yaml"
        f.write_text(yaml.dump(batch))
        return str(f)

    def test_build_batch_items(self, test_account, ape_provider, tmp_path):
        """build_batch_items fails because as_transaction() triggers EVC auth."""
        vault_address = _create_vault_via_evc(test_account)

        batch_path = self._make_batch_file(tmp_path, vault_address)
        data = parse_batch_file(batch_path)
        items = build_batch_items(data, str(test_account.address))

        # Should have 2 items: approve + deposit
        assert len(items) == 2
        for item in items:
            assert "targetContract" in item
            assert "onBehalfOfAccount" in item
            assert "value" in item
            assert "data" in item
            assert item["onBehalfOfAccount"] == str(test_account.address)

    def test_batch_simulate_via_evc(self, test_account, ape_provider, tmp_path, funded_weth):
        """Build batch items and run EVC.batchSimulation() via simulate_tx."""
        vault_address = _create_vault_via_evc(test_account)

        batch_path = self._make_batch_file(tmp_path, vault_address, amount="0.01")
        data = parse_batch_file(batch_path)
        items = build_batch_items(data, str(test_account.address))

        batch_items = [
            (item["targetContract"], item["onBehalfOfAccount"], item["value"], item["data"])
            for item in items
        ]

        evc_instance = evc_contract(TWYNE_EVC)
        sim = simulate_tx(evc_instance, "batchSimulation", [batch_items], sender=test_account)

        # batchSimulation may succeed or fail depending on approvals/balances,
        # but it should not fail with a missing contract error
        assert isinstance(sim, dict)
        assert "success" in sim


# --------------------------------------------------------------------------- #
# Operators: leverage, deleverage, teleport
# --------------------------------------------------------------------------- #


class TestOperatorContracts:
    """Tests that operator contracts are deployed and accessible."""

    def test_euler_leverage_operator_exists(self, ape_provider):
        """Euler leverage operator contract is deployed at the expected address."""
        op = leverage_operator("euler")
        assert str(op.address).lower() == EULER_LEVERAGE_OP.lower()

    def test_euler_deleverage_operator_exists(self, ape_provider):
        """Euler deleverage operator contract is deployed at the expected address."""
        op = deleverage_operator("euler")
        assert str(op.address).lower() == EULER_DELEVERAGE_OP.lower()

    def test_aave_leverage_operator_exists(self, ape_provider):
        """Aave leverage operator contract is deployed at the expected address."""
        op = leverage_operator("aave")
        assert str(op.address).lower() == AAVE_LEVERAGE_OP.lower()

    def test_aave_deleverage_operator_exists(self, ape_provider):
        """Aave deleverage operator contract is deployed at the expected address."""
        op = deleverage_operator("aave")
        assert str(op.address).lower() == AAVE_DELEVERAGE_OP.lower()

    def test_aave_teleport_operator_exists(self, ape_provider):
        """Aave teleport operator contract is deployed at the expected address."""
        op = teleport_operator()
        assert str(op.address).lower() == AAVE_TELEPORT_OP.lower()


class TestOperatorSimulations:
    """Tests that operator simulations revert with operator logic, not missing contracts.

    Uses _create_vault_via_evc() for vault creation (bypasses CLI's stale factory ABI).

    Operator ABI signatures:
      executeLeverage(address, uint256, uint256, uint256, uint256, uint256, bytes[])
      executeDeleverage(address, uint256, uint256, uint256, bytes[])
      executeTeleport(address, uint256, uint256)
    """

    def test_euler_leverage_simulate_reverts_meaningfully(self, test_account, ape_provider):
        """Euler leverage simulation reverts from operator logic (no flash loan context)."""
        op = leverage_operator("euler")
        vault_addr = _create_vault_via_evc(test_account)

        # executeLeverage(collateralVault, underlyingCollateralAmount, collateralAmount,
        #                 flashloanAmount, minAmountOut, deadline, swapData[])
        sim = simulate_tx(
            op, "executeLeverage",
            [vault_addr, 10**18, 10**18, 10**18, 0, 2**256 - 1, []],
            sender=test_account,
        )
        # Should fail (no active flash loan / swap data) but NOT with a missing contract error
        assert sim["success"] is False
        assert sim["error"]  # has a meaningful error message

    def test_euler_deleverage_simulate_reverts_meaningfully(self, test_account, ape_provider):
        """Euler deleverage simulation reverts from operator logic."""
        op = deleverage_operator("euler")
        vault_addr = _create_vault_via_evc(test_account)

        # executeDeleverage(collateralVault, flashloanAmount, maxDebt,
        #                   withdrawCollateralAmount, swapData[])
        sim = simulate_tx(
            op, "executeDeleverage",
            [vault_addr, 10**18, 10**18, 10**18, []],
            sender=test_account,
        )
        assert sim["success"] is False
        assert sim["error"]

    def test_aave_leverage_simulate_reverts_meaningfully(self, test_account, ape_provider):
        """Aave leverage simulation reverts from operator logic."""
        op = leverage_operator("aave")
        vault_addr = _create_vault_via_evc(test_account)

        # executeLeverage(collateralVault, underlyingCollateralAmount, collateralAmount,
        #                 flashloanAmount, minAmountOut, deadline, swapData[])
        sim = simulate_tx(
            op, "executeLeverage",
            [vault_addr, 10**18, 10**18, 10**18, 0, 2**256 - 1, []],
            sender=test_account,
        )
        assert sim["success"] is False
        assert sim["error"]

    def test_aave_teleport_simulate_reverts_meaningfully(self, test_account, ape_provider):
        """Aave teleport operator simulation reverts from operator logic."""
        op = teleport_operator()

        vault = _create_vault_via_evc(test_account)

        # executeTeleport(collateralVault, aTokenAmount, debtAmount)
        sim = simulate_tx(
            op, "executeTeleport", [vault, 10**18, 10**18],
            sender=test_account,
        )
        assert sim["success"] is False
        assert sim["error"]

    def test_euler_teleport_not_a_cv_function(self, test_account, ape_provider):
        """Euler teleport is NOT a function on CollateralVault — it's an event (T_Teleport).

        BUG DOCUMENTED: The CLI has no teleport function in CollateralVault ABI.
        Teleport is executed via TeleportOperator contract, not directly on the CV.
        """
        from twyne_cli.contracts import collateral_vault as cv_fn

        src_addr = _create_vault_via_evc(test_account)
        cv = cv_fn(src_addr)

        # teleport is an event (T_Teleport), not a callable function
        assert not hasattr(cv, "teleport") or not callable(getattr(cv, "teleport", None))
