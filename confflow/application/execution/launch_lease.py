"""Crash-safe per-token leases for external execution workers."""

from __future__ import annotations

import json
import os
import stat
from pathlib import Path

try:
    import fcntl as _fcntl
except ImportError:  # pragma: no cover - the worker is POSIX-only
    _fcntl = None  # type: ignore[assignment]


class TokenLaunchLease:
    """Hold one kernel lease for a queued producer launch token.

    The file remains as a diagnostic marker, while the POSIX advisory lock is
    held for the lifetime of the worker. A competing process attaches without
    starting another executor; a crashed process releases the lock in the
    kernel so a queued token can be retried safely.
    """

    def __init__(self, runs_root: str | Path, run_id: str, token: str) -> None:
        if not _safe_component(run_id) or not _safe_component(token):
            raise ValueError("worker lease identifiers contain unsafe characters")
        self._path = Path(runs_root) / f"run_{run_id}" / f"control-worker.claim.{token}"
        self._fd: int | None = None
        self._previous_owner: dict[str, object] | None = None

    @property
    def path(self) -> Path:
        """Return the diagnostic lease marker path."""
        return self._path

    @property
    def previous_owner(self) -> dict[str, object] | None:
        """Return the last worker identity recorded before this claim."""
        return None if self._previous_owner is None else dict(self._previous_owner)

    def acquire(self) -> bool:
        """Acquire the token lease, returning false for a live competing owner."""
        if self._fd is not None:
            return True
        _validate_private_directory(self._path.parent.parent)
        self._path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        _validate_private_directory(self._path.parent)
        nofollow = getattr(os, "O_NOFOLLOW", 0)
        flags = os.O_RDWR | os.O_CREAT | nofollow
        if _fcntl is None:
            flags |= os.O_EXCL
        try:
            fd = os.open(self._path, flags, 0o600)
        except FileExistsError:
            return False
        metadata = os.fstat(fd)
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_uid != os.getuid():
            os.close(fd)
            raise ValueError("worker lease marker must be an owner-owned regular file")
        if metadata.st_mode & (stat.S_IWGRP | stat.S_IWOTH | stat.S_IRGRP | stat.S_IROTH):
            os.fchmod(fd, 0o600)
        if _fcntl is not None:
            try:
                _fcntl.flock(fd, _fcntl.LOCK_EX | _fcntl.LOCK_NB)
            except OSError:
                os.close(fd)
                return False
        os.lseek(fd, 0, os.SEEK_SET)
        existing = os.read(fd, 64 * 1024)
        if existing:
            try:
                marker = json.loads(existing.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                marker = None
            self._previous_owner = marker if isinstance(marker, dict) else {}
        else:
            self._previous_owner = None
        payload = json.dumps(
            {
                "run_id": self._path.parent.name[4:],
                "token": self._path.name.removeprefix("control-worker.claim."),
                "pid": os.getpid(),
                "pgid": os.getpgid(0) if hasattr(os, "getpgid") else None,
                # Recovery is only safe when the worker was launched in its
                # own process session.  A normal shell launch can otherwise
                # leave the supervisor's process group in the marker and
                # make a later worker unable to distinguish a live sibling.
                "isolated_session": bool(
                    hasattr(os, "getsid") and os.getsid(0) == os.getpid()
                ),
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        os.ftruncate(fd, 0)
        remaining = memoryview(payload)
        while remaining:
            written = os.write(fd, remaining)
            if written <= 0:
                os.close(fd)
                raise OSError("worker lease marker write made no progress")
            remaining = remaining[written:]
        os.fsync(fd)
        self._fd = fd
        return True

    def release(self) -> None:
        """Release the lease without deleting its audit marker."""
        fd, self._fd = self._fd, None
        if fd is None:
            return
        if _fcntl is not None:
            try:
                _fcntl.flock(fd, _fcntl.LOCK_UN)
            except OSError:
                pass
        os.close(fd)

    def __enter__(self) -> TokenLaunchLease:
        if not self.acquire():
            raise RuntimeError("worker launch token is already claimed")
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        del exc_type, exc_value, traceback
        self.release()


def _safe_component(value: str) -> bool:
    allowed = frozenset("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789._-")
    return bool(value) and value[0].isalnum() and all(char in allowed for char in value)


def _validate_private_directory(path: Path) -> None:
    metadata = os.lstat(path)
    if (
        not stat.S_ISDIR(metadata.st_mode)
        or stat.S_ISLNK(metadata.st_mode)
        or metadata.st_uid != os.getuid()
        or stat.S_IMODE(metadata.st_mode) != 0o700
    ):
        raise ValueError("worker lease directory must be owner-private and non-symlink")


__all__ = ["TokenLaunchLease"]
