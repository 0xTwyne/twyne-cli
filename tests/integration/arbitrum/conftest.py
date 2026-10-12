"""Arbitrum (42161) Twyne-on-Morpho fork tests — drive the real CLI commands.

Prerequisites:
    anvil --fork-url $RPC_URL_42161 --chain-id 42161 --port 8456

Signers are random accounts created at test time and funded on the fork
(ETH via anvil_setBalance, tokens by impersonating the Morpho singleton),
so no signing material is stored in the repository. Swap-dependent tests
(zap, leverage, deleverage, close) need the runner's own ENSO_API_KEY and
are skipped without it.
"""

from __future__ import annotations

import json
import os
import re
import time

import httpx
import pytest
from click.testing import CliRunner

ANVIL_RPC_ARB = os.environ.get("ANVIL_RPC_URL_ARBITRUM", "http://localhost:8456")

# tech-notes arbitrum-launch-addresses/ (see src/twyne_cli/addresses/arbitrum.json)
MORPHO = "0x6c247b1F6182318877311737BaC0844bAa518F5e"
IV = "0x63DaC9b214906c30E5EBf77cf774e0dCB7638Bf5"
SYRUP_USDG = "0xE1B0dC5A21b10f1634fBfbE9976fBA2Ee2e1c762"
USDG = "0x004B506865409877C9fA29bfb1ebA929984B9bbC"
MARKET_ID = "0xfc2a80cb390c45e7e79ad24792123fbf5a96d4653db44e29646fa7af159dda7c"
HAS_ENSO = bool(os.environ.get("ENSO_API_KEY"))

needs_enso = pytest.mark.skipif(not HAS_ENSO, reason="needs ENSO_API_KEY (your own key)")


@pytest.fixture(autouse=True, scope="session")
def _isolated_vault_cache(tmp_path_factory):
    """Keep fork-created vaults out of the user's real ~/.config/twyne/cache."""
    import twyne_cli.cache as cache

    original = cache.CACHE_DIR
    cache.CACHE_DIR = tmp_path_factory.mktemp("twyne-cache")
    yield
    cache.CACHE_DIR = original


@pytest.fixture(autouse=True)
def anvil_clean_state():
    """No-op: override the parent mainnet-Anvil autouse fixture (as the MegaETH suite does)."""
    yield


_URL = re.compile(r"https?://[^\s'\"]+")


def redact(text: str) -> str:
    """Strip URLs: anvil echoes its upstream fork URL (with the provider key) in errors."""
    return _URL.sub("<url>", str(text))


def rpc(method: str, params: list):
    r = httpx.post(ANVIL_RPC_ARB, json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params}, timeout=60)
    r.raise_for_status()
    result = r.json()
    if "error" in result:
        message = redact(result["error"])
        result = None
        raise RuntimeError(message)
    return result["result"]


def _anvil_up() -> bool:
    try:
        return int(rpc("eth_chainId", []), 16) == 42161
    except Exception:
        return False


def pytest_collection_modifyitems(config, items):
    if _anvil_up():
        return
    skip = pytest.mark.skip(reason=f"Arbitrum anvil fork not reachable at {ANVIL_RPC_ARB}")
    for item in items:
        if "integration/arbitrum" in str(item.fspath):
            item.add_marker(skip)


def send_as(sender: str, to: str, data: str, gas: int = 1_000_000) -> dict:
    """Send a tx from an impersonated address and return the receipt (asserts success)."""
    rpc("anvil_impersonateAccount", [sender])
    rpc("anvil_setBalance", [sender, hex(10**20)])
    tx = rpc("eth_sendTransaction", [{"from": sender, "to": to, "data": data, "gas": hex(gas)}])
    receipt = None
    for _ in range(100):
        receipt = rpc("eth_getTransactionReceipt", [tx])
        if receipt:
            break
        time.sleep(0.1)
    rpc("anvil_stopImpersonatingAccount", [sender])
    assert receipt and int(receipt["status"], 16) == 1, f"tx to {to} failed"
    return receipt


def transfer_from(token: str, holder: str, to: str, amount: int) -> None:
    """Move ``amount`` of ``token`` from an impersonated holder to ``to``."""
    data = "0xa9059cbb" + to[2:].lower().rjust(64, "0") + hex(amount)[2:].rjust(64, "0")
    send_as(holder, token, data, 200_000)


def erc20_balance(token: str, owner: str) -> int:
    data = "0x70a08231" + owner[2:].lower().rjust(64, "0")
    return int(rpc("eth_call", [{"to": token, "data": data}, "latest"]), 16)


class Signer:
    """A throwaway fork account. ``pk_hex`` only ever goes to the CLI through its env."""

    def __init__(self):
        from eth_account import Account

        acct = Account.create()
        self.address = acct.address
        self.pk_hex = acct.key.hex()
        rpc("anvil_setBalance", [self.address, hex(10**20)])

    def fund(self, syrup: int = 0, usdg: int = 0) -> "Signer":
        if syrup:
            transfer_from(SYRUP_USDG, MORPHO, self.address, syrup)
        if usdg:
            transfer_from(USDG, MORPHO, self.address, usdg)
        return self


def twyne(signer: Signer | None, *args: str, expect_ok: bool = True):
    """Run ``twyne --chain arbitrum --rpc <anvil> <args>``; the signer goes in via env only."""
    from twyne_cli.cli import cli

    env = {"PRIVATE_KEY": signer.pk_hex} if signer else {}
    result = CliRunner().invoke(cli, ["--chain", "arbitrum", "--rpc", ANVIL_RPC_ARB, *args], env=env)
    if expect_ok and result.exit_code != 0:
        detail = redact(f"{result.output}\n{type(result.exception).__name__}: {result.exception}")
        result = None
        pytest.fail(detail, pytrace=False)
    return result


def twyne_json(*args: str) -> dict:
    out = twyne(None, "--json", *args).output
    return json.loads(out[out.index("{"):])


def new_vault_address(output: str) -> str:
    m = re.search(r"New vault address: (0x[0-9a-fA-F]{40})", output)
    assert m, output
    return m.group(1)


@pytest.fixture(scope="session")
def clp():
    """A credit LP that seeds the intermediate vault so borrowers can reserve credit."""
    s = Signer().fund(syrup=2_000 * 10**6)
    twyne(s, "tx", "credit", "deposit", IV, "2000", "--yes")
    return s
