"""Shared CLI context — extracted to avoid circular imports."""

import click

from .chains import CHAINS, ChainSpec


class TwyneContext:
    """Shared context passed to all subcommands."""

    def __init__(
        self,
        rpc_url: str | None,
        force_json: bool,
        block: int | None,
        no_cache: bool = False,
        verbose: bool = False,
        chain: ChainSpec | None = None,
    ):
        self.rpc_url = rpc_url
        self.force_json = force_json
        self.block = block
        self.no_cache = no_cache
        self.verbose = verbose
        self.chain = chain or CHAINS[1]
        self._provider_ctx = None

    def connect(self):
        """Enter Ape network context for the active chain.

        Mainnet: uses Ape's built-in `ethereum.mainnet` (default provider when no
        rpc_url, otherwise `node` with the explicit URI).

        Custom chains (e.g., MegaETH): registered as Ape custom networks via
        `ape-config.yaml`; addressed as `networks.ethereum.<slug>` and always
        require an explicit RPC URI (no built-in default provider).
        """
        from ape import networks
        from ape.logging import logger as ape_logger
        ape_logger.set_level("DEBUG" if self.verbose else "WARNING")

        ecosystem = getattr(networks, self.chain.ape_ecosystem)
        try:
            network = getattr(ecosystem, self.chain.ape_network)
        except AttributeError as e:
            raise click.ClickException(
                f"Ape does not know about network '{self.chain.ape_network}' on ecosystem "
                f"'{self.chain.ape_ecosystem}'. Custom networks are registered in ape-config.yaml — "
                f"verify the entry for chain id {self.chain.chain_id} exists."
            ) from e

        if self.rpc_url:
            self._provider_ctx = network.use_provider(
                "node", provider_settings={"uri": self.rpc_url}
            )
        elif self.chain.chain_id == 1:
            self._provider_ctx = network.use_default_provider()
        else:
            raise click.ClickException(
                f"No RPC URL configured for {self.chain.name} (chain {self.chain.chain_id}). "
                f"Set ${self.chain.env_var}, pass --rpc, or run "
                f"`twyne config set-rpc <url> --chain {self.chain.slug}`."
            )
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
