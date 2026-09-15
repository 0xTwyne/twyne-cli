# Contract compatibility

Mainnet uses the 1.0.7 interfaces. MegaETH retains its legacy ABI, generic factory call, and underlying deposit method [1].

On mainnet, create-vault, open-position, and migration use the typed Euler or Aave factory method. The first argument is the intermediate vault.

An underlying deposit approves AssetZap. One EVC batch calls AssetZap and then skim. The quote uses receipt shares with a 10-basis-point tolerance. Open-position places vault creation before these calls, and the optional debt call after them.

The mainnet risk key is the intermediate vault plus the debt asset. Protocol overview and ext-ltvs return one row per permitted pair. Rates returns a pairs array. Rates includes scalar risk fields only when the IV has one permitted pair. Scripts must select the debt asset from that array. The October USDC and USDT pairs have different risk limits from October USDe [2].

The open-position dry run simulates the full batch. Underlying deposit and open-position dry runs require an existing token allowance. They do not send an approval. The normal commands retain automatic approval.

## ABI sources

The VaultManager, factory, Euler CV, and Aave CV ABIs come from the verified 1.0.7 deployment. The shared CV ABI includes both vault types. AssetZap uses the contract artifact from the same repository. The legacy ABIs retain the CLI main revision b33ed83. The address registry follows the reviewed address records, including the replacement HealthStatViewer [1, 2, 3].

## Validation

The integration fixture pins mainnet block 25982234. It checks the deployed pair values without changing them. It raises the Euler IV supply cap only on the local fork for repeated test deposits.

Set ANVIL_RPC_URL to an isolated local fork. Run uv run pytest tests/integration/test_compatibility_107.py. These tests execute the CLI for Euler and Aave, check the October pair values, and simulate each October factory call.

## References

1. [Contracts 1.0.7](https://github.com/0xTwyne/twyne-contracts/tree/1.0.7); MegaETH live getter check on September 15 and the CLI legacy registry.
2. [October address record](https://github.com/0xTwyne/tech-notes/pull/11).
3. [Upgrade address record](https://github.com/0xTwyne/tech-notes/pull/10).
