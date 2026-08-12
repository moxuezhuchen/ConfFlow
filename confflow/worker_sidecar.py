"""Atomic publication of the released control-worker sidecars."""

from __future__ import annotations

import hashlib
import os
import stat
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING

from .core.contracts import output_txt_path_for_input
from .worker_security import _validate_attempt_root, _validate_path

if TYPE_CHECKING:
    from .application.execution.state_root import StateRoot


class WorkerSidecarPublisher:
    """Publish the two fixed worker sidecars before terminal completion.

    Each file is copied to a private temporary file, fsynced, and installed
    with ``os.replace``.  A failed copy or install leaves the previous target
    untouched and, importantly, propagates to the service executor before its
    completion callback can run.
    """

    def __init__(self, root: StateRoot) -> None:
        self._root = root

    def publish(self, *, staged_input: str, work_dir: str) -> None:
        """Atomically publish the report and minimum-structure sidecars."""
        attempt_root = _validate_attempt_root(self._root)
        destination_root = Path(work_dir).parent
        if destination_root != attempt_root:
            _validate_path(
                destination_root,
                attempt_root,
                "workflow result root",
                kind="directory",
            )

        sources = (
            Path(output_txt_path_for_input(staged_input)),
            Path(staged_input).with_name(f"{Path(staged_input).stem}min.xyz"),
        )
        for source in sources:
            # Reject an escaped source path before inspecting filesystem
            # existence.  Error ordering is part of the fail-closed worker
            # boundary: an attacker must not turn path rejection into a
            # misleading missing-sidecar result.
            _validate_path(
                source,
                attempt_root,
                "worker sidecar source",
                kind="file",
                allow_missing=True,
            )
            if not source.is_file():
                raise FileNotFoundError(
                    f"worker completed without required sidecar: {source.name}"
                )
            destination = destination_root / source.name
            _validate_path(
                destination,
                attempt_root,
                "worker sidecar",
                kind="file",
                allow_missing=True,
            )
            if source == destination:
                continue
            self._atomic_copy(source, destination, expected_digest=_file_digest(source))

    @staticmethod
    def _atomic_copy(source: Path, destination: Path, *, expected_digest: str) -> None:
        """Copy one owner-owned source and replace its destination atomically."""
        nofollow = getattr(os, "O_NOFOLLOW", 0)
        source_fd = os.open(source, os.O_RDONLY | nofollow)
        temporary_path: str | None = None
        target_fd: int | None = None
        try:
            source_metadata = os.fstat(source_fd)
            if (
                not stat.S_ISREG(source_metadata.st_mode)
                or source_metadata.st_uid != os.getuid()
            ):
                raise ValueError(
                    f"worker sidecar source must be an owner-owned regular file: {source}"
                )

            target_fd, temporary_path = tempfile.mkstemp(
                prefix=f".{destination.name}.",
                suffix=".tmp",
                dir=destination.parent,
            )
            os.fchmod(target_fd, 0o600)
            digest = hashlib.sha256()
            while True:
                chunk = os.read(source_fd, 1024 * 1024)
                if not chunk:
                    break
                digest.update(chunk)
                remaining = memoryview(chunk)
                while remaining:
                    written = os.write(target_fd, remaining)
                    if written <= 0:
                        raise OSError("worker sidecar publication made no progress")
                    remaining = remaining[written:]
            if digest.hexdigest() != expected_digest:
                raise ValueError(f"worker sidecar changed while being published: {source}")
            os.fsync(target_fd)
            os.close(target_fd)
            target_fd = None
            os.replace(temporary_path, destination)
            temporary_path = None
            _fsync_directory(destination.parent)
        finally:
            if target_fd is not None:
                os.close(target_fd)
            os.close(source_fd)
            if temporary_path is not None:
                try:
                    os.unlink(temporary_path)
                except FileNotFoundError:
                    pass


def _file_digest(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _fsync_directory(path: Path) -> None:
    """Persist the directory entry where the platform exposes directory fsync."""
    try:
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    except OSError:
        return
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


__all__ = ["WorkerSidecarPublisher"]
