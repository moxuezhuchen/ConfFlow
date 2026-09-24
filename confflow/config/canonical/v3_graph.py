#!/usr/bin/env python3

"""Authoritative V3 workflow graph.

The semantic/deplanning truth for a V3 workflow is the **declared persisted step
ids plus their declared ``inputs``** — never the tolerant structural fallback the
parser produces (``execution_order``/``terminal_steps`` fall back to document order
when a graph is unresolved or cyclic). This module validates the declared graph and
only *then* derives the authoritative ordering/topology, so an invalid graph yields
no authoritative graph at all.

Identity domain is the V3 persisted id (never a name or label). Ordering is
deterministic: within one topological wave, ids are sorted by the frozen order key
(a sequential ``sNNN`` sorts numerically, anything else lexically).
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .diagnostics import Diagnostic
from .workflow import CanonicalStepDefinition

__all__ = ["ValidatedWorkflowGraph", "build_validated_graph", "v3_id_order_key"]

_SEQUENTIAL_ID = re.compile(r"^s([0-9]+)$")

#: A fragment-local graph handle (e.g. ``#step1``) — never a persisted identity.
FRAGMENT_HANDLE_PREFIX = "#step"


def v3_id_order_key(step_id: str) -> tuple[int, int | str]:
    """Return the frozen canonical order key for a V3 step id."""
    match = _SEQUENTIAL_ID.match(step_id)
    if match:
        return (0, int(match.group(1)))
    return (1, step_id)


def is_persisted_id(value: str | None) -> bool:
    """Return whether ``value`` is a legal persisted V3 step id (not a fragment handle)."""
    if not value or not isinstance(value, str):
        return False
    if value.startswith(FRAGMENT_HANDLE_PREFIX):
        return False
    return re.fullmatch(r"[a-z][a-z0-9_]{0,63}", value) is not None


@dataclass(frozen=True)
class ValidatedWorkflowGraph:
    """The authoritative, acyclic dependency graph of a V3 definition."""

    step_ids: tuple[str, ...]
    predecessors: dict[str, tuple[str, ...]]
    successors: dict[str, tuple[str, ...]]
    roots: tuple[str, ...]
    terminals: tuple[str, ...]
    topological_order: tuple[str, ...]

    def ancestors(self, step_id: str) -> tuple[str, ...]:
        """Return every strict ancestor of ``step_id`` (transitive predecessors)."""
        seen: set[str] = set()
        frontier = list(self.predecessors.get(step_id, ()))
        while frontier:
            current = frontier.pop()
            if current in seen:
                continue
            seen.add(current)
            frontier.extend(self.predecessors.get(current, ()))
        return tuple(sorted(seen, key=v3_id_order_key))


def _error(code: str, path: str, message: str, step_ref: str | None) -> Diagnostic:
    return Diagnostic(code, "error", path, message, step_ref)


def build_validated_graph(
    steps: tuple[CanonicalStepDefinition, ...],
    *,
    require_ids: bool,
) -> tuple[ValidatedWorkflowGraph | None, list[Diagnostic]]:
    """Validate the declared graph and return the authoritative one, or diagnostics.

    ``require_ids`` selects the runnable profile: when true every step must carry a
    legal persisted id. When false (fragment) a document with any missing id has no
    resolvable id graph and is returned as ``(None, [])`` — the caller treats that
    as "graph checks not applicable", not as success.
    """
    diagnostics: list[Diagnostic] = []

    if not require_ids and any(step.id is None for step in steps):
        # A fragment that is not fully id'd has no resolvable id graph.
        return None, []

    ids: list[str] = []
    predecessors: dict[str, tuple[str, ...]] = {}
    for index, step in enumerate(steps, start=1):
        path = f"steps[{index}]"
        if step.id is None:
            if require_ids:
                diagnostics.append(
                    _error(
                        "workflow.v3.missing_id",
                        f"{path}.id",
                        f"runnable step {index} is missing a persistent 'id'",
                        None,
                    )
                )
            continue
        if not is_persisted_id(step.id):
            diagnostics.append(
                _error(
                    "workflow.v3.invalid_id",
                    f"{path}.id",
                    f"step id {step.id!r} is not a legal persisted V3 id",
                    None,
                )
            )
            continue
        if step.id in predecessors:
            diagnostics.append(
                _error(
                    "workflow.v3.duplicate_id",
                    f"{path}.id",
                    f"duplicate step id: {step.id!r}",
                    step.id,
                )
            )
            continue
        ids.append(step.id)
        predecessors[step.id] = tuple(step.predecessors)

    if diagnostics:
        return None, diagnostics

    id_set = set(ids)
    if not id_set:
        # No persisted ids at all (fragment): no id graph is resolvable.
        return None, []

    for step_id in ids:
        declared = predecessors[step_id]
        seen: set[str] = set()
        for reference in declared:
            if reference == step_id:
                diagnostics.append(
                    _error(
                        "workflow.v3.self_loop",
                        f"steps.{step_id}.inputs",
                        f"step {step_id!r} lists itself as an input",
                        step_id,
                    )
                )
            elif reference in seen:
                diagnostics.append(
                    _error(
                        "workflow.v3.duplicate_input",
                        f"steps.{step_id}.inputs",
                        f"step {step_id!r} lists input {reference!r} more than once",
                        step_id,
                    )
                )
            elif reference not in id_set:
                diagnostics.append(
                    _error(
                        "workflow.v3.unknown_input",
                        f"steps.{step_id}.inputs",
                        f"step {step_id!r} references an unknown input: {reference!r}",
                        step_id,
                    )
                )
            seen.add(reference)

    if diagnostics:
        return None, diagnostics

    # Kahn's algorithm; each wave is sorted by the frozen id order key.
    indegree = {step_id: len(predecessors[step_id]) for step_id in ids}
    successors: dict[str, list[str]] = {step_id: [] for step_id in ids}
    for step_id in ids:
        for reference in predecessors[step_id]:
            successors[reference].append(step_id)
    ready = sorted((step_id for step_id in ids if indegree[step_id] == 0), key=v3_id_order_key)
    order: list[str] = []
    while ready:
        wave = ready
        ready = []
        for step_id in wave:
            order.append(step_id)
            for child in successors[step_id]:
                indegree[child] -= 1
                if indegree[child] == 0:
                    ready.append(child)
        ready = sorted(ready, key=v3_id_order_key)

    if len(order) != len(ids):
        involved = sorted(
            (step_id for step_id in ids if step_id not in set(order)), key=v3_id_order_key
        )
        diagnostics.append(
            _error(
                "workflow.v3.dependency_cycle",
                "steps",
                f"workflow contains a dependency cycle involving: {', '.join(involved)}",
                involved[0] if involved else None,
            )
        )
        return None, diagnostics

    referenced: set[str] = set()
    for declared in predecessors.values():
        referenced.update(declared)
    roots = tuple(step_id for step_id in ids if not predecessors[step_id])
    terminals = tuple(
        sorted((step_id for step_id in ids if step_id not in referenced), key=v3_id_order_key)
    )

    graph = ValidatedWorkflowGraph(
        step_ids=tuple(sorted(ids, key=v3_id_order_key)),
        predecessors=predecessors,
        successors={
            step_id: tuple(sorted(children, key=v3_id_order_key))
            for step_id, children in successors.items()
        },
        roots=tuple(sorted(roots, key=v3_id_order_key)),
        terminals=terminals,
        topological_order=tuple(order),
    )
    return graph, []
