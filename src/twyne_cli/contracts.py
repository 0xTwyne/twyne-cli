"""Contract loading, ABI caching, and address registry."""

import json
import os
from importlib import resources

from ape import Contract

# --------------------------------------------------------------------------- #
# ABI loading
# --------------------------------------------------------------------------- #

_ABI_CACHE: dict[str, list] = {}


def _load_abi(name: str) -> list:
    """Load an ABI JSON file by contract name (cached)."""
    if name not in _ABI_CACHE:
        ref = resources.files("twyne_cli") / "abis" / f"{name}.json"
        _ABI_CACHE[name] = json.loads(ref.read_text())
    return _ABI_CACHE[name]


# --------------------------------------------------------------------------- #
# Address registry
# --------------------------------------------------------------------------- #

_ADDRESSES: dict | None = None


def _load_addresses() -> dict:
    """Load mainnet addresses (cached). Env overrides take precedence."""
    global _ADDRESSES
    if _ADDRESSES is None:
        ref = resources.files("twyne_cli") / "addresses" / "mainnet.json"
        _ADDRESSES = json.loads(ref.read_text())
    return _ADDRESSES


def get_address(key: str) -> str:
    """
    Get a contract address by key.

    Checks environment variable TWYNE_{KEY} first (uppercase), then falls back
    to the bundled addresses/mainnet.json.

    Keys: healthStatViewer, vaultManager, collateralVaultFactory, oracleRouter,
          aaveOracleRouter, intermediateVaults.euler_eWETH, etc.
    """
    env_key = f"TWYNE_{key.upper()}"
    env_val = os.environ.get(env_key)
    if env_val:
        return env_val

    addrs = _load_addresses()

    # Support dotted keys like "intermediateVaults.euler_eWETH"
    parts = key.split(".")
    obj = addrs
    for part in parts:
        if isinstance(obj, dict):
            obj = obj.get(part)
        else:
            return ""
    return obj if isinstance(obj, str) else ""


# --------------------------------------------------------------------------- #
# Contract constructors
# --------------------------------------------------------------------------- #


def health_stat_viewer():
    """Get HealthStatViewer contract instance."""
    return Contract(get_address("healthStatViewer"), abi=_load_abi("HealthStatViewer"))


def vault_manager():
    """Get VaultManager contract instance."""
    return Contract(get_address("vaultManager"), abi=_load_abi("VaultManager"))


def collateral_vault_factory():
    """Get CollateralVaultFactory contract instance."""
    return Contract(get_address("collateralVaultFactory"), abi=_load_abi("CollateralVaultFactory"))


def collateral_vault(address: str):
    """Get a CollateralVault contract instance at a given address."""
    return Contract(address, abi=_load_abi("CollateralVault"))


def credit_vault(address: str):
    """Get a credit/intermediate vault (EVault) instance at a given address."""
    return Contract(address, abi=_load_abi("EVault"))


def euler_oracle():
    """Get Euler oracle router contract instance."""
    return Contract(get_address("oracleRouter"), abi=_load_abi("EulerRouter"))


def aave_oracle():
    """Get Aave oracle router contract instance."""
    return Contract(get_address("aaveOracleRouter"), abi=_load_abi("EulerRouter"))


def aave_v3_pool():
    """Get Aave V3 Pool contract instance."""
    from .constants import AAVE_V3_POOL

    return Contract(AAVE_V3_POOL, abi=_load_abi("AaveV3Pool"))


def intermediate_vaults() -> dict[str, str]:
    """Return dict of intermediate vault name → address."""
    addrs = _load_addresses()
    return addrs.get("intermediateVaults", {})


def evc(address: str | None = None):
    """Get EVC contract instance (Twyne EVC by default)."""
    addr = address or get_address("evc")
    return Contract(addr, abi=_load_abi("EVC"))


def erc20(address: str):
    """Get ERC20 contract instance at a given address."""
    return Contract(address, abi=_load_abi("ERC20"))


def leverage_operator(protocol: str = "euler"):
    """Get leverage operator contract. Protocol: 'euler' or 'aave'."""
    key = "eulerLeverageOperator" if protocol == "euler" else "aaveV3LeverageOperator"
    return Contract(get_address(key), abi=_load_abi("LeverageOperator"))


def deleverage_operator(protocol: str = "euler"):
    """Get deleverage operator contract. Protocol: 'euler' or 'aave'."""
    key = "eulerDeleverageOperator" if protocol == "euler" else "aaveV3DeleverageOperator"
    return Contract(get_address(key), abi=_load_abi("DeleverageOperator"))


def teleport_operator():
    """Get Aave teleport operator contract."""
    return Contract(get_address("aaveV3TeleportOperator"), abi=_load_abi("TeleportOperator"))


def euler_wrapper():
    """Get Euler wrapper contract for credit vault deposits."""
    return Contract(get_address("eulerWrapper"), abi=_load_abi("EulerWrapper"))


def aave_wrapper():
    """Get Aave wrapper contract for credit vault deposits."""
    return Contract(get_address("aaveV3Wrapper"), abi=_load_abi("AaveWrapper"))


def aave_atoken_wrapper():
    """Get Aave aToken wrapper contract."""
    return Contract(get_address("aWSTETHWrapper"), abi=_load_abi("AaveATokenWrapper"))


def resolve_euler_factory_vault(address: str) -> str | None:
    """For Euler IVs, the factory expects the collateral asset (eVault share token).

    Queries IV.asset() on-chain to get the eVault share token address.
    Returns None if the call fails (address may already be correct).
    """
    try:
        iv = credit_vault(address)
        collateral_asset = str(iv.asset())
        if collateral_asset and collateral_asset != address:
            return collateral_asset
    except Exception:
        pass
    return None


def resolve_aave_factory_vault(address: str) -> str | None:
    """If address is a known Aave IV, return the aToken wrapper the factory expects.

    Returns None if no mapping exists (address is already correct or unknown).
    """
    addrs = _load_addresses()
    mapping = addrs.get("aaveIVToFactoryVault", {})
    addr_lower = address.lower()
    for iv, wrapper in mapping.items():
        if iv.lower() == addr_lower:
            return wrapper
    return None
