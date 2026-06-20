#!/usr/bin/env python3

"""Tests for workflow step handlers on the typed execution boundary."""

from __future__ import annotations

from pathlib import Path

import pytest

from confflow.calc.runner import CalcStepResult
from confflow.core.exceptions import ConfFlowError
from confflow.workflow.step_handlers import StepExecutionResult, run_calc_step, run_confgen_step


def _xyz(path: Path, multi: bool = False) -> Path:
    text = "1\nframe1\nH 0 0 0\n"
    if multi:
        text += "1\nframe2\nH 0 0 1\n"
    path.write_text(text, encoding="utf-8")
    return path


def test_confgen_multiframe_input_is_copied_and_reused(tmp_path):
    step_dir = tmp_path / "step_01_confgen"
    step_dir.mkdir()
    source = _xyz(tmp_path / "multi.xyz", multi=True)

    first = run_confgen_step(
        step_dir=str(step_dir),
        current_input=str(source),
        params={},
        input_files=[str(source)],
    )
    second = run_confgen_step(
        step_dir=str(step_dir),
        current_input=str(source),
        params={},
        input_files=[str(source)],
    )

    assert first.output_path == str(step_dir / "search.xyz")
    assert first.copied_multi_frame is True
    assert second.reused_existing is True
    assert (step_dir / ".confgen_signature").exists()


def test_confgen_recomputes_when_params_change(tmp_path, monkeypatch):
    step_dir = tmp_path / "step_01_confgen"
    step_dir.mkdir()
    source = _xyz(tmp_path / "single.xyz")

    def fake_run_generation(**kwargs):
        Path("search.xyz").write_text(
            f"1\nangle={kwargs['angle_step']}\nH 0 0 0\n",
            encoding="utf-8",
        )

    monkeypatch.setattr(
        "confflow.workflow.step_handlers.confgen.run_generation", fake_run_generation
    )

    run_confgen_step(
        step_dir=str(step_dir),
        current_input=str(source),
        params={"angle_step": 120},
        input_files=[str(source), str(source)],
    )
    result = run_confgen_step(
        step_dir=str(step_dir),
        current_input=str(source),
        params={"angle_step": 60},
        input_files=[str(source), str(source)],
    )

    assert result.cleaned_stale_artifacts is True
    assert "angle=60" in (step_dir / "search.xyz").read_text(encoding="utf-8")


def test_calc_step_builds_typed_request_and_tracks_failed_file(tmp_path, monkeypatch):
    input_xyz = _xyz(tmp_path / "input.xyz")
    step_dir = tmp_path / "step_02_calc"
    output = step_dir / "result.xyz"
    failed = step_dir / "failed.xyz"

    class FakeRunner:
        def run(self, request):
            assert request.step_name == "calc1"
            assert request.input_xyz == str(input_xyz)
            assert request.config.program == "orca"
            assert request.config.task == "sp"
            step_dir.mkdir()
            output.write_text("1\nok\nH 0 0 0\n", encoding="utf-8")
            failed.write_text("1\nbad\nH 0 0 1\n", encoding="utf-8")
            return CalcStepResult(
                output_path=str(output),
                failed_path=str(failed),
                total_tasks=2,
                succeeded=1,
                failed=1,
                cleaned_stale_artifacts=True,
            )

    class Tracker:
        def __init__(self):
            self.calls = []

        def append(self, failed_path, step_name):
            self.calls.append((failed_path, step_name))

    tracker = Tracker()
    monkeypatch.setattr("confflow.workflow.step_handlers.CalcStepRunner", FakeRunner)

    result = run_calc_step(
        step_dir=str(step_dir),
        current_input=str(input_xyz),
        params={"iprog": "orca", "itask": "sp", "keyword": "HF"},
        global_config={},
        root_dir=str(tmp_path),
        steps=[],
        failure_tracker=tracker,
        step_name="calc1",
    )

    assert isinstance(result, StepExecutionResult)
    assert result.output_path == str(output)
    assert result.failed_path == str(failed)
    assert result.cleaned_stale_artifacts is True
    assert tracker.calls == [(str(failed), "calc1")]


def test_calc_step_rejects_multiple_inputs_without_confgen(tmp_path):
    with pytest.raises(ConfFlowError, match="exactly one input file"):
        run_calc_step(
            step_dir=str(tmp_path / "step"),
            current_input=["a.xyz", "b.xyz"],
            params={"keyword": "HF"},
            global_config={},
            root_dir=str(tmp_path),
            steps=[],
            failure_tracker=None,
            step_name="calc",
        )
