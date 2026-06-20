#!/usr/bin/env python3

"""Focused tests for workflow engine helpers and typed dispatch."""

from __future__ import annotations

from pathlib import Path

import pytest

from confflow.core.pairs import normalize_pair_list
from confflow.workflow.engine import count_conformers_any, run_workflow, validate_inputs_compatible
from confflow.workflow.helpers import as_list, count_conformers_in_xyz, resolve_step_output
from confflow.workflow.stats import count_task_statuses_in_results_db


def test_as_list_and_pair_normalization():
    assert as_list(None) is None
    assert as_list("x") == ["x"]
    assert normalize_pair_list("1-2") == [[1, 2]]
    assert normalize_pair_list(["1,2", "3 4"]) == [[1, 2], [3, 4]]
    with pytest.raises(ValueError):
        normalize_pair_list("1,2,3")


def test_count_conformers_any_and_validation(tmp_path):
    xyz = tmp_path / "input.xyz"
    xyz.write_text("1\none\nH 0 0 0\n1\ntwo\nH 0 0 1\n", encoding="utf-8")
    single = tmp_path / "single.xyz"
    single.write_text("1\none\nH 0 0 0\n", encoding="utf-8")
    assert count_conformers_in_xyz(str(xyz)) == 2
    assert count_conformers_any(str(xyz)) == 2
    assert count_conformers_any([str(xyz), str(tmp_path / "missing.xyz")]) == 2
    validate_inputs_compatible([str(single)])

    bad = tmp_path / "bad.xyz"
    bad.write_text("not xyz\n", encoding="utf-8")
    with pytest.raises(ValueError, match="cannot parse input XYZ"):
        validate_inputs_compatible([str(bad)])


def test_resolve_step_output_prefers_expected_files(tmp_path):
    step_dir = tmp_path / "step"
    step_dir.mkdir()
    assert resolve_step_output(str(step_dir)) is None
    result = step_dir / "result.xyz"
    result.write_text("1\nok\nH 0 0 0\n", encoding="utf-8")
    assert resolve_step_output(str(step_dir)) == str(result)
    output = step_dir / "output.xyz"
    output.write_text("1\nclean\nH 0 0 0\n", encoding="utf-8")
    assert resolve_step_output(str(step_dir)) == str(output)


def test_count_task_statuses_in_results_db(tmp_path):
    from confflow.calc.db.database import ResultsDB

    db = ResultsDB(str(tmp_path / "results.db"))
    db.insert_result({"job_name": "a", "status": "success"})
    db.insert_result({"job_name": "b", "status": "failed"})
    db.insert_result({"job_name": "c", "status": "canceled"})
    db.close()

    assert count_task_statuses_in_results_db(str(tmp_path / "results.db")) == {
        "total": 3,
        "success": 1,
        "failed": 1,
        "canceled": 1,
        "pending": 0,
        "skipped": 0,
    }


def test_run_workflow_dispatches_to_step_handlers(tmp_path, monkeypatch):
    input_xyz = tmp_path / "input.xyz"
    input_xyz.write_text("1\nseed\nH 0 0 0\n", encoding="utf-8")
    config = tmp_path / "workflow.yaml"
    config.write_text(
        "global: {}\n"
        "steps:\n"
        "  - name: gen\n"
        "    type: confgen\n"
        "    params: {}\n"
        "  - name: calc\n"
        "    type: calc\n"
        "    params:\n"
        "      keyword: HF\n",
        encoding="utf-8",
    )

    def fake_confgen(step_dir, current_input, params, input_files, global_config=None):
        path = Path(step_dir) / "search.xyz"
        Path(step_dir).mkdir(parents=True, exist_ok=True)
        path.write_text("1\ngen\nH 0 0 0\n", encoding="utf-8")
        from confflow.workflow.step_handlers import StepExecutionResult

        return StepExecutionResult(output_path=str(path))

    def fake_calc(
        step_dir, current_input, params, global_config, root_dir, steps, failure_tracker, step_name
    ):
        del current_input, params, global_config, root_dir, steps, failure_tracker, step_name
        path = Path(step_dir) / "result.xyz"
        Path(step_dir).mkdir(parents=True, exist_ok=True)
        path.write_text("1\ncalc | Energy=-1.0\nH 0 0 0\n", encoding="utf-8")
        from confflow.workflow.step_handlers import StepExecutionResult

        return StepExecutionResult(output_path=str(path))

    monkeypatch.setattr("confflow.workflow.engine._run_confgen_step", fake_confgen)
    monkeypatch.setattr("confflow.workflow.engine._run_calc_step", fake_calc)

    stats = run_workflow([str(input_xyz)], str(config), work_dir=str(tmp_path / "run"))
    output = Path(stats["final_output"])
    assert output.name == "result.xyz"
    assert output.exists()

    assert [step["name"] for step in stats["steps"]] == ["gen", "calc"]


def test_run_workflow_skips_disabled_steps(tmp_path, monkeypatch):
    input_xyz = tmp_path / "input.xyz"
    input_xyz.write_text("1\nseed\nH 0 0 0\n", encoding="utf-8")
    config = tmp_path / "workflow.yaml"
    config.write_text(
        "global: {}\n"
        "steps:\n"
        "  - name: disabled_calc\n"
        "    type: calc\n"
        "    enabled: false\n"
        "    params:\n"
        "      keyword: HF\n",
        encoding="utf-8",
    )

    def fail_calc(*args, **kwargs):
        raise AssertionError("disabled calc step should not run")

    monkeypatch.setattr("confflow.workflow.engine._run_calc_step", fail_calc)
    stats = run_workflow([str(input_xyz)], str(config), work_dir=str(tmp_path / "run"))

    assert stats["final_output"] == str(input_xyz.resolve())
    assert stats["steps"] == []
