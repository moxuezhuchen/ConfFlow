#!/usr/bin/env python3
"""Phase 1c: Transient-retry patch for task_runner.py.

This module provides a RetryAwareTaskRunner class that extends TaskRunner
with exponential-backoff retry for transient failures.

Usage: Replace TaskRunner().run(...) with RetryAwareTaskRunner(retry_config).run(...)

Transient failures (retryable):
  - exec_error (e.g. g16 killed / resource unavailable)
  - abnormal_termination (nonzero exit / bad termination)

Non-transient failures (NOT retried):
  - input_error, parse_error, stop_requested, worker_exception
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, cast

from .components.task_runner import TaskRunner

__all__ = [
    "RetryAwareTaskRunner",
    "RetryConfig",
]


@dataclass
class RetryConfig:
    """Configuration for transient-error retry behaviour."""

    max_retries: int = 2
    base_delay: float = 2.0
    max_delay: float = 60.0
    jitter: bool = True
    retry_on: tuple[str, ...] = ("exec_error", "abnormal_termination")


@dataclass
class _RetryRecord:
    """Tracks retry history for a task."""

    attempts: int = 0
    errors: list[str] = field(default_factory=list)


class RetryAwareTaskRunner:
    """TaskRunner wrapper that retries transient failures with exponential backoff.

    Parameters
    ----------
    retry_config : RetryConfig
        Retry policy configuration.
    """

    TRANSIENT_KINDS = frozenset({"exec_error", "abnormal_termination"})

    def __init__(self, retry_config: RetryConfig | None = None) -> None:
        self._inner = TaskRunner()
        self._config = retry_config or RetryConfig()

    def run(self, task_info: Any) -> dict[str, Any]:
        """Run a task with transient-error retry.

        Returns the final result dict with additional fields:
          - retries (int): number of retry attempts made
          - retry_errors (list[str]): error messages from each failed attempt
        """
        record = _RetryRecord()

        while True:
            result = self._inner.run(task_info)
            status = result.get("status", "")

            if status in {"success", "skipped", "rescued"}:
                if record.attempts > 0:
                    result["retries"] = record.attempts
                    result["retry_errors"] = record.errors
                return cast(dict[str, Any], result)

            error_kind = result.get("error_kind", "")
            if error_kind not in self.TRANSIENT_KINDS:
                # Non-transient failure: do not retry
                if record.attempts > 0:
                    result["retries"] = record.attempts
                    result["retry_errors"] = record.errors
                return cast(dict[str, Any], result)

            if record.attempts >= self._config.max_retries:
                # Max retries exhausted
                if record.attempts > 0:
                    result["retries"] = record.attempts
                    result["retry_errors"] = record.errors
                result_out: dict[str, Any] = result
                return result_out

            # Transient: record error and retry with backoff
            record.attempts += 1
            error_msg = result.get("error", "")
            record.errors.append(error_msg)
            delay = self._backoff(record.attempts - 1)
            time.sleep(delay)

    def _backoff(self, attempt: int) -> float:
        delay: float = min(self._config.base_delay * (2 ** attempt), self._config.max_delay)
        if self._config.jitter:
            import random
            delay = delay * (0.5 + random.random())
        return delay
