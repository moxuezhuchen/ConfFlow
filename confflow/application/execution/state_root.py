"""Private state-root validation and layout for durable execution aggregates."""

from __future__ import annotations

import os
import stat
from dataclasses import dataclass
from pathlib import Path

from .errors import ErrorCode, ExecutionServiceError


@dataclass(frozen=True)
class RunPaths:
    """Private staging and workflow directories for one durable run ID."""

    staging: Path
    work: Path


@dataclass(frozen=True)
class StateRoot:
    """Validated owner-private root that contains only the versioned repository layout."""

    path: Path

    @classmethod
    def resolve(cls, value: str | Path, *, expected_uid: int | None = None) -> StateRoot:
        """Validate an existing explicit non-home non-root state root."""
        raw = Path(value).expanduser()
        if not raw.is_absolute():
            raise _unavailable("State root must be an absolute path")
        if raw.is_symlink():
            raise _unavailable("State root must not be a symlink")
        _validate_ancestors(raw)
        try:
            path = raw.resolve(strict=True)
        except OSError as error:
            raise _unavailable(f"Cannot resolve state root: {error}", retryable=True) from error
        if not path.is_dir() or path == Path("/") or path == Path.home().resolve():
            raise _unavailable("State root must be an existing non-home non-root directory")
        uid = _current_uid() if expected_uid is None else expected_uid
        _validate_private_directory(path, uid)
        return cls(path)

    @property
    def version_dir(self) -> Path:
        """Return the private v1 state directory, creating it with owner-only mode."""
        return _ensure_private_components(self.path, ("v1",), _current_uid())

    @property
    def database_path(self) -> Path:
        """Return the only supported durable database path."""
        return self.version_dir / "repository.sqlite3"

    def ensure_run_paths(self, run_id: str) -> RunPaths:
        """Create and return owner-private run-local staging and work directories."""
        if not _is_run_id(run_id):
            raise _unavailable("Invalid run ID for state-root layout")
        uid = _current_uid()
        prefix = ("v1", "runs", run_id)
        _ensure_private_components(self.path, prefix, uid)
        staging = _ensure_private_components(self.path, (*prefix, "staging"), uid)
        work = _ensure_private_components(self.path, (*prefix, "work"), uid)
        return RunPaths(staging=staging, work=work)


def _ensure_private_components(base: Path, components: tuple[str, ...], uid: int) -> Path:
    """Create and open every managed component relative to a no-follow directory fd."""
    directory_flag = getattr(os, "O_DIRECTORY", 0)
    nofollow_flag = getattr(os, "O_NOFOLLOW", 0)
    if not directory_flag or not nofollow_flag:
        raise _unavailable("Secure state paths require O_DIRECTORY and O_NOFOLLOW")
    flags = os.O_RDONLY | directory_flag | nofollow_flag
    current = base
    descriptor: int | None = None
    try:
        descriptor = os.open(base, flags)
        _validate_directory_metadata(os.fstat(descriptor), uid)
        for component in components:
            try:
                os.mkdir(component, mode=0o700, dir_fd=descriptor)
            except FileExistsError:
                pass
            child = os.open(component, flags, dir_fd=descriptor)
            try:
                _validate_directory_metadata(os.fstat(child), uid)
            except Exception:
                os.close(child)
                raise
            os.close(descriptor)
            descriptor = child
            current /= component
        return current
    except ExecutionServiceError:
        raise
    except OSError as error:
        raise _unavailable(f"Cannot securely create state path: {error}", retryable=True) from error
    finally:
        if descriptor is not None:
            os.close(descriptor)


def _current_uid() -> int:
    """Return the POSIX owner identity or fail closed on unsupported hosts."""
    getuid = getattr(os, "getuid", None)
    if getuid is None:
        raise _unavailable("State-root ownership verification requires POSIX")
    return int(getuid())


def _validate_private_directory(path: Path, uid: int) -> None:
    """Reject foreign-owned or group/other-writable state directories."""
    try:
        metadata = path.stat()
    except OSError as error:
        raise _unavailable(f"Cannot stat state path: {error}", retryable=True) from error
    _validate_directory_metadata(metadata, uid)


def _validate_directory_metadata(metadata: os.stat_result, uid: int) -> None:
    """Validate one already-open managed directory without following another path lookup."""
    if not stat.S_ISDIR(metadata.st_mode):
        raise _unavailable("Managed state component must be a directory")
    if metadata.st_uid != uid:
        raise _unavailable("State root owner does not match the active owner")
    if stat.S_IMODE(metadata.st_mode) != 0o700:
        raise _unavailable("State root and managed directories must have mode 0700")


def _validate_ancestors(path: Path) -> None:
    """Reject unsafe ancestors until a system sticky directory is the trust boundary."""
    try:
        for parent in path.parents:
            if parent == Path("/"):
                return
            metadata = os.lstat(parent)
            if stat.S_ISLNK(metadata.st_mode):
                raise _unavailable("State-root ancestor must not be a symlink")
            mode = stat.S_IMODE(metadata.st_mode)
            writable = mode & (stat.S_IWGRP | stat.S_IWOTH)
            if writable and not (mode & stat.S_ISVTX):
                raise _unavailable("State-root ancestor must not be broadly writable")
            if writable and mode & stat.S_ISVTX:
                return
    except ExecutionServiceError:
        raise
    except OSError as error:
        raise _unavailable(f"Cannot validate state-root ancestors: {error}", retryable=True) from error


def _is_run_id(value: str) -> bool:
    """Match the frozen v1 run-ID grammar without importing service internals."""
    allowed = frozenset("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789._-")
    return bool(value) and len(value) <= 128 and value[0].isalnum() and all(char in allowed for char in value)


def _unavailable(message: str, *, retryable: bool = False) -> ExecutionServiceError:
    """Create the typed repository-boundary failure."""
    return ExecutionServiceError(ErrorCode.REPOSITORY_UNAVAILABLE, message, retryable=retryable)
