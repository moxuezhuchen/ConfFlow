#!/usr/bin/env python3

"""PR-7 negative tests: the never-released Workflow V3 public wire is retired.

These tests pin the retirement boundary, not the removed implementation:

* ``config contract --json --version 3`` fails closed with a stable error and
  emits nothing (no fallback to v1/v2, no silent migration, no execution);
* a V3 workflow document is rejected everywhere it could be recognised, parsed,
  validated, displayed or executed — before any side effect;
* ``confflow workflow upgrade`` (the V2->V3 public emitter) is a retired route;
* the public ``confflow.config.canonical`` surface exposes no V3-only symbol,
  no V3 contract generator, no V3 execution advertisement and no V3 parser.

V3 was never a published release (last release v2.1.6 predates the first V3
commit by 77 commits), so no V3 bytes are preserved.
"""

from __future__ import annotations

import importlib.util
import io
import json
from pathlib import Path

import pytest
import yaml

from confflow import cli as cli_main_module
from confflow.cli import main as cli_main
from confflow.config import cli as config_cli
from confflow.config.canonical import (
    CONFIGURATION_CONTRACT_BUILDERS,
    build_configuration_contract_for_version,
    detect_schema_version,
    workflow_schema_sha256,
)
from confflow.config.canonical.issues import ConfigValidationError
from confflow.core.contracts import ExitCode

V3_SCHEMA = "confflow.workflow.v3"

#: Modules that existed only for the never-released V3 public wire plus the
#: retired V3 capability table and the V2->V3 upgrade CLI.
RETIRED_V3_MODULES = (
    "confflow.config.canonical.v3_parser",
    "confflow.config.canonical.v3_graph",
    "confflow.config.canonical.upgrade",
    "confflow.config.canonical.structured",
    "confflow.config.canonical.theory",
    "confflow.config.canonical.extensions",
    "confflow.config.canonical.yaml_io",
    "confflow.config.canonical.execution_versions",
    "confflow.config.workflow_cli",
)

#: Public canonical names that must not exist any more. A representative sample
#: per retired area: parser/graph, contract, catalogs, capability advertisement
#: and the migration kernel.
RETIRED_V3_SYMBOLS = (
    "WORKFLOW_SCHEMA_VERSION_V3",
    "WORKFLOW_V3_ID_PATTERN",
    "SchemaProfile",
    "workflow_json_schema_v3",
    "workflow_schema_sha256_v3",
    "parse_v3_document",
    "build_validated_graph",
    "ValidatedWorkflowGraph",
    "v3_id_order_key",
    "ValidationProfile",
    "validate_workflow_v3",
    "validate_v3_definition",
    "resolve_step_semantic_params",
    "workflow_definition_fingerprint_v3",
    "build_workflow_definition_payload_v3",
    "EXECUTION_CLASS_GLOBAL_MEMBERS",
    "WORKFLOW_SEMANTICS_VERSION",
    "CONFIGURATION_CONTRACT_V3_SCHEMA",
    "build_configuration_contract_v3",
    "build_editor_manifest_v3",
    "editor_manifest_sha256_v3",
    "build_recipe_catalog_v3",
    "recipe_catalog_sha256_v3",
    "instantiate_recipe_v3",
    "allocate_step_id",
    "StepIdExhaustionError",
    "compile_structured_calc",
    "STRUCTURED_THEORY_FIELDS",
    "MIGRATION_NAMESPACE",
    "MigrationRecord",
    "UnknownParamsPolicy",
    "UpgradeError",
    "UpgradeResult",
    "parse_unknown_params_policy",
    "upgrade_v2_to_v3",
    "CAPABILITIES",
    "VersionCapability",
    "can_parse",
    "can_execute",
    "require_executable",
    "require_executable_workflow_file",
    "DEFAULT_EXTENSION_REGISTRY",
    "ExtensionRegistry",
    "is_valid_namespace",
    "v3_calc_keys",
    "param_properties",
    "program_choices",
    "task_choices",
    "theory_dispersion_choices",
    "theory_solvent_models",
    "dump_workflow_yaml",
    "order_workflow_document",
    "write_workflow_yaml_atomic",
)

V3_DOCUMENT = {
    "schema": V3_SCHEMA,
    "steps": [
        {"id": "s001", "type": "confgen", "inputs": [], "params": {"chains": ["1-2"]}},
        {"id": "s002", "type": "calc", "inputs": ["s001"], "params": {"keyword": "HF"}},
    ],
}


def _write_xyz(path: Path) -> Path:
    path.write_text("1\nseed\nH 0 0 0\n", encoding="utf-8")
    return path


def _write_v3_config(path: Path) -> Path:
    path.write_text(yaml.safe_dump(V3_DOCUMENT, sort_keys=False), encoding="utf-8")
    return path


class TestContractVersion3Retired:
    def test_version_3_fails_closed_with_a_stable_error(self, capsys) -> None:
        assert config_cli.main(["contract", "--json", "--version", "3"]) == ExitCode.USAGE_ERROR

        captured = capsys.readouterr()
        assert captured.out == ""
        assert "unsupported configuration contract version 3; supported: 1, 2" in captured.err

    def test_version_4_fails_closed_the_same_way(self, capsys) -> None:
        assert config_cli.main(["contract", "--json", "--version", "4"]) == ExitCode.USAGE_ERROR
        captured = capsys.readouterr()
        assert captured.out == ""

    def test_the_builder_api_refuses_version_3(self) -> None:
        with pytest.raises(ValueError, match="unsupported configuration contract version 3"):
            build_configuration_contract_for_version(
                3, producer_version="0.0.0", producer_commit=None, producer_dirty=None
            )

    def test_no_version_3_builder_is_registered(self) -> None:
        assert sorted(CONFIGURATION_CONTRACT_BUILDERS) == [1, 2]

    def test_v1_and_v2_may_not_leak_v3_vocabulary(self, capsys) -> None:
        for args in (
            ["contract", "--json"],
            ["contract", "--json", "--version", "2"],
        ):
            assert config_cli.main(args) == ExitCode.SUCCESS
            emitted = capsys.readouterr().out
            assert "confflow.workflow.v3" not in emitted
            assert "configuration-contract.v3" not in emitted


class TestPublicImportSurface:
    def test_retired_modules_are_absent(self) -> None:
        for module in RETIRED_V3_MODULES:
            assert importlib.util.find_spec(module) is None, module

    def test_retired_symbols_are_absent(self) -> None:
        import confflow.config.canonical as canonical

        present = [name for name in RETIRED_V3_SYMBOLS if hasattr(canonical, name)]
        assert present == []
        assert not any("v3" in name.lower() for name in dir(canonical))
        assert not any("v3" in name.lower() for name in canonical.__all__)

    def test_no_execution_versions_module_or_advertisement(self) -> None:
        import confflow.config.canonical as canonical

        assert importlib.util.find_spec("confflow.config.canonical.execution_versions") is None
        assert not hasattr(canonical, "CAPABILITIES")
        assert not hasattr(canonical, "can_execute")


class TestV3DocumentRejected:
    def test_detect_schema_version_rejects_the_retired_wire(self) -> None:
        with pytest.raises(ConfigValidationError) as caught:
            detect_schema_version({"schema": V3_SCHEMA, "steps": []})
        assert caught.value.issue.path == "schema"
        assert caught.value.issue.message == ("unsupported workflow schema: 'confflow.workflow.v3'")

    def test_config_validate_fails_closed_with_the_v2_digest(
        self, monkeypatch: pytest.MonkeyPatch, capsys
    ) -> None:
        monkeypatch.setattr(config_cli.sys, "stdin", io.StringIO(json.dumps(V3_DOCUMENT)))

        assert config_cli.main(["validate", "--stdin", "--json"]) == ExitCode.USAGE_ERROR
        payload = json.loads(capsys.readouterr().out)
        assert payload["valid"] is False
        assert payload["workflow_schema_sha256"] == workflow_schema_sha256()
        assert any("unsupported workflow schema" in issue["message"] for issue in payload["issues"])
        assert set(payload) == {"schema", "valid", "workflow_schema_sha256", "issues"}

    def test_dry_run_fails_closed_with_no_side_effects(self, tmp_path: Path, capsys) -> None:
        xyz = _write_xyz(tmp_path / "input.xyz")
        config = _write_v3_config(tmp_path / "wf.yaml")
        work = tmp_path / "work"

        code = cli_main([str(xyz), "-c", str(config), "-w", str(work), "--dry-run"])

        assert code == ExitCode.USAGE_ERROR
        captured = capsys.readouterr()
        assert captured.out == ""
        assert "unsupported workflow schema" in captured.err
        assert not work.exists()

    def test_config_show_fails_closed(self, tmp_path: Path, capsys) -> None:
        config = _write_v3_config(tmp_path / "wf.yaml")

        code = cli_main(["-c", str(config), "--config-show"])

        assert code == ExitCode.USAGE_ERROR
        assert capsys.readouterr().out == ""

    def test_execution_is_rejected_before_any_side_effect(self, tmp_path: Path, capsys) -> None:
        xyz = _write_xyz(tmp_path / "input.xyz")
        config = _write_v3_config(tmp_path / "wf.yaml")
        work = tmp_path / "work"

        code = cli_main([str(xyz), "-c", str(config), "-w", str(work)])

        assert code == ExitCode.RUNTIME_ERROR
        captured = capsys.readouterr()
        assert captured.out == ""
        assert "legacy_workflow_not_executable" in captured.err
        assert not work.exists()
        assert not (work / ".confflow_execution").exists()


class TestUpgradeCommandRetired:
    def test_workflow_upgrade_is_a_retired_route(self, tmp_path: Path, capsys) -> None:
        config = _write_v3_config(tmp_path / "wf.yaml")
        output = tmp_path / "upgraded.yaml"

        code = cli_main(["workflow", "upgrade", str(config), "-o", str(output)])

        assert code == ExitCode.USAGE_ERROR
        captured = capsys.readouterr()
        assert captured.out == ""
        assert "retired command 'workflow'" in captured.err
        assert not output.exists()

    def test_workflow_help_no_longer_advertises_upgrade(self, capsys) -> None:
        assert cli_main(["workflow", "--help"]) == ExitCode.USAGE_ERROR
        captured = capsys.readouterr()
        assert captured.out == ""
        assert "upgrade" not in captured.err

    def test_cli_module_does_not_import_the_retired_emitter(self) -> None:
        source = Path(cli_main_module.__file__).read_text(encoding="utf-8")
        assert "workflow_cli" not in source
        assert "upgrade_v2_to_v3" not in source
