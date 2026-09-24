#!/usr/bin/env python3

"""Workflow V3 structural parser: ``confflow.workflow.v3`` mapping -> canonical IR.

This module is deliberately *structural*. It:

* confirms the document is V3,
* validates it against the production V3 JSON Schema (the shared structural truth
  generated from the param descriptor registry),
* maps it into the canonical workflow IR (stable id, label, inputs,
  ``checkpoint.from_step``, extensions, annotations, ``source_version``).

It does **not** perform semantic validation — unknown/duplicate/self/cyclic
predecessors, checkpoint ancestry, calc fan-in, and unrecognised-extension
rejection all belong to R3.4. Ordering and terminal detection here are
best-effort population of the IR's required fields from the *declared* graph; a
cycle or an unknown reference simply falls back to document order and the
validator later rejects it, so the parser never invents a verdict.
"""

from __future__ import annotations

import copy
from collections.abc import Mapping
from typing import Any

from jsonschema import Draft202012Validator

from ...core.exceptions import ConfFlowError
from .issues import ConfigIssue, ConfigValidationError
from .parser import detect_schema_version
from .resolve import resolve_global_options
from .schema import WORKFLOW_SCHEMA_VERSION_V3, SchemaProfile, workflow_json_schema_v3
from .workflow import (
    CanonicalStepDefinition,
    CanonicalWorkflowDefinition,
    topo_order,
)

__all__ = ["parse_v3_document"]

#: Local, non-persisted graph handle for a fragment step that has no id yet. It
#: contains characters no legal V3 id may contain, so it can never collide with a
#: real id and is never mistaken for one.
_FRAGMENT_HANDLE_PREFIX = "#step"


def _sort_key(error: Any) -> tuple[list[str], str]:
    return ([str(part) for part in error.absolute_path], error.message)


def _validate(document: Mapping[str, Any], profile: SchemaProfile) -> None:
    validator = Draft202012Validator(workflow_json_schema_v3(profile))
    errors = sorted(validator.iter_errors(document), key=_sort_key)
    if errors:
        first = errors[0]
        path = "/".join(str(part) for part in first.absolute_path)
        raise ConfigValidationError(ConfigIssue(path, first.message))


def parse_v3_document(
    raw: Mapping[str, Any],
    *,
    profile: SchemaProfile = SchemaProfile.DOCUMENT,
) -> CanonicalWorkflowDefinition:
    """Parse a ``confflow.workflow.v3`` document into the canonical IR."""
    if not isinstance(raw, Mapping):
        raise ConfigValidationError(ConfigIssue("", "workflow config root must be a mapping"))
    owned = dict(raw)

    version = detect_schema_version(owned)
    if version != WORKFLOW_SCHEMA_VERSION_V3:
        raise ConfigValidationError(
            ConfigIssue(
                "schema", f"expected a {WORKFLOW_SCHEMA_VERSION_V3} document, got {version!r}"
            )
        )

    _validate(owned, profile)

    global_mapping = owned.get("global") or {}
    global_options = resolve_global_options(global_mapping)

    raw_steps = owned.get("steps") or []
    steps: list[CanonicalStepDefinition] = []
    predecessors: dict[str, tuple[str, ...]] = {}
    names: list[str] = []

    for index, raw_step in enumerate(raw_steps, start=1):
        step_id = raw_step.get("id")
        handle = step_id if step_id is not None else f"{_FRAGMENT_HANDLE_PREFIX}{index}"
        declared_inputs = tuple(str(item) for item in (raw_step.get("inputs") or ()))
        checkpoint = raw_step.get("checkpoint") or {}
        names.append(handle)
        predecessors[handle] = declared_inputs
        steps.append(
            CanonicalStepDefinition(
                name=handle,
                type=str(raw_step.get("type")),
                enabled=bool(raw_step.get("enabled", True)),
                params=copy.deepcopy(raw_step.get("params") or {}),
                predecessors=declared_inputs,
                inputs_declared=True,
                v2_name=handle,
                id=step_id,
                label=raw_step.get("label"),
                checkpoint_from=(
                    checkpoint.get("from_step") if isinstance(checkpoint, Mapping) else None
                ),
                raw_inputs=copy.deepcopy(raw_step.get("inputs") or []),
                extensions=copy.deepcopy(raw_step.get("extensions") or {}),
                annotations=copy.deepcopy(raw_step.get("annotations") or {}),
            )
        )

    try:
        execution_order = tuple(
            name
            for wave in topo_order({key: list(value) for key, value in predecessors.items()})
            for name in wave
        )
    except ConfFlowError:
        # An unresolved reference or a cycle: leave ordering to document order and
        # let R3.4 semantic validation reject it.
        execution_order = tuple(names)

    referenced = {item for values in predecessors.values() for item in values}
    terminal_steps = tuple(name for name in names if name not in referenced)

    return CanonicalWorkflowDefinition(
        global_config=copy.deepcopy(dict(global_mapping)),
        global_options=global_options,
        steps=tuple(steps),
        dependency_mode="explicit",
        predecessors=predecessors,
        execution_order=execution_order,
        terminal_steps=terminal_steps,
        extensions=copy.deepcopy(owned.get("extensions") or {}),
        source_version=WORKFLOW_SCHEMA_VERSION_V3,
        annotations=copy.deepcopy(owned.get("annotations") or {}),
    )
