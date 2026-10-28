"""Storage policy shared by durable SQLite state stores.

Local filesystems use WAL. Any other filesystem is refused unless a
root-owned approval file declares the locking, atomic-rename and fsync
guarantees, in which case the store uses DELETE journal mode. Failures are
persistence errors; the application layer translates them at its boundary.
"""

from __future__ import annotations

import json
import os
import stat
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from .contracts import PersistenceError

__all__ = [
    "LOCAL_WAL_FILESYSTEMS",
    "NON_LOCAL_FILESYSTEMS",
    "ApprovalVerifier",
    "FilesystemDetector",
    "SharedFilesystemApproval",
    "StorageUnsupportedError",
    "assert_local_wal_filesystem",
    "detect_filesystem_type",
    "mount_table",
    "preflight_store",
    "reject_database_symlinks",
    "select_journal_mode",
]

LOCAL_WAL_FILESYSTEMS: Final[frozenset[str]] = frozenset(
    {"ext4", "xfs", "btrfs", "tmpfs", "overlay", "apfs"}
)
NON_LOCAL_FILESYSTEMS: Final[frozenset[str]] = frozenset(
    {"9p", "nfs", "nfs4", "cifs", "smbfs", "fuse.sshfs"}
)

FilesystemDetector = Callable[[str | os.PathLike[str]], str]


class StorageUnsupportedError(PersistenceError):
    """Raised when storage cannot honour the requested durability policy."""


@dataclass(frozen=True)
class SharedFilesystemApproval:
    """Verified administrator declaration bound to exactly one state-root identity."""

    approval_id: str
    root_realpath: str
    device: int
    inode: int
    filesystem_type: str


def mount_table() -> list[tuple[str, str]]:
    """Return ``(realpath, fstype)`` pairs from the Linux mount table."""
    rows: list[tuple[str, str]] = []
    for line in Path("/proc/mounts").read_text(encoding="utf-8").splitlines():
        fields = line.split()
        if len(fields) >= 3:
            rows.append((os.path.realpath(fields[1].replace("\\040", " ")), fields[2]))
    return rows


def _longest_mount_type(target: str, mounts: list[tuple[str, str]]) -> str | None:
    """Return the filesystem type of the longest mount point containing *target*."""
    match = max(
        (
            item
            for item in mounts
            if target.startswith(item[0].rstrip("/") + "/") or target == item[0]
        ),
        key=lambda item: len(item[0]),
        default=None,
    )
    return None if match is None else match[1]


def detect_filesystem_type(path: str | os.PathLike[str]) -> str:
    """Return the filesystem type under *path*, or ``unknown`` when no mount matches."""
    mounted = _longest_mount_type(os.path.realpath(path), mount_table())
    return "unknown" if mounted is None else mounted


def _local_required(filesystem: str, path: str | os.PathLike[str]) -> str:
    return (
        "WAL requires a verified local filesystem; "
        f"{os.fspath(path)} is on {filesystem!r}. "
        "Put the run root on a local filesystem such as ext4 or xfs."
    )


def assert_local_wal_filesystem(
    path: str | os.PathLike[str], detector: FilesystemDetector = detect_filesystem_type
) -> str:
    """Return the detected type of *path* when it is a local WAL-capable filesystem."""
    try:
        detected = detector(path)
        if detected not in LOCAL_WAL_FILESYSTEMS:
            raise StorageUnsupportedError(_local_required(detected, path))
        mounted = _longest_mount_type(os.path.realpath(path), mount_table())
        if mounted is None or mounted in NON_LOCAL_FILESYSTEMS:
            raise StorageUnsupportedError(_local_required(mounted or "unknown", path))
    except FileNotFoundError as error:
        raise StorageUnsupportedError("Cannot verify filesystem type for WAL") from error
    return detected


class ApprovalVerifier:
    """Verify external root-owned approval JSON without trusting caller-supplied objects."""

    def verify(
        self, path: str | os.PathLike[str], *, root: Path, filesystem_type: str
    ) -> SharedFilesystemApproval:
        """Read a regular root-owned approval file and bind every identity field."""
        candidate = Path(os.fspath(path))
        descriptor: int | None = None
        try:
            nofollow = getattr(os, "O_NOFOLLOW", 0)
            if not nofollow:
                raise StorageUnsupportedError("Shared-FS approval verification requires O_NOFOLLOW")
            descriptor = os.open(candidate, os.O_RDONLY | nofollow)
            metadata = os.fstat(descriptor)
            if not stat.S_ISREG(metadata.st_mode):
                raise StorageUnsupportedError(
                    "Shared-FS approval must be a regular non-symlink file"
                )
            if metadata.st_uid != 0 or stat.S_IMODE(metadata.st_mode) & (
                stat.S_IWGRP | stat.S_IWOTH
            ):
                raise StorageUnsupportedError(
                    "Shared-FS approval must be root-owned and non-writable by group/other"
                )
            with os.fdopen(descriptor, encoding="utf-8", closefd=False) as stream:
                payload = json.load(stream)
            guarantees = payload["guarantees"]
            root_stat = root.stat()
            if (
                not isinstance(payload, dict)
                or set(payload)
                != {
                    "approval_id",
                    "root_realpath",
                    "device",
                    "inode",
                    "filesystem_type",
                    "guarantees",
                }
                or not isinstance(guarantees, dict)
                or set(guarantees) != {"locking", "atomic_rename", "fsync"}
                or any(type(guarantees[key]) is not bool for key in guarantees)
                or not all(guarantees.values())
                or not isinstance(payload["approval_id"], str)
                or not payload["approval_id"]
                or not isinstance(payload["root_realpath"], str)
                or payload["root_realpath"] != str(root.resolve())
                or type(payload["device"]) is not int
                or payload["device"] < 0
                or payload["device"] != root_stat.st_dev
                or type(payload["inode"]) is not int
                or payload["inode"] < 0
                or payload["inode"] != root_stat.st_ino
                or not isinstance(payload["filesystem_type"], str)
                or not payload["filesystem_type"]
                or payload["filesystem_type"] != filesystem_type
            ):
                raise ValueError("approval does not bind current root/filesystem guarantees")
            return SharedFilesystemApproval(
                payload["approval_id"],
                payload["root_realpath"],
                payload["device"],
                payload["inode"],
                payload["filesystem_type"],
            )
        except StorageUnsupportedError:
            raise
        except (KeyError, TypeError, ValueError, json.JSONDecodeError, OSError) as error:
            raise StorageUnsupportedError(f"Invalid shared-FS approval: {error}") from error
        finally:
            if descriptor is not None:
                os.close(descriptor)


def reject_database_symlinks(path: str | os.PathLike[str]) -> None:
    """Refuse a store whose database file or existing SQLite side file is a symlink.

    A link can point at a file on another filesystem, so checking only the parent
    directory would not bind the database SQLite actually opens. Dangling links count.
    """
    name = os.fspath(path)
    for candidate in (name, f"{name}-wal", f"{name}-shm", f"{name}-journal"):
        try:
            metadata = os.lstat(candidate)
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(metadata.st_mode):
            raise StorageUnsupportedError(
                f"durable store files must not be symlinks; {candidate} is a symlink"
            )


def preflight_store(
    path: str | os.PathLike[str],
    *,
    shared_fs_approval_path: str | os.PathLike[str] | None = None,
    filesystem_detector: FilesystemDetector | None = None,
) -> None:
    """Refuse a store path that opening it would refuse, without creating anything.

    Runs the same symlink and filesystem checks as ``SqliteWorkItemStore.open``
    on the absolute store path.  A missing path is checked as-is: its existing
    prefix decides the filesystem, and nothing is created or opened.
    """
    absolute = os.path.abspath(os.fspath(path))
    reject_database_symlinks(absolute)
    select_journal_mode(
        os.path.dirname(absolute),
        shared_fs_approval_path=shared_fs_approval_path,
        filesystem_detector=filesystem_detector,
    )


def select_journal_mode(
    directory: str | os.PathLike[str],
    *,
    shared_fs_approval_path: str | os.PathLike[str] | None = None,
    filesystem_detector: FilesystemDetector | None = None,
) -> str:
    """Return ``wal`` for a local filesystem, or ``delete`` under a verified approval."""
    detector = filesystem_detector or detect_filesystem_type
    if shared_fs_approval_path is None:
        assert_local_wal_filesystem(directory, detector)
        return "wal"
    ApprovalVerifier().verify(
        shared_fs_approval_path,
        root=Path(directory),
        filesystem_type=detector(directory),
    )
    return "delete"
