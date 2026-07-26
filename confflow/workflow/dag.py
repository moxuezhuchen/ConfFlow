#!/usr/bin/env python3

"""Minimal helpers for explicit workflow step dependencies."""

from __future__ import annotations

from graphlib import CycleError, TopologicalSorter
from typing import Any

from ..core.exceptions import ConfFlowError

__all__ = [
    "build_step_graph",
    "topo_order",
]


def _canonical_step_name(step: dict[str, Any], index: int) -> str:
    raw_name = step.get("name")
    if raw_name is not None:
        name = str(raw_name).strip()
        if name:
            return name
    return f"step_{index:02d}"


def _normalize_inputs(raw_inputs: Any) -> list[str]:
    if raw_inputs is None:
        return []
    if isinstance(raw_inputs, str):
        value = raw_inputs.strip()
        return [value] if value else []
    if isinstance(raw_inputs, (list, tuple)):
        result: list[str] = []
        for item in raw_inputs:
            if item is None:
                continue
            value = str(item).strip()
            if value:
                result.append(value)
        return result
    value = str(raw_inputs).strip()
    return [value] if value else []


def build_step_graph(
    steps: list[dict[str, Any]],
) -> tuple[dict[str, list[str]], dict[str, dict[str, Any]], dict[str, list[str]]]:
    """Build canonical step names and normalized predecessor lists.

    The returned predecessor map reflects only the ``inputs`` fields. The
    engine applies its legacy linear fallback when no step declares that
    field. A step without ``inputs`` is therefore a root whenever explicit
    DAG mode is selected by the engine.

    Returns
    -------
    predecessors : dict[str, list[str]]
        Canonical step name to declared predecessor names.
    by_name : dict[str, dict[str, Any]]
        Original step dictionaries indexed by canonical name.
    declared_inputs : dict[str, list[str]]
        Canonical step name to normalized ``inputs`` values.

    Raises
    ------
    ConfFlowError
        If names are duplicated or a predecessor is unknown.
    """
    predecessors: dict[str, list[str]] = {}
    by_name: dict[str, dict[str, Any]] = {}
    declared_inputs: dict[str, list[str]] = {}

    for index, step in enumerate(steps, start=1):
        name = _canonical_step_name(step, index)
        if name in by_name:
            raise ConfFlowError(f"workflow step names must be unique; duplicate name: {name!r}")
        inputs = _normalize_inputs(step.get("inputs"))
        by_name[name] = step
        predecessors[name] = list(inputs)
        declared_inputs[name] = list(inputs)

    known_names = set(by_name)
    for name, inputs in predecessors.items():
        unknown = [predecessor for predecessor in inputs if predecessor not in known_names]
        if unknown:
            raise ConfFlowError(
                f"workflow step {name!r} has unknown predecessor(s): {', '.join(map(repr, unknown))}"
            )

    return predecessors, by_name, declared_inputs


def topo_order(predecessors: dict[str, list[str]]) -> list[list[str]]:
    """Return deterministic topological waves for a predecessor map."""
    if not predecessors:
        return []

    known_names = set(predecessors)
    for name, inputs in predecessors.items():
        unknown = [predecessor for predecessor in inputs if predecessor not in known_names]
        if unknown:
            raise ConfFlowError(
                f"workflow step {name!r} has unknown predecessor(s): {', '.join(map(repr, unknown))}"
            )

    sorter = TopologicalSorter(predecessors)
    try:
        sorter.prepare()
    except CycleError as exc:
        raise ConfFlowError(f"workflow contains a dependency cycle: {exc}") from exc

    waves: list[list[str]] = []
    while sorter.is_active():
        ready = sorted(sorter.get_ready())
        if not ready:
            raise ConfFlowError("workflow topological ordering produced no ready steps")
        waves.append(ready)
        sorter.done(*ready)
    return waves
