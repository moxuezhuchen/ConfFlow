#!/usr/bin/env python3

"""V4 batch step execution.

Runs one compiled calculation step's deterministic work items under a
scheduler policy, collecting results in canonical order and assembling the
step result; it understands threads and policies, never chemistry. Steps are
barriers with no streaming or cross-step scheduling; only concurrency width is
a scheduler concern and scientific identity never depends on it. Persistence
arrives through ports not imports: optional ``WorkItemRepository`` records
transitions and optional ``ReuseStore`` short-circuits only digest-identical
work; in-memory implementations ship now, durable stores land behind the same
protocols without touching execution contracts.
"""

from __future__ import annotations

import concurrent.futures
import os
import threading
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Any, Protocol, cast, runtime_checkable

from ..domain._immutable import FrozenDict
from ..domain.artifact import ArtifactSet
from ..domain.completion import WorkItemStatus, evaluate_step_status
from ..domain.diagnostics import Diagnostic, DiagnosticSeverity
from ..domain.errors import PublicationError
from ..domain.result import ResultSet
from ..domain.step_result import StepProvenance, StepResult
from ..domain.structure import StructureSet
from ..domain.work_item import WorkItem, WorkItemResult
from ..persistence.artifacts import ArtifactIntegrityError, verify_artifact
from ..persistence.contracts import (
    CorruptStateError,
    OwnerIdentity,
    OwnerVerdict,
    PersistenceError,
    ReuseCode,
    StoredWorkItemStatus,
    step_dir,
    validate_run_root,
)
from ..persistence.publication import (
    load_published_step_result,
    publish_step_result,
    verify_for_publication,
)
from ..persistence.recovery import owner_identity_current, reconcile_owner
from ..persistence.reuse import ReuseInputs, build_producer_provenance, evaluate_reuse
from .checks import ScientificCheck
from .contracts import ExecutionBinding, ExecutionEnvironment
from .native import ProgramAdapter
from .profiles import ResultProfile
from .recovery import RecoveryPolicy
from .work_item_executor import ItemExecutionContext, WorkItemExecutor, _diagnostic

if TYPE_CHECKING:
    from ..persistence.work_items import SqliteWorkItemStore
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

    def record_finished(
        self, result: WorkItemResult, *, environment_digest: str | None = None
    ) -> None:
        """Record the terminal result of a work item."""
        del environment_digest
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


def _item_error_diagnostics(item: WorkItemResult, *, step_id: str) -> tuple[Diagnostic, ...]:
    """Project one failed item's error into a step-level diagnostic."""
    error = item.error
    if error is None:
        return ()
    if any(
        existing.code == error.code and existing.message == error.message
        for existing in item.diagnostics
    ):
        return ()
    return (
        Diagnostic(
            code=error.code,
            message=error.message,
            severity=DiagnosticSeverity.ERROR,
            step_id=step_id,
            work_item_id=item.work_item_id,
            details=error.details,
        ),
    )


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
    native_env: FrozenDict | None = None
    definition_digest: str | None = None
    should_cancel: Callable[[], bool] | None = None
    producer_provenance: FrozenDict | None = None
    executor_capability: str | None = None
    #: Optional generation-ownership guard, called immediately before step
    #: publication as an advisory fast-fail.  It is NOT the authority: the
    #: authoritative expected-owner check happens inside
    #: ``publish_step_result``'s generation arbitration region together with
    #: the write itself (``generation_id`` below).
    ownership_guard: Callable[[], None] | None = None
    #: Generation that owns this step's publication.  When set, the step
    #: result write is fenced by the run root's generation arbitration lock:
    #: a superseded generation raises ``StaleGenerationError`` and never
    #: replaces the current generation's durable step truth.
    generation_id: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "items", tuple(self.items))
        object.__setattr__(self, "checks", tuple(self.checks))
        if self.scientific_defaults is None:
            from ..workflow.v4.document import ScientificDefaults

            object.__setattr__(self, "scientific_defaults", ScientificDefaults())
        if self.producer_provenance is not None and not isinstance(
            self.producer_provenance, FrozenDict
        ):
            object.__setattr__(self, "producer_provenance", FrozenDict(self.producer_provenance))
        if self.native_env is not None and not isinstance(self.native_env, FrozenDict):
            object.__setattr__(self, "native_env", FrozenDict(self.native_env))


class BatchStepExecutor:
    """Schedule one step's work items and assemble its step result.

    One generic item lifecycle for every executor capability: the
    configured *executor* (resolved by the caller through the execution
    registry's ``executor_implementation``) runs each item through
    the single ``execute(work_item, context, *, should_cancel=None)``
    seam under the shared register→reuse→claim→commit→publish protocol.
    Batch never branches on scientific capability; capability selection
    lives in the registry (owner A).
    """

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
            scientific=cast("ScientificDefinition", request.scientific),
            scientific_defaults=cast("ScientificDefaults", request.scientific_defaults),
            adapter=cast("ProgramAdapter", request.adapter),
            profile=cast("ResultProfile", request.profile),
            checks=request.checks,
            recovery=self._bind_recovery(request.recovery, request.adapter),
            execution_binding=request.execution_binding,
            run_root=request.run_root,
            work_base=request.work_base,
            supervisor=self._supervisor,
            environment=request.environment,
            native_env=request.native_env,
        )
        fail_fast = (
            step.scheduler.on_failure is not None and step.scheduler.on_failure.value == "fail_fast"
        )
        results: dict[str, WorkItemResult] = {}
        results_lock = threading.Lock()

        def run_one(item: WorkItem) -> WorkItemResult:
            if cancelled():
                result = self._cancelled_without_launch(request, item, "fail_fast stop")
                with results_lock:
                    results[item.id] = result
                return result
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

    def execute_step_resumable(
        self,
        request: StepExecutionRequest,
        *,
        store: SqliteWorkItemStore,
        run_root: str,
        owner_token: str | None = None,
    ) -> StepResult:
        """Execute one step with durable per-item resume and reuse.

        Every item is registered in *store*, evaluated through an explicit
        :class:`ReuseDecision`, and only executed when no valid durable
        result exists.  Terminal outcomes commit durably before assembly;
        the assembled step result is atomically published under *run_root*.
        Run-state transitions stay with the caller (run lifecycle truth
        lives outside the step executor); the published digest is
        re-discoverable via ``detect_published``.

        Invalidation (definition/input/environment/provenance/artifact
        mismatch on a terminal row) fails closed: the item is reported as
        failed without executing and without touching history.  Re-running
        a new definition generation over the same store is explicit future
        work, never silent overwrite.  Likewise ``CANCELLED`` rows are
        carried, never auto-relaunched.

        Parameters
        ----------
        request : StepExecutionRequest
            Step, items, and resolved contracts, as in :meth:`execute_step`.
            ``work_base`` is ignored: durable item directories always live
            under ``steps/<step_id>/`` in *run_root* so artifact locators
            stay portable.
        store : SqliteWorkItemStore
            Durable per-item truth for this step (duck-typed; the executor
            never owns the database lifecycle).
        run_root : str
            Managed persistence root owning the step directory.
        owner_token : str | None
            Claim token binding this controller's claims; defaults to a
            step-scoped token.
            Reused, blocked, and invalidated items never
            execute.

        Returns
        -------
        StepResult
            Assembled (and published) step result covering every item: reused
            stored results, freshly executed results, and synthetic reports
            for blocked/invalidated/carried items.
        """
        step = request.step
        ordered = tuple(sorted(request.items, key=lambda item: item.logical_key))
        if request.environment is None:
            # Unknown never stands in for verified equivalence: durable
            # execution requires a measured environment on every path,
            # including pure executors (which record their implementation
            # environment, never a native binary).  Checked before request
            # validation so the prohibition cannot hide behind preflight.
            raise PersistenceError(
                f"durable execution of step {step.step_id!r} requires a measured "
                "environment; environment=None is prohibited"
            )
        validation_error = self._validate_request(request)
        if validation_error is not None:
            failed = tuple(
                self._preflight_failure(request, item, validation_error) for item in ordered
            )
            return self._assemble(request, failed, (validation_error,))
        run_root_abs = validate_run_root(run_root)
        # Durability pre-check: a corrupt published step_result.json fails
        # closed here, before any re-execution or republication. Corruption
        # is never healed by overwrite; load_published_step_result raises
        # CorruptStateError for unreadable/invalid payloads and returns None
        # when absent (fresh run). The error propagates without touching
        # the corrupt bytes.
        load_published_step_result(run_root=run_root_abs, step_id=step.step_id)
        durable_base = os.path.join(step_dir(run_root_abs, step.step_id))
        owner = owner_identity_current(owner_token=owner_token or f"v4-batch:{step.step_id}")
        environment = request.environment
        environment_digest = environment.digest()
        # ONE immutable native-environment snapshot per step.  The
        # application measures the environment over exactly this mapping.
        # Direct batch callers without a threaded snapshot get the
        # producer-side rule (ambient + declared) built once here.
        native_env = request.native_env
        if native_env is None and request.execution_binding is not None:
            from .binding_resolution import effective_native_env

            native_env = FrozenDict(
                effective_native_env(request.execution_binding, inherit=os.environ)
            )
        provenance = self._current_provenance(request)
        context = ItemExecutionContext(
            step_id=step.step_id,
            scientific=cast("ScientificDefinition", request.scientific),
            scientific_defaults=cast("ScientificDefaults", request.scientific_defaults),
            adapter=cast("ProgramAdapter", request.adapter),
            profile=cast("ResultProfile", request.profile),
            checks=request.checks,
            recovery=self._bind_recovery(request.recovery, request.adapter),
            execution_binding=request.execution_binding,
            run_root=run_root_abs,
            work_base=durable_base,
            supervisor=self._supervisor,
            environment=request.environment,
            native_env=native_env,
            executor_capability=request.executor_capability,
        )
        stop_event = threading.Event()
        stop_event_proxy = request.should_cancel

        def cancelled() -> bool:
            if stop_event.is_set():
                return True
            return bool(stop_event_proxy and stop_event_proxy())

        fail_fast = (
            step.scheduler.on_failure is not None and step.scheduler.on_failure.value == "fail_fast"
        )
        width = step.scheduler.max_parallel_items or 1
        results: dict[str, WorkItemResult] = {}
        durable_ids: set[str] = set()
        shared_lock = threading.Lock()

        def run_one(item: WorkItem) -> WorkItemResult:
            if cancelled():
                result = self._cancelled_without_launch(request, item, "fail_fast stop")
                with shared_lock:
                    results[item.id] = result
                return result
            try:
                result, durable = self._durable_run_one(
                    request,
                    item,
                    context,
                    store=store,
                    run_root=run_root_abs,
                    owner=owner,
                    environment_digest=environment_digest,
                    provenance=provenance,
                    should_cancel=cancelled,
                )
            except (PersistenceError, CorruptStateError) as exc:
                result = self._durable_synthetic_failure(
                    request,
                    item,
                    code="persistence_error",
                    message=f"durable store failure: {exc}",
                    retryable=False,
                    details={"error": str(exc)},
                )
                durable = False
            if durable:
                with shared_lock:
                    durable_ids.add(item.id)
            if fail_fast and not result.is_completed:
                stop_event.set()
            with shared_lock:
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
        step_result = self._assemble(request, collected, ())
        # Strict durable publication (D-owned): a step result covering
        # items without durable results is never published.  Carried
        # CANCELLED items are exempt: they contribute no scientific
        # payload (accepted sets only merge completed items) and are
        # explicit terminal lifecycle states, so a cancelled step still
        # publishes its lifecycle truth instead of crashing the run.
        # Every other non-durable item (blocked/invalidated/uncommitted)
        # refuses publication loudly; the gap diagnostic is reported
        # through the raised error, never through a published-but-unbacked
        # step file.
        cancelled_ids = {
            item.work_item_id for item in collected if item.status is WorkItemStatus.CANCELLED
        }
        gap_ids = sorted(
            {item.work_item_id for item in collected} - set(durable_ids) - cancelled_ids
        )
        if gap_ids:
            # Strict durable publication: a step result covering items
            # without durable results is never published.  The gap
            # diagnostic is reported through the raised error, not through
            # a published-but-unbacked step file.  Status-only blocked
            # reports stay lifecycle/report data; they never counterfeit
            # durable publication.  Per-item reasons ride in the message so
            # controllers can distinguish invalidation (stale generation)
            # from blocking failures without a payload.
            gap = Diagnostic(
                code="publication_durability_gap",
                message=(
                    "step result covers items without durable results "
                    f"({len(gap_ids)}); refusing to publish"
                ),
                severity=DiagnosticSeverity.ERROR,
                step_id=step.step_id,
                details=FrozenDict({"non_durable_item_ids": sorted(gap_ids)}),
            )
            by_id = {item.work_item_id: item for item in collected}

            def _gap_reason(item_id: str) -> str:
                item = by_id[item_id]
                code = item.error.code if item.error is not None else "no-error"
                return f"{item_id}={item.status.value}:{code}"

            reasons = sorted(_gap_reason(item_id) for item_id in gap_ids)
            raise PersistenceError(
                f"refusing to publish step {step.step_id!r}: "
                f"{len(gap_ids)} items lack durable results "
                f"({', '.join(sorted(gap_ids))}); gap code={gap.code}; "
                f"reasons={', '.join(reasons)}"
            )
        if request.ownership_guard is not None:
            # CONTRACT 7: once a newer generation owns the run root, a
            # superseded writer must not publish step-level truth either.
            # This guard is an advisory fast-fail; the authoritative
            # expected-owner check and the write happen atomically inside
            # ``publish_step_result`` (see ``request.generation_id``).
            request.ownership_guard()
        verified_checksums = {
            artifact.checksum.lower()
            for item in collected
            if item.work_item_id in durable_ids
            for artifact in item.artifacts
            if artifact.checksum is not None
        }
        try:
            verify_for_publication(
                step_result,
                durable_item_ids=sorted(durable_ids),
                verified_artifact_checksums=sorted(verified_checksums),
            )
        except PublicationError as exc:
            raise PersistenceError(f"refusing to publish step {step.step_id!r}: {exc}") from exc
        publish_step_result(
            run_root=run_root_abs,
            step_id=step.step_id,
            step_result=step_result,
            expected_generation_id=request.generation_id,
        )
        return step_result

    # ------------------------------------------------------------------
    # Durable helpers
    # ------------------------------------------------------------------

    def _launch(
        self,
        item: WorkItem,
        context: ItemExecutionContext,
        *,
        store: SqliteWorkItemStore,
        should_cancel: Callable[[], bool],
        attempt: int | None = None,
    ) -> WorkItemResult:
        """Launch one claimed item in-process through the local executor."""
        if attempt is None:
            attempt = store.get_registered(item.id)["current_attempt"]
        return self._executor.execute(item, context, should_cancel=should_cancel)

    def _reconcile_before_retry(
        self,
        item: WorkItem,
        context: ItemExecutionContext,
        *,
        store: SqliteWorkItemStore,
        environment_digest: str | None,
    ) -> WorkItemResult | None:
        """Reconcile an abandoned attempt before retry (R1.2: always None)."""
        return None

    @staticmethod
    def _commit_environment_digest(result: WorkItemResult) -> str | None:
        """Return the execution-environment override for committing *result*.

        R1.2 retired remote delivery: local results carry no override and
        keep the registration-time digest. The metadata-key lookup stays
        so stale carriers fail closed to None instead of mis-committing.
        """
        try:
            metadata = result.metadata
        except AttributeError:
            return None
        if not isinstance(metadata, Mapping):
            return None
        from .environment import EXECUTION_ENVIRONMENT_METADATA_KEY

        carried = metadata.get(EXECUTION_ENVIRONMENT_METADATA_KEY)
        if not isinstance(carried, Mapping):
            return None
        digest = carried.get("digest")
        if not isinstance(digest, str) or not digest.strip():
            return None
        return digest

    @staticmethod
    def _current_provenance(request: StepExecutionRequest) -> FrozenDict:
        """Build the producer-provenance record for *request*."""
        if request.producer_provenance is not None:
            return request.producer_provenance
        adapter = request.adapter
        profile = request.profile
        if adapter is None or profile is None:  # guarded by _validate_request
            raise PersistenceError("durable execution requires an adapter and a profile")
        recovery = request.recovery
        recovery_version = getattr(recovery, "contract_version", None) or "none"
        return build_producer_provenance(
            adapter_version=adapter.adapter_version,
            profile_version=profile.contract_version,
            check_versions={check.name: check.contract_version for check in request.checks},
            recovery_version=recovery_version,
            parser_version=adapter.parser_version,
        )

    @staticmethod
    def _current_reuse_inputs(
        item: WorkItem,
        *,
        step_semantic_digest: str,
        environment_digest: str | None,
        provenance: FrozenDict,
    ) -> ReuseInputs:
        """Build the current reuse axes for *item*."""
        return ReuseInputs(
            work_item_digest=item.semantic_digest,
            step_semantic_digest=step_semantic_digest,
            environment_digest=environment_digest,
            producer_provenance=provenance,
            artifact_checksums=(),
        )

    @staticmethod
    def _stored_reuse_inputs(store: SqliteWorkItemStore, work_item_id: str) -> ReuseInputs:
        """Build the stored reuse axes for a registered item."""
        registered = store.get_registered(work_item_id)
        return ReuseInputs(
            work_item_digest=registered["work_item_digest"],
            step_semantic_digest=registered["step_semantic_digest"],
            environment_digest=registered["environment_digest"],
            producer_provenance=FrozenDict(registered["producer_provenance"]),
            artifact_checksums=(),
        )

    def _durable_synthetic_failure(
        self,
        request: StepExecutionRequest,
        item: WorkItem,
        *,
        code: str,
        message: str,
        retryable: bool,
        details: dict[str, Any] | None = None,
    ) -> WorkItemResult:
        """Build a non-persisted failure report for a non-executed item."""
        from ..domain.work_item import RecoveryInfo, ResultError, Timing

        recovery_name = getattr(request.recovery, "name", "none")
        diagnostic = _diagnostic(
            code,
            message,
            step_id=request.step.step_id,
            work_item_id=item.id,
            logical_key=item.logical_key,
            details=details or {},
        )
        return WorkItemResult(
            work_item_id=item.id,
            status=WorkItemStatus.FAILED,
            diagnostics=(diagnostic,),
            timing=Timing(finished_at=time.time(), duration_seconds=0.0),
            error=ResultError(
                code=code,
                message=message,
                retryable=retryable,
                details=FrozenDict(details or {}),
            ),
            recovery=RecoveryInfo(profile=str(recovery_name), attempted=False),
            semantic_digest=item.semantic_digest,
        )

    def _durable_run_one(
        self,
        request: StepExecutionRequest,
        item: WorkItem,
        context: ItemExecutionContext,
        *,
        store: SqliteWorkItemStore,
        run_root: str,
        owner: OwnerIdentity,
        environment_digest: str | None,
        provenance: FrozenDict,
        should_cancel: Callable[[], bool],
        _claim_retried: bool = False,
    ) -> tuple[WorkItemResult, bool]:
        """Run one item through register → decide → execute-or-report."""
        step = request.step
        try:
            store.register_item(
                work_item_id=item.id,
                logical_key=item.logical_key,
                step_id=step.step_id,
                work_item_digest=item.semantic_digest,
                step_semantic_digest=step.step_semantic_digest,
                environment_digest=environment_digest,
                producer_provenance=dict(provenance.thaw()),
            )
        except CorruptStateError:
            # Registration conflict: the id is already registered under
            # different digests, i.e. a definition/input change.  The stored
            # record below carries the old axes, so evaluation reports the
            # precise invalidation and history is never overwritten.
            pass
        stored_status = store.get_state(item.id)
        current_inputs = self._current_reuse_inputs(
            item,
            step_semantic_digest=step.step_semantic_digest,
            environment_digest=environment_digest,
            provenance=provenance,
        )
        stored_inputs: ReuseInputs | None = None
        if stored_status is not None:
            try:
                stored_inputs = self._stored_reuse_inputs(store, item.id)
            except PersistenceError:
                stored_inputs = None
        owner_verdict: OwnerVerdict | None = None
        if stored_status is StoredWorkItemStatus.RUNNING:
            recorded = store.get_owner(item.id)
            owner_verdict = (
                reconcile_owner(recorded) if recorded is not None else OwnerVerdict.UNCERTAIN
            )
        artifacts_verified = True
        if stored_status is StoredWorkItemStatus.COMPLETED:
            artifacts_verified = self._verify_stored_artifacts(store, run_root, item.id)
        decision = evaluate_reuse(
            current=current_inputs,
            stored=stored_inputs,
            stored_status=stored_status,
            owner_verdict=owner_verdict,
            artifacts_verified=artifacts_verified,
            work_item_id=item.id,
        )
        code = decision.decision
        if code is ReuseCode.REUSE:
            stored_result = store.get_result(item.id)
            if stored_result is None:
                return (
                    self._durable_synthetic_failure(
                        request,
                        item,
                        code="persistence_error",
                        message=(
                            f"work item {item.logical_key!r} is durably COMPLETED "
                            "but its result payload is missing; refusing to invent one"
                        ),
                        retryable=False,
                        details=dict(decision.details.thaw()),
                    ),
                    False,
                )
            return self._rescoped_reuse(item, stored_result), True
        if code is ReuseCode.EXECUTE_NEW and decision.details.get("requires_explicit_retry"):
            carried = store.get_result(item.id)
            if carried is not None:
                return self._rescoped_reuse(item, carried), True
            return (
                self._cancelled_without_launch(
                    request, item, "durable CANCELLED record requires explicit retry"
                ),
                False,
            )
        if code in (
            ReuseCode.EXECUTE_NEW,
            ReuseCode.RETRY_FAILED,
            ReuseCode.RECOVER_ABANDONED,
        ):
            if code is ReuseCode.RECOVER_ABANDONED:
                # R7: reconcile the abandoned attempt's durable result BEFORE
                # advancing the attempt.  A worker that completed and wrote a
                # verified bundle must be imported, committed, and returned —
                # advancing first would make the bundle stale and duplicate
                # native execution.
                #
                # Reconciliation is only legal for the SAME registered
                # execution generation: the abandoned attempt's digest axes
                # (work item, step semantics, environment, provenance) must
                # equal the axes of the invocation asking for recovery.  When
                # they differ (e.g. the execution environment changed after
                # the crash), adopting the bundle would publish the abandoned
                # generation's result as the new generation's result without
                # any diagnostic.  Fall through to the normal
                # interrupted -> next-attempt -> relaunch protocol instead;
                # the abandoned bundle stays on disk as history.
                reconciled = None
                if stored_inputs is not None and stored_inputs == current_inputs:
                    reconciled = self._reconcile_before_retry(
                        item,
                        context,
                        store=store,
                        environment_digest=environment_digest,
                    )
                if reconciled is not None:
                    return reconciled, True
                store.mark_interrupted(item.id, reason=decision.reason)
            if store.claim(item.id, owner=owner):
                # The real durable attempt just opened drives the
                # attempt-isolated execution directory (via the context).
                attempt = int(store.get_registered(item.id)["current_attempt"])
                result = self._launch(
                    item,
                    replace(context, attempt=attempt),
                    store=store,
                    should_cancel=should_cancel,
                    attempt=attempt,
                )
                store.record_finished(
                    result,
                    environment_digest=self._commit_environment_digest(result),
                )
                return result, True
            return self._contested_claim(
                request,
                item,
                context,
                store=store,
                owner=owner,
                environment_digest=environment_digest,
                provenance=provenance,
                run_root=run_root,
                should_cancel=should_cancel,
                _claim_retried=_claim_retried,
            )
        if code is ReuseCode.BLOCKED_UNCERTAIN_OWNER:
            return (
                self._durable_synthetic_failure(
                    request,
                    item,
                    code=code.value,
                    message=(
                        f"work item {item.logical_key!r} is owned by a possibly live "
                        f"boundary ({decision.reason}); duplicate launch forbidden"
                    ),
                    retryable=True,
                    details=dict(decision.details.thaw()),
                ),
                False,
            )
        if code is ReuseCode.INVALIDATE_ENVIRONMENT and stored_status in (
            StoredWorkItemStatus.COMPLETED,
            StoredWorkItemStatus.FAILED,
            StoredWorkItemStatus.INTERRUPTED,
        ):
            # Environment-only generation advance: the binding (executable
            # content + effective env) moved while definition/inputs stayed
            # put. Unlike definition/input/provenance/artifact invalidation
            # (terminal, history preserved, nothing re-executes), a new
            # environment generation re-executes as a fresh attempt: the
            # registration advances to the new axes (prior attempts
            # preserved), then the normal claim → launch → commit path runs.
            # This is what makes ENV -5 → -10 on the same root relaunch
            # (+1) with the new result instead of reusing stale science.
            try:
                refresh = getattr(store, "refresh_for_new_environment", None)
                if not callable(refresh):
                    raise PersistenceError("store cannot advance environment generation")
                refresh(
                    item.id,
                    work_item_digest=item.semantic_digest,
                    step_semantic_digest=step.step_semantic_digest,
                    environment_digest=environment_digest,
                    producer_provenance=dict(provenance.thaw()),
                    reason=decision.reason,
                )
            except (PersistenceError, CorruptStateError) as exc:
                return (
                    self._durable_synthetic_failure(
                        request,
                        item,
                        code=code.value,
                        message=(
                            f"work item {item.logical_key!r} environment changed but "
                            f"generation refresh failed ({exc}); history preserved"
                        ),
                        retryable=False,
                        details=dict(decision.details.thaw()),
                    ),
                    False,
                )
            if store.claim(item.id, owner=owner):
                attempt = int(store.get_registered(item.id)["current_attempt"])
                result = self._launch(
                    item,
                    replace(context, attempt=attempt),
                    store=store,
                    should_cancel=should_cancel,
                    attempt=attempt,
                )
                # Local commits keep the refreshed registration digest.
                override = self._commit_environment_digest(result)
                store.record_finished(result, environment_digest=override)
                return result, True
            return self._contested_claim(
                request,
                item,
                context,
                store=store,
                owner=owner,
                environment_digest=environment_digest,
                provenance=provenance,
                run_root=run_root,
                should_cancel=should_cancel,
                _claim_retried=_claim_retried,
            )
        return (
            self._durable_synthetic_failure(
                request,
                item,
                code=code.value,
                message=(
                    f"work item {item.logical_key!r} invalidated ({decision.reason}); "
                    "history is preserved and nothing was re-executed"
                ),
                retryable=False,
                details=dict(decision.details.thaw()),
            ),
            False,
        )

    def _contested_claim(
        self,
        request: StepExecutionRequest,
        item: WorkItem,
        context: ItemExecutionContext,
        *,
        store: SqliteWorkItemStore,
        owner: OwnerIdentity,
        environment_digest: str | None,
        provenance: FrozenDict,
        run_root: str,
        should_cancel: Callable[[], bool],
        _claim_retried: bool = False,
    ) -> tuple[WorkItemResult, bool]:
        """Resolve a lost claim race without duplicating native execution."""
        state = store.get_state(item.id)
        if state is StoredWorkItemStatus.RUNNING:
            recorded = store.get_owner(item.id)
            verdict = reconcile_owner(recorded) if recorded is not None else OwnerVerdict.UNCERTAIN
            if verdict is OwnerVerdict.DEFINITELY_DEAD:
                store.mark_interrupted(item.id, reason="rival owner is definitely dead")
                if store.claim(item.id, owner=owner):
                    attempt = int(store.get_registered(item.id)["current_attempt"])
                    result = self._launch(
                        item,
                        replace(context, attempt=attempt),
                        store=store,
                        should_cancel=should_cancel,
                        attempt=attempt,
                    )
                    store.record_finished(
                        result,
                        environment_digest=self._commit_environment_digest(result),
                    )
                    return result, True
        elif not _claim_retried and state in (
            StoredWorkItemStatus.PENDING,
            StoredWorkItemStatus.COMPLETED,
            StoredWorkItemStatus.FAILED,
            StoredWorkItemStatus.INTERRUPTED,
        ):
            return self._durable_run_one(
                request,
                item,
                context,
                store=store,
                run_root=run_root,
                owner=owner,
                environment_digest=environment_digest,
                provenance=provenance,
                should_cancel=should_cancel,
                _claim_retried=True,
            )
        return (
            self._durable_synthetic_failure(
                request,
                item,
                code=ReuseCode.BLOCKED_UNCERTAIN_OWNER.value,
                message=(
                    f"work item {item.logical_key!r} claim lost to a rival owner "
                    "whose death is unproven; duplicate launch forbidden"
                ),
                retryable=True,
                details={"stored_status": state.value if state is not None else None},
            ),
            False,
        )

    @staticmethod
    def _verify_stored_artifacts(
        store: SqliteWorkItemStore, run_root: str, work_item_id: str
    ) -> bool:
        """Return whether every stored artifact of *work_item_id* verifies."""
        stored_result = store.get_result(work_item_id)
        if stored_result is None:
            return False
        try:
            for artifact in stored_result.artifacts:
                verify_artifact(run_root=run_root, ref=artifact)
        except ArtifactIntegrityError:
            return False
        return True

    @staticmethod
    def _rescoped_reuse(item: WorkItem, stored_result: WorkItemResult) -> WorkItemResult:
        """Return *stored_result* addressed to *item* with a reuse diagnostic."""
        diagnostic = Diagnostic(
            code="reuse_hit",
            message="work item reused from durable completed result",
            severity=DiagnosticSeverity.INFO,
            work_item_id=item.id,
            logical_key=item.logical_key,
            details=FrozenDict({"reused_from": stored_result.work_item_id}),
        )
        if stored_result.work_item_id == item.id:
            return WorkItemResult(
                work_item_id=stored_result.work_item_id,
                status=stored_result.status,
                structures=stored_result.structures,
                results=stored_result.results,
                artifacts=stored_result.artifacts,
                diagnostics=tuple(stored_result.diagnostics) + (diagnostic,),
                timing=stored_result.timing,
                error=stored_result.error,
                recovery=stored_result.recovery,
                semantic_digest=stored_result.semantic_digest,
                metadata=stored_result.metadata,
            )
        return WorkItemResult(
            work_item_id=item.id,
            status=stored_result.status,
            structures=stored_result.structures,
            results=stored_result.results,
            artifacts=stored_result.artifacts,
            diagnostics=tuple(stored_result.diagnostics) + (diagnostic,),
            timing=stored_result.timing,
            error=stored_result.error,
            recovery=stored_result.recovery,
            semantic_digest=item.semantic_digest,
            metadata=stored_result.metadata,
        )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    _supervisor: Any = None

    @staticmethod
    def _bind_recovery(
        recovery: RecoveryPolicy | None, adapter: ProgramAdapter | None
    ) -> RecoveryPolicy | None:
        """Substitute a step-bound rescue policy for an unbound instance."""
        if recovery is None or adapter is None:
            return recovery
        if getattr(recovery, "name", "") != "ts_rescue_scan":
            return recovery
        if getattr(recovery, "_adapter", None) is not None:
            return recovery
        from .registry import default_registry

        name = str(getattr(recovery, "name", "ts_rescue_scan"))
        bound: RecoveryPolicy | None = default_registry().recovery_implementation(
            name, adapter=adapter
        )
        return bound

    def _validate_request(self, request: StepExecutionRequest) -> Diagnostic | None:
        step = request.step
        if request.scientific is None:
            return _diagnostic(
                "native_input_error",
                f"step {step.step_id!r} has no scientific definition",
                step_id=step.step_id,
            )
        if request.producer_provenance is None:
            # Native-backed steps derive reuse provenance from the resolved
            # adapter/profile pair, which must then be present.  Pure
            # executors carry explicit provenance instead (no adapter).
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
            diagnostics.extend(_item_error_diagnostics(item, step_id=step.step_id))
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
