"""1inch swap API integration for operator actions (leverage/deleverage)."""

import time

import httpx

from .constants import ONEINCH_API_BASE, ONEINCH_CHAIN_ID


class SwapClient:
    """Client for 1inch Swap API v6."""

    def __init__(self, api_key: str | None, chain_id: int = ONEINCH_CHAIN_ID):
        if not api_key:
            raise ValueError("1inch API key required. Set ONEINCH_API_KEY env var or pass --api-key.")
        self.api_key = api_key
        self.chain_id = chain_id
        self.base_url = f"{ONEINCH_API_BASE}/{chain_id}"
        self.headers = {
            "Accept": "application/json",
            "Authorization": f"Bearer {api_key}",
        }
        self._last_request_time = 0.0

    def _rate_limit(self):
        """Enforce 1 request per second rate limit."""
        elapsed = time.time() - self._last_request_time
        if elapsed < 1.1:
            time.sleep(1.1 - elapsed)
        self._last_request_time = time.time()

    def get_swap_quote(
        self,
        src_token: str,
        dst_token: str,
        amount: int,
        from_address: str,
        slippage: float = 0.5,
        disable_estimate: bool = True,
    ) -> dict | None:
        """Get swap quote with calldata from 1inch API.

        Returns:
            API response dict with 'tx' and 'dstAmount', or None on failure.
        """
        self._rate_limit()

        params = {
            "src": src_token,
            "dst": dst_token,
            "amount": str(amount),
            "from": from_address,
            "slippage": str(slippage),
            "disableEstimate": str(disable_estimate).lower(),
        }

        with httpx.Client(timeout=30) as client:
            response = client.get(
                f"{self.base_url}/swap",
                headers=self.headers,
                params=params,
            )

        if response.status_code != 200:
            return None

        return response.json()

    def get_quote_only(
        self,
        src_token: str,
        dst_token: str,
        amount: int,
    ) -> dict | None:
        """Get quote without calldata (cheaper, no from_address needed)."""
        self._rate_limit()

        params = {
            "src": src_token,
            "dst": dst_token,
            "amount": str(amount),
        }

        with httpx.Client(timeout=30) as client:
            response = client.get(
                f"{self.base_url}/quote",
                headers=self.headers,
                params=params,
            )

        if response.status_code != 200:
            return None

        return response.json()
