"""Extract the read-only ABI subsets for the credit~loan V2 and pooled contracts.

Usage: python scripts/sync_v2_abis.py <base build> <pooled build> [<pooled build> ...]
                                       [--periphery <periphery build>] [--only <File.json>]
       python scripts/sync_v2_abis.py --periphery <periphery build> --only CreditLoanV2Lens.json

<base build> is a twyne-contracts checkout with a built out/ of the per-vault V2 base (vault,
manager). Each <pooled build> is one pooled option (Option A and/or B); the pooled views are the
union of all given builds, and the CLI treats a view missing on the deployed option as absent.
<periphery build> is a checkout of `credit-loan-v2-periphery` (or `-periphery-direct`: the periphery
files are the same on both) with a built out/. It supplies the lens ABI (CreditLoanV2Lens; the Aave
lens has the same ABI). `--only` rewrites one file; the base and pooled builds are then not needed.

The CLI reads V2 state only, so each file keeps view functions (plus the events/errors of none).
Source commit is printed; record it in the commit message when the ABIs change.
"""

import argparse
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
        # account and ledger views served by the extension (second fallback() delegatecall)
        ("pooled", "PooledLongTailExtension.sol/PooledLongTailExtension.json", None),
    ],
    "PooledResidualAccount.json": [("pooled", "PooledResidualAccount.sol/PooledResidualAccount.json", None)],
    "PooledParamsRegistry.json": [("pooled", "PooledParamsRegistry.sol/PooledParamsRegistry.json", None)],
    # CreditLoanV2AaveLens has the identical ABI: one file serves both lenses
    "CreditLoanV2Lens.json": [("periphery", "CreditLoanV2Lens.sol/CreditLoanV2Lens.json", None)],
    "VaultManagerV2.json": [
        ("base", "VaultManagerV2.sol/VaultManagerV2.json", {
            "boost", "rebalanceFee", "MAX_REBALANCE_FEE", "liqParams",
            "borrowBuffer", "settlementConverter", "settlementConverterTolerance",
        })
    ],
}


def main(base: Path | None, pooled: list[Path], periphery: Path | None, only: str | None) -> None:
    roots = {"base": [base] if base else [], "pooled": pooled, "periphery": [periphery] if periphery else []}
    for name, sources in SPECS.items():
        if only is not None and name != only:
            continue
        merged: dict[str, dict] = {}
        for role, rel, keep in sources:
            if not roots[role]:
                sys.exit(f"{name} needs a {role} build")
            for root in roots[role]:
                abi = json.loads((root / "out" / rel).read_text())["abi"]
                for e in abi:
                    if e["type"] != "function" or e["stateMutability"] not in ("view", "pure"):
                        continue
                    if keep is not None and e["name"] not in keep:
                        continue
                    merged.setdefault(e["name"] + str(len(e["inputs"])), e)
        (OUT_DIR / name).write_text(json.dumps(sorted(merged.values(), key=lambda e: e["name"]), indent=2) + "\n")
        print(f"{name}: {len(merged)} functions")
    for root in [r for rs in roots.values() for r in rs]:
        try:
            commit = subprocess.check_output(["git", "-C", str(root), "rev-parse", "HEAD"], text=True, stderr=subprocess.DEVNULL).strip()
        except subprocess.CalledProcessError:
            commit = "unknown (not a git checkout: record the branch commit yourself)"
        print("source commit:", root, commit)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("builds", nargs="*", type=Path, help="<base build> <pooled build> [<pooled build> ...]")
    parser.add_argument("--periphery", type=Path, help="periphery build (lens ABI)")
    parser.add_argument("--only", help="rewrite one ABI file only, e.g. CreditLoanV2Lens.json")
    a = parser.parse_args()
    if a.only is not None and a.only not in SPECS:
        parser.error(f"--only must be one of {sorted(SPECS)}")
    needs_builds = a.only is None or any(r in ("base", "pooled") for r, _, _ in SPECS[a.only])
    if needs_builds and len(a.builds) < 2:
        parser.error("need <base build> and at least one <pooled build>")
    main(a.builds[0] if a.builds else None, a.builds[1:], a.periphery, a.only)
