#!/usr/bin/env python3
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
        patch("confflow.blocks.refine.process_xyz", side_effect=Exception("boom")),
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
