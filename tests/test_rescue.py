#!/usr/bin/env python3

"""Tests for rescue module (merged)."""

from __future__ import annotations

from unittest.mock import patch

from confflow import calc
from confflow.calc import rescue, scan_ops
from confflow.calc.analysis import _bond_length_from_xyz_lines
from confflow.calc.components import executor


def test_coords_lines_to_xyz_valid():
    lines = ["C 0.0 0.0 0.0", "H 0.0 0.0 1.0"]
    result = scan_ops._coords_lines_to_xyz(lines)
    assert len(result) == 2
    assert result[0] == ("C", 0.0, 0.0, 0.0)
    assert result[1] == ("H", 0.0, 0.0, 1.0)


def test_coords_lines_to_xyz_invalid():
    assert scan_ops._coords_lines_to_xyz([]) == []
    assert scan_ops._coords_lines_to_xyz(["C 0.0"]) is None
    assert scan_ops._coords_lines_to_xyz(["C x y z"]) is None

    bad_lines = ["H 0.0 0.0", "C 1.0 1.0 1.0 1.0"]
    assert scan_ops._coords_lines_to_xyz(bad_lines) is None

    bad_num = ["H 0.0 0.0 abc"]
    assert scan_ops._coords_lines_to_xyz(bad_num) is None


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
    coords = scan_ops._read_gaussian_input_coords(str(gjf_path))
    assert coords == ["C 0.0 0.0 0.0", "H 0.0 0.0 1.0"]

    content_freeze = """0 1
C -1 0.0 0.0 0.0
H 0 0.0 0.0 1.0
"""
    gjf_path.write_text(content_freeze)
    coords = scan_ops._read_gaussian_input_coords(str(gjf_path))
    assert coords == ["C -1 0.0 0.0 0.0", "H 0 0.0 0.0 1.0"]

    assert scan_ops._read_gaussian_input_coords("non_existent.gjf") is None

    gjf_path.write_text("not a gaussian input")
    assert scan_ops._read_gaussian_input_coords(str(gjf_path)) is None


def test_read_gaussian_input_coords_minimal(tmp_path):
    gjf = tmp_path / "test.gjf"
    gjf.write_text("%chk=test.chk\n# opt ts\n\ntitle\n\n0 1\nC 0.0 0.0 0.0\nH 0.0 0.0 1.0\n\n")

    coords = scan_ops._read_gaussian_input_coords(str(gjf))
    assert coords is not None
    assert len(coords) == 2
    assert "C 0.0 0.0 0.0" in coords[0]


def test_xyz_to_coords_lines():
    xyz = [("C", 0.0, 0.0, 0.0), ("H", 1.0, 0.0, 0.0)]
    lines = scan_ops._xyz_to_coords_lines(xyz)
    assert len(lines) == 2
    assert "C " in lines[0]
    assert "H " in lines[1]


def test_set_bond_length_on_coords():
    coords = ["C 0 0 0", "C 1.5 0 0"]
    new_coords = scan_ops._set_bond_length_on_coords(coords, 1, 2, 2.0)
    assert new_coords is not None
    assert "2.000000" in new_coords[1]

    coords2 = ["C 0.0 0.0 0.0", "H 0.0 0.0 1.0"]
    new_coords2 = scan_ops._set_bond_length_on_coords(coords2, 1, 2, 1.5)
    assert "1.500000" in new_coords2[1]

    assert scan_ops._set_bond_length_on_coords(coords2, 0, 2, 1.5) is None
    assert scan_ops._set_bond_length_on_coords(coords2, 1, 3, 1.5) is None
    assert scan_ops._set_bond_length_on_coords(coords2, 1, 1, 1.5) is None

    coords_overlap = ["C 0.0 0.0 0.0", "H 0.0 0.0 0.0"]
    assert scan_ops._set_bond_length_on_coords(coords_overlap, 1, 2, 1.5) is None


def test_write_reports(tmp_path):
    wd = tmp_path / "work"
    scan_ops._write_ts_failure_report(str(wd), "job1", "stage1", "msg1")
    report_file = wd / "ts_failures.txt"
    assert report_file.exists()
    assert "job1 | stage1 | msg1" in report_file.read_text()

    scan_dir = wd / "scan"
    scan_ops._write_scan_marker(str(scan_dir), "job1", "error1")
    marker_file = scan_dir / "job1.scan_error.txt"
    assert marker_file.exists()
    assert "job1: error1" in marker_file.read_text()

    scan_ops._write_scan_marker("", "job1", "error1")


def test_find_failed_ts_input_coords(tmp_path):
    wd = tmp_path / "work"
    wd.mkdir()
    backup = tmp_path / "backup"
    backup.mkdir()

    cfg = {"backup_dir": str(backup)}
    job = "job1"

    gjf_file = wd / "job1.gjf"
    gjf_file.write_text("0 1\nC 1.0 1.0 1.0")
    coords = scan_ops._find_failed_ts_input_coords(str(wd), job, cfg)
    assert coords == ["C 1.0 1.0 1.0"]
    gjf_file.unlink()

    com_file = backup / "job1.com"
    com_file.write_text("0 1\nH 2.0 2.0 2.0")
    coords = scan_ops._find_failed_ts_input_coords(str(wd), job, cfg)
    assert coords == ["H 2.0 2.0 2.0"]

    com_file.unlink()
    assert scan_ops._find_failed_ts_input_coords(str(wd), job, cfg) is None


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
            "ts_bond_atoms": "1,2",
            "keyword": "opt freq",
        },
    }

    task_orca = dict(task_info)
    task_orca["config"] = dict(task_info["config"], iprog="orca")
    assert rescue._ts_rescue_scan(task_orca, "fail") is None

    task_no_bond = dict(task_info)
    task_no_bond["config"] = dict(task_info["config"])
    task_no_bond["config"].pop("ts_bond_atoms")
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
            "ts_bond_atoms": "1,2",
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
            "ts_bond_atoms": "1,2",
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
        if job_name == "A000001":
            raise RuntimeError("TS failed")

        try:
            float(job_name)
            is_scan = True
        except ValueError:
            is_scan = False

        if is_scan:
            r = _bond_length_from_xyz_lines(coords, 1, 2)
            assert r is not None
            scan_rs.append(float(r))

            kw = str(config.get("keyword", ""))
            assert "freq" not in kw.lower()
            assert "calcfc" not in kw.lower()
            assert "rcfc" not in kw.lower()
            assert "readfc" not in kw.lower()
            assert "tight" not in kw.lower()
            assert "noeigentest" not in kw.lower()
            assert "opt" in kw.lower()
            assert "nomicro" in kw.lower()

            assert config.get("gaussian_modredundant") in (None, "", [])
            assert config.get("gaussian_oldchk") in (None, "")
            assert config.get("gaussian_oldchk_file") in (None, "")
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
        "job_name": "A000001",
        "work_dir": str(tmp_path / "A000001"),
        "coords": base_coords,
        "config": {
            "iprog": "g16",
            "itask": "ts",
            "ts_rescue_scan": "true",
            "ts_bond_atoms": "1,2",
            "keyword": "opt(nomicro,calcfc,tight,ts,noeigentest) freq",
            "gaussian_oldchk": "A000001.old.chk",
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

    scan_table = tmp_path / "A000001" / "scan" / "scan_table.txt"
    assert scan_table.exists()
    txt = scan_table.read_text(encoding="utf-8")
    assert "Bond 1-2" in txt  # Updated for Rich table format
    assert "E (Eh)" in txt  # Updated for Rich table format
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


# =============================================================================
# TS rescue scan path-coverage tests (merged from test_rescue_ts_scan_paths.py)
# =============================================================================


def test_rescue_helpers_extended(tmp_path):
    assert scan_ops._coords_lines_to_xyz(["C 0 0"]) is None
    assert scan_ops._coords_lines_to_xyz(["C a b c"]) is None

    assert scan_ops._read_gaussian_input_coords(None) is None
    assert scan_ops._read_gaussian_input_coords("nonexistent.gjf") is None

    gjf = tmp_path / "test.gjf"
    gjf.write_text("0 1\nC 0 0 0\n\n")
    assert scan_ops._read_gaussian_input_coords(str(gjf)) == ["C 0 0 0"]

    assert scan_ops._find_failed_ts_input_coords(str(tmp_path), "job", {"iprog": 1}) is None

    res = scan_ops._xyz_to_coords_lines([("C", 0, 0, 0)])
    assert "C" in res[0] and "0.000000" in res[0]

    coords = ["C 0 0 0", "H 0 0 1"]
    new_coords = scan_ops._set_bond_length_on_coords(coords, 1, 2, 1.5)
    assert any("C" in ln and "0.000000" in ln for ln in new_coords)

    scan_ops._write_ts_failure_report(str(tmp_path), "job", "stage", "message")
    assert (tmp_path / "ts_failures.txt").exists()

    scan_ops._write_scan_marker(str(tmp_path), "job", "message")
    assert (tmp_path / "job.scan_error.txt").exists()


def test_ts_rescue_scan_no_coords(tmp_path):
    task_info = {
        "job_name": "test",
        "work_dir": str(tmp_path / "work"),
        "config": {"itask": 4, "iprog": 1, "ts_bond_atoms": "1,2"},
        "coords": ["H 0 0 0", "H 0 0 1.0"],
    }

    with patch("confflow.calc.rescue.executor._run_calculation_step") as mock_run:
        mock_run.return_value = {"final_coords": None}
        with patch("confflow.calc.rescue.os.path.exists", return_value=True):
            with patch(
                "confflow.core.io.read_xyz_file",
                return_value=(
                    True,
                    [
                        {
                            "atoms": ["H", "H"],
                            "coords": [[0, 0, 0], [0, 0, 1.0]],
                            "energy": -1.0,
                        }
                    ],
                ),
            ):
                res = rescue._ts_rescue_scan(task_info, "error")
                assert res is None


def test_ts_rescue_scan_drift_failure(tmp_path):
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

    with patch("confflow.calc.rescue.executor._run_calculation_step") as mock_run:
        mock_run.return_value = {"final_coords": ["H 0 0 0", "H 0 0 1.2"], "e_low": -1.0}
        with patch("confflow.calc.rescue.os.path.exists", return_value=True):
            with patch(
                "confflow.core.io.read_xyz_file",
                return_value=(
                    True,
                    [
                        {
                            "atoms": ["H", "H"],
                            "coords": [[0, 0, 0], [0, 0, 1.2]],
                            "energy": -1.0,
                        }
                    ],
                ),
            ):
                res = rescue._ts_rescue_scan(task_info, "error")
                assert res is None


def test_ts_rescue_scan_rmsd_failure(tmp_path):
    task_info = {
        "job_name": "test",
        "work_dir": str(tmp_path / "work"),
        "config": {"itask": 4, "iprog": 1, "ts_rmsd_threshold": 0.01},
        "coords": ["H 0 0 0", "H 0 0 1.0"],
    }

    with patch("confflow.calc.rescue.executor._run_calculation_step") as mock_run:
        mock_run.return_value = {"final_coords": ["H 0 0 0", "H 0 0 1.1"], "e_low": -1.0}
        with patch("confflow.calc.rescue.os.path.exists", return_value=True):
            with patch(
                "confflow.core.io.read_xyz_file",
                return_value=(
                    True,
                    [
                        {
                            "atoms": ["H", "H"],
                            "coords": [[0, 0, 0], [0, 0, 1.1]],
                            "energy": -1.0,
                        }
                    ],
                ),
            ):
                res = rescue._ts_rescue_scan(task_info, "error")
                assert res is None


def test_ts_rescue_scan_freq_failure(tmp_path):
    task_info = {
        "job_name": "test",
        "work_dir": str(tmp_path / "work"),
        "config": {"itask": 4, "iprog": 1, "keyword": "freq", "ts_bond_atoms": "1,2"},
        "coords": ["H 0 0 0", "H 0 0 1.0"],
    }

    with patch("confflow.calc.rescue.executor._run_calculation_step") as mock_run:
        mock_run.return_value = {
            "final_coords": ["H 0 0 0", "H 0 0 1.0"],
            "e_low": -1.0,
            "num_imag_freqs": 0,
        }
        with patch("confflow.calc.rescue.os.path.exists", return_value=True):
            with patch(
                "confflow.core.io.read_xyz_file",
                return_value=(
                    True,
                    [
                        {
                            "atoms": ["H", "H"],
                            "coords": [[0, 0, 0], [0, 0, 1.0]],
                            "energy": -1.0,
                            "metadata": {"num_imag_freqs": 0},
                        }
                    ],
                ),
            ):
                res = rescue._ts_rescue_scan(task_info, "error")
                assert res is None


def _task_info_for_scan(
    tmp_path, *, keyword: str, config_extra: dict | None = None, job: str = "job"
):
    import os

    wd = str(tmp_path)
    os.makedirs(wd, exist_ok=True)
    cfg = {"keyword": keyword, "ts_bond_atoms": "1,2"}
    if config_extra:
        cfg.update(config_extra)

    base_coords = ["C 0 0 0", "H 0 0 1.5"]
    return {"work_dir": wd, "job_name": job, "coords": base_coords, "config": cfg}


def test_ts_rescue_scan_full_logic(tmp_path):
    task_info = _task_info_for_scan(tmp_path / "rescue", keyword="opt ts")

    with (
        patch("confflow.calc.rescue.executor._run_calculation_step") as mock_run,
        patch("confflow.calc.rescue._get_policy"),
    ):
        mock_run.side_effect = [
            {"e_low": -100.0, "final_coords": ["C 0 0 0", "H 0 0 1.5"]},
            {"e_low": -95.0, "final_coords": ["C 0 0 0", "H 0 0 1.4"]},
            {"e_low": -95.0, "final_coords": ["C 0 0 0", "H 0 0 1.6"]},
            {"e_low": -90.0, "final_coords": ["C 0 0 0", "H 0 0 1.7"]},
            {"e_low": -85.0, "final_coords": ["C 0 0 0", "H 0 0 1.8"]},
            {"e_low": -80.0, "final_coords": ["C 0 0 0", "H 0 0 1.9"]},
        ]

        res = rescue._ts_rescue_scan(task_info, fail_reason="test")
        assert res is None


def test_ts_rescue_scan_consecutive_down(tmp_path):
    task_info = _task_info_for_scan(tmp_path / "rescue_down", keyword="opt ts")

    with (
        patch("confflow.calc.rescue.executor._run_calculation_step") as mock_run,
        patch("confflow.calc.rescue._get_policy"),
    ):
        mock_run.side_effect = [
            {"e_low": -100.0, "final_coords": ["C 0 0 0", "H 0 0 1.5"]},
            {"e_low": -105.0, "final_coords": ["C 0 0 0", "H 0 0 1.4"]},
            {"e_low": -110.0, "final_coords": ["C 0 0 0", "H 0 0 1.3"]},
            {"e_low": -95.0, "final_coords": ["C 0 0 0", "H 0 0 1.6"]},
            {"e_low": -105.0, "final_coords": ["C 0 0 0", "H 0 0 1.7"]},
            {"e_low": -110.0, "final_coords": ["C 0 0 0", "H 0 0 1.8"]},
        ]

        res = rescue._ts_rescue_scan(task_info, fail_reason="test")
        assert res is None


def test_ts_rescue_scan_coarse_extend_none(tmp_path):
    task_info = _task_info_for_scan(tmp_path / "rescue_none", keyword="opt ts")

    with patch("confflow.calc.rescue.executor._run_calculation_step") as mock_run:
        mock_run.return_value = {"status": "failed"}
        res = rescue._ts_rescue_scan(task_info, fail_reason="test")
        assert res is None


def test_ts_rescue_scan_error_paths_and_geometric_failure(tmp_path):
    task_info = _task_info_for_scan(
        tmp_path / "rescue_errors",
        keyword="opt ts freq",
        config_extra={"ts_bond_atoms": "1,2"},
    )

    with (
        patch("confflow.calc.rescue.executor._run_calculation_step") as mock_run,
        patch("confflow.calc.rescue._get_policy"),
    ):
        mock_run.side_effect = (
            [
                {"e_low": -100.0, "final_coords": ["C 0 0 0", "H 0 0 1.5"]},
                {"e_low": -110.0, "final_coords": ["C 0 0 0", "H 0 0 1.4"]},
                {"e_low": -90.0, "final_coords": ["C 0 0 0", "H 0 0 1.6"]},
                {"e_low": -110.0, "final_coords": ["C 0 0 0", "H 0 0 1.7"]},
            ]
            + [{"e_low": -95.0, "final_coords": ["C 0 0 0", "H 0 0 1.6"]}] * 11
            + [{"status": "success", "e_low": -90.0, "final_coords": ["C 0 0 0", "H 0 0 1.61"]}]
        )

        res = rescue._ts_rescue_scan(task_info, fail_reason="test")
        assert res is None

    task_info2 = _task_info_for_scan(
        tmp_path / "rescue_geom",
        keyword="opt ts",
        config_extra={"ts_bond_drift_threshold": 0.1},
        job="job2",
    )

    with (
        patch("confflow.calc.rescue.executor._run_calculation_step") as mock_run,
        patch("confflow.calc.rescue._get_policy"),
    ):
        mock_run.side_effect = (
            [
                {"e_low": -100.0, "final_coords": ["C 0 0 0", "H 0 0 1.5"]},
                {"e_low": -110.0, "final_coords": ["C 0 0 0", "H 0 0 1.4"]},
                {"e_low": -90.0, "final_coords": ["C 0 0 0", "H 0 0 1.6"]},
                {"e_low": -110.0, "final_coords": ["C 0 0 0", "H 0 0 1.7"]},
            ]
            + [{"e_low": -95.0, "final_coords": ["C 0 0 0", "H 0 0 1.6"]}] * 11
            + [{"status": "success", "e_low": -90.0, "final_coords": ["C 0 0 0", "H 0 0 1.7"]}]
        )

        res2 = rescue._ts_rescue_scan(task_info2, fail_reason="test")
        assert res2 is None


def test_ts_rescue_scan_internal_patches_v8_style(tmp_path):
    wd = tmp_path / "work"
    wd.mkdir()

    job = "test_job_geom"
    initial_coords = ["3", "comment", "C 0 0 0", "H 0 0 1.1", "H 0 0 -1.1"]

    cfg = {
        "ts_bond_atoms": [2, 3],
        "ts_bond_drift_threshold": 0.1,
        "itask": 3,
        "keyword": "opt(ts,calcfc)",
        "iprog": "gaussian",
    }

    task_info = {
        "work_dir": str(wd),
        "job_name": job,
        "coords": initial_coords,
        "config": cfg,
    }

    def mock_run_step(ts_wd, ts_job, policy, coords, ts_cfg, **kwargs):
        if "scan_0" in ts_job:
            return {"e_low": -1.0, "final_coords": coords}
        if "_rescue" in ts_job and "scan" not in ts_job:
            return {"final_coords": ["2.0"], "e_low": -1.0}
        return {"e_low": -1.1, "final_coords": coords}

    def mock_bond_length(coords, a1, a2):
        try:
            return float(coords[0])
        except Exception:
            return 1.1

    with patch("confflow.calc.rescue.executor._run_calculation_step", side_effect=mock_run_step):
        with (
            patch("confflow.calc.rescue._get_policy"),
            patch("confflow.calc.rescue._keyword_requests_freq", return_value=False),
            patch("confflow.calc.rescue.make_scan_keyword_from_ts_keyword", return_value="scan"),
            patch(
                "confflow.calc.rescue._bond_length_from_xyz_lines", side_effect=mock_bond_length
            ),
            patch("confflow.blocks.refine.rmsd_engine.fast_rmsd", return_value=0.01),
            patch(
                "confflow.calc.scan_ops._set_bond_length_on_coords",
                side_effect=lambda c, a1, a2, r: [str(r)],
            ),
        ):
            res = rescue._ts_rescue_scan(task_info, "fail")
            assert res is None


def test_ts_rescue_scan_freq_failure_with_lowest(tmp_path):
    wd = tmp_path / "work"
    wd.mkdir()

    job = "test_job_freq"
    initial_coords = ["3", "comment", "C 0 0 0", "H 0 0 1.1", "H 0 0 -1.1"]

    cfg = {
        "ts_bond_atoms": [2, 3],
        "itask": 3,
        "keyword": "opt(ts,calcfc) freq",
        "iprog": "gaussian",
    }

    task_info = {"work_dir": str(wd), "job_name": job, "coords": initial_coords, "config": cfg}

    def mock_run_step(ts_wd, ts_job, policy, coords, ts_cfg, **kwargs):
        if "scan_0" in ts_job:
            return {"e_low": -1.0, "final_coords": coords}
        if "_rescue" in ts_job and "scan" not in ts_job:
            return {
                "final_coords": coords,
                "e_low": -1.0,
                "num_imag_freqs": 2,
                "lowest_freq": -100.0,
            }
        return {"e_low": -1.1, "final_coords": coords}

    with patch("confflow.calc.rescue.executor._run_calculation_step", side_effect=mock_run_step):
        with (
            patch("confflow.calc.rescue._get_policy"),
            patch("confflow.calc.rescue._keyword_requests_freq", return_value=True),
            patch("confflow.calc.rescue.make_scan_keyword_from_ts_keyword", return_value="scan"),
            patch("confflow.calc.rescue._bond_length_from_xyz_lines", return_value=1.1),
            patch("confflow.blocks.refine.rmsd_engine.fast_rmsd", return_value=0.01),
            patch(
                "confflow.calc.scan_ops._set_bond_length_on_coords",
                side_effect=lambda c, a1, a2, r: [str(r)],
            ),
        ):
            res = rescue._ts_rescue_scan(task_info, "fail")
            assert res is None
