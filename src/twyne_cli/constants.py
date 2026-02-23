"""Protocol-level constants for the Twyne CLI."""

# Basis point precision for LTV values (1e4 = 100%)
MAXFACTOR = 10_000

# 1e18 precision for health factors, oracle prices (Euler), utilization
WAD = 10**18

# Sentinel address for USD denomination in oracle queries
USD_ADDRESS = "0x0000000000000000000000000000000000000348"

# Zero address — used as default for optional address parameters
ZERO_ADDRESS = "0x0000000000000000000000000000000000000000"

# Aave V3 Pool (mainnet) — used for Aave vault position data
AAVE_V3_POOL = "0x87870Bca3F3fD6335C3F4ce8392D69350B4fA4E2"

# 1inch API
ONEINCH_API_BASE = "https://api.1inch.dev/swap/v6.0"
ONEINCH_CHAIN_ID = 1  # Ethereum mainnet

# Default slippage for swap operations (0.5%)
DEFAULT_SLIPPAGE = 0.5

# Default deadline offset for swap operations (20 minutes)
DEFAULT_DEADLINE_OFFSET = 20 * 60
