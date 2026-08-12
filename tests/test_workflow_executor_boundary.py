"""Boundary tests for executor callback ordering and step lineage."""

from __future__ import annotations

from pathlib import Path

from confflow.workflow.engine import run_workflow
from confflow.workflow.step_handlers import StepExecutionResult


def test_executor_preserves_callback_order_and_predecessor_lineage(
    tmp_path: Path,
    monkeypatch,
) -> None:
    input_xyz = tmp_path / "input.xyz"
    input_xyz.write_text("1\nseed\nH 0 0 0\n", encoding="utf-8")
    config = tmp_path / "workflow.yaml"
    config.write_text(
        "global: {}\n"
        "steps:\n"
        "  - name: gen\n"
        "    type: confgen\n"
        "  - name: calc\n"
        "    type: calc\n"
        "    params: {keyword: HF}\n",
        encoding="utf-8",
    )
    events: list[str] = []
    seen_inputs: list[str | list[str]] = []

    def fake_confgen(step_dir, current_input, params, input_files, global_config):
        del current_input, params, input_files, global_config
        output = Path(step_dir) / "search.xyz"
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text("1\ngen\nH 0 0 0\n", encoding="utf-8")
        return StepExecutionResult(output_path=str(output))

    def fake_calc(
        step_dir,
        current_input,
        params,
        global_config,
        root_dir,
        steps,
        failure_tracker,
        step_name,
    ):
        del params, global_config, root_dir, steps, failure_tracker, step_name
        seen_inputs.append(current_input)
        output = Path(step_dir) / "result.xyz"
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text("1\ncalc\nH 0 0 0\n", encoding="utf-8")
        return StepExecutionResult(output_path=str(output))

    monkeypatch.setattr("confflow.workflow.engine._run_confgen_step", fake_confgen)
    monkeypatch.setattr("confflow.workflow.engine._run_calc_step", fake_calc)
    run_workflow(
        [str(input_xyz)],
        str(config),
        work_dir=str(tmp_path / "run"),
        step_started_callback=lambda name, *_: events.append(f"started:{name}"),
        on_step_status_change=lambda record: events.append(f"status:{record.name}:{record.status}"),
    )

    assert seen_inputs == [str(tmp_path / "run" / "gen" / "search.xyz")]
    assert events[:4] == [
        "started:gen",
        "status:gen:submitted",
        "status:gen:completed",
        "started:calc",
    ]
    assert events[4:] == ["status:calc:submitted", "status:calc:completed"]
