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
import subprocess
import time
from dataclasses import dataclass, field
from typing import Any, Protocol


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

    Mirrors the legacy behaviour: the subprocess inherits stdout/stderr, exit
    code is read from ``Popen.returncode`` and the policy is used to parse the
    log file in place. ``poll`` derives a :class:`CalcStatus` from the cached
    ``Popen`` object so the same handle can be observed from a different thread
    or process without holding the popen handle directly.
    """

    def __init__(self) -> None:
        self._handles: dict[str, subprocess.Popen] = {}

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
            proc = subprocess.Popen(cmd, cwd=work_dir, env=env, stdout=out, stderr=err, text=True)
        except OSError as exc:
            out.close()
            err.close()
            raise RuntimeError(f"Failed to launch {cmd!r}: {exc}") from exc
        self._handles[job_name] = proc
        return CalcHandle(
            job_name=job_name,
            work_dir=work_dir,
            submitted_at=time.time(),
            executor_data={"_proc": proc, "_stdout": out, "_stderr": err, "_policy": policy},
        )

    @staticmethod
    def _close_streams(handle: CalcHandle) -> None:
        for key in ("_stdout", "_stderr"):
            stream = handle.executor_data.pop(key, None)
            if stream is not None:
                stream.close()

    def is_terminal(self, handle: CalcHandle) -> bool:
        proc = handle.executor_data.get("_proc")
        if proc is None:
            return True
        is_terminal = proc.poll() is not None
        if is_terminal:
            self._close_streams(handle)
        return is_terminal

    def succeeded(self, handle: CalcHandle) -> bool:
        proc = handle.executor_data.get("_proc")
        if proc is None:
            return False
        return proc.returncode == 0

    def error(self, handle: CalcHandle) -> str | None:
        proc = handle.executor_data.get("_proc")
        if proc is None or proc.returncode is None:
            return None
        if proc.returncode == 0:
            return None
        return f"subprocess exited with code {proc.returncode}"

    def cancel(self, handle: CalcHandle) -> None:
        proc = handle.executor_data.get("_proc")
        if proc is None:
            return
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=5)
            finally:
                self._close_streams(handle)

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
        proc = handle.executor_data.get("_proc")
        if proc is None:
            return CalcStatus(is_terminal=True, succeeded=False, error="missing handle")
        rc = proc.poll()
        if rc is None:
            return CalcStatus(is_terminal=False, succeeded=False)
        self._close_streams(handle)
        return CalcStatus(
            is_terminal=True,
            succeeded=rc == 0,
            exit_code=rc,
            error=None if rc == 0 else f"subprocess exited with code {rc}",
        )


__all__ = ["CalcExecutor", "CalcHandle", "CalcStatus", "LocalCalcExecutor"]
