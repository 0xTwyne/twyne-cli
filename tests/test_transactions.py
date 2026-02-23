"""Tests for transaction helper functions."""

from unittest.mock import MagicMock


def test_parse_amount_human_readable():
    """Parse '1.5' with 18 decimals -> 1500000000000000000."""
    from twyne_cli.transactions import parse_amount
    assert parse_amount("1.5", 18) == 1_500_000_000_000_000_000


def test_parse_amount_six_decimals():
    """Parse '100.5' with 6 decimals (USDC) -> 100500000."""
    from twyne_cli.transactions import parse_amount
    assert parse_amount("100.5", 6) == 100_500_000


def test_parse_amount_raw_wei():
    """Parse raw integer string as-is when flagged."""
    from twyne_cli.transactions import parse_amount
    assert parse_amount("1000000000000000000", 18, raw=True) == 1_000_000_000_000_000_000


def test_parse_amount_max():
    """Parse 'max' as uint256 max."""
    from twyne_cli.transactions import parse_amount
    assert parse_amount("max", 18) == 2**256 - 1


def test_parse_amount_whole_number():
    """Parse whole number without decimal point."""
    from twyne_cli.transactions import parse_amount
    assert parse_amount("100", 18) == 100_000_000_000_000_000_000


def test_parse_amount_zero():
    """Parse '0' correctly."""
    from twyne_cli.transactions import parse_amount
    assert parse_amount("0", 18) == 0


def test_format_receipt():
    """Format a mock transaction receipt for display."""
    from twyne_cli.transactions import format_receipt
    mock_receipt = MagicMock()
    mock_receipt.txn_hash = "0xabc123"
    mock_receipt.gas_used = 150_000
    mock_receipt.status = 1
    mock_receipt.block_number = 12345678
    result = format_receipt(mock_receipt)
    assert "0xabc123" in result
    assert "150,000" in result or "150000" in result
    assert "Success" in result or "success" in result


def test_format_receipt_failed():
    """Format a failed transaction receipt."""
    from twyne_cli.transactions import format_receipt
    mock_receipt = MagicMock()
    mock_receipt.txn_hash = "0xdef456"
    mock_receipt.gas_used = 50_000
    mock_receipt.status = 0
    mock_receipt.block_number = 12345679
    result = format_receipt(mock_receipt)
    assert "FAILED" in result
