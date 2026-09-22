"""Regression tests for task identity and calculation-result acceptance."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from confflow.calc.components.task_runner import TaskRunner
from confflow.calc.db.database import ResultsDB
from confflow.calc.executor import CalcCancellationError
from confflow.calc.run_services import TaskSourceBuilder
from confflow.calc.runner import CalcStepRunner
from confflow.core.exceptions import CalculationExecutionError


def _build_tasks(tmp_path: Path, cids: list[str]):
    geometries = [{"coords": ["C 0 0 0"], "metadata": {"CID": cid}} for cid in cids]
    builder = TaskSourceBuilder(
        work_dir=str(tmp_path / "calc"),
        config={"itask": "sp"},
        iter_geometries_fn=lambda _path: geometries,
        job_name_fn=CalcStepRunner._job_name_for_geom,
    )
    return builder.build_from_input("input.xyz")


def test_task_names_are_unique_after_suffix_cleaning_and_truncation(tmp_path):
    long_prefix = "long-" + "x" * 60
    cids = [
        "A",
        "A",
        "A_dup1",
        "name/with punctuation",
        "name:with punctuation",
        long_prefix + "-one",
        long_prefix + "-two",
    ]

    tasks, metadata_by_job = _build_tasks(tmp_path, cids)
    names = [task.job_name for task in tasks]

    assert names[:5] == [
        "A",
        "A_dup1",
        "A_dup1_dup1",
        "name_with_punctuation",
        "name_with_punctuation_dup1",
    ]
    assert len(names) == len(set(names))
    assert len({task.work_dir for task in tasks}) == len(tasks)
    assert [task.metadata["CID"] for task in tasks] == cids
    assert metadata_by_job == {task.job_name: task.metadata for task in tasks}

    db = ResultsDB(str(tmp_path / "results.db"))
    try:
        for index, task in enumerate(tasks):
            db.insert_result(
                {
                    "job_name": task.job_name,
                    "status": "success",
                    "energy": -float(index),
                    "metadata": task.metadata,
                }
            )

        rows = list(db.iter_all_results())
        assert len(rows) == len(tasks)
        assert {row["job_name"] for row in rows} == set(names)
        assert {row["cid"] for row in rows} == set(cids)
        for task in tasks:
            row = db.get_result_by_job_name(task.job_name)
            assert row is not None
            assert row["cid"] == task.metadata["CID"]
    finally:
        db.close()


def _run_task(tmp_path: Path, *, itask: int, result: dict, config: dict | None = None):
    task_info = {
        "job_name": "task",
        "work_dir": str(tmp_path / "work"),
        "config": {"itask": itask, "iprog": 1, **(config or {})},
        "coords": ["C 0 0 0"],
    }
    with (
        patch(
            "confflow.calc.components.executor._run_calculation_step",
            return_value=result,
        ),
        patch("confflow.calc.components.executor.handle_backups") as backups,
    ):
        return TaskRunner().run(task_info), backups


def test_opt_freq_missing_frequency_is_parse_error(tmp_path):
    result, _backups = _run_task(
        tmp_path,
        itask=3,
        result={
            "final_coords": ["C 0 0 0"],
            "e_low": -1.0,
            "g_low": -0.9,
            "num_imag_freqs": None,
        },
    )

    assert result["status"] == "failed"
    assert result["error_kind"] == "parse_error"
    assert "frequency" in result["error"].lower()


def test_opt_freq_zero_imaginary_frequencies_is_success(tmp_path):
    result, _backups = _run_task(
        tmp_path,
        itask=3,
        result={
            "final_coords": ["C 0 0 0"],
            "e_low": -1.0,
            "g_low": -0.9,
            "num_imag_freqs": 0,
        },
    )

    assert result["status"] == "success"
    assert result["num_imag_freqs"] == 0


def test_missing_frequency_remains_valid_for_sp_opt_and_ts_without_freq(tmp_path):
    for index, itask in enumerate((0, 1, 4)):
        result, _backups = _run_task(
            tmp_path / str(index),
            itask=itask,
            result={"final_coords": ["C 0 0 0"], "e_low": -1.0},
        )
        assert result["status"] == "success"


def test_unconfirmed_cancellation_skips_rescue_and_preserves_work_dir(tmp_path):
    work_dir = tmp_path / "work"
    task_info = {
        "job_name": "task",
        "work_dir": str(work_dir),
        "config": {
            "itask": 4,
            "iprog": 1,
            "ts_rescue_scan": True,
            "delete_work_dir": True,
        },
        "coords": ["C 0 0 0"],
    }
    with (
        patch(
            "confflow.calc.components.executor._run_calculation_step",
            side_effect=CalcCancellationError("process boundary is still live"),
        ),
        patch("confflow.calc.components.task_runner._ts_rescue_scan") as rescue,
        patch("confflow.calc.components.executor.handle_backups") as backups,
    ):
        result = TaskRunner().run(task_info)

    assert result["status"] == "failed"
    assert result["error_kind"] == "exec_error"
    assert "could not be confirmed" in result["error"]
    rescue.assert_not_called()
    backups.assert_not_called()
    assert work_dir.is_dir()


def test_unconfirmed_cancellation_from_rescue_preserves_work_dir(tmp_path):
    work_dir = tmp_path / "work"
    task_info = {
        "job_name": "task",
        "work_dir": str(work_dir),
        "config": {
            "itask": 4,
            "iprog": 1,
            "ts_rescue_scan": True,
            "delete_work_dir": True,
        },
        "coords": ["C 0 0 0"],
    }
    with (
        patch(
            "confflow.calc.components.executor._run_calculation_step",
            side_effect=CalculationExecutionError("initial calculation failed"),
        ),
        patch(
            "confflow.calc.components.task_runner._ts_rescue_scan",
            side_effect=CalcCancellationError("rescue process boundary is still live"),
        ) as rescue,
        patch("confflow.calc.components.executor.handle_backups") as backups,
    ):
        result = TaskRunner().run(task_info)

    assert result["status"] == "failed"
    assert result["error_kind"] == "exec_error"
    assert "could not be confirmed" in result["error"]
    rescue.assert_called_once()
    backups.assert_not_called()
    assert work_dir.is_dir()


def test_unconfirmed_cancellation_from_real_rescue_keeps_rescue_tree(tmp_path):
    """A rescue reoptimization cancellation reaches TaskRunner unhandled."""
    work_dir = tmp_path / "work"
    task_info = {
        "job_name": "task",
        "work_dir": str(work_dir),
        "config": {
            "itask": 4,
            "iprog": 1,
            "keyword": "opt(ts)",
            "ts_bond_atoms": "1,2",
            "ts_rescue_scan": True,
            "delete_work_dir": True,
        },
        "coords": ["H 0 0 0", "H 0 0 1.0"],
    }

    def run_step(step_work_dir, *_args, **_kwargs):
        if str(step_work_dir).endswith("ts_rescue"):
            raise CalcCancellationError("rescue process boundary is still live")
        raise CalculationExecutionError("initial calculation failed")

    context = {
        "cfg": task_info["config"],
        "job": "task",
        "wd": str(work_dir),
        "a1": 1,
        "a2": 2,
        "r0": 1.0,
        "base_coords": task_info["coords"],
    }
    with (
        patch("confflow.calc.components.executor._run_calculation_step", side_effect=run_step),
        patch("confflow.calc.rescue._prepare_rescue_context", return_value=context),
        patch(
            "confflow.calc.rescue._run_coarse_and_fine_scan",
            return_value=(1.0, task_info["coords"], [], []),
        ),
        patch("confflow.calc.components.executor.handle_backups") as backups,
    ):
        result = TaskRunner().run(task_info)

    assert result["status"] == "failed"
    assert result["error_kind"] == "exec_error"
    assert "could not be confirmed" in result["error"]
    backups.assert_not_called()
    assert work_dir.is_dir()
    assert (work_dir / "ts_rescue").is_dir()


def test_unconfirmed_cancellation_during_rescue_scan_keeps_work_dir(tmp_path):
    """The scan worker must propagate cancellation instead of treating it as a bad point."""
    work_dir = tmp_path / "work"
    task_info = {
        "job_name": "task",
        "work_dir": str(work_dir),
        "config": {
            "itask": 4,
            "iprog": 1,
            "keyword": "opt(ts)",
            "ts_bond_atoms": "1,2",
            "ts_rescue_scan": True,
            "delete_work_dir": True,
        },
        "coords": ["H 0 0 0", "H 0 0 1.0"],
    }
    calls = 0

    def run_step(_step_work_dir, *_args, **_kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise CalculationExecutionError("initial calculation failed")
        raise CalcCancellationError("scan process boundary is still live")

    context = {
        "cfg": task_info["config"],
        "job": "task",
        "wd": str(work_dir),
        "a1": 1,
        "a2": 2,
        "r0": 1.0,
        "base_coords": task_info["coords"],
    }
    with (
        patch("confflow.calc.components.executor._run_calculation_step", side_effect=run_step),
        patch("confflow.calc.rescue._prepare_rescue_context", return_value=context),
        patch("confflow.calc.components.executor.handle_backups") as backups,
    ):
        result = TaskRunner().run(task_info)

    assert result["status"] == "failed"
    assert result["error_kind"] == "exec_error"
    assert "could not be confirmed" in result["error"]
    backups.assert_not_called()
    assert work_dir.is_dir()
