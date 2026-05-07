"""Unit tests for the chain registry, --chain flag parsing, and capability gating."""

from click.testing import CliRunner

from twyne_cli.chains import (
    CHAINS,
    active_chain,
    all_chains,
    resolve_chain,
    set_active_chain,
    supported_slugs,
)
from twyne_cli.exceptions import (
    ChainCapabilityError,
    ChainNotSupportedError,
    EulerNotSupportedError,
    OperatorsNotSupportedError,
)

# --------------------------------------------------------------------------- #
# Registry
# --------------------------------------------------------------------------- #


def test_registry_has_mainnet_and_megaeth():
    assert 1 in CHAINS
    assert 4326 in CHAINS
    assert CHAINS[1].slug == "mainnet"
    assert CHAINS[4326].slug == "megaeth"


def test_supported_slugs_match_registry():
    assert set(supported_slugs()) == {"mainnet", "megaeth"}


def test_megaeth_capability_flags():
    mega = CHAINS[4326]
    assert mega.supports_euler is False
    assert mega.supports_operators is False
    assert mega.env_var == "RPC_URL_4326"
    assert mega.default_rpc == "https://mainnet.megaeth.com/rpc"


def test_mainnet_capability_flags():
    main = CHAINS[1]
    assert main.supports_euler is True
    assert main.supports_operators is True
    assert main.env_var == "RPC_URL_1"


def test_all_chains_returns_sorted_by_chain_id():
    chains = all_chains()
    ids = [c.chain_id for c in chains]
    assert ids == sorted(ids)


# --------------------------------------------------------------------------- #
# resolve_chain
# --------------------------------------------------------------------------- #


def test_resolve_chain_by_slug():
    assert resolve_chain("mainnet").chain_id == 1
    assert resolve_chain("megaeth").chain_id == 4326


def test_resolve_chain_by_slug_case_insensitive():
    assert resolve_chain("MAINNET").chain_id == 1
    assert resolve_chain("MegaETH").chain_id == 4326


def test_resolve_chain_by_int_id():
    assert resolve_chain(1).slug == "mainnet"
    assert resolve_chain(4326).slug == "megaeth"


def test_resolve_chain_by_str_id():
    assert resolve_chain("1").slug == "mainnet"
    assert resolve_chain("4326").slug == "megaeth"


def test_resolve_chain_none_returns_mainnet():
    assert resolve_chain(None).chain_id == 1


def test_resolve_chain_empty_returns_mainnet():
    assert resolve_chain("").chain_id == 1


def test_resolve_chain_unknown_slug_raises():
    import pytest
    with pytest.raises(ChainNotSupportedError):
        resolve_chain("foobar")


def test_resolve_chain_unknown_id_raises():
    import pytest
    with pytest.raises(ChainNotSupportedError):
        resolve_chain(999)


# --------------------------------------------------------------------------- #
# Active chain (module-level state)
# --------------------------------------------------------------------------- #


def test_active_chain_default_is_mainnet():
    set_active_chain(CHAINS[1])
    assert active_chain().chain_id == 1


def test_set_active_chain_switches_state():
    try:
        set_active_chain(CHAINS[4326])
        assert active_chain().chain_id == 4326
    finally:
        set_active_chain(CHAINS[1])  # restore


# --------------------------------------------------------------------------- #
# Exception classes
# --------------------------------------------------------------------------- #


def test_operators_error_is_capability_subclass():
    assert issubclass(OperatorsNotSupportedError, ChainCapabilityError)
    err = OperatorsNotSupportedError(CHAINS[4326])
    assert "MegaETH" in str(err)
    assert "4326" in str(err)


def test_euler_error_is_capability_subclass():
    assert issubclass(EulerNotSupportedError, ChainCapabilityError)
    err = EulerNotSupportedError(CHAINS[4326])
    assert "Euler" in str(err)
    assert "Aave" in str(err)


def test_chain_not_supported_lists_supported_slugs():
    err = ChainNotSupportedError("foobar")
    msg = str(err)
    assert "mainnet" in msg
    assert "megaeth" in msg


# --------------------------------------------------------------------------- #
# --chain flag parsing
# --------------------------------------------------------------------------- #


def test_chain_flag_accepts_slug():
    from twyne_cli.cli import cli
    runner = CliRunner()
    result = runner.invoke(cli, ["--chain", "megaeth", "--help"])
    assert result.exit_code == 0


def test_chain_flag_accepts_id():
    from twyne_cli.cli import cli
    runner = CliRunner()
    result = runner.invoke(cli, ["--chain", "4326", "--help"])
    assert result.exit_code == 0


def test_chain_flag_rejects_unknown():
    from twyne_cli.cli import cli
    runner = CliRunner()
    result = runner.invoke(cli, ["--chain", "polygon", "vault", "list"])
    assert result.exit_code != 0
    assert "not supported" in result.output.lower()


def test_chain_flag_default_is_mainnet():
    from twyne_cli.cli import cli
    runner = CliRunner()
    # Just opening --help shouldn't change state, but the cli callback resolves
    # the active chain. After invocation, active_chain() should be mainnet by default.
    runner.invoke(cli, ["--help"])
    assert active_chain().chain_id == 1


# --------------------------------------------------------------------------- #
# Operator/Euler gating via CLI
# --------------------------------------------------------------------------- #


def test_operators_group_blocked_on_megaeth():
    from twyne_cli.cli import cli
    runner = CliRunner()
    result = runner.invoke(
        cli,
        ["--chain", "megaeth", "tx", "operators", "leverage",
         "0x0000000000000000000000000000000000000000", "1"],
    )
    assert result.exit_code != 0
    assert "not supported on MegaETH" in result.output


def test_operators_deleverage_blocked_on_megaeth():
    from twyne_cli.cli import cli
    runner = CliRunner()
    result = runner.invoke(
        cli,
        ["--chain", "megaeth", "tx", "operators", "deleverage",
         "0x0000000000000000000000000000000000000000", "1"],
    )
    assert result.exit_code != 0
    assert "not supported on MegaETH" in result.output


def test_operators_teleport_blocked_on_megaeth():
    from twyne_cli.cli import cli
    runner = CliRunner()
    result = runner.invoke(
        cli,
        ["--chain", "megaeth", "tx", "operators", "teleport",
         "0x0000000000000000000000000000000000000000",
         "0x0000000000000000000000000000000000000000"],
    )
    assert result.exit_code != 0
    assert "not supported on MegaETH" in result.output


def test_operators_close_position_blocked_on_megaeth():
    from twyne_cli.cli import cli
    runner = CliRunner()
    result = runner.invoke(
        cli,
        ["--chain", "megaeth", "tx", "operators", "close-position",
         "0x0000000000000000000000000000000000000000"],
    )
    assert result.exit_code != 0
    assert "not supported on MegaETH" in result.output


def test_operators_group_works_on_mainnet():
    """Sanity: --chain mainnet should not trigger the operator-not-supported error."""
    from twyne_cli.cli import cli
    runner = CliRunner()
    result = runner.invoke(cli, ["--chain", "mainnet", "tx", "operators", "--help"])
    assert result.exit_code == 0
    assert "leverage" in result.output


def test_credit_deposit_protocol_euler_blocked_on_megaeth():
    """--protocol euler on MegaETH should fail with a clear UsageError."""
    from twyne_cli.cli import cli
    runner = CliRunner()
    result = runner.invoke(
        cli,
        ["--chain", "megaeth", "tx", "credit", "deposit",
         "0xcA883E66FC22792461E039d92350BeC04228f9F8", "1.0",
         "--protocol", "euler"],
    )
    assert result.exit_code != 0
    assert "Euler protocol is not available" in result.output
    assert "MegaETH" in result.output
    assert "--protocol aave" in result.output


# --------------------------------------------------------------------------- #
# Address loader: chain-aware
# --------------------------------------------------------------------------- #


def test_address_loader_returns_megaeth_addresses():
    from twyne_cli.contracts import _load_addresses
    set_active_chain(CHAINS[4326])
    try:
        addrs = _load_addresses()
        assert addrs["chainId"] == 4326
        assert addrs["vaultManager"] == "0x91BB674Fcc7CA44ecF97d8330738f8c806318017"
        assert addrs["evc"] == "0xFF06F28cf0c44Cf1E8F03E6835bB2F3a2a752C5C"
        assert addrs["operators"] == {}
        assert "aave_aWETH" in addrs["intermediateVaults"]
    finally:
        set_active_chain(CHAINS[1])


def test_address_loader_returns_mainnet_addresses():
    from twyne_cli.contracts import _load_addresses
    set_active_chain(CHAINS[1])
    addrs = _load_addresses()
    assert addrs["chainId"] == 1
    assert "eulerLeverageOperator" in addrs


def test_get_address_uses_active_chain():
    from twyne_cli.contracts import get_address
    set_active_chain(CHAINS[4326])
    try:
        assert get_address("vaultManager") == "0x91BB674Fcc7CA44ecF97d8330738f8c806318017"
        assert get_address("evc") == "0xFF06F28cf0c44Cf1E8F03E6835bB2F3a2a752C5C"
    finally:
        set_active_chain(CHAINS[1])


# --------------------------------------------------------------------------- #
# Cache: chain-keyed
# --------------------------------------------------------------------------- #


def test_cache_path_includes_chain_id(tmp_path, monkeypatch):
    from twyne_cli import cache as cache_mod
    monkeypatch.setattr(cache_mod, "CACHE_DIR", tmp_path)
    set_active_chain(CHAINS[4326])
    try:
        c = cache_mod.VaultCache()
        assert c.chain_id == 4326
        assert "chain4326" in str(c._cache_file)
        assert c.start_block == 14_706_831
    finally:
        set_active_chain(CHAINS[1])


def test_cache_uses_mainnet_start_block_by_default():
    from twyne_cli.cache import VaultCache
    set_active_chain(CHAINS[1])
    c = VaultCache()
    assert c.start_block == 23_233_276


# --------------------------------------------------------------------------- #
# Config: multi-chain RPC schema + back-compat
# --------------------------------------------------------------------------- #


def test_resolve_rpc_for_chain_reads_multi_chain_dict():
    from twyne_cli.commands.config import resolve_rpc_for_chain
    cfg = {"rpc_urls": {"mainnet": "https://main.example", "megaeth": "https://mega.example"}}
    assert resolve_rpc_for_chain(cfg, CHAINS[1]) == "https://main.example"
    assert resolve_rpc_for_chain(cfg, CHAINS[4326]) == "https://mega.example"


def test_resolve_rpc_for_chain_legacy_back_compat():
    """Legacy 'rpc_url' field is treated as mainnet RPC (back-compat)."""
    from twyne_cli.commands.config import resolve_rpc_for_chain
    cfg = {"rpc_url": "https://legacy.example"}
    assert resolve_rpc_for_chain(cfg, CHAINS[1]) == "https://legacy.example"
    # Legacy doesn't apply to non-mainnet chains.
    assert resolve_rpc_for_chain(cfg, CHAINS[4326]) is None


def test_resolve_rpc_for_chain_returns_none_when_unset():
    from twyne_cli.commands.config import resolve_rpc_for_chain
    assert resolve_rpc_for_chain({}, CHAINS[1]) is None
    assert resolve_rpc_for_chain({}, CHAINS[4326]) is None
