#!/usr/bin/env python3

"""Tests for the typed calc step runner."""

from __future__ import annotations

import json
from pathlib import Path

from confflow.calc.runner import CalcStepRequest, CalcStepRunner
from confflow.config.models import CalcStepParams, GlobalOptions


def _config():
    return CalcStepParams.from_params(
        {
            "iprog": "orca",
            "itask": "sp",
            "keyword": "HF",
            "auto_clean": False,
            "delete_work_dir": False,
        },
        GlobalOptions.from_mapping({}),
    )


def test_calc_step_runner_writes_result_and_manifest(tmp_path, monkeypatch):
    input_xyz = tmp_path / "input.xyz"
    input_xyz.write_text("1\nCID=seed\nH 0 0 0\n", encoding="utf-8")
    step_dir = tmp_path / "step_01_calc"

    def fake_execute_tasks(*, todo, results_db, append_result_fn, **kwargs):
        for task in todo:
            data = task.model_dump() if hasattr(task, "model_dump") else dict(task)
            res = {
                **data,
                "status": "success",
                "energy": -1.0,
                "final_coords": data["coords"],
            }
            results_db.insert_result(res)
            append_result_fn(res)

    monkeypatch.setattr("confflow.calc.runner.execute_tasks", fake_execute_tasks)

    result = CalcStepRunner().run(
        CalcStepRequest(
            step_name="calc",
            step_dir=str(step_dir),
            input_xyz=str(input_xyz),
            config=_config(),
        )
    )

    assert Path(result.output_path).name == "result.xyz"
    assert result.total_tasks == 1
    assert result.succeeded == 1
    assert result.failed == 0
    assert "seed" in Path(result.output_path).read_text(encoding="utf-8")

    manifest = json.loads((step_dir / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["status"] == "completed"
    assert manifest["total_tasks"] == 1
    assert manifest["succeeded"] == 1


def test_calc_step_runner_reuses_completed_manifest(tmp_path, monkeypatch):
    input_xyz = tmp_path / "input.xyz"
    input_xyz.write_text("1\nCID=seed\nH 0 0 0\n", encoding="utf-8")
    step_dir = tmp_path / "step_01_calc"

    def fake_execute_tasks(*, todo, results_db, append_result_fn, **kwargs):
        for task in todo:
            data = task.model_dump()
            res = {**data, "status": "success", "energy": -1.0, "final_coords": data["coords"]}
            results_db.insert_result(res)
            append_result_fn(res)

    monkeypatch.setattr("confflow.calc.runner.execute_tasks", fake_execute_tasks)
    runner = CalcStepRunner()
    request = CalcStepRequest(
        step_name="calc",
        step_dir=str(step_dir),
        input_xyz=str(input_xyz),
        config=_config(),
    )
    first = runner.run(request)
    second = CalcStepRunner().run(request)

    assert second.reused is True
    assert second.output_path == first.output_path
    assert second.total_tasks == 0
