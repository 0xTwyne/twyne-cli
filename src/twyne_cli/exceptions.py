"""Domain exceptions raised by the CLI."""

from __future__ import annotations


class ChainNotSupportedError(Exception):
    """Raised when --chain resolves to a chain id or slug not in the registry."""

    def __init__(self, value):
        from .chains import supported_slugs

        self.value = value
        super().__init__(
            f"Chain '{value}' is not supported. Supported: {', '.join(supported_slugs())} "
            f"(or pass a numeric chain id)."
        )


class ChainCapabilityError(Exception):
    """Raised when a feature is invoked on a chain that doesn't support it.

    Caught at the command boundary and converted to click.UsageError for clean UX.
    """

    def __init__(self, chain, feature: str, hint: str | None = None):
        self.chain = chain
        self.feature = feature
        self.hint = hint
        msg = f"{feature} is not supported on {chain.name} (chain {chain.chain_id})."
        if hint:
            msg += f" {hint}"
        super().__init__(msg)


class OperatorsNotSupportedError(ChainCapabilityError):
    """Operators (leverage/deleverage/teleport) not deployed on this chain."""

    def __init__(self, chain):
        super().__init__(
            chain,
            "Operator commands",
            hint="Operators have not been deployed on this chain yet.",
        )


class EulerNotSupportedError(ChainCapabilityError):
    """Euler-protocol contracts (wrapper, EVC, IVs) absent on this chain."""

    def __init__(self, chain):
        super().__init__(
            chain,
            "Euler protocol",
            hint="This deployment is Aave-only. Use --protocol aave.",
        )
