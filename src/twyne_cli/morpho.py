"""Twyne-on-Morpho helpers: protocol detection, market registry, flash-loan cap.

Morpho values are never quoted in USD on chain. The HealthStatViewer and the
vault itself price a Morpho position in the market's loan token (USDG on
Arbitrum), scaled to 1e18. Callers must label that unit.
"""

from __future__ import annotations

from dataclasses import dataclass

from .chains import active_chain
from .contracts import _load_addresses, get_address, vault_manager

MORPHO_LLTV_TO_BPS = 10**14  # LLTV is 1e18-scaled; bps = lltv / 1e14


@dataclass(frozen=True)
class MorphoMarket:
    name: str
    id: str
    intermediate_vault: str
    loan_token: str
    collateral_token: str
    oracle: str
    irm: str
    lltv: int

    @property
    def params(self) -> tuple:
        """MarketParams tuple in struct order (loanToken, collateralToken, oracle, irm, lltv)."""
        return (self.loan_token, self.collateral_token, self.oracle, self.irm, self.lltv)

    @property
    def lltv_bps(self) -> int:
        return self.lltv // MORPHO_LLTV_TO_BPS


def detect_protocol(cv, block=None) -> str:
    """Return 'morpho', 'aave' or 'euler' for a collateral vault.

    Morpho: targetVault() is the chain's Morpho singleton. Aave: aToken() is
    non-zero. Euler: aToken() reverts. Only a revert counts as "not this family";
    an RPC/transport error propagates, so a vault is never misclassified silently.
    """
    from ape.exceptions import ContractLogicError

    chain = active_chain()
    if chain.supports_morpho:
        singleton = get_address("morpho")
        target = str(cv.targetVault(block_identifier=block))
        if singleton and target.lower() == singleton.lower():
            return "morpho"
    try:
        atoken = cv.aToken(block_identifier=block)
    except ContractLogicError:
        return "euler"
    try:
        is_aave = bool(atoken) and int(str(atoken), 16) != 0
    except ValueError:  # not an address (e.g. a test double): not Aave
        is_aave = False
    return "aave" if is_aave else "euler"


def registered_markets() -> list[MorphoMarket]:
    """Morpho markets listed in the active chain's address registry."""
    out = []
    for name, m in _load_addresses().get("morphoMarkets", {}).items():
        out.append(MorphoMarket(
            name=name,
            id=m["id"],
            intermediate_vault=m["intermediateVault"],
            loan_token=m["loanToken"],
            collateral_token=m["collateralToken"],
            oracle=m["oracle"],
            irm=m["irm"],
            lltv=int(m["lltv"]),
        ))
    return out


def allowed_markets(iv: str, block=None, vm=None) -> list[MorphoMarket]:
    """Registry markets for ``iv`` that the VaultManager currently allows on chain."""
    manager = vm if vm is not None else vault_manager()
    singleton = get_address("morpho")
    return [
        m for m in registered_markets()
        if m.intermediate_vault.lower() == iv.lower()
        and manager.isAllowedMorphoMarket(singleton, iv, m.id, block_identifier=block)
    ]


def resolve_market(iv: str, market_id: str | None = None, block=None) -> MorphoMarket:
    """Pick the market for ``iv``: the given id, or the IV's only allowed market."""
    import click

    markets = allowed_markets(iv, block)
    if market_id:
        for m in markets:
            if m.id.lower() == market_id.lower():
                return m
        raise click.UsageError(
            f"Morpho market {market_id} is not an allowed market for intermediate vault {iv}."
        )
    if len(markets) == 1:
        return markets[0]
    if not markets:
        raise click.UsageError(f"No allowed Morpho market is registered for intermediate vault {iv}.")
    ids = ", ".join(m.id for m in markets)
    raise click.UsageError(f"Intermediate vault {iv} has several Morpho markets; pass --market-id ({ids}).")


def market_for_vault(cv) -> MorphoMarket:
    """Build the market of an existing Morpho collateral vault from its own marketParams()."""
    mp = cv.marketParams()
    return MorphoMarket(
        name="",
        id=_hex(cv.marketId()),
        intermediate_vault=str(cv.intermediateVault()),
        loan_token=str(mp[0]),
        collateral_token=str(mp[1]),
        oracle=str(mp[2]),
        irm=str(mp[3]),
        lltv=int(mp[4]),
    )


def check_flashloan_cap(token: str, amount: int, token_symbol: str, decimals: int) -> None:
    """Refuse a Morpho flash loan larger than the singleton's balance of ``token``."""
    import click

    from .contracts import erc20

    available = int(erc20(token).balanceOf(get_address("morpho")))
    if amount > available:
        raise click.UsageError(
            f"Flash loan of {amount / 10**decimals:,.6f} {token_symbol} exceeds the "
            f"{available / 10**decimals:,.6f} {token_symbol} that Morpho holds on {active_chain().name}. "
            "Use a smaller amount."
        )


def _hex(value) -> str:
    if isinstance(value, (bytes, bytearray)):
        return "0x" + bytes(value).hex()
    s = str(value)
    return s if s.startswith("0x") else "0x" + s
