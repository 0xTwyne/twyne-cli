"""Swap API integrations for operator actions (leverage/deleverage).

Includes:
- Euler Swap API client (for deleverage operator Swapper multicall data)
- 1inch Swap API client (legacy, for direct swaps)
"""

import time

import httpx

from .constants import ONEINCH_API_BASE, ONEINCH_CHAIN_ID

# --------------------------------------------------------------------------- #
# Euler Swap API (for deleverage operator)
# --------------------------------------------------------------------------- #

EULER_SWAP_API_URL = "https://swap.euler.finance"


def get_swap_quote(
    chain_id: int,
    token_in: str,
    token_out: str,
    amount: int,
    receiver: str,
    origin: str,
    slippage: float = 1.0,
    deadline: int = 0,
    mode: int = 0,  # 0=EXACT_IN
    account_in: str = "0x" + "0" * 40,
    account_out: str = "0x" + "0" * 40,
    vault_in: str = "0x" + "0" * 40,
) -> dict:
    """Call Euler Swap API and return swap quote with pre-built Swapper calldata.

    Args:
        chain_id: Chain ID (1 for mainnet).
        token_in: Address of token to sell (e.g. WETH).
        token_out: Address of token to buy (e.g. USDC).
        amount: Amount of token_in in raw units.
        receiver: Address that receives the swapped tokens (deleverage operator).
        origin: EOA that initiated the transaction.
        slippage: Slippage tolerance in percent (default 1.0 = 1%).
        deadline: Unix timestamp deadline (default: 30 min from now).
        mode: Swapper mode — 0=EXACT_IN, 1=EXACT_OUT, 2=TARGET_DEBT.
        account_in: Sub-account for input (default: zero address).
        account_out: Sub-account for output (default: zero address).
        vault_in: Vault for input (default: zero address).

    Returns:
        Parsed swap data dict from the API response.
    """
    if deadline == 0:
        deadline = int(time.time()) + 1800  # 30 min default

    params = {
        "chainId": chain_id,
        "tokenIn": token_in,
        "tokenOut": token_out,
        "amount": str(amount),
        "receiver": receiver,
        "origin": origin,
        "accountIn": account_in,
        "accountOut": account_out,
        "vaultIn": vault_in,
        "slippage": str(slippage),
        "swapperMode": mode,
        "deadline": deadline,
        "isRepay": False,
        "targetDebt": "0",
        "currentDebt": "0",
    }

    resp = httpx.get(f"{EULER_SWAP_API_URL}/swap", params=params, timeout=30)
    resp.raise_for_status()
    return resp.json()["data"]


def extract_multicall_data(quote: dict) -> list[bytes]:
    """Extract the swapData[] bytes array from a swap quote response.

    The Euler Swap API returns multicallItems where each item has a 'data' field
    containing hex-encoded Swapper function calldata. These are passed directly
    to DeleverageOperator.executeDeleverage() as the swapData[] parameter.
    """
    return [bytes.fromhex(item["data"][2:]) for item in quote["swap"]["multicallItems"]]


# --------------------------------------------------------------------------- #
# 1inch Swap API (legacy)
# --------------------------------------------------------------------------- #


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
