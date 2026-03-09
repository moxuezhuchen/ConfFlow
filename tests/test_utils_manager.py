#!/usr/bin/env python3

"""Tests for utils and manager modules (merged)."""

from __future__ import annotations

import os
from unittest.mock import MagicMock, patch

import pytest

from confflow.calc.manager import ChemTaskManager
from confflow.core.utils import (
    ConfFlowLogger,
    InputFileError,
    XYZFormatError,
    format_duration_hms,
    format_index_ranges,
    parse_index_spec,
    parse_iprog,
    parse_itask,
    parse_memory,
    validate_xyz_file,
    validate_yaml_config,
)

# =============================================================================
# utils tests
# =============================================================================


def test_validate_yaml_config_requires_chains_for_confgen():
    cfg = {
        "global": {
            "cores_per_task": 1,
            "max_parallel_jobs": 1,
            "gaussian_path": "g16",
            "orca_path": "orca",
        },
        "steps": [
            {"name": "step_01", "type": "confgen", "params": {}},
        ],
    }
    errors = validate_yaml_config(cfg)
    assert any("confgen step requires" in e for e in errors)


def test_validate_yaml_config_accepts_freeze_list_global():
    cfg = {
        "global": {
            "cores_per_task": 1,
            "max_parallel_jobs": 1,
            "gaussian_path": "g16",
            "orca_path": "orca",
            "freeze": [86, 92],
        },
        "steps": [
            {"name": "step_01", "type": "confgen", "params": {"chains": ["1-2-3-4"]}},
        ],
    }
    errors = validate_yaml_config(cfg)
    assert errors == []


def test_validate_yaml_config_accepts_confgen_bond_overrides():
    cfg = {
        "global": {
            "cores_per_task": 1,
            "max_parallel_jobs": 1,
            "gaussian_path": "g16",
            "orca_path": "orca",
        },
        "steps": [
            {
                "name": "step_01",
                "type": "confgen",
                "params": {
                    "chains": ["1-2-3-4"],
                    "add_bond": [[1, 2]],
                    "del_bond": [[3, 4]],
                    "no_rotate": [[2, 3]],
                    "force_rotate": [[2, 3]],
                },
            },
        ],
    }
    errors = validate_yaml_config(cfg)
    assert errors == []


def test_validate_xyz_file_errors(tmp_path):
    with pytest.raises(InputFileError, match="File does not exist"):
        validate_xyz_file(str(tmp_path / "nonexistent.xyz"))

    with pytest.raises(InputFileError, match="Path is not a file"):
        validate_xyz_file(str(tmp_path))

    empty = tmp_path / "empty.xyz"
    empty.write_text("")
    with pytest.raises(InputFileError, match="File is empty"):
        validate_xyz_file(str(empty))

    f1 = tmp_path / "f1.xyz"
    f1.write_text("abc\ntest\nC 0 0 0\n")
    valid, geoms = validate_xyz_file(str(f1))
    assert not valid
    assert len(geoms) == 0

    f2 = tmp_path / "f2.xyz"
    f2.write_text("-1\ntest\nC 0 0 0\n")
    valid, geoms = validate_xyz_file(str(f2))
    assert not valid

    f3 = tmp_path / "f3.xyz"
    f3.write_text("2\ntest\nC 0 0 0\n")
    valid, geoms = validate_xyz_file(str(f3))
    assert not valid

    f4 = tmp_path / "f4.xyz"
    f4.write_text("1\ntest\nC123 0 0 0\n")
    valid, geoms = validate_xyz_file(str(f4))
    assert not valid

    with pytest.raises(XYZFormatError):
        validate_xyz_file(str(f4), strict=True)


def test_validate_yaml_config_errors():
    errors = validate_yaml_config({})
    assert any("missing required section" in e for e in errors)

    config = {
        "global": {
            "gaussian_path": "/nonexistent/g16",
            "orca_path": "/nonexistent/orca",
            "cores_per_task": 0,
            "max_parallel_jobs": "abc",
        },
        "steps": "not a list",
    }
    errors = validate_yaml_config(config)
    assert any("Gaussian path not found" in e for e in errors)
    assert any("ORCA path not found" in e for e in errors)
    assert any("invalid cores_per_task" in e for e in errors)
    assert any("invalid max_parallel_jobs" in e for e in errors)
    assert any("'steps' must be a list" in e for e in errors)


def test_validate_step_config_errors():
    from confflow.core.utils import _validate_step_config

    errors = _validate_step_config({}, 0)
    assert any("missing 'name' field" in e for e in errors)
    assert any("missing 'type' field" in e for e in errors)

    errors = _validate_step_config({"name": "s1", "type": "invalid"}, 0)
    assert any("invalid type" in e for e in errors)

    step = {
        "name": "s1",
        "type": "calc",
        "params": {
            "itask": "invalid",
            "iprog": "invalid",
        },
    }
    errors = _validate_step_config(step, 0)
    assert any("invalid itask value" in e for e in errors)
    assert any("invalid iprog value" in e for e in errors)

    step = {"name": "s1", "type": "calc", "params": {"iprog": "orca"}}
    errors = _validate_step_config(step, 0)
    assert any("ORCA task missing 'keyword' parameter" in e for e in errors)

    step = {"name": "s1", "type": "confgen", "params": {"add_bond": "invalid"}}
    errors = _validate_step_config(step, 0)
    assert any("requires 'chains'" in e for e in errors)
    assert any("format error" in e for e in errors)


def test_manager_default_backup_dir_is_step_local(tmp_path):
    mgr = ChemTaskManager(settings={"iprog": "orca", "itask": "sp", "keyword": "B3LYP"})
    mgr.work_dir = str(tmp_path / "step_01")
    mgr._ensure_work_dir()

    assert mgr.backup_dir == os.path.join(mgr.work_dir, "backups")
    assert os.path.isdir(mgr.backup_dir)


def test_parse_index_spec():
    assert parse_index_spec("1-3,5") == [1, 2, 3, 5]
    assert parse_index_spec("10") == [10]
    assert parse_index_spec("") == []


def test_parse_index_spec_extended():
    assert parse_index_spec("0") == []
    assert parse_index_spec("none") == []
    assert parse_index_spec("false") == []
    assert parse_index_spec([1, "2-3", 5]) == [1, 2, 3, 5]
    assert parse_index_spec("1-0") == []
    assert parse_index_spec("abc 123 def") == [123]


def test_format_index_ranges():
    assert format_index_ranges([1, 2, 3, 5]) == "1-3,5"
    assert format_index_ranges([10]) == "10"
    assert format_index_ranges([]) == "none"


def test_format_duration_hms():
    assert format_duration_hms(3661) == "1:01:01"
    assert format_duration_hms(60) == "1:00"


def test_parse_memory_extended():
    assert parse_memory("4GB") == 4096
    assert parse_memory("4GB", unit="GB") == 4
    assert parse_memory("1024MB") == 1024
    assert parse_memory("1024") == 1024
    assert parse_memory("invalid") == 4096


def test_parse_iprog_itask():
    assert parse_iprog({"iprog": "g16"}) == 1
    assert parse_iprog("orca") == 2
    assert parse_iprog(1) == 1
    assert parse_iprog("invalid") == 2

    assert parse_itask({"itask": "opt"}) == 0
    assert parse_itask("sp") == 1
    assert parse_itask("4") == 4
    assert parse_itask("invalid") == 3


def test_logger_embedded_mode():
    ConfFlowLogger._initialized = False
    ConfFlowLogger()

    with patch("logging.getLogger") as mock_get:
        mock_root = MagicMock()
        mock_root.hasHandlers.return_value = True
        mock_get.side_effect = lambda name=None: mock_root if name is None else MagicMock()

        ConfFlowLogger._initialized = False
        ConfFlowLogger()
        assert ConfFlowLogger._embedded_mode is True

    ConfFlowLogger.set_embedded_mode(True)
    assert ConfFlowLogger._embedded_mode is True
    ConfFlowLogger.set_embedded_mode(False)
    assert ConfFlowLogger._embedded_mode is False


def test_logger_file_handler(tmp_path):
    log_file = tmp_path / "test.log"
    logger = ConfFlowLogger()
    ConfFlowLogger._embedded_mode = False
    logger.add_file_handler(str(log_file))
    assert "file" in logger.handlers
    logger.info("test message")
    logger.close()
    assert log_file.exists()


# =============================================================================
# manager tests
# =============================================================================


def test_manager_init_no_config():
    manager = ChemTaskManager(None)
    assert manager.config == {}
    assert "chem_tasks_" in manager.work_dir


def test_manager_init_with_config(tmp_path):
    cfg = tmp_path / "test.ini"
    cfg.write_text("[DEFAULT]\nprogram = gaussian\n")
    manager = ChemTaskManager(str(cfg))
    assert manager.config["program"] == "gaussian"


def test_manager_ensure_work_dir(tmp_path):
    manager = ChemTaskManager(None, resume_dir=str(tmp_path / "work"))
    manager._ensure_work_dir()
    assert os.path.exists(tmp_path / "work")
    assert manager.results_db is not None
    assert manager.backup_dir is not None


def test_read_single_frame_xyz_coords(tmp_path):
    manager = ChemTaskManager(None)
    xyz = tmp_path / "test.xyz"
    xyz.write_text("2\n\nC 0 0 0\nH 0 0 1\n")
    coords = manager._read_single_frame_xyz_coords(str(xyz))
    assert coords is not None
    assert len(coords) == 2
    assert "C" in coords[0]
    assert "0" in coords[0]


def test_read_single_frame_xyz_coords_invalid(tmp_path):
    manager = ChemTaskManager(None)
    xyz = tmp_path / "bad.xyz"
    xyz.write_text("not an xyz")
    assert manager._read_single_frame_xyz_coords(str(xyz)) is None
    assert manager._read_single_frame_xyz_coords("nonexistent.xyz") is None


def test_read_xyz_basic(tmp_path):
    mgr = ChemTaskManager(None)
    xyz = tmp_path / "test.xyz"
    xyz.write_text("2\ncomment\nC 0 0 0\nC 1.5 0 0\n")
    confs = mgr._read_xyz(str(xyz))
    assert len(confs) == 1
    assert confs[0]["title"] == "comment"
    assert len(confs[0]["coords"]) == 2


def test_read_xyz_fallback(tmp_path):
    manager = ChemTaskManager(None)
    xyz_path = tmp_path / "bad.xyz"
    xyz_path.write_text(
        "2\ncomment\nC 0.0 0.0 0.0\nC 1.5 0.0 0.0\n\n3\nnext\nO 0 0 0\nH 1 0 0\nH 0 1 0"
    )

    geoms = manager._read_xyz(str(xyz_path))
    assert len(geoms) == 2
    assert geoms[0]["title"] == "comment"
    assert len(geoms[0]["coords"]) == 2


def test_recover_result_from_backups_gaussian(tmp_path):
    mgr = ChemTaskManager(None)
    mgr.backup_dir = str(tmp_path / "backup")
    os.makedirs(mgr.backup_dir)

    log = tmp_path / "backup" / "job1.log"
    log.write_text("Normal termination of Gaussian 16\nSCF Done: E(RB3LYP) = -1.0\n")

    xyz = tmp_path / "backup" / "job1.xyz"
    xyz.write_text("2\n\nC 0 0 0\nC 1.5 0 0\n")

    task = {"job_name": "job1", "config": {"iprog": 1}}
    res = mgr._recover_result_from_backups(task)
    assert res["status"] == "success"
    assert res["energy"] == -1.0


def test_manager_run_stop_beacon(tmp_path):
    settings = tmp_path / "settings.ini"
    settings.write_text("[calc]\nmax_parallel_jobs=1\n")

    xyz = tmp_path / "test.xyz"
    xyz.write_text("2\ntest\nC 0 0 0\nH 0 0 1\n2\ntest2\nC 0 0 0\nH 0 0 2\n")

    manager = ChemTaskManager(str(settings), resume_dir=str(tmp_path / "work"))
    manager._ensure_work_dir()

    stop_file = tmp_path / "work" / "STOP"
    stop_file.touch()

    manager.run(str(xyz))
    assert manager.stop_requested is True


def test_manager_run_failed_output(tmp_path, monkeypatch):
    import confflow.calc.manager

    settings = tmp_path / "settings.ini"
    settings.write_text("[calc]\nmax_parallel_jobs=1\n")

    xyz = tmp_path / "test.xyz"
    xyz.write_text("2\ntest\nC 0 0 0\nH 0 0 1\n")

    manager = ChemTaskManager(str(settings), resume_dir=str(tmp_path / "work"))

    monkeypatch.setattr(
        confflow.calc.manager,
        "_run_task",
        lambda t: {"job_name": t["job_name"], "status": "failed", "error": "test error"},
    )

    manager.run(str(xyz))

    failed_file = tmp_path / "work" / "failed.xyz"
    assert failed_file.exists()


def test_manager_stop_beacon_async(tmp_path, monkeypatch):
    xyz_file = tmp_path / "input.xyz"
    xyz_file.write_text("1\ntest\nH 0.0 0.0 0.0\n1\ntest2\nH 0.0 0.0 1.0\n")

    settings_file = tmp_path / "settings.ini"
    settings_file.write_text("[Global]\nmax_parallel_jobs=1\n")

    manager = ChemTaskManager(str(settings_file), resume_dir=str(tmp_path / "work"))
    manager._ensure_work_dir()

    monkeypatch.setattr("confflow.calc.manager.as_completed", lambda x: x)

    class SyncExecutor:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def shutdown(self, *args, **kwargs):
            pass

        def submit(self, func, *args, **kwargs):
            stop_file = tmp_path / "work" / "STOP"
            stop_file.write_text("")
            res = func(*args, **kwargs)
            fut = MagicMock()
            fut.result.return_value = res
            return fut

    monkeypatch.setattr("confflow.calc.manager.ProcessPoolExecutor", SyncExecutor)

    def fake_run_task(task):
        return {
            "job_name": task["job_name"],
            "status": "success",
            "energy": -1.0,
            "final_coords": task["coords"],
        }

    with patch("confflow.calc.manager._run_task", side_effect=fake_run_task):
        manager.run(str(xyz_file))

    assert manager.stop_requested is True


def test_manager_auto_clean(tmp_path, monkeypatch):
    xyz_file = tmp_path / "input.xyz"
    xyz_file.write_text("1\ntest\nH 0.0 0.0 0.0\n")

    settings_file = tmp_path / "settings.ini"
    settings_file.write_text("[Global]\nauto_clean=true\nclean_opts=-t 0.1 -ewin 10.0\n")

    manager = ChemTaskManager(str(settings_file), resume_dir=str(tmp_path / "work"))
    manager._ensure_work_dir()

    manager.results_db.insert_result(
        {
            "job_name": "A000001",
            "status": "success",
            "energy": -1.0,
            "final_coords": ["H 0.0 0.0 0.0"],
        }
    )

    mock_refine = MagicMock()
    monkeypatch.setattr("confflow.blocks.refine.process_xyz", mock_refine)

    manager.run(str(xyz_file))

    assert mock_refine.called


def test_manager_recover_orca(tmp_path):
    manager = ChemTaskManager("", resume_dir=str(tmp_path / "work"))
    manager.backup_dir = str(tmp_path / "backup")
    os.makedirs(manager.backup_dir)

    log_file = tmp_path / "backup" / "job1.out"
    log_file.write_text(
        "FINAL SINGLE POINT ENERGY      -123.456\n****ORCA TERMINATED NORMALLY****\n"
    )

    xyz_file = tmp_path / "backup" / "job1.xyz"
    xyz_file.write_text("1\ntest\nH 0.0 0.0 0.0\n")

    task = {
        "job_name": "job1",
        "config": {"iprog": "orca", "itask": "sp"},
    }

    res = manager._recover_result_from_backups(task)
    assert res is not None
    assert res["status"] == "success"
    assert res["energy"] == -123.456


def test_manager_read_xyz_errors(tmp_path):
    manager = ChemTaskManager("")
    assert manager._read_xyz("non_existent.xyz") == []

    bad_xyz = tmp_path / "bad.xyz"
    bad_xyz.write_text("not_a_number\ncomment\nH 0 0 0\n")
    assert manager._read_xyz(str(bad_xyz)) == []

    truncated_xyz = tmp_path / "truncated.xyz"
    truncated_xyz.write_text("2\ncomment\nH 0 0 0\n")
    assert manager._read_xyz(str(truncated_xyz)) == []


# =============================================================================
# Manager path-coverage tests (merged from test_calc_manager_paths.py)
# =============================================================================


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
    from types import SimpleNamespace

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
                "job_name": "A000001",
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
