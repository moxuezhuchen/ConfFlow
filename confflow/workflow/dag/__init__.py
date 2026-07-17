#!/usr/bin/env python3
"""Workflow DAG introspection and conditional execution - Phase 1b.

Provides:
- DAG graph representation (nodes = steps, edges = data flow).
- Cycle detection.
- Topological sort with deterministic tie-breaking.
- Conditional step enablement based on step results.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

__all__ = [
    "DAGStep",
    "DAGGraph",
    "WorkflowDAG",
]


@dataclass
class DAGStep:
    """A single step node in the workflow DAG."""

    name: str
    step_type: str
    params: dict[str, Any] = field(default_factory=dict)
    enabled: bool = True
    depends_on: list[str] = field(default_factory=list)


@dataclass
class DAGGraph:
    """Directed acyclic graph of workflow steps."""

    steps: list[DAGStep] = field(default_factory=list)
    edges: list[tuple[str, str]] = field(default_factory=list)

    def add_step(self, step: DAGStep) -> None:
        """Add a step node to the graph."""
        self.steps.append(step)

    def add_edge(self, from_step: str, to_step: str) -> None:
        """Add a directed edge (from_step -> to_step)."""
        self.edges.append((from_step, to_step))

    def validate(self) -> list[str]:
        """Return list of validation error messages; empty if valid."""
        errors: list[str] = []
        step_names = {s.name for s in self.steps}

        for step in self.steps:
            for dep in step.depends_on:
                if dep not in step_names:
                    errors.append(f"Step '{step.name}' depends on unknown step '{dep}'")

        for src, dst in self.edges:
            if src not in step_names:
                errors.append(f"Edge source '{src}' does not exist")
            if dst not in step_names:
                errors.append(f"Edge destination '{dst}' does not exist")

        visiting: set[str] = set()
        visited: set[str] = set()

        def dfs(node: str, path: list[str]) -> list[list[str]]:
            if node in visiting:
                return [path + [node]]
            if node in visited:
                return []
            visiting.add(node)
            children = [dst for src, dst in self.edges if src == node]
            for child in children:
                result = dfs(child, path + [node])
                if result:
                    return result
            visiting.remove(node)
            visited.add(node)
            return []

        for step in self.steps:
            cycle_result = dfs(step.name, [])
            if cycle_result:
                errors.append(f"Cycle detected: {' -> '.join(cycle_result[0])}")
        return errors

    def topological_sort(self) -> list[str]:
        """Return step names in topologically sorted order.

        Raises ValueError if a cycle is detected.
        """
        errors = self.validate()
        if any("Cycle" in e for e in errors):
            raise ValueError(f"Cannot sort cyclic graph: {errors}")

        in_degree: dict[str, int] = {s.name: 0 for s in self.steps}
        adjacency: dict[str, list[str]] = {s.name: [] for s in self.steps}
        for src, dst in self.edges:
            adjacency[src].append(dst)
            in_degree[dst] += 1

        queue: list[str] = sorted([s for s, d in in_degree.items() if d == 0])
        sorted_names: list[str] = []
        while queue:
            node = queue.pop(0)
            sorted_names.append(node)
            children = sorted(adjacency[node])
            for child in children:
                in_degree[child] -= 1
                if in_degree[child] == 0:
                    if child not in queue:
                        queue.append(child)
            queue.sort()
        return sorted_names


_CONDITION_PATTERN = re.compile(
    r"prev\.(\w+)\s*(==|!=|<=|>=|<|>)\s*(.+)"
)


class WorkflowDAG:
    """High-level workflow DAG builder and analyser.

    Converts a list of workflow step dicts (as loaded from YAML) into
    an analysable DAG graph, and can evaluate conditional enablement.
    """

    def __init__(self, steps: list[dict[str, Any]]) -> None:
        self._steps = steps
        self._graph: DAGGraph = self._build_graph(steps)

    @staticmethod
    def _build_graph(steps: list[dict[str, Any]]) -> DAGGraph:
        graph = DAGGraph()
        step_names_seen: list[str] = []

        for step in steps:
            name = str(step.get("name", f"step_{len(step_names_seen)}"))
            step_type = str(step.get("type", "calc"))
            params = dict(step.get("params") or {})
            enabled = bool(step.get("enabled", True))
            depends_on: list[str] = []

            raw_deps = step.get("depends_on")
            if raw_deps is not None:
                if isinstance(raw_deps, str):
                    raw_deps = [raw_deps]
                if isinstance(raw_deps, list):
                    for dep in raw_deps:
                        dep_str = str(dep).strip()
                        if dep_str:
                            depends_on.append(dep_str)
            elif len(step_names_seen) > 0:
                depends_on.append(step_names_seen[-1])

            graph.add_step(DAGStep(
                name=name,
                step_type=step_type,
                params=params,
                enabled=enabled,
                depends_on=depends_on,
            ))
            step_names_seen.append(name)

            for dep in depends_on:
                graph.add_edge(dep, name)

        return graph

    @property
    def graph(self) -> DAGGraph:
        """The underlying DAG graph."""
        return self._graph

    def validate(self) -> list[str]:
        """Return validation errors."""
        return self._graph.validate()

    def topological_sort(self) -> list[str]:
        """Return step names in execution order."""
        return self._graph.topological_sort()

    def get_step(self, name: str) -> dict[str, Any] | None:
        """Return the original step dict for a given name."""
        for step in self._steps:
            if str(step.get("name", "")) == name:
                return step
        return None

    def evaluate_conditions(
        self,
        results: dict[str, dict[str, Any]],
    ) -> dict[str, bool]:
        """Evaluate step-level `when` conditions against step results."""
        decisions: dict[str, bool] = {}
        for step in self._steps:
            name = str(step.get("name", ""))
            condition_expr = step.get("when")
            if condition_expr is None:
                decisions[name] = True
                continue
            try:
                m = _CONDITION_PATTERN.match(str(condition_expr).strip())
                if not m:
                    decisions[name] = True
                    continue
                field_name = m.group(1)
                op = m.group(2)
                raw_value = m.group(3).strip()
                prev_result = results.get(name, {})
                actual = prev_result.get(field_name)
                if actual is None:
                    decisions[name] = False
                    continue
                try:
                    expected = float(raw_value)
                    actual_f = float(actual)
                    comparisons = {
                        "==": actual_f == expected,
                        "!=": actual_f != expected,
                        ">=": actual_f >= expected,
                        "<=": actual_f <= expected,
                        ">": actual_f > expected,
                        "<": actual_f < expected,
                    }
                    decisions[name] = comparisons.get(op, False)
                except ValueError:
                    left = str(actual).strip().lower()
                    right = raw_value.strip('"').strip("'").lower()
                    if op == "==":
                        decisions[name] = left == right
                    else:
                        decisions[name] = left != right
            except Exception:
                decisions[name] = True
        return decisions
