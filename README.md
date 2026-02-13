# twyne-cli

Lightweight CLI for querying Twyne protocol state on-chain.

## Setup

```bash
uv sync
cp .env.example .env  # Set your RPC_URL
```

## Usage

```bash
uv run twyne vault health <vault-address>
uv run twyne vault info <vault-address>
uv run twyne vault list
uv run twyne protocol overview
uv run twyne protocol rates <iv-address>
uv run twyne user <wallet-address>
```
