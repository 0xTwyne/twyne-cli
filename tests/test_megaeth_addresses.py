"""Verify MegaETH addresses resolve correctly through contracts.py.

No RPC required — purely tests that addresses/megaeth.json + the chain registry
+ the contracts loader give us back the on-chain pinned addresses.
"""

from __future__ import annotations

import pytest

from twyne_cli.chains import CHAINS, set_active_chain
from twyne_cli.contracts import _ADDRESSES, _load_addresses, get_address

# Pinned MegaETH addresses (from
# repos/tech-notes/megaeth-launch-addresses/MegaethDeployment_Apr30_2026/).
VAULT_MANAGER = "0x91BB674Fcc7CA44ecF97d8330738f8c806318017"
COLLATERAL_VAULT_FACTORY = "0x65E83e6F11c28c2bAEe42c01Be57575d8dfF0037"
EVC = "0xFF06F28cf0c44Cf1E8F03E6835bB2F3a2a752C5C"
PROTOCOL_CONFIG = "0x84101fdEC409E6446263c4738c3AE11166EAa392"
GENERIC_FACTORY = "0x577D42B0e234a64925Ed0C73486959C95D240405"
ORACLE_ROUTER = "0x6a93bAFC66D05E8f3c13060c752976D8b2a49972"
AAVE_POOL = "0x7e324AbC5De01d112AfC03a584966ff199741C28"
AAVE_V3_WRAPPER = "0x95FcAe04fCD438A82D3E7c2c9c17F5462771baB6"
AAVE_INTERMEDIATE_VAULT = "0xcA883E66FC22792461E039d92350BeC04228f9F8"
ATOKEN_WRAPPER = "0x8db3a8Be0584E97A8634dBEb0110dE55fe504141"


@pytest.fixture(autouse=True)
def _activate_megaeth():
    """Switch to MegaETH for the test, restore mainnet after."""
    _ADDRESSES.clear()
    set_active_chain(CHAINS[4326])
    try:
        yield
    finally:
        set_active_chain(CHAINS[1])
        _ADDRESSES.clear()


def test_addresses_file_loads():
    addrs = _load_addresses()
    assert addrs["chainId"] == 4326


@pytest.mark.parametrize(
    "key,expected",
    [
        ("vaultManager", VAULT_MANAGER),
        ("collateralVaultFactory", COLLATERAL_VAULT_FACTORY),
        ("evc", EVC),
        ("protocolConfig", PROTOCOL_CONFIG),
        ("GenericFactory", GENERIC_FACTORY),
        ("oracleRouter", ORACLE_ROUTER),
        ("aavePool", AAVE_POOL),
        ("aaveV3Wrapper", AAVE_V3_WRAPPER),
        ("aWETHWrapper", ATOKEN_WRAPPER),
        ("intermediateVaults.aave_aWETH", AAVE_INTERMEDIATE_VAULT),
    ],
)
def test_get_address_resolves_megaeth_keys(key, expected):
    assert get_address(key).lower() == expected.lower()


def test_operator_keys_are_empty_on_megaeth():
    """Operators object must be empty — wire-level signal that supports_operators=False."""
    addrs = _load_addresses()
    assert addrs["operators"] == {}
    assert get_address("eulerLeverageOperator") == ""
    assert get_address("aaveV3LeverageOperator") == ""
    assert get_address("aaveV3TeleportOperator") == ""


def test_iv_to_factory_vault_mapping():
    """IV → aTokenWrapper map drives resolve_aave_factory_vault for createVault."""
    from twyne_cli.contracts import resolve_aave_factory_vault
    resolved = resolve_aave_factory_vault(AAVE_INTERMEDIATE_VAULT)
    assert resolved is not None
    assert resolved.lower() == ATOKEN_WRAPPER.lower()
