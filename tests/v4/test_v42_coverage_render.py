#!/usr/bin/env python3

"""V4-2 branch coverage: program rendering and parsing pure functions.

Direct unit tests over the Gaussian/ORCA render/parse helpers with crafted
inputs cover vocabulary validation, formatting edges, and parser commit
rules without launching anything.
"""

from __future__ import annotations

import pytest

from confflow.programs.gaussian import parsing as gparse
from confflow.programs.gaussian import rendering as grender
from confflow.programs.orca import parsing as oparse
from confflow.programs.orca import rendering as orender


class TestOrcaRendering:
    """ORCA block, maxcore, keyword, and job-name edges."""

    def test_format_blocks_mapping_and_scalars(self) -> None:
        assert orender.format_orca_blocks({}) == ""
        assert orender.format_orca_blocks("") == ""
        assert orender.format_orca_blocks("   ") == ""
        text = orender.format_orca_blocks("%pal nprocs 4 end")
        assert text.endswith("\n")
        rendered = orender.format_orca_blocks(
            {"pal": {"nprocs": 4}, "scf": {"maxiter": 200}, "flag": True}
        )
        assert "nprocs 4" in rendered
        assert "true" in rendered
        listed = orender.format_orca_blocks({"geom": {"Constraints": ["{ C 0 C }"]}})
        assert "{ C 0 C }" in listed

    def test_format_blocks_string_lines(self) -> None:
        assert "x" in orender.format_orca_blocks("  x\n  y  ")
        assert orender.format_orca_blocks({"k": "v1\nv2"}) != ""

    def test_format_blocks_other_scalars(self) -> None:
        assert "7" in orender.format_orca_blocks({"k": 7})
        assert "3.5" in orender.format_orca_blocks({"k": 3.5})
        assert orender.format_orca_blocks({"k": None}) != ""

    def test_constraint_block(self) -> None:
        assert orender.orca_constraint_block([]) == ""
        assert orender.orca_constraint_block(()) == ""
        block = orender.orca_constraint_block([1, 3])
        assert "{ C 0 C }" in block
        assert "{ C 2 C }" in block

    def test_sanitize_job_name(self) -> None:
        assert orender.sanitize_job_name("s_opt:s0") == "s_opt_s0"
        assert orender.sanitize_job_name("...") == "job"
        assert orender.sanitize_job_name("", fallback="...") == "job"
        assert len(orender.sanitize_job_name("x" * 200)) == 128

    def test_resolve_blocks(self) -> None:
        assert orender.resolve_blocks_text({"blocks": None}) == ""
        assert orender.resolve_blocks_text({"blocks": "%pal nprocs 2 end"}) != ""
        assert orender.resolve_blocks_text({"blocks": {"pal": {"nprocs": 2}}}) != ""
        with pytest.raises(ValueError, match="native_input_error"):
            orender.resolve_blocks_text({"blocks": 123})

    def test_compute_maxcore_mb(self) -> None:
        assert orender.compute_maxcore_mb(8 * 1024**3, 4) == 2000
        assert orender.compute_maxcore_mb(100 * 1024**2, 4) == 100

    def test_resolve_maxcore(self) -> None:
        assert orender.resolve_maxcore({"maxcore": "1500"}, memory_bytes=None, cores=None) == "1500"
        assert orender.resolve_maxcore({"maxcore": 1500.0}, memory_bytes=None, cores=None) == "1500"
        with pytest.raises(ValueError, match="native_input_error"):
            orender.resolve_maxcore({"maxcore": "bogus"}, memory_bytes=None, cores=None)
        with pytest.raises(ValueError, match="native_input_error"):
            orender.resolve_maxcore({"maxcore": "1.5"}, memory_bytes=None, cores=None)
        with pytest.raises(ValueError, match="native_input_error"):
            orender.resolve_maxcore({}, memory_bytes=None, cores=None)
        with pytest.raises(ValueError, match="native_input_error"):
            orender.resolve_maxcore({}, memory_bytes=1024**3, cores=0)
        assert orender.resolve_maxcore({}, memory_bytes=8 * 1024**3, cores=4) == "2000"

    def test_resolve_keyword(self) -> None:
        assert orender.resolve_keyword({"keyword": "B3LYP Opt"}) == "B3LYP Opt"
        with pytest.raises(ValueError, match="native_input_error"):
            orender.resolve_keyword({})
        with pytest.raises(ValueError, match="native_input_error"):
            orender.resolve_keyword({"keyword": "  "})

    def test_format_coord_lines(self) -> None:
        text = orender.format_coord_lines(["O"], [(0.0, 0.0, 0.0)])
        assert text.startswith("O")

    def test_render_orca_input(self) -> None:
        text = orender.render_orca_input(
            keyword="B3LYP Opt",
            cores=2,
            maxcore="1000",
            blocks_text="",
            freeze=None,
            charge=0,
            multiplicity=1,
            coords_text="O 0 0 0",
        )
        assert text.startswith("! B3LYP Opt")
        assert "%pal nprocs 2 end" in text
        assert "* xyz 0 1" in text
        frozen = orender.render_orca_input(
            keyword="B3LYP Opt",
            cores=2,
            maxcore="1000",
            blocks_text="",
            freeze=[1],
            charge=0,
            multiplicity=1,
            coords_text="O 0 0 0",
        )
        assert "Constraints" in frozen


class TestOrcaParsing:
    """ORCA parser commit rules and edge inputs."""

    def test_read_log_missing(self, tmp_path) -> None:
        assert oparse.read_log_text(str(tmp_path / "ghost.out")) is None

    def test_termination(self, tmp_path) -> None:
        good = tmp_path / "a.out"
        good.write_text("x\n****ORCA TERMINATED NORMALLY****\n")
        assert oparse.termination_reached(good.read_text()) is True
        bad = tmp_path / "b.out"
        bad.write_text("x\n")
        assert oparse.termination_reached(bad.read_text()) is False

    def test_parse_energies(self) -> None:
        energies = oparse.parse_energies("FINAL SINGLE POINT ENERGY      -76.4\n")
        assert energies["electronic"] == pytest.approx(-76.4)
        assert oparse.parse_energies("nothing here\n") == {}
        both = oparse.parse_energies(
            "FINAL SINGLE POINT ENERGY -1.0\n"
            "G-E(el) ... 0.1 Eh\n"
            "Final Gibbs free energy ... -0.9 Eh\n"
            "FINAL SINGLE POINT ENERGY -2.0\n"
        )
        assert both["electronic"] == pytest.approx(-2.0)

    def test_parse_frequencies_commit(self) -> None:
        assert oparse.parse_frequencies("no freqs\n") == []
        uncommitted = "VIBRATIONAL FREQUENCIES\n 1: 100.0 cm-1\n"
        assert oparse.parse_frequencies(uncommitted) == []
        committed = uncommitted + "NORMAL MODES\n"
        assert oparse.parse_frequencies(committed) == [100.0]
        assert oparse.parse_frequencies(committed.replace("NORMAL MODES", "IR SPECTRUM")) == [100.0]

    def test_noise_floor_and_modes(self) -> None:
        assert oparse.analyze_vibrational_frequencies([5.0, -5.0]) == (0, None)
        assert oparse.true_vibrational_modes([5.0, 100.0, -50.0]) == (100.0, -50.0)

    def test_cartesian_blocks(self) -> None:
        text = (
            "CARTESIAN COORDINATES (ANGSTROEM)\n"
            "-------------------------------\n"
            "  O   0.0 0.0 0.0\n"
            "  H   1.0 0.0 0.0\n"
            "\n"
        )
        parsed = oparse.parse_cartesian_blocks(text)
        assert parsed is not None
        assert parsed[0] == ("O", "H")
        assert oparse.parse_cartesian_blocks("nothing\n") is None

    def test_xyz_companion(self, tmp_path) -> None:
        xyz = tmp_path / "a.xyz"
        xyz.write_text("2\ncomment\nO 0 0 0\nH 1 0 0\n")
        parsed = oparse.parse_xyz_companion(str(xyz))
        assert parsed is not None
        assert len(parsed[0]) == 2
        assert oparse.parse_xyz_companion(str(tmp_path / "ghost.xyz")) is None
        bad = tmp_path / "bad.xyz"
        bad.write_text("not a number\n")
        assert oparse.parse_xyz_companion(str(bad)) is None

    def test_error_details(self, tmp_path) -> None:
        log = tmp_path / "a.out"
        log.write_text("ORCA finished by error\n")
        assert "Abnormal" in oparse.orca_error_details(log.read_text())
        assert oparse.orca_error_details("") == ""


class TestGaussianRendering:
    """Gaussian vocabulary, keyword, freeze, and section edges."""

    def test_check_native_keys(self) -> None:
        grender.check_native_keys({})
        with pytest.raises(ValueError, match="native_input_error"):
            grender.check_native_keys({"blocks": {}})

    def test_normalize_keyword(self) -> None:
        assert grender.normalize_gaussian_keyword("#p B3LYP") == "B3LYP"
        assert grender.normalize_gaussian_keyword("opt") == "opt"
        assert grender.normalize_gaussian_keyword("") == ""

    def test_format_keyword_line(self) -> None:
        assert grender.format_keyword_line("B3LYP opt") == "# B3LYP opt"
        assert grender.format_keyword_line("#p B3LYP opt") == "#p B3LYP opt"

    def test_format_memory(self) -> None:
        with pytest.raises(ValueError, match="native_input_error"):
            grender.format_memory_gb(0)
        with pytest.raises(ValueError, match="native_input_error"):
            grender.format_memory_gb(None)
        with pytest.raises(ValueError, match="native_input_error"):
            grender.format_memory_gb("16GB")
        assert grender.format_memory_gb(16 * 1024**3) == "16GB"
        assert grender.format_memory_gb(1536 * 1024**2) == "1GB"

    def test_resolve_counts(self) -> None:
        assert grender.resolve_core_count(4) == 4
        with pytest.raises(ValueError, match="native_input_error"):
            grender.resolve_core_count(0)
        with pytest.raises(ValueError, match="native_input_error"):
            grender.resolve_core_count(None)
        assert grender.resolve_charge(0) == 0
        with pytest.raises(ValueError, match="native_input_error"):
            grender.resolve_charge(None)
        with pytest.raises(ValueError, match="native_input_error"):
            grender.resolve_charge("x")
        assert grender.resolve_multiplicity(1) == 1
        with pytest.raises(ValueError, match="native_input_error"):
            grender.resolve_multiplicity(None)
        with pytest.raises(ValueError, match="native_input_error"):
            grender.resolve_multiplicity("x")

    def test_coerce_sections(self) -> None:
        assert grender.coerce_section_lines(None, "x") == []
        assert grender.coerce_section_lines("a\nb", "x") == ["a", "b"]
        assert grender.coerce_section_lines(["a", "", "b"], "x") == ["a", "b"]

    def test_resolve_extra_section(self) -> None:
        assert grender.resolve_extra_section({}) == ""
        text = grender.resolve_extra_section(
            {"extra_sections": "Extra=1", "modredundant": "B 1 2 F"}
        )
        assert "Extra=1" in text
        assert "B 1 2 F" in text

    def test_apply_freeze(self) -> None:
        atoms = ["O", "H"]
        coords = [(0.0, 0.0, 0.0), (1.0, 0.0, 0.0)]
        assert grender.apply_freeze(atoms, coords, []) == [
            "O      0.000000     0.000000     0.000000",
            "H      1.000000     0.000000     0.000000",
        ]
        frozen = grender.apply_freeze(atoms, coords, [2])
        assert " -1 " in frozen[1]
        assert " 0 " in frozen[0]

    def test_format_coordinates(self) -> None:
        formatted = grender.format_coordinates(["O"], [(0.0, 0.0, 0.0)])
        assert formatted[0].startswith("O")

    def test_resolve_link0(self) -> None:
        assert (
            grender.resolve_link0_lines(
                job="job", write_chk=False, oldchk_name=None, user_link0=None
            )
            == []
        )
        lines = grender.resolve_link0_lines(
            job="job", write_chk=True, oldchk_name=None, user_link0=["%Mem=4GB"]
        )
        assert any(line.startswith("%Chk=") for line in lines)
        assert "%Mem=4GB" in lines
        old = grender.resolve_link0_lines(
            job="job", write_chk=True, oldchk_name="prev.old.chk", user_link0=None
        )
        assert any(line.startswith("%OldChk=") for line in old)

    def test_resolve_write_chk(self) -> None:
        assert grender.resolve_write_chk({}) is True
        assert grender.resolve_write_chk({"write_chk": False}) is False
        assert grender.resolve_write_chk({"write_chk": "no"}) is False
        assert grender.resolve_write_chk({"write_chk": "yes"}) is True

    def test_resolve_keyword(self) -> None:
        assert grender.resolve_keyword({"keyword": "opt"}) == "opt"
        with pytest.raises(ValueError, match="native_input_error"):
            grender.resolve_keyword({})
        with pytest.raises(ValueError, match="native_input_error"):
            grender.resolve_keyword({"keyword": "   "})

    def test_render_gaussian_input(self) -> None:
        text = grender.render_gaussian_input(
            link0_lines=[],
            cores=2,
            memory="2GB",
            keyword_line="# opt",
            job="job",
            charge=0,
            multiplicity=1,
            coord_lines=["O 0 0 0"],
            extra_section="",
        )
        assert "%nprocshared=2" in text
        assert "0 1" in text

    def test_scan_keyword_from_ts(self) -> None:
        assert grender.scan_keyword_from_ts("") is None
        assert grender.scan_keyword_from_ts(None) is None
        assert grender.scan_keyword_from_ts("opt=(ts)") == "opt"


class TestGaussianParsing:
    """Gaussian parser energy, frequency, geometry, and termination edges."""

    def test_read_log_missing(self, tmp_path) -> None:
        assert gparse.read_log_text(str(tmp_path / "ghost.log")) is None

    def test_termination(self, tmp_path) -> None:
        good = tmp_path / "a.log"
        good.write_text(" Normal termination of Gaussian 16.\n")
        assert gparse.termination_reached(good.read_text()) is True
        assert gparse.check_termination(str(good)) is True
        bad = tmp_path / "b.log"
        bad.write_text("Error termination\n")
        assert gparse.termination_reached(bad.read_text()) is False
        assert gparse.check_termination(str(tmp_path / "ghost.log")) is False

    def test_parse_energies(self) -> None:
        energies, _sources = gparse.parse_energies(" SCF Done:  E(RB3LYP) =  -76.4     A.U.\n")
        assert energies["electronic"] == pytest.approx(-76.4)
        d_exponent, _ = gparse.parse_energies(" SCF Done:  E(RB3LYP) =  -0.764D+02     A.U.\n")
        assert d_exponent["electronic"] == pytest.approx(-76.4)
        assert gparse.parse_energies("nothing\n")[0] == {}
        thermo, _ = gparse.parse_energies(
            " SCF Done:  E(RB3LYP) =  -76.4     A.U.\n"
            " Sum of electronic and thermal Free Energies=                 -76.3\n"
            " Thermal correction to Gibbs Free Energy=              0.1\n"
        )
        assert thermo["gibbs"] == pytest.approx(-76.3)
        assert thermo["gibbs_correction"] == pytest.approx(0.1)
        archive, _ = gparse.parse_energies("\\HF=-76.5\\Gibbs=-76.35\\@\n")
        assert archive["electronic"] == pytest.approx(-76.5)

    def test_parse_frequencies_commit(self) -> None:
        assert gparse.parse_frequencies("nothing\n") == []
        uncommitted = " Harmonic frequencies\n Frequencies --  100.0  200.0\n"
        assert gparse.parse_frequencies(uncommitted) == []
        committed = uncommitted + " Zero-point correction= 0.5\n"
        assert gparse.parse_frequencies(committed) == [100.0, 200.0]

    def test_noise_floor(self) -> None:
        assert gparse.analyze_vibrational_frequencies([5.0, -200.0]) == (1, -200.0)
        assert gparse.true_vibrational_modes([5.0, 100.0]) == (100.0,)

    def test_symbol_table(self) -> None:
        assert gparse.symbol_for_atomic_number(1) == "H"
        assert gparse.symbol_for_atomic_number(8) == "O"
        assert gparse.symbol_for_atomic_number(0) is None
        assert gparse.symbol_for_atomic_number(999) is None

    def test_parse_final_geometry(self, tmp_path) -> None:
        assert gparse.parse_final_geometry("nothing\n") is None
        log = tmp_path / "a.log"
        log.write_text(
            " Standard orientation:\n"
            " ---------------------------------------------------------------------\n"
            " Center     Atomic      Atomic             Coordinates (Angstroms)\n"
            " Number     Number       Type             X           Y           Z\n"
            " ---------------------------------------------------------------------\n"
            "      1          8           0        0.000000    0.000000    0.000000\n"
            "      2          1           0        0.000000    0.000000    1.000000\n"
            " ---------------------------------------------------------------------\n"
        )
        parsed = gparse.parse_final_geometry(log.read_text())
        assert parsed is not None
        assert list(parsed[0]) == ["O", "H"]
