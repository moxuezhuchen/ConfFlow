#!/usr/bin/env python3

"""Tests for viz.report module (merged from test_core.py and test_analysis_and_viz_paths.py)."""

from __future__ import annotations

from unittest.mock import patch

import numpy as np


class TestVizReport:
    """Tests for viz.report module."""

    def test_calculate_boltzmann_weights_basic(self):
        from confflow.blocks.viz.report import calculate_boltzmann_weights

        energies = [-1.0, -1.001]
        weights = calculate_boltzmann_weights(energies)
        assert len(weights) == 2
        assert weights[1] > weights[0]
        assert abs(sum(weights) - 100.0) < 1e-5

    def test_calculate_boltzmann_weights_empty(self):
        from confflow.blocks.viz.report import calculate_boltzmann_weights

        assert calculate_boltzmann_weights([]) == []

    def test_calculate_boltzmann_weights_invalid(self):
        from confflow.blocks.viz.report import calculate_boltzmann_weights

        assert calculate_boltzmann_weights([None, float("inf")]) == [0, 0]

    def test_calculate_boltzmann_weights_high_energy(self):
        from confflow.blocks.viz.report import calculate_boltzmann_weights

        energies = [0.0, 1.0]
        weights = calculate_boltzmann_weights(energies)
        assert weights[0] > 99.9
        assert weights[1] < 0.1

    def test_format_duration(self):
        from confflow.blocks.viz.report import format_duration

        assert format_duration(30) == "30.0s"
        assert format_duration(120) == "2.0min"
        assert format_duration(7200) == "2.0h"

    def test_generate_text_report_basic(self):
        from confflow.blocks.viz.report import generate_text_report

        conformers = [
            {"metadata": {"E": -1.0, "G_corr": 0.1}, "comment": "C1"},
            {"metadata": {"G": -1.1}, "comment": "C2"},
        ]
        stats = {
            "steps": [
                {
                    "index": 1,
                    "name": "Step1",
                    "type": "calc",
                    "status": "completed",
                    "input_conformers": 10,
                    "output_conformers": 8,
                    "duration_seconds": 60,
                }
            ],
            "total_duration_seconds": 60,
            "initial_conformers": 10,
            "final_conformers": 8,
        }
        text = generate_text_report(conformers, stats=stats)
        assert "WORKFLOW SUMMARY" in text
        assert "CONFORMER ANALYSIS" in text
        assert "Step1" in text

    def test_generate_text_report_edge_cases(self, tmp_path):
        from confflow.blocks.viz.report import generate_text_report

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

    def test_viz_report_parse_and_stats_warning(self, tmp_path):
        from confflow.blocks.viz import report as viz_report

        assert viz_report.parse_xyz_file(str(tmp_path / "missing.xyz")) == []
        any_xyz = tmp_path / "any.xyz"
        any_xyz.write_text("1\ncomment\nH 0 0 0\n", encoding="utf-8")

        with patch("confflow.blocks.viz.report.read_xyz_file_safe", return_value=[]):
            assert viz_report.parse_xyz_file(str(tmp_path / "any.xyz")) == []

        conformers = [
            {"metadata": {"E": "-1.0", "G_corr": "0.1", "TSBond": {"bad": 1}}},
            {"metadata": {"Energy": -2.0, "ts_bond_length": None}},
        ]
        text = viz_report.generate_text_report(
            conformers, temperature=298.15, stats={"steps": []}
        )
        assert "CONFORMER ANALYSIS" in text

    def test_viz_report_generation(self, tmp_path):
        import confflow.blocks.viz as viz

        xyz = tmp_path / "result.xyz"
        xyz.write_text("2\nEnergy=-1.0\nH 0 0 0\nH 0 0 0.74\n", encoding="utf-8")
        confs = viz.parse_xyz_file(str(xyz))
        assert len(confs) == 1
        text = viz.generate_text_report(confs, stats={"steps": []})
        assert "CONFORMER ANALYSIS" in text


class TestAnalysisHelpers:
    """Tests for calc.analysis helper functions."""

    def test_parse_ts_bond_atoms(self):
        from confflow.calc.analysis import _parse_ts_bond_atoms

        assert _parse_ts_bond_atoms(None) is None
        assert _parse_ts_bond_atoms([1]) is None
        assert _parse_ts_bond_atoms(["a", "b"]) is None
        assert _parse_ts_bond_atoms("1,1") is None
        assert _parse_ts_bond_atoms("0,1") is None
        assert _parse_ts_bond_atoms("1,2") == (1, 2)
        assert _parse_ts_bond_atoms(["1", "x", 2]) == (1, 2)
        assert _parse_ts_bond_atoms(["x", "y"]) is None

    def test_coords_array_from_xyz_lines(self):
        from confflow.calc.analysis import _coords_array_from_xyz_lines

        assert _coords_array_from_xyz_lines([]) is None
        assert _coords_array_from_xyz_lines(["H 0 0"]) is None
        assert _coords_array_from_xyz_lines(["H 0 0 0", "C 1 1"]) is None
        assert _coords_array_from_xyz_lines(["C a b c"]) is None
        assert _coords_array_from_xyz_lines([None]) is None

        lines = ["C 0.0 0.0 0.0", "H 1.0 2.0 3.0"]
        arr = _coords_array_from_xyz_lines(lines)
        assert arr.shape == (2, 3)
        assert np.allclose(arr[1], [1.0, 2.0, 3.0])


class TestCoreTypes:
    """Tests for core.types module."""

    def test_core_types_imported(self):
        from confflow.core import types as t

        assert t.CoordLine is str
        assert hasattr(t, "CoordLines")
