"""Tests for 1inch swap integration."""

import pytest
from unittest.mock import MagicMock, patch


def test_swap_client_init():
    """SwapClient initializes with API key."""
    from twyne_cli.swap import SwapClient
    client = SwapClient(api_key="test-key", chain_id=1)
    assert client.api_key == "test-key"
    assert client.chain_id == 1


def test_swap_client_missing_key():
    """SwapClient raises without API key."""
    from twyne_cli.swap import SwapClient
    with pytest.raises(ValueError, match="API key"):
        SwapClient(api_key=None, chain_id=1)


def test_swap_client_empty_key():
    """SwapClient raises with empty API key."""
    from twyne_cli.swap import SwapClient
    with pytest.raises(ValueError, match="API key"):
        SwapClient(api_key="", chain_id=1)


@patch("twyne_cli.swap.httpx.Client")
def test_get_swap_quote(mock_httpx_cls):
    """get_swap_quote calls 1inch API and returns calldata."""
    from twyne_cli.swap import SwapClient

    mock_client = MagicMock()
    mock_httpx_cls.return_value.__enter__ = MagicMock(return_value=mock_client)
    mock_httpx_cls.return_value.__exit__ = MagicMock(return_value=False)

    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "tx": {
            "to": "0x1111111254EEB25477B68fb85Ed929f73A960582",
            "data": "0xabcdef",
            "value": "0",
            "gas": 300000,
        },
        "dstAmount": "1500000000000000000",
    }
    mock_client.get.return_value = mock_response

    client = SwapClient(api_key="test-key", chain_id=1)
    result = client.get_swap_quote(
        src_token="0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2",
        dst_token="0x7f39C581F595B53c5cb19bD0b3f8dA6c935E2Ca0",
        amount=1_000_000_000_000_000_000,
        from_address="0xSender",
        slippage=0.5,
    )
    assert result is not None
    assert "tx" in result
    assert result["dstAmount"] == "1500000000000000000"


@patch("twyne_cli.swap.httpx.Client")
def test_get_swap_quote_failure(mock_httpx_cls):
    """get_swap_quote returns None on API failure."""
    from twyne_cli.swap import SwapClient

    mock_client = MagicMock()
    mock_httpx_cls.return_value.__enter__ = MagicMock(return_value=mock_client)
    mock_httpx_cls.return_value.__exit__ = MagicMock(return_value=False)

    mock_response = MagicMock()
    mock_response.status_code = 429  # rate limited
    mock_client.get.return_value = mock_response

    client = SwapClient(api_key="test-key", chain_id=1)
    result = client.get_swap_quote(
        src_token="0xA",
        dst_token="0xB",
        amount=1000,
        from_address="0xSender",
    )
    assert result is None
