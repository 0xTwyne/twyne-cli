# twyne-cli

Lightweight CLI for querying Twyne protocol state on-chain.

## Install

```bash
uv tool install .
```

This makes the `twyne` command available globally. To update after code changes:

```bash
uv tool install --reinstall .
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

Manage your saved RPC:

```bash
twyne config get-rpc      # Show current RPC
twyne config clear-rpc    # Remove saved RPC, revert to default
```

## Usage

### Read-only queries

```bash
twyne vault health <vault-address>
twyne vault info <vault-address>
twyne vault list
twyne protocol overview
twyne protocol rates <asset-or-iv-address>
twyne user <wallet-address>
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

#### Operator actions (leverage, deleverage, teleport)

```bash
twyne tx operators leverage <vault> <amount> [--protocol euler|aave] [--slippage <pct>]
twyne tx operators deleverage <vault> <amount> [--protocol euler|aave] [--slippage <pct>]
twyne tx operators teleport <vault> <target-vault> [--protocol euler|aave]
```

#### Factory

```bash
twyne tx factory create-vault <asset-address> <target-vault> [--vault-type 0|1] [--ltv <bps>] [--target-asset <addr>]
```

#### EVC batch execution

```bash
twyne tx batch execute <batch-file.yaml> [--evc-address <addr>]
twyne tx batch simulate <batch-file.yaml> [--evc-address <addr>]
```

Batch files are YAML or JSON with an `operations` list. Supported actions: `collateral.deposit`, `collateral.withdraw`, `collateral.borrow`, `collateral.repay`, `token.approve`.

## Development

```bash
uv sync
uv run pytest tests/
uv run ruff check src/ tests/
```
