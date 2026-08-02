"""Explicit opt-in synthetic producer lifecycle fixture (default off).

This module provides a non-computing ``WorkflowExecutor`` that consumes the
formal ``ExecutionService`` launch intent and drives every durable fact through
the official token-bound ``ExecutionLifecycle`` surface:

    prepared -> queued -> running -> (checkpointed) -> terminal

The fixture exists only for launcher-path control acceptance and is never
wired into production defaults: ``open_control_service`` and the regular
``confflow control execute`` path keep using the real control adapter.
Callers must explicitly build the fixture with ``open_synthetic_service`` or
by constructing ``SyntheticProducerExecutor`` directly.

Payload is restricted to one fixed, small, built-in text artifact.  No shell
command, external executable, user content, or caller-supplied path is ever
accepted.  The fixture never writes the producer SQLite repository and never
invokes the workflow engine; only the service's CAS mutations produce events,
revisions, cursors, checkpoints and the validated artifact manifest.
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
import threading
from pathlib import Path

from .errors import ErrorCode, ExecutionServiceError
from .models import Artifact, CancelReceipt, CancelRequest, LaunchReceipt, LaunchRequest, RunState
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

    Exactly one worker thread ever exists per launch token in this process, and
    the durable claim marker in the runs directory prevents a second process
    from spawning a second worker for the same token.  Repeated or concurrent
    ``ensure_launched`` calls attach to the existing attempt and never start
    another producer.  A confirmed cancellation tombstones the token and
    signals the worker to back off; the service owns the terminal transition.
    """

    def __init__(self, state_root: StateRoot) -> None:
        self._runs_root = state_root.path.parent
        self._service: ExecutionService | None = None
        self._lock = threading.Lock()
        self._threads: dict[str, threading.Thread] = {}
        self._tombstones: set[str] = set()
        self._errors: dict[str, str] = {}

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
        """Consume one launch token idempotently and attach without duplication."""
        with self._lock:
            if request.token in self._tombstones or self._cancelled_marker(request.run_id):
                return LaunchReceipt(accepted=False, cancelled=True)
            if request.token in self._threads:
                return LaunchReceipt(accepted=True)
            if self._claim(request):
                self._start_worker(request)
                return LaunchReceipt(accepted=True)
            if not self._may_take_over(request):
                return LaunchReceipt(accepted=True)
            self._start_worker(request)
            return LaunchReceipt(accepted=True)

    def ensure_cancelled(self, request: CancelRequest) -> CancelReceipt:
        """Tombstone the bound launch token and signal the worker to back off."""
        with self._lock:
            self._tombstones.add(request.launch_token or "")
        run_dir = self._runs_root / f"run_{request.run_id}"
        run_dir.mkdir(parents=True, exist_ok=True)
        (run_dir / "synthetic.cancelled").touch()
        return CancelReceipt(confirmed=True)

    def _claim(self, request: LaunchRequest) -> bool:
        """Atomically claim the token with an exclusive marker; True on first claim."""
        run_dir = self._runs_root / f"run_{request.run_id}"
        run_dir.mkdir(parents=True, exist_ok=True)
        marker = run_dir / f"synthetic.claim.{request.token}"
        try:
            with open(marker, "x", encoding="utf-8") as handle:
                json.dump(
                    {"v": 1, "run_id": request.run_id, "token": request.token},
                    handle,
                    sort_keys=True,
                    separators=(",", ":"),
                )
            return True
        except FileExistsError:
            return False

    def _may_take_over(self, request: LaunchRequest) -> bool:
        """Decide whether an attach may drive a still-queued foreign claim."""
        service = self._service
        if service is None:
            return False
        try:
            state = service.status(request.run_id).state
        except ExecutionServiceError:
            return False
        return state is RunState.QUEUED

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
        try:
            service = self._service
            if service is None:
                raise RuntimeError("SyntheticProducerExecutor is not bound to an ExecutionService")
            if self._is_cancelled(request):
                return
            lifecycle = ExecutionLifecycle(service, request.run_id, request.token)
            lifecycle.started()
            if self._is_cancelled(request):
                return
            run_dir = self._runs_root / f"run_{request.run_id}"
            artifact_dir = run_dir / "synthetic"
            artifact_dir.mkdir(parents=True, exist_ok=True)
            (artifact_dir / "artifact.txt").write_text(SYNTHETIC_ARTIFACT_CONTENT, encoding="utf-8")
            lifecycle.checkpoint(SYNTHETIC_CHECKPOINT_ID)
            if self._is_cancelled(request):
                return
            lifecycle.completed((SYNTHETIC_ARTIFACT,))
        except ExecutionServiceError as error:
            if error.code is ErrorCode.INVALID_STATE_TRANSITION:
                # Token-arbitration loser: another worker won a lifecycle race,
                # a confirmed cancellation claimed the aggregate, or a terminal
                # winner already owns the run.  Nothing further may be written.
                return
            self._record_error(request, error)
            self._fail(request)
        except BaseException as error:  # noqa: BLE001 - preserve worker failures
            self._record_error(request, error)
            self._fail(request)

    def _fail(self, request: LaunchRequest) -> None:
        """Commit a terminal failure only through the official lifecycle surface."""
        service = self._service
        if service is None:
            return
        try:
            ExecutionLifecycle(service, request.run_id, request.token).failed((SYNTHETIC_ARTIFACT,))
        except ExecutionServiceError:
            pass

    def _is_cancelled(self, request: LaunchRequest) -> bool:
        if request.token in self._tombstones:
            return True
        return self._cancelled_marker(request.run_id)

    def _record_error(self, request: LaunchRequest, error: BaseException) -> None:
        with self._lock:
            self._errors[request.token] = f"{type(error).__name__}: {error}"


def open_synthetic_service(state_root: str | Path) -> ExecutionService:
    """Open the explicit opt-in synthetic service for one state root.

    This is the only supported entry point for launcher-path control
    acceptance.  Production defaults must keep using
    ``workflow_adapter.open_control_service``.
    """
    root = _ensure_state_root(state_root)
    executor = SyntheticProducerExecutor(root)
    service = ExecutionService(
        repository=SQLiteExecutionRepository(root),
        executor=executor,
        identity_verifier=FileIdentityVerifier(sys.executable),
    )
    executor.bind(service)
    return service


def _ensure_state_root(value: str | Path) -> StateRoot:
    root = Path(value).expanduser()
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(root, 0o700)
    return StateRoot.resolve(root)
