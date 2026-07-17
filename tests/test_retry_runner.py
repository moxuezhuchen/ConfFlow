#!/usr/bin/env python3
"""Tests for RetryAwareTaskRunner (Phase 1c)."""

from __future__ import annotations

import pytest

from confflow.calc.retry_runner import RetryAwareTaskRunner, RetryConfig


class TestRetryConfig:
    def test_defaults(self):
        cfg = RetryConfig()
        assert cfg.max_retries == 2
        assert cfg.base_delay == 2.0
        assert "exec_error" in cfg.retry_on
        assert "abnormal_termination" in cfg.retry_on

    def test_custom_config(self):
        cfg = RetryConfig(max_retries=5, base_delay=1.0, jitter=False)
        assert cfg.max_retries == 5
        assert cfg.base_delay == 1.0
        assert cfg.jitter is False

    def test_retry_on_strict_types(self):
        cfg = RetryConfig(retry_on=("exec_error",))
        assert cfg.retry_on == ("exec_error",)


class TestRetryAwareTaskRunner:
    def test_non_transient_not_retried(self, monkeypatch):
        calls = []
        def fake_inner_run(self_, task_info):
            calls.append(1)
            return {
                "job_name": "A1",
                "status": "failed",
                "error_kind": "parse_error",
                "error": "cannot parse output",
            }

        monkeypatch.setattr(
            "confflow.calc.components.task_runner.TaskRunner.run",
            fake_inner_run,
        )
        runner = RetryAwareTaskRunner(RetryConfig(max_retries=3, base_delay=0.01))
        result = runner.run({"job_name": "A1"})
        assert calls == [1]
        assert result["error_kind"] == "parse_error"

    def test_transient_retried(self, monkeypatch):
        calls = []
        def fake_inner_run(self_, task_info):
            calls.append(1)
            return {
                "job_name": "A1",
                "status": "failed",
                "error_kind": "exec_error",
                "error": "g16 killed",
            }

        monkeypatch.setattr(
            "confflow.calc.components.task_runner.TaskRunner.run",
            fake_inner_run,
        )
        runner = RetryAwareTaskRunner(RetryConfig(max_retries=2, base_delay=0.01))
        result = runner.run({"job_name": "A1"})
        assert len(calls) == 3
        assert result["retries"] == 2

    def test_success_passthrough(self, monkeypatch):
        calls = []
        def fake_inner_run(self_, task_info):
            calls.append(1)
            return {
                "job_name": "A1",
                "status": "success",
                "final_val": -100.0,
                "final_coords": ["C 0 0 0"],
            }

        monkeypatch.setattr(
            "confflow.calc.components.task_runner.TaskRunner.run",
            fake_inner_run,
        )
        runner = RetryAwareTaskRunner(RetryConfig(max_retries=2, base_delay=0.01))
        result = runner.run({"job_name": "A1"})
        assert calls == [1]
        assert result["status"] == "success"
        assert result.get("retries", 0) == 0
