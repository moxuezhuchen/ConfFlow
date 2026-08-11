"""Tests for the versioned workflow-configuration contract."""

from __future__ import annotations

import io
import json
from contextlib import redirect_stdout

from confflow.config_contract import contract_payload, main, validate_mapping, workflow_schema_hash


def test_contract_is_deterministic_and_has_a_separate_schema_identity() -> None:
    payload = contract_payload()
    assert payload["response_schema"] == "confflow.config.contract.v1"
    assert payload["workflow_schema"]["version"] == "v1"
    assert payload["workflow_schema"]["sha256"] == workflow_schema_hash()
    assert payload["workflow_schema"]["sha256"] != ""
    assert payload["producer"]["configuration_contract"] == "1.0"


def test_validate_mapping_is_environment_independent() -> None:
    result = validate_mapping({"global": {}, "steps": [{"type": "calc", "params": {"keyword": "B3LYP"}}]})
    assert result["valid"] is True
    assert result["issues"] == []

    invalid = validate_mapping({"steps": "not-a-list"})
    assert invalid["valid"] is False
    assert invalid["issues"][0]["code"] == "configuration_invalid"


def test_contract_cli_emits_one_json_document() -> None:
    output = io.StringIO()
    with redirect_stdout(output):
        assert main(["contract", "--json"]) == 0
    parsed = json.loads(output.getvalue())
    assert parsed["response_schema"] == "confflow.config.contract.v1"
