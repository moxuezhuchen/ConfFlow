#!/usr/bin/env python3

"""Workflow document schema and parser tests for V4.

Pins strict unknown-member rejection, the single source of resource defaults,
registry-driven JSON Schema vocabularies, and the typed parse diagnostics.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

import pytest
from pydantic import ValidationError

from confflow.execution import default_registry
from confflow.workflow.v4 import (
    parse_workflow_document,
    parse_workflow_text,
    parse_workflow_text_document,
)
from confflow.workflow.v4.parser import WorkflowYamlError
from confflow.workflow.v4.schema import (
    DEFAULT_CORES_PER_ITEM,
    DEFAULT_MAX_PARALLEL_ITEMS,
    DEFAULT_MEMORY_PER_ITEM,
    TRANSFORM_KINDS,
    DocumentModel,
    build_workflow_json_schema,
)
from tests.v4._builders import (
    V4_SCHEMA,
    analysis_step,
    calc_step,
    codes,
    reasons,
    v4_doc,
)


def base_document() -> dict[str, Any]:
    """Return a minimal valid V4 document mapping."""
    return v4_doc([analysis_step("s1")])


def document_with_unknown_top_level_member() -> dict[str, Any]:
    document = base_document()
    document["unknown_top_level"] = True
    return document


def document_with_unknown_calculation_member() -> dict[str, Any]:
    document = v4_doc([calc_step("s1")])
    document["steps"][0]["calculation"]["auto_clean"] = True
    return document


def document_with_string_cores() -> dict[str, Any]:
    document = base_document()
    document["global"] = {"resources": {"cores_per_item": "8"}}
    return document


def document_with_string_enabled() -> dict[str, Any]:
    document = base_document()
    document["steps"][0]["enabled"] = "true"
    return document


def document_with_unknown_cardinality() -> dict[str, Any]:
    document = base_document()
    document["inputs"] = {"structures": {"kind": "structure", "cardinality": "lots"}}
    return document


def document_with_unknown_pairing() -> dict[str, Any]:
    document = base_document()
    document["inputs"] = {"structures": {"kind": "structure", "pairing": "zip"}}
    return document


def document_with_unknown_on_failure() -> dict[str, Any]:
    document = base_document()
    document["global"] = {"scheduler": {"on_failure": "stop"}}
    return document


def document_with_empty_steps() -> dict[str, Any]:
    return v4_doc([])


@pytest.mark.parametrize(
    "factory",
    [
        pytest.param(document_with_unknown_top_level_member, id="unknown-top-level-member"),
        pytest.param(document_with_unknown_calculation_member, id="auto-clean-member"),
        pytest.param(document_with_string_cores, id="string-cores"),
        pytest.param(document_with_string_enabled, id="string-enabled"),
        pytest.param(document_with_unknown_cardinality, id="unknown-cardinality"),
        pytest.param(document_with_unknown_pairing, id="unknown-pairing"),
        pytest.param(document_with_unknown_on_failure, id="unknown-on-failure"),
        pytest.param(document_with_empty_steps, id="empty-steps"),
    ],
)
def test_document_model_rejects_invalid_documents(
    factory: Callable[[], dict[str, Any]],
) -> None:
    """Shape violations fail closed at the schema boundary."""
    with pytest.raises(ValidationError):
        DocumentModel.model_validate(factory())


def test_schema_alias_is_the_document_identity() -> None:
    """The ``schema`` alias is exposed as ``schema_id``."""
    model = DocumentModel.model_validate(base_document())
    assert model.schema_id == V4_SCHEMA
    assert model.model_dump(by_alias=True)["schema"] == V4_SCHEMA


def test_document_defaults_are_single_source() -> None:
    """Omitting resources yields the declared defaults with an equal dump."""
    omitted = DocumentModel.model_validate(base_document())
    assert omitted.global_.resources.cores_per_item == DEFAULT_CORES_PER_ITEM
    assert omitted.global_.resources.memory_per_item == DEFAULT_MEMORY_PER_ITEM
    assert omitted.global_.scheduler.max_parallel_items == DEFAULT_MAX_PARALLEL_ITEMS
    explicit = DocumentModel.model_validate(
        v4_doc(
            [analysis_step("s1")],
            global_config={
                "resources": {
                    "cores_per_item": DEFAULT_CORES_PER_ITEM,
                    "memory_per_item": DEFAULT_MEMORY_PER_ITEM,
                },
                "scheduler": {"max_parallel_items": DEFAULT_MAX_PARALLEL_ITEMS},
            },
        )
    )
    assert explicit.model_dump() == omitted.model_dump()


def test_json_schema_injects_registry_vocabularies() -> None:
    """Schema enums come from the registry and the transform vocabulary."""
    registry = default_registry()
    schema = build_workflow_json_schema(registry)
    definitions = schema["$defs"]
    assert definitions["StepModel"]["properties"]["executor"]["enum"] == sorted(
        registry.capability_names
    )
    calculation = definitions["CalculationModel"]["properties"]
    assert calculation["execution_adapter"]["enum"] == sorted(registry.adapter_names)
    assert calculation["result_profile"]["enum"] == sorted(registry.profile_names)
    assert calculation["checks"]["items"]["enum"] == sorted(registry.check_names)
    assert calculation["recovery"]["properties"]["profile"]["enum"] == sorted(
        registry.recovery_names
    )
    assert definitions["AnalysisModel"]["properties"]["checks"]["items"]["enum"] == sorted(
        registry.check_names
    )
    assert definitions["TransformModel"]["properties"]["kind"]["enum"] == list(TRANSFORM_KINDS)


def test_json_schema_has_no_extension_escape_hatch() -> None:
    """A misspelled member such as ``auto_clean`` exists nowhere in the schema."""
    assert "auto_clean" not in json.dumps(build_workflow_json_schema())


def test_unsupported_schema_version_is_a_schema_error() -> None:
    """An unknown schema id is rejected before any step conversion."""
    result = parse_workflow_document(
        {"schema": "confflow.workflow.v99", "steps": [analysis_step("s1")]}
    )
    assert result.ok is False
    assert result.definition is None
    assert codes(result.diagnostics) == ["schema_error"]
    assert reasons(result.diagnostics) == ["version_unsupported"]
    assert result.diagnostics[0].details["found"] == "confflow.workflow.v99"
    assert result.diagnostics[0].details["supported"] == V4_SCHEMA


DUPLICATE_KEY_YAML = """\
schema: confflow.workflow.v4
steps:
  - id: s1
    executor: analysis
steps: []
"""

MALFORMED_YAML = "schema: [unterminated\n"


def test_duplicate_yaml_keys_are_rejected() -> None:
    """Duplicate mapping keys are a hard YAML error, never last-wins."""
    with pytest.raises(WorkflowYamlError, match="duplicate key"):
        parse_workflow_text(DUPLICATE_KEY_YAML)
    result = parse_workflow_text_document(DUPLICATE_KEY_YAML)
    assert result.ok is False
    assert codes(result.diagnostics) == ["schema_error"]
    assert reasons(result.diagnostics) == ["yaml_syntax"]


def test_malformed_yaml_is_reported_as_syntax_error() -> None:
    """YAML syntax failures surface as typed schema_error diagnostics."""
    with pytest.raises(WorkflowYamlError):
        parse_workflow_text(MALFORMED_YAML)
    result = parse_workflow_text_document(MALFORMED_YAML)
    assert result.ok is False
    assert codes(result.diagnostics) == ["schema_error"]
    assert reasons(result.diagnostics) == ["yaml_syntax"]


def test_mismatched_executor_block_is_a_capability_error() -> None:
    """A block belonging to another executor is misplaced, not ignored."""
    document = v4_doc([analysis_step("s1")])
    document["steps"][0]["calculation"] = {"program": "g16"}
    result = parse_workflow_document(document)
    assert result.ok is False
    assert codes(result.diagnostics) == ["capability_error"]
    assert reasons(result.diagnostics) == ["misplaced_executor_block"]


def test_valid_document_parses_without_diagnostics() -> None:
    """A minimal analysis workflow converts into a canonical definition."""
    result = parse_workflow_document(base_document())
    assert result.ok is True
    assert result.definition is not None
    assert result.definition.step_ids == ("s1",)
    assert result.errors == ()
