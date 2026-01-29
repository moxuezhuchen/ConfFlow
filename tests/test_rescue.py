"""rescue 模块测试（合并版）"""

import os
import pytest
from unittest.mock import MagicMock

from confflow import calc
from confflow.calc import rescue
from confflow.calc.components import executor
from confflow.calc.analysis import _bond_length_from_xyz_lines


def test_coords_lines_to_xyz_valid():
    lines = ["C 0.0 0.0 0.0", "H 0.0 0.0 1.0"]
    result = rescue._coords_lines_to_xyz(lines)
    assert len(result) == 2
    assert result[0] == ("C", 0.0, 0.0, 0.0)
    assert result[1] == ("H", 0.0, 0.0, 1.0)


def test_coords_lines_to_xyz_invalid():
    assert rescue._coords_lines_to_xyz([]) == []
    assert rescue._coords_lines_to_xyz(["C 0.0"]) is None
    assert rescue._coords_lines_to_xyz(["C x y z"]) is None

    bad_lines = ["H 0.0 0.0", "C 1.0 1.0 1.0 1.0"]
    assert rescue._coords_lines_to_xyz(bad_lines) is None

    bad_num = ["H 0.0 0.0 abc"]
    assert rescue._coords_lines_to_xyz(bad_num) is None


def test_read_gaussian_input_coords(tmp_path):
    gjf_path = tmp_path / "test.gjf"
    content = """%mem=1GB
# opt freq

Title

0 1
C 0.0 0.0 0.0
H 0.0 0.0 1.0

"""
    gjf_path.write_text(content)
    coords = rescue._read_gaussian_input_coords(str(gjf_path))
    assert coords == ["C 0.0 0.0 0.0", "H 0.0 0.0 1.0"]

    content_freeze = """0 1
C -1 0.0 0.0 0.0
H 0 0.0 0.0 1.0
"""
    gjf_path.write_text(content_freeze)
    coords = rescue._read_gaussian_input_coords(str(gjf_path))
    assert coords == ["C -1 0.0 0.0", "H 0 0.0 0.0"]

    assert rescue._read_gaussian_input_coords("non_existent.gjf") is None

    gjf_path.write_text("not a gaussian input")
    assert rescue._read_gaussian_input_coords(str(gjf_path)) is None


def test_read_gaussian_input_coords_minimal(tmp_path):
    gjf = tmp_path / "test.gjf"
    gjf.write_text("%chk=test.chk\n# opt ts\n\ntitle\n\n0 1\nC 0.0 0.0 0.0\nH 0.0 0.0 1.0\n\n")

    coords = rescue._read_gaussian_input_coords(str(gjf))
    assert coords is not None
    assert len(coords) == 2
    assert "C 0.0 0.0 0.0" in coords[0]


def test_xyz_to_coords_lines():
    xyz = [("C", 0.0, 0.0, 0.0), ("H", 1.0, 0.0, 0.0)]
    lines = rescue._xyz_to_coords_lines(xyz)
    assert len(lines) == 2
    assert "C " in lines[0]
    assert "H " in lines[1]


def test_set_bond_length_on_coords():
    coords = ["C 0 0 0", "C 1.5 0 0"]
    new_coords = rescue._set_bond_length_on_coords(coords, 1, 2, 2.0)
    assert new_coords is not None
    assert "2.000000" in new_coords[1]

    coords2 = ["C 0.0 0.0 0.0", "H 0.0 0.0 1.0"]
    new_coords2 = rescue._set_bond_length_on_coords(coords2, 1, 2, 1.5)
    assert "1.500000" in new_coords2[1]

    assert rescue._set_bond_length_on_coords(coords2, 0, 2, 1.5) is None
    assert rescue._set_bond_length_on_coords(coords2, 1, 3, 1.5) is None
    assert rescue._set_bond_length_on_coords(coords2, 1, 1, 1.5) is None

    coords_overlap = ["C 0.0 0.0 0.0", "H 0.0 0.0 0.0"]
    assert rescue._set_bond_length_on_coords(coords_overlap, 1, 2, 1.5) is None


def test_write_reports(tmp_path):
    wd = tmp_path / "work"
    rescue._write_ts_failure_report(str(wd), "job1", "stage1", "msg1")
    report_file = wd / "ts_failures.txt"
    assert report_file.exists()
    assert "job1 | stage1 | msg1" in report_file.read_text()

    scan_dir = wd / "scan"
    rescue._write_scan_marker(str(scan_dir), "job1", "error1")
    marker_file = scan_dir / "job1.scan_error.txt"
    assert marker_file.exists()
    assert "job1: error1" in marker_file.read_text()

    rescue._write_scan_marker("", "job1", "error1")


def test_find_failed_ts_input_coords(tmp_path):
    wd = tmp_path / "work"
    wd.mkdir()
    backup = tmp_path / "backup"
    backup.mkdir()

    cfg = {"backup_dir": str(backup)}
    job = "job1"

    gjf_file = wd / "job1.gjf"
    gjf_file.write_text("0 1\nC 1.0 1.0 1.0")
    coords = rescue._find_failed_ts_input_coords(str(wd), job, cfg)
    assert coords == ["C 1.0 1.0 1.0"]
    gjf_file.unlink()

    com_file = backup / "job1.com"
    com_file.write_text("0 1\nH 2.0 2.0 2.0")
    coords = rescue._find_failed_ts_input_coords(str(wd), job, cfg)
    assert coords == ["H 2.0 2.0 2.0"]

    com_file.unlink()
    assert rescue._find_failed_ts_input_coords(str(wd), job, cfg) is None


def test_ts_rescue_scan_failures(monkeypatch, tmp_path):
    wd = tmp_path / "work"
    wd.mkdir()

    task_info = {
        "job_name": "job1",
        "work_dir": str(wd),
        "coords": ["C 0.0 0.0 0.0", "H 0.0 0.0 1.0"],
        "config": {
            "iprog": "g16",
            "itask": "ts",
            "ts_bond": "1,2",
            "keyword": "opt freq",
        },
    }

    task_orca = dict(task_info)
    task_orca["config"] = dict(task_info["config"], iprog="orca")
    assert rescue._ts_rescue_scan(task_orca, "fail") is None

    task_no_bond = dict(task_info)
    task_no_bond["config"] = dict(task_info["config"])
    task_no_bond["config"].pop("ts_bond")
    assert rescue._ts_rescue_scan(task_no_bond, "fail") is None

    task_no_kw = dict(task_info)
    task_no_kw["config"] = dict(task_info["config"], keyword="")
    assert rescue._ts_rescue_scan(task_no_kw, "fail") is None

    def fake_run_fail(*args, **kwargs):
        raise RuntimeError("Calculation failed")

    monkeypatch.setattr(executor, "_run_calculation_step", fake_run_fail)
    assert rescue._ts_rescue_scan(task_info, "fail") is None

    def fake_run_descending(work_dir, job_name, policy, coords, config, is_sp_task=False):
        r = _bond_length_from_xyz_lines(coords, 1, 2)
        return {"e_low": -float(r), "final_coords": coords}

    monkeypatch.setattr(executor, "_run_calculation_step", fake_run_descending)
    assert rescue._ts_rescue_scan(task_info, "fail") is None


def test_ts_rescue_scan_geometric_check_failure(monkeypatch, tmp_path):
    wd = tmp_path / "work"
    wd.mkdir()

    task_info = {
        "job_name": "job1",
        "work_dir": str(wd),
        "coords": ["C 0.0 0.0 0.0", "H 0.0 0.0 1.0"],
        "config": {
            "iprog": "g16",
            "itask": "ts",
            "ts_bond": "1,2",
            "keyword": "opt",
            "ts_bond_drift_threshold": 0.1,
            "ts_rmsd_threshold": 0.1,
        },
    }

    def fake_run_geom_fail(work_dir, job_name, policy, coords, config, is_sp_task=False):
        if "_scan_" in job_name:
            return {"e_low": 0.0, "final_coords": coords}
        if "_rescue" in job_name:
            return {"e_low": -100.0, "final_coords": ["C 0.0 0.0 0.0", "H 0.0 0.0 2.0"]}
        return {"e_low": 0.0, "final_coords": coords}

    monkeypatch.setattr(executor, "_run_calculation_step", fake_run_geom_fail)
    monkeypatch.setattr(executor, "handle_backups", lambda *a, **k: None)

    assert rescue._ts_rescue_scan(task_info, "fail") is None


def test_ts_rescue_scan_freq_check_failure(monkeypatch, tmp_path):
    wd = tmp_path / "work"
    wd.mkdir()

    task_info = {
        "job_name": "job1",
        "work_dir": str(wd),
        "coords": ["C 0.0 0.0 0.0", "H 0.0 0.0 1.0"],
        "config": {
            "iprog": "g16",
            "itask": "ts",
            "ts_bond": "1,2",
            "keyword": "opt freq",
        },
    }

    def fake_run_freq_fail(work_dir, job_name, policy, coords, config, is_sp_task=False):
        if "_scan_" in job_name:
            return {"e_low": 0.0, "final_coords": coords}
        if "_rescue" in job_name:
            return {"e_low": -100.0, "final_coords": coords, "num_imag_freqs": 0}
        return {"e_low": 0.0, "final_coords": coords}

    monkeypatch.setattr(executor, "_run_calculation_step", fake_run_freq_fail)
    monkeypatch.setattr(executor, "handle_backups", lambda *a, **k: None)

    assert rescue._ts_rescue_scan(task_info, "fail") is None


def test_ts_failure_triggers_scan_rescue_and_keyword_rewrite(monkeypatch, tmp_path):
    base_coords = ["H 0 0 0", "H 0 0 1.0"]

    scan_rs = []

    def fake_run_calculation_step(work_dir, job_name, prog_id, coords, config, is_sp_task=False):
        if job_name == "c0001":
            raise RuntimeError("TS failed")

        if "_scan_" in job_name:
            r = _bond_length_from_xyz_lines(coords, 1, 2)
            assert r is not None
            scan_rs.append(float(r))

            kw = str(config.get("keyword", ""))
            assert "freq" not in kw.lower()
            assert "calcfc" not in kw.lower()
            assert "tight" not in kw.lower()
            assert "noeigentest" not in kw.lower()
            assert "opt" in kw.lower()
            assert "nomicro" in kw.lower()

            assert config.get("gaussian_modredundant") in (None, "", [])
            assert str(config.get("freeze", "")) in ("1,2", "2,1")
            assert str(config.get("itask")).lower() == "opt"

            e = -((float(r) - 1.10) ** 2) + 1.0
            return {
                "e_low": e,
                "g_low": None,
                "g_corr": None,
                "num_imag_freqs": None,
                "lowest_freq": None,
                "final_coords": coords,
            }

        if job_name.endswith("_rescue"):
            return {
                "e_low": -123.456,
                "g_low": None,
                "g_corr": None,
                "num_imag_freqs": 1,
                "lowest_freq": -123.4,
                "final_coords": coords,
            }

        raise RuntimeError(f"unexpected job_name: {job_name}")

    monkeypatch.setattr(executor, "_run_calculation_step", fake_run_calculation_step)
    monkeypatch.setattr(executor, "handle_backups", lambda *a, **k: None)

    task_info = {
        "job_name": "c0001",
        "work_dir": str(tmp_path / "c0001"),
        "coords": base_coords,
        "config": {
            "iprog": "g16",
            "itask": "ts",
            "ts_rescue_scan": "true",
            "ts_bond_atoms": "1,2",
            "keyword": "opt(nomicro,calcfc,tight,ts,noeigentest) freq",
            "scan_max_steps": 5,
            "scan_uphill_limit": 2,
            "scan_fine_half_window": 0.1,
            "scan_coarse_step": 0.1,
            "scan_fine_step": 0.02,
        },
    }

    res = calc.TaskRunner().run(task_info)

    assert res["status"] == "success"
    assert res.get("rescued_by_scan") is True

    scan_table = tmp_path / "c0001" / "scan" / "scan_table.txt"
    assert scan_table.exists()
    txt = scan_table.read_text(encoding="utf-8")
    assert "Bond: 1-2" in txt
    assert "E(Eh)" in txt
    assert "MAX" in txt


def test_ts_rescue_scan_disabled(tmp_path, monkeypatch):
    task_info = {
        "job_name": "test_job",
        "work_dir": str(tmp_path),
        "coords": ["H 0 0 0", "H 0 0 0.74"],
        "config": {
            "iprog": "g16",
            "itask": "ts",
            "keyword": "opt=(ts,calcfc) freq",
            "ts_rescue_scan": False,
        },
    }

    def mock_run_fail(*args, **kwargs):
        raise RuntimeError("TS failed")

    monkeypatch.setattr(executor, "_run_calculation_step", mock_run_fail)
    monkeypatch.setattr(executor, "handle_backups", lambda *a, **k: None)

    runner = calc.TaskRunner()
    result = runner.run(task_info)

    assert result["status"] == "failed"
    assert "TS failed" in result["error"]
    assert "rescued" not in result
