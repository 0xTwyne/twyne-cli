"""Tests for swap API integrations (Euler Swap API + 1inch)."""

from unittest.mock import MagicMock, patch

import httpx
import pytest


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


# --------------------------------------------------------------------------- #
# Euler Swap API tests
# --------------------------------------------------------------------------- #

FAKE_WETH = "0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2"
FAKE_USDC = "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48"
FAKE_OPERATOR = "0x36b2Bd4E17827E9dEABdB3AD520AC597972196D4"
FAKE_ORIGIN = "0xSenderAddress1234567890123456789012345678"


def _mock_euler_swap_response():
    """Build a realistic Euler Swap API response."""
    return {
        "data": {
            "swap": {
                "swapperData": "0xabc123",
                "multicallItems": [
                    {"data": "0xdeadbeef01020304"},
                    {"data": "0xcafebabe05060708"},
                ],
            },
            "amountIn": "1000000000000000000",
            "amountOut": "2500000000",
            "amountOutMin": "2475000000",
        }
    }


class TestEulerSwapApi:
    """Tests for the Euler Swap API client functions."""

    @patch("twyne_cli.swap.httpx.get")
    def test_get_swap_quote(self, mock_get):
        """Calls Euler Swap API with correct params and returns parsed response."""
        from twyne_cli.swap import get_swap_quote

        mock_response = MagicMock()
        mock_response.json.return_value = _mock_euler_swap_response()
        mock_response.raise_for_status = MagicMock()
        mock_get.return_value = mock_response

        result = get_swap_quote(
            chain_id=1,
            token_in=FAKE_WETH,
            token_out=FAKE_USDC,
            amount=1_000_000_000_000_000_000,
            receiver=FAKE_OPERATOR,
            origin=FAKE_ORIGIN,
            slippage=1.0,
            deadline=1700000000,
        )

        # Verify API was called
        mock_get.assert_called_once()
        call_kwargs = mock_get.call_args
        params = call_kwargs.kwargs.get("params") or call_kwargs[1].get("params")
        assert params["chainId"] == 1
        assert params["tokenIn"] == FAKE_WETH
        assert params["tokenOut"] == FAKE_USDC
        assert params["amount"] == "1000000000000000000"
        assert params["receiver"] == FAKE_OPERATOR
        assert params["origin"] == FAKE_ORIGIN
        assert params["slippage"] == "1.0"
        assert params["deadline"] == 1700000000

        # Verify parsed response
        assert result["amountOutMin"] == "2475000000"
        assert len(result["swap"]["multicallItems"]) == 2

    def test_extract_multicall_data(self):
        """Extracts hex bytes from multicall items."""
        from twyne_cli.swap import extract_multicall_data

        quote = _mock_euler_swap_response()["data"]
        result = extract_multicall_data(quote)

        assert len(result) == 2
        assert isinstance(result[0], bytes)
        assert isinstance(result[1], bytes)
        assert result[0] == bytes.fromhex("deadbeef01020304")
        assert result[1] == bytes.fromhex("cafebabe05060708")

    @patch("twyne_cli.swap.httpx.get")
    def test_swap_api_error(self, mock_get):
        """HTTP errors raise appropriately."""
        from twyne_cli.swap import get_swap_quote

        mock_response = MagicMock()
        mock_response.raise_for_status.side_effect = httpx.HTTPStatusError(
            "Bad Request", request=MagicMock(), response=MagicMock(status_code=400)
        )
        mock_get.return_value = mock_response

        with pytest.raises(httpx.HTTPStatusError):
            get_swap_quote(
                chain_id=1,
                token_in=FAKE_WETH,
                token_out=FAKE_USDC,
                amount=1000,
                receiver=FAKE_OPERATOR,
                origin=FAKE_ORIGIN,
            )

    @patch("twyne_cli.swap.httpx.get")
    def test_default_deadline(self, mock_get):
        """Default deadline is ~30 min from now when not specified."""
        import time

        from twyne_cli.swap import get_swap_quote

        mock_response = MagicMock()
        mock_response.json.return_value = _mock_euler_swap_response()
        mock_response.raise_for_status = MagicMock()
        mock_get.return_value = mock_response

        before = int(time.time()) + 1800

        get_swap_quote(
            chain_id=1,
            token_in=FAKE_WETH,
            token_out=FAKE_USDC,
            amount=1000,
            receiver=FAKE_OPERATOR,
            origin=FAKE_ORIGIN,
            # deadline=0 (default)
        )

        after = int(time.time()) + 1800
        params = mock_get.call_args.kwargs.get("params") or mock_get.call_args[1].get("params")
        assert before <= params["deadline"] <= after
