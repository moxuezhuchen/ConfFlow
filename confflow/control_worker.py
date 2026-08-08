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
import sys
import time
from collections.abc import Callable, Sequence
from pathlib import Path, PurePosixPath
from typing import Any

from .application.execution.errors import ErrorCode, ExecutionServiceError
from .application.execution.models import RunState
from .application.execution.sqlite import SQLiteExecutionRepository
from .application.execution.state_root import StateRoot
from .application.execution.workflow_adapter import (
    WorkflowRunSpec,
    build_workflow_service,
)
from .control import _validator
from .core.exceptions import StopRequestedError
from .workflow.engine import run_workflow

HANDOFF_SCHEMA = "confflow.control.worker-handoff.v1"
_TERMINAL_STATES = frozenset({RunState.COMPLETED, RunState.FAILED, RunState.CANCELLED})


def run_control_worker(
    *,
    state_root: str | Path,
    run_id: str,
    handoff_path: str | Path,
    workflow_runner: Callable[..., dict[str, Any] | None] = run_workflow,
    sleep: Callable[[float], None] = time.sleep,
) -> RunState:
    """Consume one prepared queued token and run the bound workflow.

    A paused attempt remains under this worker's supervision until a formal
    producer ``resume`` changes it back to ``queued``.  Rebuilding the local
    adapter for each attempt is intentional: every attempt gets the producer's
    current token, identity check, and lifecycle callbacks.
    """
    root = StateRoot.resolve(state_root)
    payload, config_path, tasks = _load_handoff(handoff_path, run_id)
    repository = SQLiteExecutionRepository(root)
    aggregate = repository.read(run_id)
    if aggregate is None:
        raise ExecutionServiceError(ErrorCode.UNKNOWN_RUN, "control worker cannot find the prepared run")
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
        if current.state is not RunState.QUEUED:
            raise ExecutionServiceError(
                ErrorCode.INVALID_STATE_TRANSITION,
                f"control worker requires queued state, got {current.state.value}"
            )

        spec = WorkflowRunSpec(
            run_id=run_id,
            input_xyz=tuple(item["input_xyz"] for item in tasks),
            config_file=config_path,
            work_dir=tasks[0]["work_dir"],
            resume=resume,
            pause_beacon_file=str(root.path.parent / f"run_{run_id}" / "PAUSE"),
        )
        service, executor = build_workflow_service(
            spec,
            state_root=root.path,
            workflow_runner=workflow_runner,
        )
        executor_snapshot = service.consume_queued_launch(run_id)
        if executor_snapshot.state in _TERMINAL_STATES:
            return executor_snapshot.state
        try:
            executor.wait()
        except StopRequestedError:
            # A pause beacon is a normal lifecycle boundary.  The executor
            # records PAUSED before re-raising the engine sentinel; keep this
            # process alive so a later producer ``resume`` can return the
            # same launch intent to QUEUED without a second prepare/claim.
            pass
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
    try:
        state = run_control_worker(
            state_root=args.state_root,
            run_id=args.run_id,
            handoff_path=args.handoff,
        )
    except Exception as error:  # noqa: BLE001 - process boundary maps one failure to stderr
        print(f"control worker failed: {error}", file=sys.stderr)
        return 1
    print(json.dumps({"run_id": args.run_id, "state": state.value}, separators=(",", ":")))
    return 0 if state is RunState.COMPLETED else 1


def _load_handoff(path: str | Path, run_id: str) -> tuple[dict[str, Any], str, list[dict[str, str]]]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
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
    if _file_digest(config_path) != config["sha256"]:
        raise ValueError("workflow configuration digest does not match handoff")
    tasks: list[dict[str, str]] = []
    for item in payload["tasks"]:
        input_path = _safe_absolute_path(item["input_xyz"], "task.input_xyz")
        work_dir = _safe_absolute_path(item["work_dir"], "task.work_dir")
        if _file_digest(input_path) != item["sha256"]:
            raise ValueError(f"input digest does not match task {item['task_id']}")
        tasks.append(
            {
                "task_id": item["task_id"],
                "input_xyz": input_path,
                "work_dir": work_dir,
            }
        )
    return payload, config_path, tasks


def _safe_absolute_path(value: object, label: str) -> str:
    if not isinstance(value, str) or not value.startswith("/") or "\\" in value:
        raise ValueError(f"{label} must be an absolute POSIX path")
    path = PurePosixPath(value)
    if ".." in path.parts:
        raise ValueError(f"{label} must not contain parent traversal")
    return path.as_posix()


def _file_digest(path: str) -> str:
    with Path(path).open("rb") as handle:
        digest = hashlib.sha256()
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _canonical_json(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


__all__ = ["HANDOFF_SCHEMA", "main", "run_control_worker"]
