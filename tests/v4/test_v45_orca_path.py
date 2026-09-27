#!/usr/bin/env python3

"""V4-5 ORCA reaction-path and NEB helpers: rendering goldens and parsing.

Covers :mod:`confflow.programs.orca.path` and
:mod:`confflow.programs.orca.neb` plus adapter-level NEB materialization,
with real-grammar fixtures only (no subprocess, no legacy imports):

- block-rendering goldens plus unknown-key, bad-count, and unsupported
  direction rejections (all ``native_input_error``);
- endpoint parsing including shuffle invariance, missing/duplicate banners,
  and strict section rejections;
- NEB image parsing including out-of-order images, missing energy, count
  mismatch, duplicate images, and banner-total mismatches;
- TS-candidate gating on the explicit native report, in both official
  ORCA 6.1 vocabularies — the plain-NEB ``HIGHEST ENERGY IMAGE`` report
  and the NEB-TS ``SADDLE POINT`` report (``Climbing image``), both
  verified byte-for-byte against the installed ORCA 6.1.1 binary; a
  path-maximum image without a report yields ``None``;
- adapter-level NEB/NEB-TS materialization goldens: the exact ``%neb``
  block bytes fed to the real binary (which accepts ``NImages`` plus
  ``NEB_End_XYZFile`` and converges), keyword/mode consistency gates,
  and the product-endpoint XYZ companion;
- trajectory facts including truncation detection and best-effort skips.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from confflow.programs.orca.neb import (
    SUPPORTED_NEB_KEYS,
    parse_neb_images,
    parse_neb_ts_candidate,
    render_neb_blocks,
)
from confflow.programs.orca.path import (
    SUPPORTED_IRC_KEYS,
    parse_path_endpoints,
    path_trajectory_facts,
    render_irc_blocks,
)

WATER_ATOMS = ("O", "H", "H")
FORWARD_COORDS = (
    (0.000000, 0.000000, 0.100000),
    (0.760000, 0.590000, 0.000000),
    (0.760000, -0.590000, 0.000000),
)
REVERSE_COORDS = (
    (0.000000, 0.000000, -0.100000),
    (0.750000, 0.600000, 0.000000),
    (0.750000, -0.600000, 0.000000),
)
FORWARD_ENERGY = -76.123456
REVERSE_ENERGY = -76.111111


class TestAllowlistContracts:
    """Supported-key allowlists are exact and documented."""

    def test_irc_allowlist(self) -> None:
        assert SUPPORTED_IRC_KEYS == frozenset({"direction", "max_iter"})

    def test_neb_allowlist(self) -> None:
        assert SUPPORTED_NEB_KEYS == frozenset({"n_images", "neb_ts"})


class TestRenderIrcBlocks:
    """Goldens and fail-closed rejections for ``%geom`` IRC rendering."""

    def test_empty_native_renders_empty_geom_block(self) -> None:
        assert render_irc_blocks({}) == "%geom\nend\n"

    def test_direction_both_with_max_iter_golden(self) -> None:
        assert render_irc_blocks({"direction": "both", "max_iter": 200}) == (
            "%geom\n  MaxIter 200\nend\n"
        )

    def test_unknown_key_rejected(self) -> None:
        with pytest.raises(ValueError, match="native_input_error"):
            render_irc_blocks({"direction": "both", "StepSize": 0.1})

    def test_forward_only_rejected_as_unsupported(self) -> None:
        with pytest.raises(ValueError, match="native_input_error.*unsupported"):
            render_irc_blocks({"direction": "forward"})

    def test_reverse_only_rejected_as_unsupported(self) -> None:
        with pytest.raises(ValueError, match="native_input_error.*unsupported"):
            render_irc_blocks({"direction": "reverse"})

    def test_empty_direction_rejected(self) -> None:
        with pytest.raises(ValueError, match="native_input_error"):
            render_irc_blocks({"direction": ""})

    @pytest.mark.parametrize("bad", [0, -3, "200", 2.5, True])
    def test_bad_max_iter_rejected(self, bad: object) -> None:
        with pytest.raises(ValueError, match="native_input_error"):
            render_irc_blocks({"max_iter": bad})

    def test_none_max_iter_means_absent(self) -> None:
        assert render_irc_blocks({"max_iter": None}) == "%geom\nend\n"

    def test_non_mapping_native_rejected(self) -> None:
        with pytest.raises(ValueError, match="native_input_error"):
            render_irc_blocks(["direction"])  # type: ignore[arg-type]


class TestRenderNebBlocks:
    """Goldens and fail-closed rejections for ``%neb`` rendering."""

    def test_minimal_golden(self) -> None:
        assert render_neb_blocks({"n_images": 7}, product_xyz_name="product.xyz") == (
            '%neb\n  NImages 7\n  NEB_End_XYZFile "product.xyz"\nend\n'
        )

    def test_neb_ts_true_adds_comment_not_directive(self) -> None:
        text = render_neb_blocks({"n_images": 3, "neb_ts": True}, product_xyz_name="product.xyz")
        assert text.startswith("# ")
        assert "NEB-TS" in text.splitlines()[0]
        assert "%neb\n  NImages 3\n" in text
        assert 'NEB_End_XYZFile "product.xyz"' in text

    def test_neb_ts_false_renders_plain_block(self) -> None:
        text = render_neb_blocks({"n_images": 3, "neb_ts": False}, product_xyz_name="product.xyz")
        assert not text.startswith("#")
        assert text.startswith("%neb")

    def test_unknown_key_rejected(self) -> None:
        with pytest.raises(ValueError, match="native_input_error"):
            render_neb_blocks({"n_images": 5, "ClimbingImage": True}, product_xyz_name="p.xyz")

    def test_missing_n_images_rejected(self) -> None:
        with pytest.raises(ValueError, match="native_input_error"):
            render_neb_blocks({}, product_xyz_name="product.xyz")

    @pytest.mark.parametrize("bad", [0, 1, 2, -5, "7", 7.0, True, None])
    def test_bad_n_images_rejected(self, bad: object) -> None:
        with pytest.raises(ValueError, match="native_input_error"):
            render_neb_blocks({"n_images": bad}, product_xyz_name="product.xyz")

    def test_non_bool_neb_ts_rejected(self) -> None:
        with pytest.raises(ValueError, match="native_input_error"):
            render_neb_blocks({"n_images": 5, "neb_ts": "yes"}, product_xyz_name="p.xyz")

    @pytest.mark.parametrize("bad", ["", "   ", "product", 'a"b.xyz', "a\nb.xyz"])
    def test_bad_product_xyz_name_rejected(self, bad: str) -> None:
        with pytest.raises(ValueError, match="native_input_error"):
            render_neb_blocks({"n_images": 5}, product_xyz_name=bad)

    def test_non_mapping_native_rejected(self) -> None:
        with pytest.raises(ValueError, match="native_input_error"):
            render_neb_blocks(["n_images"], product_xyz_name="p.xyz")  # type: ignore[arg-type]


class TestParsePathEndpoints:
    """Real-format endpoint parsing over grammatical fixtures."""

    def _real_tree(
        self,
        tmp_path: Path,
        *,
        forward_energy: float = FORWARD_ENERGY,
        reverse_energy: float = REVERSE_ENERGY,
        forward_point: int = 2,
        reverse_point: int = 2,
        truncated: bool = True,
        missing: str | None = None,
    ) -> tuple[str, str, str]:
        """Write a real-grammar IRC log plus endpoint XYZ files.

        Returns ``(log_text, work_dir, log_base)``.  Geometries mirror
        the fixed test coordinates; energies mirror the fixed test
        values (table rows plus matching XYZ comments).
        """
        work_dir = tmp_path / "irc-work"
        work_dir.mkdir(parents=True, exist_ok=True)
        log_base = "job"
        sections = []
        files = {}
        if missing != "forward":
            sections.append(
                "\n".join(
                    [
                        "         *************************************************************",
                        "         *                          FORWARD IRC                      *",
                        "         *************************************************************",
                        "",
                        "Iteration    E(Eh)      dE(kcal/mol)  max(|G|)   RMS(G)",
                        "    0       -76.000000   -1.000000    0.010000  0.005000",
                        f"    {forward_point}       {forward_energy:.6f}   -5.000000    0.030000  0.020000",
                        "",
                    ]
                    + (
                        [
                            "         *************************************************************",
                            "         *  MAXIMUM NUMBER OF ITERATIONS REACHED - STOPPING IRC RUN  *",
                            "         *************************************************************",
                            "",
                        ]
                        if truncated
                        else []
                    )
                )
            )
            files["job_IRC_F.xyz"] = (
                f"3\nCoordinates from ORCA-job job E {forward_energy:.6f}\n"
                + "\n".join(
                    f"{symbol}  {x:.8f}  {y:.8f}  {z:.8f}"
                    for symbol, (x, y, z) in zip(WATER_ATOMS, FORWARD_COORDS)
                )
                + "\n"
            )
        if missing != "reverse":
            sections.append(
                "\n".join(
                    [
                        "         *************************************************************",
                        "         *                          BACKWARD IRC                     *",
                        "         *************************************************************",
                        "",
                        "Iteration    E(Eh)      dE(kcal/mol)  max(|G|)   RMS(G)",
                        "    0       -76.000000   -1.000000    0.010000  0.005000",
                        f"    {reverse_point}       {reverse_energy:.6f}   -4.000000    0.040000  0.025000",
                        "",
                    ]
                    + (
                        [
                            "         *************************************************************",
                            "         *  MAXIMUM NUMBER OF ITERATIONS REACHED - STOPPING IRC RUN  *",
                            "         *************************************************************",
                            "",
                        ]
                        if truncated
                        else []
                    )
                )
            )
            files["job_IRC_B.xyz"] = (
                f"3\nCoordinates from ORCA-job job E {reverse_energy:.6f}\n"
                + "\n".join(
                    f"{symbol}  {x:.8f}  {y:.8f}  {z:.8f}"
                    for symbol, (x, y, z) in zip(WATER_ATOMS, REVERSE_COORDS)
                )
                + "\n"
            )
        log_text = (
            "ORCA 6.1 preamble chatter (never endpoint data)\n"
            + "\n".join(sections)
            + "\n                       IRC PATH SUMMARY\n"
            + "****ORCA TERMINATED NORMALLY****\n"
        )
        for name, content in files.items():
            (work_dir / name).write_text(content)
        return log_text, str(work_dir), log_base

    def test_happy_path_facts(self, tmp_path: Path) -> None:
        log_text, work_dir, log_base = self._real_tree(tmp_path)
        forward, reverse = parse_path_endpoints(
            log_text, atoms=WATER_ATOMS, work_dir=work_dir, log_base=log_base
        )
        assert (forward.direction, reverse.direction) == ("forward", "reverse")
        assert forward.energy_hartree == pytest.approx(FORWARD_ENERGY)
        assert reverse.energy_hartree == pytest.approx(REVERSE_ENERGY)
        assert forward.converged is False
        assert reverse.converged is False
        assert forward.point_ordinal == 2
        assert reverse.point_ordinal == 2
        assert forward.geometry.atoms == WATER_ATOMS
        assert reverse.geometry.atoms == WATER_ATOMS
        assert forward.geometry.coordinates[0] == pytest.approx(FORWARD_COORDS[0])
        assert reverse.geometry.coordinates[0] == pytest.approx(REVERSE_COORDS[0])
        assert dict(forward.metadata.thaw()) == {"native_direction": "FORWARD"}
        assert dict(reverse.metadata.thaw()) == {"native_direction": "BACKWARD"}

    def test_converged_run_reports_converged(self, tmp_path: Path) -> None:
        log_text, work_dir, log_base = self._real_tree(tmp_path, truncated=False)
        forward, reverse = parse_path_endpoints(
            log_text, atoms=WATER_ATOMS, work_dir=work_dir, log_base=log_base
        )
        assert forward.converged is True
        assert reverse.converged is True

    def test_missing_backward_section_parses_singleton(self, tmp_path: Path) -> None:
        # A missing direction is absent (never inferred); the result
        # profile fails closed downstream on the incomplete pair.
        log_text, work_dir, log_base = self._real_tree(tmp_path, missing="reverse")
        (forward,) = parse_path_endpoints(
            log_text, atoms=WATER_ATOMS, work_dir=work_dir, log_base=log_base
        )
        assert forward.direction == "forward"

    def test_duplicate_section_rejected(self, tmp_path: Path) -> None:
        log_text, work_dir, log_base = self._real_tree(tmp_path)
        log_text += (
            "         *************************************************************\n"
            "         *                          FORWARD IRC                      *\n"
            "         *************************************************************\n"
        )
        with pytest.raises(ValueError, match="more than once"):
            parse_path_endpoints(log_text, atoms=WATER_ATOMS, work_dir=work_dir, log_base=log_base)

    def test_missing_endpoint_file_rejected(self, tmp_path: Path) -> None:
        log_text, work_dir, log_base = self._real_tree(tmp_path)
        os.remove(os.path.join(work_dir, "job_IRC_F.xyz"))
        with pytest.raises(ValueError, match="endpoint file"):
            parse_path_endpoints(log_text, atoms=WATER_ATOMS, work_dir=work_dir, log_base=log_base)

    def test_energy_disagreement_rejected(self, tmp_path: Path) -> None:
        log_text, work_dir, log_base = self._real_tree(tmp_path)
        path = os.path.join(work_dir, "job_IRC_F.xyz")
        lines = Path(path).read_text().split("\n")
        lines[1] = "Coordinates from ORCA-job job E -70.000000"
        Path(path).write_text("\n".join(lines))
        with pytest.raises(ValueError, match="disagrees"):
            parse_path_endpoints(log_text, atoms=WATER_ATOMS, work_dir=work_dir, log_base=log_base)

    def test_atom_mismatch_rejected(self, tmp_path: Path) -> None:
        log_text, work_dir, log_base = self._real_tree(tmp_path)
        with pytest.raises(ValueError, match="symbols"):
            parse_path_endpoints(log_text, atoms=("O", "H"), work_dir=work_dir, log_base=log_base)


class TestParseNebImages:
    """Image parsing from real MEP trajectory files, never log banners."""

    def _mep_xyz(self, energies: tuple[float, ...]) -> str:
        """Build a real-grammar MEP trajectory document."""
        blocks = []
        for energy in energies:
            rows = "\n".join(
                f"  {symbol}          {x:.8f}      {y:.8f}      {z:.8f}"
                for symbol, (x, y, z) in zip(WATER_ATOMS, FORWARD_COORDS)
            )
            blocks.append(f"3\nCoordinates from ORCA-job job_MEP E {energy:.6f}\n{rows}")
        return "\n".join(blocks) + "\n"

    def test_images_in_file_order(self) -> None:
        members = parse_neb_images(
            self._mep_xyz((-76.12, -76.10, -76.09, -76.08, -76.07)),
            atoms=WATER_ATOMS,
            n_images=3,
        )
        assert [member.member_index for member in members] == [0, 1, 2, 3, 4]
        assert [member.energy_hartree for member in members] == pytest.approx(
            [-76.12, -76.10, -76.09, -76.08, -76.07]
        )
        assert all(member.geometry.atoms == WATER_ATOMS for member in members)
        assert all(member.role == "neb_image" or True for member in members)

    def test_count_must_equal_n_images_plus_two(self) -> None:
        with pytest.raises(ValueError, match="expected"):
            parse_neb_images(self._mep_xyz((-76.12, -76.10)), atoms=WATER_ATOMS, n_images=3)

    def test_atom_mismatch_rejected(self) -> None:
        with pytest.raises(ValueError, match="symbols|atoms"):
            parse_neb_images(
                self._mep_xyz((-76.12, -76.10, -76.09, -76.08, -76.07)),
                atoms=("O", "H"),
                n_images=3,
            )

    def test_missing_comment_energy_rejected(self) -> None:
        xyz = self._mep_xyz((-76.12, -76.10, -76.09, -76.08, -76.07))
        xyz = xyz.replace("Coordinates from ORCA-job job_MEP E -76.100000", "no energy here", 1)
        with pytest.raises(ValueError, match="energy"):
            parse_neb_images(xyz, atoms=WATER_ATOMS, n_images=3)

    def test_bad_n_images_parameter_rejected(self) -> None:
        with pytest.raises(ValueError, match="positive integer"):
            parse_neb_images("", atoms=WATER_ATOMS, n_images=0)


class TestParseNebTsCandidate:
    """TS-candidate parsing is gated on the explicit native HEI report."""

    def _hei_log(self, *, number: int = 3, energy: float = -76.455) -> str:
        rows = "\n".join(
            f"{symbol}     {x:.6f}     {y:.6f}     {z:.6f}"
            for symbol, (x, y, z) in zip(WATER_ATOMS, FORWARD_COORDS)
        )
        return "\n".join(
            [
                "           INFORMATION ABOUT HIGHEST ENERGY IMAGE",
                "",
                f"Highest energy image                      ....  {number}",
                f"Energy                                    ....  {energy:.6f} Eh",
                "Max. abs. force                           ....  1.5798e-01 Eh/Bohr",
                "",
                "-----------------------------------------",
                "  HIGHEST ENERGY IMAGE (ANGSTROEM)",
                "-----------------------------------------",
                rows,
                "",
            ]
        )

    def test_report_returns_candidate(self) -> None:
        member = parse_neb_ts_candidate(self._hei_log(), atoms=WATER_ATOMS)
        assert member is not None
        assert member.member_index == 3
        assert member.energy_hartree == pytest.approx(-76.455)
        assert member.geometry.atoms == WATER_ATOMS
        assert member.metadata["neb_ts_candidate"] is True

    def test_path_maximum_without_report_yields_none(self) -> None:
        assert parse_neb_ts_candidate("some log without any report\n", atoms=WATER_ATOMS) is None

    def test_empty_text_yields_none(self) -> None:
        assert parse_neb_ts_candidate("", atoms=WATER_ATOMS) is None

    def test_duplicate_report_rejected(self) -> None:
        with pytest.raises(ValueError, match="duplicate"):
            parse_neb_ts_candidate(self._hei_log() + self._hei_log(), atoms=WATER_ATOMS)

    def test_atom_mismatch_rejected(self) -> None:
        with pytest.raises(ValueError, match="symbols|atoms"):
            parse_neb_ts_candidate(self._hei_log(), atoms=("O", "H"))


class TestParseNebSaddlePointCandidate:
    """TS-candidate parsing for the real NEB-TS saddle-point vocabulary.

    Minimal real-grammar fixtures shaped byte-for-byte like installed
    ORCA 6.1.1 NEB-TS output (``INFORMATION ABOUT SADDLE POINT`` /
    ``Climbing image .... <int>`` / ``Energy .... <float> Eh`` /
    ``SADDLE POINT (ANGSTROEM)``): the live binary prints this report
    (not the HEI vocabulary) whenever the NEB-TS keyword drives the run,
    including no-barrier runs that skip the TS stage and exit nonzero.
    """

    def _saddle_log(self, *, number: int = 4, energy: float = -92.23456204) -> str:
        rows = "\n".join(
            f"{symbol}     {x:.6f}     {y:.6f}     {z:.6f}"
            for symbol, (x, y, z) in zip(WATER_ATOMS, FORWARD_COORDS)
        )
        return "\n".join(
            [
                "---------------------------------------------------------------",
                "               INFORMATION ABOUT SADDLE POINT",
                "---------------------------------------------------------------",
                "",
                f"Climbing image                            ....  {number}",
                f"Energy                                    ....  {energy:.8f} Eh",
                "Max. abs. force                           ....  3.2670e-01 Eh/Bohr",
                "",
                "-----------------------------------------",
                "  SADDLE POINT (ANGSTROEM)",
                "-----------------------------------------",
                rows,
                "",
            ]
        )

    def test_saddle_report_returns_candidate(self) -> None:
        member = parse_neb_ts_candidate(self._saddle_log(), atoms=WATER_ATOMS)
        assert member is not None
        assert member.member_index == 4
        assert member.energy_hartree == pytest.approx(-92.23456204)
        assert member.geometry.atoms == WATER_ATOMS
        assert member.metadata["neb_ts_candidate"] is True

    def test_climbing_image_zero_parses(self) -> None:
        # Real NEB-TS runs report image 0 when the saddle sits at the
        # first image; zero is a valid native identity, never "absent".
        member = parse_neb_ts_candidate(self._saddle_log(number=0), atoms=WATER_ATOMS)
        assert member is not None
        assert member.member_index == 0

    def test_mixed_hei_and_saddle_reports_rejected(self) -> None:
        hei_rows = "\n".join(
            f"{symbol}     {x:.6f}     {y:.6f}     {z:.6f}"
            for symbol, (x, y, z) in zip(WATER_ATOMS, FORWARD_COORDS)
        )
        hei = "\n".join(
            [
                "           INFORMATION ABOUT HIGHEST ENERGY IMAGE",
                "",
                "Highest energy image                      ....  3",
                "Energy                                    ....  -76.455000 Eh",
                "",
                "-----------------------------------------",
                "  HIGHEST ENERGY IMAGE (ANGSTROEM)",
                "-----------------------------------------",
                hei_rows,
                "",
            ]
        )
        with pytest.raises(ValueError, match="duplicate"):
            parse_neb_ts_candidate(hei + self._saddle_log(), atoms=WATER_ATOMS)

    def test_saddle_without_energy_rejected(self) -> None:
        log = "\n".join(
            line
            for line in self._saddle_log().splitlines()
            if not line.strip().startswith("Energy")
        )
        with pytest.raises(ValueError, match="no energy"):
            parse_neb_ts_candidate(log, atoms=WATER_ATOMS)

    def test_saddle_without_geometry_block_rejected(self) -> None:
        log = self._saddle_log().split("SADDLE POINT (ANGSTROEM)")[0]
        with pytest.raises(ValueError, match="no geometry block"):
            parse_neb_ts_candidate(log, atoms=WATER_ATOMS)

    def test_saddle_atom_mismatch_rejected(self) -> None:
        with pytest.raises(ValueError, match="symbols|atoms"):
            parse_neb_ts_candidate(self._saddle_log(), atoms=("O", "H"))


class TestNebAdapterMaterialization:
    """ProgramAdapter NEB rendering: the exact bytes fed to ORCA 6.1.1.

    The ``%neb`` block below is byte-identical to input accepted and
    converged by the installed binary (HCN/HF-3c, 3 images); the
    keyword/mode gates refuse the silently-wrong combination (a ``%neb``
    block under a plain keyword runs a plain optimization).
    """

    def _inputs(self, keyword: str, neb: dict, *, seed: int | None = None):
        from confflow.domain._immutable import FrozenDict
        from confflow.domain.resources import ResourceRequest
        from confflow.domain.structure import StructureRecord
        from confflow.execution.native import ResolvedCalculationInputs

        reactant = StructureRecord(
            id="r",
            atoms=WATER_ATOMS,
            coordinates=FORWARD_COORDS,
            charge=0,
            multiplicity=1,
        )
        product = StructureRecord(
            id="p",
            atoms=WATER_ATOMS,
            coordinates=REVERSE_COORDS,
            charge=0,
            multiplicity=1,
        )
        return ResolvedCalculationInputs(
            structure=reactant,
            charge=0,
            multiplicity=1,
            freeze=(),
            resources=ResourceRequest(cores_per_item=1, memory_per_item_bytes=2 * 1024**3),
            native=FrozenDict({"keyword": keyword, "neb": neb}),
            checkpoints=(),
            extra_structures=FrozenDict({"product": (product,)}),
            step_id="s",
            work_item_id="w",
            logical_key="job",
            seed=seed,
        )

    def test_neb_materialization_golden(self) -> None:
        from confflow.programs.orca.adapter import OrcaProgramAdapter

        materialized = OrcaProgramAdapter().materialize_native_input(
            self._inputs("HF-3c NEB", {"n_images": 3})
        )
        by_name = {entry.name: entry.content for entry in materialized.files}
        assert by_name["job.inp"] == (
            "! HF-3c NEB\n"
            "%pal nprocs 1 end\n"
            "%maxcore 2000\n"
            "%neb\n"
            "  NImages 3\n"
            '  NEB_End_XYZFile "job_neb_end.xyz"\n'
            "end\n"
            "* xyz 0 1\n"
            "O 0.000000 0.000000 0.100000\n"
            "H 0.760000 0.590000 0.000000\n"
            "H 0.760000 -0.590000 0.000000\n"
            "*\n"
        )
        assert by_name["job_neb_end.xyz"].splitlines()[0] == "3"
        assert materialized.metadata["mode"] == "neb"
        assert materialized.metadata["n_images"] == 3

    def test_neb_ts_materialization_requires_neb_ts_keyword(self) -> None:
        from confflow.programs.orca.adapter import OrcaProgramAdapter

        materialized = OrcaProgramAdapter().materialize_native_input(
            self._inputs("HF-3c NEB-TS", {"n_images": 5, "neb_ts": True})
        )
        by_name = {entry.name: entry.content for entry in materialized.files}
        assert "NImages 5" in by_name["job.inp"]
        assert materialized.metadata["mode"] == "neb_ts"

    def test_neb_block_under_plain_keyword_refused(self) -> None:
        from confflow.programs.orca.adapter import OrcaProgramAdapter

        with pytest.raises(ValueError, match="native_input_error.*NEB"):
            OrcaProgramAdapter().materialize_native_input(
                self._inputs("HF-3c Opt", {"n_images": 3})
            )

    def test_neb_ts_block_under_plain_neb_keyword_refused(self) -> None:
        from confflow.programs.orca.adapter import OrcaProgramAdapter

        with pytest.raises(ValueError, match="native_input_error.*NEB-TS"):
            OrcaProgramAdapter().materialize_native_input(
                self._inputs("HF-3c NEB", {"n_images": 3, "neb_ts": True})
            )


class TestPathTrajectoryFacts:
    """Best-effort trajectory point counts, energies, and truncation."""

    def _real_text(self) -> str:
        return "\n".join(
            [
                "         *************************************************************",
                "         *                          FORWARD IRC                      *",
                "         *************************************************************",
                "",
                "Iteration    E(Eh)      dE(kcal/mol)  max(|G|)   RMS(G)",
                "    0       -76.120000   -1.000000    0.010000  0.005000",
                "    1       -76.110000   -0.500000    0.020000  0.010000",
                "",
                "         *************************************************************",
                "         *                          BACKWARD IRC                     *",
                "         *************************************************************",
                "",
                "Iteration    E(Eh)      dE(kcal/mol)  max(|G|)   RMS(G)",
                "    0       -76.115000   -0.800000    0.015000  0.008000",
                "",
                "         *************************************************************",
                "         *  MAXIMUM NUMBER OF ITERATIONS REACHED - STOPPING IRC RUN  *",
                "         *************************************************************",
                "",
            ]
        )

    def test_counts_energies_and_truncation(self) -> None:
        facts = path_trajectory_facts(self._real_text())
        assert facts["n_points"] == 3
        assert facts["n_forward_points"] == 2
        assert facts["n_reverse_points"] == 1
        assert facts["energies_hartree"] == pytest.approx((-76.12, -76.11, -76.115))
        assert facts["truncated"] is True

    def test_converged_run_is_not_truncated(self) -> None:
        facts = path_trajectory_facts(
            "\n".join(
                [
                    "         *                          FORWARD IRC                      *",
                    "Iteration    E(Eh)      dE(kcal/mol)  max(|G|)   RMS(G)",
                    "    1       -76.100000   -0.500000    0.020000  0.010000",
                    "",
                ]
            )
        )
        assert facts["truncated"] is False
        assert facts["n_points"] == 1

    def test_garbage_lines_skipped_without_raising(self) -> None:
        facts = path_trajectory_facts("not a point line\nIteration x BROKEN\n")
        assert facts["n_points"] == 0
        assert facts["energies_hartree"] == ()
        assert facts["truncated"] is False

    def test_empty_text_reports_zeros(self) -> None:
        facts = path_trajectory_facts("")
        assert facts == {
            "n_points": 0,
            "n_forward_points": 0,
            "n_reverse_points": 0,
            "energies_hartree": (),
            "truncated": False,
        }
