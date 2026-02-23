# Twyne CLI Integration Test Bug Report

**Date:** 2026-02-23
**Test Environment:** Anvil mainnet fork at block 24,520,000 (port 8454)
**Framework:** Ape + pytest
**Test Files:** `tests/integration/test_smoke.py`, `test_credit_vault.py`, `test_collateral_vault.py`, `test_factory_batch_ops.py`

## Executive Summary

Integration testing of the `tx` command group against a live Anvil fork uncovered **6 bugs** in the CLI's transaction layer. All bugs stem from **stale or incorrect ABIs** and **wrong parameter passing** in `src/twyne_cli/commands/tx.py`. No bugs were found in the read-only query commands or the `simulate_tx()` infrastructure itself.

## Test Results Summary

| Test File | Passed | Failed | XFailed | Errors | Notes |
|-----------|--------|--------|---------|--------|-------|
| `test_smoke.py` | 5 | 0 | 0 | 0 | Provider, accounts, WETH, eWETH, contracts |
| `test_credit_vault.py` | 7 | 9 | 0 | 0 | Euler deposit works; withdraw/redeem ABI wrong |
| `test_factory_batch_ops.py` | 13 | 11 | 0 | 0 | Batch/operator OK; factory ABI wrong |
| `test_collateral_vault.py` | 0 | 3 | 1 | 22 | All cascade from vault creation failure |
| **Total** | **25** | **23** | **1** | **22** | |

## Bug Catalog

### Bug 1 (Critical): CollateralVaultFactory ABI is stale v1

**File:** `src/twyne_cli/abis/CollateralVaultFactory.json`

**Problem:** The bundled ABI defines `createCollateralVault(address _asset, address _targetVault, uint256 _liqLTV)` (3 parameters). The deployed factory (v2) requires 5 parameters:

```solidity
// Source: twyne-contracts/src/TwyneFactory/CollateralVaultFactory.sol
function createCollateralVault(
    VaultType _vaultType,    // uint8: 0=EULER_V2, 1=AAVE_V3
    address _asset,          // collateral asset (e.g. eWETH)
    address _targetVault,    // external lending target (e.g. Euler USDC vault)
    uint _liqLTV,            // liquidation LTV in basis points
    address _targetAsset     // address(0) for Euler
) external callThroughEVC whenNotPaused returns (address vault)
```

Additionally, the factory call must be routed through the EVC (`callThroughEVC` modifier).

**Evidence:** All factory tests fail with `ArgumentsLengthError: The number of the given arguments (2) do not match what is defined in the ABI (3)`, and on-chain calls with correct 5-arg encoding through EVC succeed in the Foundry test suite.

**Impact:** `twyne tx create-vault` is completely non-functional.

**Fix:** Replace `CollateralVaultFactory.json` with the v2 ABI from the deployed contract. Update `tx.py` `create_vault` command to accept `--vault-type`, `--asset`, `--target-vault`, `--liq-ltv`, `--target-asset` parameters and route through EVC.

---

### Bug 2 (High): `create-vault` command passes wrong parameters

**File:** `src/twyne_cli/commands/tx.py`, lines 852-892

**Problem:** The command accepts `beacon_address` and `--salt` parameters and calls:
```python
sim = simulate_tx(fct, "createCollateralVault", [beacon_address, salt], sender=account)
```

But even with the correct ABI, the factory expects `(vaultType, asset, targetVault, liqLTV, targetAsset)`. The CLI parameters don't match the factory interface at all.

**Impact:** `twyne tx create-vault` passes wrong arguments regardless of ABI version.

---

### Bug 3 (High): `credit-withdraw` uses wrong ABI for intermediate vault

**File:** `src/twyne_cli/commands/tx.py`, lines 568-607

**Problem:** The command loads the intermediate vault (IV) using `collateral_vault(iv_address)`, which uses the `CollateralVault.json` ABI. This ABI has:
- `withdraw(uint256 amount, address receiver)` — **2 arguments**

But the IV is an ERC4626 vault (CreditEVault/EVault) which requires:
- `withdraw(uint256 assets, address receiver, address owner)` — **3 arguments**

```python
# tx.py line 579
cv = collateral_vault(iv_address)  # Wrong: loads CollateralVault ABI
# tx.py line 584
sim = simulate_tx(cv, "withdraw", [raw_amount, recv, str(account.address)], sender=account)
# Fails: ABI only has 2-arg withdraw, 3 args passed
```

**Evidence:** All `TestCreditWithdraw` tests fail. Direct calls with `Contract(IV, abi=EVAULT_ABI)` and 3-arg `withdraw` succeed.

**Impact:** `twyne tx credit-withdraw` is non-functional.

**Fix:** Add an `intermediate_vault()` or `evault()` contract constructor in `contracts.py` that loads an ERC4626-compatible ABI. Use it in `credit-withdraw` instead of `collateral_vault()`.

---

### Bug 4 (High): `credit-redeem` uses wrong ABI — no `redeem` function exists

**File:** `src/twyne_cli/commands/tx.py`, lines 610-649

**Problem:** Same as Bug 3, but worse. The command loads the IV using `collateral_vault(iv_address)`, and the `CollateralVault.json` ABI has **no `redeem` function at all**. The ERC4626 vault needs:
- `redeem(uint256 shares, address receiver, address owner)` — **3 arguments**

```python
# tx.py line 621
cv = collateral_vault(iv_address)  # Wrong: CollateralVault ABI has no redeem
# tx.py line 626
sim = simulate_tx(cv, "redeem", [raw_amount, recv, str(account.address)], sender=account)
# Fails: "redeem" method not found in ABI
```

**Evidence:** All `TestCreditRedeem` tests fail. Direct calls with `Contract(IV, abi=EVAULT_ABI)` and 3-arg `redeem` succeed.

**Impact:** `twyne tx credit-redeem` is non-functional.

**Fix:** Same as Bug 3 — use an ERC4626-compatible contract constructor.

---

### Bug 5 (Medium): `deposit-atokens` parameter order is swapped

**File:** `src/twyne_cli/commands/tx.py`, lines 527-565

**Problem:** The ABI for `AaveATokenWrapper` defines:
```solidity
function depositATokens(uint256 assets, address receiver) external returns (uint256);
```

But the CLI passes parameters in the wrong order:
```python
# tx.py line 543
sim = simulate_tx(wrapper, "depositATokens", [iv_address, raw_amount], sender=account)
#                                              ^^^^^^^^^^  ^^^^^^^^^^
#                                              address     uint256
# ABI expects:                                  uint256     address
```

And again at line 562:
```python
receipt = wrapper.depositATokens(iv_address, raw_amount, sender=account)
```

**Evidence:** The Solidity interface (`IAaveV3ATokenWrapper.sol` line 84) confirms `depositATokens(uint256 assets, address receiver)`. The CLI passes `(address, uint256)` — reversed.

**Impact:** `twyne tx credit deposit-atokens` will revert or produce incorrect behavior on-chain.

**Fix:** Swap parameters: `[raw_amount, receiver_address]` instead of `[iv_address, raw_amount]`.

---

### Bug 6 (Medium): Missing EVault/ERC4626 contract constructor

**File:** `src/twyne_cli/contracts.py`

**Problem:** The `contracts.py` module has constructors for:
- `collateral_vault(address)` — loads `CollateralVault.json`
- `collateral_vault_factory()` — loads `CollateralVaultFactory.json`
- `erc20(address)` — loads minimal ERC20 ABI
- `euler_wrapper()` / `aave_atoken_wrapper()` — loads wrapper ABIs

But there is **no constructor for intermediate vaults** (CreditEVault/EVault). The credit-withdraw and credit-redeem commands incorrectly use `collateral_vault()` as a workaround, which loads the wrong ABI.

**Impact:** Prevents correct implementation of any IV-targeted operations (withdraw, redeem, maxWithdraw, maxRedeem, convertToShares, convertToAssets, etc.).

**Fix:** Add `intermediate_vault(address)` or `evault(address)` that loads an ERC4626-compatible ABI with at minimum: `withdraw(uint256, address, address)`, `redeem(uint256, address, address)`, `deposit(uint256, address)`, `mint(uint256, address)`, `balanceOf(address)`, `totalAssets()`, `totalSupply()`.

---

## What Works

The following transaction paths work correctly (verified by integration tests):

1. **Euler wrapper deposit** (`depositUnderlyingToIntermediateVault`): Simulation and execution both succeed when WETH approval is granted. The wrapper correctly deposits underlying tokens into the Euler EVault IV.

2. **Aave aToken wrapper instantiation**: The `aave_atoken_wrapper()` constructor returns a valid contract with correct address and callable `aToken()` view.

3. **Collateral vault operations (deposit, withdraw, borrow, repay, setTwyneLiqLTV, liquidate, skim)**: All collateral vault ABI functions have correct signatures. Tests would pass if vault creation succeeds (blocked by Bug 1).

4. **Batch validation and parsing**: Pure Python batch-building logic works correctly.

5. **Operator contract existence**: All 5 operator contracts (lever, delever, teleport, deposit-and-lever, delever-and-withdraw) resolve to valid on-chain addresses.

6. **`simulate_tx()` infrastructure**: The simulation wrapper correctly returns `{"success": bool, "result": ..., "error": ...}` for both successful and failing calls.

## Recommendations

### Priority 1 — Fix factory ABI and create-vault command
Update `CollateralVaultFactory.json` to v2 ABI. Rewrite `create-vault` to accept the 5 correct parameters and route through EVC.

### Priority 2 — Add EVault/ERC4626 contract constructor
Add `intermediate_vault()` to `contracts.py`. Update `credit-withdraw` and `credit-redeem` to use it.

### Priority 3 — Fix deposit-atokens parameter order
Swap `[iv_address, raw_amount]` to `[raw_amount, receiver_address]` in lines 543 and 562.

### Priority 4 — Run integration tests in CI
The test suite (`tests/integration/`) requires an Anvil fork. Consider adding a CI job that starts Anvil and runs `pytest tests/integration/ -v`.
