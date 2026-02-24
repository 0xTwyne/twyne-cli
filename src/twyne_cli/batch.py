"""EVC batch file parser -- YAML/JSON to BatchItem[] to EVC.batch() call."""

import json
from pathlib import Path

import yaml

from .contracts import collateral_vault, erc20
from .transactions import parse_amount


def parse_batch_file(filepath: str) -> dict:
    """Parse a YAML or JSON batch file.

    Returns: dict with 'evc' (optional) and 'operations' (list of dicts).
    """
    path = Path(filepath)
    content = path.read_text()

    if path.suffix in (".yaml", ".yml"):
        data = yaml.safe_load(content)
    elif path.suffix == ".json":
        data = json.loads(content)
    else:
        try:
            data = yaml.safe_load(content)
        except yaml.YAMLError:
            data = json.loads(content)

    validate_batch(data)
    return data


def validate_batch(data: dict):
    """Validate batch file schema."""
    if not isinstance(data, dict):
        raise ValueError("Batch file must be a YAML/JSON object")

    if "operations" not in data:
        raise ValueError("Batch file must contain 'operations' list")

    ops = data["operations"]
    if not isinstance(ops, list) or len(ops) == 0:
        raise ValueError("'operations' must be a non-empty list")

    for i, op in enumerate(ops):
        if "action" not in op:
            raise ValueError(f"Operation {i} missing 'action' field")


def encode_operation(op: dict, on_behalf_of: str, block=None) -> dict:
    """Encode a single batch operation into a BatchItem tuple.

    Returns: dict with targetContract, onBehalfOfAccount, value, data.
    """
    action = op["action"]
    parts = action.split(".")

    if parts[0] == "collateral":
        return _encode_collateral_op(parts[1], op, on_behalf_of, block)
    elif parts[0] == "token":
        return _encode_token_op(parts[1], op, on_behalf_of, block)
    else:
        raise ValueError(f"Unknown action namespace: {parts[0]}")


def _encode_collateral_op(fn_name: str, op: dict, on_behalf_of: str, block=None) -> dict:
    """Encode a collateral vault operation."""
    vault_addr = op["vault"]
    cv = collateral_vault(vault_addr)

    asset_addr = cv.asset(block_identifier=block)
    token = erc20(asset_addr)
    decimals = token.decimals(block_identifier=block)

    if fn_name == "deposit":
        raw_amount = parse_amount(op["amount"], decimals)
        data = cv.deposit.encode_input(raw_amount)
    elif fn_name == "borrow":
        raw_amount = parse_amount(op["amount"], decimals)
        receiver = op.get("receiver", on_behalf_of)
        data = cv.borrow.encode_input(raw_amount, receiver)
    elif fn_name == "withdraw":
        raw_amount = parse_amount(op["amount"], decimals)
        receiver = op.get("receiver", on_behalf_of)
        data = cv.withdraw.encode_input(raw_amount, receiver)
    elif fn_name == "repay":
        raw_amount = parse_amount(op["amount"], decimals)
        data = cv.repay.encode_input(raw_amount)
    elif fn_name == "set_ltv":
        ltv = int(op["ltv"])
        data = cv.setTwyneLiqLTV.encode_input(ltv)
    else:
        raise ValueError(f"Unknown collateral action: {fn_name}")

    return {
        "targetContract": vault_addr,
        "onBehalfOfAccount": on_behalf_of,
        "value": 0,
        "data": data,
    }


def _encode_token_op(fn_name: str, op: dict, on_behalf_of: str, block=None) -> dict:
    """Encode a token operation (approve)."""
    token_addr = op["token"]
    token = erc20(token_addr)
    decimals = token.decimals(block_identifier=block)

    if fn_name == "approve":
        raw_amount = parse_amount(op["amount"], decimals)
        spender = op["spender"]
        data = token.approve.encode_input(spender, raw_amount)
    else:
        raise ValueError(f"Unknown token action: {fn_name}")

    return {
        "targetContract": token_addr,
        "onBehalfOfAccount": on_behalf_of,
        "value": 0,
        "data": data,
    }


def build_batch_items(batch_data: dict, on_behalf_of: str, block=None) -> list[dict]:
    """Build list of BatchItem dicts from parsed batch file."""
    return [encode_operation(op, on_behalf_of, block) for op in batch_data["operations"]]


def collect_approval_requirements(batch_data: dict, on_behalf_of: str, block=None) -> list[dict]:
    """Analyze batch operations and return required token approvals.

    Scans operations that pull tokens from the user and returns a deduplicated
    list of {token, spender, amount, reason} dicts. Skips (token, spender) pairs
    that already have an explicit token.approve action in the batch.

    Returns list of dicts: {token, spender, amount, reason}.
    """
    # First pass: collect explicit approvals already in the batch
    explicit_approvals: set[tuple[str, str]] = set()
    for op in batch_data["operations"]:
        if op["action"] == "token.approve":
            explicit_approvals.add((op["token"].lower(), op["spender"].lower()))

    # Second pass: collect requirements from token-pulling operations
    requirements: dict[tuple[str, str], dict] = {}
    for op in batch_data["operations"]:
        action = op["action"]
        parts = action.split(".")
        if parts[0] != "collateral" or len(parts) < 2:
            continue

        fn_name = parts[1]
        if fn_name not in ("deposit", "deposit_underlying", "repay"):
            continue

        vault_addr = op["vault"]
        cv = collateral_vault(vault_addr)

        if fn_name == "deposit":
            token_addr = cv.asset(block_identifier=block)
        elif fn_name == "deposit_underlying":
            token_addr = cv.underlyingAsset(block_identifier=block)
        elif fn_name == "repay":
            token_addr = cv.targetAsset(block_identifier=block)
        else:
            continue

        # Skip if batch already has an explicit approval for this pair
        key = (token_addr.lower(), vault_addr.lower())
        if key in explicit_approvals:
            continue

        token = erc20(token_addr)
        decimals = token.decimals(block_identifier=block)
        raw_amount = parse_amount(op["amount"], decimals)

        if key in requirements:
            requirements[key]["amount"] += raw_amount
        else:
            symbol = token.symbol(block_identifier=block)
            requirements[key] = {
                "token": token_addr,
                "spender": vault_addr,
                "amount": raw_amount,
                "reason": f"{fn_name} {symbol} to {vault_addr[:10]}...",
            }

    return list(requirements.values())
