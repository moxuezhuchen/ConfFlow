"""Adapters that run the existing workflow engine through ``ExecutionService``.

The workflow engine remains responsible for step ordering, calc execution and
its existing output files.  This module owns only the application boundary:
durable prepare/launch, lifecycle callbacks, cancellation signalling and
conversion of the producer manifest into service artifacts.
"""

from __future__ import annotations

import errno
import hashlib
import inspect
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
from ...config.canonical import require_executable_workflow_file
from ...contract import OUTPUT_MANIFEST_SCHEMA
from ...core.exceptions import StopRequestedError
from ...workflow.engine import run_workflow as default_workflow_runner
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
from .state_root import StateRoot

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
]

_EXECUTION_IDENTITY_FILE = ".confflow_execution_identity.json"


class WorkflowRunner(Protocol):
    """Subset of the legacy engine used by the adapter."""

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
    step_started_callback: Callable[[str, str, str], None] | None = None
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
    """Launch the unchanged workflow engine behind service lifecycle tokens."""

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
                    f"checkpoint.{getattr(record, 'name', 'step')}."
                    f"{getattr(record, 'fail_count', 0)}.{status}"
                )
                lifecycle.checkpoint(checkpoint_id)

            runner_kwargs: dict[str, Any] = {
                "input_xyz": list(self._spec.input_xyz),
                "config_file": self._spec.config_file,
                "work_dir": self._spec.work_dir,
                "original_input_files": (
                    None
                    if self._spec.original_input_files is None
                    else list(self._spec.original_input_files)
                ),
                "resume": self._spec.resume,
                "verbose": self._spec.verbose,
                "pause_beacon_file": self._spec.pause_beacon_file,
                "step_started_callback": self._spec.step_started_callback,
            }
            parameters = inspect.signature(self._workflow_runner).parameters
            accepts_keywords = any(
                parameter.kind is inspect.Parameter.VAR_KEYWORD for parameter in parameters.values()
            )
            if "cancel_beacon_file" in parameters or accepts_keywords:
                runner_kwargs["cancel_beacon_file"] = self._spec.cancel_beacon_file
            if "on_step_status_change" in parameters or any(
                parameter.kind is inspect.Parameter.VAR_KEYWORD for parameter in parameters.values()
            ):
                runner_kwargs["on_step_status_change"] = checkpoint_update
            self._result = self._workflow_runner(
                **runner_kwargs,
            )
            artifacts = _load_artifacts(self._spec.work_dir)
            _write_execution_identity(self._spec)
            lifecycle.completed(artifacts)
        except StopRequestedError as error:
            self._error = error
            service = self._service
            if service is not None:
                try:
                    aggregate = service.status(request.run_id)
                    if (
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
                    ExecutionLifecycle(service, request.run_id, request.token).failed(
                        _load_artifacts(self._spec.work_dir)
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
        """Signal the worker; its token-bound lifecycle owns the terminal transition."""
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
    """Build one durable service and its legacy workflow execution adapter."""
    # Mandatory execution-capability guard (R4 review fix): this is the lowest
    # shared service boundary, so EVERY caller — run_workflow_through_service,
    # _prepare_failed_retry, the control worker via run_worker_attempt, and any
    # direct API user — passes through it. The gate reads the config file only
    # and must precede every persistent side effect below: _ensure_state_root
    # (mkdir/chmod), ensure_run_paths, and the SQLite repository.
    require_executable_workflow_file(spec.config_file)
    root = _ensure_state_root(state_root)
    if spec.cancel_beacon_file is None:
        run_paths = root.ensure_run_paths(spec.run_id)
        spec = replace(spec, cancel_beacon_file=str(run_paths.work / "CANCEL"))
    repository = SQLiteExecutionRepository(root)
    verifier = _CurrentProcessIdentity()
    executor = ServiceWorkflowExecutor(spec, workflow_runner)
    service = ExecutionService(
        repository=repository,
        executor=executor,
        identity_verifier=verifier,
    )
    executor.bind(service)
    return service, executor


def open_control_service(
    state_root: str | Path, *, identity_executable: str | None = None
) -> ExecutionService:
    """Open the same service for a separate agent control command."""
    root = _ensure_state_root(state_root)
    return ExecutionService(
        repository=SQLiteExecutionRepository(root),
        executor=_AgentControlExecutor(root),
        identity_verifier=_CurrentProcessIdentity(identity_executable),
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
    step_started_callback: Callable[[str, str, str], None] | None = None,
    work_directory_lease: _WorkDirectoryLease | None = None,
    workflow_runner: WorkflowRunner = default_workflow_runner,
) -> dict[str, Any] | None:
    """Run the legacy engine synchronously while all state transitions use the service."""
    # Mandatory execution-capability guard (R3.5): a V3 document must be
    # rejected BEFORE build_workflow_service, because that call creates the
    # state root, run paths and the SQLite repository. The guard reads the
    # config file only — zero side effects.
    require_executable_workflow_file(config_file)
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
        step_started_callback=step_started_callback,
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


def _load_artifacts(work_dir: str) -> tuple[Artifact, ...]:
    path = Path(work_dir) / "output_manifest.json"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return ()
    if not isinstance(payload, dict) or payload.get("content_schema") != OUTPUT_MANIFEST_SCHEMA:
        return ()
    terminals = payload.get("terminals")
    if not isinstance(terminals, dict):
        return ()
    root = Path(work_dir).resolve()
    artifacts: list[Artifact] = []
    for terminal, values in terminals.items():
        if not isinstance(terminal, str) or not isinstance(values, list):
            continue
        for value in values:
            if not isinstance(value, str):
                continue
            candidate = (root / value).resolve()
            try:
                relative = candidate.relative_to(root).as_posix()
            except ValueError:
                continue
            if not candidate.is_file():
                continue
            artifacts.append(
                Artifact(
                    terminal=terminal,
                    path=relative,
                    sha256=_file_digest(str(candidate)),
                    size=candidate.stat().st_size,
                    content_schema=OUTPUT_MANIFEST_SCHEMA,
                )
            )
    return tuple(artifacts)


def _load_stats(work_dir: str) -> dict[str, Any] | None:
    path = Path(work_dir) / "workflow_stats.json"
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def _load_completed_stats(
    service: ExecutionService,
    run_id: str,
    work_dir: str,
    spec: WorkflowRunSpec,
) -> dict[str, Any] | None:
    """Attach to a completed run only while its required files still exist."""
    stats = _load_stats(work_dir)
    if stats is None:
        raise ExecutionServiceError(
            ErrorCode.ARTIFACT_INTEGRITY_FAILED,
            f"Completed run is missing workflow stats: {run_id}",
        )

    declared_outputs = stats.get("final_outputs")
    if not isinstance(declared_outputs, list):
        declared_outputs = [stats.get("final_output")]
    for output in declared_outputs:
        if not isinstance(output, str):
            continue
        candidate = Path(output)
        if not candidate.is_absolute():
            candidate = Path(work_dir) / candidate
        if not candidate.is_file() or candidate.stat().st_size <= 0:
            raise ExecutionServiceError(
                ErrorCode.ARTIFACT_INTEGRITY_FAILED,
                f"Completed run output is missing or empty: {output}",
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

    state_path = Path(work_dir) / ".workflow_state.json"
    if state_path.exists():
        try:
            state_payload = json.loads(state_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise ExecutionServiceError(
                ErrorCode.ARTIFACT_INTEGRITY_FAILED,
                f"Completed run workflow state is invalid: {state_path}",
            ) from error
        if not isinstance(state_payload, dict) or state_payload.get("final_status") != "completed":
            raise ExecutionServiceError(
                ErrorCode.ARTIFACT_INTEGRITY_FAILED,
                f"Completed run workflow state is not complete: {state_path}",
            )

    # Test doubles and older adapters may not expose the terminal artifact
    # projection.  The workflow stats checks above remain useful there; the
    # durable service projection supplies stronger digest/size checks when it
    # is available.
    artifacts_method = getattr(service, "artifacts", None)
    if artifacts_method is None:
        return stats
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
    return stats


def _write_execution_identity(spec: WorkflowRunSpec) -> None:
    """Publish the request identity only after the workflow produced its outputs."""
    write_atomic_json(
        Path(spec.work_dir) / _EXECUTION_IDENTITY_FILE,
        {
            "run_id": spec.run_id,
            "request_digest": _request_digest(spec),
        },
    )
