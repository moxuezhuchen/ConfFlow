"""Adapters that run the V4 application through ``ExecutionService``.

The single formal V4 application owns step ordering, execution, and output
publication.  This module owns only the application boundary: durable
prepare/launch, lifecycle callbacks, cancellation signalling, and conversion
of the V4 manifest into service artifacts.  No formal path reaches the
legacy engine.
"""

from __future__ import annotations

import errno
import hashlib
import json
import os
import shutil
import stat
import sys
import threading
from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Protocol

from ...artifact_json import write_atomic_json
from ...core.exceptions import StopRequestedError
from ...persistence import arbitration
from ...persistence.fsatomic import fsync_directory
from ..v4_entry import formal_v4_runner as default_workflow_runner
from ..v4_entry import require_v4_document_file
from .errors import ErrorCode, ExecutionServiceError
from .models import (
    Artifact,
    CancelReceipt,
    CancelRequest,
    ExecutableIdentity,
    LaunchReceipt,
    LaunchRequest,
    PrepareRequest,
    RunState,
)
from .ports import IdentityVerifier, WorkflowExecutor
from .service import ExecutionLifecycle, ExecutionService
from .sqlite import SQLiteExecutionRepository
from .state_root import RunPaths, StateRoot
from .v4_artifacts import load_v4_completed_artifacts

#: Control-channel pointer (under ``RunPaths.work``) naming the actual V4
#: run root of a durable run.  It lets a cross-process ``control cancel``
#: reach the same generation arbitration ledger the producer writes.
CONTROL_RUN_ROOT_FILENAME = "run_root"


def _publish_control_run_root(run_paths: RunPaths, work_dir: str) -> None:
    """Durably name the V4 run root for cross-process control commands."""
    target = Path(run_paths.work) / CONTROL_RUN_ROOT_FILENAME
    payload = os.path.abspath(work_dir)
    tmp_path = f"{target}.tmp.{os.getpid()}"
    try:
        with open(tmp_path, "w", encoding="utf-8") as handle:
            os.fchmod(handle.fileno(), 0o600)
            handle.write(payload + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_path, target)
    finally:
        try:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
        except OSError:
            pass
    fsync_directory(str(target.parent))


def _control_run_root_path(state_root: StateRoot, run_id: str) -> Path:
    return state_root.path / "v1" / "runs" / run_id / "work" / CONTROL_RUN_ROOT_FILENAME


def resolve_control_run_root(state_root: StateRoot, run_id: str) -> str | None:
    """Return the run's V4 run root from the control-channel pointer.

    ``None`` means no generation has begun yet (the pointer is absent), so
    there is nothing to arbitrate and the durable service cancel intent plus
    the worker stop beacon own the pre-generation window.  A present but
    unreadable/empty pointer fails closed.
    """
    path = _control_run_root_path(state_root, run_id)
    try:
        text = path.read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        return None
    except OSError as error:
        raise ExecutionServiceError(
            ErrorCode.INTERNAL, f"cannot read control run-root pointer: {error}"
        ) from error
    if not text:
        raise ExecutionServiceError(ErrorCode.INTERNAL, "control run-root pointer is empty")
    return text


try:
    import fcntl as _fcntl
except ImportError:  # pragma: no cover - Windows has no fcntl
    _fcntl = None  # type: ignore[assignment]

__all__ = [
    "ServiceWorkflowExecutor",
    "WorkflowRunSpec",
    "acquire_work_directory_lease",
    "build_workflow_service",
    "open_control_service",
    "run_workflow_through_service",
    "step_record_identity",
]

_EXECUTION_IDENTITY_FILE = ".confflow_execution_identity.json"

#: Formal V4 terminal statuses published in run_result.json (producer contract).
_V4_TERMINAL_STATUSES = frozenset({"completed", "partial", "failed", "cancelled"})

#: Service-event marker recording a scientific partial outcome. The service
#: aggregate has no PARTIAL state; a V4 ``partial`` is committed as service
#: FAILED with this checkpoint event so the partial truth stays visible in
#: the durable event stream while run_result.json keeps ``status=partial``.
_V4_PARTIAL_CHECKPOINT = "v4.status.partial"


def _v4_status_from_value(value: Any) -> str | None:
    """Normalize one status value to a formal V4 terminal status, if known."""
    if value is None:
        return None
    text = str(getattr(value, "value", value)).strip().lower()
    return text if text in _V4_TERMINAL_STATUSES else None


def _resolve_v4_terminal_status(result: Any, work_dir: str) -> str:
    """Resolve the formal V4 terminal status for one finished runner call.

    The durable ``run_result.json`` manifest is the scientific truth when it
    exists; otherwise the runner's ``result["status"]`` is used. An absent
    or unrecognized status fails closed to ``"failed"`` — it is never
    treated as ``"completed"``.
    """
    try:
        raw = (Path(work_dir) / "run_result.json").read_text(encoding="utf-8")
        payload = json.loads(raw)
        if isinstance(payload, dict):
            manifest_status = _v4_status_from_value(payload.get("status"))
            if manifest_status is not None:
                return manifest_status
            if "status" in payload:
                # A manifest that names an unknown status is corrupt science;
                # fail closed instead of guessing completion.
                return "failed"
    except (OSError, ValueError):
        pass
    if isinstance(result, dict):
        result_status = _v4_status_from_value(result.get("status"))
        if result_status is not None:
            return result_status
    # No silent fallback to success: an undeterminable outcome is a failure.
    return "failed"


def _commit_v4_terminal(
    *,
    lifecycle: ExecutionLifecycle,
    service: ExecutionService,
    run_id: str,
    token: str,
    v4_status: str,
    artifacts: Sequence[Artifact],
    work_dir: str,
) -> None:
    """Commit one resolved V4 status to the token-bound service lifecycle.

    Mapping (Definition != Binding != Policy — scientific status is never
    rewritten, only projected onto the service control state):

    - ``completed`` -> ``lifecycle.completed()`` (the only success path);
    - ``failed`` -> ``lifecycle.failed()``;
    - ``cancelled`` -> ``lifecycle.cancelled()``, creating the durable
      cancel intent first when the runner reports cancellation without one;
    - ``partial`` -> ``lifecycle.failed()`` with a ``v4.status.partial``
      checkpoint marker (failure-with-partial-metadata). The scientific
      ``status=partial`` is preserved verbatim in ``run_result.json`` and
      ``ServiceWorkflowExecutor._result``; the service aggregate is FAILED
      so partial work never masquerades as completed.

    The projection must agree with the run root's durable terminal
    arbitration winner: a mismatch fails closed instead of letting the
    service aggregate become an independent winner.
    """
    winner = arbitration.current_terminal_status(work_dir)
    if winner is not None and winner != v4_status:
        raise ExecutionServiceError(
            ErrorCode.INTERNAL,
            f"terminal arbitration mismatch for {run_id}: durable winner is "
            f"{winner!r} but the published status is {v4_status!r}",
        )
    if v4_status == "completed":
        lifecycle.completed(artifacts)
        return
    if v4_status == "failed":
        lifecycle.failed(artifacts)
        return
    if v4_status == "cancelled":
        try:
            lifecycle.cancelled()
            return
        except ExecutionServiceError as error:
            if error.code is not ErrorCode.INVALID_STATE_TRANSITION:
                raise
            # The runner reports scientific cancellation but no durable
            # cancel intent exists yet. Persist the intent, then confirm
            # the stop the runner already observed.
            try:
                service.cancel(run_id)
            except ExecutionServiceError as cancel_error:
                if cancel_error.code is ErrorCode.TERMINAL_RUN:
                    # A terminal completed/failed winner already owns the
                    # aggregate; leave it untouched.
                    return
                raise
            lifecycle.cancelled()
            return
    if v4_status == "partial":
        try:
            lifecycle.checkpoint(_V4_PARTIAL_CHECKPOINT)
        except ExecutionServiceError as error:
            if error.code is not ErrorCode.INVALID_STATE_TRANSITION:
                raise
            # A terminal/cancel winner owns the aggregate; the failed()
            # commit below respects that winner unchanged.
        lifecycle.failed(artifacts)
        return
    lifecycle.failed(artifacts)


def _terminal_artifacts_for_status(work_dir: str, v4_status: str) -> tuple[Artifact, ...]:
    """Project terminal artifacts for one resolved V4 status.

    Only ``completed`` reads the typed COMPLETED manifest projection, which
    fails closed (missing/corrupt/unbound manifest raises
    ``ARTIFACT_INTEGRITY_FAILED`` with no legacy fallback).  Any other
    terminal status carries no completion artifacts: ``failed``/``partial``/
    ``cancelled`` (and the fail-closed ``failed`` for an undeterminable
    outcome) project as an empty set so the service lifecycle commits the
    status itself instead of inventing completion evidence.
    """
    if v4_status != "completed":
        return ()
    return load_v4_completed_artifacts(work_dir)


def step_record_identity(record: Any) -> str:
    """Return the durable step identity of a v1 or v2 state record (PD-8).

    V1 records answer ``name`` (the V2-workflow dirname-bound identity), V2
    records answer ``id`` (the stable step ID). The label never enters a
    durable service checkpoint id.
    """
    identity = getattr(record, "name", None) or getattr(record, "id", None)
    if not identity:
        raise ExecutionServiceError(
            ErrorCode.INTERNAL, "workflow step record carries no durable identity"
        )
    return str(identity)


class WorkflowRunner(Protocol):
    """Subset of the formal V4 runner used by the adapter."""

    def __call__(self, **kwargs: Any) -> dict[str, Any] | None: ...


@dataclass(frozen=True)
class WorkflowRunSpec:
    """Inputs needed to invoke the existing synchronous workflow engine."""

    run_id: str
    input_xyz: tuple[str, ...]
    config_file: str
    work_dir: str
    original_input_files: tuple[str, ...] | None = None
    resume: bool = False
    verbose: bool = False
    pause_beacon_file: str | None = None
    cancel_beacon_file: str | None = None
    # The interactive CLI acquires this before converting any input files so
    # an active attempt protects the whole work-directory mutation boundary.
    work_directory_lease: _WorkDirectoryLease | None = None


class FileIdentityVerifier(IdentityVerifier):
    """Measure one executable without invoking it."""

    def __init__(self, executable: str) -> None:
        self._executable = _resolve_executable(executable)

    @property
    def executable(self) -> str:
        return self._executable

    def measure(self) -> ExecutableIdentity:
        return measure_executable(self._executable)


class ServiceWorkflowExecutor(WorkflowExecutor):
    """Launch the formal V4 application behind service lifecycle tokens."""

    def __init__(self, spec: WorkflowRunSpec, workflow_runner: WorkflowRunner) -> None:
        self._spec = spec
        self._workflow_runner = workflow_runner
        self._service: ExecutionService | None = None
        self._lock = threading.Lock()
        self._threads: dict[str, threading.Thread] = {}
        self._cancelled_tokens: set[str] = set()
        self._finished = threading.Event()
        self._result: dict[str, Any] | None = None
        self._error: BaseException | None = None

    def bind(self, service: ExecutionService) -> None:
        """Bind the service after construction to avoid a circular dependency."""
        self._service = service

    def ensure_launched(self, request: LaunchRequest) -> LaunchReceipt:
        with self._lock:
            if request.token in self._cancelled_tokens:
                return LaunchReceipt(accepted=False, cancelled=True)
            if request.token in self._threads:
                return LaunchReceipt(accepted=True)
            thread = threading.Thread(
                target=self._run,
                args=(request,),
                daemon=True,
                name=f"confflow-run-{request.run_id}",
            )
            self._threads[request.token] = thread
            thread.start()
        return LaunchReceipt(accepted=True)

    def ensure_cancelled(self, request: CancelRequest) -> CancelReceipt:
        # The durable cancellation claim is recorded in the run root's
        # arbitration ledger BEFORE the receipt: a completion claim that
        # follows it loses, and a completion claim that already won makes
        # this cancel lose.  The beacon stays the live stop signal.
        arbitration.record_cancel_intent(self._spec.work_dir, source="service", token=request.token)
        with self._lock:
            self._cancelled_tokens.add(request.launch_token or "")
        beacon = self._spec.cancel_beacon_file
        if beacon:
            Path(beacon).parent.mkdir(parents=True, exist_ok=True)
            Path(beacon).touch()
        return CancelReceipt(confirmed=True)

    def wait(self, timeout: float | None = None) -> None:
        """Wait for the one workflow attempt and re-raise its original error."""
        if not self._finished.wait(timeout):
            raise TimeoutError("ConfFlow workflow did not finish before the timeout")
        if self._error is not None:
            raise self._error

    def _project_arbitrated_winner(self, winner: str, request: LaunchRequest) -> None:
        """Project the run root's terminal winner onto the service aggregate.

        The exception paths use this when the arbitration decision is already
        durable (a cancellation or completion linearized before the runner
        failed/stopped): the service commits the winner instead of inventing
        a competing terminal state of its own.
        """
        service = self._service
        if service is None:  # pragma: no cover - callers guard this
            return
        try:
            artifacts = _terminal_artifacts_for_status(self._spec.work_dir, winner)
        except ExecutionServiceError:
            artifacts = ()
        _commit_v4_terminal(
            lifecycle=ExecutionLifecycle(service, request.run_id, request.token),
            service=service,
            run_id=request.run_id,
            token=request.token,
            v4_status=winner,
            artifacts=artifacts,
            work_dir=self._spec.work_dir,
        )

    def _run(self, request: LaunchRequest) -> None:
        work_lease: _WorkDirectoryLease | None = None
        owns_work_lease = False
        try:
            service = self._service
            if service is None:
                raise RuntimeError("ServiceWorkflowExecutor is not bound to an ExecutionService")
            lifecycle = ExecutionLifecycle(service, request.run_id, request.token)
            with self._lock:
                if request.token in self._cancelled_tokens:
                    try:
                        lifecycle.cancelled()
                    except ExecutionServiceError as error:
                        # A completed/failed terminal callback may have won
                        # the race before this pre-start cancellation callback.
                        if error.code is not ErrorCode.INVALID_STATE_TRANSITION:
                            raise
                    return
            work_lease = self._spec.work_directory_lease
            if work_lease is None:
                work_lease = _WorkDirectoryLease(self._spec.work_dir)
                owns_work_lease = True
                acquired = work_lease.acquire()
            else:
                acquired = True
            if not acquired:
                # Claim the service token before reporting the conflict so the
                # rejected attempt cannot remain durably queued forever.
                lifecycle.started()
                raise ExecutionServiceError(
                    ErrorCode.ALREADY_RUNNING,
                    f"Work directory is already running: {self._spec.work_dir}",
                )
            try:
                lifecycle.started()
            except ExecutionServiceError as error:
                if error.code is not ErrorCode.INVALID_STATE_TRANSITION:
                    raise
                with self._lock:
                    cancelled_before_start = request.token in self._cancelled_tokens
                if not cancelled_before_start:
                    raise
                try:
                    lifecycle.cancelled()
                except ExecutionServiceError as error:
                    # A completed/failed terminal callback may have won the
                    # race before this cancellation callback.
                    if error.code is not ErrorCode.INVALID_STATE_TRANSITION:
                        raise
                return

            def checkpoint_update(record: Any) -> None:
                """Project the engine's persisted step boundary into the service."""
                status = str(getattr(record, "status", ""))
                if status in {"pending", "running"}:
                    return
                checkpoint_id = (
                    f"checkpoint.{step_record_identity(record)}."
                    f"{getattr(record, 'fail_count', 0)}.{status}"
                )
                lifecycle.checkpoint(checkpoint_id)

            runner_kwargs: dict[str, Any] = {
                "input_xyz": list(self._spec.input_xyz),
                "config_file": self._spec.config_file,
                "work_dir": self._spec.work_dir,
                "pause_beacon_file": self._spec.pause_beacon_file,
                # The typed V4 runner owns this supervision seam explicitly:
                # cancel delivery (beacon pre-check plus live should_cancel
                # polling) is real control flow, and the step-status hook
                # carries the durable checkpoint projection for doubles.
                # Service-owned flags (resume/verbose/original inputs) stay
                # on the spec: the runner never read them.
                "cancel_beacon_file": self._spec.cancel_beacon_file,
                "on_step_status_change": checkpoint_update,
            }
            self._result = self._workflow_runner(
                **runner_kwargs,
            )
            # F6: the V4 terminal status owns the service lifecycle. Only
            # status==completed may commit lifecycle.completed(); failed,
            # cancelled and partial each take their explicit branch in
            # _commit_v4_terminal so scientific failure never masquerades
            # as a completed service aggregate.
            v4_status = _resolve_v4_terminal_status(self._result, self._spec.work_dir)
            # Formal V4 runtime: terminal artifacts come only from the typed
            # V4 manifest projection. A missing or corrupt COMPLETED manifest
            # fails closed here; non-completed statuses carry no completion
            # artifacts, and there is no legacy manifest fallback.
            artifacts = _terminal_artifacts_for_status(self._spec.work_dir, v4_status)
            _write_execution_identity(self._spec)
            _commit_v4_terminal(
                lifecycle=lifecycle,
                service=service,
                run_id=request.run_id,
                token=request.token,
                v4_status=v4_status,
                artifacts=artifacts,
                work_dir=self._spec.work_dir,
            )
        except StopRequestedError as error:
            self._error = error
            service = self._service
            if service is not None:
                try:
                    aggregate = service.status(request.run_id)
                    winner = arbitration.current_terminal_status(self._spec.work_dir)
                    if winner is not None:
                        # The run root's arbitration already owns a terminal
                        # outcome (e.g. cancellation or completion linearized
                        # before the stop): project the winner, never invent
                        # a competing one.
                        self._project_arbitrated_winner(winner, request)
                    elif (
                        self._spec.cancel_beacon_file
                        and Path(self._spec.cancel_beacon_file).exists()
                    ):
                        ExecutionLifecycle(service, request.run_id, request.token).cancelled()
                    elif aggregate.state is RunState.RUNNING:
                        ExecutionLifecycle(service, request.run_id, request.token).paused()
                except ExecutionServiceError as error:
                    # A confirmed service cancellation may have already won the
                    # race and moved the aggregate to terminal state.
                    if error.code is not ErrorCode.INVALID_STATE_TRANSITION:
                        self._error = error
        except BaseException as error:  # noqa: BLE001 - preserve runner failures
            self._error = error
            service = self._service
            if service is not None:
                try:
                    try:
                        failed_status = _resolve_v4_terminal_status(error, self._spec.work_dir)
                        failed_artifacts = _terminal_artifacts_for_status(
                            self._spec.work_dir, failed_status
                        )
                    except ExecutionServiceError:
                        # A corrupt manifest must not leave the run without a
                        # terminal state: record the failure with no artifacts
                        # and preserve the original error for wait().
                        failed_artifacts = ()
                    winner = arbitration.current_terminal_status(self._spec.work_dir)
                    if winner is not None and winner != "failed":
                        self._project_arbitrated_winner(winner, request)
                    else:
                        ExecutionLifecycle(service, request.run_id, request.token).failed(
                            failed_artifacts
                        )
                except ExecutionServiceError as lifecycle_error:
                    # A terminal cancellation/other lifecycle winner owns the
                    # aggregate; the original error remains available to wait().
                    if lifecycle_error.code is not ErrorCode.INVALID_STATE_TRANSITION:
                        self._error = lifecycle_error
        finally:
            if work_lease is not None and owns_work_lease:
                work_lease.release()
            self._finished.set()


class _AgentControlExecutor(WorkflowExecutor):
    """Cross-process control adapter for an agent-owned service repository."""

    def __init__(self, state_root: StateRoot) -> None:
        self._state_root = state_root

    def ensure_launched(self, request: LaunchRequest) -> LaunchReceipt:
        """Leave actual launch to the worker; control commands never launch work."""
        return LaunchReceipt(accepted=True)

    def ensure_cancelled(self, request: CancelRequest) -> CancelReceipt:
        """Durably claim cancellation, then signal the worker to stop.

        The run root's arbitration ledger owns the terminal winner: when the
        producer already recorded a non-cancelled terminal, this cancel
        loses and raises without touching the beacon.  The beacon itself is
        only the live stop signal for the worker, never a winner authority.
        """
        run_root = resolve_control_run_root(self._state_root, request.run_id)
        if run_root is not None:
            winner = arbitration.record_cancel_intent(
                run_root, source="control", token=request.token
            )
            if winner is not None and winner != "cancelled":
                raise ExecutionServiceError(
                    ErrorCode.INVALID_STATE_TRANSITION,
                    f"terminal ownership of {request.run_id} belongs to {winner!r}; "
                    "the cancellation did not claim the terminal transition",
                )
        beacon = self._state_root.ensure_run_paths(request.run_id).work / "CANCEL"
        beacon.touch()
        return CancelReceipt(confirmed=True)


class _CurrentProcessIdentity(FileIdentityVerifier):
    """Use the running ConfFlow interpreter as the service launch identity."""

    def __init__(self, executable: str | None = None) -> None:
        super().__init__(sys.executable if executable is None else executable)


_WORK_LOCKS: dict[str, threading.Lock] = {}
_WORK_LOCKS_GUARD = threading.Lock()


class _WorkDirectoryLease:
    """Hold an advisory lock for the lifetime of one workflow attempt.

    The service run ID is content-bound, so editing an input creates a new
    durable record.  A work-directory lease keeps that identity change from
    allowing two live attempts to mutate the same checkpoint and step files.
    POSIX ``flock`` is process-crash safe; the in-process fallback only applies
    on platforms without ``fcntl``.
    """

    def __init__(self, work_dir: str) -> None:
        self._path = Path(work_dir).resolve(strict=False) / ".confflow-work.lock"
        self._fd: int | None = None
        self._local_lock: threading.Lock | None = None

    def acquire(self) -> bool:
        """Try to acquire the work-directory lease without waiting."""
        self._path.parent.mkdir(parents=True, exist_ok=True)
        if _fcntl is not None:
            nofollow = getattr(os, "O_NOFOLLOW", 0)
            fd = os.open(self._path, os.O_RDWR | os.O_CREAT | nofollow, 0o600)
            try:
                metadata = os.fstat(fd)
                if not stat.S_ISREG(metadata.st_mode):
                    raise OSError("work-directory lock must be a regular file")
                getuid = getattr(os, "getuid", None)
                if getuid is not None and metadata.st_uid != getuid():
                    raise OSError("work-directory lock owner does not match the active user")
                if stat.S_IMODE(metadata.st_mode) != 0o600:
                    os.fchmod(fd, 0o600)
                try:
                    _fcntl.flock(fd, _fcntl.LOCK_EX | _fcntl.LOCK_NB)
                except OSError as error:
                    if error.errno in {errno.EACCES, errno.EAGAIN}:
                        return self._close_fd(fd)
                    raise
            except BaseException:
                os.close(fd)
                raise
            self._fd = fd
            return True

        # The durable execution service is POSIX-only today.  Keep direct
        # adapter calls deterministic on other platforms with a process-local
        # lock rather than pretending a stale marker proves ownership.
        key = str(self._path)
        with _WORK_LOCKS_GUARD:
            lock = _WORK_LOCKS.setdefault(key, threading.Lock())
        if not lock.acquire(blocking=False):
            return False
        self._local_lock = lock
        return True

    def _close_fd(self, fd: int) -> bool:
        os.close(fd)
        return False

    def release(self) -> None:
        """Release the lease; repeated release is harmless."""
        fd, self._fd = self._fd, None
        if fd is not None:
            try:
                _fcntl.flock(fd, _fcntl.LOCK_UN)
            finally:
                os.close(fd)
        lock, self._local_lock = self._local_lock, None
        if lock is not None:
            lock.release()


def acquire_work_directory_lease(work_dir: str) -> _WorkDirectoryLease:
    """Acquire the shared work-directory lease before preflight mutations."""
    lease = _WorkDirectoryLease(work_dir)
    if not lease.acquire():
        raise ExecutionServiceError(
            ErrorCode.ALREADY_RUNNING,
            f"Work directory is already running: {work_dir}",
        )
    return lease


def build_workflow_service(
    spec: WorkflowRunSpec,
    *,
    state_root: str | Path,
    workflow_runner: WorkflowRunner = default_workflow_runner,
) -> tuple[ExecutionService, ServiceWorkflowExecutor]:
    """Build one durable service and its formal V4 execution adapter."""
    # Mandatory formal-runtime guard (worker I): the single V4 application is
    # the only formal runtime. A V2/V3 document fails closed with
    # legacy_workflow_not_executable BEFORE every persistent side effect
    # below: _ensure_state_root (mkdir/chmod), ensure_run_paths, and the
    # SQLite repository. The guard reads the config file only.
    require_v4_document_file(spec.config_file)
    root = _ensure_state_root(state_root)
    run_paths = root.ensure_run_paths(spec.run_id)
    # Publish the control-channel pointer before anything else so a
    # cross-process ``control cancel`` can reach this run's generation
    # arbitration ledger from the state root alone.
    _publish_control_run_root(run_paths, spec.work_dir)
    if spec.cancel_beacon_file is None:
        spec = replace(spec, cancel_beacon_file=str(run_paths.work / "CANCEL"))
    repository = SQLiteExecutionRepository(root)
    verifier = _CurrentProcessIdentity()
    executor = ServiceWorkflowExecutor(spec, workflow_runner)
    work_dir = spec.work_dir

    def _terminal_arbiter(_run_id: str) -> str | None:
        """Project the run root's durable terminal winner (single authority)."""
        return arbitration.current_terminal_status(work_dir)

    def _cancel_arbiter(_run_id: str) -> str | None:
        """Claim the run root's cancellation ordering before any DB mutation."""
        return arbitration.record_cancel_intent(work_dir, source="service")

    service = ExecutionService(
        repository=repository,
        executor=executor,
        identity_verifier=verifier,
        terminal_arbiter=_terminal_arbiter,
        cancel_arbiter=_cancel_arbiter,
    )
    executor.bind(service)
    return service, executor


def open_control_service(
    state_root: str | Path,
    *,
    identity_executable: str | None = None,
    terminal_arbiter: Callable[[str], str | None] | None = None,
    cancel_arbiter: Callable[[str], str | None] | None = None,
) -> ExecutionService:
    """Open the same service for a separate agent control command.

    Without explicit arbiters, both resolve the run's control-channel
    run-root pointer (published by :func:`build_workflow_service`), so a
    cross-process ``control cancel`` participates in the same durable
    generation arbitration authority as the producer: the cancel claim is
    recorded before any service-state mutation, and a terminal winner
    already recorded by the producer refuses the cancel.  An absent pointer
    means no generation has begun yet; the cancel then relies on the
    durable service cancel intent plus the worker stop beacon.
    """
    root = _ensure_state_root(state_root)
    resolved_terminal = terminal_arbiter
    if resolved_terminal is None:

        def resolved_terminal(_run_id: str) -> str | None:
            run_root = resolve_control_run_root(root, _run_id)
            if run_root is None:
                return None
            return arbitration.current_terminal_status(run_root)

    resolved_cancel = cancel_arbiter
    if resolved_cancel is None:

        def resolved_cancel(_run_id: str) -> str | None:
            run_root = resolve_control_run_root(root, _run_id)
            if run_root is None:
                return None
            return arbitration.record_cancel_intent(run_root, source="control")

    return ExecutionService(
        repository=SQLiteExecutionRepository(root),
        executor=_AgentControlExecutor(root),
        identity_verifier=_CurrentProcessIdentity(identity_executable),
        terminal_arbiter=resolved_terminal,
        cancel_arbiter=resolved_cancel,
    )


def run_workflow_through_service(
    *,
    input_xyz: Sequence[str],
    config_file: str,
    work_dir: str,
    state_root: str | Path,
    run_id: str,
    resume: bool = False,
    verbose: bool = False,
    pause_beacon_file: str | None = None,
    cancel_beacon_file: str | None = None,
    original_input_files: Sequence[str] | None = None,
    work_directory_lease: _WorkDirectoryLease | None = None,
    workflow_runner: WorkflowRunner = default_workflow_runner,
) -> dict[str, Any] | None:
    """Run the formal V4 application synchronously while all state transitions use the service."""
    # Mandatory formal-runtime guard (worker I): a V2/V3 document fails
    # closed with legacy_workflow_not_executable BEFORE build_workflow_service,
    # because that call creates the state root, run paths and the SQLite
    # repository. The guard reads the config file only — zero side effects.
    require_v4_document_file(config_file)
    spec = WorkflowRunSpec(
        run_id=run_id,
        input_xyz=tuple(input_xyz),
        config_file=config_file,
        work_dir=work_dir,
        original_input_files=None if original_input_files is None else tuple(original_input_files),
        resume=resume,
        verbose=verbose,
        pause_beacon_file=pause_beacon_file,
        cancel_beacon_file=cancel_beacon_file,
        work_directory_lease=work_directory_lease,
    )
    service, executor = build_workflow_service(
        spec,
        state_root=state_root,
        workflow_runner=workflow_runner,
    )
    request = _prepare_request(spec, executor_identity(service))
    snapshot = service.prepare(request)
    if snapshot.state is RunState.FAILED and resume:
        # A successful retry leaves this base record FAILED. Every explicit
        # resume must revalidate artifacts, including completed retries.
        spec, service, executor, snapshot = _prepare_failed_retry(
            spec,
            state_root=state_root,
            workflow_runner=workflow_runner,
            initial_service=service,
            revalidate_completed=True,
        )
        run_id = spec.run_id
    elif snapshot.state is RunState.COMPLETED and resume:
        # ``--resume`` is an explicit strict validation request.  Route it
        # through a fresh controlled record so the engine checks every saved
        # artifact before reporting success; a plain repeated invocation is
        # still an attach-only query below.
        spec, service, executor, snapshot = _prepare_failed_retry(
            spec,
            state_root=state_root,
            workflow_runner=workflow_runner,
            initial_service=service,
            revalidate_completed=True,
        )
        run_id = spec.run_id
    elif (
        work_directory_lease is not None
        and not resume
        and snapshot.state in {RunState.COMPLETED, RunState.FAILED, RunState.CANCELLED}
    ):
        # Each non-resume interactive CLI invocation is an explicit fresh
        # request.  A content-bound historical record is retained for audit,
        # while a new attempt is allowed to rebuild the shared work directory.
        spec, service, executor, snapshot = _prepare_failed_retry(
            spec,
            state_root=state_root,
            workflow_runner=workflow_runner,
            initial_service=service,
            revalidate_completed=True,
            fresh_execution=True,
            retry_resume=False,
        )
        run_id = spec.run_id
    if snapshot.state is RunState.PAUSED:
        if not resume:
            raise ExecutionServiceError(
                ErrorCode.INVALID_STATE_TRANSITION,
                "Paused run requires resume=True",
            )
        snapshot = service.resume(run_id)
    elif snapshot.state is RunState.PREPARED or snapshot.state is RunState.QUEUED:
        snapshot = service.execute(run_id)
    elif snapshot.state in {RunState.RUNNING, RunState.COMPLETED}:
        if snapshot.state is RunState.RUNNING:
            raise ExecutionServiceError(
                ErrorCode.INVALID_STATE_TRANSITION,
                "Run is already running and cannot be attached by this process",
            )
        return _load_completed_stats(service, run_id, work_dir, spec)
    elif snapshot.state in {RunState.FAILED, RunState.CANCELLED}:
        raise ExecutionServiceError(ErrorCode.TERMINAL_RUN, f"Run is terminal: {run_id}")

    try:
        executor.wait()
    except BaseException:
        current = service.status(run_id)
        if current.state is RunState.CANCELLED:
            raise ExecutionServiceError(
                ErrorCode.TERMINAL_RUN,
                f"Run was cancelled: {run_id}",
            ) from None
        raise
    final = service.status(run_id)
    if final.state is RunState.PAUSED:
        raise StopRequestedError("Workflow paused by service lifecycle")
    if final.state is RunState.CANCELLED:
        # Worker exit semantics (D2): completed is the only success exit.
        # Cancelled — whether from a beacon exception or a V4
        # status=cancelled manifest — is a documented non-success terminal
        # (TERMINAL_RUN), agreeing with the control worker's nonzero exit
        # and the CLI's nonzero mapping.
        raise ExecutionServiceError(
            ErrorCode.TERMINAL_RUN,
            f"Workflow was cancelled: {run_id}",
        )
    if final.state is RunState.FAILED:
        # D1/D2: a scientific failed — and a scientific partial, which the
        # service commits as FAILED with a v4.status.partial marker — is a
        # documented non-success terminal. The V4 status is preserved in
        # run_result.json and the error message; it is never reported as
        # completed. Partial callers must read the manifest status.
        v4_status = _resolve_v4_terminal_status(
            executor._result,  # noqa: SLF001 - adapter result is its synchronous facade
            work_dir,
        )
        raise ExecutionServiceError(
            ErrorCode.TERMINAL_RUN,
            f"Workflow ended as {v4_status} (service failed): {run_id}",
        )
    if final.state is not RunState.COMPLETED:
        raise ExecutionServiceError(ErrorCode.INTERNAL, f"Workflow ended in {final.state.value}")
    return executor._result  # noqa: SLF001 - adapter result is its synchronous facade


def _prepare_request(spec: WorkflowRunSpec, identity: ExecutableIdentity) -> PrepareRequest:
    """Build a prepare request from one immutable workflow specification."""
    return PrepareRequest(
        run_id=spec.run_id,
        idempotency_key=spec.run_id,
        request_digest=_request_digest(spec),
        workflow_config_digest=_file_digest(spec.config_file),
        input_manifest_digest=_inputs_digest(spec.input_xyz),
        expected_executable_identity=identity,
    )


def _prepare_failed_retry(
    spec: WorkflowRunSpec,
    *,
    state_root: str | Path,
    workflow_runner: WorkflowRunner,
    initial_service: ExecutionService,
    revalidate_completed: bool = False,
    fresh_execution: bool = False,
    retry_resume: bool = True,
) -> tuple[WorkflowRunSpec, ExecutionService, ServiceWorkflowExecutor, Any]:
    """Create or attach a new durable attempt for a strict local CLI retry.

    The control protocol intentionally keeps terminal states terminal.  The
    local synchronous CLI can still offer ``--resume`` by creating a distinct
    service record while passing ``resume=True`` to the existing workflow
    engine.  Every prior terminal attempt remains queryable in the repository.
    """
    del initial_service  # The candidate services share its durable repository root.
    for retry_number in range(1, 1000):
        retry_id = _failed_retry_run_id(spec.run_id, retry_number)
        retry_spec = replace(spec, run_id=retry_id, resume=retry_resume)
        retry_service, retry_executor = build_workflow_service(
            retry_spec,
            state_root=state_root,
            workflow_runner=workflow_runner,
        )
        retry_request = _prepare_request(retry_spec, executor_identity(retry_service))
        try:
            retry_snapshot = retry_service.prepare(retry_request)
        except ExecutionServiceError as error:
            # A collision with a pre-existing record must not silently become
            # a different request.  The generated IDs are deterministic and
            # only a matching prior attempt may be reused.
            raise error
        if (
            retry_snapshot.state is RunState.FAILED
            or (revalidate_completed and retry_snapshot.state is RunState.COMPLETED)
            or (fresh_execution and retry_snapshot.state is RunState.CANCELLED)
        ):
            continue
        if retry_snapshot.state is RunState.CANCELLED:
            raise ExecutionServiceError(
                ErrorCode.TERMINAL_RUN,
                f"Run is terminal: {retry_id}",
            )
        if retry_snapshot.state is RunState.COMPLETED:
            return retry_spec, retry_service, retry_executor, retry_snapshot
        return retry_spec, retry_service, retry_executor, retry_snapshot
    raise ExecutionServiceError(
        ErrorCode.INTERNAL,
        f"Too many failed resume attempts for run: {spec.run_id}",
    )


def _failed_retry_run_id(run_id: str, retry_number: int) -> str:
    """Return a bounded deterministic ID for a local failed-run retry."""
    suffix = f".resume.{retry_number}"
    return f"{run_id[: 128 - len(suffix)]}{suffix}"


def executor_identity(service: ExecutionService) -> ExecutableIdentity:
    """Return the identity measured by the service's launch verifier."""
    verifier = service._identity_verifier  # noqa: SLF001 - adapter assembly boundary
    return verifier.measure()


def measure_executable(executable: str) -> ExecutableIdentity:
    """Measure realpath, device/inode and SHA-256 for an executable."""
    path = Path(executable)
    resolved = path.resolve(strict=True)
    metadata = resolved.stat()
    digest = hashlib.sha256(resolved.read_bytes()).hexdigest()
    return ExecutableIdentity(
        sha256=digest,
        realpath=str(resolved),
        device_inode=f"{metadata.st_dev}:{metadata.st_ino}",
    )


def _resolve_executable(value: str) -> str:
    candidate = Path(value)
    if not candidate.is_absolute():
        found = shutil.which(value)
        if found is None:
            raise ExecutionServiceError(
                ErrorCode.EXECUTABLE_IDENTITY_MISMATCH, f"Executable not found: {value}"
            )
        candidate = Path(found)
    return str(candidate.resolve(strict=True))


def _ensure_state_root(value: str | Path) -> StateRoot:
    root = Path(value).expanduser()
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(root, 0o700)
    return StateRoot.resolve(root)


def _file_digest(path: str) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _file_content_binding(path: str) -> dict[str, str | int]:
    """Return a path-and-content descriptor for request identity binding."""
    absolute = os.path.abspath(path)
    digest = hashlib.sha256()
    size = 0
    with open(absolute, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
            size += len(chunk)
    return {"path": absolute, "size": size, "sha256": digest.hexdigest()}


def _inputs_digest(paths: Sequence[str]) -> str:
    encoded = json.dumps(
        [_file_content_binding(path) for path in paths],
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _request_digest(spec: WorkflowRunSpec) -> str:
    encoded = json.dumps(
        {
            "run_id": spec.run_id,
            "input_xyz": [_file_content_binding(path) for path in spec.input_xyz],
            "original_input_files": (
                None
                if spec.original_input_files is None
                else [_file_content_binding(path) for path in spec.original_input_files]
            ),
            "config": _file_content_binding(spec.config_file),
            "work_dir": os.path.abspath(spec.work_dir),
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    return hashlib.sha256(encoded).hexdigest()


def _load_completed_stats(
    service: ExecutionService,
    run_id: str,
    work_dir: str,
    spec: WorkflowRunSpec,
) -> dict[str, Any] | None:
    """Attach to a completed formal V4 run via its durable manifest."""
    # Formal V4 runtime: the durable truth of a completed run is
    # run_result.json published by the single V4 application. There is no
    # legacy fallback: a missing or corrupt manifest fails closed with
    # ARTIFACT_INTEGRITY_FAILED, and a non-completed manifest behind a
    # completed aggregate is corrupt (pre-fix masquerade, F6) and fails
    # closed instead of attaching as success.
    v4_manifest = Path(work_dir) / "run_result.json"
    if not v4_manifest.is_file():
        raise ExecutionServiceError(
            ErrorCode.ARTIFACT_INTEGRITY_FAILED,
            f"Completed V4 run is missing its manifest: {v4_manifest}",
        )
    try:
        payload = json.loads(v4_manifest.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ExecutionServiceError(
            ErrorCode.ARTIFACT_INTEGRITY_FAILED,
            f"Completed V4 run manifest is invalid: {v4_manifest}",
        ) from error
    if not isinstance(payload, dict):
        raise ExecutionServiceError(
            ErrorCode.ARTIFACT_INTEGRITY_FAILED,
            f"Completed V4 run manifest must be an object: {v4_manifest}",
        )
    manifest_status = _v4_status_from_value(payload.get("status"))
    if manifest_status != "completed":
        raise ExecutionServiceError(
            ErrorCode.ARTIFACT_INTEGRITY_FAILED,
            f"Completed run manifest reports {payload.get('status')!r}: {v4_manifest}",
        )

    identity_path = Path(work_dir) / _EXECUTION_IDENTITY_FILE
    if identity_path.exists():
        try:
            identity = json.loads(identity_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise ExecutionServiceError(
                ErrorCode.ARTIFACT_INTEGRITY_FAILED,
                f"Completed run identity marker is invalid: {identity_path}",
            ) from error
        if not isinstance(identity, dict) or identity.get("request_digest") != _request_digest(
            spec
        ):
            raise ExecutionServiceError(
                ErrorCode.ARTIFACT_INTEGRITY_FAILED,
                f"Completed run belongs to a different workflow request: {run_id}",
            )

    # The durable service projection supplies digest/size checks against
    # the terminal artifact record when it is available.
    artifacts_method = getattr(service, "artifacts", None)
    if artifacts_method is None:
        return payload
    manifest = artifacts_method(run_id)
    for artifact in manifest.artifacts:
        candidate = Path(work_dir) / artifact.path
        if (
            not candidate.is_file()
            or candidate.stat().st_size != artifact.size
            or _file_digest(str(candidate)) != artifact.sha256
        ):
            raise ExecutionServiceError(
                ErrorCode.ARTIFACT_INTEGRITY_FAILED,
                f"Completed run artifact is missing or changed: {artifact.path}",
            )
    return payload


def _write_execution_identity(spec: WorkflowRunSpec) -> None:
    """Publish the request identity only after the workflow produced its outputs."""
    write_atomic_json(
        Path(spec.work_dir) / _EXECUTION_IDENTITY_FILE,
        {
            "run_id": spec.run_id,
            "request_digest": _request_digest(spec),
        },
    )
