"""Secure reader for the producer-owned external-worker handoff.

This module owns the immutable handoff envelope boundary. The control worker
re-exports the private helpers for compatibility with existing integrations and
tests, while execution and staging orchestration remains in control_worker.
"""

from __future__ import annotations

import hashlib
import json
import os
import stat
from pathlib import Path, PurePosixPath
from typing import Any

from .application.execution.state_root import StateRoot
from .control import _validator

HANDOFF_SCHEMA = "confflow.control.worker-handoff.v1"


def _load_handoff(
    path: str | Path, run_id: str, root: StateRoot
) -> tuple[dict[str, Any], str, list[dict[str, str]]]:
    """Read and validate one producer-owned worker handoff envelope."""
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


def _validate_attempt_root(root: StateRoot) -> Path:
    """Require the state-root parent to be an owner-controlled attempt root."""
    attempt_root = root.path.parent
    metadata = os.lstat(attempt_root)
    uid = os.getuid()
    if (
        not stat.S_ISDIR(metadata.st_mode)
        or metadata.st_uid != uid
        or stat.S_IMODE(metadata.st_mode) & (stat.S_IWGRP | stat.S_IWOTH)
    ):
        raise ValueError(
            "control worker state-root parent must be owner-controlled and non-writable"
        )
    return attempt_root.resolve(strict=True)


def _validate_path(
    value: str | Path,
    attempt_root: Path,
    label: str,
    *,
    kind: str,
    allow_missing: bool = False,
) -> Path:
    """Validate owner-only, no-symlink containment below the attempt root."""
    candidate = Path(value)
    try:
        relative = candidate.relative_to(attempt_root)
    except ValueError as error:
        raise ValueError(f"{label} must remain below the worker attempt root") from error
    current = attempt_root
    uid = os.getuid()
    parts = relative.parts
    if not parts:
        raise ValueError(f"{label} must not be the attempt root")
    for index, part in enumerate(parts):
        current /= part
        try:
            metadata = os.lstat(current)
        except FileNotFoundError:
            if allow_missing and index == len(parts) - 1:
                return current
            raise ValueError(f"{label} does not exist") from None
        if metadata.st_uid != uid or stat.S_ISLNK(metadata.st_mode):
            raise ValueError(f"{label} must contain only owner-owned non-symlink paths")
        if metadata.st_mode & (stat.S_IWGRP | stat.S_IWOTH):
            raise ValueError(f"{label} must not contain group/world-writable paths")
        if index < len(parts) - 1 and not stat.S_ISDIR(metadata.st_mode):
            raise ValueError(f"{label} has a non-directory parent")
    metadata = os.lstat(current)
    if metadata.st_mode & (stat.S_IWGRP | stat.S_IWOTH):
        raise ValueError(f"{label} must not contain group/world-writable paths")
    if kind == "file" and not stat.S_ISREG(metadata.st_mode):
        raise ValueError(f"{label} must be a regular file")
    if kind == "directory" and not stat.S_ISDIR(metadata.st_mode):
        raise ValueError(f"{label} must be a directory")
    return current


def _safe_absolute_path(value: object, label: str) -> str:
    """Accept only absolute canonical POSIX locators without traversal."""
    if not isinstance(value, str) or not value.startswith("/") or "\\" in value:
        raise ValueError(f"{label} must be an absolute POSIX path")
    path = PurePosixPath(value)
    if ".." in path.parts:
        raise ValueError(f"{label} must not contain parent traversal")
    return path.as_posix()


def _read_json_file(path: Path) -> dict[str, Any]:
    """Read one owner-owned regular JSON file through a stable descriptor."""
    nofollow = getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(path, os.O_RDONLY | nofollow)
    except OSError as error:
        raise ValueError(f"cannot securely open worker handoff {path}: {error}") from error
    try:
        metadata = os.fstat(fd)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid != os.getuid()
            or metadata.st_mode & (stat.S_IWGRP | stat.S_IWOTH)
        ):
            raise ValueError("worker handoff must be an owner-owned non-writable regular file")
        chunks: list[bytes] = []
        while True:
            chunk = os.read(fd, 1024 * 1024)
            if not chunk:
                break
            chunks.append(chunk)
    finally:
        os.close(fd)
    try:
        value = json.loads(b"".join(chunks).decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError(f"worker handoff is not valid UTF-8 JSON: {path}") from error
    if not isinstance(value, dict):
        raise ValueError("control worker handoff must be an object")
    return value


def _file_digest(path: str) -> str:
    """Return the SHA-256 digest of one regular file."""
    with Path(path).open("rb") as handle:
        digest = hashlib.sha256()
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _canonical_json(value: object) -> bytes:
    """Serialize a handoff payload with the frozen worker digest profile."""
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _sha256_bytes(value: bytes) -> str:
    """Return the SHA-256 digest of canonical handoff bytes."""
    return hashlib.sha256(value).hexdigest()


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
]
