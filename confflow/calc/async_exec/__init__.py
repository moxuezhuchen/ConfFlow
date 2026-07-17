#!/usr/bin/env python3
"""Async executor wrapper for calc tasks - Phase 1a.

Provides AsyncTaskExecutor that wraps ProcessPoolExecutor with:
- Per-task timeout enforcement.
- Exponential-backoff retry for transient failures.
- Progress callback integration.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass
from typing import Any, cast

from ..task_execution import _classify_future_exception

__all__ = [
    "AsyncTaskExecutor",
    "RetryConfig",
    "TaskResult",
]


@dataclass
class RetryConfig:
    """Configuration for transient-error retry behaviour."""

    max_retries: int = 2
    base_delay: float = 2.0
    max_delay: float = 60.0
    jitter: bool = True
    retry_on: tuple[str, ...] = ("abnormal_termination", "exec_error")

    def backoff(self, attempt: int) -> float:
        """Return delay in seconds for the given attempt (0-based)."""
        delay = min(self.base_delay * (2 ** attempt), self.max_delay)
        if self.jitter:
            import random
            jitter: float = 0.5 + random.random()
            delay = delay * jitter
        return cast(float, delay)


@dataclass
class TaskResult:
    """Normalised task execution result."""

    job_name: str
    status: str
    error: str | None = None
    error_kind: str | None = None
    final_coords: Any = None
    retries: int = 0
    elapsed_seconds: float = 0.0
    raw_result: dict[str, Any] | None = None


def _classify_result_error(result: dict[str, Any]) -> str | None:
    val = result.get("error_kind", "")
    return str(val) if val else None


class AsyncTaskExecutor:
    """ProcessPoolExecutor wrapper with timeout and retry support.

    Parameters
    ----------
    max_workers : int
        Maximum number of parallel worker processes.
    timeout_seconds : float | None
        Per-task wall-time limit. None disables.
    retry_config : RetryConfig | None
        Retry policy; None disables retries.
    progress_callback : callable | None
        Optional (job_name, status) -> None called after each task result.
    """

    def __init__(
        self,
        max_workers: int = 4,
        timeout_seconds: float | None = 900.0,
        retry_config: RetryConfig | None = None,
        progress_callback: Callable[[str, str], None] | None = None,
    ) -> None:
        self.max_workers = max_workers
        self.timeout_seconds = timeout_seconds
        self.retry_config = retry_config or RetryConfig(max_retries=0)
        self.progress_callback = progress_callback
        self._executor: ProcessPoolExecutor | None = None

    def __enter__(self) -> AsyncTaskExecutor:
        self._executor = ProcessPoolExecutor(max_workers=self.max_workers)
        return self

    def __exit__(self, *args: Any) -> None:
        if self._executor is not None:
            self._executor.__exit__(*args)
            self._executor = None

    def submit(
        self,
        run_task_fn: Callable[..., dict[str, Any]],
        task_payload: dict[str, Any],
    ) -> TaskResult:
        """Run a single task with timeout and retry. Returns TaskResult (never raises)."""
        job_name = task_payload.get("job_name", "?")
        start = time.monotonic()
        attempt = 0

        while True:
            assert self._executor is not None, "must be used as context manager"
            future = self._executor.submit(run_task_fn, task_payload)
            try:
                raw_result: dict[str, Any] = future.result(timeout=self.timeout_seconds)
                elapsed = time.monotonic() - start
                status = raw_result.get("status", "unknown")
                error_kind = _classify_result_error(raw_result)
                transient = (
                    error_kind in self.retry_config.retry_on if error_kind else False
                )
                can_retry = (
                    status in {"failed", "canceled"}
                    and transient
                    and attempt < self.retry_config.max_retries
                )
                if can_retry:
                    attempt += 1
                    delay = self.retry_config.backoff(attempt - 1)
                    future = None
                    time.sleep(delay)
                    continue
                if attempt > 0:
                    raw_result["retries"] = attempt
                if self.progress_callback:
                    self.progress_callback(job_name, status)
                return TaskResult(
                    job_name=job_name,
                    status=status,
                    error=raw_result.get("error"),
                    error_kind=error_kind,
                    final_coords=raw_result.get("final_coords"),
                    retries=attempt,
                    elapsed_seconds=elapsed,
                    raw_result=raw_result,
                )
            except TimeoutError:
                elapsed = time.monotonic() - start
                if self.progress_callback:
                    self.progress_callback(job_name, "failed")
                return TaskResult(
                    job_name=job_name,
                    status="failed",
                    error=f"Task {job_name} exceeded timeout {self.timeout_seconds}s",
                    error_kind="exec_error",
                    final_coords=None,
                    retries=attempt,
                    elapsed_seconds=elapsed,
                    raw_result={
                        "job_name": job_name,
                        "status": "failed",
                        "error": f"Task {job_name} exceeded timeout {self.timeout_seconds}s",
                        "error_kind": "exec_error",
                        "final_coords": None,
                    },
                )
            except Exception as e:  # noqa: BLE001
                elapsed = time.monotonic() - start
                error_kind = _classify_future_exception(e)
                if self.progress_callback:
                    self.progress_callback(job_name, "failed")
                return TaskResult(
                    job_name=job_name,
                    status="failed",
                    error=f"Worker exception: {e}",
                    error_kind=error_kind,
                    final_coords=None,
                    retries=attempt,
                    elapsed_seconds=elapsed,
                    raw_result={
                        "job_name": job_name,
                        "status": "failed",
                        "error": f"Worker exception: {e}",
                        "error_kind": error_kind,
                        "final_coords": None,
                    },
                )

    def map(
        self,
        run_task_fn: Callable[..., dict[str, Any]],
        payloads: list[dict[str, Any]],
    ) -> list[TaskResult]:
        """Run multiple tasks in parallel, returning a list of TaskResults."""
        return [self.submit(run_task_fn, p) for p in payloads]
