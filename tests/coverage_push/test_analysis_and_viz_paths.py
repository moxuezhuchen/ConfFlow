import json
from unittest.mock import patch

import numpy as np
import pytest


def test_analysis_helpers():
    from confflow.calc.analysis import _coords_array_from_xyz_lines, _parse_ts_bond_atoms

    assert _parse_ts_bond_atoms(None) is None
    assert _parse_ts_bond_atoms([1]) is None
    assert _parse_ts_bond_atoms(["a", "b"]) is None
    assert _parse_ts_bond_atoms("1,1") is None
    assert _parse_ts_bond_atoms("0,1") is None
    assert _parse_ts_bond_atoms("1,2") == (1, 2)

    assert _coords_array_from_xyz_lines([]) is None
    assert _coords_array_from_xyz_lines(["H 0 0"]) is None
    assert _coords_array_from_xyz_lines(["H 0 0 0", "C 1 1"]) is None

    lines = ["C 0.0 0.0 0.0", "H 1.0 2.0 3.0"]
    arr = _coords_array_from_xyz_lines(lines)
    assert arr.shape == (2, 3)
    assert np.allclose(arr[1], [1.0, 2.0, 3.0])


def test_calc_analysis_parsing_edge_cases():
    from confflow.calc import analysis as ana

    assert ana._parse_ts_bond_atoms(["1", "x", 2]) == (1, 2)
    assert ana._parse_ts_bond_atoms(["x", "y"]) is None

    assert ana._coords_array_from_xyz_lines(["C a b c"]) is None
    assert ana._coords_array_from_xyz_lines([None]) is None


def test_calculate_boltzmann_weights_high_energy():
    from confflow.blocks.viz.report import calculate_boltzmann_weights

    energies = [0.0, 1.0]
    weights = calculate_boltzmann_weights(energies)
    assert weights[0] > 99.9
    assert weights[1] < 0.1


def test_viz_report_edge_cases(tmp_path):
    from confflow.blocks.viz.report import (
        calculate_boltzmann_weights,
        format_duration,
        generate_text_report,
    )

    assert format_duration(30) == "30.0s"
    assert format_duration(120) == "2.0min"
    assert format_duration(7200) == "2.0h"

    assert calculate_boltzmann_weights([]) == []
    assert calculate_boltzmann_weights([None, float("inf")]) == [0, 0]

    stats = {
        "steps": [
            {
                "name": "Step1",
                "type": "calc",
                "status": "completed",
                "input_conformers": 10,
                "output_conformers": 8,
                "output_xyz": str(tmp_path / "step1.xyz"),
                "duration_seconds": 100,
            }
        ],
        "total_duration_seconds": 100,
        "initial_conformers": 10,
        "final_conformers": 8,
    }

    confs = [
        {"metadata": {"E": "invalid", "G_corr": "0.1"}, "atoms": []},
        {"metadata": {"Energy": 1.0}, "atoms": []},
    ]
    text = generate_text_report(confs, stats=stats)
    assert "WORKFLOW SUMMARY" in text
    assert "Step1" in text

def test_viz_report_parse_and_stats_warning(tmp_path):
    from confflow.blocks.viz import report as viz_report

    assert viz_report.parse_xyz_file(str(tmp_path / "missing.xyz")) == []

    with patch("confflow.blocks.viz.report.read_xyz_file", side_effect=Exception("boom")):
        assert viz_report.parse_xyz_file(str(tmp_path / "any.xyz")) == []

    conformers = [
        {"metadata": {"E": "-1.0", "G_corr": "0.1", "TSBond": {"bad": 1}}},
        {"metadata": {"Energy": -2.0, "ts_bond_length": None}},
    ]
    text = viz_report.generate_text_report(conformers, temperature=298.15, stats={"steps": []})
    assert "CONFORMER ANALYSIS" in text


def test_core_types_imported_for_coverage():
    from confflow.core import types as t

    assert t.CoordLine is str
    assert hasattr(t, "CoordLines")
