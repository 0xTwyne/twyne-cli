"""Protocol-level constants for the Twyne CLI."""

# Basis point precision for LTV values (1e4 = 100%)
MAXFACTOR = 10_000

# 1e18 precision for health factors, oracle prices (Euler), utilization
WAD = 10**18

# Sentinel address for USD denomination in oracle queries
USD_ADDRESS = "0x0000000000000000000000000000000000000348"

# Aave V3 Pool (mainnet) — used for Aave vault position data
AAVE_V3_POOL = "0x87870Bca3F3fD6335C3F4ce8392D69350B4fA4E2"
