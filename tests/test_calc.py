#!/usr/bin/env python3

"""Comprehensive tests for calc module - merged from test_calc_core_extended.py and test_calc_core_extended_v2.py."""

from __future__ import annotations

import importlib
import os
import sys
from unittest.mock import MagicMock, patch

import pytest

# =============================================================================
# calc.core tests
# =============================================================================


class TestCalcCore:
    """Tests for calc.setup module."""

    def test_get_itask_variants(self):
        from confflow.calc.setup import get_itask

        assert get_itask({"itask": "opt"}) == 0
        assert get_itask({"itask": "sp"}) == 1
        assert get_itask({"itask": "freq"}) == 2
        assert get_itask({"itask": "opt_freq"}) == 3
        assert get_itask({"itask": 1}) == 1
        assert get_itask({"itask": "1"}) == 1
        assert get_itask({}) == 3  # Default

    def test_parse_iprog_variants(self):
        from confflow.calc.setup import parse_iprog

        assert parse_iprog({"iprog": "gaussian"}) == 1
        assert parse_iprog({"iprog": "g16"}) == 1
        assert parse_iprog({"iprog": "orca"}) == 2
        assert parse_iprog({"iprog": 1}) == 1
        assert parse_iprog({}) == 2  # Default

    def test_setup_logging_fallback(self, tmp_path, monkeypatch):
        import confflow.calc.setup

        monkeypatch.setattr(confflow.calc.setup, "UTILS_AVAILABLE", False)

        log_dir = tmp_path / "logs_fallback"
        logger = confflow.calc.setup.setup_logging(str(log_dir))
        assert logger is not None
        log_file = log_dir / "calc.log"
        assert log_file.exists()

    def test_calc_core_fallback_logic(self):
        with patch.dict(sys.modules, {"confflow.core.utils": None}):
            import confflow.calc.setup as core_mod

            importlib.reload(core_mod)

            from confflow.calc.setup import utils_parse_iprog, utils_parse_itask

            assert utils_parse_itask({"itask": 1}) == 1
            assert utils_parse_itask({"itask": "opt"}) == 0
            assert utils_parse_itask({"itask": "3"}) == 3
            assert utils_parse_itask({}) == 3

            assert utils_parse_iprog({"iprog": 1}) == 1
            assert utils_parse_iprog({"iprog": "gaussian"}) == 1
            assert utils_parse_iprog({"iprog": "orca"}) == 2
            assert utils_parse_iprog({}) == 1

        import confflow.calc.setup as core_mod

        importlib.reload(core_mod)


# =============================================================================
# calc.resources tests
# =============================================================================


class TestResourceMonitor:
    """Tests for ResourceMonitor."""

    def test_resource_monitor_disabled(self, monkeypatch):
        from confflow.calc import resources

        monkeypatch.setattr(resources, "psutil", None)

        monitor = resources.ResourceMonitor()
        assert monitor.enabled is False
        assert monitor.get_current_load() == (0.0, 0.0)
        assert monitor.can_start_new_task(1, 1) is True
        assert monitor.wait_for_resources() is True

    def test_resource_monitor_enabled(self, monkeypatch):
        from confflow.calc import resources

        mock_psutil = MagicMock()
        mock_psutil.cpu_percent.return_value = 50.0
        mock_psutil.virtual_memory.return_value.percent = 60.0
        monkeypatch.setattr(resources, "psutil", mock_psutil)

        monitor = resources.ResourceMonitor(cpu_threshold=80, mem_threshold=80)
        monitor.enabled = True

        assert monitor.get_current_load() == (50.0, 60.0)
        assert monitor.can_start_new_task(1, 4) is True

        mock_psutil.cpu_percent.return_value = 90.0
        assert monitor.can_start_new_task(1, 4) is False

        mock_psutil.cpu_percent.return_value = 50.0
        assert monitor.can_start_new_task(4, 4) is False

    def test_resource_monitor_wait(self, monkeypatch):
        from confflow.calc import resources

        mock_psutil = MagicMock()
        mock_psutil.cpu_percent.side_effect = [90.0, 40.0]
        mock_psutil.virtual_memory.return_value.percent = 50.0
        monkeypatch.setattr(resources, "psutil", mock_psutil)

        monitor = resources.ResourceMonitor(cpu_threshold=80, check_interval=0.1)
        monitor.enabled = True

        assert monitor.wait_for_resources(max_wait_seconds=1) is True

    def test_resource_monitor_exception(self, monkeypatch):
        from confflow.calc import resources

        mock_psutil = MagicMock()
        mock_psutil.cpu_percent.side_effect = RuntimeError("psutil error")
        monkeypatch.setattr(resources, "psutil", mock_psutil)

        monitor = resources.ResourceMonitor()
        monitor.enabled = True

        assert monitor.get_current_load() == (0.0, 0.0)

    def test_wait_loop(self, monkeypatch):
        from confflow.calc.resources import ResourceMonitor

        monitor = ResourceMonitor(cpu_threshold=50, mem_threshold=50, check_interval=0.1)

        loads = [(90.0, 90.0), (90.0, 90.0), (10.0, 10.0)]

        def mock_get_load():
            return loads.pop(0) if loads else (10.0, 10.0)

        monkeypatch.setattr(monitor, "get_current_load", mock_get_load)
        assert monitor.wait_for_resources(max_wait_seconds=1) is True

    def test_wait_timeout(self, monkeypatch):
        from confflow.calc.resources import ResourceMonitor

        monitor = ResourceMonitor(cpu_threshold=50, mem_threshold=50, check_interval=0.1)
        monkeypatch.setattr(monitor, "get_current_load", lambda: (90.0, 90.0))

        assert monitor.wait_for_resources(max_wait_seconds=0.2) is False


# =============================================================================
# calc.components.executor tests
# =============================================================================


class TestExecutor:
    """Tests for executor module."""

    def test_cleanup_lingering_processes(self):
        from confflow.calc.components.executor import _cleanup_lingering_processes

        policy = MagicMock()
        config = {"test": "config"}
        _cleanup_lingering_processes(config, policy)
        policy.cleanup_lingering_processes.assert_called_once_with(config)

    def test_get_error_details_fallback(self):
        from confflow.calc.components.executor import _get_error_details

        policy = MagicMock()
        policy.get_error_details.return_value = "Policy error"

        res = _get_error_details("work", "job", {}, Exception("test"), policy)
        assert res == "Policy error"

        res = _get_error_details("work", "job", {}, Exception("test"), None)
        assert "test" in res

    def test_handle_backups_rmtree_failure(self, tmp_path, monkeypatch):
        import shutil

        from confflow.calc.components.executor import handle_backups

        work_dir = tmp_path / "work"
        work_dir.mkdir()
        (work_dir / "test.tmp").write_text("temp")

        def mock_rmtree(path):
            raise OSError("Permission denied")

        monkeypatch.setattr(shutil, "rmtree", mock_rmtree)
        handle_backups(str(work_dir), {"ibkout": 0}, success=True, cleanup_work_dir=True)

        assert not os.path.exists(work_dir / "test.tmp")

    def test_handle_backups_with_scan(self, tmp_path):
        from confflow.calc.components.executor import handle_backups

        work_dir = tmp_path / "work"
        work_dir.mkdir()
        scan_dir = work_dir / "scan"
        scan_dir.mkdir()
        (scan_dir / "scan.log").write_text("scan data")

        backup_dir = tmp_path / "backup"

        handle_backups(str(work_dir), {"ibkout": 1, "backup_dir": str(backup_dir)}, success=True)

        assert os.path.exists(backup_dir / "work_scan" / "scan.log")

    def test_handle_backups_ibkout_0(self, tmp_path):
        from confflow.calc.components.executor import handle_backups

        work_dir = tmp_path / "work"
        work_dir.mkdir()
        (work_dir / "test.out").write_text("content")

        backup_dir = tmp_path / "backup"
        config = {"ibkout": 0, "backup_dir": str(backup_dir)}

        handle_backups(str(work_dir), config, success=True, cleanup_work_dir=False)
        assert not backup_dir.exists()

    def test_handle_backups_success_only(self, tmp_path):
        import shutil

        from confflow.calc.components.executor import handle_backups

        work_dir = tmp_path / "work"
        work_dir.mkdir()
        (work_dir / "test.out").write_text("content")

        backup_dir = tmp_path / "backup"
        config = {"ibkout": 2, "backup_dir": str(backup_dir)}

        handle_backups(str(work_dir), config, success=True, cleanup_work_dir=False)
        assert (backup_dir / "test.out").exists()

        shutil.rmtree(backup_dir)
        handle_backups(str(work_dir), config, success=False, cleanup_work_dir=False)
        assert not backup_dir.exists()

    def test_handle_backups_scan_dir(self, tmp_path):
        from confflow.calc.components.executor import handle_backups

        work_dir = tmp_path / "work"
        work_dir.mkdir()
        scan_dir = work_dir / "scan"
        scan_dir.mkdir()
        (scan_dir / "scan.out").write_text("content")

        backup_dir = tmp_path / "backup"
        config = {"ibkout": 1, "backup_dir": str(backup_dir)}

        handle_backups(str(work_dir), config, success=True, cleanup_work_dir=False)
        assert (backup_dir / "work_scan" / "scan.out").exists()

    def test_handle_backups_failure_preserves_workdir(self, tmp_path, monkeypatch):
        import shutil

        from confflow.calc.components.executor import handle_backups

        work_dir = tmp_path / "work"
        work_dir.mkdir()
        (work_dir / "test.out").write_text("content")
        backup_dir = tmp_path / "backup"

        def fail_move(src, dst):
            raise OSError("move failed")

        def fail_copy2(src, dst):
            raise OSError("copy failed")

        monkeypatch.setattr(shutil, "move", fail_move)
        monkeypatch.setattr(shutil, "copy2", fail_copy2)

        backup_ok = handle_backups(
            str(work_dir),
            {"ibkout": 1, "backup_dir": str(backup_dir)},
            success=True,
            cleanup_work_dir=True,
        )

        assert backup_ok is False
        assert work_dir.exists()
        assert (work_dir / "test.out").exists()

    def test_save_config_hash(self, tmp_path):
        from confflow.calc.components.executor import _save_config_hash

        work_dir = tmp_path / "work"
        work_dir.mkdir()
        config = {"itask": "opt", "iprog": "g16"}
        _save_config_hash(str(work_dir), config)
        assert (work_dir / ".config_hash").exists()
        h1 = (work_dir / ".config_hash").read_text()

        config2 = {"itask": "freq", "iprog": "orca"}
        _save_config_hash(str(work_dir), config2)
        h2 = (work_dir / ".config_hash").read_text()
        assert h1 != h2


# =============================================================================
# calc.db.database tests
# =============================================================================


class TestResultsDB:
    """Tests for ResultsDB."""

    def test_backup(self, tmp_path):
        from confflow.calc.db.database import ResultsDB

        db_path = tmp_path / "test.db"
        db = ResultsDB(str(db_path))
        db.insert_result({"job_name": "test", "status": "success"})

        backup_path = tmp_path / "test.db.backup"
        db.backup(str(backup_path))

        assert os.path.exists(backup_path)

        with patch("shutil.move", side_effect=OSError("Move failed")):
            with pytest.raises(OSError):
                db.backup(str(tmp_path / "ro" / "fail.db"))

    def test_column_check(self, tmp_path):
        import sqlite3

        from confflow.calc.db.database import ResultsDB

        db_path = tmp_path / "old.db"
        conn = sqlite3.connect(str(db_path))
        conn.execute("""
            CREATE TABLE task_results (
                task_id INTEGER PRIMARY KEY, 
                job_name TEXT, 
                task_index INTEGER, 
                status TEXT,
                energy REAL,
                final_gibbs_energy REAL,
                final_sp_energy REAL,
                num_imag_freqs INTEGER,
                lowest_freq REAL,
                g_corr REAL,
                final_coords TEXT,
                error TEXT
            )
        """)
        conn.close()

        db = ResultsDB(str(db_path))
        db.insert_result({"job_name": "test", "status": "success", "ts_bond_length": 1.5})

        res = db.get_result_by_job_name("test")
        assert res["status"] == "success"
        db.close()

    def test_insert_result_updates_existing_job(self, tmp_path):
        from confflow.calc.db.database import ResultsDB

        db = ResultsDB(str(tmp_path / "test.db"))
        db.insert_result({"job_name": "job1", "status": "failed", "error": "boom"})
        db.insert_result(
            {
                "job_name": "job1",
                "status": "success",
                "energy": -1.23,
                "final_coords": ["H 0 0 0"],
            }
        )

        got = db.get_result_by_job_name("job1")
        assert got is not None
        assert got["status"] == "success"
        assert got["energy"] == -1.23
        assert got["error"] is None
        assert len(db.get_all_results()) == 1
        db.close()


# =============================================================================
# Executor extended tests (from test_final_push.py)
# =============================================================================


class TestExecutorAdvanced:
    """Advanced tests for executor module."""

    def test_executor_stop_beacon(self, cd_tmp):
        """Test stop_beacon stop signal."""
        from unittest.mock import MagicMock, patch

        from confflow.calc.components.executor import _run_calculation_step

        work_dir = cd_tmp / "work"
        work_dir.mkdir()
        stop_file = cd_tmp / "stop.txt"
        stop_file.write_text("STOP")

        policy = MagicMock()
        policy.input_ext = "inp"
        policy.log_ext = "log"
        policy.name = "Mock"
        policy.get_execution_command.return_value = ["sleep", "10"]
        policy.get_environment.return_value = None

        config = {"stop_beacon_file": str(stop_file), "stop_check_interval_seconds": 0.1}

        with patch("subprocess.Popen") as mock_popen:
            mock_proc = MagicMock()
            mock_proc.poll.side_effect = [None, None, 0]
            mock_popen.return_value = mock_proc

            with pytest.raises(RuntimeError, match="STOP signal received"):
                _run_calculation_step(str(work_dir), "job", policy, None, config)

            mock_proc.kill.assert_called_once()

    @pytest.mark.parametrize(
        "value",
        [
            "bad",
            0,
            -1,
            "0",
            "nan",
            ".nan",
            "inf",
            ".inf",
            "-inf",
            float("nan"),
            float("inf"),
            float("-inf"),
        ],
    )
    def test_executor_rejects_invalid_stop_check_interval(self, cd_tmp, value):
        """Invalid stop polling intervals should fail before launching the process."""
        from unittest.mock import MagicMock, patch

        from confflow.calc.components.executor import _run_calculation_step
        from confflow.core.exceptions import ConfigurationError

        work_dir = cd_tmp / "work"
        work_dir.mkdir()

        policy = MagicMock()
        config = {"stop_check_interval_seconds": value}

        with patch("subprocess.Popen") as mock_popen:
            with pytest.raises(ConfigurationError, match="stop_check_interval_seconds"):
                _run_calculation_step(str(work_dir), "job", policy, None, config)

            mock_popen.assert_not_called()

    @pytest.mark.parametrize(
        ("config", "expected_sleep"),
        [({"stop_check_interval_seconds": 0.25}, 0.25), ({}, 1.0)],
    )
    def test_executor_uses_validated_stop_check_interval(self, cd_tmp, config, expected_sleep):
        """Valid/default stop polling intervals are parsed once and reused."""
        from unittest.mock import MagicMock, patch

        from confflow.calc.components.executor import _run_calculation_step

        work_dir = cd_tmp / "work"
        work_dir.mkdir()

        policy = MagicMock()
        policy.input_ext = "inp"
        policy.log_ext = "log"
        policy.name = "Mock"
        policy.get_execution_command.return_value = ["true"]
        policy.get_environment.return_value = None
        policy.check_termination.return_value = True
        policy.parse_output.return_value = {"energy": -1.0}

        with patch("subprocess.Popen") as mock_popen, patch("time.sleep") as mock_sleep:
            mock_proc = MagicMock()
            mock_proc.poll.side_effect = [None, 0]
            mock_proc.returncode = 0
            mock_popen.return_value = mock_proc

            result = _run_calculation_step(str(work_dir), "job", policy, None, dict(config))

        assert result == {"energy": -1.0}
        mock_popen.assert_called_once()
        mock_sleep.assert_called_once_with(expected_sleep)

    @pytest.mark.parametrize(
        "value",
        ["bad", 0, -1, "0", "nan", "inf", "-inf", float("nan"), float("inf")],
    )
    def test_executor_rejects_invalid_max_wall_time_before_launch(self, cd_tmp, value):
        """Invalid wall-time limits should fail before launching the process."""
        from unittest.mock import MagicMock, patch

        from confflow.calc.components.executor import _run_calculation_step
        from confflow.core.exceptions import ConfigurationError

        work_dir = cd_tmp / "work"
        work_dir.mkdir()

        policy = MagicMock()
        config = {"max_wall_time_seconds": value}

        with patch("subprocess.Popen") as mock_popen:
            with pytest.raises(ConfigurationError, match="max_wall_time_seconds"):
                _run_calculation_step(str(work_dir), "job", policy, None, config)

            mock_popen.assert_not_called()

    def test_executor_omitted_max_wall_time_does_not_check_timeout(self, cd_tmp):
        """Omitting max_wall_time_seconds preserves the no-timeout polling path."""
        from unittest.mock import MagicMock, patch

        from confflow.calc.components.executor import _run_calculation_step

        work_dir = cd_tmp / "work"
        work_dir.mkdir()

        policy = MagicMock()
        policy.input_ext = "inp"
        policy.log_ext = "log"
        policy.name = "Mock"
        policy.get_execution_command.return_value = ["true"]
        policy.get_environment.return_value = None
        policy.check_termination.return_value = True
        policy.parse_output.return_value = {"energy": -1.0}

        with (
            patch("subprocess.Popen") as mock_popen,
            patch("time.sleep") as mock_sleep,
            patch("time.monotonic") as mock_monotonic,
        ):
            mock_proc = MagicMock()
            mock_proc.poll.side_effect = [None, 0]
            mock_proc.returncode = 0
            mock_popen.return_value = mock_proc

            result = _run_calculation_step(str(work_dir), "job", policy, None, {})

        assert result == {"energy": -1.0}
        mock_popen.assert_called_once()
        mock_sleep.assert_called_once_with(1.0)
        mock_monotonic.assert_not_called()

    def test_executor_valid_max_wall_time_allows_quick_process(self, cd_tmp):
        """A valid wall-time limit should not affect a quickly exiting process."""
        from unittest.mock import MagicMock, patch

        from confflow.calc.components.executor import _run_calculation_step

        work_dir = cd_tmp / "work"
        work_dir.mkdir()

        policy = MagicMock()
        policy.input_ext = "inp"
        policy.log_ext = "log"
        policy.name = "Mock"
        policy.get_execution_command.return_value = ["true"]
        policy.get_environment.return_value = None
        policy.check_termination.return_value = True
        policy.parse_output.return_value = {"energy": -1.0}

        config = {"max_wall_time_seconds": 60, "stop_check_interval_seconds": 0.1}
        with (
            patch("subprocess.Popen") as mock_popen,
            patch("time.sleep") as mock_sleep,
            patch("time.monotonic", side_effect=[0.0, 0.5]),
        ):
            mock_proc = MagicMock()
            mock_proc.poll.side_effect = [None, 0]
            mock_proc.returncode = 0
            mock_popen.return_value = mock_proc

            result = _run_calculation_step(str(work_dir), "job", policy, None, config)

        assert result == {"energy": -1.0}
        mock_popen.assert_called_once()
        mock_sleep.assert_called_once_with(0.1)

    def test_executor_max_wall_time_kills_and_reaps_timed_out_process(self, cd_tmp):
        """A timed-out subprocess should be killed and reaped before reporting failure."""
        from unittest.mock import MagicMock, patch

        from confflow.calc.components.executor import _run_calculation_step
        from confflow.core.exceptions import CalculationExecutionError

        work_dir = cd_tmp / "work"
        work_dir.mkdir()

        policy = MagicMock()
        policy.input_ext = "inp"
        policy.log_ext = "log"
        policy.name = "Mock"
        policy.get_execution_command.return_value = ["sleep", "10"]
        policy.get_environment.return_value = None

        config = {"max_wall_time_seconds": 1, "stop_check_interval_seconds": 0.1}
        with (
            patch("subprocess.Popen") as mock_popen,
            patch("time.sleep") as mock_sleep,
            patch("time.monotonic", side_effect=[0.0, 1.5]),
        ):
            mock_proc = MagicMock()
            mock_proc.poll.return_value = None
            mock_popen.return_value = mock_proc

            with pytest.raises(CalculationExecutionError, match="exceeded max_wall_time_seconds"):
                _run_calculation_step(str(work_dir), "job", policy, None, config)

        mock_proc.kill.assert_called_once()
        mock_proc.wait.assert_called_once()
        mock_sleep.assert_not_called()

    def test_save_config_hash_failure(self, tmp_path):
        """Test config hash save failure."""
        from unittest.mock import patch

        from confflow.calc.components.executor import _save_config_hash

        with patch("builtins.open", side_effect=OSError("Permission denied")):
            _save_config_hash(str(tmp_path), {"itask": 1, "iprog": 1})

    def test_executor_nonzero_exit(self, cd_tmp):
        """Test nonzero exit code."""
        from unittest.mock import MagicMock, patch

        from confflow.calc.components.executor import _run_calculation_step

        work_dir = cd_tmp / "work"
        work_dir.mkdir()

        policy = MagicMock()
        policy.input_ext = "inp"
        policy.log_ext = "log"
        policy.name = "Mock"
        policy.get_execution_command.return_value = ["false"]
        policy.get_environment.return_value = None

        config = {}

        with patch("subprocess.Popen") as mock_popen:
            mock_proc = MagicMock()
            mock_proc.poll.return_value = 0
            mock_proc.returncode = 1
            mock_popen.return_value = mock_proc

            with pytest.raises(RuntimeError, match="nonzero exit"):
                _run_calculation_step(str(work_dir), "job", policy, None, config)

    def test_executor_abnormal_termination(self, cd_tmp):
        """Test abnormal termination."""
        from unittest.mock import MagicMock, patch

        from confflow.calc.components.executor import _run_calculation_step

        work_dir = cd_tmp / "work"
        work_dir.mkdir()

        policy = MagicMock()
        policy.input_ext = "inp"
        policy.log_ext = "log"
        policy.name = "Mock"
        policy.get_execution_command.return_value = ["true"]
        policy.get_environment.return_value = None
        policy.check_termination.return_value = False

        config = {}

        with patch("subprocess.Popen") as mock_popen:
            mock_proc = MagicMock()
            mock_proc.poll.return_value = 0
            mock_proc.returncode = 0
            mock_popen.return_value = mock_proc

            with pytest.raises(RuntimeError, match="Abnormal termination"):
                _run_calculation_step(str(work_dir), "job", policy, None, config)


def test_execute_tasks_marks_pending_and_canceled_on_stop(tmp_path):
    from confflow.calc.task_execution import execute_tasks
    from confflow.core.models import TaskContext

    stop_file = tmp_path / "STOP"
    inserted = []

    class FakeResultsDB:
        def insert_result(self, res):
            inserted.append(res)

    class Reporter:
        def __init__(self, *args, **kwargs):
            self.reported = []

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def report(self, status):
            self.reported.append(status)

    class Future:
        def __init__(self, result, *, done=False, cancelled=False):
            self._result = result
            self._done = done
            self._cancelled = cancelled

        def result(self):
            return self._result

        def done(self):
            return self._done

        def cancelled(self):
            return self._cancelled

    class FakeExecutor:
        def __init__(self, *args, **kwargs):
            self.futures = {}

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def shutdown(self, *args, **kwargs):
            return None

        def submit(self, fn, arg):
            job_name = arg["job_name"]
            if job_name == "A000001":
                fut = Future({**arg, "status": "success", "final_coords": arg["coords"]}, done=True)
            else:
                fut = Future(None, done=False, cancelled=True)
            self.futures[job_name] = fut
            return fut

    tasks = [
        TaskContext(
            job_name="A000001",
            work_dir=str(tmp_path / "A000001"),
            coords=["H 0 0 0"],
            config={},
        ),
        TaskContext(
            job_name="A000002",
            work_dir=str(tmp_path / "A000002"),
            coords=["H 0 0 1"],
            config={},
        ),
    ]

    def fake_as_completed(futures):
        stop_file.write_text("STOP", encoding="utf-8")
        return [next(iter(futures.keys()))]

    execute_tasks(
        todo=tasks,
        config={"max_parallel_jobs": 2, "stop_beacon_file": str(stop_file)},
        results_db=FakeResultsDB(),
        run_task_fn=lambda task: {**task, "status": "success", "final_coords": task["coords"]},
        append_result_fn=lambda res: None,
        stop_requested_fn=lambda: False,
        set_stop_requested_fn=lambda value: None,
        progress_reporter_cls=Reporter,
        executor_cls=FakeExecutor,
        as_completed_fn=fake_as_completed,
    )

    by_job = {row["job_name"]: row for row in inserted}
    assert by_job["A000001"]["status"] == "pending"
    assert by_job["A000001"]["error_kind"] == "stop_requested"
    assert by_job["A000002"]["status"] == "canceled"
    assert by_job["A000002"]["error_kind"] == "stop_requested"


def test_execute_tasks_records_broken_process_pool_and_continues(tmp_path):
    from concurrent.futures.process import BrokenProcessPool

    from confflow.calc.task_execution import execute_tasks
    from confflow.core.models import TaskContext

    inserted = []
    appended = []

    class FakeResultsDB:
        def insert_result(self, res):
            inserted.append(res)

    class Reporter:
        def __init__(self, *args, **kwargs):
            self.reported = []

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def report(self, status):
            self.reported.append(status)

    class Future:
        def __init__(self, *, result=None, exc=None):
            self._result = result
            self._exc = exc

        def result(self):
            if self._exc is not None:
                raise self._exc
            return self._result

    class FakeExecutor:
        def __init__(self, *args, **kwargs):
            self.futures = []

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def submit(self, fn, arg):
            del fn
            if arg["job_name"] == "A000001":
                fut = Future(exc=BrokenProcessPool("worker lost"))
            else:
                fut = Future(result={**arg, "status": "success", "final_coords": arg["coords"]})
            self.futures.append(fut)
            return fut

    tasks = [
        TaskContext(
            job_name="A000001",
            work_dir=str(tmp_path / "A000001"),
            coords=["H 0 0 0"],
            config={},
        ),
        TaskContext(
            job_name="A000002",
            work_dir=str(tmp_path / "A000002"),
            coords=["H 0 0 1"],
            config={},
        ),
    ]

    execute_tasks(
        todo=tasks,
        config={"max_parallel_jobs": 2},
        results_db=FakeResultsDB(),
        run_task_fn=lambda task: {**task, "status": "success", "final_coords": task["coords"]},
        append_result_fn=appended.append,
        stop_requested_fn=lambda: False,
        set_stop_requested_fn=lambda value: None,
        progress_reporter_cls=Reporter,
        executor_cls=FakeExecutor,
        as_completed_fn=lambda futures: list(futures.keys()),
    )

    by_job = {row["job_name"]: row for row in inserted}
    assert by_job["A000001"]["status"] == "failed"
    assert by_job["A000001"]["error_kind"] == "broken_process_pool"
    assert "worker lost" in by_job["A000001"]["error"]
    assert by_job["A000002"]["status"] == "success"
    assert len(appended) == 1
    assert appended[0]["job_name"] == "A000002"


# =============================================================================
# Viz Report extended tests
# =============================================================================


class TestVizReportAdvanced:
    """Tests for viz.report module."""

    def test_viz_report_failed_count_from_db(self, tmp_path):
        """Test text report includes failure count."""
        from confflow.blocks.viz.report import generate_text_report

        steps = [
            {
                "index": 1,
                "name": "TestStep",
                "type": "calc",
                "status": "completed",
                "input_conformers": 10,
                "output_conformers": 8,
                "failed_conformers": 2,
                "duration_seconds": 100,
                "metadata": {},
            }
        ]

        conformers = [{"metadata": {"E": -1.0}}]
        text = generate_text_report(conformers, stats={"steps": steps})
        assert "TestStep" in text
        assert "  2" in text


# =============================================================================
# TaskRunner and input_helpers path coverage (merged from test_task_runner_and_input_helpers_paths.py)
# =============================================================================


def test_task_runner_misses():
    from confflow.calc.components.task_runner import TaskRunner

    runner = TaskRunner()

    with pytest.raises(ValueError, match="Unsupported iprog"):
        runner._get_policy({"iprog": 999})

    with pytest.raises(KeyError):
        runner.run({})


def test_task_runner_itask3_imag(tmp_path):
    from confflow.calc.components.task_runner import TaskRunner

    runner = TaskRunner()
    task_info = {
        "job_name": "test",
        "work_dir": str(tmp_path / "work"),
        "config": {"itask": 3, "iprog": 1},
        "coords": ["C 0 0 0"],
    }

    with patch("confflow.calc.components.executor._run_calculation_step") as mock_run:
        mock_run.return_value = {
            "final_coords": ["C 0 0 0"],
            "num_imag_freqs": 1,
            "lowest_freq": -100.0,
            "e_low": -100.0,
            "g_low": -99.9,
        }
        with patch("confflow.calc.components.executor.handle_backups"):
            res = runner.run(task_info)
            assert res["status"] == "failed"
            assert res["error_kind"] == "parse_error"
            assert "has 1 imaginary frequenc" in res["error"]


def test_task_runner_itask4_no_freq_drift(tmp_path):
    from confflow.calc.components.task_runner import TaskRunner

    runner = TaskRunner()
    task_info = {
        "job_name": "test",
        "work_dir": str(tmp_path / "work"),
        "config": {
            "itask": 4,
            "iprog": 1,
            "ts_bond_atoms": "1,2",
            "ts_bond_drift_threshold": 0.1,
        },
        "coords": ["H 0 0 0", "H 0 0 1.0"],
    }

    with patch("confflow.calc.components.executor._run_calculation_step") as mock_run:
        mock_run.return_value = {"final_coords": ["H 0 0 0", "H 0 0 1.2"], "e_low": -1.0}
        with patch("confflow.calc.components.executor.handle_backups"):
            res = runner.run(task_info)
            assert res["status"] == "failed"
            assert res["error_kind"] == "parse_error"
            assert "bond drift |ΔR|=0.200 Å exceeds threshold 0.100 Å" in res["error"]


def test_task_runner_itask4_no_freq_allows_large_rmsd(tmp_path):
    from confflow.calc.components.task_runner import TaskRunner

    runner = TaskRunner()
    task_info = {
        "job_name": "test",
        "work_dir": str(tmp_path / "work"),
        "config": {"itask": 4, "iprog": 1, "ts_rmsd_threshold": 0.01},
        "coords": ["H 0 0 0", "H 0 0 1.0"],
    }

    with patch("confflow.calc.components.executor._run_calculation_step") as mock_run:
        mock_run.return_value = {"final_coords": ["H 0 0 0", "H 0 0 1.1"], "e_low": -1.0}
        with patch("confflow.calc.components.executor.handle_backups"):
            res = runner.run(task_info)
            assert res["status"] == "success"


def test_task_runner_itask1_sp_energy(tmp_path):
    from confflow.calc.components.task_runner import TaskRunner

    runner = TaskRunner()
    task_info = {
        "job_name": "test",
        "work_dir": str(tmp_path / "work"),
        "config": {"itask": 1, "iprog": 1},
        "coords": ["C 0 0 0"],
        "metadata": {"G_corr": 0.1},
    }

    with patch("confflow.calc.components.executor._run_calculation_step") as mock_run:
        mock_run.return_value = {"final_coords": ["C 0 0 0"], "e_low": -100.0}
        with patch("confflow.calc.components.executor.handle_backups"):
            res = runner.run(task_info)
            assert res["status"] == "success"
            assert res["final_gibbs_energy"] == -99.9
            assert res["final_sp_energy"] == -100.0


def test_task_runner_missing_energy_is_parse_error(tmp_path):
    from confflow.calc.components.task_runner import TaskRunner

    runner = TaskRunner()
    task_info = {
        "job_name": "test",
        "work_dir": str(tmp_path / "work"),
        "config": {"itask": 1, "iprog": 1},
        "coords": ["C 0 0 0"],
    }

    with patch("confflow.calc.components.executor._run_calculation_step") as mock_run:
        mock_run.return_value = {
            "final_coords": ["C 0 0 0"],
            "e_low": None,
            "g_low": None,
        }
        with patch("confflow.calc.components.executor.handle_backups") as mock_backups:
            res = runner.run(task_info)

    assert res["status"] == "failed"
    assert res["error_kind"] == "parse_error"
    assert "No energy parsed" in res["error"]
    assert "energy" not in res
    assert "final_gibbs_energy" not in res
    mock_backups.assert_called_once()
    assert mock_backups.call_args.args[2] is False


@pytest.mark.parametrize(
    ("config", "expected_cleanup"),
    [
        ({"itask": 1, "iprog": 1}, True),
        ({"itask": 1, "iprog": 1, "delete_work_dir": "true"}, True),
        ({"itask": 1, "iprog": 1, "delete_work_dir": "false"}, False),
    ],
)
def test_task_runner_delete_work_dir_controls_success_cleanup(tmp_path, config, expected_cleanup):
    from confflow.calc.components.task_runner import TaskRunner

    runner = TaskRunner()
    task_info = {
        "job_name": "test",
        "work_dir": str(tmp_path / "work"),
        "config": config,
        "coords": ["C 0 0 0"],
    }

    with patch("confflow.calc.components.executor._run_calculation_step") as mock_run:
        mock_run.return_value = {"final_coords": ["C 0 0 0"], "e_low": -100.0}
        with patch("confflow.calc.components.executor.handle_backups") as mock_backups:
            res = runner.run(task_info)

    assert res["status"] == "success"
    mock_backups.assert_called_once()
    assert mock_backups.call_args.kwargs["cleanup_work_dir"] is expected_cleanup


def test_task_runner_delete_work_dir_false_controls_failure_cleanup(tmp_path):
    from confflow.calc.components.task_runner import TaskRunner

    runner = TaskRunner()
    task_info = {
        "job_name": "test",
        "work_dir": str(tmp_path / "work"),
        "config": {"itask": 1, "iprog": 1, "delete_work_dir": False},
        "coords": ["C 0 0 0"],
    }

    with patch("confflow.calc.components.executor._run_calculation_step") as mock_run:
        mock_run.return_value = {
            "final_coords": ["C 0 0 0"],
            "e_low": None,
            "g_low": None,
        }
        with patch("confflow.calc.components.executor.handle_backups") as mock_backups:
            res = runner.run(task_info)

    assert res["status"] == "failed"
    assert res["error_kind"] == "parse_error"
    mock_backups.assert_called_once()
    assert mock_backups.call_args.kwargs["cleanup_work_dir"] is False


def test_task_runner_normalized_config_default_cleans_work_dir(tmp_path):
    from confflow.calc.components.task_runner import TaskRunner
    from confflow.calc.config_types import ensure_calc_task_config

    runner = TaskRunner()
    normalized_config = ensure_calc_task_config({"itask": 1, "iprog": 1})
    task_info = {
        "job_name": "test",
        "work_dir": str(tmp_path / "work"),
        "config": normalized_config,
        "coords": ["C 0 0 0"],
    }

    with patch("confflow.calc.components.executor._run_calculation_step") as mock_run:
        mock_run.return_value = {"final_coords": ["C 0 0 0"], "e_low": -100.0}
        with patch("confflow.calc.components.executor.handle_backups") as mock_backups:
            res = runner.run(task_info)

    assert normalized_config["delete_work_dir"] is True
    assert res["status"] == "success"
    mock_backups.assert_called_once()
    assert mock_backups.call_args.kwargs["cleanup_work_dir"] is True


def test_task_runner_ts_missing_energy_attempts_rescue(tmp_path):
    from confflow.calc.components.task_runner import TaskRunner

    runner = TaskRunner()
    task_info = {
        "job_name": "test",
        "work_dir": str(tmp_path / "work"),
        "config": {"itask": 4, "iprog": 1, "ts_rescue_scan": "true"},
        "coords": ["C 0 0 0"],
    }

    with patch("confflow.calc.components.executor._run_calculation_step") as mock_run:
        mock_run.return_value = {
            "final_coords": ["C 0 0 0"],
            "e_low": None,
            "g_low": None,
        }
        with patch("confflow.calc.components.task_runner._ts_rescue_scan") as mock_rescue:
            mock_rescue.return_value = {"status": "rescued", "energy": -1.0}
            with patch("confflow.calc.components.executor.handle_backups"):
                res = runner.run(task_info)

    assert res["status"] == "rescued"
    assert res["energy"] == -1.0
    mock_rescue.assert_called_once()
    assert "No energy parsed" in mock_rescue.call_args.args[1]


def test_task_runner_ts_missing_energy_reports_rescue_failed_when_rescue_fails(tmp_path):
    from confflow.calc.components.task_runner import TaskRunner

    runner = TaskRunner()
    task_info = {
        "job_name": "test",
        "work_dir": str(tmp_path / "work"),
        "config": {"itask": 4, "iprog": 1, "ts_rescue_scan": "true"},
        "coords": ["C 0 0 0"],
    }

    with patch("confflow.calc.components.executor._run_calculation_step") as mock_run:
        mock_run.return_value = {
            "final_coords": ["C 0 0 0"],
            "e_low": None,
            "g_low": None,
        }
        with patch("confflow.calc.components.task_runner._ts_rescue_scan") as mock_rescue:
            mock_rescue.return_value = None
            with patch("confflow.calc.components.executor.handle_backups"):
                res = runner.run(task_info)

    assert res["status"] == "failed"
    assert res["error_kind"] == "rescue_failed"
    assert "No energy parsed" in res["error"]
    assert "energy" not in res
    assert "final_gibbs_energy" not in res
    mock_rescue.assert_called_once()


def test_task_runner_exception_rescue(tmp_path):
    from confflow.calc.components.task_runner import TaskRunner

    runner = TaskRunner()
    task_info = {
        "job_name": "test",
        "work_dir": str(tmp_path / "work"),
        "config": {"itask": 4, "iprog": 1, "ts_rescue_scan": "true"},
        "coords": ["C 0 0 0"],
    }

    with patch(
        "confflow.calc.components.executor._run_calculation_step", side_effect=Exception("Crash")
    ):
        with patch("confflow.calc.components.task_runner._ts_rescue_scan") as mock_rescue:
            mock_rescue.return_value = {"status": "rescued"}
            with patch("confflow.calc.components.executor.handle_backups"):
                res = runner.run(task_info)
                assert res["status"] == "rescued"


def test_task_runner_stop_requested_classified(tmp_path):
    from confflow.calc.components.task_runner import TaskRunner
    from confflow.core.exceptions import StopRequestedError

    runner = TaskRunner()
    task_info = {
        "job_name": "test",
        "work_dir": str(tmp_path / "work"),
        "config": {"itask": 1, "iprog": 1},
        "coords": ["H 0 0 0"],
    }

    with patch(
        "confflow.calc.components.executor._run_calculation_step",
        side_effect=StopRequestedError("STOP signal received"),
    ):
        with patch("confflow.calc.components.executor.handle_backups"):
            res = runner.run(task_info)
            assert res["status"] == "canceled"
            assert res["error_kind"] == "stop_requested"


def test_input_helpers_total_sys_mb():
    from confflow.calc.components.input_helpers import _total_sys_mb

    with patch("confflow.calc.components.input_helpers.UTILS_AVAILABLE", False):
        assert _total_sys_mb("8GB") == 8192
        assert _total_sys_mb("512MB") == 512
        assert _total_sys_mb("1024") == 1024
        assert _total_sys_mb("invalid") == 4096


def test_input_helpers_compute_orca_maxcore_override():
    from confflow.calc.components.input_helpers import compute_orca_maxcore

    config = {"orca_maxcore": "2000", "max_parallel_jobs": 1, "total_memory": "4GB"}
    assert compute_orca_maxcore(config) == "2000"

    config = {"orca_maxcore": "invalid", "max_parallel_jobs": 1, "total_memory": "4GB"}
    assert compute_orca_maxcore(config) == "invalid"


def test_input_helpers_parse_freeze_indices_more():
    from confflow.calc.components.input_helpers import parse_freeze_indices

    assert parse_freeze_indices(None) == []
    assert parse_freeze_indices("0") == []
    assert parse_freeze_indices([None, "0", 1.0]) == [1]
    assert parse_freeze_indices(123) == []


def test_input_helpers_gaussian_apply_freeze_empty():
    from confflow.calc.components.input_helpers import gaussian_apply_freeze

    coords = ["C 0 0 0"]
    assert gaussian_apply_freeze(coords, []) == "C 0 0 0"


def test_input_helpers_orca_constraint_block_empty():
    from confflow.calc.components.input_helpers import orca_constraint_block

    assert orca_constraint_block([]) == ""
