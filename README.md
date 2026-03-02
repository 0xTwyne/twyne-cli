# twyne-cli

Lightweight CLI for querying and interacting with the Twyne protocol on-chain.

## Install

```bash
uv tool install .
```

This makes the `twyne` command available globally. To update after code changes:

```bash
uv tool install --reinstall .
```

## Quick Start

```bash
twyne init                           # guided setup: RPC, account, completions
twyne protocol overview              # see all supported collateral/debt pairs
twyne vault list                     # list all collateral vaults
twyne vault health <vault-address>   # check a vault's health
```

## RPC Configuration

The CLI works out of the box with no configuration — it uses Ape's built-in default RPC.

To use your own RPC endpoint (recommended for heavy usage), pick one of these options:

```bash
# Option 1: Save it permanently
twyne config set-rpc https://eth-mainnet.g.alchemy.com/v2/YOUR_KEY

# Option 2: Set via environment variable
export RPC_URL=https://eth-mainnet.g.alchemy.com/v2/YOUR_KEY

# Option 3: Pass per-command
twyne --rpc https://eth-mainnet.g.alchemy.com/v2/YOUR_KEY vault list
```

Resolution order: `--rpc` flag > `$RPC_URL` env var > saved config > Ape default.

Manage your saved config:

```bash
twyne config get-rpc            # show current RPC
twyne config clear-rpc          # remove saved RPC, revert to default
twyne config set-account <alias> # save default signing account
twyne config get-account        # show configured account
```

## Global Flags

Available on all commands:

| Flag | Purpose |
|------|---------|
| `--rpc <url>` | Ethereum RPC URL |
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

Authentication: `--account <alias>` (Ape keystore) or `--private-key <key>` (or `$PRIVATE_KEY`).

Shared transaction options:

| Option | Purpose |
|--------|---------|
| `--account <alias>` | Ape account alias |
| `--private-key <key>` | Raw private key (or `$PRIVATE_KEY` env var) |
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
uv run pytest tests/                 # unit tests
uv run pytest tests/integration/     # integration tests (requires RPC)
uv run ruff check src/ tests/
```

## Troubleshooting

- **E_TransferFromFailed**: Wrong token or insufficient balance. Check you hold the deposit token (not just ETH).
- **Gas estimation failed**: Use `--gas-limit 5000000` to override.
- **Simulation passed, tx reverts**: Interest accrual between sim and execution. close-position handles this automatically.
- **Verbose mode**: Add `-v` to any command for detailed revert reasons and call traces.
