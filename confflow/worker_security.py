"""Security-boundary helpers for the producer-owned control worker."""

from __future__ import annotations

import hashlib
import json
import os
import stat
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .application.execution.state_root import StateRoot


def _safe_absolute_path(value: object, label: str) -> str:
    if not isinstance(value, str) or not value.startswith("/") or "\\" in value:
        raise ValueError(f"{label} must be an absolute POSIX path")
    path = PurePosixPath(value)
    if ".." in path.parts:
        raise ValueError(f"{label} must not contain parent traversal")
    return path.as_posix()


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
