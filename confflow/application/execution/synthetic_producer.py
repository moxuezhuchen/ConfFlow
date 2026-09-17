"""Explicit opt-in synthetic producer lifecycle fixture (default off).

This module provides a non-computing ``WorkflowExecutor`` that consumes the
formal ``ExecutionService`` launch intent and drives every durable fact through
the official token-bound ``ExecutionLifecycle`` surface:

    prepared -> queued -> running -> (checkpointed) -> terminal

The fixture exists only for launcher-path control acceptance and is never
wired into production defaults: ``open_control_service`` and the regular
``confflow control execute`` path keep using the real control adapter.
Callers must explicitly build the fixture with ``open_synthetic_service``,
drive one existing queued intent with ``synthetic_agent_entry``, or construct
``SyntheticProducerExecutor`` directly.

Payload is restricted to one fixed, small, built-in text artifact.  No shell
command, external executable, user content, or caller-supplied path is ever
accepted.  The fixture never writes the producer SQLite repository and never
invokes the workflow engine; only the service's CAS mutations produce events,
revisions, cursors, checkpoints and the validated artifact manifest.

Single-worker arbitration is a per-token kernel lease (``flock`` on POSIX):
the lease file ``run_<run_id>/synthetic.claim.<token>`` is opened exclusively
and the advisory lock is held from the claim until ``started()`` is durably
committed.  A competing executor or process that cannot acquire the lease
attaches without starting a worker, so at most one worker may ever run per
token.  A crashed holder releases the lease automatically when its process
dies; a later executor may then take over only if the aggregate is still
queued.  On platforms without ``flock``, a claim marker that already exists
attaches only and is never overtaken.
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
import threading
import time
from pathlib import Path

try:
    import fcntl as _fcntl
except ImportError:  # pragma: no cover - non-POSIX platforms
    _fcntl = None  # type: ignore[assignment]

from .errors import ErrorCode, ExecutionServiceError
from .models import (
    TERMINAL_STATES,
    Artifact,
    CancelReceipt,
    CancelRequest,
    LaunchReceipt,
    LaunchRequest,
    RunSnapshot,
    RunState,
)
from .ports import WorkflowExecutor
from .service import ExecutionLifecycle, ExecutionService
from .sqlite import SQLiteExecutionRepository
from .state_root import StateRoot
from .workflow_adapter import FileIdentityVerifier

__all__ = [
    "SYNTHETIC_ARTIFACT",
    "SYNTHETIC_ARTIFACT_CONTENT",
    "SYNTHETIC_ARTIFACT_PATH",
    "SYNTHETIC_ARTIFACT_SCHEMA",
    "SYNTHETIC_ARTIFACT_TERMINAL",
    "SYNTHETIC_CHECKPOINT_ID",
    "SyntheticProducerExecutor",
    "open_synthetic_service",
    "synthetic_agent_entry",
]

SYNTHETIC_ARTIFACT_CONTENT = "ConfFlow synthetic producer fixture artifact\n"
SYNTHETIC_ARTIFACT_TERMINAL = "synthetic"
SYNTHETIC_ARTIFACT_PATH = "synthetic/artifact.txt"
SYNTHETIC_ARTIFACT_SCHEMA = "confflow.synthetic.v1"
SYNTHETIC_CHECKPOINT_ID = "fixture.ready"

SYNTHETIC_ARTIFACT = Artifact(
    terminal=SYNTHETIC_ARTIFACT_TERMINAL,
    path=SYNTHETIC_ARTIFACT_PATH,
    sha256=hashlib.sha256(SYNTHETIC_ARTIFACT_CONTENT.encode("utf-8")).hexdigest(),
    size=len(SYNTHETIC_ARTIFACT_CONTENT),
    content_schema=SYNTHETIC_ARTIFACT_SCHEMA,
)


class SyntheticProducerExecutor(WorkflowExecutor):
    """Token-arbitrated non-computing producer behind service lifecycle tokens.

    Exactly one worker thread ever runs per launch token: the per-token kernel
    lease (``flock``, or an exclusive claim marker without it) is held from the
    claim until ``started()`` commits, and any executor that cannot acquire the
    lease attaches without starting a worker.  Repeated or concurrent
    ``ensure_launched`` calls attach to the existing attempt and never start
    another producer.  A confirmed cancellation tombstones the token and
    signals the worker to back off; only that token-bound worker confirms the
    terminal transition after it has stopped producing artifacts.
    """

    def __init__(self, state_root: StateRoot) -> None:
        self._runs_root = state_root.path.parent
        self._owner = f"{os.getpid()}.{id(self):x}"
        self._service: ExecutionService | None = None
        self._lock = threading.Lock()
        self._threads: dict[str, threading.Thread] = {}
        self._tombstones: set[str] = set()
        self._errors: dict[str, str] = {}
        self._lease_fds: dict[str, int] = {}

    def bind(self, service: ExecutionService) -> None:
        """Bind the service after construction to avoid a circular dependency."""
        self._service = service

    @property
    def worker_count(self) -> int:
        """Return the number of producer workers ever registered for tokens."""
        with self._lock:
            return len(self._threads)

    @property
    def worker_errors(self) -> dict[str, str]:
        """Return per-token worker failures recorded by this executor instance."""
        with self._lock:
            return dict(self._errors)

    def ensure_launched(self, request: LaunchRequest) -> LaunchReceipt:
        """Consume one launch token idempotently and attach without duplication.

        Exactly one worker may start per token.  A live lease holder means
        another executor or process is driving the token: this call attaches
        and returns immediately.  Only a lease acquirer may start a worker, and
        only when the durable aggregate is still queued - a running or terminal
        run is always attach-only.
        """
        with self._lock:
            if request.token in self._tombstones or self._cancelled_marker(request.run_id):
                return LaunchReceipt(accepted=False, cancelled=True)
        service = self._service
        if service is None:
            return LaunchReceipt(accepted=False)
        try:
            snapshot = service.validate_launch_request(request)
        except ExecutionServiceError:
            return LaunchReceipt(accepted=False)
        if snapshot.state is not RunState.QUEUED:
            return LaunchReceipt(accepted=True)
        with self._lock:
            if request.token in self._threads:
                return LaunchReceipt(accepted=True)
        fd = self._acquire_lease(request)
        if fd < 0:
            # Another live owner holds the per-token lease: attach only.
            return LaunchReceipt(accepted=True)
        with self._lock:
            self._lease_fds[request.token] = fd
        try:
            with self._lock:
                if request.token in self._tombstones or self._cancelled_marker(request.run_id):
                    attach = LaunchReceipt(accepted=False, cancelled=True)
                elif request.token in self._threads:
                    attach = LaunchReceipt(accepted=True)
                else:
                    try:
                        snapshot = service.validate_launch_request(request)
                    except ExecutionServiceError:
                        attach = LaunchReceipt(accepted=False)
                    else:
                        if snapshot.state is not RunState.QUEUED:
                            # Durable facts (running/terminal) already exist: attach.
                            attach = LaunchReceipt(accepted=True)
                        else:
                            self._start_worker(request)
                            return LaunchReceipt(accepted=True)
        except BaseException:
            self._release_lease(request.token)
            raise
        self._release_lease(request.token)
        return attach

    def ensure_cancelled(self, request: CancelRequest) -> CancelReceipt:
        """Tombstone the bound launch token and signal the worker to back off."""
        with self._lock:
            self._tombstones.add(request.launch_token or "")
        run_dir = self._runs_root / f"run_{request.run_id}"
        run_dir.mkdir(parents=True, exist_ok=True)
        (run_dir / "synthetic.cancelled").touch()
        return CancelReceipt(confirmed=True)

    def _acquire_lease(self, request: LaunchRequest) -> int:
        """Acquire the exclusive per-token lease; return an open fd or -1.

        On POSIX the advisory ``flock`` is held until the worker commits
        ``started()`` and the kernel releases it automatically if the owning
        process dies.  Without ``flock`` the claim marker is created
        exclusively and an existing marker attaches only.
        """
        run_dir = self._runs_root / f"run_{request.run_id}"
        run_dir.mkdir(parents=True, exist_ok=True)
        lease_path = run_dir / f"synthetic.claim.{request.token}"
        payload = json.dumps(
            {"v": 2, "owner": self._owner, "run_id": request.run_id, "token": request.token},
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        if _fcntl is None:
            try:
                fd = os.open(lease_path, os.O_RDWR | os.O_CREAT | os.O_EXCL, 0o600)
            except FileExistsError:
                return -1
        else:
            fd = os.open(lease_path, os.O_RDWR | os.O_CREAT, 0o600)
            try:
                _fcntl.flock(fd, _fcntl.LOCK_EX | _fcntl.LOCK_NB)
            except OSError:
                os.close(fd)
                return -1
        os.ftruncate(fd, 0)
        os.write(fd, payload)
        return fd

    def _release_lease(self, token: str) -> None:
        """Release the per-token lease fd; idempotent and crash-safe."""
        with self._lock:
            fd = self._lease_fds.pop(token, -1)
        if fd < 0:
            return
        if _fcntl is not None:
            try:
                _fcntl.flock(fd, _fcntl.LOCK_UN)
            except OSError:
                pass
        try:
            os.close(fd)
        except OSError:
            pass

    def _cancelled_marker(self, run_id: str) -> bool:
        return (self._runs_root / f"run_{run_id}" / "synthetic.cancelled").exists()

    def _start_worker(self, request: LaunchRequest) -> None:
        thread = threading.Thread(
            target=self._drive,
            args=(request,),
            daemon=True,
            name=f"confflow-fixture-{request.run_id}",
        )
        self._threads[request.token] = thread
        thread.start()

    def _drive(self, request: LaunchRequest) -> None:
        completed = False
        try:
            service = self._service
            if service is None:
                raise RuntimeError("SyntheticProducerExecutor is not bound to an ExecutionService")
            lifecycle = ExecutionLifecycle(service, request.run_id, request.token)
            if self._is_cancelled(request):
                try:
                    lifecycle.cancelled()
                except ExecutionServiceError as error:
                    # A completed/failed terminal callback may have won the
                    # race before this pre-start cancellation callback.
                    if error.code is not ErrorCode.INVALID_STATE_TRANSITION:
                        raise
                return
            try:
                lifecycle.started()
            except ExecutionServiceError as error:
                if error.code is not ErrorCode.INVALID_STATE_TRANSITION:
                    raise
                if not self._is_cancelled(request):
                    raise
                try:
                    lifecycle.cancelled()
                except ExecutionServiceError as cancel_error:
                    # A completed/failed terminal callback may have won the
                    # race before this cancellation callback.
                    if cancel_error.code is not ErrorCode.INVALID_STATE_TRANSITION:
                        raise
                return
            # started() is durably committed (the aggregate is RUNNING), so any
            # later lease acquirer attaches instead of starting another worker.
            self._release_lease(request.token)
            if self._is_cancelled(request):
                lifecycle.cancelled()
                return
            run_dir = self._runs_root / f"run_{request.run_id}"
            artifact_dir = run_dir / "synthetic"
            artifact_dir.mkdir(parents=True, exist_ok=True)
            (artifact_dir / "artifact.txt").write_text(SYNTHETIC_ARTIFACT_CONTENT, encoding="utf-8")
            lifecycle.checkpoint(SYNTHETIC_CHECKPOINT_ID)
            self._before_completed(request)
            if self._is_cancelled(request):
                lifecycle.cancelled()
                return
            artifact = self._verified_artifact(request)
            if self._is_cancelled(request):
                lifecycle.cancelled()
                return
            lifecycle.completed((artifact,))
            completed = True
        except ExecutionServiceError as error:
            self._record_error(request, error)
            self._fail(request)
        except BaseException as error:  # noqa: BLE001 - preserve worker failures
            self._record_error(request, error)
            self._fail(request)
        finally:
            if not completed:
                self._cleanup_fixed_artifact(request)
            self._release_lease(request.token)

    def _fail(self, request: LaunchRequest) -> None:
        """Commit a terminal failure without unverified fixture artifact metadata."""
        service = self._service
        if service is None:
            return
        try:
            ExecutionLifecycle(service, request.run_id, request.token).failed()
        except ExecutionServiceError:
            pass

    def _before_completed(self, request: LaunchRequest) -> None:
        """Provide a no-op hook for deterministic lifecycle-race tests."""

    def _artifact_path(self, run_id: str) -> Path:
        """Return the one fixed fixture path; callers cannot supply a path."""
        return self._runs_root / f"run_{run_id}" / SYNTHETIC_ARTIFACT_PATH

    def _verified_artifact(self, request: LaunchRequest) -> Artifact:
        """Read the fixed file and build metadata only after all facts match."""
        path = self._artifact_path(request.run_id)
        if path.is_symlink() or not path.is_file():
            raise RuntimeError("Synthetic artifact is missing or not a regular file")
        try:
            content = path.read_bytes()
        except OSError as error:
            raise RuntimeError(f"Synthetic artifact cannot be read: {error}") from error
        digest = hashlib.sha256(content).hexdigest()
        size = len(content)
        expected = SYNTHETIC_ARTIFACT_CONTENT.encode("utf-8")
        if content != expected:
            raise RuntimeError("Synthetic artifact content does not match the built-in payload")
        if size != SYNTHETIC_ARTIFACT.size:
            raise RuntimeError("Synthetic artifact size does not match the built-in payload")
        if digest != SYNTHETIC_ARTIFACT.sha256:
            raise RuntimeError("Synthetic artifact digest does not match the built-in payload")
        return Artifact(
            terminal=SYNTHETIC_ARTIFACT_TERMINAL,
            path=SYNTHETIC_ARTIFACT_PATH,
            sha256=digest,
            size=size,
            content_schema=SYNTHETIC_ARTIFACT_SCHEMA,
        )

    def _cleanup_fixed_artifact(self, request: LaunchRequest) -> None:
        """Remove only the fixed fixture file after a non-completed attempt."""
        try:
            self._artifact_path(request.run_id).unlink(missing_ok=True)
        except OSError as error:
            self._record_error(request, error)

    def _is_cancelled(self, request: LaunchRequest) -> bool:
        if request.token in self._tombstones:
            return True
        return self._cancelled_marker(request.run_id)

    def _record_error(self, request: LaunchRequest, error: BaseException) -> None:
        with self._lock:
            self._errors[request.token] = f"{type(error).__name__}: {error}"


def open_synthetic_service(
    state_root: str | Path, *, identity_executable: str | None = None
) -> ExecutionService:
    """Open the explicit opt-in synthetic service for one state root.

    This is the only supported service entry point for launcher-path control
    acceptance.  Production defaults must keep using
    ``workflow_adapter.open_control_service``.
    """
    root = _ensure_state_root(state_root)
    executor = SyntheticProducerExecutor(root)
    service = ExecutionService(
        repository=SQLiteExecutionRepository(root),
        executor=executor,
        identity_verifier=FileIdentityVerifier(
            sys.executable if identity_executable is None else identity_executable
        ),
    )
    executor.bind(service)
    return service


def synthetic_agent_entry(
    state_root: str | Path,
    run_id: str,
    *,
    identity_executable: str | None = None,
) -> RunSnapshot:
    """Explicit opt-in synthetic fixture/agent entry (default off).

    Reads the formal service snapshot before handing off through the public
    queued-intent consumer.  Only a ``QUEUED`` run with an existing,
    non-empty launch token may enter that hand-off; the fixture then waits
    until it has driven the run to a terminal state through the official
    ``ExecutionLifecycle``.
    Terminal runs attach by returning their existing snapshot; ``PREPARED``,
    ``PAUSED`` and ``RUNNING`` runs are rejected without claiming, resuming or
    launching anything:

        control prepare -> control execute -> synthetic_agent_entry
                                             -> control status/events/artifacts

    Production defaults never reach this entry; ``open_control_service`` and
    the regular control CLI keep the real control adapter, and the fixture
    only ever consumes the formal ``LaunchRequest`` (no direct SQLite or state
    writes).
    """
    if identity_executable is None:
        service = open_synthetic_service(state_root)
    else:
        service = open_synthetic_service(state_root, identity_executable=identity_executable)
    snapshot = service.status(run_id)
    if snapshot.state in TERMINAL_STATES:
        return snapshot
    if snapshot.state is not RunState.QUEUED:
        raise ExecutionServiceError(
            ErrorCode.INVALID_STATE_TRANSITION,
            f"Cannot attach synthetic fixture in {snapshot.state.value} state",
        )

    service.consume_queued_launch(run_id)
    while True:
        snapshot = service.status(run_id)
        if snapshot.state in TERMINAL_STATES:
            return snapshot
        time.sleep(0.02)


def _ensure_state_root(value: str | Path) -> StateRoot:
    root = Path(value).expanduser()
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(root, 0o700)
    return StateRoot.resolve(root)
