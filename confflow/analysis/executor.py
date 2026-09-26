#!/usr/bin/env python3

"""Analysis step executor for ConfFlow Workflow V4 (V4-6).

:class:`AnalysisExecutor` is a real step executor: typed inputs
(structures plus results by port) plus an explicit
:class:`AnalysisDefinition` produce a :class:`AnalysisStepResult`
(result set, diagnostics, optional artifacts).  It is pure and
deterministic: no subprocesses, no program adapters, no file-name
reads, no I/O of any kind.

Wiring contract for the main agent and agent B (read carefully)
--------------------------------------------------------------
Energy math lives behind the :class:`EnergyModel` Protocol defined in
this module.  The executor resolves reaction groups (via
:mod:`confflow.analysis.grouping`) and applies the endpoint-assignment
policy; it delegates every Joule to ``definition.energy_model``,
which must satisfy this structural interface::

    class EnergyModel(Protocol):
        def compute(
            self,
            group: ReactionGroup,
            lookup: Mapping[str, ResultSet],
            *,
            analysis_step_id: str | None = ...,
            endpoint_assignment: Mapping[str, str] | None = ...,
        ) -> ComputedGroup: ...
        def to_dict(self) -> dict[str, Any]: ...

- ``group`` is the reaction triple with ``assignment`` already
  resolved to ``"unassigned"`` or ``"explicit"`` (mirroring
  ``definition.assignment``); B's adapter maps it onto its own
  ``ReactionNodeGroup`` as ``group_key`` -> ``group_key``,
  ``ts_structure_id`` -> ``ts_structure_id``,
  ``forward_endpoint_id`` -> ``forward_structure_id``,
  ``reverse_endpoint_id`` -> ``reverse_structure_id``.
- ``lookup`` maps each triple subject id to its merged per-node
  :class:`ResultSet` pool (across theory levels and input ports).
  Kind selection inside ``compute`` must never guess subjects.
- ``endpoint_assignment`` repeats ``dict(definition.endpoint_assignment)``
  (``{"forward": ..., "reverse": ...}``) for convenience.
- ``compute`` must return a :class:`ComputedGroup` (results plus typed
  diagnostics) and must never raise for missing data: a missing piece
  fails the group closed with an error diagnostic.  Only programming
  errors may raise; the executor wraps those as an
  ``analysis_compute_failed`` group diagnostic instead of propagating.
- ``to_dict`` exposes the policy payload folded into definition
  digests and result provenance.  It must be canonical JSON data.

Agent B implements this Protocol (structurally: no import of this
module is required, and this module never imports agent B's
``reaction`` / ``thermochemistry`` / ``pes`` / ``units`` modules) by
wrapping ``assemble_reaction_result`` and mapping its
``ReactionAnalysis`` field-for-field into :class:`ComputedGroup`.
The main agent wires the concrete model into
``AnalysisDefinition.energy_model`` and this executor into the
``BatchStepExecutor`` / orchestrator.

Partial policy
--------------
``require_complete`` (default): any failed group, or any error
diagnostic at all, discards every computed result; the step reports
the full diagnostic picture and nothing else.  ``accept_subset``:
failed groups are omitted with their diagnostics while complete
groups still emit.  A partial group is never filled from another
group under either policy.

Only ``confflow.domain``, the executor capability vocabulary, the
sibling analysis modules, and stdlib are imported here.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace
from typing import Any, ClassVar, Protocol, runtime_checkable

from ..domain.artifact import ArtifactSet
from ..domain.binding import PartialConsumption
from ..domain.diagnostics import Diagnostic, DiagnosticSeverity, diagnostic_sort_key
from ..domain.result import ResultSet, ScientificResult
from ..domain.structure import StructureRecord, StructureSet
from ..execution.contracts import ExecutorCapability
from .grouping import build_reaction_groups
from .models import (
    ASSIGNMENT_EXPLICIT,
    AnalysisDefinition,
    AnalysisError,
    AnalysisInputs,
    AnalysisStepResult,
    ComputedGroup,
    ReactionGroup,
)
from .registry import require_analysis_capability

__all__ = [
    "AnalysisExecutor",
    "AnalysisInputs",
    "EnergyModel",
]


@runtime_checkable
class EnergyModel(Protocol):
    """Compute seam for per-group energy math (agent B implements).

    Structural typing applies: a concrete model satisfies this Protocol
    by providing both methods with compatible signatures; no import of
    this module is required on the implementing side.
    """

    def compute(
        self,
        group: ReactionGroup,
        lookup: Mapping[str, ResultSet],
        *,
        analysis_step_id: str | None = None,
        endpoint_assignment: Mapping[str, str] | None = None,
    ) -> ComputedGroup:
        """Aggregate one reaction group into computed results.

        Parameters
        ----------
        group : ReactionGroup
            Reaction triple with ``assignment`` already resolved.
        lookup : Mapping[str, ResultSet]
            Per-node source pools keyed by triple subject id.
        analysis_step_id : str or None
            Analysis step id for result provenance, when known.
        endpoint_assignment : Mapping[str, str] or None
            ``{"forward": ..., "reverse": ...}`` chemistry mapping.

        Returns
        -------
        ComputedGroup
            Computed results plus typed diagnostics; empty results with
            an error diagnostic when the group fails closed.
        """
        ...

    def to_dict(self) -> dict[str, Any]:
        """Return the policy payload folded into digests and provenance.

        Returns
        -------
        dict[str, Any]
            Canonical JSON data describing the energy policy.
        """
        ...


def _is_energy_model(value: Any) -> bool:
    """Return whether *value* satisfies the compute seam structurally."""
    return callable(getattr(value, "compute", None)) and callable(getattr(value, "to_dict", None))


def _merge_structures(inputs: AnalysisInputs) -> StructureSet:
    """Merge structure ports in sorted port order, de-duplicated by id.

    Parameters
    ----------
    inputs : AnalysisInputs
        Typed executor inputs.

    Returns
    -------
    StructureSet
        Combined structures; a repeated id keeps its first occurrence
        in ``(port, position)`` order.
    """
    merged: dict[str, StructureRecord] = {}
    ports = inputs.structures
    for port in sorted(ports):
        for record in ports[port]:
            if record.id not in merged:
                merged[record.id] = record
    return StructureSet(tuple(merged.values()))


def _merge_results(inputs: AnalysisInputs) -> ResultSet:
    """Merge result ports in sorted port order, preserving positions.

    Parameters
    ----------
    inputs : AnalysisInputs
        Typed executor inputs.

    Returns
    -------
    ResultSet
        Combined results in deterministic port order.
    """
    merged: list[ScientificResult] = []
    ports = inputs.results
    for port in sorted(ports):
        merged.extend(ports[port])
    return ResultSet(tuple(merged))


def _lookup_for_group(group: ReactionGroup, pools: Mapping[str, ResultSet]) -> dict[str, ResultSet]:
    """Return the per-node source pools for one group's triple.

    Parameters
    ----------
    group : ReactionGroup
        Reaction triple (must be structurally complete).
    pools : Mapping[str, ResultSet]
        Pools keyed by every known structure id.

    Returns
    -------
    dict[str, ResultSet]
        Pools keyed by triple subject id only.
    """
    lookup: dict[str, ResultSet] = {}
    for subject in group.subject_ids():
        lookup[subject] = pools[subject]
    return lookup


class AnalysisExecutor:
    """Pure, deterministic analysis step executor.

    The executor resolves reaction groups, applies the explicit
    endpoint-assignment policy, and delegates energy math to the
    definition's energy model across the :class:`EnergyModel` seam.
    """

    name: ClassVar[str] = "analysis"
    contract_version: ClassVar[str] = "confflow.contract.executor.analysis.v1"
    capability: ClassVar[ExecutorCapability] = ExecutorCapability.ANALYSIS

    def execute(
        self,
        inputs: AnalysisInputs,
        definition: AnalysisDefinition | None = None,
    ) -> AnalysisStepResult:
        """Execute one analysis step over typed inputs.

        Parameters
        ----------
        inputs : AnalysisInputs
            Structures and results by port, plus the definition.
        definition : AnalysisDefinition or None
            Explicit definition overriding ``inputs.definition`` when
            given; otherwise the inputs' definition applies.

        Returns
        -------
        AnalysisStepResult
            Computed results (empty under ``require_complete`` when
            anything failed), no minted structures, and diagnostics in
            deterministic order.

        Raises
        ------
        AnalysisError
            With code ``analysis_invalid_definition`` when *inputs* is
            not an :class:`AnalysisInputs`, or ``analysis_unknown_kind``
            when the kind names no registered capability.
        """
        if not isinstance(inputs, AnalysisInputs):
            raise AnalysisError(
                "analysis_invalid_definition",
                "analysis execution requires AnalysisInputs",
                details={"reason": "invalid_inputs_type"},
            )
        effective = definition if definition is not None else inputs.definition
        if not isinstance(effective, AnalysisDefinition):
            raise AnalysisError(
                "analysis_invalid_definition",
                "analysis execution requires an AnalysisDefinition",
                details={"reason": "missing_definition"},
            )
        require_analysis_capability(effective.kind)
        model = effective.energy_model
        if not _is_energy_model(model):
            raise AnalysisError(
                "analysis_compute_failed",
                "energy_model does not satisfy the compute seam (compute + to_dict required)",
                details={"reason": "invalid_energy_model"},
            )
        structures = _merge_structures(inputs)
        source_results = _merge_results(inputs)
        pools: dict[str, ResultSet] = {
            record.id: source_results.by_subject(record.id) for record in structures
        }
        groups, global_diagnostics = build_reaction_groups(structures, source_results)
        explicit = effective.assignment == ASSIGNMENT_EXPLICIT
        assignment_map = dict(effective.endpoint_assignment)
        step_diagnostics: list[Diagnostic] = list(global_diagnostics)
        computed_results: list[ScientificResult] = []
        failed = False
        for group in groups:
            resolved = replace(group, assignment=ASSIGNMENT_EXPLICIT) if explicit else group
            group_diagnostics = list(resolved.diagnostics)
            outcome: ComputedGroup | None = None
            if not [item for item in group_diagnostics if item.is_error] and resolved.is_complete:
                try:
                    candidate = model.compute(
                        resolved,
                        _lookup_for_group(resolved, pools),
                        analysis_step_id=None,
                        endpoint_assignment=dict(assignment_map),
                    )
                except Exception as exc:  # fail the group closed, never propagate
                    candidate = ComputedGroup(
                        results=(),
                        diagnostics=(
                            Diagnostic(
                                code="analysis_compute_failed",
                                message=f"energy model failed group {group.group_key!r}: {exc}",
                                severity=DiagnosticSeverity.ERROR,
                                details={
                                    "reason": "compute_raised",
                                    "group_key": group.group_key,
                                    "error": type(exc).__name__,
                                },
                            ),
                        ),
                    )
                if not isinstance(candidate, ComputedGroup):
                    candidate = ComputedGroup(
                        results=(),
                        diagnostics=(
                            Diagnostic(
                                code="analysis_compute_failed",
                                message=(
                                    f"energy model returned "
                                    f"{type(candidate).__name__} for group "
                                    f"{group.group_key!r}; expected ComputedGroup"
                                ),
                                severity=DiagnosticSeverity.ERROR,
                                details={
                                    "reason": "invalid_compute_result",
                                    "group_key": group.group_key,
                                },
                            ),
                        ),
                    )
                outcome = candidate
                group_diagnostics.extend(candidate.diagnostics)
            if [item for item in group_diagnostics if item.is_error]:
                failed = True
            elif outcome is not None:
                computed_results.extend(outcome.results)
            step_diagnostics.extend(group_diagnostics)
        has_errors = failed or any(item.is_error for item in step_diagnostics)
        if effective.partial_policy is PartialConsumption.REQUIRE_COMPLETE and has_errors:
            emitted: tuple[ScientificResult, ...] = ()
        else:
            emitted = tuple(computed_results)
        ordered_results = sorted(
            emitted,
            key=lambda result: (
                result.subject_structure_id or "",
                result.kind,
                result.value_digest,
            ),
        )
        return AnalysisStepResult(
            structures=StructureSet(),
            results=ResultSet(tuple(ordered_results)),
            artifacts=ArtifactSet(),
            diagnostics=tuple(sorted(step_diagnostics, key=diagnostic_sort_key)),
        )
