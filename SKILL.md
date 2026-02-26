---
name: twyne-cli
description: Guide for using the Twyne Protocol CLI to interact with Twyne on-chain — querying vault health, protocol parameters, user portfolios, and executing transactions (opening/closing positions, depositing, borrowing, leveraging, migrating positions). Use this skill whenever a user wants to interact with Twyne protocol via the command line, asks about vault operations, wants to check their position health, needs to open or close a Twyne position, wants to deposit collateral or become a Credit LP, or asks about Twyne CLI commands. Also use this when users mention twyne-cli, collateral vaults, intermediate vaults, credit delegation, or position migration between Euler/Aave and Twyne.
---

# Twyne CLI

Twyne CLI (`twyne`) is a Python tool for interacting with the Twyne credit delegation protocol on Ethereum mainnet. Read operations (vault health, protocol state) and write operations (open/close positions, deposit, borrow, leverage).

## How to Respond

**Match your verbosity to the user's specificity.** There are three modes:

### Mode 1: Direct Execution

When the user's intent is fully specified — they name the action, the tokens, the amounts, and any key parameters — skip explanations and give them the commands.

A fully specified request looks like: "Open a position with 2 wstETH collateral borrowing WETH on Euler at 85% liqLTV" or "Close my vault at 0xABC123" or "Deposit 5 WETH as credit LP into the Euler IV."

In this mode:
- Lead with the command(s) they need to run, ready to copy-paste
- Mention `--dry-run` once as a safety check
- No protocol explainers, no "what is a Credit LP" sections

### Mode 2: Guided Exploration

When the user asks a general question ("how does Twyne work?", "what can I do with the CLI?") or expresses vague intent ("I want to earn yield on my ETH"), explain the relevant mechanics and present their options.

In this mode:
- Explain the protocol concept relevant to their question (credit delegation, CLPs, siphoning rate, etc.)
- **Always include contract addresses** when listing intermediate vaults or other protocol components — use the Known Intermediate Vaults table below
- Show the relevant CLI commands they'd use, including transaction commands (e.g. `twyne tx credit deposit`) not just read-only ones
- Then transition to Mode 3 (ask clarifying questions) if they seem like they want to take action

### Mode 3: Interactive Clarification (Most Common)

Most users fall between the two extremes. They state partial intent — they know what they want to do but haven't specified all the parameters. In this case, **ask follow-up questions** to fill the gaps before giving commands.

For each action, here are the parameters you need. If any are missing from the user's request, ask about them — present the available options and briefly explain the tradeoff so the user can make an informed choice.

**Opening a position — required parameters:**

| Parameter | If missing, ask | Options / Guidance |
|-----------|----------------|-------------------|
| Collateral token | "What collateral do you want to use?" | wstETH (most common), WETH. See Known Intermediate Vaults below for what's supported. |
| Debt token | "What do you want to borrow?" | WETH, USDC, etc. Must be available as a target vault on Euler/Aave. |
| Protocol | "Euler or Aave?" | Euler (default, `--vault-type 0`) supports more pairs. Aave (`--vault-type 1`) requires `--target-asset`. |
| Deposit amount | "How much collateral are you depositing?" | In human units (e.g. `2.0` = 2 wstETH). |
| Borrow amount | "Do you want to borrow immediately, or just deposit for now?" | Optional. Can borrow later via `tx collateral borrow`. |
| Liquidation LTV | "What liquidation LTV do you want?" | Range: the minimum is roughly `extLiqLTV * buffer / 10000` (protocol-dependent), the max is shown by `protocol overview` (typically 95-98%). Higher = more leverage but closer to liquidation. 85% (8500 bps) is a moderate default. Explain: "At 85% liqLTV, you'll be liquidated if your debt reaches 85% of your collateral value. Higher means more leverage but less safety margin." |

**Credit LP deposit — required parameters:**

| Parameter | If missing, ask | Options / Guidance |
|-----------|----------------|-------------------|
| Token | "What token are you depositing?" | WETH, wstETH, or Aave aTokens. Must match an available intermediate vault. |
| Protocol | "Euler or Aave?" | Determines which wrapper contract is used. |
| Amount | "How much?" | In human units. |

**Closing a position — required parameters:**

| Parameter | If missing, ask | Options / Guidance |
|-----------|----------------|-------------------|
| Vault address | "What's your vault address?" | They can find it via `twyne vault list` or `twyne user <wallet>`. |
| Slippage | Usually fine at default (1.0%) | Only ask if they mention slippage concern. |

**Checking health — required parameters:**

| Parameter | If missing, ask |
|-----------|----------------|
| Vault address | "What's your vault address? Run `twyne vault list` or `twyne user <wallet>` to find it." |

## Setup

```bash
uv sync                              # install dependencies
uv run twyne config set-rpc <url>    # persist RPC (or export RPC_URL=<url>)
```

Transaction commands need a signer: `--account <alias>` (Ape keyfile) or `--private-key <key>` (or `$PRIVATE_KEY` env var).

## Command Quick Reference

### Read
```bash
twyne vault health <vault>           # health factors + risk level
twyne vault info <vault>             # full vault state (collateral, debt, LTV, HFs)
twyne vault list                     # all collateral vaults
twyne protocol overview              # all collateral assets, IVs, max LTVs, buffers
twyne protocol rates <address>       # params for specific collateral asset or IV
twyne user <wallet>                  # all vaults owned by wallet
```

### Transactions — Positions
```bash
# Open (atomic: create vault + deposit + optional borrow)
twyne tx factory open-position <iv> <target-vault> --deposit <amt> [--borrow <amt>] --ltv <bps> --account <key>
  # --vault-type 0 = Euler (default), 1 = Aave
  # --deposit is in underlying units (WETH, wstETH), not receipt tokens
  # For Aave: add --vault-type 1 --target-asset <debt-token>

# Close (atomic: deleverage + withdraw all)
twyne tx operators close-position <vault> [--slippage 1.0] --account <key>
```

### Transactions — Vault Operations
```bash
twyne tx collateral deposit <vault> <amount>              # deposit vault asset (eVault shares)
twyne tx collateral deposit-underlying <vault> <amount>   # deposit raw underlying (e.g. WETH)
twyne tx collateral withdraw <vault> <amount>
twyne tx collateral redeem-underlying <vault> <amount>
twyne tx collateral borrow <vault> <amount>               # borrow from external protocol
twyne tx collateral repay <vault> <amount>                # "max" to repay all
twyne tx collateral set-ltv <vault> <ltv-bps>             # change liquidation LTV
twyne tx collateral liquidate <vault>                     # liquidate unhealthy vault
```

### Transactions — Credit LP
```bash
twyne tx credit deposit <iv> <amount> --protocol euler    # deposit underlying into IV
twyne tx credit deposit-atokens <iv> <amount>             # deposit Aave aTokens
twyne tx credit withdraw <iv> <amount>
twyne tx credit redeem <iv> <shares>
```

### Transactions — Operators
```bash
twyne tx operators leverage <vault> <amount>              # flash loan + swap to increase position
twyne tx operators deleverage <vault> <amount> [--slippage 1.0]
twyne tx operators teleport <source-vault> <target-vault>
```

### Transactions — Migration
```bash
twyne tx discover-positions <wallet>                      # find migratable Euler/Aave positions
twyne tx migrate-position <wallet> --position <n> --ltv <bps>
```

### Transactions — Batch
```bash
twyne tx batch execute <file.yaml>     # execute EVC batch from file
twyne tx batch simulate <file.yaml>    # simulate only
```

All `tx` commands accept: `--account`, `--private-key`, `--dry-run`, `--yes`, `--raw`, `--max-approve`, `--gas-multiplier`, `--priority-fee`, `--gas-limit`.

### Global Flags
`--rpc <url>`, `--json`, `--block <num>`, `--no-cache`, `-v` (verbose).

## Known Intermediate Vaults (Mainnet)

| Name | Address | Collateral | Protocol |
|------|---------|------------|----------|
| euler_eWETH | `0x87b8081A3ace680f35125F469526Ac10f5418Ca7` | WETH | Euler |
| euler_ewstETH | `0x7613D202Af490c3d1cE1873b0a7022a34E89815f` | wstETH | Euler |
| aave_awstETH | `0x75029a47f28550C93Ad5A3BbD2d9b5315204B561` | wstETH | Aave |

## Key Facts

- Amounts are human-readable by default (1.0 = 1 token). `--raw` for wei.
- LTV is in basis points: 8500 = 85%, 9300 = 93%.
- The CLI auto-handles ERC20 approvals. `--max-approve` for unlimited.
- `--dry-run` simulates via eth_call without submitting.
- Health factor < 1.0 = liquidatable. Twyne liquidation is inheritance-style (vault ownership transfers to liquidator).
- WETH users may need to wrap ETH first: `cast send 0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2 --value <amt>ether 'deposit()'`.

## Troubleshooting

- **E_TransferFromFailed**: Wrong token or insufficient balance. Check you hold the deposit token (not just ETH).
- **Gas estimation failed**: Use `--gas-limit 5000000` to override.
- **Simulation passed, tx reverts**: Interest accrual between sim and execution. close-position handles this automatically.
