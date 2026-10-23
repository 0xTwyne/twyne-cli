"""Extract the read-only ABI subsets for the credit~loan V2 and pooled contracts.

Usage: python scripts/sync_v2_abis.py <twyne-contracts worktree with a built out/>

The CLI reads V2 state only, so each file keeps view functions (plus the events/errors of none).
Source commit is printed; record it in the commit message when the ABIs change.
"""

import json
import subprocess
import sys
from pathlib import Path

OUT_DIR = Path(__file__).resolve().parent.parent / "src" / "twyne_cli" / "abis"

# target file -> [(artifact path under out/, function-name filter or None for all views)]
SPECS = {
    "CollateralVaultV2.json": [("EulerCollateralVaultV2.sol/EulerCollateralVaultV2.json", None)],
    "PooledLongTailVault.json": [
        ("PooledLongTailVaultV2.sol/PooledLongTailVaultV2.json", None),
        # reached through the pool's fallback() delegatecall; not in the pool ABI
        ("PooledLongTailModule.sol/PooledLongTailModule.json", {"attributedLoss", "attributedLossReplayed", "isExcisable", "priceAge"}),
    ],
    "PooledResidualAccount.json": [("PooledResidualAccount.sol/PooledResidualAccount.json", None)],
    "PooledParamsRegistry.json": [("PooledParamsRegistry.sol/PooledParamsRegistry.json", None)],
    "VaultManagerV2.json": [
        ("VaultManagerV2.sol/VaultManagerV2.json", {"boost", "rebalanceFee", "collateralPriceMaxAge", "priceFreshnessReader", "liqParams"})
    ],
}


def main(contracts: Path) -> None:
    for name, sources in SPECS.items():
        merged: dict[str, dict] = {}
        for rel, keep in sources:
            abi = json.loads((contracts / "out" / rel).read_text())["abi"]
            for e in abi:
                if e["type"] != "function" or e["stateMutability"] not in ("view", "pure"):
                    continue
                if keep is not None and e["name"] not in keep:
                    continue
                merged.setdefault(e["name"] + str(len(e["inputs"])), e)
        (OUT_DIR / name).write_text(json.dumps(sorted(merged.values(), key=lambda e: e["name"]), indent=2) + "\n")
        print(f"{name}: {len(merged)} functions")
    print("source commit:", subprocess.check_output(["git", "-C", str(contracts), "rev-parse", "HEAD"], text=True).strip())


if __name__ == "__main__":
    main(Path(sys.argv[1]))
