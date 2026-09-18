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

#: Thermochemistry follows a harmonic-frequency table and marks it complete.
_GAUSSIAN_THERMO = (
    " Zero-point correction=                           0.024000\n"
    " Sum of electronic and thermal Free Energies=          -1.500000\n"
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
            "NORMAL MODES",
            "----------------",
            "0: -999.00 cm-1",
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


def test_orca_linear_molecule_rigid_modes_not_imaginary(tmp_path):
    """Linear molecules have 5 rigid modes; none may be counted as imaginary."""
    log = tmp_path / "orca_linear.out"
    log.write_text(
        _orca_log([-4.0, -1.0, 0.0, 1.0, 2.0, 15.0, 600.0, 1300.0, 2400.0]),
        encoding="utf-8",
    )

    res = OrcaPolicy().parse_output(str(log), {}, is_sp_task=False)

    assert res["num_imag_freqs"] == 0
    # A genuinely soft but real vibration above the noise floor is kept.
    assert res["lowest_freq"] == 15.0


def test_orca_diatomic_real_vibration_is_kept(tmp_path):
    """Five rigid modes (one negative) plus one real vibration."""
    log = tmp_path / "orca_diatomic.out"
    log.write_text(_orca_log([-4.0, -2.0, -0.5, 0.5, 1.5, 2000.0]), encoding="utf-8")

    res = OrcaPolicy().parse_output(str(log), {}, is_sp_task=False)

    assert res["num_imag_freqs"] == 0
    assert res["lowest_freq"] == 2000.0


# ---------------------------------------------------------------------------
# Gaussian parser
# ---------------------------------------------------------------------------


def test_gaussian_ignores_near_zero_negative_noise(tmp_path):
    log = tmp_path / "g.log"
    log.write_text(
        " Frequencies -- -3.0000   -2.0000   100.0000\n" + _GAUSSIAN_ORIENTATION + _GAUSSIAN_THERMO,
        encoding="utf-8",
    )

    res = GaussianPolicy().parse_output(str(log), {}, is_sp_task=False)

    assert res["num_imag_freqs"] == 0
    assert res["lowest_freq"] == 100.0


def test_gaussian_counts_true_imaginary_mode(tmp_path):
    log = tmp_path / "g.log"
    log.write_text(
        " Frequencies -- -100.0000   200.0000   300.0000\n"
        + _GAUSSIAN_ORIENTATION
        + _GAUSSIAN_THERMO,
        encoding="utf-8",
    )

    res = GaussianPolicy().parse_output(str(log), {}, is_sp_task=False)

    assert res["num_imag_freqs"] == 1
    assert res["lowest_freq"] == -100.0


def test_gaussian_uses_last_complete_frequency_section(tmp_path):
    """Complete A + complete B -> B (each committed at its thermochemistry)."""
    log = tmp_path / "g.log"
    log.write_text(
        " Harmonic frequencies (cm**-1), IR intensities\n"
        " Frequencies -- -350.0000   -100.0000   200.0000\n"
        + _GAUSSIAN_ORIENTATION
        + _GAUSSIAN_THERMO
        + " Harmonic frequencies (cm**-1), IR intensities\n"
        " Frequencies -- -120.0000   300.0000   400.0000\n"
        + _GAUSSIAN_ORIENTATION
        + _GAUSSIAN_THERMO,
        encoding="utf-8",
    )

    res = GaussianPolicy().parse_output(str(log), {}, is_sp_task=False)

    assert res["num_imag_freqs"] == 1
    assert res["lowest_freq"] == -120.0


def test_gaussian_last_section_without_imaginary_wins(tmp_path):
    log = tmp_path / "g.log"
    log.write_text(
        " Harmonic frequencies (cm**-1), IR intensities\n"
        " Frequencies -- -350.0000   200.0000\n"
        + _GAUSSIAN_ORIENTATION
        + _GAUSSIAN_THERMO
        + " Harmonic frequencies (cm**-1), IR intensities\n"
        " Frequencies -- 100.0000   300.0000\n" + _GAUSSIAN_ORIENTATION + _GAUSSIAN_THERMO,
        encoding="utf-8",
    )

    res = GaussianPolicy().parse_output(str(log), {}, is_sp_task=False)

    assert res["num_imag_freqs"] == 0
    assert res["lowest_freq"] == 100.0


def test_gaussian_truncated_last_section_keeps_previous(tmp_path):
    """A section header with no frequencies must not erase the last full one."""
    log = tmp_path / "g.log"
    log.write_text(
        " Harmonic frequencies (cm**-1), IR intensities\n"
        " Frequencies -- -120.0000   300.0000\n"
        + _GAUSSIAN_ORIENTATION
        + _GAUSSIAN_THERMO
        + " Harmonic frequencies (cm**-1), IR intensities\n",
        encoding="utf-8",
    )

    res = GaussianPolicy().parse_output(str(log), {}, is_sp_task=False)

    assert res["num_imag_freqs"] == 1
    assert res["lowest_freq"] == -120.0


def test_gaussian_partial_trailing_section_not_committed(tmp_path):
    """A trailing section with one Frequencies line is incomplete at EOF.

    It must not override the last committed section.
    """
    log = tmp_path / "g.log"
    log.write_text(
        " Harmonic frequencies (cm**-1), IR intensities\n"
        " Frequencies -- -120.0000   300.0000\n"
        + _GAUSSIAN_ORIENTATION
        + _GAUSSIAN_THERMO
        + " Harmonic frequencies (cm**-1), IR intensities\n"
        " Frequencies -- -500.0000   900.0000\n",
        encoding="utf-8",
    )

    res = GaussianPolicy().parse_output(str(log), {}, is_sp_task=False)

    assert res["num_imag_freqs"] == 1
    assert res["lowest_freq"] == -120.0


def test_orca_uses_last_complete_frequency_section(tmp_path):
    """Complete A + complete B -> B (each committed at NORMAL MODES)."""
    log = tmp_path / "orca.out"
    log.write_text(
        "VIBRATIONAL FREQUENCIES\n"
        "0: -100.00 cm-1\n"
        "1: 200.00 cm-1\n"
        "NORMAL MODES\n"
        "0: -999.00 cm-1\n"
        "VIBRATIONAL FREQUENCIES\n"
        "0: -120.00 cm-1\n"
        "1: 300.00 cm-1\n"
        "NORMAL MODES\n"
        "0: -999.00 cm-1\n",
        encoding="utf-8",
    )

    res = OrcaPolicy().parse_output(str(log), {}, is_sp_task=False)

    assert res["num_imag_freqs"] == 1
    assert res["lowest_freq"] == -120.0


def test_orca_partial_trailing_section_not_committed(tmp_path):
    """A trailing candidate cut off at EOF was never committed.

    The last explicitly completed section stays authoritative.
    """
    log = tmp_path / "orca.out"
    log.write_text(
        "VIBRATIONAL FREQUENCIES\n"
        "0: -100.00 cm-1\n"
        "1: 200.00 cm-1\n"
        "NORMAL MODES\n"
        "VIBRATIONAL FREQUENCIES\n"
        "0: -500.00 cm-1\n"
        "1: 900.00 cm-1\n",
        encoding="utf-8",
    )

    res = OrcaPolicy().parse_output(str(log), {}, is_sp_task=False)

    assert res["num_imag_freqs"] == 1
    assert res["lowest_freq"] == -100.0


def test_orca_header_only_trailing_section_not_committed(tmp_path):
    log = tmp_path / "orca.out"
    log.write_text(
        "VIBRATIONAL FREQUENCIES\n"
        "0: -100.00 cm-1\n"
        "1: 200.00 cm-1\n"
        "NORMAL MODES\n"
        "VIBRATIONAL FREQUENCIES\n",
        encoding="utf-8",
    )

    res = OrcaPolicy().parse_output(str(log), {}, is_sp_task=False)

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


def test_validate_ts_rmsd_fails_closed_when_not_computable():
    """Unparseable or mismatched coordinates must fail, not silently pass."""
    unparseable = validate_ts_rmsd(["coords"], ["H 0 0 0"], 0.1)
    assert unparseable is not None
    assert "could not be parsed" in unparseable

    mismatch = validate_ts_rmsd(["H 0 0 0"], ["H 0 0 0", "H 0 0 1"], 0.1)
    assert mismatch is not None
    assert "atom count mismatch" in mismatch

    non_finite = validate_ts_rmsd(["H 0 0 0", "H nan 0 1"], ["H 0 0 0", "H 0 0 1"], 0.1)
    assert non_finite is not None


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
