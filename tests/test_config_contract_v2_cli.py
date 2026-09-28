#!/usr/bin/env python3

"""CLI tests for the configuration contract, v1 default and opt-in v2.

The important assertions here are about *not* changing things:

* ``confflow config contract --json`` must stay byte-for-byte what it was;
* ``--version 1`` must be the same bytes as the default;
* ``--version 2`` must be the only thing that adds members;
* an unknown or retired version must be refused, not accepted and silently
  treated as v1.

The v2 test deliberately goes through the real ``confflow.cli`` dispatch rather
than calling the builder, so the wiring is exercised too. The
``configuration-validation.v1`` response checks below moved here from the
retired V3 CLI-inspection tests: they pin the released V1/V2 wire.
"""

from __future__ import annotations

import io
import json

import pytest

from confflow.cli import main as cli_main
from confflow.config import cli as config_cli
from confflow.config.canonical.contract import (
    CONFIGURATION_CONTRACT_BUILDERS,
    build_configuration_contract_for_version,
)
from confflow.config.canonical.serialization import canonical_json
from confflow.core.contracts import ExitCode

EXPECTED_V1_KEYS = {
    "producer",
    "schema",
    "validation_response_schema",
    "workflow_schema",
    "workflow_schema_sha256",
    "workflow_schema_version",
}

EXPECTED_V2_ONLY_KEYS = {
    "editor_manifest",
    "editor_manifest_sha256",
    "recipe_catalog",
    "recipe_catalog_sha256",
}


def _stdout(capsys) -> str:
    captured = capsys.readouterr()
    assert captured.err == "", captured.err
    lines = [line for line in captured.out.splitlines() if line.strip()]
    assert len(lines) == 1, f"expected exactly one JSON document, got {len(lines)}"
    return captured.out


def _document(capsys) -> dict:
    return json.loads(_stdout(capsys))


class TestDefaultIsV1:
    def test_the_default_is_still_v1(self, capsys) -> None:
        assert config_cli.main(["contract", "--json"]) == ExitCode.SUCCESS

        payload = _document(capsys)
        assert payload["schema"] == "confflow.configuration-contract.v1"
        assert set(payload) == EXPECTED_V1_KEYS

    def test_the_default_has_not_gained_any_v2_member(self, capsys) -> None:
        config_cli.main(["contract", "--json"])

        payload = _document(capsys)
        assert not (EXPECTED_V2_ONLY_KEYS & set(payload))

    def test_the_output_is_canonical_json_plus_a_newline(self, capsys) -> None:
        config_cli.main(["contract", "--json"])
        emitted = _stdout(capsys)
        payload = json.loads(emitted)

        expected = build_configuration_contract_for_version(
            1,
            producer_version=payload["producer"]["version"],
            producer_commit=payload["producer"]["commit"],
            producer_dirty=payload["producer"]["dirty"],
        )
        assert emitted == canonical_json(expected) + "\n"

    def test_the_real_cli_dispatch_also_emits_v1(self, capsys) -> None:
        assert cli_main(["config", "contract", "--json"]) == ExitCode.SUCCESS
        assert _document(capsys)["schema"] == "confflow.configuration-contract.v1"


class TestExplicitV1:
    def test_version_1_is_byte_identical_to_the_default(self, capsys) -> None:
        config_cli.main(["contract", "--json"])
        default_bytes = _stdout(capsys)

        config_cli.main(["contract", "--json", "--version", "1"])
        explicit_bytes = _stdout(capsys)

        assert explicit_bytes == default_bytes

    def test_version_1_is_still_v1(self, capsys) -> None:
        config_cli.main(["contract", "--json", "--version", "1"])

        payload = _document(capsys)
        assert payload["schema"] == "confflow.configuration-contract.v1"
        assert set(payload) == EXPECTED_V1_KEYS


class TestExplicitV2:
    def test_version_2_adds_exactly_the_four_members(self, capsys) -> None:
        assert config_cli.main(["contract", "--json", "--version", "2"]) == ExitCode.SUCCESS

        payload = _document(capsys)
        assert payload["schema"] == "confflow.configuration-contract.v2"
        assert set(payload) == EXPECTED_V1_KEYS | EXPECTED_V2_ONLY_KEYS

    def test_version_2_embeds_a_manifest_and_a_catalog(self, capsys) -> None:
        config_cli.main(["contract", "--json", "--version", "2"])

        payload = _document(capsys)
        assert payload["editor_manifest"]["schema"] == "confflow.editor-manifest.v1"
        assert payload["recipe_catalog"]["schema"] == "confflow.recipe-catalog.v1"
        assert payload["editor_manifest"]["fields"]
        assert payload["recipe_catalog"]["recipes"]

    def test_the_real_cli_dispatch_also_emits_v2(self, capsys) -> None:
        assert cli_main(["config", "contract", "--json", "--version", "2"]) == ExitCode.SUCCESS
        assert _document(capsys)["schema"] == "confflow.configuration-contract.v2"

    def test_two_invocations_produce_identical_bytes(self, capsys) -> None:
        config_cli.main(["contract", "--json", "--version", "2"])
        first = _stdout(capsys)

        config_cli.main(["contract", "--json", "--version", "2"])
        second = _stdout(capsys)

        assert first == second

    def test_the_document_is_canonical_json_plus_a_newline(self, capsys) -> None:
        config_cli.main(["contract", "--json", "--version", "2"])
        emitted = _stdout(capsys)

        # ``_emit`` uses the same separators and key order as canonical_json, so
        # the wire bytes are exactly the canonical form of the document.
        assert emitted == canonical_json(json.loads(emitted)) + "\n"


class TestVersionIsRestricted:
    @pytest.mark.parametrize("value", ["two", "", "2.0"])
    def test_a_non_integer_version_exits_before_printing(self, capsys, value: str) -> None:
        with pytest.raises(SystemExit) as caught:
            config_cli.main(["contract", "--json", "--version", value])

        assert caught.value.code != 0
        captured = capsys.readouterr()
        assert captured.out == ""
        assert "--version" in captured.err

    @pytest.mark.parametrize("value", ["0", "-1", "3", "4"])
    def test_an_unsupported_version_fails_closed_before_printing(self, capsys, value: str) -> None:
        assert config_cli.main(["contract", "--json", "--version", value]) == (ExitCode.USAGE_ERROR)

        captured = capsys.readouterr()
        assert captured.out == ""
        assert "unsupported configuration contract version" in captured.err
        assert "supported: 1, 2" in captured.err

    def test_the_accepted_choices_are_the_supported_versions(self) -> None:
        # PR-7 retires the additive v3 producer contract along with the rest of
        # the never-released Workflow V3 public wire.
        assert sorted(CONFIGURATION_CONTRACT_BUILDERS) == [1, 2]


class TestExistingSurfacesAreUntouched:
    def test_validate_still_works(self, monkeypatch, capsys) -> None:
        monkeypatch.setattr(config_cli.sys, "stdin", io.StringIO('{"global": {}, "steps": []}'))

        assert config_cli.main(["validate", "--json", "--stdin"]) == ExitCode.SUCCESS
        payload = _document(capsys)
        assert payload["schema"] == "confflow.configuration-validation.v1"
        assert payload["valid"] is True

    def _run_validate(self, monkeypatch, capsys, document: dict) -> tuple[int, dict]:
        monkeypatch.setattr(config_cli.sys, "stdin", io.StringIO(json.dumps(document)))
        code = config_cli.main(["validate", "--json", "--stdin"])
        return code, json.loads(capsys.readouterr().out)

    def test_v2_response_is_unchanged(self, monkeypatch, capsys) -> None:
        from confflow.config.canonical import workflow_schema_sha256

        code, payload = self._run_validate(
            monkeypatch,
            capsys,
            {
                "global": {},
                "steps": [{"name": "gen", "type": "confgen", "params": {"chains": ["1-2"]}}],
            },
        )
        assert code == ExitCode.SUCCESS
        assert payload["valid"] is True
        assert payload["workflow_schema_sha256"] == workflow_schema_sha256()
        assert set(payload) == {"schema", "valid", "workflow_schema_sha256", "issues"}

        code, payload = self._run_validate(
            monkeypatch,
            capsys,
            {"steps": [{"name": "gen", "type": "confgen", "params": {}}]},
        )
        assert code == ExitCode.USAGE_ERROR
        assert payload["valid"] is False
        assert payload["workflow_schema_sha256"] == workflow_schema_sha256()

    def test_unknown_schema_fails_closed(self, monkeypatch, capsys) -> None:
        code, payload = self._run_validate(
            monkeypatch, capsys, {"schema": "confflow.workflow.v4", "steps": []}
        )
        assert code == ExitCode.USAGE_ERROR
        assert payload["valid"] is False
        assert any("unsupported workflow schema" in issue["message"] for issue in payload["issues"])
