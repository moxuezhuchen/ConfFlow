#!/usr/bin/env python3

"""Tests for rerunning failed calc conformers."""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import patch

import pytest

from confflow.calc.api import CalcStepExecutionResult
from confflow.cli import main
from confflow.core.contracts import ExitCode
from confflow.workflow.rerun_failed import (
    RerunFailedRuntimeError,
    RerunFailedUsageError,
    run_rerun_failed,
)


def _write_failed_xyz(step_dir: Path) -> Path:
    step_dir.mkdir(parents=True)
    failed = step_dir / "failed.xyz"
    failed.write_text("2\nfailed\nC 0 0 0\nH 0 0 1\n", encoding="utf-8")
    return failed


def _write_config(path: Path) -> Path:
    path.write_text(
        "global: {}\n"
        "steps:\n"
        "  - name: gen\n"
        "    type: confgen\n"
        "    params:\n"
        "      chains: ['1-2']\n"
        "  - name: calc1\n"
        "    type: calc\n"
        "    params:\n"
        "      iprog: orca\n"
        "      itask: sp\n"
        "      keyword: hf-3c\n",
        encoding="utf-8",
    )
    return path


def test_rerun_failed_missing_step_dir_returns_usage_error(tmp_path):
    config = _write_config(tmp_path / "confflow.yaml")

    with pytest.raises(RerunFailedUsageError, match="Step directory does not exist"):
        run_rerun_failed(
            step_dir=str(tmp_path / "missing"),
            config_file=str(config),
            step_ref="calc1",
        )


def test_rerun_failed_missing_failed_xyz_returns_runtime_error(tmp_path):
    config = _write_config(tmp_path / "confflow.yaml")
    step_dir = tmp_path / "work" / "step_02_calc1"
    step_dir.mkdir(parents=True)

    with pytest.raises(RerunFailedRuntimeError, match="failed.xyz was not found"):
        run_rerun_failed(step_dir=str(step_dir), config_file=str(config), step_ref="calc1")


def test_rerun_failed_empty_failed_xyz_returns_runtime_error(tmp_path):
    config = _write_config(tmp_path / "confflow.yaml")
    step_dir = tmp_path / "work" / "step_02_calc1"
    step_dir.mkdir(parents=True)
    (step_dir / "failed.xyz").write_text("", encoding="utf-8")

    with pytest.raises(RerunFailedRuntimeError, match="not a readable XYZ file"):
        run_rerun_failed(step_dir=str(step_dir), config_file=str(config), step_ref="calc1")


def test_cli_rerun_failed_requires_config(tmp_path, capsys):
    step_dir = tmp_path / "work" / "step_02_calc1"
    _write_failed_xyz(step_dir)

    result = main(["--rerun-failed", str(step_dir), "--step", "calc1"])

    captured = capsys.readouterr()
    assert result == ExitCode.USAGE_ERROR
    assert "--config is required with --rerun-failed" in captured.err


def test_cli_rerun_failed_requires_step(tmp_path, capsys):
    config = _write_config(tmp_path / "confflow.yaml")
    step_dir = tmp_path / "work" / "step_02_calc1"
    _write_failed_xyz(step_dir)

    result = main(["--rerun-failed", str(step_dir), "-c", str(config)])

    captured = capsys.readouterr()
    assert result == ExitCode.USAGE_ERROR
    assert "--step is required with --rerun-failed" in captured.err


def test_rerun_failed_selects_calc_step_by_name(tmp_path):
    config = _write_config(tmp_path / "confflow.yaml")
    step_dir = tmp_path / "work" / "step_02_calc1"
    failed = _write_failed_xyz(step_dir)
    output_dir = tmp_path / "rerun"

    def fake_run_calc_workflow_step(**kwargs):
        os.makedirs(kwargs["step_dir"])
        output = Path(kwargs["step_dir"]) / "output.xyz"
        output.write_text("2\nok\nC 0 0 0\nH 0 0 1\n", encoding="utf-8")
        return CalcStepExecutionResult(output_path=str(output))

    with patch(
        "confflow.workflow.rerun_failed.calc.run_calc_workflow_step",
        side_effect=fake_run_calc_workflow_step,
    ) as mock_run:
        result = run_rerun_failed(
            step_dir=str(step_dir),
            config_file=str(config),
            step_ref="calc1",
            output_dir=str(output_dir),
        )

    assert result.failed_path == str(failed)
    assert result.step_label == "2:calc1"
    assert result.output_dir == str(output_dir)
    assert result.input_count == 1
    assert result.output_count == 1
    assert result.failed_count == 0
    _, kwargs = mock_run.call_args
    assert kwargs["step_dir"] == str(output_dir)
    assert kwargs["input_source"] == str(failed)
    assert kwargs["legacy_task_config"]["keyword"] == "hf-3c"


def test_rerun_failed_selects_calc_step_by_one_based_index(tmp_path):
    config = _write_config(tmp_path / "confflow.yaml")
    step_dir = tmp_path / "work" / "step_02_calc1"
    _write_failed_xyz(step_dir)

    def fake_run_calc_workflow_step(**kwargs):
        os.makedirs(kwargs["step_dir"])
        output = Path(kwargs["step_dir"]) / "result.xyz"
        output.write_text("2\nok\nC 0 0 0\nH 0 0 1\n", encoding="utf-8")
        return CalcStepExecutionResult(output_path=str(output))

    with patch(
        "confflow.workflow.rerun_failed.calc.run_calc_workflow_step",
        side_effect=fake_run_calc_workflow_step,
    ):
        result = run_rerun_failed(step_dir=str(step_dir), config_file=str(config), step_ref="2")

    assert result.step_label == "2:calc1"
    assert result.output_dir == f"{step_dir}_rerun"


def test_rerun_failed_rejects_non_calc_step(tmp_path):
    config = _write_config(tmp_path / "confflow.yaml")
    step_dir = tmp_path / "work" / "step_01_gen"
    _write_failed_xyz(step_dir)

    with pytest.raises(RerunFailedUsageError, match="not calc/task"):
        run_rerun_failed(step_dir=str(step_dir), config_file=str(config), step_ref="gen")


def test_rerun_failed_rejects_existing_output_dir(tmp_path):
    config = _write_config(tmp_path / "confflow.yaml")
    step_dir = tmp_path / "work" / "step_02_calc1"
    _write_failed_xyz(step_dir)
    output_dir = tmp_path / "rerun"
    output_dir.mkdir()

    with pytest.raises(RerunFailedUsageError, match="already exists"):
        run_rerun_failed(
            step_dir=str(step_dir),
            config_file=str(config),
            step_ref="calc1",
            output_dir=str(output_dir),
        )


def test_cli_rerun_failed_does_not_call_run_workflow(tmp_path):
    from confflow.workflow.rerun_failed import RerunFailedResult

    config = _write_config(tmp_path / "confflow.yaml")
    step_dir = tmp_path / "work" / "step_02_calc1"
    failed = _write_failed_xyz(step_dir)
    output_dir = tmp_path / "rerun"

    with (
        patch("confflow.cli.run_workflow") as mock_workflow,
        patch("confflow.cli.run_rerun_failed") as mock_rerun,
    ):
        mock_rerun.return_value = RerunFailedResult(
            failed_path=str(failed),
            config_file=str(config),
            step_label="2:calc1",
            output_dir=str(output_dir),
            input_count=1,
            output_count=1,
            failed_count=0,
        )
        result = main(
            [
                "--rerun-failed",
                str(step_dir),
                "-c",
                str(config),
                "--step",
                "calc1",
                "-o",
                str(output_dir),
            ]
        )

    assert result == ExitCode.SUCCESS
    mock_rerun.assert_called_once_with(
        step_dir=str(step_dir),
        config_file=str(config),
        step_ref="calc1",
        output_dir=str(output_dir),
    )
    mock_workflow.assert_not_called()


def test_cli_normal_path_still_calls_run_workflow(tmp_path):
    input_xyz = tmp_path / "input.xyz"
    input_xyz.write_text("2\ntest\nC 0 0 0\nH 0 0 1\n", encoding="utf-8")
    config = _write_config(tmp_path / "confflow.yaml")

    with patch("confflow.cli.run_workflow") as mock_workflow:
        result = main([str(input_xyz), "-c", str(config), "-w", str(tmp_path / "work")])

    assert result == ExitCode.SUCCESS
    mock_workflow.assert_called_once()
