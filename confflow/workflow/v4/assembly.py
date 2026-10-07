#!/usr/bin/env python3

"""Synthetic V4 work-item assembly.

Assembly turns an :class:`~confflow.workflow.v4.plan.ExecutionPlan` plus run
inputs (and, for synthetic tests, materialized producer outputs) into
deterministic :class:`~confflow.domain.work_item.WorkItem` records.

Everything is matched by stable identity:

- structure fan-out uses one item per structure entity;
- checkpoint-like artifacts are matched to structures by
  ``subject_structure_id``;
- ambiguity is a diagnostic, never a positional guess.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ...domain._immutable import FrozenDict
from ...domain.artifact import ArtifactRef, ArtifactSet
from ...domain.binding import (
    Cardinality,
    Pairing,
    PartialConsumption,
    PortKind,
    SelectorKind,
    SourceKind,
)
from ...domain.completion import StepStatus, WorkItemStatus
from ...domain.diagnostics import Diagnostic, diagnostic_sort_key
from ...domain.errors import DomainError
from ...domain.result import ResultSet
from ...domain.structure import (
    StructureRecord,
    StructureSet,
    check_structure_id_conflicts,
)
from ...domain.work_item import (
    WorkItem,
    WorkItemInputs,
    make_work_item_id,
    work_item_semantic_digest,
)
from .diagnostics import DiagnosticCode, DiagnosticReason, error, warning
from .graph import ResolvedEdge
from .plan import ExecutionPlan, PlannedStep
from .scientific import EffectiveScientificParameters, resolve_scientific_parameters

__all__ = [
    "AssemblyResult",
    "MaterializedOutputs",
    "RunInputs",
    "StepOutputs",
    "assemble_work_items",
]


@dataclass(frozen=True, slots=True)
class RunInputs:
    """Named run inputs supplied to assembly."""

    structures: FrozenDict = field(default_factory=FrozenDict)
    artifacts: FrozenDict = field(default_factory=FrozenDict)
    results: FrozenDict = field(default_factory=FrozenDict)

    def __post_init__(self) -> None:
        for name, expected in (
            ("structures", StructureSet),
            ("artifacts", ArtifactSet),
            ("results", ResultSet),
        ):
            mapping = getattr(self, name)
            if not isinstance(mapping, FrozenDict):
                object.__setattr__(self, name, FrozenDict(mapping))
                mapping = getattr(self, name)
            for key, value in mapping.items():
                if not isinstance(value, expected):
                    raise DomainError(
                        f"run input {key!r} must hold {expected.__name__}, "
                        f"got {type(value).__name__}"
                    )

    @classmethod
    def empty(cls) -> RunInputs:
        """Return empty run inputs."""
        return cls()

    def to_dict(self) -> dict[str, Any]:
        """Return a canonical, JSON-compatible representation."""
        return {
            "structures": {
                name: [record.to_dict() for record in value]
                for name, value in self.structures.items()
            },
            "artifacts": {
                name: [record.to_dict() for record in value]
                for name, value in self.artifacts.items()
            },
            "results": {
                name: [record.to_dict() for record in value] for name, value in self.results.items()
            },
        }


@dataclass(frozen=True, slots=True)
class StepOutputs:
    """Materialized outputs of one producer step.

    Carries the actual producer :class:`~confflow.domain.completion.StepStatus`
    and enough completion provenance for downstream gating: consumers check
    the real failed/partial/cancelled state instead of blindly consuming the
    successful subset.  ``status`` is required for production binding: an
    unknown (``None``) status is a legacy deserialization marker only and
    assembly rejects consuming it fail-closed — it is never treated as
    completed.  Run inputs are unaffected: they are not producer outputs
    and need no status.
    """

    step_id: str
    structures: StructureSet = field(default_factory=StructureSet)
    artifacts: ArtifactSet = field(default_factory=ArtifactSet)
    results: ResultSet = field(default_factory=ResultSet)
    status: StepStatus | None = None
    producer_step_digest: str | None = None
    item_statuses: tuple[WorkItemStatus, ...] = ()

    def __post_init__(self) -> None:
        if self.status is not None and not isinstance(self.status, StepStatus):
            raise DomainError("StepOutputs status must be a StepStatus or None")
        object.__setattr__(self, "item_statuses", tuple(self.item_statuses))
        for item_status in self.item_statuses:
            if not isinstance(item_status, WorkItemStatus):
                raise DomainError("StepOutputs item_statuses must be WorkItemStatus members")
        if self.producer_step_digest is not None and (
            not isinstance(self.producer_step_digest, str) or not self.producer_step_digest.strip()
        ):
            raise DomainError("StepOutputs producer_step_digest must be a non-empty string or None")

    @property
    def has_known_status(self) -> bool:
        """Return whether this record carries an explicit producer status."""
        return self.status is not None


@dataclass(frozen=True, slots=True)
class MaterializedOutputs:
    """Producer outputs available to synthetic assembly."""

    steps: FrozenDict = field(default_factory=FrozenDict)

    def __post_init__(self) -> None:
        if not isinstance(self.steps, FrozenDict):
            object.__setattr__(self, "steps", FrozenDict(self.steps))
        for key, value in self.steps.items():
            if not isinstance(value, StepOutputs):
                raise DomainError(
                    f"materialized output {key!r} must be a StepOutputs, got {type(value).__name__}"
                )

    @classmethod
    def empty(cls) -> MaterializedOutputs:
        """Return empty materialized outputs."""
        return cls()

    def step(self, step_id: str) -> StepOutputs | None:
        """Return the materialized outputs of *step_id*, or ``None``."""
        value = self.steps.get(step_id)
        return value if isinstance(value, StepOutputs) else None


@dataclass(frozen=True, slots=True)
class AssemblyResult:
    """Outcome of work-item assembly."""

    items: tuple[WorkItem, ...] = ()
    diagnostics: tuple[Diagnostic, ...] = ()
    skipped_step_ids: tuple[str, ...] = ()

    @property
    def ok(self) -> bool:
        """Return whether assembly produced no errors."""
        return not any(item.is_error for item in self.diagnostics)

    @property
    def errors(self) -> tuple[Diagnostic, ...]:
        """Return error diagnostics in deterministic order."""
        return tuple(item for item in self.diagnostics if item.is_error)

    @property
    def warnings(self) -> tuple[Diagnostic, ...]:
        """Return warning diagnostics in deterministic order."""
        return tuple(item for item in self.diagnostics if not item.is_error)

    def for_step(self, step_id: str) -> tuple[WorkItem, ...]:
        """Return assembled items of *step_id* in deterministic order."""
        return tuple(item for item in self.items if item.step_id == step_id)


@dataclass(frozen=True, slots=True)
class _ResolvedSource:
    edge: ResolvedEdge
    structures: StructureSet = field(default_factory=StructureSet)
    artifacts: ArtifactSet = field(default_factory=ArtifactSet)
    results: ResultSet = field(default_factory=ResultSet)
    supplied: bool = True
    missing_ids: tuple[str, ...] = ()
    source_payload: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class _ItemDomainEntry:
    subject_structure_id: str | None
    group_key: str
    lineage_root_id: str | None
    driver_structure: StructureRecord | None


def _apply_selector(
    structures: StructureSet,
    artifacts: ArtifactSet,
    results: ResultSet,
    edge: ResolvedEdge,
) -> tuple[StructureSet, ArtifactSet, ResultSet, tuple[str, ...]]:
    selector = edge.source.selector
    missing: list[str] = []
    if selector.kind is SelectorKind.IDS:
        if edge.target_port.kind is PortKind.STRUCTURE:
            selected: list[StructureRecord] = []
            for structure_id in selector.ids:
                record = structures.get(structure_id)
                if record is None:
                    missing.append(structure_id)
                else:
                    selected.append(record)
            return StructureSet(tuple(selected)), ArtifactSet(), ResultSet(), tuple(missing)
        if edge.target_port.kind is PortKind.ARTIFACT:
            selected_artifacts: list[ArtifactRef] = []
            for artifact_id in selector.ids:
                record = artifacts.get(artifact_id)
                if record is None:
                    missing.append(artifact_id)
                else:
                    selected_artifacts.append(record)
            return (
                StructureSet(),
                ArtifactSet(tuple(selected_artifacts)),
                ResultSet(),
                tuple(missing),
            )
        # Result ports filter by independent ResultRef identity: each
        # requested id resolves on its own; only a missing id or duplicate
        # candidates for the SAME requested id fail.  Distinct ids may each
        # resolve once, so MANY ports can legitimately select several ids
        # with no global exactly-one requirement.
        selected_results, missing_ids, ambiguous_ids = results.select_ids(selector.ids)
        missing.extend(missing_ids)
        missing.extend(f"ambiguous:{result_id}" for result_id in ambiguous_ids)
        return structures.__class__(), artifacts.__class__(), selected_results, tuple(missing)
    if selector.kind is SelectorKind.ROLE:
        return StructureSet(), artifacts.by_role(selector.role or ""), ResultSet(), ()
    return structures, artifacts, results, ()


def _producer_state_diagnostic(
    *,
    consumer_step_id: str,
    edge: ResolvedEdge,
    outputs: StepOutputs | None,
    producer_completion: Any | None,
) -> Diagnostic | None:
    """Check actual producer failed/partial/cancelled state for one edge."""
    if outputs is None:
        return None  # waiting: caller emits NOT_ASSEMBLABLE, never an error
    field_path = f"steps.{consumer_step_id}.bindings.{edge.target_port.name}"
    if outputs.status is None:
        return error(
            DiagnosticCode.CARDINALITY_ERROR,
            DiagnosticReason.CARDINALITY_MISMATCH,
            f"producer {edge.source.step_id!r} has no recorded status; "
            f"step {consumer_step_id!r} cannot consume outputs with unknown "
            "completion state",
            step_id=consumer_step_id,
            logical_key=f"{consumer_step_id}:*",
            field_path=field_path,
            details={
                "port": edge.target_port.name,
                "producer_step_id": edge.source.step_id,
                "producer_status": "unknown",
            },
        )
    status = outputs.status
    if status is StepStatus.COMPLETED:
        return None
    field_path = f"steps.{consumer_step_id}.bindings.{edge.target_port.name}"
    base_details: dict[str, Any] = {
        "port": edge.target_port.name,
        "producer_step_id": edge.source.step_id,
        "producer_status": status.value,
    }
    if status is StepStatus.FAILED:
        return error(
            DiagnosticCode.CARDINALITY_ERROR,
            DiagnosticReason.CARDINALITY_MISMATCH,
            f"producer {edge.source.step_id!r} failed; "
            f"step {consumer_step_id!r} cannot consume its outputs",
            step_id=consumer_step_id,
            logical_key=f"{consumer_step_id}:*",
            field_path=field_path,
            details=base_details,
        )
    if status is StepStatus.CANCELLED:
        return error(
            DiagnosticCode.CARDINALITY_ERROR,
            DiagnosticReason.CARDINALITY_MISMATCH,
            f"producer {edge.source.step_id!r} was cancelled; "
            f"step {consumer_step_id!r} cannot consume its outputs",
            step_id=consumer_step_id,
            logical_key=f"{consumer_step_id}:*",
            field_path=field_path,
            details=base_details,
        )
    # PARTIAL: only an explicit accept_subset consumer of an allow-partial
    # producer may proceed; compile-time checks cover the declaration, but
    # the run-time actual state must be re-checked here.
    if status is StepStatus.PARTIAL:
        if edge.partial_consumption is not PartialConsumption.ACCEPT_SUBSET:
            return error(
                DiagnosticCode.COMPILE_ERROR,
                DiagnosticReason.PARTIAL_CONSUMPTION_UNDEFINED,
                f"producer {edge.source.step_id!r} is partial; "
                f"step {consumer_step_id!r} must declare accept_subset to consume it",
                step_id=consumer_step_id,
                logical_key=f"{consumer_step_id}:*",
                field_path=field_path,
                details=base_details,
            )
        return None
    return None


def _resolve_source(
    plan: ExecutionPlan,
    edge: ResolvedEdge,
    run_inputs: RunInputs,
    materialized: MaterializedOutputs,
) -> _ResolvedSource:
    source = edge.source
    if source.kind is SourceKind.RUN_INPUT:
        name = source.port
        structures = run_inputs.structures.get(name)
        artifacts = run_inputs.artifacts.get(name)
        results = run_inputs.results.get(name)
        supplied = any(value is not None for value in (structures, artifacts, results))
        resolved_structures = structures if isinstance(structures, StructureSet) else StructureSet()
        resolved_artifacts = artifacts if isinstance(artifacts, ArtifactSet) else ArtifactSet()
        resolved_results = results if isinstance(results, ResultSet) else ResultSet()
        payload: dict[str, Any] = {"kind": "run_input", "name": name}
        selected = _apply_selector(resolved_structures, resolved_artifacts, resolved_results, edge)
        return _ResolvedSource(
            edge=edge,
            structures=selected[0],
            artifacts=selected[1],
            results=selected[2],
            supplied=supplied,
            missing_ids=selected[3],
            source_payload=payload,
        )
    outputs = materialized.step(source.step_id or "")
    producer_step = plan.step(source.step_id or "")
    resolved_structures = outputs.structures if outputs is not None else StructureSet()
    resolved_artifacts = outputs.artifacts if outputs is not None else ArtifactSet()
    resolved_results = outputs.results if outputs is not None else ResultSet()
    payload = {
        "kind": "step_output",
        "step_id": source.step_id,
        "producer_step_digest": (
            producer_step.step_semantic_digest if producer_step is not None else None
        ),
    }
    selected = _apply_selector(resolved_structures, resolved_artifacts, resolved_results, edge)
    return _ResolvedSource(
        edge=edge,
        structures=selected[0],
        artifacts=selected[1],
        results=selected[2],
        supplied=outputs is not None,
        missing_ids=selected[3],
        source_payload=payload,
    )


def _port_value_count(resolved: _ResolvedSource) -> int:
    """Count the resolved values that can actually fill the target port."""
    kind = resolved.edge.target_port.kind
    if kind is PortKind.STRUCTURE:
        return len(resolved.structures)
    if kind is PortKind.ARTIFACT:
        return len(resolved.artifacts)
    return len(resolved.results)


def _empty_required_port_message(resolved: _ResolvedSource) -> str:
    """Name the empty required port, its kind and (for a step output) the producer."""
    edge = resolved.edge
    port = edge.target_port
    message = f"required {port.kind.value} port {port.name!r} has no values"
    if edge.source.kind is SourceKind.STEP_OUTPUT and edge.source.step_id is not None:
        message += f" (step {edge.source.step_id!r} published no {port.kind.value} for it)"
    return message


def _cardinality_diagnostic(
    step_id: str,
    resolved: _ResolvedSource,
    logical_key: str,
    *,
    subject_structure_id: str | None = None,
) -> Diagnostic | None:
    edge = resolved.edge
    port = edge.target_port
    count = len(resolved.structures) + len(resolved.artifacts) + len(resolved.results)
    required = edge.cardinality in (Cardinality.ONE, Cardinality.ONE_OR_MORE)
    violates = False
    if edge.cardinality is Cardinality.ONE and count != 1:
        violates = True
    elif edge.cardinality is Cardinality.OPTIONAL and count > 1:
        violates = True
    elif edge.cardinality is Cardinality.ONE_OR_MORE and count < 1:
        violates = True
    if not violates:
        return None
    field_path = f"steps.{step_id}.bindings.{port.name}"
    details: dict[str, Any] = {
        "port": port.name,
        "cardinality": edge.cardinality.value,
        "contract_cardinality": port.cardinality.value,
        "matched": count,
    }
    if edge.source.kind is SourceKind.RUN_INPUT:
        details["run_input"] = edge.source.port
    if resolved.missing_ids:
        details["missing_ids"] = list(resolved.missing_ids)
    if edge.pairing is Pairing.BY_SUBJECT:
        details["subject_structure_id"] = subject_structure_id
        details["role"] = edge.source.selector.role
        if required and count == 0:
            return error(
                DiagnosticCode.CARDINALITY_ERROR,
                DiagnosticReason.ARTIFACT_SUBJECT_MISSING,
                f"no {port.kind.value} matches subject {subject_structure_id!r} "
                f"on port {port.name!r}",
                step_id=step_id,
                logical_key=logical_key,
                field_path=field_path,
                details=details,
            )
    if edge.source.kind is SourceKind.RUN_INPUT and not resolved.supplied and required:
        return error(
            DiagnosticCode.CARDINALITY_ERROR,
            DiagnosticReason.RUN_INPUT_MISSING,
            f"required run input {edge.source.port!r} was not supplied",
            step_id=step_id,
            logical_key=logical_key,
            field_path=field_path,
            details=details,
        )
    return error(
        DiagnosticCode.CARDINALITY_ERROR,
        DiagnosticReason.CARDINALITY_MISMATCH,
        f"port {port.name!r} expected {edge.cardinality.value} but matched {count}",
        step_id=step_id,
        logical_key=logical_key,
        field_path=field_path,
        details=details,
    )


def _structure_payload(
    structure: StructureRecord, effective: EffectiveScientificParameters
) -> dict[str, Any]:
    """Return the provenance-aware digest payload of one structure input."""
    return structure.reuse_payload(effective)


def _determine_domain(
    plan: ExecutionPlan,
    step: PlannedStep,
    resolved_sources: tuple[_ResolvedSource, ...],
) -> tuple[tuple[_ItemDomainEntry, ...], list[Diagnostic]]:
    diagnostics: list[Diagnostic] = []
    structure_sources = [
        resolved
        for resolved in resolved_sources
        if resolved.edge.target_port.kind is PortKind.STRUCTURE
    ]
    per_structure = [
        resolved for resolved in structure_sources if resolved.edge.pairing is Pairing.PER_STRUCTURE
    ]
    by_group = [
        resolved for resolved in structure_sources if resolved.edge.pairing is Pairing.BY_GROUP_KEY
    ]
    if len(per_structure) > 1:
        diagnostics.append(
            error(
                DiagnosticCode.BINDING_ERROR,
                DiagnosticReason.PAIRING_UNDEFINED,
                "multiple structure ports declare per_structure pairing; "
                "use explicit by_group_key pairing",
                step_id=step.step_id,
                details={"ports": sorted(item.edge.target_port.name for item in per_structure)},
            )
        )
        return (), diagnostics
    if per_structure and by_group:
        diagnostics.append(
            error(
                DiagnosticCode.BINDING_ERROR,
                DiagnosticReason.PAIRING_UNDEFINED,
                "per_structure and by_group_key pairing cannot be mixed on one step",
                step_id=step.step_id,
                details={
                    "ports": sorted(item.edge.target_port.name for item in per_structure + by_group)
                },
            )
        )
        return (), diagnostics
    if per_structure:
        driver = per_structure[0]
        entries = tuple(
            _ItemDomainEntry(
                subject_structure_id=structure.id,
                # Per-structure items are keyed by entity identity; a
                # producer-set group key must not collapse distinct items.
                group_key=structure.id,
                lineage_root_id=structure.lineage_root_id,
                driver_structure=structure,
            )
            for structure in driver.structures
        )
        return entries, diagnostics
    if by_group:
        ordered_keys: list[str] = []
        for resolved in sorted(by_group, key=lambda item: item.edge.target_port.name):
            for structure in resolved.structures:
                if structure.group_key is None:
                    diagnostics.append(
                        error(
                            DiagnosticCode.BINDING_ERROR,
                            DiagnosticReason.PAIRING_UNDEFINED,
                            "by_group_key pairing requires structures with an explicit group_key",
                            step_id=step.step_id,
                            logical_key=f"{step.step_id}:pending",
                            field_path=(
                                f"steps.{step.step_id}.bindings.{resolved.edge.target_port.name}"
                            ),
                            details={"structure_id": structure.id},
                        )
                    )
                    continue
                if structure.group_key not in ordered_keys:
                    ordered_keys.append(structure.group_key)
        entries = tuple(
            _ItemDomainEntry(
                subject_structure_id=None,
                group_key=key,
                lineage_root_id=None,
                driver_structure=None,
            )
            for key in ordered_keys
        )
        return entries, diagnostics
    single_structures = [
        structure for resolved in structure_sources for structure in resolved.structures
    ]
    subject = single_structures[0].id if len(single_structures) == 1 else None
    lineage = single_structures[0].lineage_root_id if len(single_structures) == 1 else None
    return (
        (
            _ItemDomainEntry(
                subject_structure_id=subject,
                group_key="all",
                lineage_root_id=lineage,
                driver_structure=single_structures[0] if len(single_structures) == 1 else None,
            ),
        ),
        diagnostics,
    )


def assemble_work_items(
    plan: ExecutionPlan,
    run_inputs: RunInputs | None = None,
    *,
    materialized: MaterializedOutputs | None = None,
) -> AssemblyResult:
    """Assemble deterministic work items for *plan*.

    Per-step scoping: healthy steps retain their items even when another
    step fails; an errored step yields no items for itself (callers must
    never execute an errored current step) but never wipes unrelated
    steps.  Waiting (unmaterialized producers) stays a warning skip, never
    an error.  Legal-empty MANY sets complete; missing/failed/cancelled
    producers and cardinality violations are scoped errors.
    """
    inputs = run_inputs if run_inputs is not None else RunInputs.empty()
    outputs = materialized if materialized is not None else MaterializedOutputs.empty()
    diagnostics: list[Diagnostic] = []
    items: list[WorkItem] = []
    skipped: list[str] = []

    for name in sorted(set(inputs.structures) | set(inputs.artifacts) | set(inputs.results)):
        if name not in plan.graph.run_input_ports:
            diagnostics.append(
                warning(
                    DiagnosticCode.CARDINALITY_ERROR,
                    DiagnosticReason.RUN_INPUT_UNEXPECTED,
                    f"supplied run input {name!r} is not declared by the workflow",
                    details={"input_name": name},
                )
            )

    for step in plan.steps:
        step_diagnostics: list[Diagnostic] = []
        edges = plan.graph.incoming(step.step_id)
        missing_producers = sorted(
            {
                edge.source.step_id
                for edge in edges
                if edge.source.kind is SourceKind.STEP_OUTPUT
                and edge.source.step_id is not None
                and outputs.step(edge.source.step_id) is None
            }
        )
        if missing_producers:
            # Waiting, not an error: downstream simply cannot run yet.
            waiting = warning(
                DiagnosticCode.COMPILE_ERROR,
                DiagnosticReason.NOT_ASSEMBLABLE,
                f"step {step.step_id!r} waits for unmaterialized producer outputs",
                step_id=step.step_id,
                details={"producers": missing_producers},
            )
            diagnostics.append(waiting)
            skipped.append(step.step_id)
            continue
        # Actual producer failed/partial/cancelled gating (run-time check;
        # compile-time declarations cannot substitute for observed state).
        producer_gating_failed = False
        for edge in edges:
            if edge.source.kind is not SourceKind.STEP_OUTPUT:
                continue
            producer_outputs = outputs.step(edge.source.step_id or "")
            producer_step = plan.step(edge.source.step_id or "")
            producer_completion = producer_step.completion if producer_step is not None else None
            gating = _producer_state_diagnostic(
                consumer_step_id=step.step_id,
                edge=edge,
                outputs=producer_outputs,
                producer_completion=producer_completion,
            )
            if gating is not None:
                step_diagnostics.append(gating)
                producer_gating_failed = True
        if producer_gating_failed:
            diagnostics.extend(step_diagnostics)
            continue
        resolved_sources: list[_ResolvedSource] = []
        step_selector_failed = False
        for edge in edges:
            resolved = _resolve_source(plan, edge, inputs, outputs)
            if resolved.missing_ids:
                step_diagnostics.append(
                    error(
                        DiagnosticCode.CARDINALITY_ERROR,
                        DiagnosticReason.CARDINALITY_MISMATCH,
                        f"selector ids are missing on port {edge.target_port.name!r}",
                        step_id=step.step_id,
                        field_path=f"steps.{step.step_id}.bindings.{edge.target_port.name}",
                        details={"missing_ids": list(resolved.missing_ids)},
                    )
                )
                step_selector_failed = True
            resolved_sources.append(resolved)
        if step_selector_failed:
            diagnostics.extend(step_diagnostics)
            continue
        provisioning_errors: list[Diagnostic] = []
        for resolved in resolved_sources:
            required = resolved.edge.cardinality in (Cardinality.ONE, Cardinality.ONE_OR_MORE)
            count = _port_value_count(resolved)
            if not required or count > 0:
                continue
            edge = resolved.edge
            details: dict[str, Any] = {
                "port": edge.target_port.name,
                "cardinality": edge.cardinality.value,
                "matched": 0,
            }
            if edge.source.kind is SourceKind.RUN_INPUT:
                details["run_input"] = edge.source.port
                if not resolved.supplied:
                    provisioning_errors.append(
                        error(
                            DiagnosticCode.CARDINALITY_ERROR,
                            DiagnosticReason.RUN_INPUT_MISSING,
                            f"required run input {edge.source.port!r} was not supplied",
                            step_id=step.step_id,
                            logical_key=f"{step.step_id}:*",
                            field_path=f"steps.{step.step_id}.bindings.{edge.target_port.name}",
                            details=details,
                        )
                    )
                    continue
            if edge.source.kind is SourceKind.STEP_OUTPUT:
                details["producer_step_id"] = edge.source.step_id
            provisioning_errors.append(
                error(
                    DiagnosticCode.CARDINALITY_ERROR,
                    DiagnosticReason.CARDINALITY_MISMATCH,
                    _empty_required_port_message(resolved),
                    step_id=step.step_id,
                    logical_key=f"{step.step_id}:*",
                    field_path=f"steps.{step.step_id}.bindings.{edge.target_port.name}",
                    details=details,
                )
            )
        if provisioning_errors:
            diagnostics.extend(provisioning_errors)
            continue
        domain, domain_diagnostics = _determine_domain(plan, step, tuple(resolved_sources))
        if any(item.is_error for item in domain_diagnostics):
            diagnostics.extend(domain_diagnostics)
            continue
        effective_cache: dict[str, EffectiveScientificParameters] = {}
        effective_failed = False
        step_items: list[WorkItem] = []
        step_item_errors: list[Diagnostic] = []
        for ordinal, entry in enumerate(domain):
            logical_key = f"{step.step_id}:{entry.group_key}"
            inputs_payload: dict[str, Any] = {}
            structures_inputs: dict[str, StructureSet] = {}
            artifacts_inputs: dict[str, ArtifactSet] = {}
            results_inputs: dict[str, ResultSet] = {}
            item_errors: list[Diagnostic] = []
            for resolved in sorted(resolved_sources, key=lambda item: item.edge.target_port.name):
                port = resolved.edge.target_port
                edge = resolved.edge
                if port.kind is PortKind.STRUCTURE:
                    if edge.pairing is Pairing.PER_STRUCTURE:
                        if entry.driver_structure is None:
                            structure_value = StructureSet()
                        else:
                            structure_value = StructureSet.of(entry.driver_structure)
                    elif edge.pairing is Pairing.BY_GROUP_KEY:
                        structure_value = StructureSet(
                            tuple(
                                structure
                                for structure in resolved.structures
                                if structure.group_key == entry.group_key
                            )
                        )
                    else:
                        structure_value = resolved.structures
                    structures_inputs[port.name] = structure_value
                    payload_structures: list[dict[str, Any]] = []
                    for structure in structure_value:
                        if structure.id not in effective_cache:
                            effective, effective_diagnostics = resolve_scientific_parameters(
                                structure=structure,
                                overrides=(
                                    step.scientific.overrides
                                    if step.scientific is not None
                                    else FrozenDict()
                                ),
                                defaults=plan.scientific_defaults,
                            )
                            effective_cache[structure.id] = effective
                            for diagnostic in effective_diagnostics:
                                scoped = (
                                    diagnostic
                                    if diagnostic.step_id is not None
                                    else Diagnostic(
                                        code=diagnostic.code,
                                        message=diagnostic.message,
                                        severity=diagnostic.severity,
                                        step_id=step.step_id,
                                        logical_key=diagnostic.logical_key,
                                        field_path=diagnostic.field_path,
                                        details=diagnostic.details,
                                    )
                                )
                                if scoped.is_error:
                                    effective_failed = True
                                step_diagnostics.append(scoped)
                        effective = effective_cache[structure.id]
                        payload_structures.append(_structure_payload(structure, effective))
                    inputs_payload[port.name] = {
                        "source": resolved.source_payload,
                        "structures": payload_structures,
                    }
                elif port.kind is PortKind.ARTIFACT:
                    if edge.pairing is Pairing.BY_SUBJECT:
                        if entry.subject_structure_id is None:
                            item_errors.append(
                                error(
                                    DiagnosticCode.CARDINALITY_ERROR,
                                    DiagnosticReason.SUBJECT_IDENTITY_MISSING,
                                    "subject-keyed artifacts require an item subject structure",
                                    step_id=step.step_id,
                                    logical_key=logical_key,
                                    field_path=f"steps.{step.step_id}.bindings.{port.name}",
                                )
                            )
                            artifact_value = ArtifactSet()
                        else:
                            artifact_value = resolved.artifacts.by_subject(
                                entry.subject_structure_id
                            )
                    else:
                        artifact_value = resolved.artifacts
                    artifacts_inputs[port.name] = artifact_value
                    inputs_payload[port.name] = {
                        "source": resolved.source_payload,
                        "artifacts": [artifact.digest_payload() for artifact in artifact_value],
                    }
                else:
                    if edge.pairing is Pairing.BY_SUBJECT:
                        if entry.subject_structure_id is None:
                            item_errors.append(
                                error(
                                    DiagnosticCode.CARDINALITY_ERROR,
                                    DiagnosticReason.SUBJECT_IDENTITY_MISSING,
                                    "subject-keyed results require an item subject structure",
                                    step_id=step.step_id,
                                    logical_key=logical_key,
                                    field_path=f"steps.{step.step_id}.bindings.{port.name}",
                                )
                            )
                            result_value = ResultSet()
                        else:
                            result_value = resolved.results.by_subject(entry.subject_structure_id)
                    else:
                        result_value = resolved.results
                    results_inputs[port.name] = result_value
                    inputs_payload[port.name] = {
                        "source": resolved.source_payload,
                        "results": [result.digest_payload() for result in result_value],
                    }
                cardinality_diagnostic = _cardinality_diagnostic(
                    step.step_id,
                    _ResolvedSource(
                        edge=edge,
                        structures=structures_inputs.get(port.name, StructureSet()),
                        artifacts=artifacts_inputs.get(port.name, ArtifactSet()),
                        results=results_inputs.get(port.name, ResultSet()),
                        supplied=resolved.supplied,
                        missing_ids=resolved.missing_ids,
                    ),
                    logical_key,
                    subject_structure_id=entry.subject_structure_id,
                )
                if cardinality_diagnostic is not None:
                    item_errors.append(cardinality_diagnostic)
            # Same entity on multiple ports must carry identical payloads.
            if not item_errors:
                all_records = [record for value in structures_inputs.values() for record in value]
                conflicts = check_structure_id_conflicts(all_records)
                for conflict_id, first_record, second_record in conflicts:
                    item_errors.append(
                        error(
                            DiagnosticCode.IDENTITY_ERROR,
                            DiagnosticReason.DUPLICATE_STRUCTURE_IDENTITY,
                            f"structure id {conflict_id!r} carries conflicting payloads "
                            "on one work item",
                            step_id=step.step_id,
                            logical_key=logical_key,
                            details={
                                "structure_id": conflict_id,
                                "first": first_record.reuse_payload(),
                                "second": second_record.reuse_payload(),
                            },
                        )
                    )
            if item_errors:
                step_item_errors.extend(item_errors)
                continue
            structure_sets = FrozenDict(structures_inputs)
            artifact_sets = FrozenDict(artifacts_inputs)
            result_sets = FrozenDict(results_inputs)
            semantic_digest = work_item_semantic_digest(
                step_semantic_digest=step.step_semantic_digest,
                input_payload=inputs_payload,
                resources=step.resources,
            )
            item = WorkItem(
                id=make_work_item_id(logical_key),
                logical_key=logical_key,
                step_id=step.step_id,
                named_inputs=WorkItemInputs(
                    structures=structure_sets,
                    artifacts=artifact_sets,
                    results=result_sets,
                ),
                resources=step.resources,
                semantic_digest=semantic_digest,
                ordinal=ordinal,
                group_key=entry.group_key,
                lineage_root_id=entry.lineage_root_id,
                execution_binding_id=(
                    step.execution.binding_id if step.execution is not None else None
                ),
            )
            step_items.append(item)
        diagnostics.extend(step_diagnostics)
        if effective_failed:
            continue
        if step_item_errors:
            # An errored step yields no items for itself, but healthy steps
            # already collected in `items` are retained with scoped errors.
            diagnostics.extend(step_item_errors)
            continue
        keys = [item.logical_key for item in step_items]
        if len(set(keys)) != len(keys):
            for key in sorted({key for key in keys if keys.count(key) > 1}):
                diagnostics.append(
                    error(
                        DiagnosticCode.IDENTITY_ERROR,
                        DiagnosticReason.DUPLICATE_LOGICAL_KEY,
                        f"duplicate work item logical key {key!r}",
                        step_id=step.step_id,
                        logical_key=key,
                    )
                )
            continue
        items.extend(step_items)

    order_index = {step.step_id: index for index, step in enumerate(plan.steps)}
    items.sort(key=lambda item: (order_index[item.step_id], item.logical_key))
    return AssemblyResult(
        tuple(items),
        tuple(sorted(diagnostics, key=diagnostic_sort_key)),
        tuple(skipped),
    )
