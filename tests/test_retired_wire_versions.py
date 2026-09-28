#!/usr/bin/env python3
"""PR-9 gate: the retired V1/V2/V3 configuration wire fails closed.

Short and behavioural on purpose.  Every case here is a *rejection* the
V4-only configuration surface must keep, plus the small positive surface that
replaced the V1/V2 one:

* ``config contract --json`` emits the single current wire
  (``confflow.configuration-contract.v4``); ``--version 1`` / ``2`` / ``3`` /
  anything unknown fails closed with the stable ``unsupported_workflow_version``
  code, writes nothing to stdout and never falls back;
* ``config validate`` (the released V2 document validator) is retired;
* a V1/V2/V3 workflow document is rejected at the outermost version
  discriminator before any managed-path validation, lease, mkdir or execution;
* the V4 validator still accepts a valid document and rejects an invalid one;
* the retired public Python surface is gone.

The exhaustive architecture guard lives in
``tests/v4/test_architecture_boundaries.py`` (``TestV1V2PublicWireRetired``);
this file is the behaviour gate a reviewer can read in one pass.
"""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from confflow.config import cli as config_cli
from confflow.core.contracts import ExitCode

REPO_ROOT = Path(__file__).resolve().parent.parent

V4_CONTRACT_SCHEMA = "confflow.configuration-contract.v4"
UNSUPPORTED = "unsupported_workflow_version"

V1_DOCUMENT = {"schema": "confflow.workflow.v1", "steps": []}
V2_DOCUMENT = {
    "schema": "confflow.workflow.v2",
    "global": {"charge": 0, "multiplicity": 1},
    "steps": [
        {"name": "gen", "type": "confgen", "params": {"chains": ["1-2-3-4"]}},
        {
            "name": "opt",
            "type": "calc",
            "inputs": ["gen"],
            "params": {"iprog": "orca", "itask": "opt", "keyword": "B3LYP Opt"},
        },
    ],
}
V3_DOCUMENT = {"schema": "confflow.workflow.v3", "steps": []}
LEGACY_DOCUMENT = {
    "global": {"charge": 0, "multiplicity": 1},
    "steps": [{"name": "gen", "type": "confgen", "params": {"chains": ["1-2-3-4"]}}],
}

V4_DOCUMENT = {
    "schema": "confflow.workflow.v4",
    "inputs": {"structures": {"kind": "structure", "cardinality": "many"}},
    "global": {"scientific_defaults": {"charge": 0, "multiplicity": 1}},
    "steps": [
        {
            "id": "s_opt",
            "executor": "calculation",
            "bindings": {"structure": {"source": {"run": "structures"}}},
            "calculation": {
                "program": "orca",
                "role": "opt",
                "execution_adapter": "standard",
                "result_profile": "standard",
                "native": {"keyword": "B3LYP Opt"},
                "checks": ["normal_termination"],
                "recovery": {"profile": "none"},
            },
        }
    ],
}

RETIRED_PUBLIC_MODULES = (
    "confflow.config.canonical",
    "confflow.config.canonical.parser",
    "confflow.config.canonical.validation",
    "confflow.config.canonical.contract",
    "confflow.config.canonical.editor_manifest",
    "confflow.config.canonical.recipes",
    "confflow.config.canonical.v2_adapter",
    "confflow.config.canonical.workflow",
    "confflow.config.canonical.pydantic",
    "confflow.config.models",
    "confflow.core.types",
    "confflow.shared.config_validation",
    "confflow.workflow.plan",
    "confflow.workflow.dry_run",
    "confflow.workflow.config_show",
)


class TestContractVersionRetired:
    def test_default_emits_the_single_current_wire(self, capsys) -> None:
        assert config_cli.main(["contract", "--json"]) == ExitCode.SUCCESS
        payload = json.loads(capsys.readouterr().out)
        assert payload["content_schema"] == V4_CONTRACT_SCHEMA
        assert payload["workflow_schema_id"] == "confflow.workflow.v4"

    def test_explicit_version_4_is_the_same_document(self, capsys) -> None:
        assert config_cli.main(["contract", "--json"]) == ExitCode.SUCCESS
        default_bytes = capsys.readouterr().out
        assert config_cli.main(["contract", "--json", "--version", "4"]) == ExitCode.SUCCESS
        assert capsys.readouterr().out == default_bytes

    @pytest.mark.parametrize("version", ["1", "2", "3", "9"])
    def test_every_other_version_fails_closed(self, version: str, capsys) -> None:
        code = config_cli.main(["contract", "--json", "--version", version])

        assert code == ExitCode.USAGE_ERROR
        captured = capsys.readouterr()
        assert captured.out == ""
        assert UNSUPPORTED in captured.err
        # Never a fallback to another wire.
        assert "configuration-contract.v1" not in captured.err
        assert "configuration-contract.v2" not in captured.err
        assert "configuration-contract.v3" not in captured.err

    def test_config_validate_is_retired(self, capsys, monkeypatch) -> None:
        monkeypatch.setattr(sys, "stdin", __import__("io").StringIO(json.dumps(V2_DOCUMENT)))

        code = config_cli.main(["validate", "--stdin", "--json"])

        assert code == ExitCode.USAGE_ERROR
        captured = capsys.readouterr()
        assert captured.out == ""
        assert UNSUPPORTED in captured.err
        assert "confflow v4 validate" in captured.err


class TestWorkflowDocumentVersionsRejected:
    @pytest.mark.parametrize(
        "document",
        [V1_DOCUMENT, V2_DOCUMENT, V3_DOCUMENT, LEGACY_DOCUMENT],
        ids=["v1", "v2", "v3", "legacy-no-schema"],
    )
    def test_execution_refuses_before_any_side_effect(
        self, document: dict, tmp_path: Path, capsys
    ) -> None:
        from confflow.cli import main as cli_main

        xyz = tmp_path / "input.xyz"
        xyz.write_text("1\nseed\nH 0 0 0\n", encoding="utf-8")
        config = tmp_path / "wf.yaml"
        config.write_text(yaml.safe_dump(document, sort_keys=False), encoding="utf-8")
        work = tmp_path / "work"

        code = cli_main([str(xyz), "-c", str(config), "-w", str(work)])

        assert code == ExitCode.RUNTIME_ERROR
        captured = capsys.readouterr()
        assert captured.out == ""
        assert "legacy_workflow_not_executable" in captured.err
        assert not work.exists()

    @pytest.mark.parametrize("switch", ["--dry-run", "--config-show"])
    def test_retired_legacy_diagnostic_switches_are_gone(self, switch: str) -> None:
        """The V2 dry-run / config-show routes were retired with the wire."""
        from confflow.cli import build_parser

        with pytest.raises(SystemExit):
            build_parser().parse_args([switch, "-c", "wf.yaml"])

    def test_v4_document_is_still_the_formal_format(self, tmp_path: Path) -> None:
        from confflow.application.v4_entry import require_v4_document_file

        config = tmp_path / "flow.yaml"
        config.write_text(yaml.safe_dump(V4_DOCUMENT, sort_keys=False), encoding="utf-8")

        assert require_v4_document_file(config)["schema"] == "confflow.workflow.v4"


class TestV4SurfaceStillWorks:
    def test_valid_document_validates_and_compiles(self) -> None:
        from confflow.producer.validation import validate_workflow_bytes
        from confflow.workflow.v4 import compile_workflow

        payload = json.dumps(V4_DOCUMENT).encode("utf-8")
        report = validate_workflow_bytes(payload)

        assert report.ok is True
        assert report.schema_id == "confflow.workflow.v4"
        assert report.definition_digest
        compiled = compile_workflow(V4_DOCUMENT)
        assert compiled.ok is True

    def test_invalid_document_is_rejected_with_structured_diagnostics(self) -> None:
        from confflow.producer.validation import validate_workflow_bytes

        broken = json.loads(json.dumps(V4_DOCUMENT))
        broken["steps"][0]["executor"] = "no-such-executor"
        report = validate_workflow_bytes(json.dumps(broken).encode("utf-8"))

        assert report.ok is False
        assert report.errors()

    def test_producer_contract_bytes_are_unchanged_by_the_retirement(self) -> None:
        """The V4 producer contract must not carry a V1/V2 surface."""
        script = (
            "import sys, json;"
            "from confflow.producer.contract import generate_contract_bytes;"
            "payload = json.loads(generate_contract_bytes(producer_version='probe'));"
            "print(json.dumps(sorted(payload)))"
        )
        completed = subprocess.run(
            [sys.executable, "-c", script],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        assert completed.returncode == 0, completed.stderr
        keys = json.loads(completed.stdout)
        for retired in (
            "workflow_schema_version",
            "configuration-contract.v1",
            "configuration-contract.v2",
        ):
            assert retired not in keys
        # The frozen validation-response id is still the current V4 value.
        assert json.loads(
            subprocess.run(
                [
                    sys.executable,
                    "-c",
                    "import json;"
                    "from confflow.producer.contract import generate_contract_bytes;"
                    "print(json.dumps(json.loads(generate_contract_bytes("
                    "producer_version='probe'))['validation_response_schema']))",
                ],
                cwd=REPO_ROOT,
                capture_output=True,
                text=True,
                check=True,
            ).stdout
        ) == "confflow.configuration-validation.v1"


class TestRetiredPublicSurface:
    @pytest.mark.parametrize("module", RETIRED_PUBLIC_MODULES)
    def test_retired_module_is_not_importable(self, module: str) -> None:
        try:
            spec = importlib.util.find_spec(module)
        except ModuleNotFoundError:
            return  # a missing parent package is the strongest form of absence
        assert spec is None, module

    def test_config_package_exports_nothing_retired(self) -> None:
        import confflow.config as config

        assert config.__all__ == []
        with pytest.raises(AttributeError):
            _ = config.WorkflowConfig
