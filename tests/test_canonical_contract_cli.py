"""Regression tests for the producer-owned configuration contract CLI."""

from __future__ import annotations

import io
import json

from confflow.config import cli as config_cli
from confflow.config.canonical.contract import build_configuration_contract
from confflow.config.canonical.schema import workflow_json_schema, workflow_schema_sha256
from confflow.config.canonical.serialization import canonical_json, canonical_sha256
from confflow.core.contracts import ExitCode


def _payload(captured: str) -> dict[str, object]:
    return json.loads(captured)


def test_canonical_contract_primitives_are_deterministic_and_owned():
    first = {"b": [2, 1], "a": {"z": True}}
    second = {"a": {"z": True}, "b": [2, 1]}

    assert canonical_json(first) == canonical_json(second)
    assert canonical_sha256(first) == canonical_sha256(second)
    owned = workflow_json_schema()
    owned["properties"]["global"]["type"] = "array"
    assert workflow_json_schema()["properties"]["global"]["type"] == "object"
    assert workflow_schema_sha256() == workflow_schema_sha256()


def test_contract_binds_schema_hash_and_producer():
    contract = build_configuration_contract(
        producer_version="2.0.0-test", producer_commit="abc", producer_dirty=False
    )

    assert contract["producer"]["version"] == "2.0.0-test"
    assert contract["workflow_schema_sha256"] == workflow_schema_sha256()


def test_validate_stdin_emits_single_success_json(monkeypatch, capsys):
    monkeypatch.setattr(config_cli.sys, "stdin", io.StringIO('{"global": {}, "steps": []}'))

    assert config_cli.main(["validate", "--json", "--stdin"]) == ExitCode.SUCCESS
    assert _payload(capsys.readouterr().out)["valid"] is True


def test_validate_stdin_emits_structured_semantic_error(monkeypatch, capsys):
    monkeypatch.setattr(config_cli.sys, "stdin", io.StringIO('{"steps": [{"type": "bad"}]}'))

    assert config_cli.main(["validate", "--json", "--stdin"]) == ExitCode.USAGE_ERROR
    result = _payload(capsys.readouterr().out)
    assert result["valid"] is False
    assert result["issues"]


def test_validate_stdin_rejects_non_mapping_and_invalid_json(monkeypatch, capsys):
    monkeypatch.setattr(config_cli.sys, "stdin", io.StringIO("[]"))
    assert config_cli.main(["validate", "--json", "--stdin"]) == ExitCode.USAGE_ERROR
    assert _payload(capsys.readouterr().out)["valid"] is False

    monkeypatch.setattr(config_cli.sys, "stdin", io.StringIO("{"))
    assert config_cli.main(["validate", "--json", "--stdin"]) == ExitCode.USAGE_ERROR
    assert "invalid JSON input" in capsys.readouterr().err


def test_contract_command_emits_machine_readable_document(capsys):
    assert config_cli.main(["contract", "--json"]) == ExitCode.SUCCESS
    assert _payload(capsys.readouterr().out)["schema"] == "confflow.configuration-contract.v1"
