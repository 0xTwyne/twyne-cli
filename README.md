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

## Development

```bash
uv sync
uv run pytest tests/
uv run ruff check src/ tests/
```
