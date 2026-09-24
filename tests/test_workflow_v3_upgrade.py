#!/usr/bin/env python3

"""R3.3 — deterministic V2 -> V3 upgrade.

Covers the migration algorithm (RFC §14), the reserved migration-metadata
mapping (`annotations.confflow.migration.v2`), the unknown-params policies, the
deterministic serializer and the ``confflow workflow upgrade`` CLI.
"""

from __future__ import annotations

import copy
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
import yaml
from jsonschema import Draft202012Validator

from confflow.config.canonical import (
    SchemaProfile,
    dump_workflow_yaml,
    upgrade_v2_to_v3,
    workflow_json_schema_v3,
)
from confflow.config.canonical.upgrade import UpgradeError

FIXTURES = Path(__file__).parent / "fixtures" / "workflow_v3"
MIGRATION = "confflow.migration.v2"


def _fixture(name: str) -> Any:
    return yaml.safe_load((FIXTURES / name).read_text(encoding="utf-8"))


def _document(raw: dict[str, Any], **kwargs: Any) -> dict[str, Any]:
    return upgrade_v2_to_v3(raw, **kwargs).document


def _validate_document(document: dict[str, Any]) -> list[str]:
    validator = Draft202012Validator(workflow_json_schema_v3(SchemaProfile.DOCUMENT))
    return [error.message for error in validator.iter_errors(document)]


def _step(document: dict[str, Any], step_id: str) -> dict[str, Any]:
    return next(step for step in document["steps"] if step["id"] == step_id)


# ---------------------------------------------------------------------------
# U1 — implicit linear
# ---------------------------------------------------------------------------
def test_implicit_linear_becomes_a_chain() -> None:
    document = _document(_fixture("upgrade_linear_v2.yaml"))

    assert document == _fixture("upgrade_linear_v3.yaml")
    assert [step["id"] for step in document["steps"]] == ["s001", "s002", "s003"]
    assert [step["label"] for step in document["steps"]] == ["A", "B", "C"]
    assert [step["inputs"] for step in document["steps"]] == [[], ["s001"], ["s002"]]


# ---------------------------------------------------------------------------
# U2 — explicit DAG
# ---------------------------------------------------------------------------
def test_explicit_dag_preserves_edges() -> None:
    document = _document(_fixture("upgrade_dag_v2.yaml"))

    assert document == _fixture("upgrade_dag_v3.yaml")
    assert _step(document, "s001")["inputs"] == []
    assert _step(document, "s002")["inputs"] == ["s001"]
    assert _step(document, "s003")["inputs"] == ["s001"]


# ---------------------------------------------------------------------------
# U3 — mixed explicit V2 semantics
# ---------------------------------------------------------------------------
def test_any_declared_inputs_selects_explicit_mode() -> None:
    raw = {
        "global": {},
        "steps": [
            {"name": "A", "type": "confgen", "params": {"chains": ["1-2"]}},
            {"name": "B", "type": "confgen", "inputs": ["A"], "params": {"chains": ["1-2"]}},
            {"name": "C", "type": "confgen", "params": {"chains": ["1-2"]}},
        ],
    }
    document = _document(raw)

    # C had no inputs, so in explicit mode it becomes a root (not chained after B).
    assert _step(document, "s001")["inputs"] == []
    assert _step(document, "s002")["inputs"] == ["s001"]
    assert _step(document, "s003")["inputs"] == []


# ---------------------------------------------------------------------------
# U4 — unnamed / generated names
# ---------------------------------------------------------------------------
def test_unnamed_steps_get_generated_labels() -> None:
    raw = {
        "global": {},
        "steps": [
            {"type": "gen", "params": {"chains": ["1-2"]}},
            {"type": "task", "params": {"keyword": "HF"}},
        ],
    }
    first = _document(raw)
    second = _document(raw)

    assert first == second
    assert [step["label"] for step in first["steps"]] == ["confgen_1", "calc_2"]
    assert [step["type"] for step in first["steps"]] == ["confgen", "calc"]
    assert [step["id"] for step in first["steps"]] == ["s001", "s002"]


# ---------------------------------------------------------------------------
# U5 — disabled step
# ---------------------------------------------------------------------------
def test_disabled_step_is_preserved() -> None:
    raw = {
        "global": {},
        "steps": [
            {"name": "a", "type": "confgen", "enabled": False, "params": {"chains": ["1-2"]}},
            {"name": "b", "type": "confgen", "params": {"chains": ["1-2"]}},
        ],
    }
    document = _document(raw)

    assert _step(document, "s001")["enabled"] is False
    assert "enabled" not in _step(document, "s002")
    assert _step(document, "s002")["inputs"] == ["s001"]


# ---------------------------------------------------------------------------
# U6 — type aliases
# ---------------------------------------------------------------------------
def test_task_and_gen_aliases_normalise() -> None:
    raw = {
        "global": {},
        "steps": [
            {"name": "g", "type": "gen", "params": {"chains": ["1-2"]}},
            {"name": "t", "type": "task", "params": {"keyword": "HF"}},
        ],
    }
    document = _document(raw)

    assert [step["type"] for step in document["steps"]] == ["confgen", "calc"]


# ---------------------------------------------------------------------------
# U7 / U8 — checkpoint migration
# ---------------------------------------------------------------------------
def test_checkpoint_by_name_migrates_to_id() -> None:
    document = _document(_fixture("upgrade_checkpoint_name_v2.yaml"))

    assert document == _fixture("upgrade_checkpoint_name_v3.yaml")
    assert _step(document, "s002")["checkpoint"] == {"from_step": "s001"}
    assert "chk_from_step" not in _step(document, "s002")["params"]


def test_checkpoint_by_numeric_position_uses_document_order() -> None:
    document = _document(_fixture("upgrade_checkpoint_index_v2.yaml"))

    assert document == _fixture("upgrade_checkpoint_index_v3.yaml")
    # document position 1 is "zeta" (not the alphabetically first "alpha").
    assert _step(document, "s001")["label"] == "zeta"
    assert _step(document, "s003")["checkpoint"] == {"from_step": "s001"}
    assert "chk_from_step" not in _step(document, "s003")["params"]


# ---------------------------------------------------------------------------
# U9 / U10 — parameter preservation and alias canonicalisation
# ---------------------------------------------------------------------------
def test_absent_defaults_are_not_materialised() -> None:
    raw = {
        "global": {},
        "steps": [
            {"name": "g", "type": "confgen", "params": {"chains": ["1-2"]}},
            {"name": "c", "type": "calc", "params": {"keyword": "HF"}},
        ],
    }
    document = _document(raw)

    assert _step(document, "s001")["params"] == {"chains": ["1-2"]}  # no angle_step default
    assert _step(document, "s002")["params"] == {"keyword": "HF"}  # no iprog/itask default


def test_present_alias_values_are_canonicalised_absent_stay_absent() -> None:
    raw = {
        "global": {},
        "steps": [
            {
                "name": "c",
                "type": "calc",
                "params": {"keyword": "HF", "iprog": 2, "itask": "optfreq"},
            },
            {"name": "d", "type": "calc", "params": {"keyword": "HF"}},
        ],
    }
    document = _document(raw)

    assert _step(document, "s001")["params"]["iprog"] == "orca"
    assert _step(document, "s001")["params"]["itask"] == "opt_freq"
    assert "iprog" not in _step(document, "s002")["params"]
    assert "itask" not in _step(document, "s002")["params"]


# ---------------------------------------------------------------------------
# U11 — unknown params default: fail closed
# ---------------------------------------------------------------------------
def test_unknown_param_fails_closed_by_default() -> None:
    raw = {
        "global": {},
        "steps": [{"name": "c", "type": "calc", "params": {"keyword": "HF", "vendor_magic": 123}}],
    }
    with pytest.raises(UpgradeError) as caught:
        _document(raw)

    diagnostics = caught.value.diagnostics
    assert len(diagnostics) == 1
    assert diagnostics[0].code == "workflow.upgrade.unknown_param"
    assert "vendor_magic" in diagnostics[0].path
    assert "s001" in (diagnostics[0].step_ref or "")


# ---------------------------------------------------------------------------
# U12 — unknown params -> annotations (lossy)
# ---------------------------------------------------------------------------
def test_unknown_params_route_to_lossy_annotations() -> None:
    raw = {
        "global": {},
        "steps": [
            {
                "name": "c",
                "type": "calc",
                "params": {"keyword": "HF", "vendor_magic": 123, "vendor_two": "x"},
            }
        ],
    }
    result = upgrade_v2_to_v3(raw, unknown_params_policy="annotations")
    step = result.document["steps"][0]

    assert step["params"] == {"keyword": "HF"}
    assert step["annotations"][MIGRATION]["unknown_params"] == {
        "vendor_magic": 123,
        "vendor_two": "x",
    }
    assert "unknown_fields" not in step["annotations"][MIGRATION]
    assert _validate_document(result.document) == []
    assert result.warnings  # lossy warning emitted
    assert any("no longer participate in execution semantics" in w for w in result.warnings)
    assert any(record.lossy for record in result.migrations)


# ---------------------------------------------------------------------------
# U13 / U14 — unknown params -> extensions
# ---------------------------------------------------------------------------
def test_unknown_params_route_to_extension_namespace() -> None:
    raw = {
        "global": {},
        "steps": [{"name": "c", "type": "calc", "params": {"keyword": "HF", "vendor_magic": 123}}],
    }
    result = upgrade_v2_to_v3(raw, unknown_params_policy="extensions:vendor.example")
    step = result.document["steps"][0]

    assert step["params"] == {"keyword": "HF"}
    assert step["extensions"] == {"vendor.example": {"vendor_magic": 123}}
    assert "annotations" not in step  # not also copied to annotations
    assert _validate_document(result.document) == []
    assert any("does not recognize" in w for w in result.warnings)


def test_invalid_extension_namespace_is_rejected() -> None:
    raw = {
        "global": {},
        "steps": [{"name": "c", "type": "calc", "params": {"keyword": "HF", "x": 1}}],
    }
    with pytest.raises(UpgradeError) as caught:
        _document(raw, unknown_params_policy="extensions:nosegments")

    assert caught.value.diagnostics[0].code == "workflow.upgrade.unknown_params_namespace"


# ---------------------------------------------------------------------------
# U15 / U16 — unknown fields
# ---------------------------------------------------------------------------
def test_unknown_step_fields_preserve_nested_structure() -> None:
    raw = {
        "global": {},
        "steps": [
            {
                "name": "c",
                "type": "calc",
                "params": {"keyword": "HF"},
                "note": "hello",
                "tools": {"x": 1, "y": [2, 3]},
            }
        ],
    }
    step = _document(raw)["steps"][0]

    assert step["annotations"][MIGRATION]["unknown_fields"] == {
        "note": "hello",
        "tools": {"x": 1, "y": [2, 3]},
    }


def test_unknown_root_fields_go_to_root_annotations() -> None:
    raw = {
        "global": {},
        "tools": {"vendor": "foo"},
        "note": "legacy",
        "steps": [{"name": "c", "type": "calc", "params": {"keyword": "HF"}}],
    }
    document = _document(raw)

    assert document["annotations"][MIGRATION]["unknown_fields"] == {
        "tools": {"vendor": "foo"},
        "note": "legacy",
    }
    assert "extensions" not in document


def test_source_field_named_annotations_or_migration_is_preserved_safely() -> None:
    raw = {
        "global": {},
        "steps": [
            {
                "name": "c",
                "type": "calc",
                "params": {"keyword": "HF"},
                "annotations": {"user": 1},
                MIGRATION: {"reserved": True},
            }
        ],
    }
    step = _document(raw)["steps"][0]

    assert step["annotations"][MIGRATION]["unknown_fields"] == {
        "annotations": {"user": 1},
        MIGRATION: {"reserved": True},
    }


# ---------------------------------------------------------------------------
# U11..U17 migration-metadata grouping / emptiness (tests G, H)
# ---------------------------------------------------------------------------
def test_known_params_and_unknown_fields_group_correctly() -> None:
    raw = {
        "global": {},
        "steps": [
            {
                "name": "c",
                "type": "calc",
                "params": {"keyword": "HF", "vendor_magic": 1},
                "note": "hi",
            }
        ],
    }
    step = upgrade_v2_to_v3(raw, unknown_params_policy="annotations").document["steps"][0]

    block = step["annotations"][MIGRATION]
    assert block["unknown_fields"] == {"note": "hi"}
    assert block["unknown_params"] == {"vendor_magic": 1}


def test_empty_migration_groups_are_omitted() -> None:
    raw = {
        "global": {},
        "steps": [{"name": "c", "type": "calc", "params": {"keyword": "HF"}}],
    }
    step = _document(raw)["steps"][0]

    assert "annotations" not in step
    assert "annotations" not in _document(raw)


# ---------------------------------------------------------------------------
# U17 / U18 / U19 — refusals
# ---------------------------------------------------------------------------
def test_upgrade_refuses_a_v3_document_without_renumbering() -> None:
    v3 = _fixture("upgrade_linear_v3.yaml")
    before = copy.deepcopy(v3)
    with pytest.raises(UpgradeError) as caught:
        _document(v3)

    assert caught.value.diagnostics[0].code == "workflow.upgrade.already_v3"
    assert v3 == before


def test_upgrade_refuses_unknown_schema() -> None:
    with pytest.raises(UpgradeError):
        _document({"schema": "confflow.workflow.v9", "steps": []})


@pytest.mark.parametrize(
    "raw",
    [
        {
            "global": {},
            "steps": [
                {"name": "a", "type": "confgen", "inputs": ["b"], "params": {"chains": ["1-2"]}},
                {"name": "b", "type": "confgen", "inputs": ["a"], "params": {"chains": ["1-2"]}},
            ],
        },
        {
            "global": {},
            "steps": [
                {"name": "c", "type": "calc", "inputs": ["missing"], "params": {"keyword": "HF"}}
            ],
        },
        {
            "global": {},
            "steps": [
                {"name": "c", "type": "calc", "params": {"keyword": "HF", "chk_from_step": "nope"}}
            ],
        },
    ],
)
def test_invalid_v2_is_refused(raw: dict[str, Any]) -> None:
    with pytest.raises(UpgradeError):
        _document(raw)


# ---------------------------------------------------------------------------
# U20 — >999 steps
# ---------------------------------------------------------------------------
def test_more_than_999_steps_ids_continue() -> None:
    steps: list[dict[str, Any]] = []
    for index in range(1000):
        step: dict[str, Any] = {"name": f"c{index}", "type": "calc", "params": {"keyword": "HF"}}
        if index:
            step["inputs"] = [f"c{index - 1}"]
        steps.append(step)
    raw = {"global": {}, "steps": steps}

    document = _document(raw)

    assert len(document["steps"]) == 1000
    assert document["steps"][0]["id"] == "s001"
    assert document["steps"][999]["id"] == "s1000"
    assert _validate_document(document) == []


# ---------------------------------------------------------------------------
# U21 / U22 — determinism
# ---------------------------------------------------------------------------
def test_mapping_is_deterministic() -> None:
    raw = _fixture("upgrade_linear_v2.yaml")
    assert _document(raw) == _document(raw)


def test_serialization_is_byte_deterministic() -> None:
    document = _document(_fixture("upgrade_linear_v2.yaml"))
    assert dump_workflow_yaml(document) == dump_workflow_yaml(document)


def test_cli_output_is_deterministic_across_processes(tmp_path: Path) -> None:
    source = FIXTURES / "upgrade_linear_v2.yaml"
    script = "from confflow.cli import main; import sys; sys.exit(main(sys.argv[1:]))"

    def run() -> bytes:
        completed = subprocess.run(
            [sys.executable, "-c", script, "workflow", "upgrade", str(source)],
            capture_output=True,
            check=False,
        )
        assert completed.returncode == 0, completed.stderr.decode()
        # The environment emits import-time warnings with timestamps; compare the
        # emitted document bytes only (from the first ``schema:`` line).
        return completed.stdout[completed.stdout.index(b"schema:") :]

    assert run() == run()


# ---------------------------------------------------------------------------
# U23 / U24 / U25 — output invariants
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "name",
    ["upgrade_linear_v2.yaml", "upgrade_dag_v2.yaml", "upgrade_checkpoint_name_v2.yaml"],
)
def test_default_upgrade_output_passes_the_document_schema(name: str) -> None:
    assert _validate_document(_document(_fixture(name))) == []


def test_output_contains_no_fragment_handles() -> None:
    raw = _fixture("upgrade_linear_v2.yaml")
    assert "#step" not in dump_workflow_yaml(_document(raw))


def test_author_order_drives_id_allocation() -> None:
    raw = {
        "global": {},
        "steps": [
            {"name": "B", "type": "confgen", "params": {"chains": ["1-2"]}},
            {"name": "A", "type": "confgen", "params": {"chains": ["1-2"]}},
            {"name": "C", "type": "confgen", "params": {"chains": ["1-2"]}},
        ],
    }
    document = _document(raw)

    assert [step["id"] for step in document["steps"]] == ["s001", "s002", "s003"]
    assert [step["label"] for step in document["steps"]] == ["B", "A", "C"]


# ---------------------------------------------------------------------------
# U26 — the source mapping is never mutated
# ---------------------------------------------------------------------------
def test_upgrade_does_not_mutate_the_source() -> None:
    raw = {
        "global": {"iprog": "orca"},
        "tools": {"x": 1},
        "steps": [
            {"name": "c", "type": "calc", "params": {"keyword": "HF", "weird": 7}, "note": "hi"}
        ],
    }
    before = copy.deepcopy(raw)

    upgrade_v2_to_v3(raw, unknown_params_policy="annotations")

    assert raw == before


# ---------------------------------------------------------------------------
# §12 — the combined migration fixture
# ---------------------------------------------------------------------------
COMBINED: dict[str, Any] = {
    "global": {"iprog": "orca"},
    "tools": {"vendor": "foo"},
    "steps": [
        {"name": "s", "type": "confgen", "params": {"chains": ["1-2"]}, "note": "step-note"},
        {"name": "c", "type": "calc", "params": {"keyword": "HF", "vendor_magic": 5}},
    ],
}


def test_combined_fixture_default_policy_fails_on_the_unknown_param() -> None:
    with pytest.raises(UpgradeError) as caught:
        _document(COMBINED)
    assert any(d.code == "workflow.upgrade.unknown_param" for d in caught.value.diagnostics)


def test_combined_fixture_annotations_policy() -> None:
    document = _document(COMBINED, unknown_params_policy="annotations")

    assert document["annotations"][MIGRATION]["unknown_fields"] == {"tools": {"vendor": "foo"}}
    assert document["steps"][0]["annotations"][MIGRATION]["unknown_fields"] == {"note": "step-note"}
    assert document["steps"][1]["annotations"][MIGRATION]["unknown_params"] == {"vendor_magic": 5}
    assert _validate_document(document) == []


def test_combined_fixture_extensions_policy_keeps_fields_as_annotations() -> None:
    document = _document(COMBINED, unknown_params_policy="extensions:vendor.example")

    # only the unknown *param* becomes a semantic extension; unknown fields stay annotations
    assert document["annotations"][MIGRATION]["unknown_fields"] == {"tools": {"vendor": "foo"}}
    assert document["steps"][0]["annotations"][MIGRATION]["unknown_fields"] == {"note": "step-note"}
    assert document["steps"][1]["extensions"] == {"vendor.example": {"vendor_magic": 5}}
    assert "annotations" not in document["steps"][1]
    assert _validate_document(document) == []


# ---------------------------------------------------------------------------
# U27 — CLI
# ---------------------------------------------------------------------------
def _cli(args: list[str]) -> int:
    from confflow.cli import main

    return main(args)


def _recover_yaml(text: str) -> Any:
    """Drop leading import-time noise, then parse the emitted document."""
    marker = text.index("schema:")
    return yaml.safe_load(text[marker:])


def test_cli_writes_stdout(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    source = FIXTURES / "upgrade_linear_v2.yaml"
    assert _cli(["workflow", "upgrade", str(source)]) == 0
    assert _recover_yaml(capsys.readouterr().out) == _fixture("upgrade_linear_v3.yaml")


def test_cli_writes_a_file_and_refuses_to_overwrite(tmp_path: Path) -> None:
    source = FIXTURES / "upgrade_linear_v2.yaml"
    destination = tmp_path / "out.yaml"

    assert _cli(["workflow", "upgrade", str(source), "-o", str(destination)]) == 0
    assert yaml.safe_load(destination.read_text(encoding="utf-8")) == _fixture(
        "upgrade_linear_v3.yaml"
    )

    # second write must refuse to clobber
    assert _cli(["workflow", "upgrade", str(source), "-o", str(destination)]) != 0


def test_cli_check_writes_nothing(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    source = FIXTURES / "upgrade_linear_v2.yaml"
    assert _cli(["workflow", "upgrade", str(source), "--check"]) == 0
    assert "schema:" not in capsys.readouterr().out


def test_cli_exit_codes(tmp_path: Path) -> None:
    v3 = tmp_path / "v3.yaml"
    v3.write_text(yaml.safe_dump(_fixture("upgrade_linear_v3.yaml")), encoding="utf-8")
    assert _cli(["workflow", "upgrade", str(v3)]) != 0

    unknown = tmp_path / "unknown.yaml"
    unknown.write_text("schema: confflow.workflow.v9\nsteps: []\n", encoding="utf-8")
    assert _cli(["workflow", "upgrade", str(unknown)]) != 0

    assert _cli(["workflow", "upgrade", str(tmp_path / "does-not-exist.yaml")]) != 0


def test_cli_unknown_params_policy(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    source = tmp_path / "unknown_param.yaml"
    source.write_text(
        yaml.safe_dump(
            {
                "global": {},
                "steps": [{"name": "c", "type": "calc", "params": {"keyword": "HF", "weird": 1}}],
            }
        ),
        encoding="utf-8",
    )
    assert _cli(["workflow", "upgrade", str(source)]) != 0
    assert _cli(["workflow", "upgrade", str(source), "--unknown-params=annotations"]) == 0
    document = _recover_yaml(capsys.readouterr().out)
    assert document["steps"][0]["annotations"][MIGRATION]["unknown_params"] == {"weird": 1}
    assert _cli(["workflow", "upgrade", str(source), "--unknown-params=extensions:bad"]) != 0
