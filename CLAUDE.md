# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Twyne CLI is a lightweight Python command-line tool for querying Twyne protocol state directly on-chain. It provides read-only access to vault health factors, position details, protocol parameters, and user portfolios via direct RPC calls — no indexer or API dependency required.

- **Python 3.11+**, managed with **uv**
- **Framework**: Ape (eth-ape) — provides Click CLI, multicall, contract interaction
- **Read-only**: No write transactions in v1
- **Output**: Auto-detects TTY (tables for humans, JSON when piped)

## Commands

```bash
# Install dependencies
uv sync

# Run CLI
uv run twyne --help
uv run twyne vault health <address>
uv run twyne vault info <address>
uv run twyne vault list
uv run twyne protocol overview
uv run twyne protocol rates <iv-address>
uv run twyne user <wallet-address>

# Run tests
uv run pytest tests/

# Lint
uv run ruff check src/ tests/
```

## Architecture

```
CLI (Click via Ape)
  → contracts.py (ABI loading, address registry, Contract instances)
  → ape_ethereum.multicall.Call (batched RPC queries)
  → formatting.py (TTY detection → Rich tables or JSON)
```

### Source Layout

- `src/twyne_cli/cli.py` — Click group entry point, global flags (--rpc, --json, --block)
- `src/twyne_cli/commands/vault.py` — vault health, info, list
- `src/twyne_cli/commands/protocol.py` — protocol overview, rates
- `src/twyne_cli/commands/user.py` — user portfolio
- `src/twyne_cli/contracts.py` — ABI loading from bundled JSON, contract instantiation, address registry
- `src/twyne_cli/formatting.py` — TTY-aware output (tables vs JSON)
- `src/twyne_cli/constants.py` — MAXFACTOR (1e4), WAD (1e18), USD_ADDRESS

### Data Files

- `abis/` — Bundled contract ABIs (CollateralVault, HealthStatViewer, VaultManager, etc.)
- `addresses/mainnet.json` — Contract addresses from tech-notes (source of truth)

## Environment Variables

- `RPC_URL` — Ethereum mainnet RPC endpoint (required)

## Conventions

- LTV values are in basis points (1e4 = 100%). Divide by `MAXFACTOR` for display.
- Oracle prices: Euler uses 1e18 precision, Aave uses 1e8. Both oracles use EulerRouter ABI interface.
- Health factors are 1e18 precision. Divide by `WAD` for display.
- USD address for oracle queries: `0x0000000000000000000000000000000000000348`
- Protocol detection: try `aToken()` call — Aave if exists, else Euler
- The `T_CollateralVaultCreated` event on CollateralVaultFactory is used to enumerate all vaults

## Key Contracts

| Contract | Address | Purpose |
|----------|---------|---------|
| HealthStatViewer | `0x0dd9065c998E75657BcE6C3a11d7F5AbA5CBdbD4` | Health factor queries |
| VaultManager | `0x0acd3A3c8Ab6a5F7b5A594C88DFa28999dA858aC` | Protocol parameters |
| CollateralVaultFactory | `0xa1517cCe0bE75700A8838EA1cEE0dc383cd3A332` | Vault enumeration |
| Euler Oracle Router | `0xb001f039D76bA48E577A17c04b6940DB37aF8648` | Euler price quotes |
| Aave Oracle Router | `0x5D7A67418ee94259fd3A6091E2Cc0baeedfFA185` | Aave price quotes |
| Aave V3 Pool | `0x87870Bca3F3fD6335C3F4ce8392D69350B4fA4E2` | Aave position data |
