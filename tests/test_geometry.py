#!/usr/bin/env python3

"""Tests for confflow.calc.geometry — parse_last_geometry and check_termination."""

from __future__ import annotations

from confflow.calc.geometry import check_termination, parse_last_geometry


class TestParseLastGeometry:
    """Tests for parse_last_geometry."""

    def test_nonexistent_file_returns_none(self, tmp_path):
        assert parse_last_geometry(str(tmp_path / "nope.log"), prog_id=1) is None

    def test_gaussian_standard_orientation(self, tmp_path):
        log = tmp_path / "g16.log"
        log.write_text(
            "some preamble\n"
            " Standard orientation:\n"
            " ---\n"
            " Center  Atomic  Atomic      Coordinates (Angstroms)\n"
            " Number  Number  Type        X            Y            Z\n"
            " ---\n"
            "   1       6       0      0.000000     0.000000     0.000000\n"
            "   2       1       0      1.089000     0.000000     0.000000\n"
            " ---\n"
        )
        coords = parse_last_geometry(str(log), prog_id=1)
        assert coords is not None
        assert len(coords) == 2
        assert coords[0].startswith("C")
        assert coords[1].startswith("H")

    def test_gaussian_uses_last_block(self, tmp_path):
        log = tmp_path / "g16.log"
        block = (
            " Standard orientation:\n"
            " ---\n"
            " Center  Atomic  Atomic      Coordinates (Angstroms)\n"
            " Number  Number  Type        X            Y            Z\n"
            " ---\n"
            "   1       6       0      {x}     0.000000     0.000000\n"
            " ---\n"
        )
        log.write_text(block.format(x="1.000000") + "\n" + block.format(x="2.000000"))
        coords = parse_last_geometry(str(log), prog_id=1)
        assert coords is not None
        # Should be from the *last* block
        assert "2.000000" in coords[0]

    def test_orca_xyz_companion(self, tmp_path):
        log = tmp_path / "orca.out"
        log.write_text("dummy orca log")
        xyz = tmp_path / "orca.xyz"
        xyz.write_text("2\ncomment\nC  0.0  0.0  0.0\nH  1.0  0.0  0.0\n")
        coords = parse_last_geometry(str(log), prog_id=2)
        assert coords is not None
        assert len(coords) == 2

    def test_orca_log_fallback(self, tmp_path):
        log = tmp_path / "orca.out"
        log.write_text(
            "CARTESIAN COORDINATES (ANGSTROEM)\n"
            "-----------------------------------\n"
            "C     0.000000     0.000000     0.000000\n"
            "H     1.089000     0.000000     0.000000\n"
            "\n"
            "other stuff\n"
        )
        coords = parse_last_geometry(str(log), prog_id=2)
        assert coords is not None
        assert len(coords) == 2

    def test_empty_log_returns_none(self, tmp_path):
        log = tmp_path / "empty.log"
        log.write_text("no geometry here\n")
        assert parse_last_geometry(str(log), prog_id=1) is None


class TestCheckTermination:
    """Tests for check_termination."""

    def test_nonexistent_file_returns_false(self, tmp_path):
        assert check_termination(str(tmp_path / "missing.log"), "gaussian") is False

    def test_gaussian_normal_termination(self, tmp_path):
        log = tmp_path / "g16.log"
        log.write_text("lots of output\n Normal termination of Gaussian 16.\n")
        assert check_termination(str(log), "gaussian") is True

    def test_gaussian_abnormal_termination(self, tmp_path):
        log = tmp_path / "g16.log"
        log.write_text("Error termination of Gaussian 16.\n")
        assert check_termination(str(log), "gaussian") is False

    def test_orca_normal_termination(self, tmp_path):
        log = tmp_path / "orca.out"
        log.write_text("lots of output\n****ORCA TERMINATED NORMALLY****\n")
        assert check_termination(str(log), "orca") is True

    def test_orca_abnormal_termination(self, tmp_path):
        log = tmp_path / "orca.out"
        log.write_text("ORCA aborted\n")
        assert check_termination(str(log), "orca") is False
