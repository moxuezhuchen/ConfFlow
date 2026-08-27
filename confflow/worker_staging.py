"""Secure staging helpers for the producer-owned external worker."""

from __future__ import annotations

import hashlib
import os
import stat
from collections.abc import Callable
from pathlib import Path

from .application.execution.state_root import StateRoot


def _stage_worker_inputs(
    root: StateRoot,
    run_id: str,
    config_path: str,
    tasks: list[dict[str, str]],
    *,
    expected_config_digest: str,
    stage_file: Callable[..., Path] | None = None,
    ensure_directory: Callable[[Path], None] | None = None,
) -> tuple[str, list[dict[str, str]]]:
    """Copy validated inputs into the producer-owned immutable staging root."""
    stage_file_fn: Callable[..., Path] = _stage_file if stage_file is None else stage_file
    ensure_directory_fn: Callable[[Path], None] = (
        _ensure_directory if ensure_directory is None else ensure_directory
    )
    paths = root.ensure_run_paths(run_id)
    staged_config = stage_file_fn(
        config_path,
        paths.staging / "workflow.yaml",
        expected_digest=expected_config_digest,
    )
    staged_tasks: list[dict[str, str]] = []
    for task in tasks:
        input_name = Path(task["input_xyz"]).name
        staged_input = stage_file_fn(
            task["input_xyz"],
            paths.staging / "inputs" / input_name,
            expected_digest=task["sha256"],
        )
        staged_tasks.append({**task, "input_xyz": str(staged_input), "work_dir": task["work_dir"]})
    ensure_directory_fn(Path(tasks[0]["work_dir"]))
    return str(staged_config), staged_tasks


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
    """Create and validate an owner-only, non-symlink worker directory."""
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    metadata = os.lstat(path)
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
        raise ValueError("worker work_dir must be a non-symlink directory")
    if metadata.st_uid != os.getuid():
        raise ValueError("worker work_dir must be owner-owned")
    os.chmod(path, 0o700)


__all__ = ["_ensure_directory", "_stage_file", "_stage_worker_inputs"]
