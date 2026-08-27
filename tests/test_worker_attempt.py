"""Tests for the narrow post-lease worker attempt boundary."""

from __future__ import annotations

import ast
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from confflow.application.execution.models import RunState
from confflow.worker_attempt import run_worker_attempt


class _FakeRoot:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.work = path / "work"

    def ensure_run_paths(self, run_id: str) -> SimpleNamespace:
        assert run_id == "attempt-run"
        return SimpleNamespace(work=self.work)


def _inputs(tmp_path: Path) -> tuple[str, list[dict[str, str]]]:
    config = tmp_path / "workflow.yaml"
    input_xyz = tmp_path / "input.xyz"
    work_dir = tmp_path / "results" / "input_confflow_work"
    return str(config), [{"input_xyz": str(input_xyz), "work_dir": str(work_dir)}]


def test_terminal_consumption_does_not_wait_and_builds_the_bound_spec(
    tmp_path: Path,
) -> None:
    staged_config, staged_tasks = _inputs(tmp_path)
    runner = object()
    calls: dict[str, Any] = {"wait": 0}

    class Service:
        def consume_queued_launch(self, run_id: str) -> SimpleNamespace:
            calls["consumed"] = run_id
            return SimpleNamespace(state=RunState.COMPLETED)

    class Executor:
        def wait(self) -> None:
            calls["wait"] += 1

    def build(spec: Any, *, state_root: Path, workflow_runner: Any) -> tuple[Service, Executor]:
        calls["spec"] = spec
        calls["state_root"] = state_root
        calls["runner"] = workflow_runner
        return Service(), Executor()

    state = run_worker_attempt(
        root=_FakeRoot(tmp_path / "state"),
        run_id="attempt-run",
        staged_config=staged_config,
        staged_tasks=staged_tasks,
        resume=True,
        workflow_runner=runner,
        service_builder=build,
    )

    assert state is RunState.COMPLETED
    assert calls["consumed"] == "attempt-run"
    assert calls["wait"] == 0
    assert calls["state_root"] == tmp_path / "state"
    assert calls["runner"] is runner
    spec = calls["spec"]
    assert spec.run_id == "attempt-run"
    assert spec.input_xyz == (staged_tasks[0]["input_xyz"],)
    assert spec.original_input_files == spec.input_xyz
    assert spec.config_file == staged_config
    assert spec.work_dir == staged_tasks[0]["work_dir"]
    assert spec.resume is True
    assert spec.pause_beacon_file == str(tmp_path / "state" / "work" / "PAUSE")
    assert spec.cancel_beacon_file == str(tmp_path / "state" / "work" / "CANCEL")


def test_nonterminal_consumption_waits_once_and_returns_to_durable_projection(
    tmp_path: Path,
) -> None:
    staged_config, staged_tasks = _inputs(tmp_path)
    calls = 0

    class Service:
        def consume_queued_launch(self, run_id: str) -> SimpleNamespace:
            assert run_id == "attempt-run"
            return SimpleNamespace(state=RunState.RUNNING)

    class Executor:
        def wait(self) -> None:
            nonlocal calls
            calls += 1

    def build(*args: Any, **kwargs: Any) -> tuple[Service, Executor]:
        del args, kwargs
        return Service(), Executor()

    assert (
        run_worker_attempt(
            root=_FakeRoot(tmp_path / "state"),
            run_id="attempt-run",
            staged_config=staged_config,
            staged_tasks=staged_tasks,
            resume=False,
            workflow_runner=lambda **_: None,
            service_builder=build,
        )
        is None
    )
    assert calls == 1


@pytest.mark.parametrize("stage", ["builder", "consume", "wait"])
def test_attempt_boundary_propagates_failures_unchanged(tmp_path: Path, stage: str) -> None:
    staged_config, staged_tasks = _inputs(tmp_path)
    failure = RuntimeError(stage)

    class Service:
        def consume_queued_launch(self, run_id: str) -> SimpleNamespace:
            del run_id
            if stage == "consume":
                raise failure
            return SimpleNamespace(state=RunState.RUNNING)

    class Executor:
        def wait(self) -> None:
            if stage == "wait":
                raise failure

    def build(*args: Any, **kwargs: Any) -> tuple[Service, Executor]:
        del args, kwargs
        if stage == "builder":
            raise failure
        return Service(), Executor()

    with pytest.raises(RuntimeError) as raised:
        run_worker_attempt(
            root=_FakeRoot(tmp_path / "state"),
            run_id="attempt-run",
            staged_config=staged_config,
            staged_tasks=staged_tasks,
            resume=False,
            workflow_runner=lambda **_: None,
            service_builder=build,
        )
    assert raised.value is failure


def test_control_worker_keeps_post_lease_attempt_logic_outside_the_orchestrator() -> None:
    package_root = Path(__file__).parents[1] / "confflow"
    control_source = (package_root / "control_worker.py").read_text(encoding="utf-8")
    attempt_source = (package_root / "worker_attempt.py").read_text(encoding="utf-8")
    control_tree = ast.parse(control_source)
    attempt_tree = ast.parse(attempt_source)
    control_run = next(
        node
        for node in control_tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "run_control_worker"
    )
    control_text = ast.unparse(control_run)
    assert "_worker_attempt.run_worker_attempt" in control_text
    assert "WorkflowRunSpec" not in control_text
    assert "consume_queued_launch" not in control_text
    assert "executor.wait" not in control_text
    attempt_run = next(
        node
        for node in attempt_tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "run_worker_attempt"
    )
    attempt_calls = [
        ast.unparse(node.func) for node in ast.walk(attempt_run) if isinstance(node, ast.Call)
    ]
    assert attempt_calls.count("service_builder") == 1
    assert attempt_calls.count("service.consume_queued_launch") == 1
    assert attempt_calls.count("executor.wait") == 1
