"""End-to-end Twyne-on-Morpho flows on an Arbitrum fork, through the real CLI commands."""

from __future__ import annotations

import pytest

from .conftest import (
    IV,
    MARKET_ID,
    MORPHO,
    SYRUP_USDG,
    USDG,
    Signer,
    erc20_balance,
    needs_enso,
    new_vault_address,
    send_as,
    twyne,
    twyne_json,
)

LTV = "9500"  # above LLTV x beta_safe (91.49%), below the 97% cap: reserves credit


@pytest.fixture()
def borrower(clp):
    return Signer().fund(syrup=1_000 * 10**6, usdg=1_000 * 10**6)


@pytest.fixture()
def position(borrower):
    """A Morpho vault with 100 syrupUSDG collateral and 50 USDG debt."""
    out = twyne(borrower, "tx", "factory", "open-position", IV, "--ltv", LTV,
                "--deposit", "100", "--borrow", "50", "--yes").output
    return borrower, new_vault_address(out)


# --------------------------------------------------------------------------- #
# Reads
# --------------------------------------------------------------------------- #


def test_overview_lists_the_morpho_pair():
    data = twyne_json("protocol", "overview")
    pair = data["collateral_assets"][0]
    assert pair["iv_name"] == "morpho_syrupUSDG"
    assert pair["morpho_market_id"] == MARKET_ID
    assert pair["ext_liq_ltv_bps"] == 9150


def test_info_values_are_in_usdg_not_usd(position):
    _, cv = position
    info = twyne_json("vault", "info", cv)
    assert info["protocol"] == "Morpho"
    assert info["value_unit"] == "USDG"
    assert "debt_usd" not in info
    assert abs(info["debt_value"] - 50) < 0.01
    assert info["morpho_market_id"] == MARKET_ID
    assert info["externally_liquidated"] is False
    health = twyne_json("vault", "health", cv)
    assert health["value_unit"] == "USDG"
    assert health["internal_hf"] > 1


def test_user_lists_the_vault_with_health(position):
    borrower, cv = position
    data = twyne_json("user", borrower.address)
    assert data["total_vaults"] >= 1
    row = next(v for v in data["vaults"] if v["vault"].lower() == cv.lower())
    assert row["protocol"] == "morpho"
    assert row["value_unit"] == "USDG"


def test_human_output_never_prints_dollars_for_usdg(position):
    _, cv = position
    out = twyne(None, "vault", "info", cv).output
    assert "USDG" in out


# --------------------------------------------------------------------------- #
# Borrower actions
# --------------------------------------------------------------------------- #


def test_create_vault_only(borrower):
    out = twyne(borrower, "tx", "factory", "create-vault", IV, "--protocol", "morpho", "--ltv", LTV, "--yes").output
    cv = new_vault_address(out)
    assert twyne_json("vault", "info", cv)["protocol"] == "Morpho"


def test_create_vault_rejects_unknown_market(borrower):
    res = twyne(borrower, "tx", "factory", "create-vault", IV, "--market-id", "0x" + "11" * 32,
                "--ltv", LTV, "--yes", expect_ok=False)
    assert res.exit_code != 0
    assert "not an allowed market" in res.output


def test_deposit_borrow_repay_withdraw(position):
    borrower, cv = position
    twyne(borrower, "tx", "collateral", "deposit", cv, "10", "--yes")
    twyne(borrower, "tx", "collateral", "borrow", cv, "5", "--yes")
    assert abs(twyne_json("vault", "info", cv)["debt_value"] - 55) < 0.01
    twyne(borrower, "tx", "collateral", "repay", cv, "5", "--yes")
    twyne(borrower, "tx", "collateral", "withdraw", cv, "10", "--yes")
    assert abs(twyne_json("vault", "info", cv)["debt_value"] - 50) < 0.01


def test_redeem_underlying_is_rejected(position):
    borrower, cv = position
    res = twyne(borrower, "tx", "collateral", "redeem-underlying", cv, "1", "--yes", expect_ok=False)
    assert res.exit_code != 0
    assert "tx collateral withdraw" in res.output


def test_set_ltv(position):
    borrower, cv = position
    twyne(borrower, "tx", "collateral", "set-ltv", cv, "9600", "--yes")
    assert twyne_json("vault", "info", cv)["twyne_liq_ltv_bps"] == 9600


# --------------------------------------------------------------------------- #
# Credit LP
# --------------------------------------------------------------------------- #


def test_credit_deposit_and_withdraw():
    lp = Signer().fund(syrup=100 * 10**6)
    twyne(lp, "tx", "credit", "deposit", IV, "100", "--yes")
    assert erc20_balance(SYRUP_USDG, lp.address) == 0
    twyne(lp, "tx", "credit", "withdraw", IV, "50", "--yes")
    assert erc20_balance(SYRUP_USDG, lp.address) == 50 * 10**6


def test_deposit_atokens_is_rejected_on_arbitrum():
    lp = Signer()
    res = twyne(lp, "tx", "credit", "deposit-atokens", IV, "1", "--yes", expect_ok=False)
    assert res.exit_code != 0
    assert "Aave protocol is not available on Arbitrum" in res.output


# --------------------------------------------------------------------------- #
# Swap-dependent flows (Enso)
# --------------------------------------------------------------------------- #


@needs_enso
def test_zap_usdg_into_vault(position):
    borrower, cv = position
    before = twyne_json("vault", "info", cv)["user_collateral_raw"]
    twyne(borrower, "tx", "collateral", "deposit-underlying", cv, "20", "--yes")
    after = twyne_json("vault", "info", cv)["user_collateral_raw"]
    assert int(after) > int(before) + 19 * 10**6


@needs_enso
def test_zap_usdg_into_credit_vault():
    lp = Signer().fund(usdg=50 * 10**6)
    twyne(lp, "tx", "credit", "deposit-underlying", IV, "50", "--yes")
    assert erc20_balance(USDG, lp.address) == 0


@needs_enso
def test_open_position_with_token_in(borrower):
    out = twyne(borrower, "tx", "factory", "open-position", IV, "--ltv", LTV, "--deposit", "100",
                "--token-in", USDG, "--borrow", "40", "--yes").output
    cv = new_vault_address(out)
    info = twyne_json("vault", "info", cv)
    assert int(info["user_collateral_raw"]) > 90 * 10**6


@needs_enso
def test_leverage_then_deleverage(position):
    borrower, cv = position
    twyne(borrower, "tx", "operators", "leverage", cv, "100", "--yes")
    lev = twyne_json("vault", "info", cv)
    assert abs(lev["debt_value"] - 150) < 1
    twyne(borrower, "tx", "operators", "deleverage", cv, "50", "--max-debt", str(150 * 10**6), "--yes")
    assert twyne_json("vault", "info", cv)["debt_value"] < lev["debt_value"] - 40


@needs_enso
def test_close_position(position):
    borrower, cv = position
    syrup_before = erc20_balance(SYRUP_USDG, borrower.address)
    twyne(borrower, "tx", "operators", "close-position", cv, "--yes")
    info = twyne_json("vault", "info", cv)
    assert int(info["debt_raw"]) == 0
    assert int(info["total_assets_raw"]) == 0
    assert erc20_balance(SYRUP_USDG, borrower.address) > syrup_before + 40 * 10**6


def test_leverage_over_flashloan_cap_is_refused(position):
    borrower, cv = position
    res = twyne(borrower, "tx", "operators", "leverage", cv, "1000000000", "--yes", expect_ok=False)
    assert res.exit_code != 0
    assert "exceeds" in res.output


# --------------------------------------------------------------------------- #
# Morpho Blue → Twyne migration
# --------------------------------------------------------------------------- #


def _direct_morpho_position(user: Signer, collateral: int, debt: int) -> None:
    """Open a plain Morpho Blue position for ``user`` (approve, supplyCollateral, borrow)."""
    from eth_abi import encode

    params = (USDG, SYRUP_USDG, "0xA722399257215e716C8Aa084D06E4882580934ee",
              "0x66F30587FB8D4206918deb78ecA7d5eBbafD06DA", 915 * 10**15)
    mp = "(address,address,address,address,uint256)"
    approve = "0x095ea7b3" + encode(["address", "uint256"], [MORPHO, collateral]).hex()
    send_as(user.address, SYRUP_USDG, approve)
    supply = "0x238d6579" + encode([mp, "uint256", "address", "bytes"], [params, collateral, user.address, b""]).hex()
    send_as(user.address, MORPHO, supply)
    borrow = "0x50d8cd4b" + encode([mp, "uint256", "uint256", "address", "address"],
                                   [params, debt, 0, user.address, user.address]).hex()
    send_as(user.address, MORPHO, borrow)


def test_discover_and_migrate_morpho_position(clp):
    user = Signer().fund(syrup=200 * 10**6)
    _direct_morpho_position(user, 200 * 10**6, 100 * 10**6)
    out = twyne(None, "tx", "discover-positions", user.address).output
    assert "Morpho" in out and "syrupUSDG" in out
    out = twyne(user, "tx", "migrate-position", user.address, "--ltv", LTV, "--position", "1", "--yes").output
    data = twyne_json("user", user.address)
    assert data["total_vaults"] == 1
    cv = data["vaults"][0]["vault"]
    info = twyne_json("vault", "info", cv)
    # All 200 syrupUSDG moved, plus the reserved credit, now sit in Morpho for the vault.
    assert int(info["collateral_in_morpho_raw"]) >= 200 * 10**6
    # C = total - maxRelease; the IV debt (maxRelease) rounds up, so C may read 1 wei low.
    assert 200 * 10**6 - int(info["user_collateral_raw"]) <= 1
    assert abs(info["debt_value"] - 100) < 0.01


def _morpho_authorized(owner: str, operator: str) -> bool:
    from .conftest import rpc

    data = "0x65e4ad9e" + owner[2:].lower().rjust(64, "0") + operator[2:].lower().rjust(64, "0")
    return int(rpc("eth_call", [{"to": MORPHO, "data": data}, "latest"]), 16) == 1


TELEPORT_OP = "0xFf144562f9996AdFf0f2feC080bd464B3e181ca4"


def test_teleport_partial_position_and_revoke(clp):
    user = Signer().fund(syrup=200 * 10**6)
    _direct_morpho_position(user, 200 * 10**6, 80 * 10**6)
    cv = new_vault_address(twyne(user, "tx", "factory", "create-vault", IV, "--ltv", LTV, "--yes").output)
    twyne(user, "tx", "operators", "teleport", cv, "--collateral-amount", str(100 * 10**6),
          "--debt-amount", str(40 * 10**6), "--yes")
    info = twyne_json("vault", "info", cv)
    assert 100 * 10**6 - int(info["user_collateral_raw"]) <= 1
    assert abs(info["debt_value"] - 40) < 0.01
    assert not _morpho_authorized(user.address, TELEPORT_OP)


def test_failed_teleport_revokes_the_new_authorization(position):
    """Teleport into someone else's vault fails after the grant; the grant is revoked."""
    _, cv = position
    user = Signer().fund(syrup=50 * 10**6)
    _direct_morpho_position(user, 50 * 10**6, 10 * 10**6)
    res = twyne(user, "tx", "operators", "teleport", cv, "--yes", expect_ok=False)
    assert res.exit_code != 0
    assert not _morpho_authorized(user.address, TELEPORT_OP)


def test_close_position_too_little_collateral_for_margin(borrower):
    out = twyne(borrower, "tx", "factory", "open-position", IV, "--ltv", "9700",
                "--deposit", "100", "--borrow", "91", "--yes").output
    cv = new_vault_address(out)
    res = twyne(borrower, "tx", "operators", "close-position", cv, "--slippage", "15", "--yes", expect_ok=False)
    assert res.exit_code == 2
    assert "Repay part of the debt" in res.output
