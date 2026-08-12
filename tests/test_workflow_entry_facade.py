"""Focused behavior tests for the public workflow entry facade."""

from __future__ import annotations

from typing import Any

from confflow.workflow import engine


def test_run_workflow_forwards_prepared_workflow_to_orchestrator(monkeypatch) -> None:
    prepared = object()
    calls: dict[str, Any] = {}

    monkeypatch.setattr(
        engine,
        "prepare_workflow",
        lambda input_xyz, config_file, original_input_files: (
            calls.update(
                input_xyz=input_xyz,
                config_file=config_file,
                original_input_files=original_input_files,
            )
            or prepared
        ),
    )

    class FakeOrchestrator:
        def __init__(self, **kwargs: Any) -> None:
            calls["orchestrator"] = kwargs

        def run(self) -> dict[str, bool]:
            calls["ran"] = True
            return {"ok": True}

    monkeypatch.setattr(engine, "_WorkflowOrchestrator", FakeOrchestrator)

    def step_started_callback(*_args: object) -> None:
        pass

    def on_step_status_change(*_args: object) -> None:
        pass

    result = engine.run_workflow(
        ["input.xyz"],
        "workflow.yaml",
        "run-dir",
        original_input_files=["original.xyz"],
        resume=True,
        verbose=False,
        pause_beacon_file="pause",
        step_started_callback=step_started_callback,
        calc_executor="calc-executor",
        on_step_status_change=on_step_status_change,
        poll_interval_seconds=0,
    )

    assert result == {"ok": True}
    assert calls["input_xyz"] == ["input.xyz"]
    assert calls["config_file"] == "workflow.yaml"
    assert calls["original_input_files"] == ["original.xyz"]
    assert calls["orchestrator"] == {
        "prepared": prepared,
        "config_file": "workflow.yaml",
        "work_dir": "run-dir",
        "resume": True,
        "logger": engine.logger,
        "pause_beacon_file": "pause",
        "step_started_callback": step_started_callback,
        "on_step_status_change": on_step_status_change,
        "calc_executor": "calc-executor",
        "run_confgen_step": engine._run_confgen_step,
        "run_calc_step": engine._run_calc_step,
    }
    assert calls["ran"] is True
