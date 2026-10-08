#!/usr/bin/env python3
"""V4-owned native process boundary for launching supervised programs.

Spawns an argv vector without a shell and supervises it with proof the whole
boundary stopped; carries no workflow/program/policy/config knowledge.
``submit``/``poll``/``cancel``/``collect`` own handles, quiet checks, proven
cancellation, and reaping; ``cancel`` is SIGTERM, bounded grace, then SIGKILL,
reporting ``confirmed=True`` only when liveness probes prove the boundary
dead. Timeout policy stays with the caller; ``collect`` never synthesizes it
and reports wall time from a monotonic stamp. Isolation is POSIX
session/process-group with pid plus creation-time tokens, never signalling
the supervisor's own group; a boundary with live descendants stays
nonterminal; stdout/stderr go to work-dir files.

Concurrency: a global ``RLock`` guards only the handle registry structure
(lookup, insert, tombstone install, removal). Each handle owns a
per-handle ``RLock`` that serializes ``poll``/``cancel``/``collect`` for
that same handle, so one handle never observes another operation's
half-updated record state. The SIGTERM grace wait and the SIGKILL reap
wait run without holding the global lock, so cancelling one handle never
blocks ``poll`` on another handle. A proven cancellation installs a
lightweight tombstone (exit code plus names, no process or streams) that
keeps answering ``cancel`` with ``"already cancelled"`` and stays
collectible; ``collect`` drops the tombstone as it would a live record.
"""

from __future__ import annotations

import logging
import os
import signal
import subprocess
import threading
import time
from dataclasses import dataclass, field
from math import isfinite
from typing import Any, TextIO

from .native import (
    CancelOutcome,
    NativeExecutionRequest,
    NativeExecutionResult,
    NativeHandle,
    NativeStatus,
)

__all__ = [
    "NativeProcessError",
    "NativeProcessSupervisor",
]

_LOG = logging.getLogger(__name__)


def _maybe_import_psutil() -> Any:
    """Import psutil when available, otherwise return None."""
    try:
        import psutil
    except ImportError:
        return None
    return psutil


_psutil = _maybe_import_psutil()


def _psutil_error_types() -> tuple[type[BaseException], ...]:
    """Return psutil-related exception types, tolerant of a missing psutil."""
    base: tuple[type[BaseException], ...] = (AttributeError, OSError, RuntimeError)
    err_type = getattr(_psutil, "Error", None)
    if isinstance(err_type, type) and issubclass(err_type, BaseException):
        return (err_type, *base)
    return base


_PSUTIL_ERRORS = _psutil_error_types()
_PSUTIL_GONE_ERRORS: tuple[type[BaseException], ...] = tuple(
    error
    for error in (
        getattr(_psutil, "NoSuchProcess", None),
        getattr(_psutil, "ZombieProcess", None),
    )
    if isinstance(error, type)
)
_OS_PSUTIL_ERRORS: tuple[type[BaseException], ...] = (OSError, *_PSUTIL_ERRORS)
_SIGKILL: Any = getattr(signal, "SIGKILL", None)

_DETAIL_ALREADY_TERMINAL = "already terminal"
_DETAIL_ALREADY_CANCELLED = "already cancelled"
_DETAIL_CANCELLED_AFTER_SIGTERM = "cancelled: boundary stopped after SIGTERM"
_DETAIL_CANCELLED_AFTER_SIGKILL = "cancelled: boundary stopped after SIGKILL"
_DETAIL_PARENT_NOT_REAPED = "parent process was not reaped"
_DETAIL_BOUNDARY_STILL_LIVE = "process boundary is still live"


class NativeProcessError(Exception):
    """Report an inoperable native process boundary."""


def _popen_process_boundary_kwargs() -> dict[str, Any]:
    """Return subprocess options that isolate a launched native process."""
    if os.name == "posix":
        return {"start_new_session": True}
    if os.name == "nt":
        creation_flag = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        if creation_flag:
            return {"creationflags": creation_flag}
    return {}


def _positive_int(value: object) -> int | None:
    """Return ``value`` when it is a positive int, otherwise None."""
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        return None
    return value


def _safe_process_group_id(pid: int | None) -> int | None:
    """Return the process group id for ``pid``, or None when unavailable."""
    if pid is None or not hasattr(os, "getpgid"):
        return None
    try:
        return _positive_int(os.getpgid(pid))
    except OSError:
        return None


def _safe_session_id(pid: int | None) -> int | None:
    """Return the session id for ``pid``, or None when unavailable."""
    if pid is None or not hasattr(os, "getsid"):
        return None
    try:
        return _positive_int(os.getsid(pid))
    except OSError:
        return None


def _safe_create_time(pid: int | None) -> float | None:
    """Return the psutil creation time for ``pid``, or None when unavailable."""
    if pid is None or _psutil is None:
        return None
    try:
        return float(_psutil.Process(pid).create_time())
    except _PSUTIL_ERRORS:
        return None


@dataclass
class _ProcessRecord:
    """Supervisor-private mutable state for one submitted native process."""

    proc: subprocess.Popen[str]
    stdout_stream: TextIO | None
    stderr_stream: TextIO | None
    stdout_name: str
    stderr_name: str
    work_dir: str
    pid: int | None
    process_group_id: int | None
    session_id: int | None
    create_time: float | None
    known_processes: dict[int, float | None] = field(default_factory=dict)
    submitted_at: float = 0.0
    started_monotonic: float = 0.0
    cancel_confirmed: bool = False
    lock: threading.RLock = field(default_factory=threading.RLock, compare=False)


@dataclass
class _CancelledTombstone:
    """Lightweight marker retained after a proven cancellation.

    Holds only what ``cancel``/``poll``/``collect`` still promise: the
    reaped exit code, the stream file names, and the monotonic start stamp
    for wall-time accounting. The process object, streams, and boundary
    census are released, so a cancelled-never-collected handle costs
    almost nothing while ``cancel`` still reports ``"already cancelled"``.
    """

    exit_code: int | None
    stdout_name: str
    stderr_name: str
    started_monotonic: float


class NativeProcessSupervisor:
    """Launch argv vectors without a shell and supervise their boundary."""

    def __init__(
        self,
        terminate_timeout: float = 2.0,
        kill_timeout: float = 2.0,
        poll_interval: float = 0.05,
    ) -> None:
        """Create a supervisor with bounded termination waits."""
        for name, value in (
            ("terminate_timeout", terminate_timeout),
            ("kill_timeout", kill_timeout),
            ("poll_interval", poll_interval),
        ):
            if not isinstance(value, (int, float)) or not isfinite(float(value)) or value < 0:
                raise ValueError(f"{name} must be a finite non-negative number")
        self.terminate_timeout = float(terminate_timeout)
        self.kill_timeout = float(kill_timeout)
        self.poll_interval = float(poll_interval)
        self._records: dict[str, _ProcessRecord | _CancelledTombstone] = {}
        self._sequence = 0
        self._lock = threading.RLock()

    def _live_record_for(self, key: str) -> _ProcessRecord | _CancelledTombstone:
        """Return the registry entry for ``key``, failing closed when gone."""
        record = self._records.get(key)
        if record is None:
            raise NativeProcessError(f"unknown native handle key {key!r}")
        return record

    def _install_tombstone(self, key: str, record: _ProcessRecord, exit_code: int | None) -> None:
        """Replace a proven-cancelled record with its lightweight tombstone."""
        tombstone = _CancelledTombstone(
            exit_code=exit_code,
            stdout_name=record.stdout_name,
            stderr_name=record.stderr_name,
            started_monotonic=record.started_monotonic,
        )
        with self._lock:
            if self._records.get(key) is record:
                self._records[key] = tombstone

    def submit(self, request: NativeExecutionRequest) -> NativeHandle:
        """Launch the native process described by ``request``.

        Parameters
        ----------
        request : NativeExecutionRequest
            Launch vector.  ``argv`` must be non-empty, ``work_dir`` is
            created when missing, the environment is passed through as
            strings, and stdout/stderr are routed to files in ``work_dir``.

        Returns
        -------
        NativeHandle
            Opaque identity of the launched process, keyed
            ``np:<pid>:<submitted_ns>:<sequence>``.

        Raises
        ------
        NativeProcessError
            Raised for an empty argv, a non-string environment value, an
            unusable work directory or stream file, an unknown executable,
            or any spawn ``OSError``.
        """
        argv = tuple(request.argv)
        if not argv or any(not isinstance(part, str) for part in argv):
            raise NativeProcessError("argv must be a non-empty tuple of strings")
        work_dir = request.work_dir
        if not work_dir or not isinstance(work_dir, str):
            raise NativeProcessError("work_dir must be a non-empty string")
        for label, name in (
            ("stdout_file", request.stdout_file),
            ("stderr_file", request.stderr_file),
        ):
            if not name or not isinstance(name, str):
                raise NativeProcessError(f"{label} must be a non-empty string")
        try:
            os.makedirs(work_dir, exist_ok=True)
        except OSError as exc:
            raise NativeProcessError(f"cannot create work directory {work_dir!r}: {exc}") from exc
        env: dict[str, str] = {}
        for key, value in request.env.items():
            if not isinstance(value, str):
                raise NativeProcessError(f"env value for {key!r} must be a string")
            env[key] = value
        stdout_path = os.path.join(work_dir, request.stdout_file)
        stderr_path = os.path.join(work_dir, request.stderr_file)
        try:
            stdout_stream = open(stdout_path, "w", encoding="utf-8")
        except OSError as exc:
            raise NativeProcessError(f"cannot open stdout file {stdout_path!r}: {exc}") from exc
        try:
            stderr_stream = open(stderr_path, "w", encoding="utf-8")
        except OSError as exc:
            stdout_stream.close()
            raise NativeProcessError(f"cannot open stderr file {stderr_path!r}: {exc}") from exc
        try:
            proc: subprocess.Popen[str] = subprocess.Popen(
                list(argv),
                cwd=work_dir,
                env=env,
                stdout=stdout_stream,
                stderr=stderr_stream,
                text=True,
                **_popen_process_boundary_kwargs(),
            )
        except OSError as exc:
            stdout_stream.close()
            stderr_stream.close()
            raise NativeProcessError(f"failed to launch {argv[0]!r}: {exc}") from exc
        pid = _positive_int(getattr(proc, "pid", None))
        create_time = _safe_create_time(pid)
        pgid = (_safe_process_group_id(pid) or pid) if os.name == "posix" else None
        sid = (_safe_session_id(pid) or pid) if os.name == "posix" else None
        submitted_at = time.time()
        with self._lock:
            submitted_ns = time.time_ns()
            self._sequence += 1
            key = f"np:{pid}:{submitted_ns}:{self._sequence}"
            self._records[key] = _ProcessRecord(
                proc=proc,
                stdout_stream=stdout_stream,
                stderr_stream=stderr_stream,
                stdout_name=request.stdout_file,
                stderr_name=request.stderr_file,
                work_dir=work_dir,
                pid=pid,
                process_group_id=pgid,
                session_id=sid,
                create_time=create_time,
                known_processes={pid: create_time} if pid is not None else {},
                submitted_at=submitted_at,
                started_monotonic=time.monotonic(),
            )
        _LOG.debug("submitted native process %s as %s", argv[0], key)
        return NativeHandle(
            key=key,
            pid=pid,
            process_group_id=pgid,
            session_id=sid,
            create_time=create_time,
            submitted_at=submitted_at,
        )

    def poll(self, handle: NativeHandle) -> NativeStatus:
        """Return the current status of the native process.

        A process whose root exited but whose boundary still has live
        descendants reports nonterminal with ``exit_code`` None.  A quiet
        boundary reports terminal with the root exit code.

        Parameters
        ----------
        handle : NativeHandle
            Handle previously returned by :meth:`submit`.

        Returns
        -------
        NativeStatus
            The current poll observation.

        Raises
        ------
        NativeProcessError
            Raised when ``handle.key`` addresses no known process.
        """
        with self._lock:
            record = self._live_record_for(handle.key)
            if isinstance(record, _CancelledTombstone):
                return NativeStatus(is_terminal=True, exit_code=record.exit_code)
            record_lock = record.lock
        # Observation runs under the per-handle lock without the global
        # lock, so other handles never block on this poll.
        with record_lock:
            with self._lock:
                record = self._live_record_for(handle.key)
                if isinstance(record, _CancelledTombstone):
                    return NativeStatus(is_terminal=True, exit_code=record.exit_code)
            self._refresh_process_boundary(record)
            return_code = record.proc.poll()
            if return_code is None:
                return NativeStatus(is_terminal=False, exit_code=None)
            if self._live_boundary_processes(record, root_reaped=True):
                return NativeStatus(is_terminal=False, exit_code=None)
            self._close_record_streams(record)
            _LOG.debug("native process %s reached terminal state %s", handle.key, return_code)
            return NativeStatus(is_terminal=True, exit_code=return_code)

    def cancel(self, handle: NativeHandle, *, grace_seconds: float = 2.0) -> CancelOutcome:
        """Request termination and prove whether the boundary stopped.

        Signals ``SIGTERM`` to the boundary, waits ``grace_seconds`` for it to
        go quiet, then escalates to ``SIGKILL`` and waits the supervisor's kill
        window.  ``confirmed`` is true only when liveness probes prove the
        whole boundary stopped; an unconfirmed stop must never trigger
        recovery, restart, or duplicate execution.

        Detail vocabulary is stable: ``"already cancelled"``,
        ``"already terminal"``, ``"cancelled: boundary stopped after
        SIGTERM"``, ``"cancelled: boundary stopped after SIGKILL"``,
        ``"parent process was not reaped"``, ``"process boundary is still
        live"``.

        Parameters
        ----------
        handle : NativeHandle
            Handle previously returned by :meth:`submit`.
        grace_seconds : float
            Bounded seconds to wait after ``SIGTERM`` before escalating.
            Must be finite and non-negative.

        Returns
        -------
        CancelOutcome
            The proven verdict of the cancellation request.

        Raises
        ------
        NativeProcessError
            Raised when ``handle.key`` addresses no known process, or when
            signalling the boundary fails at the OS level.
        ValueError
            Raised when ``grace_seconds`` is not a finite non-negative number.
        """
        if (
            not isinstance(grace_seconds, (int, float))
            or not isfinite(float(grace_seconds))
            or grace_seconds < 0
        ):
            raise ValueError("grace_seconds must be a finite non-negative number")
        with self._lock:
            record = self._live_record_for(handle.key)
            if isinstance(record, _CancelledTombstone) or record.cancel_confirmed:
                return CancelOutcome(confirmed=True, detail=_DETAIL_ALREADY_CANCELLED)
            record_lock = record.lock
        # Only the per-handle lock is held across the bounded waits below;
        # the global registry lock is taken for short lookups and the
        # tombstone install, so cancelling one handle never blocks polls
        # on other handles while same-handle polls stay serialized here.
        with record_lock:
            with self._lock:
                record = self._live_record_for(handle.key)
                if isinstance(record, _CancelledTombstone) or record.cancel_confirmed:
                    return CancelOutcome(confirmed=True, detail=_DETAIL_ALREADY_CANCELLED)
            self._refresh_process_boundary(record)
            proc = record.proc
            if proc.poll() is not None and not self._live_boundary_processes(
                record, root_reaped=True
            ):
                self._close_record_streams(record)
                return CancelOutcome(confirmed=True, detail=_DETAIL_ALREADY_TERMINAL)
            self._signal_boundary(record, signal.SIGTERM)
            if self._wait_for_boundary(record, float(grace_seconds), root_reaped=False):
                try:
                    proc.wait(timeout=self.kill_timeout)
                except subprocess.TimeoutExpired:
                    self._close_record_streams(record)
                    _LOG.debug("cancel of %s could not reap the parent", handle.key)
                    return CancelOutcome(confirmed=False, detail=_DETAIL_PARENT_NOT_REAPED)
                except OSError as exc:
                    self._close_record_streams(record)
                    raise NativeProcessError(
                        f"failed while reaping native process {handle.key!r}: {exc}"
                    ) from exc
                record.cancel_confirmed = True
                exit_code = proc.poll()
                self._close_record_streams(record)
                self._install_tombstone(handle.key, record, exit_code)
                _LOG.debug("cancel of %s confirmed after SIGTERM", handle.key)
                return CancelOutcome(confirmed=True, detail=_DETAIL_CANCELLED_AFTER_SIGTERM)
            self._signal_boundary(record, signal.SIGTERM, force=True)
            try:
                proc.wait(timeout=self.kill_timeout)
            except subprocess.TimeoutExpired:
                self._close_record_streams(record)
                _LOG.debug("cancel of %s could not reap the parent", handle.key)
                return CancelOutcome(confirmed=False, detail=_DETAIL_BOUNDARY_STILL_LIVE)
            except OSError as exc:
                self._close_record_streams(record)
                raise NativeProcessError(
                    f"failed while reaping native process {handle.key!r}: {exc}"
                ) from exc
            if self._wait_for_boundary(record, self.kill_timeout, root_reaped=True):
                record.cancel_confirmed = True
                exit_code = proc.poll()
                self._close_record_streams(record)
                self._install_tombstone(handle.key, record, exit_code)
                _LOG.debug("cancel of %s confirmed after SIGKILL", handle.key)
                return CancelOutcome(confirmed=True, detail=_DETAIL_CANCELLED_AFTER_SIGKILL)
            self._close_record_streams(record)
            _LOG.debug("cancel of %s left a live boundary", handle.key)
            return CancelOutcome(confirmed=False, detail=_DETAIL_BOUNDARY_STILL_LIVE)

    @staticmethod
    def _tombstone_result(record: _CancelledTombstone) -> NativeExecutionResult:
        """Build the stored outcome of a proven-cancelled handle."""
        return NativeExecutionResult(
            exit_code=record.exit_code,
            wall_time_seconds=time.monotonic() - record.started_monotonic,
            timed_out=False,
            stdout_file=record.stdout_name,
            stderr_file=record.stderr_name,
        )

    def collect(self, handle: NativeHandle) -> NativeExecutionResult:
        """Reap a terminal process and return its observed outcome.

        Wall time is measured from the monotonic submit stamp.  Timeout
        accounting stays with the caller (walltime plus ``cancel``), so the
        result always reports ``timed_out=False``.  Handle state is pruned;
        collecting the same handle twice reports an unknown handle.

        Parameters
        ----------
        handle : NativeHandle
            Handle previously returned by :meth:`submit`.

        Returns
        -------
        NativeExecutionResult
            The observed outcome of the terminal process.

        Raises
        ------
        NativeProcessError
            Raised when ``handle.key`` addresses no known process, or when
            the boundary is not terminal yet.
        """
        with self._lock:
            record = self._live_record_for(handle.key)
            if isinstance(record, _CancelledTombstone):
                result = self._tombstone_result(record)
                del self._records[handle.key]
                _LOG.debug("collected native process %s with exit %s", handle.key, record.exit_code)
                return result
            record_lock = record.lock
        # Collection observes under the per-handle lock without the global
        # lock, so other handles never block on this collect.
        with record_lock:
            with self._lock:
                record = self._live_record_for(handle.key)
                if isinstance(record, _CancelledTombstone):
                    result = self._tombstone_result(record)
                    del self._records[handle.key]
                    _LOG.debug(
                        "collected native process %s with exit %s",
                        handle.key,
                        record.exit_code,
                    )
                    return result
            self._refresh_process_boundary(record)
            return_code = record.proc.poll()
            if return_code is None:
                raise NativeProcessError(
                    f"cannot collect {handle.key!r}: native process is still running"
                )
            if self._live_boundary_processes(record, root_reaped=True):
                raise NativeProcessError(
                    f"cannot collect {handle.key!r}: {_DETAIL_BOUNDARY_STILL_LIVE}"
                )
            wall_time = time.monotonic() - record.started_monotonic
            result = NativeExecutionResult(
                exit_code=return_code,
                wall_time_seconds=wall_time,
                timed_out=False,
                stdout_file=record.stdout_name,
                stderr_file=record.stderr_name,
            )
            self._close_record_streams(record)
            with self._lock:
                if self._records.get(handle.key) is record:
                    del self._records[handle.key]
            _LOG.debug("collected native process %s with exit %s", handle.key, return_code)
            return result

    @staticmethod
    def _close_record_streams(record: _ProcessRecord) -> None:
        """Close captured stdout/stderr streams, tolerating earlier closes."""
        for attr in ("stdout_stream", "stderr_stream"):
            stream = getattr(record, attr)
            setattr(record, attr, None)
            if stream is not None:
                try:
                    stream.close()
                except OSError:
                    pass

    @classmethod
    def _record_process(cls, record: _ProcessRecord, process: Any) -> None:
        """Remember one boundary member keyed by pid plus identity token."""
        pid = _positive_int(getattr(process, "pid", None))
        if pid is None or pid == os.getpid():
            return
        try:
            create_time = float(process.create_time()) if _psutil is not None else None
        except _PSUTIL_ERRORS:
            create_time = None
        if _psutil is not None and create_time is None:
            return
        record.known_processes[pid] = create_time

    @staticmethod
    def _same_process_identity(process: Any, expected_create_time: float | None) -> bool | None:
        """Check a pid still names the process seen at submit time.

        Returns True for a proven match, False for a proven PID reuse, and
        None when identity cannot be verified (fail closed for signalling,
        fail live for liveness).
        """
        if expected_create_time is None:
            return True
        try:
            current = float(process.create_time())
        except _PSUTIL_GONE_ERRORS:
            return False
        except _PSUTIL_ERRORS:
            return None
        return abs(current - expected_create_time) < 1e-6

    @staticmethod
    def _process_is_live(process: Any) -> bool:
        """Return whether a psutil process handle is observably alive."""
        try:
            if not bool(process.is_running()):
                return False
            return bool(process.status() != getattr(_psutil, "STATUS_ZOMBIE", "zombie"))
        except _PSUTIL_GONE_ERRORS:
            return False
        except _PSUTIL_ERRORS:
            return True

    def _refresh_process_boundary(self, record: _ProcessRecord) -> None:
        """Remember descendants before the native root can disappear."""
        if _psutil is None:
            return
        root_pid = _positive_int(record.pid)
        if root_pid is not None:
            try:
                if record.create_time is None and record.proc.poll() is not None:
                    return
                root = _psutil.Process(root_pid)
                if self._same_process_identity(root, record.create_time) is True:
                    self._record_process(record, root)
                    for child in root.children(recursive=True):
                        self._record_process(record, child)
            except _PSUTIL_ERRORS:
                pass

    def _group_is_safe_to_signal(self, record: _ProcessRecord) -> bool:
        """Prove the stored group still belongs to this isolated boundary."""
        if os.name != "posix":
            return False
        pgid = _positive_int(record.process_group_id)
        sid = _positive_int(record.session_id)
        if pgid is None or sid is None or pgid <= 1:
            return False
        try:
            if pgid == os.getpgrp() or sid == os.getsid(0):
                return False
        except OSError:
            return False

        root_pid = _positive_int(record.pid)
        if root_pid is not None:
            try:
                if record.create_time is None and record.proc.poll() is not None:
                    return False
                if _psutil is not None:
                    root = _psutil.Process(root_pid)
                    identity = self._same_process_identity(root, record.create_time)
                    if identity is None:
                        return False
                    if identity is False:
                        return False
                    if os.getpgid(root_pid) == pgid and os.getsid(root_pid) == sid:
                        return True
                elif os.getpgid(root_pid) == pgid and os.getsid(root_pid) == sid:
                    return True
            except ProcessLookupError:
                pass
            except OSError:
                pass
            except _PSUTIL_GONE_ERRORS:
                pass
            except _PSUTIL_ERRORS:
                return False

        known = record.known_processes
        for raw_pid, expected_create_time in list(known.items()):
            pid = _positive_int(raw_pid)
            if pid is None or pid == root_pid:
                continue
            if _psutil is not None:
                try:
                    process = _psutil.Process(pid)
                    identity = self._same_process_identity(process, expected_create_time)
                    if not self._process_is_live(process) or identity is not True:
                        continue
                except _PSUTIL_ERRORS:
                    continue
            try:
                if os.getpgid(pid) == pgid and os.getsid(pid) == sid:
                    return True
            except OSError:
                continue

        if _psutil is not None:
            try:
                for process in _psutil.process_iter(["pid", "status"]):
                    pid = _positive_int(getattr(process, "pid", None))
                    if pid is None or pid == os.getpid() or pid == sid:
                        continue
                    try:
                        if process.info.get("status") == getattr(
                            _psutil, "STATUS_ZOMBIE", "zombie"
                        ):
                            continue
                        if os.getpgid(pid) == pgid and os.getsid(pid) == sid:
                            return True
                    except _OS_PSUTIL_ERRORS:
                        continue
            except _PSUTIL_ERRORS:
                pass

        return False

    def _live_boundary_processes(
        self, record: _ProcessRecord, *, root_reaped: bool = False
    ) -> set[int]:
        """Return live pids still owned by this native boundary."""
        live: set[int] = set()
        if not root_reaped:
            try:
                if record.proc.poll() is None:
                    live.add(record.pid if record.pid is not None else -1)
            except (OSError, AttributeError):
                live.add(-1)

        if _psutil is None:
            pgid = _positive_int(record.process_group_id)
            if pgid is not None and os.name == "posix":
                try:
                    os.killpg(pgid, 0)
                except ProcessLookupError:
                    pass
                except OSError:
                    live.add(-2)
                else:
                    live.add(-2)
            return live

        if record.create_time is None and os.name == "posix":
            pgid = _positive_int(record.process_group_id)
            if pgid is not None:
                try:
                    os.killpg(pgid, 0)
                except ProcessLookupError:
                    pass
                except OSError:
                    live.add(-2)
                else:
                    live.add(-2)

        known = record.known_processes
        for raw_pid, expected_create_time in list(known.items()):
            pid = _positive_int(raw_pid)
            if pid is None:
                continue
            try:
                process = _psutil.Process(pid)
                is_live = self._process_is_live(process)
                identity = self._same_process_identity(process, expected_create_time)
                if is_live and identity is True:
                    live.add(pid)
                elif is_live and identity is None:
                    live.add(pid)
                elif not is_live:
                    known.pop(pid, None)
                elif identity is False:
                    known.pop(pid, None)
            except _PSUTIL_GONE_ERRORS:
                known.pop(pid, None)
            except _PSUTIL_ERRORS:
                live.add(pid)

        if self._group_is_safe_to_signal(record):
            pgid = _positive_int(record.process_group_id)
            sid = _positive_int(record.session_id)
            if pgid is not None:
                try:
                    for process in _psutil.process_iter(["pid", "status"]):
                        pid = _positive_int(getattr(process, "pid", None))
                        if pid is None or pid == os.getpid():
                            continue
                        try:
                            if process.info.get("status") == getattr(
                                _psutil, "STATUS_ZOMBIE", "zombie"
                            ):
                                continue
                            if os.getpgid(pid) == pgid and (sid is None or os.getsid(pid) == sid):
                                live.add(pid)
                        except ProcessLookupError:
                            continue
                        except _OS_PSUTIL_ERRORS:
                            live.add(-3)
                except _PSUTIL_ERRORS:
                    live.add(-3)
        return live

    def _wait_for_boundary(
        self, record: _ProcessRecord, timeout: float, *, root_reaped: bool
    ) -> bool:
        """Wait up to ``timeout`` seconds for the boundary to go quiet."""
        deadline = time.perf_counter() + timeout
        while True:
            self._refresh_process_boundary(record)
            if not self._live_boundary_processes(record, root_reaped=root_reaped):
                return True
            remaining = deadline - time.perf_counter()
            if remaining <= 0:
                return False
            threading.Event().wait(min(self.poll_interval, remaining))

    def _signal_known_descendants(
        self, record: _ProcessRecord, sig: int, *, force: bool = False
    ) -> None:
        """Signal remembered descendants outside the process group."""
        if _psutil is None:
            return
        root_pid = _positive_int(record.pid)
        for raw_pid, expected_create_time in list(record.known_processes.items()):
            pid = _positive_int(raw_pid)
            if pid is None or pid == root_pid or pid == os.getpid():
                continue
            try:
                process = _psutil.Process(pid)
                if (
                    not self._process_is_live(process)
                    or self._same_process_identity(process, expected_create_time) is not True
                ):
                    continue
                if force:
                    process.kill()
                else:
                    process.send_signal(sig)
            except _PSUTIL_ERRORS:
                continue

    def _signal_boundary(self, record: _ProcessRecord, sig: int, *, force: bool = False) -> None:
        """Signal the private process group plus descendants outside it."""
        group_signalled = False
        can_signal_group = (os.name == "posix" and not force) or (
            os.name == "posix" and force and _SIGKILL is not None
        )
        if can_signal_group and self._group_is_safe_to_signal(record):
            pgid = _positive_int(record.process_group_id)
            if pgid is not None:
                try:
                    os.killpg(pgid, _SIGKILL if force else sig)
                    group_signalled = True
                except ProcessLookupError:
                    pass
                except PermissionError as exc:
                    raise NativeProcessError(
                        f"permission denied while signalling native process group {pgid}"
                    ) from exc
                except OSError as exc:
                    raise NativeProcessError(
                        f"failed to signal native process group {pgid}: {exc}"
                    ) from exc

        self._signal_known_descendants(record, sig, force=force)
        proc = record.proc
        if not group_signalled:
            try:
                if proc.poll() is None:
                    if force:
                        proc.kill()
                    else:
                        proc.terminate()
            except ProcessLookupError:
                pass
            except (OSError, AttributeError) as exc:
                raise NativeProcessError("failed to signal native process") from exc
