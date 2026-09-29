#!/usr/bin/env python3

"""Producer/renderer native-definition requirement consistency.

The program adapter is the file-format authority.  Its structure-independent
native-definition requirements (strict native vocabulary, required non-empty
``keyword``, deterministic option constraints) are evaluated by semantic
validation at compile time on the same function the renderer refuses on.  A
document the renderer would deterministically reject must therefore fail
producer validation before any submission or native execution; valid documents
keep their definition digest unchanged.

These tests pin the negative cases for Gaussian and ORCA, the positive case,
the disabled-step boundary (no native rendering), and the runtime-rule
consistency between the validator and ``materialize_native_input``.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from confflow.domain._immutable import FrozenDict
from confflow.domain.resources import ResourceRequest
from confflow.execution.native import ResolvedCalculationInputs
from confflow.producer.authoring import validate_document
from confflow.producer.validation import validate_workflow_bytes
from confflow.programs.gaussian import GaussianProgramAdapter
from confflow.programs.orca import OrcaProgramAdapter
from confflow.workflow.v4.compiler import compile_workflow
from tests.v4._builders import calc_step, structure

STRUCTURE_INPUTS = {"structures": {"kind": "structure", "cardinality": "many"}}
_VALID_NATIVE = {"keyword": "B3LYP/6-31G* opt"}


def _document_bytes(document: dict[str, Any]) -> bytes:
    return json.dumps(document).encode("utf-8")


def _calculation_document(
    native: dict[str, Any] | None,
    *,
    program: str = "g16",
    enabled: bool | None = None,
) -> dict[str, Any]:
    """Build a calculation document with an explicit native mapping (None omits it)."""
    step = calc_step(
        "step_1",
        bindings={"structure": {"source": {"run": "structures"}}},
        program=program,
        enabled=enabled,
    )
    if native is None:
        del step["calculation"]["native"]
    else:
        step["calculation"]["native"] = dict(native)
    return {
        "schema": "confflow.workflow.v4",
        "global": {"scientific_defaults": {"charge": 0, "multiplicity": 1}},
        "inputs": dict(STRUCTURE_INPUTS),
        "steps": [step],
    }


def _assert_native_rejection(report: Any, message_fragment: str) -> None:
    assert report.ok is False
    errors = report.errors()
    assert len(errors) == 1, errors
    diagnostic = errors[0]
    assert diagnostic["code"] == "scientific_parameter_conflict"
    assert diagnostic["reason"] == "invalid_value"
    assert diagnostic["severity"] == "error"
    assert diagnostic["step_id"] == "step_1"
    assert diagnostic["field_path"] == "steps.step_1.calculation.native"
    assert message_fragment in diagnostic["message"]


def _gaussian_inputs(native: dict[str, Any]) -> ResolvedCalculationInputs:
    return ResolvedCalculationInputs(
        structure=structure("s0"),
        charge=0,
        multiplicity=1,
        freeze=None,
        resources=ResourceRequest.from_values(cores_per_item=4, memory_per_item="16GB"),
        native=FrozenDict(native),
        checkpoints=(),
        step_id="step_1",
        work_item_id="wi:step_1:item0",
        logical_key="step_1:item0",
    )


def _orca_inputs(native: dict[str, Any]) -> ResolvedCalculationInputs:
    return ResolvedCalculationInputs(
        structure=structure("s0"),
        charge=0,
        multiplicity=1,
        freeze=None,
        resources=ResourceRequest.from_values(cores_per_item=4, memory_per_item="16GB"),
        native=FrozenDict(native),
        checkpoints=(),
        step_id="step_1",
        work_item_id="wi:step_1:item0",
        logical_key="step_1:item0",
    )


# -- Gaussian negative cases -------------------------------------------------


def test_missing_keyword_is_rejected_by_validation() -> None:
    report = validate_workflow_bytes(_document_bytes(_calculation_document({})))
    _assert_native_rejection(report, "keyword")


def test_empty_keyword_is_rejected_by_validation() -> None:
    report = validate_workflow_bytes(_document_bytes(_calculation_document({"keyword": ""})))
    _assert_native_rejection(report, "keyword")


def test_marker_only_keyword_is_rejected_by_validation() -> None:
    report = validate_workflow_bytes(_document_bytes(_calculation_document({"keyword": "#"})))
    _assert_native_rejection(report, "empty after normalization")


def test_unknown_native_key_is_rejected_by_validation() -> None:
    report = validate_workflow_bytes(
        _document_bytes(_calculation_document({"keyword": "B3LYP", "not_a_key": 1}))
    )
    _assert_native_rejection(report, "unknown native key")


def test_omitted_native_member_is_rejected_by_validation() -> None:
    report = validate_workflow_bytes(_document_bytes(_calculation_document(None)))
    _assert_native_rejection(report, "keyword")


def test_compiler_rejects_the_same_document() -> None:
    compiled = compile_workflow(_calculation_document({}))
    assert compiled.ok is False
    errors = [item for item in compiled.diagnostics if item.is_error]
    assert len(errors) == 1
    assert errors[0].details.get("reason") == "invalid_value"
    assert errors[0].step_id == "step_1"


def test_authoring_validate_document_flags_the_requirement() -> None:
    response = validate_document(_calculation_document({}))
    assert response["ok"] is False
    assert any(
        item["code"] == "scientific_parameter_conflict"
        and item["reason"] == "invalid_value"
        and item["step_id"] == "step_1"
        for item in response["result"]["diagnostics"]
    )


# -- positive / boundary cases -----------------------------------------------


def test_valid_document_keeps_its_definition_digest() -> None:
    document = _calculation_document(_VALID_NATIVE)
    report = validate_workflow_bytes(_document_bytes(document))
    compiled = compile_workflow(document)
    assert report.ok is True
    assert compiled.ok is True
    assert report.definition_digest == compiled.plan.definition_digest
    assert report.step_ids == ("step_1",)


def test_disabled_step_never_carries_the_native_requirement() -> None:
    document = _calculation_document({}, enabled=False)
    compiled = compile_workflow(document)
    assert compiled.ok is True
    assert not [item for item in compiled.diagnostics if item.is_error]


def test_confgen_step_never_carries_the_native_requirement() -> None:
    from tests.v4._builders import confgen_step

    document = {
        "schema": "confflow.workflow.v4",
        "inputs": dict(STRUCTURE_INPUTS),
        "steps": [
            confgen_step("conf_1", bindings={"structure": {"source": {"run": "structures"}}})
        ],
    }
    compiled = compile_workflow(document)
    assert compiled.ok is True


# -- ORCA negative cases -----------------------------------------------------


def test_orca_missing_keyword_is_rejected_by_validation() -> None:
    report = validate_workflow_bytes(
        _document_bytes(_calculation_document({}, program="orca"))
    )
    _assert_native_rejection(report, "ORCA 'keyword'")


def test_orca_unknown_key_is_rejected_by_validation() -> None:
    report = validate_workflow_bytes(
        _document_bytes(_calculation_document({"keyword": "B3LYP Opt", "bogus": 1}, program="orca"))
    )
    _assert_native_rejection(report, "unknown native keys")


def test_orca_maxcore_override_is_validated_at_compile_time() -> None:
    report = validate_workflow_bytes(
        _document_bytes(
            _calculation_document({"keyword": "B3LYP Opt", "maxcore": "not-a-number"}, program="orca")
        )
    )
    _assert_native_rejection(report, "maxcore")


# -- runtime-rule consistency ------------------------------------------------


def test_gaussian_renderer_refuses_the_same_native_definition() -> None:
    adapter = GaussianProgramAdapter()
    message = adapter.validate_native_definition(FrozenDict({}))[0]
    with pytest.raises(ValueError) as excinfo:
        adapter.materialize_native_input(_gaussian_inputs({}))
    assert str(excinfo.value) == message


def test_gaussian_renderer_refuses_empty_keyword_identically() -> None:
    adapter = GaussianProgramAdapter()
    message = adapter.validate_native_definition(FrozenDict({"keyword": ""}))[0]
    with pytest.raises(ValueError) as excinfo:
        adapter.materialize_native_input(_gaussian_inputs({"keyword": ""}))
    assert str(excinfo.value) == message


def test_orca_renderer_refuses_the_same_native_definition() -> None:
    adapter = OrcaProgramAdapter()
    message = adapter.validate_native_definition(FrozenDict({}))[0]
    with pytest.raises(ValueError) as excinfo:
        adapter.materialize_native_input(_orca_inputs({}))
    assert str(excinfo.value) == message


def test_orca_renderer_refuses_unknown_keys_identically() -> None:
    adapter = OrcaProgramAdapter()
    message = adapter.validate_native_definition(FrozenDict({"keyword": "B3LYP Opt", "bogus": 1}))[
        0
    ]
    with pytest.raises(ValueError) as excinfo:
        adapter.materialize_native_input(_orca_inputs({"keyword": "B3LYP Opt", "bogus": 1}))
    assert str(excinfo.value) == message


def test_adapters_expose_the_requirement_path_on_the_protocol() -> None:
    from confflow.execution.native import ProgramAdapter

    assert isinstance(GaussianProgramAdapter(), ProgramAdapter)
    assert isinstance(OrcaProgramAdapter(), ProgramAdapter)
