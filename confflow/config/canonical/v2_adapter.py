#!/usr/bin/env python3

"""V2 compatibility adapter: ``WorkflowConfig`` -> canonical workflow IR.

This is the *only* place that translates the historical V2 document shape
(``name`` / ``type`` / ``enabled`` / ``params`` / ``inputs``, plus the legacy
linear fallback when no step declares ``inputs``) into the canonical IR. Keeping
the translation here means nothing below the IR boundary has to read raw YAML to
learn step names, types, parameters, graph predecessors, or terminal topology.

The ``raw`` mapping is still read here, on purpose: this module *is* the
compatibility boundary. Unknown step-level and root-level keys are carried
through as ``extensions`` rather than silently dropped.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from .schema import WORKFLOW_SCHEMA_VERSION
from .types import WorkflowConfig
from .workflow import (
    CanonicalStepDefinition,
    CanonicalWorkflowDefinition,
    build_step_graph,
    topo_order,
)

__all__ = ["to_canonical_workflow"]

_KNOWN_STEP_KEYS = frozenset({"name", "type", "enabled", "params", "inputs"})
_KNOWN_ROOT_KEYS = frozenset({"global", "steps"})


def _step_extensions(raw_step: Any) -> dict[str, Any]:
    if not isinstance(raw_step, Mapping):
        return {}
    return {key: value for key, value in raw_step.items() if str(key) not in _KNOWN_STEP_KEYS}


def _root_extensions(raw: Mapping[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in raw.items() if str(key) not in _KNOWN_ROOT_KEYS}


def to_canonical_workflow(workflow: WorkflowConfig) -> CanonicalWorkflowDefinition:
    """Resolve a parsed V2 workflow model into the canonical IR."""
    legacy = workflow.as_legacy_shape()
    global_config = legacy["global"]
    legacy_steps = legacy["steps"]
    raw_steps = workflow.raw.get("steps")

    raw_predecessors, by_step_name, declared_inputs = build_step_graph(legacy_steps)

    explicit = any("inputs" in step for step in legacy_steps)
    if explicit:
        predecessors = {name: tuple(inputs) for name, inputs in raw_predecessors.items()}
    else:
        ordered_names = list(by_step_name)
        predecessors = {
            name: ((ordered_names[index - 1],) if index else ())
            for index, name in enumerate(ordered_names)
        }

    execution_order = tuple(
        name
        for wave in topo_order({name: list(names) for name, names in predecessors.items()})
        for name in wave
    )
    if explicit:
        predecessor_names = {
            predecessor
            for step_predecessors in predecessors.values()
            for predecessor in step_predecessors
        }
        terminal_steps = tuple(name for name in predecessors if name not in predecessor_names)
    else:
        terminal_steps = (execution_order[-1],)

    steps: list[CanonicalStepDefinition] = []
    for index, (name, legacy_step) in enumerate(by_step_name.items()):
        raw_step = (
            raw_steps[index] if isinstance(raw_steps, list) and index < len(raw_steps) else None
        )
        steps.append(
            CanonicalStepDefinition(
                name=name,
                type=str(legacy_step.get("type", "")),
                enabled=bool(legacy_step.get("enabled", True)),
                params=dict(legacy_step.get("params") or {}),
                predecessors=predecessors.get(name, ()),
                inputs_declared="inputs" in legacy_step,
                v2_name=str(legacy_step.get("name")),
                # V3-ready fields: a V2 step has no stable id and no structured
                # checkpoint; its canonical name is its human label, and the
                # legacy ``params.chk_from_step`` stays verbatim in ``params``.
                label=name,
                raw_inputs=legacy_step.get("inputs"),
                extensions=_step_extensions(raw_step),
            )
        )

    return CanonicalWorkflowDefinition(
        global_config=global_config,
        global_options=workflow.global_options,
        steps=tuple(steps),
        dependency_mode="explicit" if explicit else "implicit_linear",
        predecessors=predecessors,
        execution_order=execution_order,
        terminal_steps=terminal_steps,
        extensions=_root_extensions(workflow.raw),
        source_version=WORKFLOW_SCHEMA_VERSION,
    )
