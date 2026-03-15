#!/usr/bin/env python3

"""Hotspot tests for rescue internals."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

from confflow.calc import rescue


class _FakeScanner:
    def __init__(self, results):
        self._results = iter(results)

    def run(self, coords, r):
        return next(self._results)


def test_prepare_rescue_context_prefers_backup_coords_and_logs_origin():
    task_info = {"job_name": "job", "work_dir": "/tmp/work", "config": {}, "coords": ["fallback"]}
    backup_coords = ["C 0 0 0", "H 0 0 1"]

    with (
        patch("confflow.calc.rescue.parse_iprog", return_value=1),
        patch("confflow.calc.rescue._parse_ts_bond_atoms", return_value=(1, 2)),
        patch("confflow.calc.rescue.make_scan_keyword_from_ts_keyword", return_value="scan"),
        patch("confflow.calc.rescue._find_failed_ts_input_coords", return_value=backup_coords),
        patch("confflow.calc.rescue._bond_length_from_xyz_lines", return_value=1.23),
        patch("confflow.calc.rescue.console.print"),
        patch("confflow.calc.rescue.print_kv"),
        patch("confflow.calc.rescue.logger.info") as mock_info,
    ):
        ctx = rescue._prepare_rescue_context(task_info, "failed")

    assert ctx is not None
    assert ctx["base_coords"] == backup_coords
    assert ctx["r0"] == 1.23
    assert mock_info.call_count == 2


def test_prepare_rescue_context_reports_missing_coords():
    task_info = {"job_name": "job", "work_dir": "/tmp/work", "config": {}, "coords": None}

    with (
        patch("confflow.calc.rescue.parse_iprog", return_value=1),
        patch("confflow.calc.rescue._parse_ts_bond_atoms", return_value=(1, 2)),
        patch("confflow.calc.rescue.make_scan_keyword_from_ts_keyword", return_value="scan"),
        patch("confflow.calc.rescue._find_failed_ts_input_coords", return_value=None),
        patch("confflow.calc.rescue._write_ts_failure_report") as mock_report,
    ):
        ctx = rescue._prepare_rescue_context(task_info, "failed")

    assert ctx is None
    mock_report.assert_called_once()
    assert "missing TS input structure coordinates" in mock_report.call_args.args[3]


def test_prepare_rescue_context_reports_uncomputable_bond_length():
    task_info = {
        "job_name": "job",
        "work_dir": "/tmp/work",
        "config": {},
        "coords": ["C 0 0 0", "H 0 0 1"],
    }

    with (
        patch("confflow.calc.rescue.parse_iprog", return_value=1),
        patch("confflow.calc.rescue._parse_ts_bond_atoms", return_value=(1, 2)),
        patch("confflow.calc.rescue.make_scan_keyword_from_ts_keyword", return_value="scan"),
        patch("confflow.calc.rescue._find_failed_ts_input_coords", return_value=None),
        patch("confflow.calc.rescue._bond_length_from_xyz_lines", return_value=None),
        patch("confflow.calc.rescue._write_ts_failure_report") as mock_report,
    ):
        ctx = rescue._prepare_rescue_context(task_info, "failed")

    assert ctx is None
    mock_report.assert_called_once()
    assert "unable to compute bond length" in mock_report.call_args.args[3]


def test_run_coarse_and_fine_scan_reports_initial_failure():
    scanner = _FakeScanner([(None, None, "bad start")])
    params = SimpleNamespace(
        max_steps=5,
        coarse_k_max=3,
        coarse_step=0.1,
        uphill_limit=2,
        fine_half_window=0.1,
        fine_step=0.1,
    )

    with (
        patch("confflow.calc.rescue._write_ts_failure_report") as mock_report,
        patch("confflow.calc.rescue.console.print"),
        patch("confflow.calc.rescue.logger.warning") as mock_warning,
    ):
        out = rescue._run_coarse_and_fine_scan(
            scanner,
            1.0,
            ["C 0 0 0", "H 0 0 1"],
            params,
            "/tmp/work",
            "job",
            1,
            2,
            "failed",
        )

    assert out is None
    mock_report.assert_called_once()
    mock_warning.assert_called_once()


def test_run_coarse_and_fine_scan_aborts_when_coarse_scan_is_strictly_increasing():
    scanner = _FakeScanner(
        [
            (0.0, ["coords"], None),
            (None, None, None),
            (0.1, ["coords"], None),
        ]
    )
    params = SimpleNamespace(
        max_steps=5,
        coarse_k_max=3,
        coarse_step=0.1,
        uphill_limit=2,
        fine_half_window=0.1,
        fine_step=0.1,
    )

    with (
        patch("confflow.calc.rescue._coarse_extend", return_value=True),
        patch("confflow.calc.rescue._emit_and_write_scan_table") as mock_table,
        patch("confflow.calc.rescue._write_ts_failure_report") as mock_report,
    ):
        out = rescue._run_coarse_and_fine_scan(
            scanner,
            1.0,
            ["C 0 0 0", "H 0 0 1"],
            params,
            "/tmp/work",
            "job",
            1,
            2,
            "failed",
        )

    assert out is None
    mock_table.assert_called_once()
    mock_report.assert_called_once()
    assert "strictly increasing" in mock_report.call_args.args[3]


def test_run_coarse_and_fine_scan_uses_direct_fine_center_and_falls_back_to_max_point():
    scanner = _FakeScanner(
        [
            (5.0, ["center"], None),
            (4.0, ["left"], None),
            (4.0, ["right"], None),
            (0.5, ["f1"], None),
            (1.5, ["f2"], None),
            (1.0, ["f3"], None),
        ]
    )
    params = SimpleNamespace(
        max_steps=5,
        coarse_k_max=3,
        coarse_step=0.1,
        uphill_limit=2,
        fine_half_window=0.1,
        fine_step=0.1,
    )

    with (
        patch("confflow.calc.rescue._find_local_max", side_effect=[None, None]),
        patch("confflow.calc.rescue._emit_and_write_scan_table") as mock_table,
    ):
        out = rescue._run_coarse_and_fine_scan(
            scanner,
            1.0,
            ["C 0 0 0", "H 0 0 1"],
            params,
            "/tmp/work",
            "job",
            1,
            2,
            "failed",
        )

    assert out is not None
    r_best, coords_best, _points, fine_points = out
    assert r_best == 1.0
    assert coords_best == ["f2"]
    assert len(fine_points) == 3
    mock_table.assert_called_once()


def test_run_coarse_and_fine_scan_reports_when_fine_scan_has_no_valid_points():
    scanner = _FakeScanner(
        [
            (5.0, ["center"], None),
            (None, None, None),
            (None, None, None),
            (None, None, None),
            (None, None, None),
            (None, None, None),
        ]
    )
    params = SimpleNamespace(
        max_steps=5,
        coarse_k_max=3,
        coarse_step=0.1,
        uphill_limit=2,
        fine_half_window=0.1,
        fine_step=0.1,
    )

    with (
        patch("confflow.calc.rescue._find_local_max", side_effect=[None, None]),
        patch("confflow.calc.rescue._emit_and_write_scan_table") as mock_table,
        patch("confflow.calc.rescue._write_ts_failure_report") as mock_report,
    ):
        out = rescue._run_coarse_and_fine_scan(
            scanner,
            1.0,
            ["C 0 0 0", "H 0 0 1"],
            params,
            "/tmp/work",
            "job",
            1,
            2,
            "failed",
        )

    assert out is None
    mock_table.assert_called_once()
    mock_report.assert_called_once()
    assert "fine scan has no valid points" in mock_report.call_args.args[3]


def test_run_ts_reoptimization_computes_gcorr_and_keeps_dirs_when_requested():
    cfg = {"keyword": "opt", "ts_rescue_keep_scan_dirs": "true"}
    task_info = {"job_name": "job", "work_dir": "/tmp/work"}

    with (
        patch(
            "confflow.calc.rescue.executor._run_calculation_step",
            return_value={
                "final_coords": ["C 0 0 0", "H 0 0 1.4"],
                "e_low": -10.0,
                "g_low": -9.5,
                "g_corr": None,
                "num_imag_freqs": 1,
                "lowest_freq": -100.0,
            },
        ),
        patch("confflow.calc.rescue._get_policy", return_value=object()),
        patch("confflow.calc.rescue._keyword_requests_freq", return_value=False),
        patch("confflow.calc.rescue.validate_ts_bond_drift", return_value=None),
        patch("confflow.calc.rescue.get_itask", return_value=2),
        patch("confflow.calc.rescue._bond_length_from_xyz_lines", return_value=1.4),
        patch("confflow.calc.rescue.console.print"),
        patch("confflow.calc.rescue.logger.info"),
        patch("confflow.calc.rescue.executor.handle_backups") as mock_backups,
    ):
        out = rescue._run_ts_reoptimization(
            cfg,
            task_info,
            "/tmp/work",
            "job",
            1,
            2,
            1.25,
            ["coords"],
            ["base"],
            [],
            [],
        )

    assert out is not None
    assert out["status"] == "success"
    assert out["final_gibbs_energy"] == -9.5
    assert out["g_corr"] == 0.5
    assert out["ts_bond_atoms"] == "1,2"
    assert out["ts_bond_length"] == 1.4
    mock_backups.assert_called_once_with(
        "/tmp/work/ts_rescue",
        cfg,
        success=True,
        cleanup_work_dir=False,
    )


def test_run_ts_reoptimization_fails_when_final_structure_is_missing():
    cfg = {"keyword": "opt"}

    with (
        patch(
            "confflow.calc.rescue.executor._run_calculation_step",
            return_value={"final_coords": None, "e_low": -10.0},
        ),
        patch("confflow.calc.rescue._get_policy", return_value=object()),
        patch("confflow.calc.rescue._write_ts_failure_report") as mock_report,
        patch("confflow.calc.rescue.console.print"),
        patch("confflow.calc.rescue.logger.warning"),
        patch("confflow.calc.rescue.executor.handle_backups") as mock_backups,
    ):
        out = rescue._run_ts_reoptimization(
            cfg,
            {"job_name": "job"},
            "/tmp/work",
            "job",
            1,
            2,
            1.25,
            ["coords"],
            ["base"],
            [],
            [],
        )

    assert out is None
    mock_report.assert_called_once()
    assert "produced no final structure" in mock_report.call_args.args[3]
    mock_backups.assert_called_once()


def test_run_ts_reoptimization_reports_missing_freq_info_and_handles_cleanup_failure():
    cfg = {"keyword": "opt freq"}

    with (
        patch(
            "confflow.calc.rescue.executor._run_calculation_step",
            return_value={"final_coords": ["C 0 0 0", "H 0 0 1.1"], "e_low": -10.0},
        ),
        patch("confflow.calc.rescue._get_policy", return_value=object()),
        patch("confflow.calc.rescue._keyword_requests_freq", return_value=True),
        patch("confflow.calc.rescue._write_ts_failure_report") as mock_report,
        patch("confflow.calc.rescue.console.print"),
        patch("confflow.calc.rescue.logger.warning") as mock_warning,
        patch("confflow.calc.rescue.logger.debug") as mock_debug,
        patch(
            "confflow.calc.rescue.executor.handle_backups",
            side_effect=OSError("cleanup failed"),
        ),
    ):
        out = rescue._run_ts_reoptimization(
            cfg,
            {"job_name": "job"},
            "/tmp/work",
            "job",
            1,
            2,
            1.25,
            ["coords"],
            ["base"],
            [],
            [],
        )

    assert out is None
    assert "no frequency info was parsed" in mock_report.call_args.args[3]
    mock_warning.assert_called_once()
    mock_debug.assert_called_once()
