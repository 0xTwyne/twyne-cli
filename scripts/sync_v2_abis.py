"""Extract the read-only ABI subsets for the credit~loan V2 and pooled contracts.

Usage: python scripts/sync_v2_abis.py <base build> <pooled build> [<pooled build> ...]

<base build> is a twyne-contracts checkout with a built out/ of the per-vault V2 base (vault,
manager). Each <pooled build> is one pooled option (Option A and/or B); the pooled views are the
union of all given builds, and the CLI treats a view missing on the deployed option as absent.

The CLI reads V2 state only, so each file keeps view functions (plus the events/errors of none).
Source commit is printed; record it in the commit message when the ABIs change.
"""

import json
import subprocess
import sys
from pathlib import Path

OUT_DIR = Path(__file__).resolve().parent.parent / "src" / "twyne_cli" / "abis"

# target file -> [(role, artifact path under out/, function-name filter or None for all views)]
SPECS = {
    "CollateralVaultV2.json": [("base", "EulerCollateralVaultV2.sol/EulerCollateralVaultV2.json", None)],
    "PooledLongTailVault.json": [
        ("pooled", "PooledLongTailVaultV2.sol/PooledLongTailVaultV2.json", None),
        # reached through the pool's fallback() delegatecall; not in the pool ABI
        ("pooled", "PooledLongTailModule.sol/PooledLongTailModule.json", {"attributedLoss", "isExcisable"}),
    ],
    "PooledResidualAccount.json": [("pooled", "PooledResidualAccount.sol/PooledResidualAccount.json", None)],
    "PooledParamsRegistry.json": [("pooled", "PooledParamsRegistry.sol/PooledParamsRegistry.json", None)],
    "VaultManagerV2.json": [
        ("base", "VaultManagerV2.sol/VaultManagerV2.json", {
            "boost", "rebalanceFee", "MAX_REBALANCE_FEE", "liqParams",
            "borrowBuffer", "settlementConverter", "settlementConverterTolerance",
        })
    ],
}


def main(base: Path, pooled: list[Path]) -> None:
    for name, sources in SPECS.items():
        merged: dict[str, dict] = {}
        for role, rel, keep in sources:
            for root in [base] if role == "base" else pooled:
                abi = json.loads((root / "out" / rel).read_text())["abi"]
                for e in abi:
                    if e["type"] != "function" or e["stateMutability"] not in ("view", "pure"):
                        continue
                    if keep is not None and e["name"] not in keep:
                        continue
                    merged.setdefault(e["name"] + str(len(e["inputs"])), e)
        (OUT_DIR / name).write_text(json.dumps(sorted(merged.values(), key=lambda e: e["name"]), indent=2) + "\n")
        print(f"{name}: {len(merged)} functions")
    for root in [base, *pooled]:
        try:
            commit = subprocess.check_output(["git", "-C", str(root), "rev-parse", "HEAD"], text=True, stderr=subprocess.DEVNULL).strip()
        except subprocess.CalledProcessError:
            commit = "unknown (not a git checkout: record the branch commit yourself)"
        print("source commit:", root, commit)


if __name__ == "__main__":
    if len(sys.argv) < 3:
        sys.exit(__doc__)
    main(Path(sys.argv[1]), [Path(a) for a in sys.argv[2:]])
