#!/usr/bin/env python3

"""Crash-safe per-attempt leases for V4 remote execution (V4-4).

One :class:`AttemptLease` binds a kernel advisory lock to a single work-item
attempt ``(run_id, step_id, work_item_id, attempt_number, launch_token)``.
The marker file remains as a diagnostic audit record, while the POSIX
advisory lock is held for the lifetime of the holder: a competing process
attaches without starting a duplicate boundary, and a crashed holder drops
the lock in the kernel so the same attempt token can be retried safely.

Duplicate-launch prevention covers two shapes:

- same attempt *and* token from another process: the marker path is
  identical, so the non-blocking exclusive lock fails and :meth:`acquire`
  returns ``False`` (attach path);
- same attempt but a *different* launch token: the marker path differs, so
  acquisition additionally probes sibling markers for the same attempt
  number under a short-lived per-attempt mutex.  A sibling whose lock is
  still held means the attempt is already owned and acquisition returns
  ``False``; a sibling whose lock is free is crash residue and is ignored.

Dependency rule: this module imports the standard library only at module
load.  It never imports ``confflow.execution``, ``confflow.workflow``,
``confflow.calc``, ``confflow.core``, or any legacy runtime; the locking
and validation algorithms it needs are ported (not imported) from the
legacy launch-lease implementation.
"""

from __future__ import annotations

import json
import os
import stat
import time
from pathlib import Path
from typing import Any, Final

__all__ = ["AttemptLease", "LeaseError"]

#: Characters allowed in every lease identity component.  ``work_item_id``
#: additionally allows ``':'`` (see :func:`_checked_component`); the marker
#: filename maps ``':'`` to ``'+'`` and ``'+'`` itself is never accepted, so
#: the mapping cannot collide.
_SAFE_CHARS: Final[frozenset[str]] = frozenset(
    "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789._-"
)

#: Maximum marker bytes read back when recovering the previous owner.
_MAX_MARKER_BYTES: Final[int] = 64 * 1024


class LeaseError(ValueError):
    """A lease identity component or marker is unsafe or unusable."""


def _checked_component(value: str, field_name: str, *, allow_colon: bool = False) -> str:
    """Validate *value* as a strict lease identity component.

    Parameters
    ----------
    value : str
        Candidate component text.
    field_name : str
        Field name used in the failure message.
    allow_colon : bool
        When True, ``':'`` is accepted as the single documented exception
        (used only for ``work_item_id``, whose structured identifiers
        contain ``':'`` separators).

    Returns
    -------
    str
        The validated component, unchanged.

    Raises
    ------
    LeaseError
        When *value* is empty, starts with a non-alphanumeric, or contains
        any character outside the allowed set.
    """
    allowed = _SAFE_CHARS | ({":"} if allow_colon else frozenset())
    if (
        not isinstance(value, str)
        or not value
        or not value[0].isalnum()
        or any(char not in allowed for char in value)
    ):
        raise LeaseError(f"lease {field_name} contains unsafe characters: {value!r}")
    return value


def _checked_attempt_number(value: int) -> int:
    """Validate *value* as a positive attempt number.

    Parameters
    ----------
    value : int
        Candidate attempt number.

    Returns
    -------
    int
        The validated attempt number.

    Raises
    ------
    LeaseError
        When *value* is not a positive int (bools are rejected explicitly).
    """
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise LeaseError(f"lease attempt_number must be a positive int: {value!r}")
    return value


def _sanitize_work_item_id(work_item_id: str) -> str:
    """Map a validated work-item id to a single safe filename segment.

    Parameters
    ----------
    work_item_id : str
        Already-validated work-item id (may contain ``':'``).

    Returns
    -------
    str
        The id with ``':'`` replaced by ``'+'``.  ``'+'`` can never appear
        in a validated id, so two distinct ids never map to one segment.
    """
    return work_item_id.replace(":", "+")


def _validate_owner_private_dir(path: Path) -> None:
    """Require *path* to be an owner-private non-symlink directory.

    Parameters
    ----------
    path : Path
        Directory to inspect with ``os.lstat`` (never following symlinks).

    Raises
    ------
    LeaseError
        When *path* is not a directory, is a symlink, is not owned by the
        current uid, or does not carry exactly mode ``0o700``.
    FileNotFoundError
        When *path* does not exist (callers create it, then re-validate).
    """
    metadata = os.lstat(path)
    if (
        not stat.S_ISDIR(metadata.st_mode)
        or stat.S_ISLNK(metadata.st_mode)
        or metadata.st_uid != os.getuid()
        or stat.S_IMODE(metadata.st_mode) != 0o700
    ):
        raise LeaseError(f"lease directory must be owner-private and non-symlink: {path}")


def _ensure_owner_private_dir(path: Path) -> None:
    """Create *path* when missing, then require owner-private ownership.

    Parameters
    ----------
    path : Path
        Directory to create (with exact mode ``0o700``) or validate.

    Raises
    ------
    LeaseError
        When an existing directory fails validation.
    """
    try:
        _validate_owner_private_dir(path)
        return
    except FileNotFoundError:
        pass
    try:
        os.mkdir(path, 0o700)
    except FileExistsError:
        _validate_owner_private_dir(path)
        return
    os.chmod(path, 0o700)
    _validate_owner_private_dir(path)


def _open_marker(path: Path, *, extra_flags: int = 0) -> int:
    """Open a lease marker without following symlinks, checking ownership.

    Parameters
    ----------
    path : Path
        Marker file to open (created with mode ``0o600`` when missing).
    extra_flags : int
        Additional ``os.open`` flags (for example ``os.O_EXCL`` for the
        no-``fcntl`` create fallback).

    Returns
    -------
    int
        The open file descriptor.

    Raises
    ------
    LeaseError
        When the opened file is not an owner-owned regular file.
    """
    nofollow = getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(path, os.O_RDWR | os.O_CREAT | nofollow | extra_flags, 0o600)
    try:
        metadata = os.fstat(fd)
    except OSError:
        os.close(fd)
        raise
    if not stat.S_ISREG(metadata.st_mode) or metadata.st_uid != os.getuid():
        os.close(fd)
        raise LeaseError(f"lease marker must be an owner-owned regular file: {path}")
    if metadata.st_mode & (stat.S_IWGRP | stat.S_IWOTH | stat.S_IRGRP | stat.S_IROTH):
        os.fchmod(fd, 0o600)
    return fd


def _try_nonblocking_lock(fd: int) -> bool:
    """Take an exclusive non-blocking lock on *fd* when locking exists.

    Parameters
    ----------
    fd : int
        Open marker file descriptor.

    Returns
    -------
    bool
        True when the lock is held (or when the platform has no ``fcntl``
        and the ``O_EXCL`` create fallback already proved exclusivity).
    """
    try:
        import fcntl as fcntl_mod
    except ImportError:
        return True
    try:
        fcntl_mod.flock(fd, fcntl_mod.LOCK_EX | fcntl_mod.LOCK_NB)
    except OSError:
        return False
    return True


def _read_marker_dict(fd: int) -> dict[str, Any] | None:
    """Read the previous JSON marker behind *fd* without trusting it.

    Parameters
    ----------
    fd : int
        Locked marker file descriptor positioned anywhere.

    Returns
    -------
    dict[str, Any] | None
        The previous marker mapping, ``{}`` for a present-but-unparseable
        marker, or ``None`` when the file is empty (first claim).
    """
    os.lseek(fd, 0, os.SEEK_SET)
    existing = os.read(fd, _MAX_MARKER_BYTES + 1)
    if not existing:
        return None
    try:
        marker = json.loads(existing[:_MAX_MARKER_BYTES].decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return {}
    return marker if isinstance(marker, dict) else {}


def _write_marker_dict(fd: int, payload: dict[str, Any]) -> None:
    """Replace the marker behind *fd* with *payload* and fsync it.

    Parameters
    ----------
    fd : int
        Locked marker file descriptor.
    payload : dict[str, Any]
        JSON-serializable audit record for the new owner.

    Raises
    ------
    OSError
        When the write makes no progress or fsync fails.
    """
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    os.ftruncate(fd, 0)
    remaining = memoryview(encoded)
    while remaining:
        written = os.write(fd, remaining)
        if written <= 0:
            raise OSError("lease marker write made no progress")
        remaining = remaining[written:]
    os.fsync(fd)


def _current_create_time() -> float | None:
    """Return this process's creation time, or ``None`` when unreadable.

    ``psutil`` is imported here (never at module load) so importing this
    module never pulls an optional third-party dependency; any failure
    degrades to ``None`` rather than failing acquisition.
    """
    try:
        import psutil
    except ImportError:
        return None
    try:
        return float(psutil.Process().create_time())
    except Exception:
        return None


class AttemptLease:
    """Hold one kernel lease for a single work-item attempt.

    Parameters
    ----------
    lease_root : str | Path
        Root directory holding the ``v4/`` lease tree.  Every level from
        *lease_root* down to the step directory is created with mode
        ``0o700`` when missing and must otherwise already be
        owner-private; anything else raises :class:`LeaseError`.
    run_id : str
        Run identifier (strict charset, leading alphanumeric).
    step_id : str
        Step identifier (strict charset, leading alphanumeric).
    work_item_id : str
        Work-item identifier.  The strict charset applies, with ``':'``
        allowed as the single documented exception; the marker filename
        maps ``':'`` to ``'+'``.
    attempt_number : int
        Positive attempt number.
    launch_token : str
        Launch token binding the claim to one controller (strict charset,
        leading alphanumeric; ``':'`` is not allowed here).

    The audit marker lives at
    ``<lease_root>/v4/<run_id>/<step_id>/<work-item>.attempt-<N>.<token>.json``.
    """

    def __init__(
        self,
        lease_root: str | Path,
        run_id: str,
        step_id: str,
        work_item_id: str,
        attempt_number: int,
        launch_token: str,
    ) -> None:
        self._run_id = _checked_component(run_id, "run_id")
        self._step_id = _checked_component(step_id, "step_id")
        self._work_item_id = _checked_component(work_item_id, "work_item_id", allow_colon=True)
        self._attempt_number = _checked_attempt_number(attempt_number)
        self._launch_token = _checked_component(launch_token, "launch_token")
        step_dir = Path(lease_root) / "v4" / self._run_id / self._step_id
        safe_item = _sanitize_work_item_id(self._work_item_id)
        stem = f"{safe_item}.attempt-{self._attempt_number}"
        self._step_dir = step_dir
        self._mutex_path = step_dir / f"{stem}.mutex"
        self._path = step_dir / f"{stem}.{self._launch_token}.json"
        self._fd: int | None = None
        self._previous_owner: dict[str, Any] | None = None

    @property
    def path(self) -> Path:
        """Return the diagnostic lease marker path."""
        return self._path

    @property
    def previous_owner(self) -> dict[str, Any] | None:
        """Return the last attempt identity recorded before this claim."""
        return None if self._previous_owner is None else dict(self._previous_owner)

    def acquire(self) -> bool:
        """Acquire the attempt lease, holding the kernel lock on success.

        Returns
        -------
        bool
            True when this instance now holds the attempt (the marker is
            rewritten and the lock is held until :meth:`release` or
            process exit).  False when the same attempt and token is
            already held elsewhere (attach path) or when the same attempt
            is already owned under a different launch token
            (duplicate-launch prevention).

        Raises
        ------
        LeaseError
            When a lease directory or marker is unsafe.
        """
        if self._fd is not None:
            return True
        for level in (
            self._step_dir.parent.parent.parent,
            self._step_dir.parent.parent,
            self._step_dir.parent,
            self._step_dir,
        ):
            _ensure_owner_private_dir(level)
        try:
            import fcntl as fcntl_mod
        except ImportError:
            fcntl_mod = None  # type: ignore[assignment]
        mutex_fd = _open_marker(self._mutex_path)
        try:
            if fcntl_mod is not None:
                fcntl_mod.flock(mutex_fd, fcntl_mod.LOCK_EX)
            if not self._claim_under_mutex(fcntl_mod is None):
                return False
        finally:
            if fcntl_mod is not None:
                try:
                    fcntl_mod.flock(mutex_fd, fcntl_mod.LOCK_UN)
                except OSError:
                    pass
            os.close(mutex_fd)
        return True

    def _claim_under_mutex(self, use_excl_fallback: bool) -> bool:
        """Probe sibling markers, then claim this token's marker.

        The caller holds the per-attempt mutex, so concurrent acquirers
        serialize here: a second acquirer always observes the first
        acquirer's held marker lock.

        Parameters
        ----------
        use_excl_fallback : bool
            When True the platform has no ``fcntl`` and exclusivity comes
            from ``O_EXCL`` creation instead of advisory locks.

        Returns
        -------
        bool
            True when this instance now holds its marker lock.
        """
        if self._sibling_attempt_owned():
            return False
        extra_flags = os.O_EXCL if use_excl_fallback else 0
        try:
            fd = _open_marker(self._path, extra_flags=extra_flags)
        except FileExistsError:
            return False
        if not _try_nonblocking_lock(fd):
            os.close(fd)
            return False
        self._previous_owner = _read_marker_dict(fd)
        payload: dict[str, Any] = {
            "run_id": self._run_id,
            "step_id": self._step_id,
            "work_item_id": self._work_item_id,
            "attempt_number": self._attempt_number,
            "launch_token": self._launch_token,
            "pid": os.getpid(),
            "pgid": os.getpgid(0) if hasattr(os, "getpgid") else None,
            "claimed_wall": time.time(),
        }
        create_time = _current_create_time()
        if create_time is not None:
            payload["create_time"] = create_time
        try:
            _write_marker_dict(fd, payload)
        except OSError:
            os.close(fd)
            raise
        self._fd = fd
        return True

    def _sibling_attempt_owned(self) -> bool:
        """Return whether the same attempt is held under another token.

        Every sibling marker (same work item and attempt number, different
        launch token) is probed with a non-blocking exclusive lock: a
        sibling that refuses the lock is still held by a live owner, while
        a sibling that grants it is crash residue and is ignored.  The
        freshest live sibling marker is kept as ``previous_owner`` so the
        refused acquisition still reports who owns the attempt.

        Returns
        -------
        bool
            True when a live sibling owner holds this attempt.
        """
        stem = f"{_sanitize_work_item_id(self._work_item_id)}.attempt-{self._attempt_number}"
        try:
            entries = sorted(self._step_dir.iterdir())
        except OSError:
            return False
        for entry in entries:
            if entry.name == self._path.name or entry.name == self._mutex_path.name:
                continue
            if not entry.name.startswith(stem + ".") or not entry.name.endswith(".json"):
                continue
            try:
                probe_fd = _open_marker(entry)
            except (LeaseError, OSError):
                # An unreadable sibling proves nothing about liveness, but
                # it also cannot prove the attempt is free; fail closed.
                if self._previous_owner is None:
                    self._previous_owner = {}
                return True
            try:
                if not _try_nonblocking_lock(probe_fd):
                    marker = _read_marker_unlocked(entry)
                    if isinstance(marker, dict) and marker:
                        self._previous_owner = marker
                    elif self._previous_owner is None:
                        self._previous_owner = {}
                    return True
            finally:
                _unlock_and_close(probe_fd)
        return False

    def release(self) -> None:
        """Release the lease without deleting its audit marker."""
        fd, self._fd = self._fd, None
        if fd is None:
            return
        _unlock_and_close(fd)

    def __enter__(self) -> AttemptLease:
        """Acquire the lease, raising :class:`LeaseError` when owned."""
        if not self.acquire():
            raise LeaseError("work-item attempt is already claimed")
        return self

    def __exit__(self, exc_type: object, exc_value: object, traceback: object) -> None:
        """Release the lease; never suppresses exceptions."""
        del exc_type, exc_value, traceback
        self.release()


def _read_marker_unlocked(path: Path) -> Any:
    """Best-effort read of a sibling marker without taking its lock."""
    try:
        with open(path, "rb") as handle:
            return json.loads(handle.read(_MAX_MARKER_BYTES).decode("utf-8"))
    except (OSError, UnicodeDecodeError, ValueError):
        return None


def _unlock_and_close(fd: int) -> None:
    """Release any advisory lock on *fd* and close it."""
    try:
        import fcntl as fcntl_mod
    except ImportError:
        fcntl_mod = None  # type: ignore[assignment]
    if fcntl_mod is not None:
        try:
            fcntl_mod.flock(fd, fcntl_mod.LOCK_UN)
        except OSError:
            pass
    os.close(fd)
