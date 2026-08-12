"""Focused validation and staging boundary for the external control worker."""

from __future__ import annotations

import hashlib
import os
import stat
from collections.abc import Mapping
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .application.execution.errors import ErrorCode, ExecutionServiceError
from .control import _validator
from .worker_security import (
    _canonical_json,
    _file_digest,
    _read_json_file,
    _safe_absolute_path,
    _sha256_bytes,
    _validate_attempt_root,
    _validate_path,
)

if TYPE_CHECKING:
    from .application.execution.state_root import StateRoot


HANDOFF_SCHEMA = "confflow.control.worker-handoff.v1"


class WorkerHandoffValidator:
    """Validate one producer-bound worker handoff before execution."""

    def load(
        self, path: str | Path, run_id: str, root: StateRoot
    ) -> tuple[dict[str, Any], str, list[dict[str, str]]]:
        """Load and validate the handoff envelope and all referenced inputs."""
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


def verify_prepared_handoff(
    payload: Mapping[str, Any],
    config_path: str,
    *,
    expected_input_manifest_digest: str,
    expected_workflow_config_digest: str,
) -> str:
    """Match the validated handoff to the producer's prepared request."""
    handoff_digest = _sha256_bytes(_canonical_json(payload))
    if handoff_digest != expected_input_manifest_digest:
        raise ExecutionServiceError(
            ErrorCode.INVALID_REQUEST,
            "control worker handoff digest does not match prepared request",
        )
    if _file_digest(config_path) != expected_workflow_config_digest:
        raise ExecutionServiceError(
            ErrorCode.INVALID_REQUEST,
            "control worker workflow digest does not match prepared request",
        )
    return handoff_digest


class WorkerInputStager:
    """Stage validated worker inputs into the producer-owned attempt root."""

    def __init__(self, root: StateRoot, run_id: str) -> None:
        self._root = root
        self._run_id = run_id

    def stage(
        self,
        config_path: str,
        tasks: list[dict[str, str]],
        *,
        expected_config_digest: str,
    ) -> tuple[str, list[dict[str, str]]]:
        """Copy the configuration and task inputs through the secure boundary."""
        paths = self._root.ensure_run_paths(self._run_id)
        staged_config = stage_file(
            config_path,
            paths.staging / "workflow.yaml",
            expected_digest=expected_config_digest,
        )
        staged_tasks: list[dict[str, str]] = []
        for task in tasks:
            input_name = Path(task["input_xyz"]).name
            staged_input = stage_file(
                task["input_xyz"],
                paths.staging / "inputs" / input_name,
                expected_digest=task["sha256"],
            )
            staged_tasks.append(
                {**task, "input_xyz": str(staged_input), "work_dir": task["work_dir"]}
            )
        ensure_directory(Path(tasks[0]["work_dir"]))
        return str(staged_config), staged_tasks


def load_handoff(
    path: str | Path, run_id: str, root: StateRoot
) -> tuple[dict[str, Any], str, list[dict[str, str]]]:
    """Compatibility function for callers of the former control-worker helper."""
    return WorkerHandoffValidator().load(path, run_id, root)


def stage_worker_inputs(
    root: StateRoot,
    run_id: str,
    config_path: str,
    tasks: list[dict[str, str]],
    *,
    expected_config_digest: str,
) -> tuple[str, list[dict[str, str]]]:
    """Compatibility function for callers of the former staging helper."""
    return WorkerInputStager(root, run_id).stage(
        config_path,
        tasks,
        expected_config_digest=expected_config_digest,
    )


def stage_file(source: str, destination: Path, *, expected_digest: str) -> Path:
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


def ensure_directory(path: Path) -> None:
    """Create and verify an owner-owned, non-symlink worker directory."""
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    metadata = os.lstat(path)
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
        raise ValueError("worker work_dir must be a non-symlink directory")
    if metadata.st_uid != os.getuid():
        raise ValueError("worker work_dir must be owner-owned")
    os.chmod(path, 0o700)


__all__ = [
    "HANDOFF_SCHEMA",
    "WorkerHandoffValidator",
    "WorkerInputStager",
    "ensure_directory",
    "load_handoff",
    "stage_file",
    "stage_worker_inputs",
    "verify_prepared_handoff",
]
