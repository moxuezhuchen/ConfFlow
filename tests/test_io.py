#!/usr/bin/env python3

"""Tests for core.io module."""

from __future__ import annotations

from unittest.mock import patch


class TestIO:
    """Tests for core.io module."""

    def test_parse_comment_metadata(self):
        """Test comment line metadata parsing."""
        from confflow.core.io import parse_comment_metadata

        meta = parse_comment_metadata("Rank=1 | E=-1.234 | G_corr=0.123")
        assert meta["Rank"] == 1.0
        assert meta["E"] == -1.234
        assert meta["G_corr"] == 0.123

        meta = parse_comment_metadata("E=-0.5 TSBond=1.89")
        assert meta["E"] == -0.5
        assert meta["TSBond"] == 1.89

        meta = parse_comment_metadata("")
        assert meta == {}

        meta = parse_comment_metadata("Status=success")
        assert meta["Status"] == "success"

        meta = parse_comment_metadata("CID=000123")
        assert meta["CID"] == "000123"

    def test_read_xyz_file(self, tmp_path):
        """Test XYZ file reading."""
        from confflow.core.io import iter_xyz_frames, read_xyz_file

        xyz = tmp_path / "test.xyz"
        xyz.write_text(
            "3\n"
            "E=-1.5 | Rank=1\n"
            "H  0.0 0.0 0.0\n"
            "C  1.0 0.0 0.0\n"
            "O  2.0 0.0 0.0\n"
            "3\n"
            "E=-1.2 | Rank=2\n"
            "H  0.0 0.0 0.1\n"
            "C  1.0 0.0 0.1\n"
            "O  2.0 0.0 0.1\n",
            encoding="utf-8",
        )

        conformers = read_xyz_file(str(xyz))
        assert len(conformers) == 2
        assert conformers[0]["natoms"] == 3
        assert conformers[0]["atoms"] == ["H", "C", "O"]
        assert conformers[0]["metadata"]["E"] == -1.5
        assert conformers[0]["metadata"]["Rank"] == 1.0
        assert conformers[1]["metadata"]["E"] == -1.2

        streamed = list(iter_xyz_frames(str(xyz)))
        assert len(streamed) == 2
        assert streamed[1]["comment"] == "E=-1.2 | Rank=2"

    def test_read_xyz_file_safe_returns_empty_list_on_supported_errors(self):
        """read_xyz_file_safe should gracefully degrade on parse/read errors."""
        from confflow.core.io import read_xyz_file_safe

        with patch("confflow.core.io.read_xyz_file", side_effect=ValueError("bad xyz")):
            assert read_xyz_file_safe("broken.xyz") == []

    def test_write_xyz_file(self, tmp_path):
        """Test XYZ file writing."""
        from confflow.core.io import read_xyz_file, write_xyz_file

        conformers = [
            {
                "natoms": 2,
                "comment": "Test molecule | E=-0.5",
                "atoms": ["H", "C"],
                "coords": [[0.0, 0.0, 0.0], [1.5, 0.0, 0.0]],
            }
        ]

        out = tmp_path / "out.xyz"
        write_xyz_file(str(out), conformers)
        result = read_xyz_file(str(out))
        assert len(result) == 1
        assert result[0]["atoms"] == ["H", "C"]
        assert result[0]["metadata"]["E"] == -0.5

    def test_ensure_conformer_cids_skips_existing_ids_and_preserves_comment_ids(self):
        """CID backfill should avoid duplicates and reuse IDs already present in comments."""
        from confflow.core.io import ensure_conformer_cids

        conformers = [
            {"comment": "CID=A000001", "metadata": {"CID": "A000001"}},
            {"comment": "CID=A000003", "metadata": {}},
            {"comment": "", "metadata": {}},
        ]

        ensure_conformer_cids(conformers, prefix="A")

        assert conformers[0]["metadata"]["CID"] == "A000001"
        assert conformers[1]["metadata"]["CID"] == "A000003"
        assert conformers[2]["metadata"]["CID"] == "A000002"
        assert "CID=A000002" in conformers[2]["comment"]

    def test_ensure_conformer_cids_replaces_legacy_numeric_cids(self):
        """Legacy numeric CID tokens should be discarded and replaced with new-format IDs."""
        from confflow.core.io import ensure_conformer_cids

        conformers = [
            {"comment": "CID=1", "metadata": {}},
            {"comment": "CID=2.0", "metadata": {"CID": "2.0"}},
            {"comment": "", "metadata": {}},
        ]

        ensure_conformer_cids(conformers, prefix="A")

        assert conformers[0]["metadata"]["CID"] == "A000001"
        assert conformers[1]["metadata"]["CID"] == "A000002"
        assert conformers[2]["metadata"]["CID"] == "A000003"
        assert "CID=A000001" in conformers[0]["comment"]
        assert "CID=A000002" in conformers[1]["comment"]

    def test_ensure_xyz_cids_rewrites_when_later_frames_are_missing(self, tmp_path):
        """All frames should receive a CID, not just the first frame."""
        from confflow.core.io import ensure_xyz_cids, read_xyz_file, write_xyz_file

        xyz = tmp_path / "multi.xyz"
        write_xyz_file(
            str(xyz),
            [
                {
                    "natoms": 1,
                    "comment": "CID=A000001",
                    "atoms": ["H"],
                    "coords": [[0.0, 0.0, 0.0]],
                    "metadata": {"CID": "A000001"},
                },
                {
                    "natoms": 1,
                    "comment": "",
                    "atoms": ["H"],
                    "coords": [[1.0, 0.0, 0.0]],
                    "metadata": {},
                },
            ],
        )

        ensure_xyz_cids(str(xyz), prefix="A")

        reread = read_xyz_file(str(xyz), parse_metadata=True)
        assert reread[0]["metadata"]["CID"] == "A000001"
        assert reread[1]["metadata"]["CID"] == "A000002"
        assert "CID=A000002" in reread[1]["comment"]

    def test_calculate_bond_length(self):
        """Test bond length calculation."""
        from confflow.core.io import calculate_bond_length

        coords_lines = [
            "H 0.0 0.0 0.0",
            "C 1.5 0.0 0.0",
            "O 2.5 0.0 0.0",
        ]

        length = calculate_bond_length(coords_lines, 1, 2)
        assert abs(length - 1.5) < 0.001

        length = calculate_bond_length(coords_lines, 2, 3)
        assert abs(length - 1.0) < 0.001

        assert calculate_bond_length(coords_lines, 0, 1) is None
        assert calculate_bond_length(coords_lines, 1, 5) is None
