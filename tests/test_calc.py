"""calc 模块的综合测试 - 合并自 test_calc_core_extended.py 和 test_calc_core_extended_v2.py"""

import pytest
import os
import sys
import importlib
from unittest.mock import MagicMock, patch


# =============================================================================
# calc.core 测试
# =============================================================================

class TestCalcCore:
    """calc.core 模块测试"""

    def test_get_itask_variants(self):
        from confflow.calc.core import get_itask
        assert get_itask({"itask": "opt"}) == 0
        assert get_itask({"itask": "sp"}) == 1
        assert get_itask({"itask": "freq"}) == 2
        assert get_itask({"itask": "opt_freq"}) == 3
        assert get_itask({"itask": 1}) == 1
        assert get_itask({"itask": "1"}) == 1
        assert get_itask({}) == 3  # Default

    def test_parse_iprog_variants(self):
        from confflow.calc.core import parse_iprog
        assert parse_iprog({"iprog": "gaussian"}) == 1
        assert parse_iprog({"iprog": "g16"}) == 1
        assert parse_iprog({"iprog": "orca"}) == 2
        assert parse_iprog({"iprog": 1}) == 1
        assert parse_iprog({}) == 2  # Default

    def test_setup_logging_fallback(self, tmp_path, monkeypatch):
        import confflow.calc.core
        monkeypatch.setattr(confflow.calc.core, "UTILS_AVAILABLE", False)
        
        log_dir = tmp_path / "logs_fallback"
        log_dir.mkdir()
        logger = confflow.calc.core.setup_logging(str(log_dir))
        assert logger is not None
        log_file = log_dir / "calc.log"
        assert log_file.exists()

    def test_calc_core_fallback_logic(self):
        with patch.dict(sys.modules, {'confflow.core.utils': None}):
            import confflow.calc.core as core_mod
            importlib.reload(core_mod)
            
            from confflow.calc.core import utils_parse_itask, utils_parse_iprog
            
            assert utils_parse_itask({"itask": 1}) == 1
            assert utils_parse_itask({"itask": "opt"}) == 0
            assert utils_parse_itask({"itask": "3"}) == 3
            assert utils_parse_itask({}) == 3
            
            assert utils_parse_iprog({"iprog": 1}) == 1
            assert utils_parse_iprog({"iprog": "gaussian"}) == 1
            assert utils_parse_iprog({"iprog": "orca"}) == 2
            assert utils_parse_iprog({}) == 1
        
        import confflow.calc.core as core_mod
        importlib.reload(core_mod)


# =============================================================================
# calc.resources 测试
# =============================================================================

class TestResourceMonitor:
    """ResourceMonitor 测试"""

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
        mock_psutil.cpu_percent.side_effect = Exception("psutil error")
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
# calc.components.executor 测试
# =============================================================================

class TestExecutor:
    """executor 模块测试"""

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
        from confflow.calc.components.executor import handle_backups
        import shutil
        
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
        from confflow.calc.components.executor import handle_backups
        import shutil
        
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
# calc.db.database 测试
# =============================================================================

class TestResultsDB:
    """ResultsDB 测试"""

    def test_backup(self, tmp_path):
        from confflow.calc.db.database import ResultsDB
        db_path = tmp_path / "test.db"
        db = ResultsDB(str(db_path))
        db.insert_result({"job_name": "test", "status": "success"})
        
        backup_path = tmp_path / "test.db.backup"
        db.backup(str(backup_path))
        
        assert os.path.exists(backup_path)
        
        with patch("shutil.move", side_effect=Exception("Move failed")):
            with pytest.raises(Exception):
                db.backup(str(tmp_path / "ro" / "fail.db"))

    def test_column_check(self, tmp_path):
        from confflow.calc.db.database import ResultsDB
        import sqlite3
        
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


# =============================================================================
# Executor 扩展测试 (来自 test_final_push.py)
# =============================================================================

class TestExecutorAdvanced:
    """executor 模块高级测试"""

    def test_executor_stop_beacon(self, tmp_path):
        """测试 stop_beacon 停止信号"""
        from confflow.calc.components.executor import _run_calculation_step
        from unittest.mock import MagicMock, patch
        
        work_dir = tmp_path / "work"
        work_dir.mkdir()
        stop_file = tmp_path / "stop.txt"
        stop_file.write_text("STOP")
        
        policy = MagicMock()
        policy.input_ext = "inp"
        policy.log_ext = "log"
        policy.name = "Mock"
        policy.get_execution_command.return_value = ["sleep", "10"]
        policy.get_environment.return_value = None
        
        config = {
            "stop_beacon_file": str(stop_file),
            "stop_check_interval_seconds": 0.1
        }
        
        with patch("subprocess.Popen") as mock_popen:
            mock_proc = MagicMock()
            mock_proc.poll.side_effect = [None, None, 0]
            mock_popen.return_value = mock_proc
            
            with pytest.raises(RuntimeError, match="STOP signal received"):
                _run_calculation_step(str(work_dir), "job", policy, None, config)
            
            mock_proc.kill.assert_called_once()

    def test_save_config_hash_failure(self, tmp_path):
        """测试 config hash 保存失败的情况"""
        from confflow.calc.components.executor import _save_config_hash
        from unittest.mock import patch
        
        with patch("builtins.open", side_effect=IOError("Permission denied")):
            _save_config_hash(str(tmp_path), {"itask": 1, "iprog": 1})

    def test_executor_nonzero_exit(self, tmp_path):
        """测试非零退出码"""
        from confflow.calc.components.executor import _run_calculation_step
        from unittest.mock import MagicMock, patch
        
        work_dir = tmp_path / "work"
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

    def test_executor_abnormal_termination(self, tmp_path):
        """测试异常终止"""
        from confflow.calc.components.executor import _run_calculation_step
        from unittest.mock import MagicMock, patch
        
        work_dir = tmp_path / "work"
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


# =============================================================================
# Viz Report 扩展测试
# =============================================================================

class TestVizReportAdvanced:
    """viz.report 模块测试"""

    def test_viz_report_failed_count_from_db(self, tmp_path):
        """测试从数据库统计失败数量"""
        import sqlite3
        from confflow.blocks.viz.report import generate_workflow_section
        
        step_dir = tmp_path / "step1"
        step_dir.mkdir()
        work_dir = step_dir / "work"
        work_dir.mkdir()
        db_path = work_dir / "results.db"
        
        conn = sqlite3.connect(db_path)
        conn.execute("CREATE TABLE task_results (status TEXT)")
        conn.execute("INSERT INTO task_results VALUES ('failed')")
        conn.execute("INSERT INTO task_results VALUES ('failed')")
        conn.execute("INSERT INTO task_results VALUES ('completed')")
        conn.commit()
        conn.close()
        
        steps = [{
            "index": 1,
            "name": "TestStep",
            "type": "calc",
            "status": "completed",
            "input_conformers": 10,
            "output_conformers": 8,
            "output_xyz": str(step_dir / "output.xyz"),
            "duration_seconds": 100,
            "metadata": {}
        }]
        
        html = generate_workflow_section({"steps": steps})
        assert "<td>2</td>" in html
