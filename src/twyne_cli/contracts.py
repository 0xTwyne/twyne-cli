"""Contract loading, ABI caching, and address registry."""

import json
import os
from importlib import resources

from .chains import ChainSpec, active_chain
from .exceptions import EulerNotSupportedError, OperatorsNotSupportedError


def _contract(address, abi):
    """Lazy wrapper around ape.Contract — defers ape import to first use."""
    from ape import Contract
    return Contract(address, abi=abi)


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
# Address registry (per-chain, cached by chain_id)
# --------------------------------------------------------------------------- #

_ADDRESSES: dict[int, dict] = {}


def _load_addresses(chain: ChainSpec | None = None) -> dict:
    """Load address registry for the given chain (defaults to active). Cached by chain_id."""
    spec = chain or active_chain()
    cached = _ADDRESSES.get(spec.chain_id)
    if cached is not None:
        return cached
    ref = resources.files("twyne_cli") / "addresses" / spec.addresses_file
    data = json.loads(ref.read_text())
    _ADDRESSES[spec.chain_id] = data
    return data


def get_address(key: str) -> str:
    """
    Get a contract address by key for the active chain.

    Checks environment variable TWYNE_{KEY} first (uppercase), then falls back
    to the bundled per-chain registry.

    Keys: vaultManager, collateralVaultFactory, oracleRouter,
          aaveOracleRouter, intermediateVaults.aave_aWETH, etc.
    """
    env_key = f"TWYNE_{key.upper().replace('.', '_')}"
    env_val = os.environ.get(env_key)
    if env_val:
        return env_val

    addrs = _load_addresses()

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


def vault_manager():
    """Get VaultManager contract instance."""
    return _contract(get_address("vaultManager"), abi=_load_abi("VaultManager"))


def collateral_vault_factory():
    """Get CollateralVaultFactory contract instance."""
    return _contract(get_address("collateralVaultFactory"), abi=_load_abi("CollateralVaultFactory"))


def collateral_vault(address: str):
    """Get a CollateralVault contract instance at a given address."""
    return _contract(address, abi=_load_abi("CollateralVault"))


def credit_vault(address: str):
    """Get a credit/intermediate vault (EVault) instance at a given address."""
    return _contract(address, abi=_load_abi("EVault"))


def euler_oracle():
    """Get Euler oracle router contract instance."""
    return _contract(get_address("oracleRouter"), abi=_load_abi("EulerRouter"))


def aave_oracle():
    """Get Aave oracle router contract instance."""
    return _contract(get_address("aaveOracleRouter"), abi=_load_abi("EulerRouter"))


def aave_v3_pool():
    """Get Aave V3 Pool contract instance."""
    addr = get_address("aavePool")
    if not addr:
        # Back-compat: mainnet hardcoded fallback for older callers that haven't been
        # updated to the per-chain registry yet.
        from .constants import AAVE_V3_POOL
        addr = AAVE_V3_POOL
    return _contract(addr, abi=_load_abi("AaveV3Pool"))


def intermediate_vaults() -> dict[str, str]:
    """Return dict of intermediate vault name → address for the active chain."""
    addrs = _load_addresses()
    return addrs.get("intermediateVaults", {})


def target_vaults() -> dict[str, str]:
    """Return dict of target debt-vault name → address for the active chain."""
    addrs = _load_addresses()
    return addrs.get("targetVaults", {})


def evc(address: str | None = None):
    """Get EVC contract instance (Twyne EVC by default)."""
    addr = address or get_address("evc")
    return _contract(addr, abi=_load_abi("EVC"))


def euler_evc():
    """Get the Euler EVC contract instance."""
    if not active_chain().supports_euler:
        raise EulerNotSupportedError(active_chain())
    return _contract(get_address("eulerEvc"), abi=_load_abi("EVC"))


def erc20(address: str):
    """Get ERC20 contract instance at a given address."""
    return _contract(address, abi=_load_abi("ERC20"))


def _check_operators_supported() -> None:
    chain = active_chain()
    if not chain.supports_operators:
        raise OperatorsNotSupportedError(chain)


def leverage_operator(protocol: str = "euler"):
    """Get leverage operator contract. Protocol: 'euler' or 'aave'."""
    _check_operators_supported()
    key = "eulerLeverageOperator" if protocol == "euler" else "aaveV3LeverageOperator"
    addr = get_address(key)
    if not addr:
        raise OperatorsNotSupportedError(active_chain())
    return _contract(addr, abi=_load_abi("LeverageOperator"))


def deleverage_operator(protocol: str = "euler"):
    """Get deleverage operator contract. Protocol: 'euler' or 'aave'."""
    _check_operators_supported()
    key = "eulerDeleverageOperator" if protocol == "euler" else "aaveV3DeleverageOperator"
    addr = get_address(key)
    if not addr:
        raise OperatorsNotSupportedError(active_chain())
    return _contract(addr, abi=_load_abi("DeleverageOperator"))


def teleport_operator():
    """Get Aave teleport operator contract."""
    _check_operators_supported()
    addr = get_address("aaveV3TeleportOperator")
    if not addr:
        raise OperatorsNotSupportedError(active_chain())
    return _contract(addr, abi=_load_abi("TeleportOperator"))


def euler_wrapper():
    """Get Euler wrapper contract for credit vault deposits."""
    if not active_chain().supports_euler:
        raise EulerNotSupportedError(active_chain())
    return _contract(get_address("eulerWrapper"), abi=_load_abi("EulerWrapper"))


def aave_wrapper():
    """Get Aave wrapper contract for credit vault deposits."""
    return _contract(get_address("aaveV3Wrapper"), abi=_load_abi("AaveWrapper"))


def aave_atoken_wrapper(iv_address: str | None = None):
    """Get Aave aToken wrapper contract.

    If iv_address is provided, resolves the wrapper via the per-chain
    aaveIVToFactoryVault map (preferred). Falls back to a chain-default key for
    callers that don't yet pass the IV — this preserves existing mainnet
    behaviour while making MegaETH (which has a different wrapper symbol) work.
    """
    addrs = _load_addresses()
    if iv_address:
        mapping = addrs.get("aaveIVToFactoryVault", {})
        addr = mapping.get(iv_address)
        if not addr:
            for iv, wrapper in mapping.items():
                if iv.lower() == iv_address.lower():
                    addr = wrapper
                    break
        if addr:
            return _contract(addr, abi=_load_abi("AaveATokenWrapper"))

    wrappers = addrs.get("aTokenWrappers", {})
    if len(wrappers) == 1:
        only = next(iter(wrappers.values()))
        return _contract(only, abi=_load_abi("AaveATokenWrapper"))

    for legacy_key in ("aWSTETHWrapper", "aWETHWrapper"):
        addr = get_address(legacy_key)
        if addr:
            return _contract(addr, abi=_load_abi("AaveATokenWrapper"))

    raise click_exception(
        f"No aToken wrapper configured for chain {active_chain().chain_id}. "
        "Pass iv_address or update addresses/<chain>.json."
    )


def click_exception(msg: str):
    """Lazy click.ClickException constructor — avoids importing click in helpers."""
    import click
    return click.ClickException(msg)


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
