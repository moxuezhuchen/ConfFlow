from unittest.mock import patch

import pytest


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
            assert "存在 1 个虚频" in res["error"]


def test_task_runner_itask4_no_freq_drift(tmp_path):
    from confflow.calc.components.task_runner import TaskRunner

    runner = TaskRunner()
    task_info = {
        "job_name": "test",
        "work_dir": str(tmp_path / "work"),
        "config": {"itask": 4, "iprog": 1, "ts_bond": "1,2", "ts_bond_drift_threshold": 0.1},
        "coords": ["H 0 0 0", "H 0 0 1.0"],
    }

    with patch("confflow.calc.components.executor._run_calculation_step") as mock_run:
        mock_run.return_value = {"final_coords": ["H 0 0 0", "H 0 0 1.2"], "e_low": -1.0}
        with patch("confflow.calc.components.executor.handle_backups"):
            res = runner.run(task_info)
            assert res["status"] == "failed"
            assert "偏移 |ΔR|=0.200 Å 超过阈值 0.100 Å" in res["error"]


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


def test_task_runner_exception_rescue(tmp_path):
    from confflow.calc.components.task_runner import TaskRunner

    runner = TaskRunner()
    task_info = {
        "job_name": "test",
        "work_dir": str(tmp_path / "work"),
        "config": {"itask": 4, "iprog": 1, "ts_rescue_scan": "true"},
        "coords": ["C 0 0 0"],
    }

    with patch("confflow.calc.components.executor._run_calculation_step", side_effect=Exception("Crash")):
        with patch("confflow.calc.components.task_runner._ts_rescue_scan") as mock_rescue:
            mock_rescue.return_value = {"status": "rescued"}
            with patch("confflow.calc.components.executor.handle_backups"):
                res = runner.run(task_info)
                assert res["status"] == "rescued"


def test_task_runner_unsupported_iprog():
    from confflow.calc.components.task_runner import TaskRunner

    runner = TaskRunner()
    with pytest.raises(ValueError, match="Unsupported iprog"):
        runner._get_policy({"iprog": 99})


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
