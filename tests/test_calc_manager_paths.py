#!/usr/bin/env python3
"""Path-handling tests for calc manager edge cases."""

from __future__ import annotations

import os
from types import SimpleNamespace
from unittest.mock import patch

import pytest


def test_manager_main_cli(tmp_path):
    from confflow.calc.manager import main as manager_main

    xyz_path = tmp_path / "test.xyz"
    xyz_path.write_text("1\n\nH 0 0 0\n")
    ini_path = tmp_path / "test.ini"
    ini_path.write_text("[global]\nengine=orca\n")

    with patch("confflow.calc.manager.ChemTaskManager.run") as mock_run:
        with patch("sys.argv", ["confcalc", str(xyz_path), "-s", str(ini_path)]):
            manager_main()
            mock_run.assert_called_once()

    with patch("sys.argv", ["confcalc", "nonexistent.xyz", "-s", str(ini_path)]):
        with pytest.raises(SystemExit) as e:
            manager_main()
        assert e.value.code == 1

    with patch("sys.argv", ["confcalc", str(xyz_path), "-s", "nonexistent.ini"]):
        with pytest.raises(SystemExit) as e:
            manager_main()
        assert e.value.code == 1


def test_manager_read_xyz_fallback_more(tmp_path):
    from confflow.calc.manager import ChemTaskManager

    mgr = ChemTaskManager(None)

    tmp_path / "bad.xyz"
    mgr = ChemTaskManager(settings_file="", resume_dir=str(tmp_path / "wd"))

    mgr._ensure_work_dir()
    stop_path = mgr.config["stop_beacon_file"]
    os.makedirs(os.path.dirname(stop_path), exist_ok=True)
    with open(stop_path, "w") as f:
        f.write("STOP")

    geoms = [
        {"title": "a", "coords": ["H 0 0 0"], "metadata": {}},
        {"title": "b", "coords": ["H 0 0 1"], "metadata": {}},
    ]

    class FakeResultsDB:
        def __init__(self, *args, **kwargs):
            self.inserted = []

        def get_result_by_job_name(self, job_name):
            return None

        def insert_result(self, res):
            self.inserted.append(res)

        def get_all_results(self):
            return []

    class _Fut:
        def __init__(self, result):
            self._result = result

        def result(self):
            return self._result

    class FakeExec:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def shutdown(self, *args, **kwargs):
            pass

        def submit(self, fn, arg):
            return _Fut(
                {"job_name": arg["job_name"], "status": "success", "final_coords": ["H 0 0 0"]}
            )

    with (
        patch("confflow.calc.manager.ResultsDB", FakeResultsDB),
        patch.object(ChemTaskManager, "_read_xyz", return_value=geoms),
        patch("confflow.calc.manager.ProcessPoolExecutor", FakeExec),
        patch("confflow.calc.manager.as_completed", lambda futs: list(futs)),
        patch("confflow.calc.manager.CalcProgressReporter"),
        patch("confflow.calc.manager.parse_iprog", return_value=1),
        patch("confflow.calc.manager.get_policy"),
        patch("confflow.calc.manager._cleanup_lingering_processes") as mock_cleanup,
    ):
        mgr.run(str(tmp_path / "input.xyz"))
        assert mock_cleanup.called


def test_calc_manager_failed_output_and_auto_clean_parse_errors(tmp_path):
    from confflow.calc.manager import ChemTaskManager

    mgr = ChemTaskManager(settings_file="", resume_dir=str(tmp_path / "wd"))
    mgr.config.update(
        {
            "auto_clean": "true",
            "clean_opts": "-t nope -ewin nope",
            "cores_per_task": "2",
            "max_parallel_jobs": "1",
        }
    )

    geoms = [
        {
            "title": "geom1",
            "coords": ["H 0 0 0", "H 0 0 1"],
            "metadata": {"CID": "123"},
        }
    ]

    long_err = "x" * 500

    class FakeResultsDB:
        def __init__(self, *args, **kwargs):
            self.inserted = []

        def get_result_by_job_name(self, job_name):
            return None

        def insert_result(self, res):
            self.inserted.append(res)

        def get_all_results(self):
            return [
                {"job_name": "A000001", "status": "failed", "error": long_err},
                {
                    "job_name": "A000001",
                    "status": "success",
                    "energy": -1.0,
                    "final_coords": ["H 0 0 0", "H 0 0 1"],
                    "num_imag_freqs": 1,
                    "lowest_freq": -12.3,
                    "ts_bond_atoms": "1,2",
                    "ts_bond_length": 1.234567,
                },
            ]

        def close(self):
            pass

    with (
        patch("confflow.calc.manager.ResultsDB", FakeResultsDB),
        patch.object(ChemTaskManager, "_read_xyz", return_value=geoms),
        patch(
            "confflow.calc.manager._run_task",
            return_value={
                "job_name": "c0001",
                "status": "success",
                "final_coords": ["H 0 0 0", "H 0 0 1"],
                "energy": -1.0,
            },
        ),
        patch("confflow.blocks.refine.RefineOptions") as mock_opts,
        patch("confflow.blocks.refine.process_xyz", side_effect=RuntimeError("boom")),
    ):
        mock_opts.return_value = SimpleNamespace(output=str(tmp_path / "wd" / "output.xyz"))
        mgr.run(str(tmp_path / "input.xyz"))

        assert (tmp_path / "wd" / "failed.xyz").exists()
        assert (tmp_path / "wd" / "result.xyz").exists()


def test_calc_manager_executor_path_inserts_results(tmp_path):
    from confflow.calc.manager import ChemTaskManager

    geoms = [
        {"title": "a", "coords": ["H 0 0 0"], "metadata": {"CID": "1"}},
        {"title": "b", "coords": ["H 0 0 1"], "metadata": {"CID": "2"}},
    ]

    class FakeResultsDB:
        def __init__(self, *args, **kwargs):
            self.inserted = []

        def get_result_by_job_name(self, job_name):
            return None

        def insert_result(self, res):
            self.inserted.append(res)

        def get_all_results(self):
            return list(self.inserted)

        def close(self):
            pass

    class _Fut:
        def __init__(self, result):
            self._result = result

        def result(self):
            return self._result

    class FakeExec:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def shutdown(self, *args, **kwargs):
            pass

        def submit(self, fn, arg):
            return _Fut(
                {
                    **arg,
                    "status": "success",
                    "energy": -1.0,
                    "final_coords": arg.get("coords") or ["H 0 0 0"],
                }
            )

    with (
        patch("confflow.calc.manager.ResultsDB", FakeResultsDB),
        patch.object(ChemTaskManager, "_read_xyz", return_value=geoms),
        patch("confflow.calc.manager.ProcessPoolExecutor", FakeExec),
        patch("confflow.calc.manager.as_completed", lambda futs: list(futs)),
        patch("confflow.calc.manager.CalcProgressReporter"),
    ):
        mgr = ChemTaskManager(settings_file="", resume_dir=str(tmp_path / "wd"))
        mgr.run(str(tmp_path / "input.xyz"))

        assert (tmp_path / "wd" / "result.xyz").exists()


def test_validate_executable_setting_rejects_argument_string():
    from confflow.core.exceptions import ExecutionPolicyError
    from confflow.core.path_policy import validate_executable_setting

    assert validate_executable_setting("g16", label="gaussian_path") == "g16"
    with pytest.raises(ExecutionPolicyError, match="exactly one executable"):
        validate_executable_setting("g16 --debug", label="gaussian_path")


def test_validate_executable_setting_absolute_allowlist_requires_exact_path(tmp_path):
    from confflow.core.exceptions import ExecutionPolicyError
    from confflow.core.path_policy import validate_executable_setting

    allowed = str(tmp_path / "opt" / "orca")
    malicious = str(tmp_path / "tmp" / "orca")

    with pytest.raises(ExecutionPolicyError, match="not allowed"):
        validate_executable_setting(
            malicious,
            label="orca_path",
            allowed_executables=[allowed],
        )


def test_manager_rejects_backup_dir_outside_sandbox(tmp_path):
    from confflow.calc.manager import ChemTaskManager
    from confflow.core.exceptions import PathSafetyError

    sandbox = tmp_path / "sandbox"
    sandbox.mkdir()
    outside = tmp_path / "outside"

    mgr = ChemTaskManager(settings={"sandbox_root": str(sandbox), "backup_dir": str(outside)})
    mgr.work_dir = str(sandbox / "work")

    with pytest.raises(PathSafetyError, match="backup_dir escapes sandbox_root"):
        mgr._ensure_work_dir()


def test_manager_rejects_work_dir_outside_sandbox_before_cleanup(tmp_path):
    from confflow.calc.manager import ChemTaskManager
    from confflow.core.exceptions import PathSafetyError

    sandbox = tmp_path / "sandbox"
    sandbox.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    victim = outside / "should_stay.txt"
    victim.write_text("keep", encoding="utf-8")

    mgr = ChemTaskManager(settings={"sandbox_root": str(sandbox)})
    mgr.work_dir = str(outside)

    with pytest.raises(PathSafetyError, match="work_dir escapes sandbox_root"):
        mgr.run(str(tmp_path / "missing.xyz"))

    assert victim.read_text(encoding="utf-8") == "keep"
