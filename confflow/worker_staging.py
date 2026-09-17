"""Secure staging helpers for the producer-owned external worker."""

from __future__ import annotations

import hashlib
import os
import secrets
import stat
from collections.abc import Callable
from pathlib import Path

from .application.execution.state_root import StateRoot

_TEMPORARY_NAME_PREFIX = ".confflow-stage-"
_TEMPORARY_NAME_TOKEN_BYTES = 16


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
    """Atomically stage one owner-owned regular file through no-follow descriptors."""
    nofollow = getattr(os, "O_NOFOLLOW", 0)
    directory = getattr(os, "O_DIRECTORY", 0)
    owner = os.getuid()
    try:
        source_fd = os.open(source, os.O_RDONLY | nofollow)
    except OSError as error:
        raise ValueError(f"cannot securely open worker input {source}: {error}") from error

    parent_fd: int | None = None
    temporary_fd: int | None = None
    temporary_name: str | None = None
    try:
        metadata = os.fstat(source_fd)
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_uid != owner:
            raise ValueError(f"worker input must be an owner-owned regular file: {source}")

        destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        try:
            parent_fd = os.open(destination.parent, os.O_RDONLY | directory | nofollow)
        except OSError as error:
            raise ValueError(
                f"cannot securely open worker staging directory {destination.parent}: {error}"
            ) from error
        parent_metadata = os.fstat(parent_fd)
        if (
            not stat.S_ISDIR(parent_metadata.st_mode)
            or parent_metadata.st_uid != owner
            or parent_metadata.st_mode & (stat.S_IWGRP | stat.S_IWOTH)
        ):
            raise ValueError(
                "worker staging directory must be owner-owned and not group/world-writable: "
                f"{destination.parent}"
            )

        _validate_existing_destination(destination.name, parent_fd, owner)
        temporary_fd, temporary_name = _open_temporary_destination(
            destination.name, parent_fd, nofollow
        )
        temporary_metadata = os.fstat(temporary_fd)
        if not stat.S_ISREG(temporary_metadata.st_mode) or temporary_metadata.st_uid != owner:
            raise ValueError("worker staging temporary file must be owner-owned and regular")
        os.fchmod(temporary_fd, 0o600)

        digest = hashlib.sha256()
        while True:
            chunk = os.read(source_fd, 1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
            remaining = memoryview(chunk)
            while remaining:
                written = os.write(temporary_fd, remaining)
                if written <= 0:
                    raise OSError("worker input staging write made no progress")
                remaining = remaining[written:]
        if digest.hexdigest() != expected_digest:
            raise ValueError(f"worker input changed while being staged: {source}")

        os.fsync(temporary_fd)
        temporary_metadata = os.fstat(temporary_fd)
        path_metadata = os.stat(temporary_name, dir_fd=parent_fd, follow_symlinks=False)
        if (
            not stat.S_ISREG(temporary_metadata.st_mode)
            or temporary_metadata.st_uid != owner
            or not stat.S_ISREG(path_metadata.st_mode)
            or path_metadata.st_uid != owner
            or (path_metadata.st_dev, path_metadata.st_ino)
            != (temporary_metadata.st_dev, temporary_metadata.st_ino)
        ):
            raise ValueError("worker staging temporary file must be a stable regular file")

        # Re-check just before publication. os.replace never follows the final
        # destination, and the verified directory descriptor pins the parent.
        _validate_existing_destination(destination.name, parent_fd, owner)
        fd_to_close = temporary_fd
        temporary_fd = None
        os.close(fd_to_close)
        os.replace(
            temporary_name,
            destination.name,
            src_dir_fd=parent_fd,
            dst_dir_fd=parent_fd,
        )
        temporary_name = None
        os.fsync(parent_fd)
        return destination
    finally:
        if temporary_fd is not None:
            os.close(temporary_fd)
        if temporary_name is not None and parent_fd is not None:
            try:
                os.unlink(temporary_name, dir_fd=parent_fd)
            except FileNotFoundError:
                pass
        if parent_fd is not None:
            os.close(parent_fd)
        os.close(source_fd)


def _open_temporary_destination(
    destination_name: str,
    parent_fd: int,
    nofollow: int,
) -> tuple[int, str]:
    """Create a private same-directory temporary file without following links."""
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | nofollow
    for _ in range(10):
        temporary_name = f"{_TEMPORARY_NAME_PREFIX}{secrets.token_hex(_TEMPORARY_NAME_TOKEN_BYTES)}"
        try:
            return os.open(temporary_name, flags, 0o600, dir_fd=parent_fd), temporary_name
        except FileExistsError:
            continue
    raise FileExistsError(
        f"could not allocate a unique worker staging temporary file for {destination_name}"
    )


def _validate_existing_destination(destination_name: str, parent_fd: int, owner: int) -> None:
    """Reject an existing staging target outside the owner-owned regular-file boundary."""
    try:
        metadata = os.stat(destination_name, dir_fd=parent_fd, follow_symlinks=False)
    except FileNotFoundError:
        return
    except OSError as error:
        raise ValueError(
            f"cannot securely inspect worker staging destination {destination_name}"
        ) from error
    if stat.S_ISLNK(metadata.st_mode):
        raise ValueError(
            f"worker staging destination must be a non-symlink file: {destination_name}"
        )
    if not stat.S_ISREG(metadata.st_mode) or metadata.st_uid != owner:
        raise ValueError(
            f"worker staging destination must be an owner-owned regular file: {destination_name}"
        )


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
