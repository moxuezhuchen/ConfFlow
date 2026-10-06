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
from ..domain.canonical import canonical_json_bytes
from ..domain.diagnostics import Diagnostic, DiagnosticSeverity, diagnostic_sort_key
from ..domain.result import ResultSet, ScientificResult
from ..domain.structure import StructureRecord, StructureSet
from ..execution.output_identity import (
    PATH_ENDPOINT_FORWARD_ROLE,
    PATH_ENDPOINT_REVERSE_ROLE,
)
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
        Combined structures; a repeated id with identical content keeps
        its first occurrence in ``(port, position)`` order.  A repeated
        id with differing content fails closed: pool order never decides
        which definition wins.

    Raises
    ------
    AnalysisError
        With code ``analysis_invalid_definition`` when one structure id
        names conflicting records.
    """
    from .models import AnalysisError as _AnalysisError

    merged: dict[str, StructureRecord] = {}
    payloads: dict[str, str] = {}
    ports = inputs.structures
    for port in sorted(ports):
        for record in ports[port]:
            seen = merged.get(record.id)
            if seen is None:
                merged[record.id] = record
                try:
                    payloads[record.id] = record.to_dict().__repr__()
                except Exception:
                    payloads[record.id] = repr(
                        (record.geometry_digest, record.role, record.group_key, record.parent_ids)
                    )
                continue
            try:
                current = record.to_dict().__repr__()
            except Exception:
                current = repr(
                    (record.geometry_digest, record.role, record.group_key, record.parent_ids)
                )
            if current != payloads[record.id]:
                raise _AnalysisError(
                    "analysis_invalid_definition",
                    f"structure id {record.id!r} names conflicting records; "
                    "refusing order-dependent resolution",
                    details={"reason": "conflicting_structure", "structure_id": record.id},
                )
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
        Combined results in deterministic port order, with exact-duplicate
        records collapsed: binding one source collection to several ports
        must not multiply its entries.  Only fully equal payloads
        (identical kind, value, subject, provenance, and identity)
        collapse — distinct records, however similar, are all kept, and
        same-``(subject, kind)`` collisions still fail closed downstream
        as ambiguous selections.
    """
    merged: list[ScientificResult] = []
    seen: set[str] = set()
    ports = inputs.results
    for port in sorted(ports):
        for record in ports[port]:
            try:
                fingerprint = record.to_dict()
                key = canonical_json_bytes(fingerprint)
            except Exception:
                key = None
            if key is not None:
                if key in seen:
                    continue
                seen.add(key)
            merged.append(record)
    return ResultSet(tuple(merged))


def _eligible_owners(
    descendant: StructureRecord | None,
    group: ReactionGroup | None,
    triple: set[str],
) -> set[str]:
    """Return the triple members a descendant may attribute to.

    A descendant carrying an endpoint role is slot-pinned: a
    ``path_endpoint_forward`` record may only track to the forward
    endpoint and a ``path_endpoint_reverse`` record only to the reverse
    endpoint.  Any other role (including ``None``) may track to any
    triple member.  Without group context every triple member is
    eligible.  No ordering is consulted.
    """
    if group is None or descendant is None:
        return set(triple)
    role = descendant.role
    if role == PATH_ENDPOINT_FORWARD_ROLE:
        forward = group.forward_endpoint_id
        return {forward} if forward is not None and forward in triple else set()
    if role == PATH_ENDPOINT_REVERSE_ROLE:
        reverse = group.reverse_endpoint_id
        return {reverse} if reverse is not None and reverse in triple else set()
    return set(triple)


def _resolve_attribution(
    subject: str,
    triple: set[str],
    structures_by_id: Mapping[str, StructureRecord],
    group: ReactionGroup | None = None,
) -> tuple[str | None, str, tuple[str, ...]]:
    """Resolve *subject* to its unique nearest triple ancestor, if any.

    The parent graph is searched exhaustively, layer by layer (one
    layer per parent hop), over deduplicated id sets: every parent path
    is explored and the shortest graph distance carrying an eligible
    triple member wins.  Zero candidates at every distance is
    ``missing``; exactly one unique candidate at the shortest distance
    is attributed; more than one candidate tied at the shortest
    distance is ``ambiguous`` and fails closed to ``None``.  A nearer
    unique candidate always beats farther ones; a tie never resolves
    by parent-tuple order, lexical order, or first-parent-wins.

    Explicit identity gates apply before any graph walk when *group*
    is given: a descendant whose ``group_key`` differs from the
    group's key is ``rejected_group_key`` (wrong-group ancestors never
    attribute across groups), and endpoint-role pinning restricts the
    eligible set (a forward descendant only tracks to the forward
    node, reverse likewise).  Branches traversing a known record with
    a mismatched ``group_key`` are dead and neither count nor expand.
    Cycles fail closed to ``missing``/``None``.
    """
    triple_set = set(triple)
    if subject in triple_set:
        return (subject, "direct", (subject,))
    descendant = structures_by_id.get(subject)
    if descendant is None:
        return (None, "unknown_structure", ())
    if group is not None and descendant.group_key != group.group_key:
        return (None, "rejected_group_key", ())
    eligible = _eligible_owners(descendant, group, triple_set)
    if group is not None and not eligible:
        return (None, "rejected_role", ())
    if not eligible:
        return (None, "missing", ())
    seen: set[str] = {subject}
    frontier: set[str] = {subject}
    while frontier:
        next_layer: set[str] = set()
        hits: set[str] = set()
        for node_id in frontier:
            record = structures_by_id.get(node_id)
            if record is None:
                continue
            for parent_id in set(record.parent_ids):
                if parent_id in seen:
                    continue
                seen.add(parent_id)
                parent_record = structures_by_id.get(parent_id)
                if (
                    group is not None
                    and parent_record is not None
                    and parent_record.group_key != group.group_key
                ):
                    continue
                if parent_id in triple_set:
                    if parent_id in eligible:
                        hits.add(parent_id)
                    continue
                next_layer.add(parent_id)
        if len(hits) == 1:
            owner = next(iter(hits))
            return (owner, "attributed", (owner,))
        if len(hits) > 1:
            return (None, "ambiguous", tuple(sorted(hits)))
        if not next_layer:
            return (None, "missing", ())
        frontier = next_layer
    return (None, "missing", ())


def _lookup_for_group(
    group: ReactionGroup,
    pools: Mapping[str, ResultSet],
    structures_by_id: Mapping[str, StructureRecord] | None = None,
    merged_results: ResultSet | None = None,
) -> dict[str, ResultSet]:
    """Return the per-node source pools for one group's triple.

    Parameters
    ----------
    group : ReactionGroup
        Reaction triple (must be structurally complete).
    pools : Mapping[str, ResultSet]
        Pools keyed by every known structure id (direct subjects only).
    structures_by_id : Mapping or None
        Structure index for lineage expansion; when given with
        *merged_results*, descendant results (optimized/frequency/SP
        outputs) join the pool of their unique nearest triple-subject
        ancestor with the ancestor subject rewritten in, preserving the
        original ``result_id``/provenance for traceability.  Attribution
        is order independent: all parent paths are searched, only a
        unique nearest eligible ancestor attributes, and ties at the
        shortest distance attribute nowhere (fail closed, never
        first-parent-wins).  Group-key mismatches never attribute and
        endpoint-role descendants only track to their own slot.
        Selection downstream stays unique-or-ambiguous-fail over the
        expanded pool.
    merged_results : ResultSet or None
        Full merged results for lineage expansion (required with
        *structures_by_id*).

    Returns
    -------
    dict[str, ResultSet]
        Pools keyed by triple subject id only.
    """
    lookup, _ = _lookup_for_group_with_diagnostics(group, pools, structures_by_id, merged_results)
    return lookup


def _lookup_for_group_with_diagnostics(
    group: ReactionGroup,
    pools: Mapping[str, ResultSet],
    structures_by_id: Mapping[str, StructureRecord] | None = None,
    merged_results: ResultSet | None = None,
) -> tuple[dict[str, ResultSet], tuple[Diagnostic, ...]]:
    """Return per-node pools plus lineage-ambiguity diagnostics.

    Attribution follows :func:`_resolve_attribution`: unique nearest
    descendants join their owner's pool (subject rewritten); ambiguous
    descendants (tied at the shortest distance) join no pool and yield
    one ``analysis_group_ambiguous`` error each so the group fails
    closed instead of swapping on parent-tuple order.  Wrong-group,
    wrong-slot, unknown, and missing lineages attribute nowhere and
    stay silent (the group fails via the usual missing-leg path when
    it needs that energy).
    """
    from dataclasses import replace as _replace

    triple = set(group.subject_ids())
    lookup: dict[str, ResultSet] = {}
    for subject in group.subject_ids():
        lookup[subject] = pools.get(subject, ResultSet())
    if structures_by_id is None or merged_results is None:
        return (lookup, ())
    diagnostics: list[Diagnostic] = []
    reported: set[str] = set()
    for result in merged_results:
        subject = result.subject_structure_id
        if subject is None or subject in triple:
            continue
        owner, status, candidates = _resolve_attribution(subject, triple, structures_by_id, group)
        if owner is not None:
            rewritten = _replace(result, subject_structure_id=owner)
            lookup[owner] = ResultSet(tuple(lookup[owner]) + (rewritten,))
        elif status == "ambiguous" and subject not in reported:
            reported.add(subject)
            diagnostics.append(
                Diagnostic(
                    code="analysis_group_ambiguous",
                    message=(
                        f"reaction group {group.group_key!r} has ambiguous lineage "
                        f"for descendant {subject!r}; refusing order-dependent attribution"
                    ),
                    severity=DiagnosticSeverity.ERROR,
                    details={
                        "reason": "ambiguous_lineage",
                        "group_key": group.group_key,
                        "subject_structure_id": subject,
                        "candidate_ids": list(candidates),
                    },
                )
            )
    ordered = tuple(
        sorted(diagnostics, key=lambda item: str(item.details.get("subject_structure_id", "")))
    )
    return (lookup, ordered)


def _nearest_triple_ancestor(
    subject: str,
    triple: set[str],
    structures_by_id: Mapping[str, StructureRecord],
    *,
    group: ReactionGroup | None = None,
) -> str | None:
    """Return the unique nearest triple-subject ancestor of *subject*.

    Exhaustive layer-by-layer search over all parent paths with
    group-key and endpoint-role gating (see
    :func:`_resolve_attribution`): one unique candidate at the
    shortest distance attributes, zero is missing, and a tie at the
    shortest distance fails closed to ``None``.  Never first-parent-,
    lexical-, or tuple-order-wins.  Cycles fail closed to ``None``.
    """
    owner, _status, _candidates = _resolve_attribution(
        subject, set(triple), structures_by_id, group
    )
    return owner


class AnalysisExecutor:
    """Pure, deterministic analysis step executor.

    The executor resolves reaction groups, applies the explicit
    endpoint-assignment policy, and delegates energy math to the
    definition's energy model across the :class:`EnergyModel` seam.
    """

    name: ClassVar[str] = "analysis"
    contract_version: ClassVar[str] = "confflow.contract.executor.analysis.v1"
    # R2.2: the ExecutorCapability.ANALYSIS member is retired with the
    # registry entry; the implementation (deleted by R2.3a) keeps a plain
    # string capability so the orphaned module stays importable.
    capability: ClassVar[str] = "analysis"

    def execute(
        self,
        inputs: AnalysisInputs,
        definition: AnalysisDefinition | None = None,
        *,
        analysis_step_id: str | None = None,
    ) -> AnalysisStepResult:
        """Execute one analysis step over typed inputs.

        Parameters
        ----------
        inputs : AnalysisInputs
            Structures and results by port, plus the definition.
        definition : AnalysisDefinition or None
            Explicit definition overriding ``inputs.definition`` when
            given; otherwise the inputs' definition applies.
        analysis_step_id : str or None
            Real analysis step id carried as the source of computed
            results (provenance ``step_id``/``source_step_id``).  When
            ``None`` the compute seam receives ``None`` and downstream
            stamping must supply the real id; production work-item
            execution always passes the real id.

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
        structures_by_id = {record.id: record for record in structures}
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
            lookup, lineage_diagnostics = _lookup_for_group_with_diagnostics(
                resolved, pools, structures_by_id, source_results
            )
            group_diagnostics.extend(lineage_diagnostics)
            outcome: ComputedGroup | None = None
            if not [item for item in group_diagnostics if item.is_error] and resolved.is_complete:
                try:
                    candidate = model.compute(
                        resolved,
                        lookup,
                        analysis_step_id=analysis_step_id,
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
