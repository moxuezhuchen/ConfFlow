#!/usr/bin/env python3

"""V4 batch step execution.

A :class:`BatchStepExecutor` runs the deterministic work items of one compiled
calculation step: it schedules items under a scheduler policy, collects work
item results in canonical order, applies the completion policy, and assembles
the step result.  It understands threads and policies, never chemistry: no
program names, no task roles, no XYZ reading.

Steps still form barriers: no streaming, no cross-step scheduling.  Only the
concurrency width is a scheduler concern; scientific identity never depends
on it.

Persistence arrives through ports, not imports: an optional
:class:`WorkItemRepository` records item state transitions and an optional
:class:`ReuseStore` short-circuits execution for digest-identical work.  V4-2
ships in-memory implementations; durable stores land in V4-3 behind the same
protocols without touching execution contracts.
"""

from __future__ import annotations

import concurrent.futures
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

from ..domain._immutable import FrozenDict
from ..domain.artifact import ArtifactSet
from ..domain.completion import WorkItemStatus, evaluate_step_status
from ..domain.diagnostics import Diagnostic, DiagnosticSeverity
from ..domain.result import ResultSet
from ..domain.step_result import StepProvenance, StepResult
from ..domain.structure import StructureSet
from ..domain.work_item import WorkItem, WorkItemResult
from .checks import ScientificCheck
from .contracts import ExecutionBinding, ExecutionEnvironment
from .native import ProgramAdapter
from .profiles import ResultProfile
from .recovery import RecoveryPolicy
from .work_item_executor import ItemExecutionContext, WorkItemExecutor, _diagnostic

if TYPE_CHECKING:
    from ..workflow.v4.document import ScientificDefaults, ScientificDefinition
    from ..workflow.v4.plan import PlannedStep

__all__ = [
    "BatchStepExecutor",
    "InMemoryReuseStore",
    "InMemoryWorkItemRepository",
    "ReuseStore",
    "StepExecutionRequest",
    "WorkItemRepository",
]


@runtime_checkable
class WorkItemRepository(Protocol):
    """Port for recording work-item state transitions."""

    def record_started(self, work_item_id: str, step_id: str, logical_key: str) -> None:
        """Record that execution of a work item started."""
        ...

    def record_finished(self, result: WorkItemResult) -> None:
        """Record the terminal result of a work item."""
        ...

    def get_result(self, work_item_id: str) -> WorkItemResult | None:
        """Return the recorded result, or ``None`` when unknown."""
        ...


@runtime_checkable
class ReuseStore(Protocol):
    """Port for digest-keyed reuse of completed work.

    A reuse hit is valid only when the stored result's semantic digest equals
    the work item's semantic digest.  The store never compares logical keys.
    """

    def lookup(self, semantic_digest: str) -> WorkItemResult | None:
        """Return a reusable result for *semantic_digest*, if any."""
        ...

    def store(self, result: WorkItemResult) -> None:
        """Record a completed result for future reuse lookups."""
        ...


class InMemoryWorkItemRepository:
    """Thread-safe in-memory work-item repository for V4-2."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._started: dict[str, dict[str, str]] = {}
        self._results: dict[str, WorkItemResult] = {}

    def record_started(self, work_item_id: str, step_id: str, logical_key: str) -> None:
        """Record that execution of a work item started."""
        with self._lock:
            self._started[work_item_id] = {"step_id": step_id, "logical_key": logical_key}

    def record_finished(self, result: WorkItemResult) -> None:
        """Record the terminal result of a work item."""
        with self._lock:
            self._results[result.work_item_id] = result

    def get_result(self, work_item_id: str) -> WorkItemResult | None:
        """Return the recorded result, or ``None`` when unknown."""
        with self._lock:
            return self._results.get(work_item_id)

    def started_ids(self) -> tuple[str, ...]:
        """Return started work-item ids in sorted order (test support)."""
        with self._lock:
            return tuple(sorted(self._started))


class InMemoryReuseStore:
    """Thread-safe in-memory digest-keyed reuse store for V4-2."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._results: dict[str, WorkItemResult] = {}

    def lookup(self, semantic_digest: str) -> WorkItemResult | None:
        """Return a reusable result for *semantic_digest*, if any."""
        with self._lock:
            return self._results.get(semantic_digest)

    def store(self, result: WorkItemResult) -> None:
        """Record a completed result for future reuse lookups."""
        if result.semantic_digest is None or not result.is_completed:
            return
        with self._lock:
            self._results[result.semantic_digest] = result


@dataclass(frozen=True, slots=True)
class StepExecutionRequest:
    """Everything batch execution of one step may read."""

    step: PlannedStep
    items: tuple[WorkItem, ...] = ()
    scientific: ScientificDefinition | None = None
    scientific_defaults: ScientificDefaults | None = None
    adapter: ProgramAdapter | None = None
    profile: ResultProfile | None = None
    checks: tuple[ScientificCheck, ...] = ()
    recovery: RecoveryPolicy | None = None
    execution_binding: ExecutionBinding | None = None
    run_root: str = ""
    work_base: str | None = None
    environment: ExecutionEnvironment | None = None
    definition_digest: str | None = None
    should_cancel: Callable[[], bool] | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "items", tuple(self.items))
        object.__setattr__(self, "checks", tuple(self.checks))
        if self.scientific_defaults is None:
            from ..workflow.v4.document import ScientificDefaults

            object.__setattr__(self, "scientific_defaults", ScientificDefaults())


class BatchStepExecutor:
    """Schedule one step's work items and assemble its step result."""

    def __init__(
        self,
        executor: WorkItemExecutor | None = None,
        *,
        repository: WorkItemRepository | None = None,
        reuse_store: ReuseStore | None = None,
    ) -> None:
        self._executor = executor or WorkItemExecutor()
        self._repository = repository
        self._reuse_store = reuse_store

    @property
    def repository(self) -> WorkItemRepository | None:
        """Return the configured repository, if any."""
        return self._repository

    def execute_step(self, request: StepExecutionRequest) -> StepResult:
        """Execute every work item of one step and assemble the step result."""
        step = request.step
        ordered = tuple(sorted(request.items, key=lambda item: item.logical_key))
        validation_error = self._validate_request(request)
        if validation_error is not None:
            failed = tuple(
                self._preflight_failure(request, item, validation_error) for item in ordered
            )
            return self._assemble(request, failed, (validation_error,))
        stop_event = threading.Event()
        if request.should_cancel is not None:
            stop_event_proxy = request.should_cancel
        else:
            stop_event_proxy = None

        def cancelled() -> bool:
            if stop_event.is_set():
                return True
            return bool(stop_event_proxy and stop_event_proxy())

        width = step.scheduler.max_parallel_items or 1
        context = ItemExecutionContext(
            step_id=step.step_id,
            scientific=request.scientific,
            scientific_defaults=request.scientific_defaults,
            adapter=request.adapter,
            profile=request.profile,
            checks=request.checks,
            recovery=self._bind_recovery(request.recovery, request.adapter),
            execution_binding=request.execution_binding,
            run_root=request.run_root,
            work_base=request.work_base,
            supervisor=self._supervisor,
            environment=request.environment,
        )
        fail_fast = (
            step.scheduler.on_failure is not None and step.scheduler.on_failure.value == "fail_fast"
        )
        results: dict[str, WorkItemResult] = {}
        results_lock = threading.Lock()

        def run_one(item: WorkItem) -> WorkItemResult:
            if cancelled():
                return self._cancelled_without_launch(request, item, "fail_fast stop")
            if self._repository is not None:
                self._repository.record_started(item.id, step.step_id, item.logical_key)
            reused = self._reuse_lookup(item)
            if reused is not None:
                result = reused
            else:
                result = self._executor.execute(item, context, should_cancel=cancelled)
            if self._repository is not None:
                self._repository.record_finished(result)
            self._reuse_record(result)
            if fail_fast and not result.is_completed:
                stop_event.set()
            with results_lock:
                results[item.id] = result
            return result

        if width <= 1 or len(ordered) <= 1:
            for item in ordered:
                run_one(item)
        else:
            with concurrent.futures.ThreadPoolExecutor(
                max_workers=width, thread_name_prefix=f"v4-{step.step_id}"
            ) as pool:
                futures = [pool.submit(run_one, item) for item in ordered]
                for future in futures:
                    future.result()
        collected = tuple(results[item.id] for item in ordered)
        return self._assemble(request, collected, ())

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    _supervisor: Any = None

    @staticmethod
    def _bind_recovery(
        recovery: RecoveryPolicy | None, adapter: ProgramAdapter | None
    ) -> RecoveryPolicy | None:
        """Substitute a step-bound rescue policy for an unbound instance.

        The shared ``RECOVERIES`` registry holds an unbound
        ``ts_rescue_scan`` instance (no adapter, execute declines); batch
        execution binds the step's program adapter here so rescue work
        renders through the same file-format authority as primary work.
        """
        if recovery is None or adapter is None:
            return recovery
        if getattr(recovery, "name", "") != "ts_rescue_scan":
            return recovery
        if getattr(recovery, "_adapter", None) is not None:
            return recovery
        from .recovery_standard import TsRescueScanPolicy

        return TsRescueScanPolicy(adapter=adapter)

    def _validate_request(self, request: StepExecutionRequest) -> Diagnostic | None:
        step = request.step
        if request.scientific is None:
            return _diagnostic(
                "native_input_error",
                f"step {step.step_id!r} has no scientific definition",
                step_id=step.step_id,
            )
        if request.adapter is None:
            return _diagnostic(
                "environment_error",
                f"step {step.step_id!r} program {request.scientific.program!r} "
                "has no registered program adapter",
                step_id=step.step_id,
            )
        if request.profile is None:
            return _diagnostic(
                "capability_error",
                f"step {step.step_id!r} result profile is not resolved",
                step_id=step.step_id,
            )
        if self._supervisor is None:
            return _diagnostic(
                "environment_error",
                "no process supervisor is configured for batch execution",
                step_id=step.step_id,
            )
        return None

    def _preflight_failure(
        self, request: StepExecutionRequest, item: WorkItem, diagnostic: Diagnostic
    ) -> WorkItemResult:
        from ..domain.completion import WorkItemStatus
        from ..domain.work_item import RecoveryInfo, ResultError, Timing

        recovery_name = getattr(request.recovery, "name", "none")
        return WorkItemResult(
            work_item_id=item.id,
            status=WorkItemStatus.FAILED,
            diagnostics=(diagnostic,),
            timing=Timing(finished_at=time.time(), duration_seconds=0.0),
            error=ResultError(
                code=diagnostic.code,
                message=diagnostic.message,
                retryable=False,
                details=diagnostic.details,
            ),
            recovery=RecoveryInfo(profile=str(recovery_name), attempted=False),
            semantic_digest=item.semantic_digest,
        )

    def _cancelled_without_launch(
        self, request: StepExecutionRequest, item: WorkItem, reason: str
    ) -> WorkItemResult:
        from ..domain.completion import WorkItemStatus
        from ..domain.work_item import RecoveryInfo, ResultError, Timing

        recovery_name = getattr(request.recovery, "name", "none")
        diagnostic = _diagnostic(
            "cancellation_error",
            f"work item {item.logical_key!r} cancelled before launch: {reason}",
            step_id=request.step.step_id,
            work_item_id=item.id,
            logical_key=item.logical_key,
            details={"confirmed": True, "launched": False},
        )
        return WorkItemResult(
            work_item_id=item.id,
            status=WorkItemStatus.CANCELLED,
            diagnostics=(diagnostic,),
            timing=Timing(finished_at=time.time(), duration_seconds=0.0),
            error=ResultError(
                code="cancellation_error",
                message=diagnostic.message,
                retryable=False,
                details=diagnostic.details,
            ),
            recovery=RecoveryInfo(profile=str(recovery_name), attempted=False),
            semantic_digest=item.semantic_digest,
        )

    def _reuse_lookup(self, item: WorkItem) -> WorkItemResult | None:
        if self._reuse_store is None:
            return None
        hit = self._reuse_store.lookup(item.semantic_digest)
        if hit is None or not hit.is_completed:
            return None
        diagnostics = tuple(hit.diagnostics) + (
            Diagnostic(
                code="reuse_hit",
                message="work item reused from digest-identical completed result",
                severity=DiagnosticSeverity.INFO,
                work_item_id=item.id,
                logical_key=item.logical_key,
                details=FrozenDict({"reused_from": hit.work_item_id}),
            ),
        )
        return WorkItemResult(
            work_item_id=item.id,
            status=hit.status,
            structures=hit.structures,
            results=hit.results,
            artifacts=hit.artifacts,
            diagnostics=diagnostics,
            timing=hit.timing,
            error=None,
            recovery=hit.recovery,
            semantic_digest=item.semantic_digest,
            metadata=hit.metadata,
        )

    def _reuse_record(self, result: WorkItemResult) -> None:
        if self._reuse_store is not None:
            self._reuse_store.store(result)

    def _assemble(
        self,
        request: StepExecutionRequest,
        collected: tuple[WorkItemResult, ...],
        step_diagnostics: tuple[Diagnostic, ...],
    ) -> StepResult:
        step = request.step
        statuses = tuple(item.status for item in collected)
        status = evaluate_step_status(step.completion, statuses)
        accepted = tuple(item for item in collected if item.is_completed)
        structures = StructureSet()
        results = ResultSet()
        artifacts = ArtifactSet()
        for item in accepted:
            structures = structures + item.structures
            results = results + item.results
            artifacts = artifacts + item.artifacts
        completed = sum(1 for item in collected if item.is_completed)
        failed = sum(1 for item in collected if item.status is WorkItemStatus.FAILED)
        cancelled = sum(1 for item in collected if item.status is WorkItemStatus.CANCELLED)
        summary = FrozenDict(
            {
                "total": len(collected),
                "completed": completed,
                "failed": failed,
                "cancelled": cancelled,
                "completion_mode": step.completion.mode.value,
                "status": status.value,
            }
        )
        diagnostics = list(step_diagnostics)
        for item in collected:
            diagnostics.extend(item.diagnostics)
        return StepResult(
            step_id=step.step_id,
            status=status,
            structures=structures,
            results=results,
            artifacts=artifacts,
            item_results=collected,
            diagnostics=tuple(diagnostics),
            summary=summary,
            provenance=StepProvenance(
                workflow_definition_digest=request.definition_digest,
                step_semantic_digest=step.step_semantic_digest,
            ),
        )

    def with_supervisor(self, supervisor: Any) -> BatchStepExecutor:
        """Return a copy of this executor bound to *supervisor*."""
        clone = BatchStepExecutor(
            executor=self._executor,
            repository=self._repository,
            reuse_store=self._reuse_store,
        )
        clone._supervisor = supervisor
        return clone
