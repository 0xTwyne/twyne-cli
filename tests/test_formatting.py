"""Tests for formatting utilities."""

from twyne_cli.formatting import (
    format_address,
    format_bps,
    format_hf,
    format_usd,
    risk_level,
)


def test_format_hf_normal():
    # 1.5 health factor (1.5 * 1e18)
    assert format_hf(1_500_000_000_000_000_000) == "1.5000"


def test_format_hf_infinity():
    assert format_hf(2**256 - 1) == "inf (no debt)"


def test_format_hf_large():
    # 200.0 health factor
    assert format_hf(200 * 10**18) == "200"


def test_format_usd():
    assert format_usd(1234.56) == "$1,234.56"
    assert format_usd(0.001) == "$0.00"
    assert format_usd(1_000_000.0) == "$1,000,000.00"


def test_format_bps():
    assert format_bps(9000) == "90.00%"
    assert format_bps(8500) == "85.00%"
    assert format_bps(10000) == "100.00%"


def test_format_address():
    addr = "0xa1517cCe0bE75700A8838EA1cEE0dc383cd3A332"
    assert format_address(addr) == "0xa151...A332"


def test_risk_level():
    assert risk_level(2**256 - 1) == "SAFE"
    assert risk_level(3 * 10**18) == "SAFE"
    assert risk_level(int(1.6 * 10**18)) == "LOW"
    assert risk_level(int(1.3 * 10**18)) == "MEDIUM"
    assert risk_level(int(1.1 * 10**18)) == "HIGH"
    assert risk_level(int(0.9 * 10**18)) == "LIQUIDATABLE"
