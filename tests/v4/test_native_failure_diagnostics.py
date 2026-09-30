#!/usr/bin/env python3

"""Native failure reasons reach the authoritative step and manifest diagnostics.

Audit residual I2: a real native execution failure kept its reason only in
``item_results[].error``; the published ``StepResult`` and the
``RunResultManifest`` step diagnostics stayed empty, so a consumer saw
``status = failed`` without a useful reason.

These tests pin the aggregation rule in ``BatchStepExecutor._assemble``:
the item error is projected into a step-level diagnostic with the producer's
own code/message and the failed step id, deduplicated against diagnostics the
item already emitted.  The manifest is then checked against its published
JSON schema so the projection stays wire-conformant.
"""

from __future__ import annotations

import json
import stat
import tempfile
from pathlib import Path

import pytest

from confflow.application.v4_run import V4RunApplication, V4RunRequest, import_xyz
from confflow.domain._immutable import FrozenDict
from confflow.domain.completion import WorkItemStatus
from confflow.domain.diagnostics import Diagnostic, DiagnosticSeverity
from confflow.domain.work_item import ResultError, WorkItemResult
from confflow.execution.batch import _item_error_diagnostics
from confflow.execution.process import NativeProcessSupervisor
from confflow.producer.contract import run_result_json_schema
from confflow.producer.validation import validate_workflow_bytes
from confflow.workflow.v4.assembly import RunInputs

_WATER_XYZ = "3\nwater\nO 0 0 0\nH 0.76 0.59 0\nH 0.76 -0.59 0\n"


def _document() -> dict:
    return {
        "schema": "confflow.workflow.v4",
        "global": {
            "scientific_defaults": {"charge": 0, "multiplicity": 1},
            "resources": {"cores_per_item": 1, "memory_per_item": "1GB"},
            "scheduler": {"max_parallel_items": 1},
        },
        "inputs": {"structures": {"kind": "structure", "cardinality": "many"}},
        "steps": [
            {
                "id": "sp",
                "executor": "calculation",
                "bindings": {"structure": {"source": {"run": "structures"}}},
                "calculation": {
                    "program": "g16",
                    "execution_adapter": "standard",
                    "result_profile": "standard",
                    "native": {"keyword": "B3LYP/6-31G(d)"},
                },
            }
        ],
    }


@pytest.mark.parametrize("executable_name", ["/bin/false"])
def test_native_failure_reason_reaches_step_and_manifest_diagnostics(
    tmp_path: Path, executable_name: str
) -> None:
    document = _document()
    validation = validate_workflow_bytes(json.dumps(document).encode())
    assert validation.ok, validation.diagnostics

    run_root = Path(tempfile.mkdtemp(prefix="native-failure-diag-", dir=tmp_path))
    report = V4RunApplication(supervisor=NativeProcessSupervisor()).run(
        V4RunRequest(
            workflow_document=document,
            run_inputs=RunInputs(
                structures=FrozenDict({"structures": import_xyz(_WATER_XYZ)})
            ),
            run_root=str(run_root),
            executables=FrozenDict({"g16": executable_name}),
        )
    )

    assert report.status == "failed", report.status

    step = json.loads((run_root / "steps/sp/step_result.json").read_text())
    item_error = step["item_results"][0]["error"]
    assert item_error is not None, "the failing native run must record an item error"

    step_diagnostics = [
        item for item in step["diagnostics"] if item["severity"] == "error"
    ]
    assert len(step_diagnostics) == 1, step_diagnostics
    diagnostic = step_diagnostics[0]
    assert diagnostic["code"] == item_error["code"]
    assert diagnostic["message"] == item_error["message"]
    assert diagnostic["step_id"] == "sp"

    manifest = json.loads((run_root / "run_result.json").read_text())
    manifest_step = manifest["steps"][0]
    assert manifest_step["id"] == "sp"
    assert manifest_step["status"] == "failed"
    manifest_diagnostics = [
        item for item in manifest_step["diagnostics"] if item["severity"] == "error"
    ]
    assert len(manifest_diagnostics) == 1, manifest_diagnostics
    assert manifest_diagnostics[0]["code"] == item_error["code"]
    assert manifest_diagnostics[0]["message"] == item_error["message"]
    assert manifest_diagnostics[0]["step_id"] == "sp"

    # The published manifest stays conformant to its frozen JSON schema.
    jsonschema = pytest.importorskip("jsonschema")
    jsonschema.validate(manifest, run_result_json_schema())


def _failed_item(
    *, error: ResultError | None, diagnostics: tuple[Diagnostic, ...] = ()
) -> WorkItemResult:
    return WorkItemResult(
        work_item_id="wi:sp:item0",
        status=WorkItemStatus.FAILED if error is not None else WorkItemStatus.COMPLETED,
        diagnostics=diagnostics,
        error=error,
    )


def test_item_error_is_projected_once_with_step_and_work_item_association() -> None:
    error = ResultError(
        code="native_execution_error",
        message="native program exited with code 3",
        details=FrozenDict({"exit_code": 3}),
    )
    projected = _item_error_diagnostics(_failed_item(error=error), step_id="sp")
    assert len(projected) == 1
    diagnostic = projected[0]
    assert diagnostic.code == "native_execution_error"
    assert diagnostic.message == "native program exited with code 3"
    assert diagnostic.severity is DiagnosticSeverity.ERROR
    assert diagnostic.step_id == "sp"
    assert diagnostic.work_item_id == "wi:sp:item0"
    assert diagnostic.details["exit_code"] == 3


def test_an_error_already_carried_by_the_item_is_not_duplicated() -> None:
    error = ResultError(code="cancellation_error", message="work item cancelled")
    already = (
        Diagnostic(
            code="cancellation_error",
            message="work item cancelled",
            severity=DiagnosticSeverity.ERROR,
            step_id="sp",
            work_item_id="wi:sp:item0",
        ),
    )
    assert _item_error_diagnostics(_failed_item(error=error, diagnostics=already), step_id="sp") == ()


def test_completed_items_never_gain_an_error_diagnostic() -> None:
    assert _item_error_diagnostics(_failed_item(error=None), step_id="sp") == ()


def test_an_error_only_failure_still_reaches_the_manifest(tmp_path: Path) -> None:
    """The exact audit-I2 shape: no failure-site diagnostic, only item.error.

    A walltime timeout records the reason in ``item_results[].error`` with no
    item diagnostic.  This is the path the fix exists for: before it, both the
    step result and the manifest carried no reason at all.
    """
    document = _document()
    document["steps"][0]["execution"] = {"walltime_seconds": 1}
    script = tmp_path / "slow-native"
    script.write_text("#!/bin/sh\nsleep 10\nexit 0\n", encoding="utf-8")
    script.chmod(script.stat().st_mode | stat.S_IXUSR)

    run_root = Path(tempfile.mkdtemp(prefix="native-failure-timeout-", dir=tmp_path))
    report = V4RunApplication(supervisor=NativeProcessSupervisor()).run(
        V4RunRequest(
            workflow_document=document,
            run_inputs=RunInputs(
                structures=FrozenDict({"structures": import_xyz(_WATER_XYZ)})
            ),
            run_root=str(run_root),
            executables=FrozenDict({"g16": str(script)}),
        )
    )

    assert report.status == "failed", report.status
    step = json.loads((run_root / "steps/sp/step_result.json").read_text())
    item = step["item_results"][0]
    assert item["error"] is not None
    assert item["diagnostics"] == [], "this scenario must exercise the error-only path"

    errors = [entry for entry in step["diagnostics"] if entry["severity"] == "error"]
    assert len(errors) == 1, errors
    assert errors[0]["code"] == item["error"]["code"]
    assert errors[0]["message"] == item["error"]["message"]
    assert errors[0]["step_id"] == "sp"

    manifest = json.loads((run_root / "run_result.json").read_text())
    manifest_errors = [
        entry
        for entry in manifest["steps"][0]["diagnostics"]
        if entry["severity"] == "error"
    ]
    assert len(manifest_errors) == 1, manifest_errors
    assert manifest_errors[0]["code"] == item["error"]["code"]
    assert manifest_errors[0]["step_id"] == "sp"
