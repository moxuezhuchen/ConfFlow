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
import json
import sys
import time
from collections.abc import Callable, Sequence
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from typing import Any

from . import worker_handoff as _worker_handoff
from . import worker_security as _worker_security
from .application.execution.errors import ErrorCode, ExecutionServiceError
from .application.execution.models import RunState
from .application.execution.sqlite import SQLiteExecutionRepository
from .application.execution.state_root import StateRoot
from .application.execution.workflow_adapter import (
    WorkflowRunSpec,
    build_workflow_service,
    open_control_service,
)
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
from .worker_sidecar import WorkerSidecarPublisher

HANDOFF_SCHEMA = _worker_handoff.HANDOFF_SCHEMA
_ensure_directory = _worker_handoff.ensure_directory
_load_handoff = _worker_handoff.load_handoff
_stage_file = _worker_handoff.stage_file
_stage_worker_inputs = _worker_handoff.stage_worker_inputs
_verify_prepared_handoff = _worker_handoff.verify_prepared_handoff
_canonical_json = _worker_security._canonical_json
_file_digest = _worker_security._file_digest
_read_json_file = _worker_security._read_json_file
_safe_absolute_path = _worker_security._safe_absolute_path
_sha256_bytes = _worker_security._sha256_bytes
_validate_attempt_root = _worker_security._validate_attempt_root
_validate_path = _worker_security._validate_path

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
    handoff_digest = _verify_prepared_handoff(
        payload,
        config_path,
        expected_input_manifest_digest=aggregate.input_manifest_digest,
        expected_workflow_config_digest=aggregate.workflow_config_digest,
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


def _token_lease(root: StateRoot, run_id: str, token: str) -> TokenLeaseManager:
    """Create a lease below StateRoot's validated private runs layout."""
    paths = root.ensure_run_paths(run_id)
    return TokenLeaseManager(paths.staging.parent.parent, run_id, token)


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
