"""Shared CLI context — extracted to avoid circular imports."""

import click
from ape import networks


class TwyneContext:
    """Shared context passed to all subcommands."""

    def __init__(self, rpc_url: str | None, force_json: bool, block: int | None, no_cache: bool = False):
        self.rpc_url = rpc_url
        self.force_json = force_json
        self.block = block
        self.no_cache = no_cache
        self._provider_ctx = None

    def connect(self):
        """Enter Ape network context.

        If --rpc or $RPC_URL is set, connects to that endpoint.
        Otherwise, uses Ape's built-in default provider (MEV Blocker RPC).
        """
        # Suppress ape's INFO logging (uses ClickHandler → stdout, breaks JSON pipe)
        from ape.logging import logger as ape_logger
        ape_logger.set_level("WARNING")

        if self.rpc_url:
            self._provider_ctx = networks.ethereum.mainnet.use_provider(
                "node", provider_settings={"uri": self.rpc_url}
            )
        else:
            self._provider_ctx = networks.ethereum.mainnet.use_default_provider()
        self._provider_ctx.__enter__()
        return self

    def disconnect(self):
        """Exit Ape network context."""
        if self._provider_ctx:
            self._provider_ctx.__exit__(None, None, None)

    def resolve_block(self) -> int | None:
        """Return the block identifier to use (None = latest)."""
        return self.block


pass_ctx = click.make_pass_decorator(TwyneContext)
