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
        generate_html_report,
        generate_workflow_section,
    )

    assert format_duration(30) == "30.0s"
    assert format_duration(120) == "2.0min"
    assert format_duration(7200) == "2.0h"

    assert calculate_boltzmann_weights([]) == []
    assert calculate_boltzmann_weights([None, float("inf")]) == [0, 0]

    assert generate_workflow_section({}) == ""

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

    html = generate_workflow_section(stats)
    assert "Step1" in html
    assert "8" in html

    with patch("sqlite3.connect", side_effect=Exception("DB Error")):
        html2 = generate_workflow_section(stats)
        assert "Step1" in html2

    confs = [
        {"metadata": {"E": "invalid", "G_corr": "0.1"}, "atoms": []},
        {"metadata": {"Energy": 1.0}, "atoms": []},
    ]
    report_path = tmp_path / "report.html"
    generate_html_report(confs, str(report_path))
    assert report_path.exists()


def test_viz_main_cli(tmp_path):
    from confflow.blocks.viz.report import main as viz_main

    xyz_path = tmp_path / "test.xyz"
    xyz_path.write_text("2\n\nC 0 0 0\nH 0 0 1\n")

    with patch("sys.argv", ["confviz", "nonexistent.xyz"]):
        assert viz_main() == 1

    empty_xyz = tmp_path / "empty.xyz"
    empty_xyz.write_text("")
    with patch("sys.argv", ["confviz", str(empty_xyz)]):
        assert viz_main() == 1

    stats_path = tmp_path / "stats.json"
    stats_path.write_text(json.dumps({"steps": []}))
    with patch("sys.argv", ["confviz", str(xyz_path), "--stats", str(stats_path)]):
        assert viz_main() == 0

    stats_path.write_text("invalid json")
    with patch("sys.argv", ["confviz", str(xyz_path), "--stats", str(stats_path)]):
        assert viz_main() == 0


def test_viz_report_parse_and_stats_warning(tmp_path):
    from confflow.blocks.viz import report as viz_report

    assert viz_report.parse_xyz_file(str(tmp_path / "missing.xyz")) == []

    with patch("confflow.blocks.viz.report.read_xyz_file", side_effect=Exception("boom")):
        assert viz_report.parse_xyz_file(str(tmp_path / "any.xyz")) == []

    out_html = tmp_path / "out.html"
    conformers = [
        {"metadata": {"E": "-1.0", "G_corr": "0.1", "TSBond": {"bad": 1}}},
        {"metadata": {"Energy": -2.0, "ts_bond_length": None}},
    ]
    viz_report.generate_html_report(conformers, str(out_html), temperature=298.15, stats={"steps": []})
    assert out_html.exists()

    xyz = tmp_path / "one.xyz"
    xyz.write_text("1\ncomment\nH 0 0 0\n")
    stats = tmp_path / "stats.json"
    stats.write_text("{not-json")
    out2 = tmp_path / "r2.html"
    ret = viz_report.main([str(xyz), "-o", str(out2), "--stats", str(stats)])
    assert ret == 0
    assert out2.exists()


def test_core_types_imported_for_coverage():
    from confflow.core import types as t

    assert t.CoordLine is str
    assert hasattr(t, "CoordLines")
