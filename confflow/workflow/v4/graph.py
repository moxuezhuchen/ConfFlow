#!/usr/bin/env python3

"""Typed binding graph: the only V4 dependency source.

Dependencies are derived exclusively from bindings; there is no parallel
inputs graph and no artifact side-channel.  Disabled steps are resolved here,
at compile time: a consumer binding whose producer is disabled is rewritten to
the disabled step's effective upstream source only when the producer contract
declares that output port a pure passthrough.  No runtime bypass algorithm
exists.

Determinism: edges, dependencies, and the topological order are derived from
stable ids and the :func:`order_key` tie-break (``sNNN`` sorts numerically,
everything else lexically), never from YAML array order.
"""

from __future__ import annotations

import heapq
import re
from dataclasses import dataclass
from typing import Any

from ...domain._immutable import FrozenDict
from ...domain.binding import (
    Binding,
    BindingSource,
    Cardinality,
    Pairing,
    PartialConsumption,
    PortKind,
    SelectorKind,
    SourceKind,
)
from ...domain.completion import CompletionMode, PartialOutputPolicy
from ...domain.diagnostics import Diagnostic, diagnostic_sort_key
from ...execution.contracts import PortSpec
from .diagnostics import DiagnosticCode, DiagnosticReason, error, warning
from .validation import ValidatedDefinition, ValidatedStep, run_input_port_spec

__all__ = [
    "BindingGraph",
    "GraphResult",
    "ResolvedEdge",
    "build_binding_graph",
    "order_key",
]

_ORDER_PATTERN = re.compile(r"^s(\d+)$")

_STRUCTURE_PAIRINGS = frozenset({Pairing.SINGLE, Pairing.PER_STRUCTURE, Pairing.BY_GROUP_KEY})
_VALUE_PAIRINGS = frozenset({Pairing.SINGLE, Pairing.BY_SUBJECT})

#: Cardinalities a binding may declare for each port-contract cardinality.
#: A binding may strengthen an optional port to required (``one``) or relax a
#: required port to zero-or-more (``many``); it can never contradict intent.
_BINDING_CARDINALITIES: dict[Cardinality, frozenset[Cardinality]] = {
    Cardinality.ONE: frozenset({Cardinality.ONE}),
    Cardinality.OPTIONAL: frozenset({Cardinality.OPTIONAL, Cardinality.ONE}),
    Cardinality.ONE_OR_MORE: frozenset(
        {Cardinality.ONE_OR_MORE, Cardinality.ONE, Cardinality.MANY}
    ),
    Cardinality.MANY: frozenset({Cardinality.MANY, Cardinality.ONE_OR_MORE, Cardinality.ONE}),
}


def order_key(step_id: str) -> tuple[int, int, str]:
    """Return the deterministic ordering key of a step id.

    ``sNNN`` ids sort numerically before other ids, which sort lexically; this
    keeps generated ids stable without depending on document order.
    """
    match = _ORDER_PATTERN.match(step_id)
    if match is not None:
        return (0, int(match.group(1)), "")
    return (1, 0, step_id)


@dataclass(frozen=True, slots=True)
class ResolvedEdge:
    """A typed, registry-resolved binding edge.

    ``source`` is the *effective* source after disabled passthrough rewriting;
    ``original_source`` records what the document declared.
    """

    target_step_id: str
    target_port: PortSpec
    source: BindingSource
    source_port: PortSpec
    pairing: Pairing
    cardinality: Cardinality
    partial_consumption: PartialConsumption | None = None
    via_disabled: tuple[str, ...] = ()
    original_source: BindingSource | None = None

    @property
    def is_run_input(self) -> bool:
        """Return whether the effective source is a run input."""
        return self.source.kind is SourceKind.RUN_INPUT

    @property
    def source_step_id(self) -> str | None:
        """Return the effective producer step id, if any."""
        return self.source.step_id if self.source.kind is SourceKind.STEP_OUTPUT else None

    def to_payload(self) -> dict[str, Any]:
        """Return a canonical, JSON-compatible representation."""
        return {
            "target_step_id": self.target_step_id,
            "target_port": self.target_port.name,
            "source": self.source.to_dict(),
            "source_port": self.source_port.name,
            "pairing": self.pairing.value,
            "cardinality": self.cardinality.value,
            "partial_consumption": (
                self.partial_consumption.value if self.partial_consumption is not None else None
            ),
            "via_disabled": list(self.via_disabled),
        }


@dataclass(frozen=True, slots=True)
class BindingGraph:
    """Resolved dependency graph of enabled steps."""

    steps: tuple[ValidatedStep, ...]
    all_steps: FrozenDict
    run_input_ports: FrozenDict
    edges: tuple[ResolvedEdge, ...]
    disabled_step_ids: tuple[str, ...]
    execution_order: tuple[str, ...]
    dependencies: FrozenDict
    successors: FrozenDict
    diagnostics: tuple[Diagnostic, ...] = ()

    def step(self, step_id: str) -> ValidatedStep | None:
        """Return the validated step with *step_id*, or ``None``."""
        return self.all_steps.get(step_id)

    def incoming(self, step_id: str) -> tuple[ResolvedEdge, ...]:
        """Return the effective edges entering *step_id*."""
        return tuple(edge for edge in self.edges if edge.target_step_id == step_id)

    def dependency_ids(self, step_id: str) -> tuple[str, ...]:
        """Return the dependency step ids of *step_id*."""
        value = self.dependencies.get(step_id)
        return tuple(value) if value is not None else ()

    def successor_ids(self, step_id: str) -> tuple[str, ...]:
        """Return the dependent step ids of *step_id*."""
        value = self.successors.get(step_id)
        return tuple(value) if value is not None else ()

    def to_payload(self) -> dict[str, Any]:
        """Return a canonical, JSON-compatible representation."""
        return {
            "execution_order": list(self.execution_order),
            "disabled_step_ids": list(self.disabled_step_ids),
            "edges": [edge.to_payload() for edge in self.edges],
            "dependencies": {
                step_id: list(self.dependency_ids(step_id)) for step_id in self.execution_order
            },
        }


@dataclass(frozen=True, slots=True)
class GraphResult:
    """Outcome of binding-graph construction."""

    graph: BindingGraph | None
    diagnostics: tuple[Diagnostic, ...] = ()

    @property
    def ok(self) -> bool:
        """Return whether the graph was built."""
        return self.graph is not None


def _declared_ports(step: ValidatedStep) -> tuple[str, ...]:
    return tuple(port.name for port in step.input_ports)


def _resolve_edge(
    target: ValidatedStep,
    binding: Binding,
    all_steps: dict[str, ValidatedStep],
    run_ports: dict[str, PortSpec],
) -> tuple[ResolvedEdge | None, list[Diagnostic]]:
    diagnostics: list[Diagnostic] = []
    field_path = f"steps.{target.step_id}.bindings.{binding.target_port}"
    target_port = target.input_port(binding.target_port)
    if target_port is None:
        diagnostics.append(
            error(
                DiagnosticCode.BINDING_ERROR,
                DiagnosticReason.UNKNOWN_PORT,
                f"step {target.step_id!r} has no input port {binding.target_port!r}",
                step_id=target.step_id,
                field_path=field_path,
                details={"declared_ports": list(_declared_ports(target))},
            )
        )
        return None, diagnostics
    source = binding.source
    if source.kind is SourceKind.RUN_INPUT:
        source_port = run_ports.get(source.port)
        if source_port is None:
            diagnostics.append(
                error(
                    DiagnosticCode.BINDING_ERROR,
                    DiagnosticReason.UNKNOWN_RUN_INPUT,
                    f"unknown run input {source.port!r}",
                    step_id=target.step_id,
                    field_path=field_path,
                    details={"declared_inputs": sorted(run_ports)},
                )
            )
            return None, diagnostics
        producer: ValidatedStep | None = None
    else:
        producer = all_steps.get(source.step_id or "")
        if producer is None:
            diagnostics.append(
                error(
                    DiagnosticCode.BINDING_ERROR,
                    DiagnosticReason.UNKNOWN_SOURCE_STEP,
                    f"binding references unknown step {source.step_id!r}",
                    step_id=target.step_id,
                    field_path=field_path,
                    details={"referenced_id": source.step_id},
                )
            )
            return None, diagnostics
        source_port = producer.output_port(source.port)
        if source_port is None:
            diagnostics.append(
                error(
                    DiagnosticCode.BINDING_ERROR,
                    DiagnosticReason.UNKNOWN_PORT,
                    f"step {producer.step_id!r} has no output port {source.port!r}",
                    step_id=target.step_id,
                    field_path=field_path,
                    details={
                        "referenced_port": source.port,
                        "declared_ports": [port.name for port in producer.output_ports],
                    },
                )
            )
            return None, diagnostics
    if source_port.kind is not target_port.kind:
        diagnostics.append(
            error(
                DiagnosticCode.BINDING_ERROR,
                DiagnosticReason.KIND_MISMATCH,
                f"binding kind mismatch: {source_port.kind.value!r} -> "
                f"{target_port.kind.value!r}",
                step_id=target.step_id,
                field_path=field_path,
                details={
                    "expected_kind": target_port.kind.value,
                    "actual_kind": source_port.kind.value,
                },
            )
        )
        return None, diagnostics
    diagnostics.extend(_validate_selector(target, binding, source_port, field_path))
    pairing = binding.pairing if binding.pairing is not None else target_port.pairing
    allowed = _STRUCTURE_PAIRINGS if target_port.kind is PortKind.STRUCTURE else _VALUE_PAIRINGS
    if pairing not in allowed:
        diagnostics.append(
            error(
                DiagnosticCode.BINDING_ERROR,
                DiagnosticReason.PAIRING_NOT_ALLOWED,
                f"pairing {pairing.value!r} is not valid for a " f"{target_port.kind.value} port",
                step_id=target.step_id,
                field_path=field_path,
                details={"allowed": sorted(item.value for item in allowed)},
            )
        )
        return None, diagnostics
    allowed_cardinalities = _BINDING_CARDINALITIES[target_port.cardinality]
    if binding.cardinality is not None and binding.cardinality not in allowed_cardinalities:
        diagnostics.append(
            error(
                DiagnosticCode.CARDINALITY_ERROR,
                DiagnosticReason.CARDINALITY_MISMATCH,
                f"binding cardinality {binding.cardinality.value!r} conflicts with "
                f"port contract {target_port.cardinality.value!r}",
                step_id=target.step_id,
                field_path=field_path,
                details={
                    "declared": binding.cardinality.value,
                    "contract": target_port.cardinality.value,
                    "allowed": sorted(item.value for item in allowed_cardinalities),
                },
            )
        )
        return None, diagnostics
    effective_cardinality = binding.cardinality or target_port.cardinality
    if producer is not None:
        policy = producer.definition.completion
        if policy.mode is CompletionMode.ALLOW_PARTIAL:
            if policy.partial_output is PartialOutputPolicy.DENY:
                diagnostics.append(
                    error(
                        DiagnosticCode.COMPILE_ERROR,
                        DiagnosticReason.PARTIAL_OUTPUT_DENIED,
                        f"producer {producer.step_id!r} forbids downstream consumption "
                        "of partial output",
                        step_id=target.step_id,
                        field_path=field_path,
                        details={
                            "producer_step_id": producer.step_id,
                            "producer_mode": policy.mode.value,
                        },
                    )
                )
                return None, diagnostics
            if binding.partial_consumption is None:
                diagnostics.append(
                    error(
                        DiagnosticCode.COMPILE_ERROR,
                        DiagnosticReason.PARTIAL_CONSUMPTION_UNDEFINED,
                        "a binding from an allow_partial producer must declare "
                        "partial_consumption",
                        step_id=target.step_id,
                        field_path=field_path,
                        details={
                            "producer_step_id": producer.step_id,
                            "producer_mode": policy.mode.value,
                        },
                    )
                )
                return None, diagnostics
    edge = ResolvedEdge(
        target_step_id=target.step_id,
        target_port=target_port,
        source=source,
        source_port=source_port,
        pairing=pairing,
        cardinality=effective_cardinality,
        partial_consumption=binding.partial_consumption,
        original_source=source,
    )
    return edge, diagnostics


def _validate_selector(
    target: ValidatedStep,
    binding: Binding,
    source_port: PortSpec,
    field_path: str,
) -> list[Diagnostic]:
    selector = binding.source.selector
    if selector.kind is SelectorKind.ROLE:
        if source_port.kind is not PortKind.ARTIFACT:
            return [
                error(
                    DiagnosticCode.BINDING_ERROR,
                    DiagnosticReason.SELECTOR_NOT_APPLICABLE,
                    f"role selector is not valid on a {source_port.kind.value} port",
                    step_id=target.step_id,
                    field_path=field_path,
                    details={"port_kind": source_port.kind.value},
                )
            ]
        if selector.role not in source_port.roles:
            return [
                error(
                    DiagnosticCode.BINDING_ERROR,
                    DiagnosticReason.UNKNOWN_SELECTOR_ROLE,
                    f"artifact role {selector.role!r} is not advertised by the producer port",
                    step_id=target.step_id,
                    field_path=field_path,
                    details={
                        "role": selector.role,
                        "advertised_roles": list(source_port.roles),
                    },
                )
            ]
        return []
    if selector.kind is SelectorKind.IDS:
        if source_port.kind is PortKind.RESULT:
            return [
                error(
                    DiagnosticCode.BINDING_ERROR,
                    DiagnosticReason.SELECTOR_NOT_APPLICABLE,
                    "id selector is not valid on a result port",
                    step_id=target.step_id,
                    field_path=field_path,
                    details={"port_kind": source_port.kind.value},
                )
            ]
        return []
    if (
        source_port.kind is PortKind.ARTIFACT
        and selector.kind is SelectorKind.ALL
        and len(source_port.roles) > 1
    ):
        return [
            error(
                DiagnosticCode.BINDING_ERROR,
                DiagnosticReason.ROLE_REQUIRED,
                "an artifact port advertising multiple roles requires an explicit role selector",
                step_id=target.step_id,
                field_path=field_path,
                details={"available_roles": list(source_port.roles)},
            )
        ]
    return []


def _find_cycle(
    nodes: tuple[str, ...],
    adjacency: dict[str, tuple[str, ...]],
) -> tuple[str, ...] | None:
    color: dict[str, int] = {}
    parent: dict[str, str | None] = {}
    for start in nodes:
        if color.get(start, 0) != 0:
            continue
        color[start] = 1
        parent[start] = None
        stack: list[tuple[str, int]] = [(start, 0)]
        while stack:
            node, index = stack[-1]
            neighbors = adjacency.get(node, ())
            if index < len(neighbors):
                stack[-1] = (node, index + 1)
                neighbor = neighbors[index]
                state = color.get(neighbor, 0)
                if state == 1:
                    cycle = [neighbor, node]
                    current = parent.get(node)
                    while current is not None and current != neighbor:
                        cycle.append(current)
                        current = parent.get(current)
                    cycle.reverse()
                    return tuple(cycle)
                if state == 0:
                    color[neighbor] = 1
                    parent[neighbor] = node
                    stack.append((neighbor, 0))
            else:
                color[node] = 2
                stack.pop()
    return None


def _resolve_passthrough(
    source: BindingSource,
    all_steps: dict[str, ValidatedStep],
    raw_edges: dict[tuple[str, str], ResolvedEdge],
    run_ports: dict[str, PortSpec],
    disabled_ids: frozenset[str],
) -> tuple[BindingSource, PortSpec | None, tuple[str, ...], list[Diagnostic]]:
    via: list[str] = []
    diagnostics: list[Diagnostic] = []
    current = source
    current_port: PortSpec | None = None
    seen: set[str] = set()
    while current.kind is SourceKind.STEP_OUTPUT and current.step_id in disabled_ids:
        disabled_id = current.step_id
        if disabled_id in seen:
            diagnostics.append(
                error(
                    DiagnosticCode.COMPILE_ERROR,
                    DiagnosticReason.DISABLED_PASSTHROUGH_AMBIGUOUS,
                    f"disabled passthrough cycle detected at {disabled_id!r}",
                    step_id=disabled_id,
                    details={"candidates": sorted(seen)},
                )
            )
            return current, current_port, tuple(via), diagnostics
        seen.add(disabled_id)
        disabled_step = all_steps[disabled_id]
        input_port_name = disabled_step.executor.passthrough_ports.get(current.port)
        if input_port_name is None:
            diagnostics.append(
                error(
                    DiagnosticCode.CAPABILITY_ERROR,
                    DiagnosticReason.DISABLED_CAPABILITY_LOST,
                    f"disabled step {disabled_id!r} cannot forward output port "
                    f"{current.port!r}: it is not a declared pure passthrough",
                    step_id=disabled_id,
                    details={"port": current.port},
                )
            )
            return current, current_port, tuple(via), diagnostics
        if current.selector.kind is not SelectorKind.ALL:
            diagnostics.append(
                error(
                    DiagnosticCode.COMPILE_ERROR,
                    DiagnosticReason.SELECTOR_COMPOSITION_UNSUPPORTED,
                    "selector composition through a disabled passthrough is not supported",
                    step_id=disabled_id,
                    details={"selector": current.selector.to_dict()},
                )
            )
            return current, current_port, tuple(via), diagnostics
        upstream = raw_edges.get((disabled_id, input_port_name))
        if upstream is None:
            diagnostics.append(
                error(
                    DiagnosticCode.COMPILE_ERROR,
                    DiagnosticReason.DISABLED_ROOT_NO_SOURCE,
                    f"disabled step {disabled_id!r} has no upstream binding for "
                    f"port {input_port_name!r}",
                    step_id=disabled_id,
                    details={"input_port": input_port_name},
                )
            )
            return current, current_port, tuple(via), diagnostics
        via.append(disabled_id)
        current = upstream.source
        current_port = upstream.source_port
    if current_port is None:
        if current.kind is SourceKind.RUN_INPUT:
            current_port = run_ports.get(current.port)
        elif current.kind is SourceKind.STEP_OUTPUT and current.step_id is not None:
            producer = all_steps.get(current.step_id)
            current_port = producer.output_port(current.port) if producer else None
    if current_port is None:
        diagnostics.append(
            error(
                DiagnosticCode.COMPILE_ERROR,
                DiagnosticReason.DISABLED_ROOT_NO_SOURCE,
                f"cannot resolve a source port for {current.to_dict()!r}",
                details={"source": current.to_dict()},
            )
        )
    return current, current_port, tuple(via), diagnostics


def build_binding_graph(validated: ValidatedDefinition) -> GraphResult:
    """Build and validate the typed binding graph of a validated definition."""
    definition = validated.definition
    all_steps: dict[str, ValidatedStep] = {step.step_id: step for step in validated.steps}
    ordered_ids = tuple(sorted(all_steps, key=order_key))
    run_ports: dict[str, PortSpec] = {
        declaration.name: run_input_port_spec(declaration) for declaration in definition.inputs
    }
    diagnostics: list[Diagnostic] = []
    raw_edges: dict[tuple[str, str], ResolvedEdge] = {}
    for step in validated.steps:
        for binding in step.definition.bindings:
            edge, edge_diagnostics = _resolve_edge(step, binding, all_steps, run_ports)
            diagnostics.extend(edge_diagnostics)
            if edge is not None:
                raw_edges[(step.step_id, binding.target_port)] = edge
    if any(item.is_error for item in diagnostics):
        return GraphResult(None, tuple(sorted(diagnostics, key=diagnostic_sort_key)))

    adjacency: dict[str, tuple[str, ...]] = {}
    for step_id in ordered_ids:
        neighbors = sorted(
            {
                edge.source.step_id
                for edge in raw_edges.values()
                if edge.target_step_id == step_id
                and edge.source.kind is SourceKind.STEP_OUTPUT
                and edge.source.step_id is not None
            },
            key=order_key,
        )
        adjacency[step_id] = tuple(neighbors)
    cycle = _find_cycle(ordered_ids, adjacency)
    if cycle is not None:
        diagnostics.append(
            error(
                DiagnosticCode.COMPILE_ERROR,
                DiagnosticReason.DEPENDENCY_CYCLE,
                "binding dependency cycle: " + " -> ".join(cycle),
                step_id=cycle[0],
                details={"cycle_steps": list(cycle)},
            )
        )
        return GraphResult(None, tuple(sorted(diagnostics, key=diagnostic_sort_key)))

    disabled_ids = frozenset(
        step.step_id for step in validated.steps if not step.definition.enabled
    )
    effective: list[ResolvedEdge] = []
    for step in validated.steps:
        if step.step_id in disabled_ids:
            continue
        for binding in step.definition.bindings:
            edge = raw_edges.get((step.step_id, binding.target_port))
            if edge is None:
                continue
            source = edge.source
            source_port = edge.source_port
            via: tuple[str, ...] = ()
            if source.kind is SourceKind.STEP_OUTPUT and source.step_id in disabled_ids:
                source, source_port, via, resolution_diagnostics = _resolve_passthrough(
                    source, all_steps, raw_edges, run_ports, disabled_ids
                )
                diagnostics.extend(resolution_diagnostics)
                if any(item.is_error for item in resolution_diagnostics):
                    continue
                assert source_port is not None
            effective.append(
                ResolvedEdge(
                    target_step_id=edge.target_step_id,
                    target_port=edge.target_port,
                    source=source,
                    source_port=source_port,
                    pairing=edge.pairing,
                    cardinality=edge.cardinality,
                    partial_consumption=edge.partial_consumption,
                    via_disabled=via,
                    original_source=edge.original_source,
                )
            )
    if any(item.is_error for item in diagnostics):
        return GraphResult(None, tuple(sorted(diagnostics, key=diagnostic_sort_key)))

    effective.sort(key=lambda item: (order_key(item.target_step_id), item.target_port.name))

    dependencies: dict[str, tuple[str, ...]] = {}
    successors: dict[str, list[str]] = {step_id: [] for step_id in ordered_ids}
    for step_id in ordered_ids:
        if step_id in disabled_ids:
            continue
        dependency_ids = sorted(
            {
                edge.source.step_id
                for edge in effective
                if edge.target_step_id == step_id and edge.source.step_id is not None
            },
            key=order_key,
        )
        dependencies[step_id] = tuple(dependency_ids)
        for dependency in dependency_ids:
            successors[dependency].append(step_id)
    successor_map: dict[str, tuple[str, ...]] = {
        step_id: tuple(sorted(set(items), key=order_key)) for step_id, items in successors.items()
    }

    indegree = {step_id: len(dependencies.get(step_id, ())) for step_id in ordered_ids}
    ready: list[tuple[tuple[int, int, str], str]] = [
        (order_key(step_id), step_id)
        for step_id in ordered_ids
        if step_id not in disabled_ids and indegree[step_id] == 0
    ]
    heapq.heapify(ready)
    execution_order: list[str] = []
    while ready:
        _, step_id = heapq.heappop(ready)
        execution_order.append(step_id)
        for successor in successor_map.get(step_id, ()):
            indegree[successor] -= 1
            if indegree[successor] == 0 and successor not in disabled_ids:
                heapq.heappush(ready, (order_key(successor), successor))
    if len(execution_order) != len(ordered_ids) - len(disabled_ids):
        diagnostics.append(
            error(
                DiagnosticCode.COMPILE_ERROR,
                DiagnosticReason.DEPENDENCY_CYCLE,
                "effective binding graph is not acyclic",
                details={"cycle_steps": sorted(set(ordered_ids) - set(execution_order))},
            )
        )
        return GraphResult(None, tuple(sorted(diagnostics, key=diagnostic_sort_key)))

    outgoing_targets = {
        edge.source.step_id
        for edge in raw_edges.values()
        if edge.source.kind is SourceKind.STEP_OUTPUT
    }
    for step_id in sorted(disabled_ids, key=order_key):
        if step_id not in outgoing_targets:
            diagnostics.append(
                warning(
                    DiagnosticCode.COMPILE_ERROR,
                    DiagnosticReason.DISABLED_STEP_UNUSED,
                    f"disabled step {step_id!r} has no consumers",
                    step_id=step_id,
                )
            )

    enabled_steps = tuple(all_steps[step_id] for step_id in execution_order)
    graph = BindingGraph(
        steps=enabled_steps,
        all_steps=FrozenDict(all_steps),
        run_input_ports=FrozenDict(run_ports),
        edges=tuple(effective),
        disabled_step_ids=tuple(sorted(disabled_ids, key=order_key)),
        execution_order=tuple(execution_order),
        dependencies=FrozenDict(dependencies),
        successors=FrozenDict(successor_map),
        diagnostics=tuple(sorted(diagnostics, key=diagnostic_sort_key)),
    )
    return GraphResult(graph, graph.diagnostics)
