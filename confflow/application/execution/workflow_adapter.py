"""Adapters that run the existing workflow engine through ``ExecutionService``.

The workflow engine remains responsible for step ordering, calc execution and
its existing output files.  This module owns only the application boundary:
durable prepare/launch, lifecycle callbacks, cancellation signalling and
conversion of the producer manifest into service artifacts.
"""

from __future__ import annotations

import hashlib
import inspect
import json
import os
import shutil
import sys
import threading
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

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

__all__ = [
    "ServiceWorkflowExecutor",
    "WorkflowRunSpec",
    "build_workflow_service",
    "open_control_service",
    "run_workflow_through_service",
]


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
    step_started_callback: Callable[[str, str, str], None] | None = None


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
        beacon = self._spec.pause_beacon_file
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
        try:
            service = self._service
            if service is None:
                raise RuntimeError("ServiceWorkflowExecutor is not bound to an ExecutionService")
            with self._lock:
                if request.token in self._cancelled_tokens:
                    return
            lifecycle = ExecutionLifecycle(service, request.run_id, request.token)
            lifecycle.started()

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
            if "on_step_status_change" in parameters or any(
                parameter.kind is inspect.Parameter.VAR_KEYWORD for parameter in parameters.values()
            ):
                runner_kwargs["on_step_status_change"] = checkpoint_update
            self._result = self._workflow_runner(
                **runner_kwargs,
            )
            lifecycle.completed(_load_artifacts(self._spec.work_dir))
        except StopRequestedError as error:
            self._error = error
            service = self._service
            if service is not None:
                try:
                    aggregate = service.status(request.run_id)
                    if aggregate.state is RunState.RUNNING:
                        ExecutionLifecycle(service, request.run_id, request.token).paused()
                except ExecutionServiceError:
                    # A confirmed service cancellation may have already won the
                    # race and moved the aggregate to terminal state.
                    pass
        except BaseException as error:  # noqa: BLE001 - preserve runner failures
            self._error = error
            service = self._service
            if service is not None:
                try:
                    ExecutionLifecycle(service, request.run_id, request.token).failed(
                        _load_artifacts(self._spec.work_dir)
                    )
                except ExecutionServiceError:
                    # A terminal cancellation/other lifecycle winner owns the
                    # aggregate; the original error remains available to wait().
                    pass
        finally:
            self._finished.set()


class _AgentControlExecutor(WorkflowExecutor):
    """Cross-process control adapter for an agent-owned service repository."""

    def __init__(self, state_root: StateRoot) -> None:
        self._runs_root = state_root.path.parent

    def ensure_launched(self, request: LaunchRequest) -> LaunchReceipt:
        """Leave actual launch to the worker; control commands never launch work."""
        return LaunchReceipt(accepted=True)

    def ensure_cancelled(self, request: CancelRequest) -> CancelReceipt:
        """Signal the worker, while the service owns the terminal transition."""
        run_dir = self._runs_root / f"run_{request.run_id}"
        beacon = run_dir / "PAUSE"
        beacon.parent.mkdir(parents=True, exist_ok=True)
        beacon.touch()
        return CancelReceipt(confirmed=True)


class _CurrentProcessIdentity(FileIdentityVerifier):
    """Use the running ConfFlow interpreter as the service launch identity."""

    def __init__(self, executable: str | None = None) -> None:
        super().__init__(sys.executable if executable is None else executable)


def build_workflow_service(
    spec: WorkflowRunSpec,
    *,
    state_root: str | Path,
    workflow_runner: WorkflowRunner = default_workflow_runner,
) -> tuple[ExecutionService, ServiceWorkflowExecutor]:
    """Build one durable service and its legacy workflow execution adapter."""
    root = _ensure_state_root(state_root)
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
    original_input_files: Sequence[str] | None = None,
    step_started_callback: Callable[[str, str, str], None] | None = None,
    workflow_runner: WorkflowRunner = default_workflow_runner,
) -> dict[str, Any] | None:
    """Run the legacy engine synchronously while all state transitions use the service."""
    spec = WorkflowRunSpec(
        run_id=run_id,
        input_xyz=tuple(input_xyz),
        config_file=config_file,
        work_dir=work_dir,
        original_input_files=None if original_input_files is None else tuple(original_input_files),
        resume=resume,
        verbose=verbose,
        pause_beacon_file=pause_beacon_file,
        step_started_callback=step_started_callback,
    )
    service, executor = build_workflow_service(
        spec,
        state_root=state_root,
        workflow_runner=workflow_runner,
    )
    identity = executor_identity(service)
    request = PrepareRequest(
        run_id=run_id,
        idempotency_key=run_id,
        request_digest=_request_digest(spec),
        workflow_config_digest=_file_digest(config_file),
        input_manifest_digest=_inputs_digest(input_xyz),
        expected_executable_identity=identity,
    )
    snapshot = service.prepare(request)
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
        return _load_stats(work_dir)
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


def _inputs_digest(paths: Sequence[str]) -> str:
    digest = hashlib.sha256()
    for path in paths:
        digest.update(Path(path).read_bytes())
    return digest.hexdigest()


def _request_digest(spec: WorkflowRunSpec) -> str:
    encoded = json.dumps(
        {
            "run_id": spec.run_id,
            "input_xyz": list(spec.input_xyz),
            "config_file": spec.config_file,
            "work_dir": spec.work_dir,
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
