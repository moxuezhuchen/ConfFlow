#!/usr/bin/env python3

"""V4 analysis work-item adapter (wave-2 stream F, minimal integration).

Bridges the pure :class:`AnalysisExecutor` core
(``execute(AnalysisInputs, definition)`` over whole-set inputs) onto the
shared work-item seam
(``execute(work_item, context, *, should_cancel=None)``) so analysis runs
through the same durable claim → execute → commit → publish lifecycle as
every other executor.

Freeze §4.2: the default is ONE explicit whole-set aggregation WorkItem
via existing SINGLE bindings.  The core may compute groups inside that
WorkItem.  No application merge/copy loop, no per-group work items, no
native launches.

Definition mapping:
- ``kind`` comes from ``native["method"]`` (or ``analysis_kind``),
  default ``"reaction_profile"``.
- Energy keys (``energy_mode``, ``electronic_result_kind``,
  ``correction_result_kind``, ``energy_fallback``) go to
  ``policy_from_native``; ``endpoint_assignment`` and ``partial_policy``
  are parsed separately and never passed through the energy policy.
- ``analysis_step_id`` is always the real ``work_item.step_id`` (never
  ``None``): it threads through the core ``execute`` into the compute
  seam, and core-emitted results are re-stamped with the real step id
  plus producer-scoped ``result_id`` via ``make_result_id`` with
  ``producer_digest=work_item.semantic_digest``.
- ``partial_policy`` maps to ``PartialConsumption`` (``accept_subset`` or
  ``require_complete``).
- Explicit ``endpoint_assignment`` flows through the core into every
  computed result value, provenance metadata, and manifest; the default
  stays pure path direction (``unassigned``).

The adapter never raises into batch: every failure is a typed FAILED
WorkItemResult.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Mapping
from typing import Any

from ..domain._immutable import FrozenDict
from ..domain.completion import WorkItemStatus
from ..domain.diagnostics import Diagnostic, DiagnosticSeverity
from ..domain.errors import DomainError
from ..domain.result import ResultSet, ScientificResult, make_result_id
from ..domain.structure import StructureSet
from ..domain.work_item import RecoveryInfo, Timing, WorkItem, WorkItemResult
from .compute import ReactionEnergyModel, policy_from_native
from .executor import AnalysisExecutor
from .models import (
    AnalysisDefinition,
    AnalysisError,
    AnalysisInputs,
)

__all__ = ["AnalysisItemAdapter", "build_analysis_definition", "ANALYSIS_KIND_DEFAULT"]

#: Default analysis kind when native names no method.
ANALYSIS_KIND_DEFAULT = "reaction_profile"

#: Native keys consumed as step-level routing (never energy policy).
_STEP_LEVEL_KEYS = frozenset({"method", "analysis_kind"})

#: Native keys consumed as explicit endpoint assignment (never energy policy).
_ASSIGNMENT_KEYS = frozenset({"endpoint_assignment"})

#: Native keys consumed as partial policy (never energy policy).
_PARTIAL_KEYS = frozenset({"partial_policy"})


def _text_or_none(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def build_analysis_definition(
    native: Mapping[str, Any],
    *,
    analysis_step_id: str,
) -> AnalysisDefinition:
    """Build an explicit AnalysisDefinition from step native params."""
    if not isinstance(native, Mapping):
        raise AnalysisError(
            "analysis_invalid_definition",
            "analysis native must be a mapping",
            details={"reason": "invalid_native_shape"},
        )
    raw = dict(native)
    kind = _text_or_none(raw.get("method", raw.get("analysis_kind"))) or ANALYSIS_KIND_DEFAULT
    # Endpoint assignment: parsed separately, never through the energy policy.
    assignment_raw = raw.get("endpoint_assignment", None)
    if assignment_raw is None:
        endpoint_assignment: dict[str, str] = {}
    elif isinstance(assignment_raw, Mapping):
        endpoint_assignment = {str(k): str(v) for k, v in dict(assignment_raw).items()}
    else:
        raise AnalysisError(
            "analysis_invalid_definition",
            "endpoint_assignment must be a mapping of endpoint slot to role",
            details={"reason": "invalid_assignment_shape"},
        )
    # Partial policy: parsed separately, never through the energy policy.
    partial_raw = raw.get("partial_policy", None)
    if partial_raw is None:
        from ..domain.binding import PartialConsumption

        partial_policy = PartialConsumption.REQUIRE_COMPLETE
    else:
        from ..domain.binding import PartialConsumption

        try:
            partial_policy = PartialConsumption(str(partial_raw))
        except ValueError as exc:
            raise AnalysisError(
                "analysis_invalid_definition",
                f"partial_policy must be one of "
                f"{[item.value for item in PartialConsumption]}, got {partial_raw!r}",
                details={"reason": "invalid_partial_policy"},
            ) from exc
    # Energy policy sees only energy keys (+ step-level routing keys).
    energy_native = {
        key: value
        for key, value in raw.items()
        if key not in _ASSIGNMENT_KEYS and key not in _PARTIAL_KEYS
    }
    try:
        policy = policy_from_native(energy_native)
    except ValueError as exc:
        raise AnalysisError(
            "analysis_invalid_definition",
            f"invalid analysis energy params: {exc}",
            details={"reason": "invalid_energy_params"},
        ) from exc
    params = {key: value for key, value in energy_native.items() if key not in _STEP_LEVEL_KEYS}
    return AnalysisDefinition(
        kind=kind,
        energy_model=ReactionEnergyModel(policy),
        params=FrozenDict({str(k): v for k, v in params.items()}),
        endpoint_assignment=FrozenDict(endpoint_assignment),
        partial_policy=partial_policy,
    )


def _stamp_results(
    results: tuple[ScientificResult, ...],
    *,
    step_id: str,
    work_item_id: str,
    producer_digest: str,
) -> tuple[ScientificResult, ...]:
    """Re-stamp core results with real step id + producer-scoped result ids.

    Loud on contract violation: ``dataclasses.replace``/``make_result_id``
    failures raise (the caller converts them to an explicit FAILED item
    with a diagnostic).  Results are never emitted half-stamped.
    """
    from dataclasses import replace as _replace

    stamped: list[ScientificResult] = []
    for result in results:
        provenance = result.provenance
        if provenance is None:
            raise DomainError(
                f"analysis result kind={result.kind!r} carries no provenance; "
                "refusing to publish unstamped analysis output"
            )
        provenance = _replace(
            provenance,
            step_id=step_id,
            work_item_id=work_item_id,
        )
        result_id = make_result_id(
            step_id=step_id,
            work_item_id=work_item_id,
            kind=result.kind,
            subject_structure_id=result.subject_structure_id,
            program=provenance.program,
            method=provenance.method,
            basis=provenance.basis,
            producer_digest=producer_digest,
        )
        stamped.append(
            _replace(
                result,
                source_step_id=step_id,
                source_work_item_id=work_item_id,
                provenance=provenance,
                result_id=result_id,
            )
        )
    return tuple(stamped)


class AnalysisItemAdapter:
    """Work-item seam adapter around the pure analysis core."""

    name: str = "analysis"
    contract_version: str = AnalysisExecutor.contract_version
    capability = AnalysisExecutor.capability

    def __init__(self) -> None:
        self._core = AnalysisExecutor()

    def execute(
        self,
        work_item: WorkItem,
        context: Any,
        *,
        should_cancel: Callable[[], bool] | None = None,
    ) -> WorkItemResult:
        """Execute one whole-set aggregation analysis work item.

        Only expected domain failures (bad definition, bad inputs, failed
        stamping) become typed FAILED results.  Unexpected exceptions
        (programmer error, corruption the contracts do not model) propagate
        — batch performs no executor catch, so they fail stop loudly
        instead of masquerading as scientific failure.
        """
        wall_start = time.time()
        monotonic_start = time.monotonic()
        try:
            return self._run(work_item, context, wall_start, monotonic_start, should_cancel)
        except (AnalysisError, DomainError, ValueError) as exc:
            code = getattr(exc, "code", type(exc).__name__)
            return self._fail(
                work_item,
                context,
                wall_start,
                monotonic_start,
                f"{code}: {exc}",
            )

    def _run(
        self,
        work_item: WorkItem,
        context: Any,
        wall_start: float,
        monotonic_start: float,
        should_cancel: Callable[[], bool] | None,
    ) -> WorkItemResult:
        if should_cancel is not None and should_cancel():
            return self._cancelled(work_item, context, wall_start, monotonic_start)
        scientific = getattr(context, "scientific", None)
        native: Mapping[str, Any] = {}
        if scientific is not None:
            native = dict(getattr(scientific, "native", {}) or {})
        definition = build_analysis_definition(native, analysis_step_id=work_item.step_id)
        named = work_item.named_inputs
        structures = FrozenDict(
            {name: StructureSet(tuple(records)) for name, records in named.structures.items()}
        )
        results = FrozenDict(
            {name: ResultSet(tuple(records)) for name, records in named.results.items()}
        )
        inputs = AnalysisInputs(structures=structures, results=results, definition=definition)
        outcome = self._core.execute(inputs, analysis_step_id=work_item.step_id)
        stamped = _stamp_results(
            tuple(outcome.results),
            step_id=work_item.step_id,
            work_item_id=work_item.id,
            producer_digest=work_item.semantic_digest,
        )
        timing = Timing(
            started_at=wall_start,
            finished_at=max(time.time(), wall_start),
            duration_seconds=max(0.0, time.monotonic() - monotonic_start),
        )
        if [item for item in outcome.diagnostics if item.is_error]:
            return WorkItemResult(
                work_item_id=work_item.id,
                status=WorkItemStatus.FAILED,
                structures=outcome.structures,
                results=ResultSet(stamped),
                artifacts=outcome.artifacts,
                diagnostics=tuple(outcome.diagnostics),
                timing=timing,
                error=None,
                recovery=RecoveryInfo(profile="none", attempted=False),
                semantic_digest=work_item.semantic_digest,
            )
        return WorkItemResult(
            work_item_id=work_item.id,
            status=WorkItemStatus.COMPLETED,
            structures=outcome.structures,
            results=ResultSet(stamped),
            artifacts=outcome.artifacts,
            diagnostics=tuple(outcome.diagnostics)
            or (
                Diagnostic(
                    code="analysis_completed",
                    message="analysis aggregation completed",
                    severity=DiagnosticSeverity.INFO,
                    step_id=work_item.step_id,
                    work_item_id=work_item.id,
                    logical_key=work_item.logical_key,
                    details=FrozenDict({"results": len(stamped)}),
                ),
            ),
            timing=timing,
            recovery=RecoveryInfo(profile="none", attempted=False),
            semantic_digest=work_item.semantic_digest,
        )

    def _fail(
        self,
        work_item: WorkItem,
        context: Any,
        wall_start: float,
        monotonic_start: float,
        message: str,
    ) -> WorkItemResult:
        from ..execution.native import NativeErrorCode
        from ..execution.work_item_executor import _diagnostic

        step_id = getattr(work_item, "step_id", "")
        timing = Timing(
            started_at=wall_start,
            finished_at=max(time.time(), wall_start),
            duration_seconds=max(0.0, time.monotonic() - monotonic_start),
        )
        context_step = getattr(context, "step_id", step_id)
        return WorkItemResult(
            work_item_id=work_item.id,
            status=WorkItemStatus.FAILED,
            diagnostics=(
                _diagnostic(
                    NativeErrorCode.NATIVE_INPUT_ERROR,
                    message,
                    step_id=context_step,
                    work_item_id=work_item.id,
                    logical_key=work_item.logical_key,
                ),
            ),
            timing=timing,
            error=None,
            recovery=RecoveryInfo(profile="none", attempted=False),
            semantic_digest=work_item.semantic_digest,
        )

    def _cancelled(
        self,
        work_item: WorkItem,
        context: Any,
        wall_start: float,
        monotonic_start: float,
    ) -> WorkItemResult:
        from ..execution.native import NativeErrorCode
        from ..execution.work_item_executor import _diagnostic

        timing = Timing(
            started_at=wall_start,
            finished_at=max(time.time(), wall_start),
            duration_seconds=max(0.0, time.monotonic() - monotonic_start),
        )
        return WorkItemResult(
            work_item_id=work_item.id,
            status=WorkItemStatus.CANCELLED,
            diagnostics=(
                _diagnostic(
                    NativeErrorCode.CANCELLATION_ERROR,
                    "work item cancelled",
                    step_id=getattr(context, "step_id", work_item.step_id),
                    work_item_id=work_item.id,
                    logical_key=work_item.logical_key,
                    details={"confirmed": True},
                ),
            ),
            timing=timing,
            error=None,
            recovery=RecoveryInfo(profile="none", attempted=False),
            semantic_digest=work_item.semantic_digest,
        )
