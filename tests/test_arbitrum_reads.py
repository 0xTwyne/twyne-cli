"""Unit tests for Arbitrum chain support and Twyne-on-Morpho reads (no RPC)."""

from __future__ import annotations

import json
from unittest.mock import MagicMock

import click
import pytest

from twyne_cli.chains import CHAINS, resolve_chain, set_active_chain

ARB = CHAINS[42161]
MAINNET = CHAINS[1]


@pytest.fixture()
def arbitrum():
    set_active_chain(ARB)
    yield ARB
    set_active_chain(MAINNET)


# --------------------------------------------------------------------------- #
# Chain spec + registry
# --------------------------------------------------------------------------- #


def test_arbitrum_chain_spec():
    assert resolve_chain("arbitrum") is ARB
    assert resolve_chain("42161") is ARB
    assert ARB.supports_morpho and not ARB.supports_euler and not ARB.supports_aave
    assert ARB.supports_operators
    assert not ARB.legacy_contracts
    assert ARB.swap_provider == "enso"
    assert MAINNET.swap_provider == "euler" and not MAINNET.supports_morpho
    assert CHAINS[4326].legacy_contracts


def test_uses_pair_risk_follows_contract_generation(arbitrum):
    from twyne_cli.contracts import uses_pair_risk

    assert uses_pair_risk()
    set_active_chain(CHAINS[4326])
    assert not uses_pair_risk()


def test_arbitrum_registry_shape(arbitrum):
    from eth_utils import is_checksum_address

    from twyne_cli.contracts import _load_addresses, get_address

    reg = _load_addresses(ARB)
    assert reg["chainId"] == 42161
    for key in ("evc", "vaultManager", "collateralVaultFactory", "healthStatViewer", "morpho", "swapper", "assetZap"):
        assert is_checksum_address(get_address(key)), key
    for key in ("morphoLeverageOperator", "morphoDeleverageOperator", "morphoTeleportOperator"):
        assert is_checksum_address(get_address(f"operators.{key}")), key
    market = reg["morphoMarkets"]["syrupUSDG_USDG"]
    assert market["intermediateVault"] == reg["intermediateVaults"]["morpho_syrupUSDG"]
    assert int(market["lltv"]) == 915 * 10**15
    # The Twyne operators and AssetZap call this Swapper (their SWAPPER()); not Euler's.
    assert get_address("swapper") == "0x6eE488A00A2ef1E2764cD7245F8a77C40060A7C7"


def test_mainnet_swapper_is_the_twyne_swapper():
    from twyne_cli.contracts import _load_addresses

    assert _load_addresses(MAINNET)["swapper"] == "0x2Bba09866b6F1025258542478C39720A09B728bF"



# --------------------------------------------------------------------------- #
# Protocol detection + markets
# --------------------------------------------------------------------------- #


def test_detect_protocol_morpho_by_singleton(arbitrum):
    from twyne_cli.morpho import detect_protocol

    cv = MagicMock()
    cv.targetVault.return_value = "0x6c247b1F6182318877311737BaC0844bAa518F5e"
    assert detect_protocol(cv) == "morpho"


def test_detect_protocol_aave_and_euler_on_mainnet():
    from twyne_cli.morpho import detect_protocol

    set_active_chain(MAINNET)
    aave = MagicMock()
    aave.aToken.return_value = "0x0B925eD163218f6662a35e0f0371Ac234f9E9371"
    assert detect_protocol(aave) == "aave"
    from ape.exceptions import ContractLogicError

    euler = MagicMock()
    euler.aToken.side_effect = ContractLogicError("no aToken")
    assert detect_protocol(euler) == "euler"
    flaky = MagicMock()
    flaky.aToken.side_effect = ConnectionError("rpc down")
    with pytest.raises(ConnectionError):
        detect_protocol(flaky)


def test_market_params_tuple_and_lltv_bps(arbitrum):
    from twyne_cli.morpho import registered_markets

    (m,) = registered_markets()
    assert m.params == (m.loan_token, m.collateral_token, m.oracle, m.irm, m.lltv)
    assert m.lltv_bps == 9150


def test_resolve_market_rejects_unknown_id(arbitrum, monkeypatch):
    import twyne_cli.morpho as morpho

    (m,) = morpho.registered_markets()
    monkeypatch.setattr(morpho, "allowed_markets", lambda iv, block=None, vm=None: [m])
    assert morpho.resolve_market(m.intermediate_vault).id == m.id
    with pytest.raises(click.UsageError, match="not an allowed market"):
        morpho.resolve_market(m.intermediate_vault, "0x" + "11" * 32)



# --------------------------------------------------------------------------- #
# Units — Morpho values are loan-token, never "$"
# --------------------------------------------------------------------------- #


def test_format_value_labels_units():
    from twyne_cli.formatting import format_value, hf_or_none

    assert format_value(1234.5, "USD") == "$1,234.50"
    assert format_value(1234.5, "USDG") == "1,234.50 USDG"
    assert "$" not in format_value(1234.5, "USDG")
    assert hf_or_none(2**256 - 1) is None
    assert hf_or_none(15 * 10**17) == 1.5



# --------------------------------------------------------------------------- #
# Vault cache: Ape's range() stop is exclusive
# --------------------------------------------------------------------------- #


def test_cache_scan_includes_the_head_block(monkeypatch, tmp_path):
    import sys
    import types

    import twyne_cli.cache as cache

    monkeypatch.setattr(cache, "CACHE_DIR", tmp_path)
    calls = []

    class Event:
        def range(self, start, stop):
            calls.append((start, stop))
            return []

    fake_contract = MagicMock()
    fake_contract.T_CollateralVaultCreated = Event()
    fake_ape = types.SimpleNamespace(Contract=lambda *a, **k: fake_contract,
                                     chain=types.SimpleNamespace(blocks=types.SimpleNamespace(height=100)))
    monkeypatch.setitem(sys.modules, "ape", fake_ape)
    set_active_chain(MAINNET)
    c = cache.VaultCache(chain_id=1, start_block=50)
    c.last_scanned_block = 90
    c.update(to_block=100)
    assert calls == [(91, 101)]
    assert c.last_scanned_block == 100
    json.dumps([v.to_dict() for v in c.vaults])
