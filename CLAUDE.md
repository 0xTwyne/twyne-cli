# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Twyne CLI is a Python command-line tool for querying and interacting with the Twyne credit delegation protocol on Ethereum mainnet. It supports read-only queries (vault health, protocol parameters, user portfolios) and write transactions (opening/closing positions, depositing, borrowing, leveraging, migrating positions) via direct RPC calls — no indexer or API dependency required.

- **Python 3.11+**, managed with **uv**
- **Framework**: Ape (eth-ape) — provides Click CLI, multicall, contract interaction
- **Output**: Auto-detects TTY (tables for humans, JSON when piped)
- **Transactions**: All write operations simulate before submitting, route through EVC, and auto-handle token approvals

## Commands

```bash
# Install dependencies
uv sync

# Install CLI globally
uv tool install .

# Run CLI (during development)
uv run twyne --help

# Run tests
uv run pytest tests/                    # unit tests
uv run pytest tests/integration/        # integration tests (requires RPC)

# Lint
uv run ruff check src/ tests/
```

## Global Flags

Available on all commands:

| Flag | Purpose |
|------|---------|
| `--rpc <url>` | Ethereum RPC URL (overrides env/config) |
| `--json` | Force JSON output (auto-detects TTY) |
| `--block <number>` | Query at specific historical block |
| `--no-cache` | Bypass vault cache, force full rescan |
| `--verbose, -v` | Show detailed revert reasons and call traces |
| `--version` | Show version |

## CLI Command Reference

### `twyne vault` — Collateral Vault Queries

| Command | Arguments | Purpose |
|---------|-----------|---------|
| `health` | `<address>` | Show external/internal health factors and risk level |
| `info` | `<address>` | Full vault state: collateral, credit, debt, LTV, health |
| `list` | (none) | List all created collateral vaults (incremental cache) |

### `twyne protocol` — Protocol-Level Queries

| Command | Arguments | Purpose |
|---------|-----------|---------|
| `overview` | (none) | All collateral assets, max LTVs, external liq buffers, IV mappings |
| `rates` | `<asset-or-iv-address>` | Params for a specific collateral asset or intermediate vault |
| `tvl` | (none) | Fetch Twyne TVL from DefiLlama (no RPC required) |
| `ext-ltvs` | (none) | External protocol liquidation LTV parameters for all Twyne pairs |

### `twyne user` — User Portfolio

| Command | Arguments | Purpose |
|---------|-----------|---------|
| `user` | `<wallet-address>` | Show all vaults owned by a wallet with health factors and risk levels |

### `twyne config` — CLI Configuration

| Command | Arguments | Purpose |
|---------|-----------|---------|
| `set-rpc` | `<url>` | Save RPC URL to `~/.config/twyne/config.json` |
| `get-rpc` | (none) | Show configured RPC URL |
| `clear-rpc` | (none) | Remove saved RPC, revert to Ape default |
| `set-account` | `<alias>` | Save default signing account alias |
| `get-account` | (none) | Show configured default account |

RPC resolution order: `--rpc` flag > `$RPC_URL` env > saved config > Ape default (MEV Blocker).

### `twyne init` — Interactive Setup

Guided first-run wizard: RPC configuration, Ape account creation/import, shell completion setup.

### `twyne completion` — Shell Completions

```bash
twyne completion bash    # output bash completion function
twyne completion zsh     # output zsh completion function
twyne completion fish    # output fish completion function
```

Setup: `eval "$(twyne completion bash)"` or `twyne completion fish > ~/.config/fish/completions/twyne.fish`.

Completions suggest vault addresses, IV names, and target vault names from the local cache and address registry.

### `twyne tx` — Transaction Commands

All `tx` commands share these options:

| Option | Purpose |
|--------|---------|
| `--account <alias>` | Ape account alias (from `ape accounts import`) |
| `--private-key <key>` | Raw private key (or `$PRIVATE_KEY` env var) |
| `--dry-run` | Simulate only, don't submit |
| `--yes` | Skip confirmation prompt |
| `--raw` | Treat amount as raw wei (no decimal conversion) |
| `--max-approve` | Use max uint256 approval instead of exact amount |
| `--skip-approval` | Don't auto-approve; error if allowance insufficient |
| `--gas-multiplier <float>` | Gas limit multiplier (overrides 1.5x default) |
| `--priority-fee <gwei>` | Max priority fee tip in gwei |
| `--gas-limit <int>` | Fixed gas limit (bypasses estimation) |

Account resolution: `--account` > `--private-key` flag > `$PRIVATE_KEY` env > `default_account` from config.

#### `twyne tx collateral` — Collateral Vault Operations

| Command | Arguments | Options | Purpose |
|---------|-----------|---------|---------|
| `deposit` | `<vault> <amount>` | shared tx opts | Deposit collateral token into vault |
| `deposit-underlying` | `<vault> <amount>` | shared tx opts | Deposit underlying asset (e.g. raw WETH for eWETH vault) |
| `withdraw` | `<vault> <amount>` | `--receiver <addr>` | Withdraw collateral |
| `redeem-underlying` | `<vault> <amount>` | `--receiver <addr>` | Withdraw as underlying asset |
| `borrow` | `<vault> <amount>` | `--receiver <addr>` | Borrow from external protocol |
| `repay` | `<vault> <amount>` | shared tx opts | Repay borrowed amount (`max` to repay all) |
| `set-ltv` | `<vault> <ltv-bps>` | shared tx opts | Set liquidation LTV (basis points) |
| `liquidate` | `<vault>` | shared tx opts | Liquidate unhealthy vault (inherits position) |
| `skim` | `<vault>` | shared tx opts | Skim excess tokens from vault |

#### `twyne tx credit` — Credit Vault (CLP / Intermediate Vault) Operations

| Command | Arguments | Options | Purpose |
|---------|-----------|---------|---------|
| `deposit` | `<iv-address> <amount>` | `--protocol euler\|aave` | Deposit underlying into IV via wrapper |
| `deposit-underlying` | `<iv-address> <amount>` | `--protocol euler\|aave` | Alias for deposit |
| `deposit-atokens` | `<iv-address> <amount>` | shared tx opts | Deposit Aave aTokens into IV |
| `withdraw` | `<iv-address> <amount>` | `--receiver <addr>` | Withdraw from IV (ERC4626) |
| `redeem` | `<iv-address> <shares>` | `--receiver <addr>` | Redeem IV shares (ERC4626) |

#### `twyne tx operators` — Leverage / Deleverage / Teleport

| Command | Arguments | Options | Purpose |
|---------|-----------|---------|---------|
| `leverage` | `<vault> <amount>` | `--protocol`, `--slippage <pct>` | Flash-borrow + swap to increase position |
| `deleverage` | `<vault> <amount>` | `--protocol`, `--slippage <pct>` | Sell collateral to reduce debt |
| `teleport` | `<vault> <target-vault>` | `--protocol` | Migrate position to different debt asset |
| `close-position` | `<vault>` | `--protocol`, `--slippage <pct>` | Fully deleverage and withdraw all |

Swaps use the Euler Swap API. Default slippage: 0.5%.

#### `twyne tx factory` — Vault Creation

| Command | Arguments | Options | Purpose |
|---------|-----------|---------|---------|
| `create-vault` | `<iv> <target-vault>` | `--vault-type 0\|1`, `--ltv <bps>`, `--target-asset <addr>` | Create collateral vault |
| `open-position` | `<iv> <target-vault>` | `--deposit <amt>`, `--borrow <amt>`, `--vault-type`, `--ltv`, `--target-asset` | Atomic create + deposit + borrow in single EVC batch |

`--vault-type`: 0 = Euler (default), 1 = Aave V3. `open-position` predicts the vault address via simulation.

#### `twyne tx batch` — EVC Batch Execution

| Command | Arguments | Options | Purpose |
|---------|-----------|---------|---------|
| `execute` | `<batch-file.yaml>` | `--evc-address <addr>` | Execute YAML/JSON batch via EVC |
| `simulate` | `<batch-file.yaml>` | `--evc-address <addr>` | Dry-run a batch |

Supported batch actions: `collateral.deposit`, `collateral.withdraw`, `collateral.borrow`, `collateral.repay`, `collateral.set_ltv`, `token.approve`.

#### `twyne tx discover-positions` — Position Discovery

```bash
twyne tx discover-positions <wallet-address> [--protocol euler|aave]
```

Scans Euler V2 (sub-accounts 0-3) or Aave V3 for positions that can be migrated to Twyne.

#### `twyne tx migrate-position` — Position Migration

```bash
twyne tx migrate-position <wallet-address> --position-num <i> [--protocol euler|aave] [--ltv <bps>]
```

Migrates a discovered position into Twyne (creates vault, moves assets atomically).

## Architecture

```
CLI (Click via Ape)
  ├── commands/           # Click command groups
  │   ├── vault.py        # vault health, info, list
  │   ├── protocol.py     # protocol overview, rates, tvl, ext-ltvs
  │   ├── user.py         # user portfolio
  │   ├── tx.py           # all transaction commands (~2200 lines)
  │   ├── config.py       # RPC and account config
  │   ├── init.py         # interactive setup wizard
  │   └── completion.py   # shell completion output
  ├── contracts.py        # ABI loading, address registry, Contract factories
  ├── transactions.py     # account resolution, amount parsing, EVC routing, approvals
  ├── batch.py            # YAML/JSON batch file parsing, EVC batch encoding
  ├── discover.py         # Euler/Aave position discovery for migration
  ├── swap.py             # Euler Swap API + 1inch integration
  ├── completions.py      # shell completion helpers (zero Ape dependency)
  ├── cache.py            # incremental vault discovery cache
  ├── formatting.py       # TTY-aware output (tables vs JSON)
  ├── context.py          # shared CLI context (TwyneContext)
  ├── constants.py        # MAXFACTOR, WAD, addresses, defaults
  ├── abis/               # bundled contract ABIs
  └── addresses/          # mainnet.json address registry
```

### Key Design Patterns

- **EVC routing**: All collateral vault operations route through the Twyne EVC (`callThroughEVC` modifier). The CLI uses `execute_through_evc()` to encode calldata and route via `evc.call()`.
- **Automatic approvals**: `ensure_allowance()` checks balances/allowances and prompts for approval. Supports `--max-approve` and `--skip-approval`.
- **Simulate before submit**: All tx commands simulate via `simulate_tx()` / `simulate_through_evc()` before confirmation.
- **TTY detection**: Output auto-switches to JSON when piped. `--json` forces JSON in TTY.
- **Incremental cache**: Vault list scans only new blocks since last checkpoint (`~/.config/twyne/cache/`).
- **Address resolution**: Euler/Aave vaults require different collateral asset formats for the factory. CLI auto-resolves via `resolve_euler_factory_vault()` / `resolve_aave_factory_vault()`.

### Data Files (bundled inside package)

- `src/twyne_cli/abis/` — Contract ABIs: CollateralVault, HealthStatViewer, VaultManager, EVault, EVC, ERC20, LeverageOperator, DeleverageOperator, TeleportOperator, AaveWrapper, AaveATokenWrapper, EulerWrapper
- `src/twyne_cli/addresses/mainnet.json` — Contract addresses from tech-notes (source of truth)

## Environment Variables

| Variable | Purpose |
|----------|---------|
| `RPC_URL` | Ethereum mainnet RPC endpoint |
| `PRIVATE_KEY` | Signing key for transactions |
| `TWYNE_*` | Override specific addresses from registry |

## AI Assistant Rules

- **Never speculate about on-chain protocol parameters.** LTV limits, health factors, interest rates, supported pairs, and all other protocol parameters are on-chain and can change. Always query them using the CLI (`twyne protocol overview`, `twyne protocol ext-ltvs`, `twyne protocol rates`, `twyne vault health`, etc.) before answering questions about what the protocol supports or whether a strategy is possible.
- **Never say something is impossible without checking first.** If a user asks whether a position, leverage level, or strategy is feasible, run the relevant query commands before responding. Do not guess based on "typical DeFi" assumptions.

## Conventions

- LTV values are in basis points (1e4 = 100%). Divide by `MAXFACTOR` for display.
- Oracle prices: Euler uses 1e18 precision, Aave uses 1e8. Both oracles use EulerRouter ABI interface.
- Health factors are 1e18 precision. Divide by `WAD` for display.
- USD address for oracle queries: `0x0000000000000000000000000000000000000348`
- Protocol detection: try `aToken()` call — Aave if exists, else Euler.
- The `T_CollateralVaultCreated` event on CollateralVaultFactory is used to enumerate all vaults.
- Amounts are human-readable by default (1.0 = 1 token). `--raw` for wei.
- `max` keyword supported for repay/withdraw operations.

## Key Contracts

| Contract | Address | Purpose |
|----------|---------|---------|
| HealthStatViewer | `0x0dd9065c998E75657BcE6C3a11d7F5AbA5CBdbD4` | Health factor queries |
| VaultManager | `0x0acd3A3c8Ab6a5F7b5A594C88DFa28999dA858aC` | Protocol parameters |
| CollateralVaultFactory | `0xa1517cCe0bE75700A8838EA1cEE0dc383cd3A332` | Vault enumeration + creation |
| Twyne EVC | `0xef39D6493884C4C84D38a4bFF879Ce16CEdE702a` | EVC for vault operations |
| Euler EVC | `0x0C9a3dd6b8F28529d72d7f9cE918D493519EE383` | Euler V2 EVC |
| Euler Oracle Router | `0xb001f039D76bA48E577A17c04b6940DB37aF8648` | Euler price quotes |
| Aave Oracle Router | `0x5D7A67418ee94259fd3A6091E2Cc0baeedfFA185` | Aave price quotes |
| Aave V3 Pool | `0x87870Bca3F3fD6335C3F4ce8392D69350B4fA4E2` | Aave position data |
| Euler Leverage Operator | `0x335AB81f1C3d9f72639004d3e982902458CF29b3` | Leverage via Euler |
| Euler Deleverage Operator | `0x36b2Bd4E17827E9dEABdB3AD520AC597972196D4` | Deleverage via Euler |
| Aave V3 Leverage Operator | `0x451949bde57aBe2F5DBD4758Cd50C6DCfC093A4C` | Leverage via Aave |
| Aave V3 Deleverage Operator | `0x229fE10bC00bBE99Ac99703647D4f74F31605e91` | Deleverage via Aave |
| Aave V3 Teleport Operator | `0x868a21426852A775395d4b90De23B3e3E662bd78` | Teleport via Aave |

## Intermediate Vaults (Mainnet)

| Name | Address | Collateral | Protocol |
|------|---------|------------|----------|
| euler_eWETH | `0x87b8081A3ace680f35125F469526Ac10f5418Ca7` | WETH | Euler |
| euler_ewstETH | `0x7613D202Af490c3d1cE1873b0a7022a34E89815f` | wstETH | Euler |
| aave_awstETH | `0x75029a47f28550C93Ad5A3BbD2d9b5315204B561` | wstETH | Aave |

## Testing

Tests use `uv run pytest`. Two categories:

- `tests/` — Unit tests (mock-based, no RPC required). Fixtures in `tests/conftest.py`.
- `tests/integration/` — Integration tests against a real RPC. Fixtures in `tests/integration/conftest.py` (session-scoped, real contracts).

Test dependencies: install with `uv sync --extra test`.

## Gas Configuration

Default: 1.5x gas limit multiplier (configured in `ape-config.yaml`). Override per-command with `--gas-multiplier`, `--priority-fee`, or `--gas-limit`.
