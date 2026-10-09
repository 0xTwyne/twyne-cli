"""Swap API integrations for operator actions (leverage/deleverage).

Includes:
- Euler Swap API client (for deleverage operator Swapper multicall data)
- Enso route builder (Twyne Swapper Generic-handler calldata; needs the user's ENSO_API_KEY)
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
# Enso (Generic-handler routes for the Twyne Swapper)
# --------------------------------------------------------------------------- #
# Port of twyne-frontend apps/lend/src/app/api/v1/swap (enso.ts + normalizer.ts,
# vault mode). Enso delivers the output straight to ``receiver``; the Swapper
# item only forwards tokenIn to the Enso router through the Generic handler.

ENSO_API_BASE = "https://api.enso.finance"
ENSO_RATE_LIMIT_RETRIES = 3

# Enso routers we accept per chain (verified against live responses; same as
# the frontend's ENSO_ALLOWED_ROUTERS).
ENSO_ALLOWED_ROUTERS: dict[int, tuple[str, ...]] = {
    1: ("0xF75584eF6673aD213a685a1B58Cc0330B8eA22Cf",),
    42161: ("0xF75584eF6673aD213a685a1B58Cc0330B8eA22Cf",),
}

# bytes32("Generic") — EVK Swapper handler id that calls an arbitrary target.
HANDLER_GENERIC = b"Generic".ljust(32, b"\0")
_SWAP_SELECTOR = bytes.fromhex("f71679d0")  # swap((bytes32,uint256,address,address,address,address,address,address,uint256,bytes))
_SWAP_PARAMS_TYPE = "(bytes32,uint256,address,address,address,address,address,address,uint256,bytes)"
_ZERO = "0x" + "0" * 40


def fetch_enso_route(
    chain_id: int, from_address: str, receiver: str, token_in: str, token_out: str,
    amount_in: int, slippage_bps: int, api_key: str,
) -> dict:
    """Call Enso /shortcuts/route and validate the response (router allowlist, non-zero min out)."""
    params = {
        "chainId": chain_id,
        "fromAddress": from_address,
        "receiver": receiver,
        "tokenIn": token_in,
        "tokenOut": token_out,
        "amountIn": str(amount_in),
        "slippage": str(slippage_bps),
    }
    # Free Enso keys allow ~1 request/second; back off and retry on 429.
    for attempt in range(ENSO_RATE_LIMIT_RETRIES + 1):
        resp = httpx.get(
            f"{ENSO_API_BASE}/api/v1/shortcuts/route",
            params=params,
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            timeout=30,
        )
        if resp.status_code != 429 or attempt == ENSO_RATE_LIMIT_RETRIES:
            break
        time.sleep(1.2 * (attempt + 1))
    if resp.status_code != 200:
        # The body never contains the key; the request headers are not echoed.
        raise RuntimeError(f"Enso API error {resp.status_code}: {resp.text[:300]}")
    data = resp.json()
    if str(data.get("minAmountOut")) in ("", "0", "None"):
        raise RuntimeError("Enso returned zero minAmountOut — slippage protection would be disabled")
    router = str(data["tx"]["to"]).lower()
    allowed = [r.lower() for r in ENSO_ALLOWED_ROUTERS.get(chain_id, ())]
    if router not in allowed:
        raise RuntimeError(f"Enso returned untrusted router {data['tx']['to']} for chain {chain_id}")
    return data


def encode_generic_swap(
    token_in: str, token_out: str, receiver: str, origin: str, target: str, payload: bytes,
) -> bytes:
    """Encode Swapper.swap(SwapParams) with the Generic handler calling ``target`` with ``payload``."""
    from eth_abi import encode
    from eth_utils import to_checksum_address

    generic = encode(["address", "bytes"], [to_checksum_address(target), payload])
    params = (
        HANDLER_GENERIC,
        0,  # MODE_EXACT_IN
        to_checksum_address(origin),
        to_checksum_address(token_in),
        to_checksum_address(token_out),
        _ZERO,  # vaultIn
        _ZERO,  # accountIn
        to_checksum_address(receiver),
        0,  # amountOut: unused in EXACT_IN; the operator / AssetZap enforces the minimum
        generic,
    )
    return _SWAP_SELECTOR + encode([_SWAP_PARAMS_TYPE], [params])


def enso_swap(
    chain_id: int, swapper: str, token_in: str, token_out: str, amount: int,
    receiver: str, origin: str, slippage: float, api_key: str,
) -> dict:
    """Build Swapper multicall items for an Enso route that pays ``receiver``.

    Returns ``{"swap_data": [bytes], "amount_out": int, "amount_out_min": int,
    "price_impact_bps": float | None, "provider": "enso"}``. ``swap_data`` is the
    ``bytes[]`` that the operators and AssetZap pass to ``Swapper.multicall``.
    """
    slippage_bps = int(round(slippage * 100))
    route = fetch_enso_route(chain_id, swapper, receiver, token_in, token_out, amount, slippage_bps, api_key)
    payload = bytes.fromhex(route["tx"]["data"][2:])
    item = encode_generic_swap(token_in, token_out, receiver, origin, route["tx"]["to"], payload)
    impact = route.get("priceImpact")
    return {
        "swap_data": [item],
        "amount_out": int(route["amountOut"]),
        "amount_out_min": int(route["minAmountOut"]),
        "price_impact_bps": float(impact) if impact is not None else None,
        "provider": "enso",
    }


def build_swap(
    token_in: str, token_out: str, amount: int, receiver: str, origin: str, slippage: float,
) -> dict:
    """Swap legs for the active chain's swap provider (see ChainSpec.swap_provider).

    Only Enso chains use this entry point today; mainnet operator commands keep
    their Euler Swap API calls unchanged.
    """
    from .chains import active_chain
    from .contracts import swapper_address
    from .secrets import require_enso_key

    chain = active_chain()
    if chain.swap_provider != "enso":
        raise RuntimeError(f"build_swap supports Enso chains only; {chain.name} uses {chain.swap_provider}")
    return enso_swap(
        chain.chain_id, swapper_address(), token_in, token_out, amount, receiver, origin, slippage,
        require_enso_key(),
    )


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
