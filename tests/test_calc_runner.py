#!/usr/bin/env python3

"""Tests for the typed calc step runner."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

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


def test_calc_step_runner_passes_ts_rescue_scan_config_to_task_runner(tmp_path):
    input_xyz = tmp_path / "input.xyz"
    input_xyz.write_text("2\nCID=ts_seed\nH 0 0 0\nH 0 0 0.8\n", encoding="utf-8")
    step_dir = tmp_path / "step_ts"
    config = CalcStepParams.from_params(
        {
            "iprog": "orca",
            "itask": "ts",
            "keyword": "HF STO-3G OptTS Freq",
            "auto_clean": False,
            "delete_work_dir": False,
            "ts_rescue_scan": True,
            "ts_bond_atoms": [1, 2],
            "ts_bond_drift_threshold": 0.25,
            "ts_rmsd_threshold": 0.3,
            "scan_coarse_step": 0.2,
            "scan_fine_step": 0.05,
            "scan_uphill_limit": 2,
            "scan_max_steps": 4,
            "scan_fine_half_window": 0.1,
            "ts_rescue_keep_scan_dirs": True,
            "ts_rescue_scan_backup": False,
        },
        GlobalOptions.from_mapping({}),
    )
    captured: dict[str, object] = {}

    def fake_rescue(task_dict, fail_reason):
        captured["config"] = dict(task_dict["config"])
        captured["fail_reason"] = fail_reason
        return {
            **task_dict,
            "status": "success",
            "energy": -1.0,
            "final_coords": task_dict["coords"],
            "rescued_by_scan": True,
        }

    with (
        patch("confflow.calc.components.executor._run_calculation_step") as mock_run,
        patch("confflow.calc.components.task_runner._ts_rescue_scan", side_effect=fake_rescue),
        patch("confflow.calc.components.executor.handle_backups", return_value=True),
    ):
        mock_run.return_value = {
            "final_coords": ["H 0 0 0", "H 0 0 0.8"],
            "e_low": None,
            "g_low": None,
        }
        result = CalcStepRunner().run(
            CalcStepRequest(
                step_name="ts",
                step_dir=str(step_dir),
                input_xyz=str(input_xyz),
                config=config,
            )
        )

    rescue_config = captured["config"]
    assert result.succeeded == 1
    assert Path(result.output_path).exists()
    assert "frequency info" in str(captured["fail_reason"])
    assert rescue_config["itask"] == "ts"
    assert rescue_config["ts_rescue_scan"] is True
    assert rescue_config["ts_bond_atoms"] == "1,2"
    assert rescue_config["ts_bond_drift_threshold"] == 0.25
    assert rescue_config["ts_rmsd_threshold"] == 0.3
    assert rescue_config["scan_coarse_step"] == 0.2
    assert rescue_config["scan_fine_step"] == 0.05
    assert rescue_config["scan_uphill_limit"] == 2
    assert rescue_config["scan_max_steps"] == 4
    assert rescue_config["scan_fine_half_window"] == 0.1
    assert rescue_config["ts_rescue_keep_scan_dirs"] is True
    assert rescue_config["ts_rescue_scan_backup"] is False
