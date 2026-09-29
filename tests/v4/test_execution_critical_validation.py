#!/usr/bin/env python3

"""Execution-critical scientific parameter validation.

Native rendering requires resolved charge and multiplicity for the driving
structure.  A V4 document that declares neither a step override nor a
run-level ``scientific_defaults`` value leaves that resolution entirely to
the input structures; the producer cannot verify it from document bytes, so
semantic validation fails closed with ``scientific_parameter_conflict`` /
``metadata_unavailable`` before any native rendering.  These tests pin the
negative (omission flagged) and positive (resolved values unaffected) cases,
plus the boundary that non-native executors and disabled steps do not carry
the requirement.
"""

from __future__ import annotations

import json
from typing import Any

from confflow.producer.authoring import validate_document
from confflow.producer.validation import validate_workflow_bytes
from confflow.workflow.v4.compiler import compile_workflow
from tests.v4._builders import calc_step, confgen_step, v4_doc

STRUCTURE_INPUTS = {"structures": {"kind": "structure", "cardinality": "many"}}

_REASON = "metadata_unavailable"
_MESSAGE = "charge and multiplicity must be resolved before native rendering"


def _document_bytes(document: dict[str, Any]) -> bytes:
    return json.dumps(document).encode("utf-8")


def _defaults_less_document(**calc_kwargs: Any) -> dict[str, Any]:
    """Build a calculation document without any scientific default source."""
    step = calc_step(
        "step_1",
        bindings={"structure": {"source": {"run": "structures"}}},
        **calc_kwargs,
    )
    return {"schema": "confflow.workflow.v4", "inputs": dict(STRUCTURE_INPUTS), "steps": [step]}


def test_missing_defaults_is_rejected_by_validation() -> None:
    report = validate_workflow_bytes(_document_bytes(_defaults_less_document()))
    assert report.ok is False
    errors = report.errors()
    assert len(errors) == 1
    diagnostic = errors[0]
    assert diagnostic["code"] == "scientific_parameter_conflict"
    assert diagnostic["reason"] == _REASON
    assert diagnostic["severity"] == "error"
    assert diagnostic["step_id"] == "step_1"
    assert diagnostic["field_path"] == "steps.step_1.calculation.overrides"
    assert diagnostic["message"] == _MESSAGE


def test_missing_defaults_is_rejected_by_the_compiler() -> None:
    compiled = compile_workflow(_defaults_less_document())
    assert compiled.ok is False
    errors = [item for item in compiled.diagnostics if item.is_error]
    assert [item.details.get("reason") for item in errors] == [_REASON]
    assert errors[0].step_id == "step_1"


def test_partial_defaults_flag_only_the_missing_field() -> None:
    document = _defaults_less_document()
    document["global"] = {"scientific_defaults": {"charge": 0}}
    compiled = compile_workflow(document)
    assert compiled.ok is False
    errors = [item for item in compiled.diagnostics if item.is_error]
    assert len(errors) == 1
    assert errors[0].details.get("reason") == _REASON
    assert errors[0].details.get("missing") == ("multiplicity",)


def test_run_defaults_resolve_the_requirement() -> None:
    document = _defaults_less_document()
    document["global"] = {"scientific_defaults": {"charge": 0, "multiplicity": 1}}
    compiled = compile_workflow(document)
    assert compiled.ok is True
    assert not [item for item in compiled.diagnostics if item.is_error]


def test_step_overrides_resolve_the_requirement() -> None:
    document = _defaults_less_document(overrides={"charge": 1, "multiplicity": 2})
    compiled = compile_workflow(document)
    assert compiled.ok is True
    assert not [item for item in compiled.diagnostics if item.is_error]


def test_step_override_and_global_default_mix_resolves() -> None:
    document = _defaults_less_document(overrides={"charge": 1})
    document["global"] = {"scientific_defaults": {"multiplicity": 1}}
    compiled = compile_workflow(document)
    assert compiled.ok is True


def test_disabled_step_never_carries_the_requirement() -> None:
    step = calc_step(
        "step_1",
        bindings={"structure": {"source": {"run": "structures"}}},
        enabled=False,
    )
    document = {
        "schema": "confflow.workflow.v4",
        "inputs": dict(STRUCTURE_INPUTS),
        "steps": [step],
    }
    compiled = compile_workflow(document)
    assert compiled.ok is True
    assert not [item for item in compiled.diagnostics if item.is_error]


def test_pure_executors_never_carry_the_requirement() -> None:
    document = v4_doc(
        [confgen_step("conf_1", bindings={"structure": {"source": {"run": "structures"}}})],
        inputs=STRUCTURE_INPUTS,
    )
    del document["global"]
    compiled = compile_workflow(document)
    assert compiled.ok is True
    assert not [item for item in compiled.diagnostics if item.is_error]


def test_authoring_validate_document_flags_the_omission() -> None:
    response = validate_document(_defaults_less_document())
    assert response["ok"] is False
    assert any(
        item["reason"] == _REASON and item["step_id"] == "step_1"
        for item in response["result"]["diagnostics"]
    )
