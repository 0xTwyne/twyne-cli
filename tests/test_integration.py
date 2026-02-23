"""Integration tests requiring Anvil fork.

Start Anvil first:
    anvil --fork-url <RPC_URL> --fork-block-number 24326520 --port 8454
"""

import pytest
from click.testing import CliRunner


@pytest.mark.integration
class TestCollateralVaultRead:
    """Test that collateral vault commands work against a forked chain."""

    def test_deposit_dry_run(self, anvil_fork):
        """Dry-run deposit should simulate without submitting."""
        # This test validates the full pipeline except actual submission
        # Requires a real collateral vault address on mainnet
        pass  # TODO: populate with real vault address after confirming fork block

    def test_batch_simulate(self, anvil_fork):
        """Batch simulation should run against the fork."""
        pass  # TODO: populate with batch file
