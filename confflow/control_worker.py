"""Producer-owned external worker for a queued control launch intent.

``confflow control execute`` deliberately stops at a durable ``queued``
state.  This entrypoint is the supported process boundary that consumes that
existing token, validates the producer-bound handoff envelope, and runs the
single formal V4 application through :class:`ExecutionService`.  It never
calls ``prepare`` and never writes the repository outside the service/lifecycle
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

from . import worker_attempt as _worker_attempt
from . import worker_sidecars as _worker_sidecars
from . import worker_staging as _worker_staging
from . import worker_supervision as _worker_supervision
from .application.execution.errors import ErrorCode, ExecutionServiceError
from .application.execution.launch_lease import TokenLaunchLease
from .application.execution.models import RunState
from .application.execution.sqlite import SQLiteExecutionRepository
from .application.execution.state_root import StateRoot
from .application.execution.workflow_adapter import (
    build_workflow_service,
    open_control_service,
)
from .application.v4_entry import formal_v4_runner as _formal_v4_runner
from .core.contracts import cli_output_to_txt
from .core.exceptions import StopRequestedError
from .core.logging import redirect_logging_streams
from .worker_handoff import (
    HANDOFF_SCHEMA,
    _canonical_json,
    _file_digest,
    _load_handoff,
    _read_json_file,
    _safe_absolute_path,
    _sha256_bytes,
    _validate_attempt_root,
    _validate_path,
)

# Formal runtime cutover (worker I): the default worker runner is the single
# V4 application authority. The parameter seam stays so tests and callers can
# inject doubles, but production never reaches the legacy engine.
run_workflow = _formal_v4_runner

_TERMINAL_STATES = frozenset({RunState.COMPLETED, RunState.FAILED, RunState.CANCELLED})


def run_control_worker(
    *,
    state_root: str | Path,
    run_id: str,
    handoff_path: str | Path,
    workflow_runner: Callable[..., dict[str, Any] | None] = _formal_v4_runner,
    sleep: Callable[[float], None] = time.sleep,
) -> RunState:
    """Consume one prepared queued token and run the bound workflow.

    A paused attempt remains under this worker's supervision until a formal
    producer ``resume`` changes it back to ``queued``.  Rebuilding the local
    adapter for each attempt is intentional: every attempt gets the producer's
    current token, identity check, and lifecycle callbacks.

    Terminal branching (D2) follows the durable service aggregate, which the
    adapter commits from the formal V4 status: only V4 ``completed`` yields
    service COMPLETED; V4 ``failed`` and V4 ``partial`` (as FAILED with a
    ``v4.status.partial`` marker) yield service FAILED; V4 ``cancelled``
    yields service CANCELLED. The worker never treats "runner returned
    without exception" as success — the V4 status-owned aggregate decides.

    Cancellation recovery is manifest-aware: a confirmed durable cancel for
    a crashed attempt terminalizes the run root's generation record and
    commits the service aggregate to the SAME terminal status the run root
    already proves (a same-generation terminal manifest is the completion
    linearization point and is never downgraded to cancelled).  Without
    this, a crash between native work and terminal publication left the
    JobDesk-visible generation ``running`` forever while the service said
    cancelled.
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
        if current.cancel_pending and current.state in {RunState.QUEUED, RunState.PAUSED}:
            token = current.launch_token
            if not token:
                raise ExecutionServiceError(
                    ErrorCode.INVALID_STATE_TRANSITION,
                    f"Cancellation-pending run has no launch token: {run_id}",
                )
            lease = _token_lease(root, run_id, token)
            if not lease.acquire():
                sleep(1.0)
                continue
            try:
                previous_owner = lease.previous_owner
                if not _cancel_owner_is_stopped(tasks[0]["work_dir"], owner=previous_owner):
                    # A stale or malformed owner marker, or a still-live
                    # process, is not proof that the prior worker stopped.
                    # Keep the durable cancel pending until a later retry can
                    # establish the crash-safe boundary.
                    sleep(1.0)
                    continue
                return _commit_cancel_or_manifest_winner(
                    control_service,
                    run_id,
                    token,
                    work_dir=tasks[0]["work_dir"],
                )
            finally:
                lease.release()
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
                previous_owner = lease.previous_owner
                if not _complete_owner_marker(previous_owner) or _has_live_work_process(
                    tasks[0]["work_dir"], owner=previous_owner
                ):
                    # A crashed worker may leave a Gaussian/ORCA child alive.
                    # Never requeue unless the prior worker identity is known
                    # and no process remains in its process group or attempt
                    # directory; an operator/supervisor must drain unknown or
                    # detached children before retrying.
                    sleep(1.0)
                    continue
                if current.cancel_pending:
                    return _commit_cancel_or_manifest_winner(
                        control_service,
                        run_id,
                        token,
                        work_dir=tasks[0]["work_dir"],
                    )
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
            try:
                attempt_state = _worker_attempt.run_worker_attempt(
                    root=root,
                    run_id=run_id,
                    staged_config=staged_config,
                    staged_tasks=staged_tasks,
                    resume=resume,
                    workflow_runner=_worker_workflow_runner(
                        workflow_runner,
                        original_input=staged_tasks[0]["input_xyz"],
                        root=root,
                        work_dir=tasks[0]["work_dir"],
                    ),
                    service_builder=build_workflow_service,
                )
            except StopRequestedError:
                # A pause beacon is a normal lifecycle boundary. The executor
                # records PAUSED before re-raising; release the token lease so
                # a later producer resume can claim the new queued attempt.
                pass
            else:
                if attempt_state in _TERMINAL_STATES:
                    return attempt_state
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
    # Worker exit semantics (D2): completed is the only success exit (0).
    # Failed, cancelled, and partial (service FAILED with a v4.status.partial
    # marker; scientific status=partial stays in run_result.json) are all
    # documented non-success exits (1), agreeing with the synchronous
    # service facade (TERMINAL_RUN) and the CLI's nonzero mapping. No
    # contract allows partial success, so partial never exits 0.
    return 0 if state is RunState.COMPLETED else 1


def _token_lease(root: StateRoot, run_id: str, token: str) -> TokenLaunchLease:
    """Create a lease below StateRoot's validated private runs layout."""
    paths = root.ensure_run_paths(run_id)
    return TokenLaunchLease(paths.staging.parent.parent, run_id, token)


def _commit_cancel_or_manifest_winner(
    control_service: Any,
    run_id: str,
    token: str,
    *,
    work_dir: str,
) -> RunState:
    """Terminalize the run root and commit the matching service aggregate.

    The producer-side completion linearization point is the durable
    same-generation ``run_result.json``.  When it exists, the run already
    terminated (scientifically) before the cancel and the service commits
    that status; otherwise the durable cancel wins and the generation
    record is terminalized as cancelled.  Either way the JobDesk-visible
    generation record and the durable service aggregate end consistent.
    """
    from .application.execution.workflow_adapter import (
        ExecutionLifecycle,
        _load_artifacts,
    )
    from .application.v4_entry import terminalize_generation_for_durable_cancel

    status = terminalize_generation_for_durable_cancel(
        run_root=work_dir,
        reason=(
            "durable cancellation confirmed after the producer stopped; "
            "no matching result manifest was published"
        ),
    )
    lifecycle = ExecutionLifecycle(control_service, run_id, token)
    if status == "cancelled":
        return lifecycle.cancelled().state
    try:
        artifacts = _load_artifacts(work_dir)
    except Exception:  # noqa: BLE001 - a legacy manifest defect must not block the terminal commit
        artifacts = ()
    if control_service.status(run_id).state is RunState.QUEUED:
        # A requeued (recovered) attempt whose producer already published a
        # terminal manifest still needs the running transition the crashed
        # worker never committed.
        lifecycle.started()
    if status == "completed":
        return lifecycle.completed(artifacts).state
    return lifecycle.failed(artifacts).state


def _complete_owner_marker(owner: dict[str, object] | None) -> bool:
    """Compatibility wrapper preserving the historical patch seam."""
    return _worker_supervision._complete_owner_marker(owner)


def _cancel_owner_is_stopped(work_dir: str, *, owner: dict[str, object] | None) -> bool:
    """Compatibility wrapper preserving historical supervisor patch seams."""
    return _worker_supervision._cancel_owner_is_stopped(
        work_dir,
        owner=owner,
        complete_owner_marker=_complete_owner_marker,
        has_live_work_process=_has_live_work_process,
    )


def _has_live_work_process(work_dir: str, *, owner: dict[str, object] | None = None) -> bool:
    """Compatibility wrapper preserving the historical patch seam."""
    return _worker_supervision._has_live_work_process(work_dir, owner=owner)


def _stage_worker_inputs(
    root: StateRoot,
    run_id: str,
    config_path: str,
    tasks: list[dict[str, str]],
    *,
    expected_config_digest: str,
) -> tuple[str, list[dict[str, str]]]:
    """Compatibility wrapper preserving the old monkeypatch seam."""
    return _worker_staging._stage_worker_inputs(
        root,
        run_id,
        config_path,
        tasks,
        expected_config_digest=expected_config_digest,
        stage_file=_stage_file,
        ensure_directory=_ensure_directory,
    )


def _stage_file(source: str, destination: Path, *, expected_digest: str) -> Path:
    """Compatibility wrapper for the extracted secure file stager."""
    return _worker_staging._stage_file(
        source,
        destination,
        expected_digest=expected_digest,
    )


def _ensure_directory(path: Path) -> None:
    """Compatibility wrapper for the extracted secure directory validator."""
    _worker_staging._ensure_directory(path)


def _publish_worker_sidecars(root: StateRoot, *, staged_input: str, work_dir: str) -> None:
    """Compatibility wrapper preserving the historical worker patch seam."""
    _worker_sidecars._publish_worker_sidecars(
        root,
        staged_input=staged_input,
        work_dir=work_dir,
        stage_file=_stage_file,
        file_digest=_file_digest,
    )


def _worker_workflow_runner(
    runner: Callable[..., dict[str, Any] | None],
    *,
    original_input: str,
    root: StateRoot,
    work_dir: str,
) -> Callable[..., dict[str, Any] | None]:
    """Run the single V4 application while preserving the public report sidecar.

    The interactive CLI owns this redirect for direct runs.  The external
    worker crosses the service boundary without invoking that CLI, so it must
    create the same ``<input-stem>.txt`` artifact itself.
    """

    def _run(**kwargs: Any) -> dict[str, Any] | None:
        with cli_output_to_txt(original_input):
            result = runner(**kwargs)
        # Publish fixed legacy sidecars before ExecutionLifecycle.completed()
        # commits the terminal aggregate. A failed copy must become a failed
        # attempt, not an irreversible completed run with missing metadata.
        try:
            _publish_worker_sidecars(root, staged_input=original_input, work_dir=work_dir)
        except FileNotFoundError:
            # Formal V4 runtime: the V4 application publishes run_result.json,
            # not the legacy min.xyz. The .txt report above still exists; when
            # the V4 manifest is present the missing legacy sidecar is not a
            # failure. Without a V4 manifest the legacy requirement stands.
            from pathlib import Path as _Path

            if not (_Path(work_dir) / "run_result.json").is_file():
                raise
        return result

    return _run


if __name__ == "__main__":  # pragma: no cover - exercised at the process boundary
    raise SystemExit(main())


__all__ = [
    "HANDOFF_SCHEMA",
    "_canonical_json",
    "_file_digest",
    "_load_handoff",
    "_read_json_file",
    "_safe_absolute_path",
    "_sha256_bytes",
    "_validate_attempt_root",
    "_validate_path",
    "main",
    "run_control_worker",
]
