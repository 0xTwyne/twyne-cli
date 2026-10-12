"""Integration tests for PT-Strata aToken wrapper on Tenderly fork.

Wrapper: 0x223d402b82d6b5c4f0b9bc0348960098228139ef
  Name:  Wrapped Aave Ethereum PT_srUSDe_2APR2026
  Asset: PT-srUSDe-2APR2026 (0x9bf45ab47747f4b4dd09b3c2c73953484b4eb375)
  Type:  ERC4626 vault (1:1 ratio, wraps PT tokens)

Tenderly fork RPC: chain ID 9991 (all accounts unlocked)
"""

import httpx
from eth_abi import encode as abi_encode

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
RPC = "https://virtual.mainnet.eu.rpc.tenderly.co/aa9655aa-df8e-4bdf-92dc-55798263d381"
WRAPPER = "0x223d402b82d6b5c4f0b9bc0348960098228139ef"
PT_TOKEN = "0x9bf45ab47747f4b4dd09b3c2c73953484b4eb375"

# Large PT holder (admin) — Tenderly auto-unlocks all accounts
PT_WHALE = "0x1241ec22c9bdf16ba1eb636f2a8de7e28a4343cf"

# Test accounts
ALICE = "0xf39Fd6e51aad88F6F4ce6aB8827279cffFb92266"
BOB = "0x70997970C51812dc3A010C7d01b50e0d17dc79C8"

# Amounts
ONE_TOKEN = 10**18
TEN_TOKENS = 10 * 10**18
HUNDRED_TOKENS = 100 * 10**18

# ---------------------------------------------------------------------------
# RPC helpers
# ---------------------------------------------------------------------------

def rpc_call(method, params=None):
    resp = httpx.post(
        RPC,
        json={"jsonrpc": "2.0", "method": method, "params": params or [], "id": 1},
        timeout=60,
    )
    data = resp.json()
    if "error" in data:
        raise RuntimeError(f"RPC error ({method}): {data['error']}")
    return data.get("result")


def eth_call(to, data, from_addr=None):
    call = {"to": to, "data": data}
    if from_addr:
        call["from"] = from_addr
    resp = httpx.post(
        RPC,
        json={"jsonrpc": "2.0", "method": "eth_call", "params": [call, "latest"], "id": 1},
        timeout=30,
    )
    r = resp.json()
    if "error" in r:
        return None
    return r.get("result")


def send_tx(from_addr, to_addr, data, value=0, gas=500_000):
    """Send a transaction on the Tenderly fork (all accounts unlocked)."""
    tx = {
        "from": from_addr,
        "to": to_addr,
        "data": data,
        "gas": hex(gas),
    }
    if value > 0:
        tx["value"] = hex(value)
    tx_hash = rpc_call("eth_sendTransaction", [tx])
    receipt = rpc_call("eth_getTransactionReceipt", [tx_hash])
    return receipt


def balance_of(token, account):
    data = "0x70a08231" + abi_encode(["address"], [account]).hex()
    result = eth_call(token, data)
    return int(result, 16) if result else 0


def approve(token, owner, spender, amount):
    data = "0x095ea7b3" + abi_encode(["address", "uint256"], [spender, amount]).hex()
    receipt = send_tx(owner, token, data)
    assert receipt["status"] == "0x1", f"Approve failed: {receipt}"
    return receipt


def allowance(token, owner, spender):
    data = "0xdd62ed3e" + abi_encode(["address", "address"], [owner, spender]).hex()
    result = eth_call(token, data)
    return int(result, 16) if result else 0


def transfer(token, from_addr, to_addr, amount):
    data = "0xa9059cbb" + abi_encode(["address", "uint256"], [to_addr, amount]).hex()
    receipt = send_tx(from_addr, token, data)
    assert receipt["status"] == "0x1", f"Transfer failed: {receipt}"
    return receipt


# ---------------------------------------------------------------------------
# ERC4626 helpers (for the wrapper)
# ---------------------------------------------------------------------------

def deposit(wrapper, assets, receiver, sender):
    """ERC4626 deposit(uint256, address)"""
    data = "0x6e553f65" + abi_encode(["uint256", "address"], [assets, receiver]).hex()
    return send_tx(sender, wrapper, data)


def withdraw(wrapper, assets, receiver, owner, sender):
    """ERC4626 withdraw(uint256, address, address)"""
    data = "0xb460af94" + abi_encode(
        ["uint256", "address", "address"], [assets, receiver, owner]
    ).hex()
    return send_tx(sender, wrapper, data)


def mint_shares(wrapper, shares, receiver, sender):
    """ERC4626 mint(uint256, address)"""
    data = "0x94bf804d" + abi_encode(["uint256", "address"], [shares, receiver]).hex()
    return send_tx(sender, wrapper, data)


def redeem(wrapper, shares, receiver, owner, sender):
    """ERC4626 redeem(uint256, address, address)"""
    data = "0xba087652" + abi_encode(
        ["uint256", "address", "address"], [shares, receiver, owner]
    ).hex()
    return send_tx(sender, wrapper, data)


def preview_deposit(wrapper, assets):
    data = "0xef8b30f7" + abi_encode(["uint256"], [assets]).hex()
    result = eth_call(wrapper, data)
    return int(result, 16) if result else 0


def preview_redeem(wrapper, shares):
    data = "0x4cdad506" + abi_encode(["uint256"], [shares]).hex()
    result = eth_call(wrapper, data)
    return int(result, 16) if result else 0


# ---------------------------------------------------------------------------
# Test runner
# ---------------------------------------------------------------------------

def log(label, detail=""):
    mark = "✓" if detail != "FAIL" else "✗"
    print(f"  {mark} {label}" + (f" — {detail}" if detail and detail != "FAIL" else ""))


def run_tests():
    print("=" * 70)
    print("PT-Strata aToken Wrapper — Integration Tests")
    print(f"Wrapper: {WRAPPER}")
    print(f"PT Token: {PT_TOKEN}")
    print("RPC: Tenderly fork (chain 9991)")
    print("=" * 70)

    passed = 0
    failed = 0

    # -----------------------------------------------------------------------
    # SETUP: Transfer PT tokens from whale to test accounts
    # -----------------------------------------------------------------------
    print("\n--- SETUP ---")
    whale_bal = balance_of(PT_TOKEN, PT_WHALE)
    print(f"  PT whale balance: {whale_bal / 10**18:,.2f}")
    assert whale_bal > HUNDRED_TOKENS, "Whale has insufficient PT"

    # Transfer to Alice
    transfer(PT_TOKEN, PT_WHALE, ALICE, HUNDRED_TOKENS)
    alice_pt = balance_of(PT_TOKEN, ALICE)
    assert alice_pt >= HUNDRED_TOKENS
    log("Alice funded", f"{alice_pt / 10**18:.2f} PT")

    # Transfer to Bob
    transfer(PT_TOKEN, PT_WHALE, BOB, TEN_TOKENS)
    bob_pt = balance_of(PT_TOKEN, BOB)
    assert bob_pt >= TEN_TOKENS
    log("Bob funded", f"{bob_pt / 10**18:.2f} PT")

    # -----------------------------------------------------------------------
    # 1. ERC4626 DEPOSIT
    # -----------------------------------------------------------------------
    print("\n--- 1. ERC4626 DEPOSIT ---")

    # 1a. Deposit without approval should revert
    receipt = deposit(WRAPPER, TEN_TOKENS, ALICE, ALICE)
    if receipt["status"] != "0x1":
        log("deposit without approval reverts")
        passed += 1
    else:
        log("deposit without approval reverts", "FAIL")
        failed += 1

    # 1b. Approve + Deposit
    approve(PT_TOKEN, ALICE, WRAPPER, HUNDRED_TOKENS)
    alice_allowance = allowance(PT_TOKEN, ALICE, WRAPPER)
    log("Alice approved wrapper", f"allowance={alice_allowance / 10**18:.0f}")

    receipt = deposit(WRAPPER, TEN_TOKENS, ALICE, ALICE)
    if receipt["status"] == "0x1":
        wrapper_bal = balance_of(WRAPPER, ALICE)
        pt_bal_after = balance_of(PT_TOKEN, ALICE)
        log("deposit 10 PT", f"wrapper shares={wrapper_bal / 10**18:.4f}, PT remaining={pt_bal_after / 10**18:.2f}")
        passed += 1
    else:
        log("deposit 10 PT", "FAIL")
        failed += 1

    # 1c. Deposit to different receiver
    receipt = deposit(WRAPPER, TEN_TOKENS, BOB, ALICE)
    if receipt["status"] == "0x1":
        bob_wrapper_bal = balance_of(WRAPPER, BOB)
        log("deposit 10 PT to Bob", f"Bob wrapper shares={bob_wrapper_bal / 10**18:.4f}")
        passed += 1
    else:
        log("deposit 10 PT to Bob", "FAIL")
        failed += 1

    # 1d. Deposit zero
    receipt = deposit(WRAPPER, 0, ALICE, ALICE)
    status = "reverted" if receipt["status"] != "0x1" else "succeeded (0 shares)"
    log(f"deposit 0 PT: {status}")
    passed += 1  # either behavior is acceptable

    # -----------------------------------------------------------------------
    # 2. ERC4626 MINT
    # -----------------------------------------------------------------------
    print("\n--- 2. ERC4626 MINT ---")

    alice_wrapper_before = balance_of(WRAPPER, ALICE)
    receipt = mint_shares(WRAPPER, 5 * ONE_TOKEN, ALICE, ALICE)
    if receipt["status"] == "0x1":
        alice_wrapper_after = balance_of(WRAPPER, ALICE)
        delta = alice_wrapper_after - alice_wrapper_before
        log("mint 5 shares", f"shares delta={delta / 10**18:.4f}")
        passed += 1
    else:
        log("mint 5 shares", "FAIL")
        failed += 1

    # -----------------------------------------------------------------------
    # 3. ERC4626 WITHDRAW
    # -----------------------------------------------------------------------
    print("\n--- 3. ERC4626 WITHDRAW ---")

    alice_pt_before = balance_of(PT_TOKEN, ALICE)
    alice_wrapper_before = balance_of(WRAPPER, ALICE)
    receipt = withdraw(WRAPPER, 5 * ONE_TOKEN, ALICE, ALICE, ALICE)
    if receipt["status"] == "0x1":
        alice_pt_after = balance_of(PT_TOKEN, ALICE)
        alice_wrapper_after = balance_of(WRAPPER, ALICE)
        log(
            "withdraw 5 PT",
            f"PT received={( alice_pt_after - alice_pt_before) / 10**18:.4f}, "
            f"shares burned={( alice_wrapper_before - alice_wrapper_after) / 10**18:.4f}",
        )
        passed += 1
    else:
        log("withdraw 5 PT", "FAIL")
        failed += 1

    # 3b. Withdraw to different receiver
    bob_pt_before = balance_of(PT_TOKEN, BOB)
    receipt = withdraw(WRAPPER, 2 * ONE_TOKEN, BOB, ALICE, ALICE)
    if receipt["status"] == "0x1":
        bob_pt_after = balance_of(PT_TOKEN, BOB)
        log("withdraw 2 PT to Bob", f"Bob PT delta={( bob_pt_after - bob_pt_before) / 10**18:.4f}")
        passed += 1
    else:
        log("withdraw 2 PT to Bob", "FAIL")
        failed += 1

    # 3c. Withdraw more than balance should revert
    huge_amount = 99999 * ONE_TOKEN
    receipt = withdraw(WRAPPER, huge_amount, ALICE, ALICE, ALICE)
    if receipt["status"] != "0x1":
        log("withdraw > balance reverts")
        passed += 1
    else:
        log("withdraw > balance reverts", "FAIL")
        failed += 1

    # -----------------------------------------------------------------------
    # 4. ERC4626 REDEEM
    # -----------------------------------------------------------------------
    print("\n--- 4. ERC4626 REDEEM ---")

    alice_wrapper_bal = balance_of(WRAPPER, ALICE)
    redeem_amount = alice_wrapper_bal // 2
    alice_pt_before = balance_of(PT_TOKEN, ALICE)

    receipt = redeem(WRAPPER, redeem_amount, ALICE, ALICE, ALICE)
    if receipt["status"] == "0x1":
        alice_pt_after = balance_of(PT_TOKEN, ALICE)
        alice_wrapper_after = balance_of(WRAPPER, ALICE)
        log(
            f"redeem {redeem_amount / 10**18:.4f} shares",
            f"PT received={( alice_pt_after - alice_pt_before) / 10**18:.4f}, "
            f"shares remaining={alice_wrapper_after / 10**18:.4f}",
        )
        passed += 1
    else:
        log(f"redeem {redeem_amount / 10**18:.4f} shares", "FAIL")
        failed += 1

    # 4b. Redeem to different receiver
    bob_pt_before = balance_of(PT_TOKEN, BOB)
    bob_wrapper_bal = balance_of(WRAPPER, BOB)
    if bob_wrapper_bal > 0:
        receipt = redeem(WRAPPER, bob_wrapper_bal // 2, BOB, BOB, BOB)
        if receipt["status"] == "0x1":
            bob_pt_after = balance_of(PT_TOKEN, BOB)
            log("Bob redeems half shares", f"PT received={( bob_pt_after - bob_pt_before) / 10**18:.4f}")
            passed += 1
        else:
            log("Bob redeems half shares", "FAIL")
            failed += 1

    # 4c. Redeem more than balance should revert
    receipt = redeem(WRAPPER, 99999 * ONE_TOKEN, ALICE, ALICE, ALICE)
    if receipt["status"] != "0x1":
        log("redeem > balance reverts")
        passed += 1
    else:
        log("redeem > balance reverts", "FAIL")
        failed += 1

    # -----------------------------------------------------------------------
    # 5. ERC20 TRANSFER of wrapper shares
    # -----------------------------------------------------------------------
    print("\n--- 5. WRAPPER SHARE TRANSFERS ---")

    alice_wrapper_bal = balance_of(WRAPPER, ALICE)
    bob_wrapper_before = balance_of(WRAPPER, BOB)
    transfer_amount = alice_wrapper_bal // 4 if alice_wrapper_bal > 0 else ONE_TOKEN

    if alice_wrapper_bal > 0:
        transfer(WRAPPER, ALICE, BOB, transfer_amount)
        bob_wrapper_after = balance_of(WRAPPER, BOB)
        log(
            f"Alice transfers {transfer_amount / 10**18:.4f} shares to Bob",
            f"Bob shares: {bob_wrapper_before / 10**18:.4f} → {bob_wrapper_after / 10**18:.4f}",
        )
        passed += 1
    else:
        log("Alice has no shares to transfer — skipped")

    # -----------------------------------------------------------------------
    # 6. ERC20 APPROVE + TRANSFERFROM
    # -----------------------------------------------------------------------
    print("\n--- 6. APPROVE + TRANSFERFROM ---")

    # Bob approves Alice to spend his wrapper shares
    bob_wrapper_bal = balance_of(WRAPPER, BOB)
    if bob_wrapper_bal > 0:
        approve(WRAPPER, BOB, ALICE, bob_wrapper_bal)
        bob_alice_allowance = allowance(WRAPPER, BOB, ALICE)
        log("Bob approves Alice", f"allowance={bob_alice_allowance / 10**18:.4f}")

        # Alice calls transferFrom (Bob → Alice)
        xfer_amount = bob_wrapper_bal // 3
        data = "0x23b872dd" + abi_encode(
            ["address", "address", "uint256"], [BOB, ALICE, xfer_amount]
        ).hex()
        receipt = send_tx(ALICE, WRAPPER, data)
        if receipt["status"] == "0x1":
            log(f"transferFrom {xfer_amount / 10**18:.4f} shares Bob→Alice")
            passed += 1
        else:
            log("transferFrom", "FAIL")
            failed += 1

        # transferFrom without approval should fail
        data = "0x23b872dd" + abi_encode(
            ["address", "address", "uint256"], [BOB, ALICE, bob_wrapper_bal]
        ).hex()
        receipt = send_tx(ALICE, WRAPPER, data)
        if receipt["status"] != "0x1":
            log("transferFrom > allowance reverts")
            passed += 1
        else:
            log("transferFrom > allowance reverts", "FAIL")
            failed += 1

    # -----------------------------------------------------------------------
    # 7. WITHDRAW/REDEEM on behalf of (via approval)
    # -----------------------------------------------------------------------
    print("\n--- 7. WITHDRAW/REDEEM ON BEHALF ---")

    # Alice deposits more to have shares
    alice_pt = balance_of(PT_TOKEN, ALICE)
    if alice_pt >= TEN_TOKENS:
        deposit(WRAPPER, TEN_TOKENS, ALICE, ALICE)

    alice_wrapper_bal = balance_of(WRAPPER, ALICE)
    if alice_wrapper_bal > ONE_TOKEN:
        # Alice approves Bob to spend her wrapper shares
        approve(WRAPPER, ALICE, BOB, alice_wrapper_bal)

        # Bob redeems Alice's shares
        bob_pt_before = balance_of(PT_TOKEN, BOB)
        receipt = redeem(WRAPPER, ONE_TOKEN, BOB, ALICE, BOB)
        if receipt["status"] == "0x1":
            bob_pt_after = balance_of(PT_TOKEN, BOB)
            log("Bob redeems Alice's shares", f"Bob PT delta={( bob_pt_after - bob_pt_before) / 10**18:.4f}")
            passed += 1
        else:
            log("Bob redeems Alice's shares", "FAIL")
            failed += 1

        # Bob withdraws on behalf of Alice
        bob_pt_before = balance_of(PT_TOKEN, BOB)
        receipt = withdraw(WRAPPER, ONE_TOKEN, BOB, ALICE, BOB)
        if receipt["status"] == "0x1":
            bob_pt_after = balance_of(PT_TOKEN, BOB)
            log("Bob withdraws on behalf of Alice", f"Bob PT delta={( bob_pt_after - bob_pt_before) / 10**18:.4f}")
            passed += 1
        else:
            log("Bob withdraws on behalf of Alice", "FAIL")
            failed += 1

    # -----------------------------------------------------------------------
    # 8. PREVIEW FUNCTIONS CONSISTENCY
    # -----------------------------------------------------------------------
    print("\n--- 8. PREVIEW FUNCTIONS ---")

    pd = preview_deposit(WRAPPER, ONE_TOKEN)
    pr = preview_redeem(WRAPPER, ONE_TOKEN)
    log(f"previewDeposit(1e18)={pd / 10**18:.6f}")
    log(f"previewRedeem(1e18)={pr / 10**18:.6f}")
    if pd > 0 and pr > 0:
        passed += 1
    else:
        failed += 1

    # -----------------------------------------------------------------------
    # 9. FULL ROUND-TRIP: deposit → transfer → redeem
    # -----------------------------------------------------------------------
    print("\n--- 9. FULL ROUND-TRIP ---")

    alice_pt_before = balance_of(PT_TOKEN, ALICE)
    if alice_pt_before >= 5 * ONE_TOKEN:
        # Deposit
        receipt = deposit(WRAPPER, 5 * ONE_TOKEN, ALICE, ALICE)
        assert receipt["status"] == "0x1", "Round-trip deposit failed"
        shares_after_deposit = balance_of(WRAPPER, ALICE)

        # Transfer half to Bob
        half_shares = shares_after_deposit // 2
        transfer(WRAPPER, ALICE, BOB, half_shares)

        # Alice redeems her remaining shares
        alice_remaining = balance_of(WRAPPER, ALICE)
        receipt = redeem(WRAPPER, alice_remaining, ALICE, ALICE, ALICE)
        if receipt["status"] == "0x1":
            log("Alice: deposit → transfer half → redeem rest")
            passed += 1
        else:
            log("Alice round-trip redeem", "FAIL")
            failed += 1

        # Bob redeems transferred shares
        bob_shares = balance_of(WRAPPER, BOB)
        if bob_shares > 0:
            receipt = redeem(WRAPPER, bob_shares, BOB, BOB, BOB)
            if receipt["status"] == "0x1":
                log("Bob: redeems transferred shares")
                passed += 1
            else:
                log("Bob redeem transferred shares", "FAIL")
                failed += 1

    # -----------------------------------------------------------------------
    # 10. TOTAL SUPPLY / TOTAL ASSETS CONSISTENCY
    # -----------------------------------------------------------------------
    print("\n--- 10. SUPPLY CONSISTENCY ---")

    total_supply_data = eth_call(WRAPPER, "0x18160ddd")
    total_assets_data = eth_call(WRAPPER, "0x01e1d114")
    ts = int(total_supply_data, 16) if total_supply_data else 0
    ta = int(total_assets_data, 16) if total_assets_data else 0
    wrapper_pt_bal = balance_of(PT_TOKEN, WRAPPER)

    log(f"totalSupply: {ts / 10**18:.6f}")
    log(f"totalAssets: {ta / 10**18:.6f}")
    log(f"PT balance of wrapper: {wrapper_pt_bal / 10**18:.6f}")

    # totalAssets should be close to or equal to PT balance held
    if ta > 0 and abs(ta - wrapper_pt_bal) <= ONE_TOKEN:
        log("totalAssets ≈ PT balance held")
        passed += 1
    elif ts == 0 and ta == 0:
        log("All shares redeemed, supply = 0")
        passed += 1
    else:
        log(f"totalAssets mismatch: {ta} vs PT bal {wrapper_pt_bal}", "FAIL")
        failed += 1

    # -----------------------------------------------------------------------
    # Summary
    # -----------------------------------------------------------------------
    print("\n" + "=" * 70)
    total = passed + failed
    print(f"RESULTS: {passed}/{total} passed, {failed} failed")
    if failed == 0:
        print("ALL TESTS PASSED")
    else:
        print(f"WARNING: {failed} tests FAILED")
    print("=" * 70)

    # Final balances
    print("\n--- Final Balances ---")
    for name, addr in [("Alice", ALICE), ("Bob", BOB), ("Wrapper", WRAPPER)]:
        pt = balance_of(PT_TOKEN, addr)
        wr = balance_of(WRAPPER, addr)
        print(f"  {name}: PT={pt / 10**18:.4f}, Wrapper={wr / 10**18:.4f}")


if __name__ == "__main__":
    run_tests()
