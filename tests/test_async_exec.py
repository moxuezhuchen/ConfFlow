#!/usr/bin/env python3
"""Tests for the async executor wrapper (Phase 1a)."""

from __future__ import annotations

import time
from concurrent.futures import ProcessPoolExecutor

import pytest

from confflow.calc.async_exec import (
    AsyncTaskExecutor,
    RetryConfig,
    TaskResult,
)


def _slow_task(payload):
    time.sleep(payload.get("sleep", 0.05))
    return {"job_name": payload.get("job_name", "?"), "status": "success"}


def _very_slow_task(payload):
    time.sleep(10.0)
    return {"job_name": payload.get("job_name", "?"), "status": "success"}


def _failing_task(payload):
    return {
        "job_name": payload.get("job_name", "?"),
        "status": "failed",
        "error": "transient exec_error",
        "error_kind": "exec_error",
    }


def _error_task(payload):
    raise RuntimeError("boom")


class TestRetryConfig:
    def test_backoff_no_jitter(self):
        cfg = RetryConfig(jitter=False)
        assert cfg.backoff(0) == pytest.approx(2.0)
        assert cfg.backoff(1) == pytest.approx(4.0)
        assert cfg.backoff(2) == pytest.approx(8.0)
        assert cfg.backoff(10) == pytest.approx(60.0)

    def test_backoff_with_jitter(self):
        cfg = RetryConfig(jitter=True, base_delay=1.0)
        for attempt in range(5):
            delay = cfg.backoff(attempt)
            base = min(1.0 * (2 ** attempt), 60.0)
            assert 0.3 * base <= delay <= 1.7 * base

    def test_retry_on_default(self):
        cfg = RetryConfig()
        assert "abnormal_termination" in cfg.retry_on
        assert "exec_error" in cfg.retry_on
        assert "parse_error" not in cfg.retry_on


class TestTaskResult:
    def test_dataclass_fields(self):
        tr = TaskResult(job_name="A1", status="success", retries=0, elapsed_seconds=1.5)
        assert tr.job_name == "A1"
        assert tr.status == "success"


class TestAsyncTaskExecutor:
    def test_enter_exit(self):
        exc = AsyncTaskExecutor(max_workers=2)
        assert exc._executor is None
        with exc:
            assert isinstance(exc._executor, ProcessPoolExecutor)
        assert exc._executor is None

    def test_successful_task(self):
        with AsyncTaskExecutor(max_workers=2, timeout_seconds=5.0) as exc:
            result = exc.submit(_slow_task, {"job_name": "A1", "sleep": 0.05})
        assert result.status == "success"
        assert result.job_name == "A1"
        assert result.error_kind is None
        assert result.retries == 0

    def test_task_timeout_returns_failed(self):
        with AsyncTaskExecutor(max_workers=1, timeout_seconds=0.2) as exc:
            result = exc.submit(_very_slow_task, {"job_name": "slow"})
        assert result.status == "failed"
        assert result.error_kind in {"exec_error", "serialization_error", "broken_process_pool"}
        err_str = str(result.error).lower()
        assert "timeout" in err_str or "exceeded" in err_str

    def test_worker_exception_returns_failed(self):
        with AsyncTaskExecutor(max_workers=1, timeout_seconds=5.0) as exc:
            result = exc.submit(_error_task, {"job_name": "err"})
        assert result.status == "failed"
        assert result.error_kind in {"worker_exception", "broken_process_pool", "serialization_error"}

    def test_retry_on_transient_failure(self):
        cfg = RetryConfig(max_retries=2, base_delay=0.01)
        with AsyncTaskExecutor(max_workers=1, retry_config=cfg) as exc:
            result = exc.submit(_failing_task, {"job_name": "fail"})
        assert result.status == "failed"
        assert result.error_kind == "exec_error"
        assert result.retries == 2

    def test_progress_callback(self):
        calls = []
        def callback(name, status):
            calls.append((name, status))

        with AsyncTaskExecutor(max_workers=1, progress_callback=callback) as exc:
            result = exc.submit(_slow_task, {"job_name": "A1"})
        assert (result.job_name, result.status) in calls

    def test_map_multiple_tasks(self):
        payloads = [{"job_name": "A{}".format(i), "sleep": 0.02} for i in range(3)]
        with AsyncTaskExecutor(max_workers=2, timeout_seconds=5.0) as exc:
            results = exc.map(_slow_task, payloads)
        assert len(results) == 3
        assert all(r.status == "success" for r in results)
