# twyne-cli

Lightweight CLI for querying and interacting with the Twyne protocol on-chain.

## Install

```bash
uv sync --frozen        # install pinned dependencies into .venv (wheels-only)
uv tool install .       # makes the `twyne` command available globally
```

`uv sync --frozen` installs exactly what `uv.lock` pins and verifies each artifact's
hash. The lockfile is generated with `uv lock --no-build`, so every install resolves
to a wheel (no `setup.py` code from third-party packages runs at install time). A
small allowlist of pure-Python sdist-only packages (`lazyasd`, `python-baseconv`,
`varint`) is the only exception; CI fails if the set of sdist-only dependencies grows.

Update after code changes:

```bash
uv tool install --reinstall .
```

### Maintainer note — adding or updating dependencies

When changing dependencies in `pyproject.toml`, always refresh the lockfile with the
wheels-only flag so a future install cannot silently fall back to building an sdist:

```bash
uv lock --no-build
```

If a new dependency is sdist-only on PyPI, `uv lock --no-build` will include it (uv
falls back when no wheel exists anywhere) and the CI sdist sentinel will fail until
the package is reviewed and added to the allowlist in `.github/workflows/check.yml`.

## Quick Start

```bash
twyne init                                   # guided setup: chain, RPC, account
twyne protocol overview                      # Ethereum mainnet (default)
twyne --chain megaeth protocol overview      # MegaETH (chain 4326)
twyne vault list                             # list collateral vaults
twyne vault health <vault-address>           # check a vault's health
```

## Supported Chains

| Slug | Chain ID | Operators? | Euler? | Default RPC |
|------|----------|-----------|--------|-------------|
| `mainnet` | 1 | yes | yes | Ape default (MEV Blocker) |
| `megaeth` | 4326 | **no** | no | `https://mainnet.megaeth.com/rpc` |

MegaETH is currently an **Aave-V3-only** Twyne deployment with no leverage/deleverage/teleport operators. The `tx operators ...` group exits cleanly with `UsageError` on MegaETH; the rest of the CLI (read queries, collateral vault ops, credit vault ops, factory, batch, discover/migrate against Aave) works normally.

Select a chain with `--chain`:

```bash
twyne --chain megaeth vault list                    # by slug
twyne --chain 4326 vault list                       # by chain id
twyne --chain mainnet protocol overview             # explicit mainnet
```

Default chain is `mainnet`; override with `twyne config set-default-chain megaeth`.

## RPC Configuration

The CLI works out of the box for mainnet via Ape's default provider. MegaETH falls back to its public RPC if nothing is configured.

Per-chain RPC resolution order:

1. `--rpc <url>` flag
2. `RPC_URL_<chain_id>` env var (e.g., `RPC_URL_1`, `RPC_URL_4326`)
3. Legacy `RPC_URL` env var (only honoured for mainnet, back-compat)
4. Saved config (`~/.config/twyne/config.json`)
5. Chain default (`https://mainnet.megaeth.com/rpc` for MegaETH; Ape default for mainnet)

Set RPCs:

```bash
# Per-chain via config
twyne config set-rpc --chain mainnet https://eth-mainnet.g.alchemy.com/v2/YOUR_KEY
twyne config set-rpc --chain megaeth https://your-megaeth-endpoint

# Per-chain via env var
export RPC_URL_1=https://eth-mainnet.g.alchemy.com/v2/YOUR_KEY
export RPC_URL_4326=https://mainnet.megaeth.com/rpc

# Per-command
twyne --chain megaeth --rpc https://mainnet.megaeth.com/rpc vault list
```

Manage saved config:

```bash
twyne config get-rpc                   # all configured RPCs
twyne config get-rpc --chain megaeth   # one chain
twyne config clear-rpc --chain megaeth # remove one chain's RPC
twyne config set-default-chain megaeth # change default --chain
twyne config set-account <alias>       # save default signing account
twyne config get-account               # show configured account
```

## Global Flags

Available on all commands:

| Flag | Purpose |
|------|---------|
| `--chain <slug-or-id>` | Target chain (`mainnet`, `megaeth`, or chain id; default `mainnet`) |
| `--rpc <url>` | RPC URL (overrides env/config for the active chain) |
| `--json` | Force JSON output |
| `--block <number>` | Query at specific historical block |
| `--no-cache` | Bypass vault cache, force full rescan |
| `--verbose, -v` | Show detailed revert reasons and call traces |
| `--version` | Show version |

## Usage

### Read-only queries

```bash
twyne vault health <vault-address>          # health factors + risk level
twyne vault info <vault-address>            # full vault state: collateral, debt, LTV, health
twyne vault list                            # all collateral vaults (cached)
twyne protocol overview                     # all collateral assets, IVs, max LTVs, buffers
twyne protocol rates <asset-or-iv-address>  # params for specific asset or IV
twyne protocol tvl                          # Twyne TVL from DefiLlama (no RPC needed)
twyne protocol ext-ltvs                     # external protocol liq LTV params for all pairs
twyne user <wallet-address>                 # all vaults owned by wallet
```

Append `--json` to force JSON output, or pipe to get JSON automatically:

```bash
twyne vault health <address> --json
twyne vault list | jq '.[] | .owner'
```

Query at a specific block:

```bash
twyne --block 21000000 vault health <address>
```

### Transactions

All transaction commands live under `twyne tx`. They simulate before submitting and support `--dry-run`, `--yes` (skip confirmation), and `--raw` (treat amount as raw wei).

Signing key resolution (in order; first match wins):

1. `--account <alias>` — Ape keystore alias (`ape accounts import <alias>` once, then reference by name)
2. `--private-key-file <path>` — file containing the raw key, **mode `0600` required**
3. `--private-key <key>` — **DEPRECATED**; the flag leaks via shell history, `ps` listings, and log aggregators. Still works (emits a warning) so existing scripts keep running, but plan to remove in a future release.
4. `$PRIVATE_KEY` env var — set with `read -s PRIVATE_KEY` to avoid shell-history capture
5. Interactive prompt — when no source is configured and stdin is a TTY, the CLI prompts (no echo). For scripted use, pipe the key: `echo "$KEY" | twyne tx ...`
6. Config `default_account` — set once via `twyne config set-account <alias>`

Shared transaction options:

| Option | Purpose |
|--------|---------|
| `--account <alias>` | Ape account alias (preferred — `ape accounts import` first) |
| `--private-key-file <path>` | Path to file containing the key (mode `0600` enforced) |
| `--private-key <key>` | **DEPRECATED**; leaks the key — see precedence above |
| `--dry-run` | Simulate only |
| `--yes` | Skip confirmation prompt |
| `--raw` | Treat amount as raw wei |
| `--max-approve` | Use max uint256 approval |
| `--skip-approval` | Error if allowance insufficient instead of auto-approving |
| `--gas-multiplier <float>` | Gas limit multiplier (default: 1.5x) |
| `--priority-fee <gwei>` | Max priority fee tip in gwei |
| `--gas-limit <int>` | Fixed gas limit (bypasses estimation) |

#### Collateral vault operations

```bash
twyne tx collateral deposit <vault> <amount>
twyne tx collateral deposit-underlying <vault> <amount>
twyne tx collateral withdraw <vault> <amount> [--receiver <addr>]
twyne tx collateral redeem-underlying <vault> <amount> [--receiver <addr>]
twyne tx collateral borrow <vault> <amount> [--receiver <addr>]
twyne tx collateral repay <vault> <amount>
twyne tx collateral set-ltv <vault> <ltv-bps>
twyne tx collateral liquidate <vault>
twyne tx collateral skim <vault>
```

#### Credit vault operations (CLP / intermediate vault)

```bash
twyne tx credit deposit <iv-address> <amount> [--protocol euler|aave]
twyne tx credit deposit-underlying <iv-address> <amount> [--protocol euler|aave]
twyne tx credit deposit-atokens <iv-address> <amount>
twyne tx credit withdraw <iv-address> <amount> [--receiver <addr>]
twyne tx credit redeem <iv-address> <shares> [--receiver <addr>]
```

#### Operator actions (leverage, deleverage, teleport, close)

```bash
twyne tx operators leverage <vault> <amount> [--protocol euler|aave] [--slippage <pct>]
twyne tx operators deleverage <vault> <amount> [--protocol euler|aave] [--slippage <pct>]
twyne tx operators teleport <vault> <target-vault> [--protocol euler|aave]
twyne tx operators close-position <vault> [--protocol euler|aave] [--slippage <pct>]
```

#### Factory (vault creation)

```bash
twyne tx factory create-vault <intermediate-vault> <target-vault> [--vault-type 0|1] [--ltv <bps>] [--target-asset <addr>]
twyne tx factory open-position <intermediate-vault> <target-vault> --deposit <amount> [--borrow <amount>] [--vault-type 0|1] [--ltv <bps>] [--target-asset <addr>]
```

`open-position` atomically creates a collateral vault, deposits collateral, and optionally borrows in a single EVC batch. The vault address is predicted via simulation. `--deposit` specifies the underlying token amount (e.g. wstETH, not ewstETH). For Euler vaults, the intermediate vault address is auto-resolved to the collateral asset the factory expects.

`--vault-type`: 0 = Euler (default), 1 = Aave V3.

#### Position discovery and migration

```bash
twyne tx discover-positions <wallet> [--protocol euler|aave]   # find migratable positions
twyne tx migrate-position <wallet> --position-num <n> [--protocol euler|aave] [--ltv <bps>]
```

`discover-positions` scans Euler V2 sub-accounts or Aave V3 for single-collateral, single-debt positions that can be migrated to Twyne.

#### EVC batch execution

```bash
twyne tx batch execute <batch-file.yaml> [--evc-address <addr>]
twyne tx batch simulate <batch-file.yaml> [--evc-address <addr>]
```

Batch files are YAML or JSON with an `operations` list. Supported actions: `collateral.deposit`, `collateral.withdraw`, `collateral.borrow`, `collateral.repay`, `collateral.set_ltv`, `token.approve`.

### Shell completions

```bash
eval "$(twyne completion bash)"                                       # bash (add to .bashrc)
eval "$(twyne completion zsh)"                                        # zsh (add to .zshrc)
twyne completion fish > ~/.config/fish/completions/twyne.fish         # fish
```

Completions suggest vault addresses, intermediate vault names, and target vault names.

### Gas configuration

All transactions use a **1.5x gas limit multiplier** by default (configured in `ape-config.yaml`). This ensures complex EVC batch transactions have enough gas headroom.

```bash
# Use 2x gas limit and 3 gwei priority fee
twyne tx collateral deposit <vault> 1.0 --gas-multiplier 2.0 --priority-fee 3

# Higher tip for time-sensitive batch
twyne tx factory open-position <iv> <tv> --deposit 1.5 --priority-fee 5

# Fixed gas limit for known-cost transactions
twyne tx collateral repay <vault> max --gas-limit 500000
```

## Key Facts

- Amounts are human-readable by default (1.0 = 1 token). `--raw` for wei.
- `max` keyword works for repay and withdraw (uses `maxRepay()` / vault balance).
- LTV is in basis points: 8500 = 85%, 9300 = 93%.
- The CLI auto-handles ERC20 approvals. `--max-approve` for unlimited, `--skip-approval` to error instead.
- `--dry-run` simulates via eth_call without submitting.
- Health factor < 1.0 = liquidatable. Twyne liquidation is inheritance-style (vault ownership transfers to liquidator).
- WETH users may need to wrap ETH first: `cast send 0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2 --value <amt>ether 'deposit()'`.

## Development

```bash
uv sync
uv run pytest tests/                            # unit tests (mainnet + MegaETH)
uv run pytest tests/integration/                # mainnet fork integration (Anvil on 8454)
uv run pytest tests/integration/megaeth/ --live # MegaETH live read-only smoke
uv run ruff check src/ tests/
```

MegaETH fork tests (when added later) start Anvil with:

```bash
anvil --fork-url https://mainnet.megaeth.com/rpc --chain-id 4326 --port 8455
```

The `--live` flag opts into read-only RPC tests against `https://mainnet.megaeth.com/rpc`. Without `--live`, all live-tagged tests are skipped.

## Troubleshooting

- **E_TransferFromFailed**: Wrong token or insufficient balance. Check you hold the deposit token (not just ETH).
- **Gas estimation failed**: Use `--gas-limit 5000000` to override.
- **Simulation passed, tx reverts**: Interest accrual between sim and execution. close-position handles this automatically.
- **Verbose mode**: Add `-v` to any command for detailed revert reasons and call traces.
