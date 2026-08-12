"""Producer-owned external worker for a queued control launch intent.

``confflow control execute`` deliberately stops at a durable ``queued``
state.  This entrypoint is the supported process boundary that consumes that
existing token, validates the producer-bound handoff envelope, and runs the
normal workflow engine through :class:`ExecutionService`.  It never calls
``prepare`` and never writes the repository outside the service/lifecycle
APIs.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import stat
import sys
import time
from collections.abc import Callable, Sequence
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from typing import Any

from .application.execution.errors import ErrorCode, ExecutionServiceError
from .application.execution.models import RunState
from .application.execution.sqlite import SQLiteExecutionRepository
from .application.execution.state_root import StateRoot
from .application.execution.workflow_adapter import (
    WorkflowRunSpec,
    build_workflow_service,
    open_control_service,
)
from .control import _validator
from .core.exceptions import StopRequestedError
from .core.logging import redirect_logging_streams
from .worker_lease import (  # noqa: F401 - retain private helper imports for callers
    TokenLeaseManager,
    _complete_owner_marker,
    _has_live_work_process,
)
from .worker_runner import (
    VerifiedWorkerHandoff,
    VerifiedWorkerLaunch,
    WorkerWorkflowRunnerAdapter,
    default_workflow_runner,
)
from .worker_security import (
    _canonical_json,
    _file_digest,
    _read_json_file,
    _safe_absolute_path,
    _sha256_bytes,
    _validate_attempt_root,
    _validate_path,
)
from .worker_sidecar import WorkerSidecarPublisher

HANDOFF_SCHEMA = "confflow.control.worker-handoff.v1"
_TERMINAL_STATES = frozenset({RunState.COMPLETED, RunState.FAILED, RunState.CANCELLED})


def run_control_worker(
    *,
    state_root: str | Path,
    run_id: str,
    handoff_path: str | Path,
    workflow_runner: Callable[..., dict[str, Any] | None] = default_workflow_runner,
    sleep: Callable[[float], None] = time.sleep,
) -> RunState:
    """Consume one prepared queued token and run the bound workflow.

    A paused attempt remains under this worker's supervision until a formal
    producer ``resume`` changes it back to ``queued``.  Rebuilding the local
    adapter for each attempt is intentional: every attempt gets the producer's
    current token, identity check, and lifecycle callbacks.
    """
    root = StateRoot.resolve(state_root)
    payload, config_path, tasks = _load_handoff(handoff_path, run_id, root)
    repository = SQLiteExecutionRepository(root)
    control_service = open_control_service(root.path)
    aggregate = repository.read(run_id)
    if aggregate is None:
        raise ExecutionServiceError(
            ErrorCode.UNKNOWN_RUN, "control worker cannot find the prepared run"
        )
    handoff_digest = _sha256_bytes(_canonical_json(payload))
    if handoff_digest != aggregate.input_manifest_digest:
        raise ExecutionServiceError(
            ErrorCode.INVALID_REQUEST,
            "control worker handoff digest does not match prepared request",
        )
    if _file_digest(config_path) != aggregate.workflow_config_digest:
        raise ExecutionServiceError(
            ErrorCode.INVALID_REQUEST,
            "control worker workflow digest does not match prepared request",
        )
    resume = False
    while True:
        current = repository.read(run_id)
        if current is None:
            raise ExecutionServiceError(ErrorCode.UNKNOWN_RUN, "control worker run disappeared")
        if current.state in _TERMINAL_STATES:
            return current.state
        if current.state is RunState.PAUSED:
            resume = True
            sleep(1.0)
            continue
        if current.state is RunState.RUNNING:
            token = current.launch_token
            if not token:
                raise ExecutionServiceError(
                    ErrorCode.INVALID_STATE_TRANSITION,
                    f"Running run has no launch token: {run_id}",
                )
            lease = _token_lease(root, run_id, token)
            if not lease.acquire():
                sleep(1.0)
                continue
            try:
                if not lease.can_recover(tasks[0]["work_dir"]):
                    # A crashed worker may leave a Gaussian/ORCA child alive.
                    # Never requeue unless the prior worker identity is known
                    # and no process remains in its process group or attempt
                    # directory; an operator/supervisor must drain unknown or
                    # detached children before retrying.
                    sleep(1.0)
                    continue
                recovered = control_service.recover_abandoned_launch(run_id, token=token)
                if recovered.state is RunState.QUEUED:
                    resume = True
            finally:
                lease.release()
            continue
        if current.state is not RunState.QUEUED:
            raise ExecutionServiceError(
                ErrorCode.INVALID_STATE_TRANSITION,
                f"control worker requires queued state, got {current.state.value}",
            )

        token = current.launch_token
        if not token:
            raise ExecutionServiceError(
                ErrorCode.INVALID_STATE_TRANSITION,
                f"Queued run has no launch token: {run_id}",
            )
        lease = _token_lease(root, run_id, token)
        if not lease.acquire():
            # Another process owns this token. It must remain the only worker;
            # attach by observing the producer projection instead of creating
            # a second ServiceWorkflowExecutor.
            sleep(1.0)
            continue
        try:
            staged_config, staged_tasks = _stage_worker_inputs(
                root,
                run_id,
                config_path,
                tasks,
                expected_config_digest=payload["workflow_config"]["sha256"],
            )
            run_paths = root.ensure_run_paths(run_id)
            spec = WorkflowRunSpec(
                run_id=run_id,
                input_xyz=tuple(item["input_xyz"] for item in staged_tasks),
                config_file=staged_config,
                work_dir=staged_tasks[0]["work_dir"],
                original_input_files=tuple(item["input_xyz"] for item in staged_tasks),
                resume=resume,
                pause_beacon_file=str(run_paths.work / "PAUSE"),
            )
            verified_handoff = VerifiedWorkerHandoff(
                run_id=run_id,
                digest=handoff_digest,
            )
            verified_launch = VerifiedWorkerLaunch(
                run_id=run_id,
                token=token,
                expected_identity=current.expected_executable_identity,
            )
            sidecar_publisher = WorkerSidecarPublisher(root)
            service, executor = build_workflow_service(
                spec,
                state_root=root.path,
                workflow_runner=_worker_workflow_runner(
                    workflow_runner,
                    original_input=staged_tasks[0]["input_xyz"],
                    handoff=verified_handoff,
                    launch=verified_launch,
                    work_dir=tasks[0]["work_dir"],
                    sidecar_publisher=sidecar_publisher,
                ),
            )
            executor_snapshot = service.consume_queued_launch(run_id)
            if executor_snapshot.state in _TERMINAL_STATES:
                return executor_snapshot.state
            try:
                executor.wait()
            except StopRequestedError:
                # A pause beacon is a normal lifecycle boundary. The executor
                # records PAUSED before re-raising; release the token lease so
                # a later producer resume can claim the new queued attempt.
                pass
        finally:
            lease.release()
        current = repository.read(run_id)
        if current is None:
            raise ExecutionServiceError(
                ErrorCode.UNKNOWN_RUN,
                "control worker run disappeared after execution",
            )
        if current.state is RunState.PAUSED:
            resume = True
            continue
        return current.state


def main(args_list: Sequence[str] | None = None) -> int:
    """Run the worker CLI and emit one compact JSON result when requested."""
    parser = argparse.ArgumentParser(prog="confflow-control-worker")
    parser.add_argument("--state-root", required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--handoff", required=True)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(args_list)
    if not args.json:
        parser.error("--json is required")
    json_stream = sys.stdout
    redirect_logging_streams(sys.stderr, include_root=True)
    try:
        with redirect_stdout(sys.stderr), redirect_stderr(sys.stderr):
            state = run_control_worker(
                state_root=args.state_root,
                run_id=args.run_id,
                handoff_path=args.handoff,
            )
    except Exception as error:  # noqa: BLE001 - process boundary maps one failure to stderr
        print(f"control worker failed: {error}", file=sys.stderr)
        return 1
    finally:
        # Keep the singleton logger away from pytest/capsys or other temporary
        # streams that may be closed after this process-boundary call returns.
        redirect_logging_streams(getattr(sys, "__stdout__", json_stream), include_root=True)
    print(
        json.dumps({"run_id": args.run_id, "state": state.value}, separators=(",", ":")),
        file=json_stream,
    )
    return 0 if state is RunState.COMPLETED else 1


def _load_handoff(
    path: str | Path, run_id: str, root: StateRoot
) -> tuple[dict[str, Any], str, list[dict[str, str]]]:
    attempt_root = _validate_attempt_root(root)
    handoff_path = _validate_path(path, attempt_root, "handoff", kind="file")
    payload = _read_json_file(handoff_path)
    if not isinstance(payload, dict):
        raise ValueError("control worker handoff must be an object")
    try:
        _validator("worker-handoff.schema.json").validate(payload)
    except Exception as error:  # jsonschema.ValidationError is intentionally optional here
        raise ValueError(f"control worker handoff schema validation failed: {error}") from error
    if payload.get("run_id") != run_id or payload.get("content_schema") != HANDOFF_SCHEMA:
        raise ValueError("control worker handoff identity does not match the requested run")
    config = payload["workflow_config"]
    config_path = _safe_absolute_path(config["path"], "workflow_config.path")
    _validate_path(config_path, attempt_root, "workflow_config.path", kind="file")
    if _file_digest(config_path) != config["sha256"]:
        raise ValueError("workflow configuration digest does not match handoff")
    tasks: list[dict[str, str]] = []
    for item in payload["tasks"]:
        input_path = _safe_absolute_path(item["input_xyz"], "task.input_xyz")
        if Path(input_path).suffix.lower() != ".xyz":
            raise ValueError("task.input_xyz must use the .xyz extension")
        work_dir = _safe_absolute_path(item["work_dir"], "task.work_dir")
        _validate_path(input_path, attempt_root, "task.input_xyz", kind="file")
        _validate_path(
            work_dir, attempt_root, "task.work_dir", kind="directory", allow_missing=True
        )
        if _file_digest(input_path) != item["sha256"]:
            raise ValueError(f"input digest does not match task {item['task_id']}")
        tasks.append(
            {
                "task_id": item["task_id"],
                "input_xyz": input_path,
                "work_dir": work_dir,
                "sha256": item["sha256"],
            }
        )
    return payload, config_path, tasks


def _token_lease(root: StateRoot, run_id: str, token: str) -> TokenLeaseManager:
    """Create a lease below StateRoot's validated private runs layout."""
    paths = root.ensure_run_paths(run_id)
    return TokenLeaseManager(paths.staging.parent.parent, run_id, token)


def _stage_worker_inputs(
    root: StateRoot,
    run_id: str,
    config_path: str,
    tasks: list[dict[str, str]],
    *,
    expected_config_digest: str,
) -> tuple[str, list[dict[str, str]]]:
    """Copy validated inputs into the producer-owned immutable staging root."""
    paths = root.ensure_run_paths(run_id)
    staged_config = _stage_file(
        config_path,
        paths.staging / "workflow.yaml",
        expected_digest=expected_config_digest,
    )
    staged_tasks: list[dict[str, str]] = []
    for task in tasks:
        input_name = Path(task["input_xyz"]).name
        staged_input = _stage_file(
            task["input_xyz"],
            paths.staging / "inputs" / input_name,
            expected_digest=task["sha256"],
        )
        staged_tasks.append({**task, "input_xyz": str(staged_input), "work_dir": task["work_dir"]})
    _ensure_directory(Path(tasks[0]["work_dir"]))
    return str(staged_config), staged_tasks


def _publish_worker_sidecars(
    publisher_or_root: WorkerSidecarPublisher | StateRoot,
    *,
    staged_input: str,
    work_dir: str,
) -> None:
    """Compatibility seam delegating fixed publication to its focused module."""
    publisher = (
        publisher_or_root
        if isinstance(publisher_or_root, WorkerSidecarPublisher)
        else WorkerSidecarPublisher(publisher_or_root)
    )
    publisher.publish(staged_input=staged_input, work_dir=work_dir)


def _stage_file(source: str, destination: Path, *, expected_digest: str) -> Path:
    """Copy one owner-owned regular file through a no-follow descriptor."""
    nofollow = getattr(os, "O_NOFOLLOW", 0)
    try:
        source_fd = os.open(source, os.O_RDONLY | nofollow)
    except OSError as error:
        raise ValueError(f"cannot securely open worker input {source}: {error}") from error
    try:
        metadata = os.fstat(source_fd)
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_uid != os.getuid():
            raise ValueError(f"worker input must be an owner-owned regular file: {source}")
        digest = hashlib.sha256()
        destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        target_fd = os.open(
            destination,
            os.O_WRONLY | os.O_CREAT | os.O_TRUNC | nofollow,
            0o600,
        )
        try:
            while True:
                chunk = os.read(source_fd, 1024 * 1024)
                if not chunk:
                    break
                digest.update(chunk)
                remaining = memoryview(chunk)
                while remaining:
                    written = os.write(target_fd, remaining)
                    if written <= 0:
                        raise OSError("worker input staging write made no progress")
                    remaining = remaining[written:]
            os.fsync(target_fd)
        finally:
            os.close(target_fd)
        if digest.hexdigest() != expected_digest:
            raise ValueError(f"worker input changed while being staged: {source}")
        return destination
    finally:
        os.close(source_fd)


def _ensure_directory(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    metadata = os.lstat(path)
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
        raise ValueError("worker work_dir must be a non-symlink directory")
    if metadata.st_uid != os.getuid():
        raise ValueError("worker work_dir must be owner-owned")
    os.chmod(path, 0o700)


def _worker_workflow_runner(
    runner: Callable[..., dict[str, Any] | None],
    *,
    handoff: VerifiedWorkerHandoff,
    launch: VerifiedWorkerLaunch,
    original_input: str,
    work_dir: str,
    sidecar_publisher: WorkerSidecarPublisher,
) -> Callable[..., dict[str, Any] | None]:
    """Build the runner only from a validated handoff and service binding."""
    return WorkerWorkflowRunnerAdapter(
        runner,
        handoff=handoff,
        launch=launch,
        original_input=original_input,
        work_dir=work_dir,
        sidecar_publisher=sidecar_publisher,
        publish_sidecars=_publish_worker_sidecars,
    )


if __name__ == "__main__":  # pragma: no cover - exercised at the process boundary
    raise SystemExit(main())


__all__ = ["HANDOFF_SCHEMA", "main", "run_control_worker"]
