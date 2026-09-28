#!/usr/bin/env python3

"""Behavior tests for the formal V4 entry helpers (no execution runtime)."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from confflow.application.v4_entry import (
    _build_run_inputs,
    _declared_input_names,
    is_v4_document,
    load_v4_document_file,
    require_v4_document,
    v4_status_payload,
)
from confflow.core.exceptions import ConfFlowError
from confflow.domain._immutable import FrozenDict

_V4_DOC = {"schema": "confflow.workflow.v4", "steps": []}
_XYZ_ONE = "1\nframe a\nH 0 0 0\n"
_XYZ_TWO = "1\nframe b\nH 0 0 0\n"


def test_is_v4_document_detects_the_formal_schema() -> None:
    assert is_v4_document(_V4_DOC) is True
    assert is_v4_document({"schema": "confflow.workflow.v3"}) is False
    assert is_v4_document("not a mapping") is False


def test_require_v4_document_fails_closed_for_legacy_documents() -> None:
    assert require_v4_document(_V4_DOC) is _V4_DOC
    with pytest.raises(ConfFlowError, match="legacy_workflow_not_executable"):
        require_v4_document({"schema": "confflow.workflow.v2"})
    with pytest.raises(ConfFlowError, match="legacy_workflow_not_executable"):
        require_v4_document(["not", "a", "mapping"])


def test_load_v4_document_file_reports_unreadable_and_invalid_files(tmp_path: Path) -> None:
    missing = tmp_path / "missing.yaml"
    with pytest.raises(ConfFlowError, match="legacy_workflow_not_executable"):
        load_v4_document_file(missing)

    broken = tmp_path / "broken.yaml"
    broken.write_text("::: not yaml: [", encoding="utf-8")
    with pytest.raises(ConfFlowError, match="legacy_workflow_not_executable"):
        load_v4_document_file(broken)

    legacy = tmp_path / "legacy.yaml"
    legacy.write_text(yaml.safe_dump({"steps": []}), encoding="utf-8")
    with pytest.raises(ConfFlowError, match="legacy_workflow_not_executable"):
        load_v4_document_file(legacy)

    formal = tmp_path / "formal.yaml"
    formal.write_text(yaml.safe_dump(_V4_DOC), encoding="utf-8")
    assert load_v4_document_file(formal)["schema"] == "confflow.workflow.v4"


def test_declared_input_names_reads_only_string_keys() -> None:
    assert _declared_input_names({"inputs": {"reactant": {}, "product": {}}}) == [
        "reactant",
        "product",
    ]
    assert _declared_input_names({"inputs": []}) == []
    assert _declared_input_names({}) == []


def test_build_run_inputs_single_declared_name_combines_sources() -> None:
    run_inputs, sources = _build_run_inputs(
        {},
        {"/a.xyz": _XYZ_ONE, "/b.xyz": _XYZ_TWO},
        source_names={"/a.xyz": "a.xyz", "/b.xyz": "b.xyz"},
    )
    assert set(run_inputs.structures) == {"structures"}
    assert sources["structures"] == f"{_XYZ_ONE}\n{_XYZ_TWO}"
    assert isinstance(sources, FrozenDict)


def test_build_run_inputs_multi_name_maps_files_in_order() -> None:
    document = {"inputs": {"reactant": {}, "product": {}}}
    run_inputs, sources = _build_run_inputs(
        document,
        {"/r.xyz": _XYZ_ONE, "/p.xyz": _XYZ_TWO},
        source_names={"/r.xyz": "r.xyz", "/p.xyz": "p.xyz"},
    )
    assert set(run_inputs.structures) == {"reactant", "product"}
    assert sources["reactant"] == _XYZ_ONE
    assert sources["product"] == _XYZ_TWO


def test_build_run_inputs_rejects_count_mismatch() -> None:
    document = {"inputs": {"reactant": {}, "product": {}}}
    with pytest.raises(ConfFlowError, match="legacy_workflow_not_executable"):
        _build_run_inputs(
            document,
            {"/r.xyz": _XYZ_ONE},
            source_names={"/r.xyz": "r.xyz"},
        )


def test_build_run_inputs_rejects_empty_content() -> None:
    with pytest.raises(ConfFlowError, match="legacy_workflow_not_executable"):
        _build_run_inputs({}, {"/empty.xyz": "\n\n"}, source_names={"/empty.xyz": "x.xyz"})


def test_v4_status_payload_projects_report_and_diagnostics() -> None:
    diagnostic = SimpleNamespace(
        code="step_failed",
        severity=SimpleNamespace(value="error"),
        message="boom",
        step_id="s001",
        field_path=None,
    )
    step = SimpleNamespace(
        step_id="s001",
        status=SimpleNamespace(value="failed"),
        summary=FrozenDict({"completed": 0, "failed": 1}),
        diagnostics=[diagnostic],
    )
    report = SimpleNamespace(
        run_id="run-1",
        status="failed",
        definition_digest="sha256:" + "0" * 64,
        step_results=[step],
        manifest=FrozenDict({"content_schema": "confflow.run_result_manifest.v1"}),
    )
    payload = v4_status_payload(report)
    assert payload["run_id"] == "run-1"
    assert payload["status"] == "failed"
    assert payload["steps"] == [
        {
            "id": "s001",
            "status": "failed",
            "summary": {"completed": 0, "failed": 1},
            "diagnostics": [
                {
                    "code": "step_failed",
                    "severity": "error",
                    "message": "boom",
                    "step_id": "s001",
                    "field_path": None,
                }
            ],
        }
    ]
    assert payload["manifest"]["content_schema"] == "confflow.run_result_manifest.v1"
