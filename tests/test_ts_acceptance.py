#!/usr/bin/env python3

"""TS frequency parsing and geometry acceptance."""

from __future__ import annotations

from unittest.mock import patch

from confflow.calc import rescue
from confflow.calc.analysis import (
    analyze_vibrational_frequencies,
    validate_ts_rmsd,
)
from confflow.calc.policies.gaussian import GaussianPolicy
from confflow.calc.policies.orca import OrcaPolicy

_GAUSSIAN_ORIENTATION = (
    " Standard orientation:\n"
    " ---------------------------------------------------------------------\n"
    " Center     Atomic      Atomic             Coordinates (Angstroms)\n"
    " Number     Number       Type             X           Y           Z\n"
    " ---------------------------------------------------------------------\n"
    "      1          1           0        0.000000    0.000000    0.000000\n"
    "      2          1           0        0.000000    0.000000    1.000000\n"
    " ---------------------------------------------------------------------\n"
)


def _orca_log(freqs: list[float]) -> str:
    lines = [
        "G-E(el) ... 0.10000 Eh",
        "Final Gibbs free energy ... -1.50000 Eh",
        "VIBRATIONAL FREQUENCIES",
        "-----------------------",
    ]
    lines.extend(f"{i}: {freq:.2f} cm-1" for i, freq in enumerate(freqs))
    lines.extend(
        [
            "CARTESIAN COORDINATES (ANGSTROEM)",
            "---------------------------------",
            "H 0.0 0.0 0.0",
            "H 0.0 0.0 1.0",
            "",
            " ",
        ]
    )
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Frequency analysis helper
# ---------------------------------------------------------------------------


def test_analyze_skips_rigid_modes_and_noise():
    imag, lowest = analyze_vibrational_frequencies(
        [-2.0, 0.0, 1.0, -1.0, 2.0, -0.5, -100.0, 200.0], skip_rigid_modes=6
    )
    assert imag == 1
    assert lowest == -100.0


def test_analyze_noise_floor_without_rigid_skip():
    imag, lowest = analyze_vibrational_frequencies([-5.0, 5.0, 100.0])
    assert imag == 0
    assert lowest == 100.0


# ---------------------------------------------------------------------------
# ORCA parser
# ---------------------------------------------------------------------------


def test_orca_ignores_negative_rigid_mode_noise(tmp_path):
    log = tmp_path / "orca.out"
    log.write_text(_orca_log([-3.0] * 6 + [150.0, 300.0]), encoding="utf-8")

    res = OrcaPolicy().parse_output(str(log), {}, is_sp_task=False)

    assert res["num_imag_freqs"] == 0
    assert res["lowest_freq"] == 150.0


def test_orca_counts_true_imaginary_mode(tmp_path):
    log = tmp_path / "orca.out"
    log.write_text(_orca_log([-2.0, -1.0, 0.0, 1.0, 2.0, -0.5, -100.0, 200.0]), encoding="utf-8")

    res = OrcaPolicy().parse_output(str(log), {}, is_sp_task=False)

    assert res["num_imag_freqs"] == 1
    assert res["lowest_freq"] == -100.0


# ---------------------------------------------------------------------------
# Gaussian parser
# ---------------------------------------------------------------------------


def test_gaussian_ignores_near_zero_negative_noise(tmp_path):
    log = tmp_path / "g.log"
    log.write_text(
        " Frequencies -- -3.0000   -2.0000   100.0000\n" + _GAUSSIAN_ORIENTATION,
        encoding="utf-8",
    )

    res = GaussianPolicy().parse_output(str(log), {}, is_sp_task=False)

    assert res["num_imag_freqs"] == 0
    assert res["lowest_freq"] == 100.0


def test_gaussian_counts_true_imaginary_mode(tmp_path):
    log = tmp_path / "g.log"
    log.write_text(
        " Frequencies -- -100.0000   200.0000   300.0000\n" + _GAUSSIAN_ORIENTATION,
        encoding="utf-8",
    )

    res = GaussianPolicy().parse_output(str(log), {}, is_sp_task=False)

    assert res["num_imag_freqs"] == 1
    assert res["lowest_freq"] == -100.0


# ---------------------------------------------------------------------------
# TS RMSD acceptance
# ---------------------------------------------------------------------------


def test_validate_ts_rmsd_is_rigid_invariant():
    initial = ["H 0 0 0", "H 0 0 1.0"]
    # Same structure rotated onto the y-axis and translated.
    final = ["H 5.0 0.0 0.0", "H 5.0 1.0 0.0"]

    assert validate_ts_rmsd(initial, final, 0.01) is None


def test_validate_ts_rmsd_detects_drift():
    initial = ["H 0 0 0", "H 0 0 1.0"]
    final = ["H 0 0 0", "H 0 0 1.5"]

    err = validate_ts_rmsd(initial, final, 0.1)

    assert err is not None
    assert "aligned RMSD" in err
    assert "exceeds threshold 0.100" in err


def test_validate_ts_rmsd_unparseable_returns_none():
    assert validate_ts_rmsd(["coords"], ["H 0 0 0"], 0.1) is None
    assert validate_ts_rmsd(["H 0 0 0"], ["H 0 0 0", "H 0 0 1"], 0.1) is None


# ---------------------------------------------------------------------------
# Rescue reoptimization uses the scan candidate as reference
# ---------------------------------------------------------------------------


def test_rescue_rmsd_uses_scan_candidate(tmp_path):
    cfg = {"keyword": "opt freq", "ts_rmsd_threshold": 0.1}
    coords_best = ["H 0 0 0", "H 0 0 1.0"]
    final = ["H 0 0 0", "H 0 0 1.5"]

    with (
        patch(
            "confflow.calc.rescue.executor._run_calculation_step",
            return_value={
                "final_coords": final,
                "e_low": -1.0,
                "num_imag_freqs": 1,
                "lowest_freq": -100.0,
            },
        ),
        patch("confflow.calc.rescue._get_policy", return_value=object()),
        patch("confflow.calc.rescue.console.print"),
        patch("confflow.calc.rescue.logger.warning"),
        patch("confflow.calc.rescue._write_ts_failure_report") as mock_report,
        patch("confflow.calc.rescue.executor.handle_backups"),
    ):
        out = rescue._run_ts_reoptimization(
            cfg, {"job_name": "job"}, str(tmp_path), "job", 1, 2, 1.0, coords_best, [], []
        )

    assert out is None
    assert "aligned RMSD" in mock_report.call_args.args[3]


def test_rescue_accepts_final_matching_scan_candidate(tmp_path):
    cfg = {"keyword": "opt freq", "ts_rmsd_threshold": 0.5}
    coords_best = ["H 0 0 0", "H 0 0 1.0"]

    with (
        patch(
            "confflow.calc.rescue.executor._run_calculation_step",
            return_value={
                "final_coords": list(coords_best),
                "e_low": -1.0,
                "num_imag_freqs": 1,
                "lowest_freq": -100.0,
            },
        ),
        patch("confflow.calc.rescue._get_policy", return_value=object()),
        patch("confflow.calc.rescue.console.print"),
        patch("confflow.calc.rescue.logger.info"),
        patch("confflow.calc.rescue.executor.handle_backups"),
    ):
        out = rescue._run_ts_reoptimization(
            cfg, {"job_name": "job"}, str(tmp_path), "job", 1, 2, 1.0, coords_best, [], []
        )

    assert out is not None
    assert out["rescued_by_scan"] is True
