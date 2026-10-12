"""HealthStatViewer reads — health factors and the position bundle for any vault family.

The lens returns values 1e18-scaled. For Euler and Aave vaults the value unit is
USD. For Morpho vaults it is the market loan token (USDG on Arbitrum), priced
with the market oracle; ``value_unit`` names it so output can label it.
"""

from __future__ import annotations

from .contracts import erc20, health_stat_viewer

WAD = 10**18

POSITION_FIELDS = (
    "user_collateral_native", "user_collateral_value", "reserved_credit_native", "reserved_credit_value",
    "borrow_native", "borrow_value", "twyne_liq_ltv_bps", "twyne_ltv_bps", "external_liq_ltv_bps",
    "external_ltv_bps", "ext_hf_raw", "in_hf_raw", "max_twyne_liq_ltv_bps",
)


def value_unit(protocol: str, cv=None, block=None) -> str:
    """'USD' for Euler/Aave; the loan-token symbol for Morpho."""
    if protocol != "morpho" or cv is None:
        return "USD"
    try:
        return str(erc20(str(cv.targetAsset(block_identifier=block))).symbol(block_identifier=block))
    except Exception:
        return "loan token"


def health(cv_address: str, block=None) -> dict:
    """health(): extHF, inHF (1e18) and the two debt values (1e18, value unit)."""
    ext_hf, in_hf, ext_debt, in_debt = health_stat_viewer().health(cv_address, block_identifier=block)
    return {
        "ext_hf_raw": int(ext_hf),
        "in_hf_raw": int(in_hf),
        "external_debt_value": int(ext_debt) / WAD,
        "internal_debt_value": int(in_debt) / WAD,
    }


def position_stats(cv_address: str, block=None) -> dict:
    """positionStats() as a dict. *_value fields are already divided by 1e18."""
    raw = health_stat_viewer().positionStats(cv_address, block_identifier=block)
    out = dict(zip(POSITION_FIELDS, (int(x) for x in raw)))
    for key in ("user_collateral_value", "reserved_credit_value", "borrow_value"):
        out[key] = out[key] / WAD
    return out
