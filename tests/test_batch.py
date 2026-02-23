"""Tests for EVC batch file parsing."""

import json

import pytest
import yaml


SAMPLE_BATCH = {
    "evc": "0xef39D6493884C4C84D38a4bFF879Ce16CEdE702a",
    "operations": [
        {
            "action": "collateral.deposit",
            "vault": "0x1111111111111111111111111111111111111111",
            "amount": "1.5",
        },
        {
            "action": "collateral.borrow",
            "vault": "0x1111111111111111111111111111111111111111",
            "amount": "1000",
            "receiver": "0x2222222222222222222222222222222222222222",
        },
    ],
}


def test_parse_batch_file_yaml(tmp_path):
    """Parse a YAML batch file into operations."""
    from twyne_cli.batch import parse_batch_file
    f = tmp_path / "batch.yaml"
    f.write_text(yaml.dump(SAMPLE_BATCH))
    result = parse_batch_file(str(f))
    assert result["evc"] == "0xef39D6493884C4C84D38a4bFF879Ce16CEdE702a"
    assert len(result["operations"]) == 2
    assert result["operations"][0]["action"] == "collateral.deposit"


def test_parse_batch_file_json(tmp_path):
    """Parse a JSON batch file into operations."""
    from twyne_cli.batch import parse_batch_file
    f = tmp_path / "batch.json"
    f.write_text(json.dumps(SAMPLE_BATCH))
    result = parse_batch_file(str(f))
    assert len(result["operations"]) == 2


def test_validate_batch_missing_operations():
    """Validate batch schema rejects missing operations."""
    from twyne_cli.batch import validate_batch
    with pytest.raises(ValueError, match="operations"):
        validate_batch({"evc": "0x..."})


def test_validate_batch_missing_action():
    """Validate batch schema rejects missing action field."""
    from twyne_cli.batch import validate_batch
    with pytest.raises(ValueError, match="action"):
        validate_batch({"operations": [{"vault": "0x..."}]})
