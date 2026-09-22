"""Calculation executor protocol and the local Popen-backed default.

Phase 1 of the JobDesk-ConfFlow integration extracts subprocess.Popen out of
``confflow.calc.components.executor._run_calculation_step`` behind a Protocol so
that alternative executors (e.g. ``WslCalcExecutor`` in JobDesk) can be wired
without monkeypatching the helper directly.

The defaults intentionally preserve the existing behaviour of
``_run_calculation_step``: stdout/stderr are routed to ``{job_name}.log`` and
``{job_name}.err``, exit code is read from ``Popen.returncode``, and the policy's
``parse_output`` is invoked on the log path. Callers that need finer control
should pass a custom executor implementation via ``TaskRunner.run(..., executor=...)``
or the ``executor_cls`` argument of ``execute_tasks``.
"""

from __future__ import annotations

import os
import signal
import subprocess
import threading
import time
from dataclasses import dataclass, field
from math import isfinite
from typing import Any, Protocol

from ..core.exceptions import CalculationExecutionError
from .psutil_compat import maybe_import_psutil, psutil_exception_types

_psutil = maybe_import_psutil()
_PSUTIL_ERRORS = psutil_exception_types(_psutil)
_PSUTIL_GONE_ERRORS = tuple(
    error
    for error in (
        getattr(_psutil, "NoSuchProcess", None),
        getattr(_psutil, "ZombieProcess", None),
    )
    if isinstance(error, type)
)
_SIGKILL = getattr(signal, "SIGKILL", None)


class CalcCancellationError(CalculationExecutionError):
    """Cancellation could not prove that every calculation process stopped."""

    def __init__(self, message: str, *, confirmed: bool = False) -> None:
        super().__init__(message)
        self.confirmed = confirmed


def _popen_process_boundary_kwargs() -> dict[str, Any]:
    """Return subprocess options that isolate a local calculation.

    ``start_new_session`` is the POSIX boundary we own.  Windows has no
    ``setsid`` equivalent, but ``CREATE_NEW_PROCESS_GROUP`` gives the
    executor a private console process group when the platform exposes it.
    Unknown platforms retain the old launch behaviour and rely on psutil's
    child-process fallback during cancellation.
    """
    if os.name == "posix":
        return {"start_new_session": True}
    if os.name == "nt":
        creation_flag = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        if creation_flag:
            return {"creationflags": creation_flag}
    return {}


def _positive_int(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        return None
    return value


def _safe_process_group_id(pid: int | None) -> int | None:
    if pid is None or not hasattr(os, "getpgid"):
        return None
    try:
        return _positive_int(os.getpgid(pid))
    except OSError:
        return None


def _safe_session_id(pid: int | None) -> int | None:
    if pid is None or not hasattr(os, "getsid"):
        return None
    try:
        return _positive_int(os.getsid(pid))
    except OSError:
        return None


def _safe_create_time(pid: int | None) -> float | None:
    if pid is None or _psutil is None:
        return None
    try:
        return float(_psutil.Process(pid).create_time())
    except _PSUTIL_ERRORS:
        return None


@dataclass
class CalcHandle:
    """Opaque handle returned by ``CalcExecutor.submit``.

    Attributes
    ----------
    job_name : str
        Step identifier (matches ``{job_name}.log`` / ``{job_name}.err``).
    work_dir : str
        Absolute path to the working directory used for the calculation.
    submitted_at : float
        Wall-clock timestamp (seconds since the epoch) at submit time.
    executor_data : dict[str, Any]
        Implementation-private payload; must be JSON-friendly for executors that
        need to resume across processes. ``LocalCalcExecutor`` stores the live
        ``subprocess.Popen`` here.
    """

    job_name: str
    work_dir: str
    submitted_at: float
    executor_data: dict[str, Any] = field(default_factory=dict)


@dataclass
class CalcStatus:
    """Result of polling a running calculation."""

    is_terminal: bool
    succeeded: bool
    exit_code: int | None = None
    error: str | None = None


class CalcExecutor(Protocol):
    """Protocol for any subprocess driving a calculation step.

    Implementations MUST treat the work directory as the process CWD and
    write the policy log to ``{work_dir}/{job_name}.{policy.log_ext}``.
    """

    def submit(
        self,
        work_dir: str,
        job_name: str,
        policy: Any,
        coords: Any,
        config: dict[str, Any],
        cmd: list[str],
        env: dict[str, str] | None,
    ) -> CalcHandle: ...

    def is_terminal(self, handle: CalcHandle) -> bool: ...

    def succeeded(self, handle: CalcHandle) -> bool: ...

    def error(self, handle: CalcHandle) -> str | None: ...

    def cancel(self, handle: CalcHandle) -> None: ...

    def fetch_output(
        self,
        handle: CalcHandle,
        log: str,
        config: dict[str, Any],
        is_sp_task: bool = False,
    ) -> dict[str, Any]: ...

    def poll(self, handle: CalcHandle) -> CalcStatus: ...


class LocalCalcExecutor:
    """Default executor: spawn the calculation locally via ``subprocess.Popen``.

    A local calculation owns a dedicated process boundary. POSIX uses a new
    session/process group and psutil supplements it for descendants that
    deliberately create a new session. Polling therefore remains nonterminal
    while a calculation child survives its parent.
    """

    def __init__(self, terminate_timeout: float = 2.0, kill_timeout: float = 2.0) -> None:
        for name, value in (
            ("terminate_timeout", terminate_timeout),
            ("kill_timeout", kill_timeout),
        ):
            if not isinstance(value, (int, float)) or not isfinite(float(value)) or value < 0:
                raise ValueError(f"{name} must be a finite non-negative number")
        self.terminate_timeout = float(terminate_timeout)
        self.kill_timeout = float(kill_timeout)
        self._handles: dict[str, subprocess.Popen] = {}
        self._lock = threading.RLock()

    def __getstate__(self) -> dict[str, Any]:
        """Keep a fresh executor pickleable for ProcessPool task dispatch."""
        state = self.__dict__.copy()
        state["_handles"] = {}
        state["_lock"] = None
        return state

    def __setstate__(self, state: dict[str, Any]) -> None:
        self.__dict__.update(state)
        self._lock = threading.RLock()

    def submit(
        self,
        work_dir: str,
        job_name: str,
        policy: Any,
        coords: Any,
        config: dict[str, Any],
        cmd: list[str],
        env: dict[str, str] | None,
    ) -> CalcHandle:
        log_path = os.path.join(work_dir, f"{job_name}.{policy.log_ext}")
        err_path = os.path.join(work_dir, f"{job_name}.err")
        out = open(log_path, "w", encoding="utf-8")
        err = open(err_path, "w", encoding="utf-8")
        try:
            proc = subprocess.Popen(
                cmd,
                cwd=work_dir,
                env=env,
                stdout=out,
                stderr=err,
                text=True,
                **_popen_process_boundary_kwargs(),
            )
        except OSError as exc:
            out.close()
            err.close()
            raise RuntimeError(f"Failed to launch {cmd!r}: {exc}") from exc
        pid = _positive_int(getattr(proc, "pid", None))
        process_create_time = _safe_create_time(pid)
        self._handles[job_name] = proc
        return CalcHandle(
            job_name=job_name,
            work_dir=work_dir,
            submitted_at=time.time(),
            executor_data={
                "_proc": proc,
                "_stdout": out,
                "_stderr": err,
                "_policy": policy,
                "_pid": pid,
                "_pgid": (_safe_process_group_id(pid) or pid) if os.name == "posix" else None,
                "_sid": (_safe_session_id(pid) or pid) if os.name == "posix" else None,
                "_create_time": process_create_time,
                "_known_processes": ({pid: process_create_time} if pid is not None else {}),
                "_cancel_confirmed": False,
            },
        )

    @staticmethod
    def _close_streams(handle: CalcHandle) -> None:
        for key in ("_stdout", "_stderr"):
            stream = handle.executor_data.pop(key, None)
            if stream is not None:
                try:
                    stream.close()
                except OSError:
                    pass

    @staticmethod
    def _known_processes(handle: CalcHandle) -> dict[int, float | None]:
        known = handle.executor_data.get("_known_processes")
        if not isinstance(known, dict):
            known = {}
            handle.executor_data["_known_processes"] = known
        return known

    @classmethod
    def _record_process(cls, handle: CalcHandle, process: Any) -> None:
        pid = _positive_int(getattr(process, "pid", None))
        if pid is None or pid == os.getpid():
            return
        try:
            create_time = float(process.create_time()) if _psutil is not None else None
        except _PSUTIL_ERRORS:
            create_time = None
        if _psutil is not None and create_time is None:
            # A PID without an identity token is unsafe to signal individually;
            # a controlled process group can still be handled by PGID/session.
            return
        cls._known_processes(handle)[pid] = create_time

    @staticmethod
    def _same_process_identity(process: Any, expected_create_time: float | None) -> bool | None:
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
        try:
            if not bool(process.is_running()):
                return False
            return bool(process.status() != getattr(_psutil, "STATUS_ZOMBIE", "zombie"))
        except _PSUTIL_GONE_ERRORS:
            return False
        except _PSUTIL_ERRORS:
            # An inaccessible process is treated as live so cancellation
            # cannot claim that its boundary stopped without proof.
            return True

    def _refresh_process_boundary(self, handle: CalcHandle) -> None:
        """Remember descendants before the calculation root can disappear."""
        if _psutil is None:
            return
        root_pid = _positive_int(handle.executor_data.get("_pid"))
        if root_pid is not None:
            try:
                proc = handle.executor_data.get("_proc")
                if handle.executor_data.get("_create_time") is None and (
                    proc is None or proc.poll() is not None
                ):
                    return
                root = _psutil.Process(root_pid)
                if (
                    self._same_process_identity(root, handle.executor_data.get("_create_time"))
                    is True
                ):
                    self._record_process(handle, root)
                    for child in root.children(recursive=True):
                        self._record_process(handle, child)
            except _PSUTIL_ERRORS:
                pass

        # The executor only treats the controlled session/group and descendants
        # captured from its root as live. Work-directory scanning belongs to
        # worker crash recovery, where it deliberately fails closed; using it
        # here would make an unrelated process block or alter cancellation.

    def _group_is_safe_to_signal(self, handle: CalcHandle) -> bool:
        """Prove the stored PGID still belongs to this isolated calculation."""
        if os.name != "posix":
            return False
        pgid = _positive_int(handle.executor_data.get("_pgid"))
        sid = _positive_int(handle.executor_data.get("_sid"))
        if pgid is None or sid is None or pgid <= 1:
            return False
        try:
            if pgid == os.getpgrp() or sid == os.getsid(0):
                return False
        except OSError:
            return False

        root_pid = _positive_int(handle.executor_data.get("_pid"))
        if root_pid is not None:
            try:
                proc = handle.executor_data.get("_proc")
                if handle.executor_data.get("_create_time") is None and (
                    proc is None or proc.poll() is not None
                ):
                    return False
                if _psutil is not None:
                    root = _psutil.Process(root_pid)
                    identity = self._same_process_identity(
                        root, handle.executor_data.get("_create_time")
                    )
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

        # If the root is gone, a known live descendant or a non-leader member
        # of the same session can authorize killpg. This prevents a recycled
        # PGID from reaching an unrelated task or the supervising worker.
        known = self._known_processes(handle)
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

        # The root may have exited before the first poll, so its child was
        # never present in ``known``. A non-leader member of the original
        # session/group is still unambiguously inside the boundary. Requiring
        # pid != sid avoids authorizing a newly reused session leader.
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
                    except (OSError, *_PSUTIL_ERRORS):
                        continue
            except _PSUTIL_ERRORS:
                pass

        return False

    def _live_boundary_processes(
        self, handle: CalcHandle, *, root_reaped: bool = False
    ) -> set[int]:
        """Return live PIDs still owned by this calculation boundary."""
        live: set[int] = set()
        proc = handle.executor_data.get("_proc")
        if proc is not None and not root_reaped:
            try:
                if proc.poll() is None:
                    root_pid = _positive_int(handle.executor_data.get("_pid"))
                    live.add(root_pid if root_pid is not None else -1)
            except (OSError, AttributeError):
                live.add(-1)

        if _psutil is None:
            pgid = _positive_int(handle.executor_data.get("_pgid"))
            if pgid is not None and os.name == "posix":
                try:
                    os.killpg(pgid, 0)
                except ProcessLookupError:
                    pass
                except OSError:
                    # Permission or an unknown probe result is not proof of
                    # termination, so keep the boundary live.
                    live.add(-2)
                else:
                    live.add(-2)
            return live

        if handle.executor_data.get("_create_time") is None and os.name == "posix":
            pgid = _positive_int(handle.executor_data.get("_pgid"))
            if pgid is not None:
                try:
                    os.killpg(pgid, 0)
                except ProcessLookupError:
                    pass
                except OSError:
                    live.add(-2)
                else:
                    # Without the root's identity token we cannot safely
                    # signal a reaped root's group or declare it stopped.
                    live.add(-2)

        known = self._known_processes(handle)
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
                # AccessDenied and similar errors are an unverified live
                # boundary, never evidence that this PID disappeared.
                live.add(pid)

        # Group members are included even if they changed their working
        # directory. The group is safe to inspect only after the identity check
        # above succeeds.
        if self._group_is_safe_to_signal(handle):
            pgid = _positive_int(handle.executor_data.get("_pgid"))
            sid = _positive_int(handle.executor_data.get("_sid"))
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
                            # A member may exit between enumeration and getsid.
                            continue
                        except (OSError, *_PSUTIL_ERRORS):
                            # An inaccessible member is not proof that the
                            # group is empty; fail closed for liveness.
                            live.add(-3)
                except _PSUTIL_ERRORS:
                    live.add(-3)
        return live

    def _wait_for_boundary(self, handle: CalcHandle, timeout: float, *, root_reaped: bool) -> bool:
        # Use perf_counter so callers' wall-time accounting can patch
        # time.monotonic without changing this independent cleanup deadline.
        deadline = time.perf_counter() + timeout
        while True:
            self._refresh_process_boundary(handle)
            if not self._live_boundary_processes(handle, root_reaped=root_reaped):
                return True
            remaining = deadline - time.perf_counter()
            if remaining <= 0:
                return False
            # Event.wait is independent of the calculation polling sleep;
            # callers frequently patch time.sleep when exercising timeout
            # handling and cleanup must remain bounded in that case too.
            threading.Event().wait(min(0.05, remaining))

    def _signal_known_descendants(
        self, handle: CalcHandle, sig: int, *, force: bool = False
    ) -> None:
        if _psutil is None:
            return
        root_pid = _positive_int(handle.executor_data.get("_pid"))
        for raw_pid, expected_create_time in list(self._known_processes(handle).items()):
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

    def _signal_boundary(self, handle: CalcHandle, sig: int, *, force: bool = False) -> None:
        """Signal the private process group and descendants outside that group."""
        group_signalled = False
        can_signal_group = (os.name == "posix" and not force) or (
            os.name == "posix" and force and _SIGKILL is not None
        )
        if can_signal_group and self._group_is_safe_to_signal(handle):
            pgid = _positive_int(handle.executor_data.get("_pgid"))
            if pgid is not None:
                try:
                    os.killpg(pgid, _SIGKILL if force else sig)
                    group_signalled = True
                except ProcessLookupError:
                    pass
                except PermissionError as exc:
                    raise CalcCancellationError(
                        f"permission denied while signalling calculation process group {pgid}"
                    ) from exc
                except OSError as exc:
                    raise CalcCancellationError(
                        f"failed to signal calculation process group {pgid}: {exc}"
                    ) from exc

        self._signal_known_descendants(handle, sig, force=force)
        proc = handle.executor_data.get("_proc")
        if proc is not None and not group_signalled:
            try:
                if proc.poll() is None:
                    if force:
                        proc.kill()
                    else:
                        proc.terminate()
            except ProcessLookupError:
                pass
            except (OSError, AttributeError) as exc:
                raise CalcCancellationError("failed to signal calculation process") from exc

    def is_terminal(self, handle: CalcHandle) -> bool:
        with self._lock:
            proc = handle.executor_data.get("_proc")
            if proc is None:
                return True
            self._refresh_process_boundary(handle)
            if proc.poll() is not None:
                if self._live_boundary_processes(handle, root_reaped=True):
                    return False
                self._close_streams(handle)
                return True
            return False

    def succeeded(self, handle: CalcHandle) -> bool:
        with self._lock:
            proc = handle.executor_data.get("_proc")
            if proc is None:
                return False
            return bool(
                proc.returncode == 0 and handle.executor_data.get("_cancel_confirmed") is not True
            )

    def error(self, handle: CalcHandle) -> str | None:
        with self._lock:
            proc = handle.executor_data.get("_proc")
            if proc is None or proc.returncode is None:
                return None
            if proc.returncode == 0:
                return None
            return f"subprocess exited with code {proc.returncode}"

    def cancel(self, handle: CalcHandle) -> None:
        with self._lock:
            proc = handle.executor_data.get("_proc")
            if proc is None or handle.executor_data.get("_cancel_confirmed") is True:
                return

            self._refresh_process_boundary(handle)
            try:
                self._signal_boundary(handle, signal.SIGTERM)
            except CalcCancellationError as exc:
                handle.executor_data["_cancel_error"] = str(exc)
                self._close_streams(handle)
                raise
            if self._wait_for_boundary(handle, self.terminate_timeout, root_reaped=False):
                try:
                    # A real Popen.wait return proves the root has been
                    # reaped. For test doubles this is also the only reliable
                    # root-state acknowledgement available to us.
                    proc.wait(timeout=self.kill_timeout)
                except subprocess.TimeoutExpired as exc:
                    handle.executor_data["_cancel_error"] = "calculation parent was not reaped"
                    self._close_streams(handle)
                    raise CalcCancellationError(
                        "calculation cancellation could not reap the parent process"
                    ) from exc
                except OSError as exc:
                    handle.executor_data["_cancel_error"] = "calculation parent wait failed"
                    self._close_streams(handle)
                    raise CalcCancellationError(
                        "calculation cancellation could not reap the parent process"
                    ) from exc
                handle.executor_data["_cancel_confirmed"] = True
                self._close_streams(handle)
                return

            try:
                self._signal_boundary(handle, signal.SIGTERM, force=True)
            except CalcCancellationError as exc:
                handle.executor_data["_cancel_error"] = str(exc)
                self._close_streams(handle)
                raise
            try:
                proc.wait(timeout=self.kill_timeout)
            except subprocess.TimeoutExpired as exc:
                handle.executor_data["_cancel_error"] = "calculation process boundary is still live"
                self._close_streams(handle)
                raise CalcCancellationError(
                    "calculation cancellation could not confirm that all processes stopped"
                ) from exc
            except OSError as exc:
                handle.executor_data["_cancel_error"] = "calculation parent wait failed"
                self._close_streams(handle)
                raise CalcCancellationError(
                    "calculation cancellation could not confirm that all processes stopped"
                ) from exc
            if self._wait_for_boundary(handle, self.kill_timeout, root_reaped=True):
                handle.executor_data["_cancel_confirmed"] = True
                self._close_streams(handle)
                return
            handle.executor_data["_cancel_error"] = "calculation process boundary is still live"
            self._close_streams(handle)
            raise CalcCancellationError(
                "calculation cancellation could not confirm that all processes stopped"
            )

    def fetch_output(
        self,
        handle: CalcHandle,
        log: str,
        config: dict[str, Any],
        is_sp_task: bool = False,
    ) -> dict[str, Any]:
        from .setup import get_itask

        policy = handle.executor_data.get("_policy")
        if policy is None:
            from .policies import get_policy
            from .setup import parse_iprog

            policy = get_policy(parse_iprog(config))
        return policy.parse_output(log, config, is_sp_task=is_sp_task or get_itask(config) == 1)

    def poll(self, handle: CalcHandle) -> CalcStatus:
        with self._lock:
            proc = handle.executor_data.get("_proc")
            if proc is None:
                return CalcStatus(is_terminal=True, succeeded=False, error="missing handle")
            self._refresh_process_boundary(handle)
            rc = proc.poll()
            if rc is None:
                return CalcStatus(is_terminal=False, succeeded=False)
            if self._live_boundary_processes(handle, root_reaped=True):
                return CalcStatus(is_terminal=False, succeeded=False)
            self._close_streams(handle)
            return CalcStatus(
                is_terminal=True,
                succeeded=rc == 0,
                exit_code=rc,
                error=None if rc == 0 else f"subprocess exited with code {rc}",
            )


__all__ = [
    "CalcCancellationError",
    "CalcExecutor",
    "CalcHandle",
    "CalcStatus",
    "LocalCalcExecutor",
]
