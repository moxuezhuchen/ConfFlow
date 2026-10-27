#!/usr/bin/env python3

"""Tests for imaginary-mode parsing (Gaussian, ORCA) and the ``ts_mode`` check.

Fixture logs are synthetic but follow the native formats: Gaussian
``Frequencies --`` blocks with ``Atom AN X Y Z`` displacement rows (three
modes per block) closed by ``Zero-point correction=``; ORCA
``VIBRATIONAL FREQUENCIES`` lists closed by a ``NORMAL MODES`` matrix in
column groups of six, with the matrix closed by ``IR SPECTRUM``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np
import pytest

from confflow.domain import ArtifactSet, FrozenDict, ResourceRequest
from confflow.execution import (
    CheckContext,
    CheckOutcome,
    GeometryOutput,
    NativeResult,
    ParsedGeometry,
    ProgramName,
    ResolvedCalculationInputs,
)
from confflow.execution.checks_standard import CHECK_FAILED_CODE
from confflow.execution.checks_ts import TsModeCheck, reaction_coordinate_overlap
from confflow.execution.profile_standard import PROFILES
from confflow.execution.profiles import ProfileContext
from confflow.programs.gaussian import parsing as gaussian_parsing
from confflow.programs.orca import parsing as orca_parsing
from tests.v4._builders import structure

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"

STEP_ID = "s_ts"
WORK_ITEM_ID = "wi:s_ts:item0"
LOGICAL_KEY = "s_ts:item0"

Vector = tuple[float, float, float]


# ---------------------------------------------------------------------------
# Fixture builders
# ---------------------------------------------------------------------------


def _gaussian_section(freqs: list[float], modes: list[list[Vector]], *, committed: bool) -> str:
    """Render one Gaussian harmonic section; ``modes[j][atom]`` is the displacement."""
    lines = [
        " Harmonic frequencies (cm**-1), IR intensities (KM/Mole), "
        "Raman scattering activities (A**4/AMU):"
    ]
    atom_count = len(modes[0]) if modes else 0
    for start in range(0, len(freqs), 3):
        chunk = freqs[start : start + 3]
        columns = list(range(start + 1, start + 1 + len(chunk)))
        lines.append("".join(f"{col:>23d}" for col in columns))
        lines.append("".join(f"{'A':>23s}" for _ in chunk))
        lines.append(" Frequencies --  " + "   ".join(f"{value:10.4f}" for value in chunk))
        lines.append(" Red. masses --     1.0000     1.0000     1.0000")
        lines.append(" Frc consts  --     0.0100     0.0200     0.0300")
        lines.append(" IR Inten    --     0.0000     0.0000     0.0000")
        lines.append(
            "  Atom  AN      X      Y      Z        X      Y      Z        X      Y      Z"
        )
        for atom in range(atom_count):
            cells = []
            for offset in range(len(chunk)):
                dx, dy, dz = modes[start + offset][atom]
                cells.append(f"{dx:9.2f}{dy:7.2f}{dz:7.2f}")
            lines.append(f"{atom + 1:5d}   8  " + "  ".join(cells))
    if committed:
        lines.append(
            " Zero-point correction=                           0.021234 (Hartree/Particle)"
        )
    return "\n".join(lines) + "\n"


WATER_ROWS: list[Vector] = [(0.0, 0.0, 0.1), (0.0, 0.0, -0.2), (0.0, 0.0, 0.1)]


def _gaussian_log(*sections: str) -> str:
    return "Entering Gaussian System\n" + "".join(sections) + "Normal termination of Gaussian 16\n"


def _orca_log(freqs: list[float], columns: list[list[float]], *, closed: bool = True) -> str:
    """Render ORCA frequencies plus a ``NORMAL MODES`` matrix (columns = modes)."""
    lines = ["-------------------", "VIBRATIONAL FREQUENCIES", "-------------------", ""]
    for index, value in enumerate(freqs):
        lines.append(f"{index:4d}:{value:14.2f} cm**-1")
    lines.extend(["", "-----------", "NORMAL MODES", "-----------", "", ""])
    lines.append("These modes are the cartesian displacements in a.u.")
    rows = len(columns[0])
    for start in range(0, len(columns), 6):
        group = list(range(start, min(start + 6, len(columns))))
        lines.append("                  " + "".join(f"{col:>10d}" for col in group))
        for row in range(rows):
            lines.append(f"{row:6d}" + "".join(f"{columns[col][row]:11.6f}" for col in group))
        lines.append("")
    if closed:
        lines.extend(["", "IR SPECTRUM", "------------"])
    return "\n".join(lines) + "\n"


# Water-like three-atom displacement fixtures (9 Cartesian components per mode).
STRETCH_9: list[float] = [0.0, 0.0, 0.0, 0.5, 0.0, 0.0, -0.5, 0.0, 0.0]


def _orca_water_columns(imaginary: float) -> tuple[list[float], list[list[float]]]:
    """Nine ORCA modes: six near-zero, one imaginary stretch, two real modes."""
    freqs = [0.0, 0.0, 0.0, 0.0, 0.0, 0.0, imaginary, 150.0, 300.0]
    columns = [[0.0] * 9 for _ in range(6)]
    columns.append(list(STRETCH_9))
    columns.append([0.1, 0.0, 0.0, 0.2, 0.0, 0.0, 0.1, 0.0, 0.0])
    columns.append([0.0, 0.3, 0.0, 0.0, 0.3, 0.0, 0.0, 0.3, 0.0])
    return freqs, columns


# ---------------------------------------------------------------------------
# Gaussian parser
# ---------------------------------------------------------------------------


class TestGaussianImaginaryMode:
    def test_imaginary_mode_extracted_with_displacements(self) -> None:
        section = _gaussian_section(
            [-123.4567, 200.0, 350.0],
            [WATER_ROWS, [(0.0, 0.1, 0.0)] * 3, [(0.2, 0.0, 0.0)] * 3],
            committed=True,
        )
        mode = gaussian_parsing.parse_imaginary_mode(_gaussian_log(section))
        assert mode is not None
        assert mode["frequency_cm"] == pytest.approx(-123.4567)
        assert mode["displacements"] == [list(row) for row in WATER_ROWS]

    def test_absent_when_no_imaginary_frequency(self) -> None:
        section = _gaussian_section([120.0, 200.0, 350.0], [WATER_ROWS] * 3, committed=True)
        assert gaussian_parsing.parse_imaginary_mode(_gaussian_log(section)) is None

    def test_most_negative_frequency_selected(self) -> None:
        section = _gaussian_section(
            [-50.0, -800.0, 350.0],
            [[(0.0, 0.0, 0.0)] * 3, WATER_ROWS, [(0.0, 0.0, 0.0)] * 3],
            committed=True,
        )
        mode = gaussian_parsing.parse_imaginary_mode(_gaussian_log(section))
        assert mode is not None
        assert mode["frequency_cm"] == pytest.approx(-800.0)
        assert mode["displacements"] == [list(row) for row in WATER_ROWS]

    def test_only_last_committed_section_used(self) -> None:
        first = _gaussian_section([-900.0, 100.0, 200.0], [WATER_ROWS] * 3, committed=True)
        second = _gaussian_section(
            [-120.0, 100.0, 200.0],
            [[(0.5, 0.0, 0.0)] * 3, [(0.0, 0.0, 0.0)] * 3, [(0.0, 0.0, 0.0)] * 3],
            committed=True,
        )
        mode = gaussian_parsing.parse_imaginary_mode(_gaussian_log(first, second))
        assert mode is not None
        assert mode["frequency_cm"] == pytest.approx(-120.0)
        assert mode["displacements"] == [[0.5, 0.0, 0.0]] * 3

    def test_uncommitted_trailing_section_ignored(self) -> None:
        section = _gaussian_section([-300.0, 100.0, 200.0], [WATER_ROWS] * 3, committed=False)
        assert gaussian_parsing.parse_imaginary_mode(_gaussian_log(section)) is None

    def test_noise_floor_mode_is_not_imaginary(self) -> None:
        section = _gaussian_section([-4.0, 100.0, 200.0], [WATER_ROWS] * 3, committed=True)
        assert gaussian_parsing.parse_imaginary_mode(_gaussian_log(section)) is None

    def test_missing_displacement_rows_yield_none(self) -> None:
        text = (
            " Harmonic frequencies (cm**-1):\n"
            " Frequencies --  -300.0000   100.0000   200.0000\n"
            " Zero-point correction=  0.02 (Hartree/Particle)\n"
        )
        assert gaussian_parsing.parse_imaginary_mode(text) is None

    def test_no_frequency_section_yields_none(self) -> None:
        assert gaussian_parsing.parse_imaginary_mode("Normal termination\n") is None


# ---------------------------------------------------------------------------
# ORCA parser
# ---------------------------------------------------------------------------


class TestOrcaImaginaryMode:
    def test_imaginary_mode_extracted_from_matrix_column(self) -> None:
        freqs, columns = _orca_water_columns(-1200.5)
        mode = orca_parsing.parse_imaginary_mode(_orca_log(freqs, columns))
        assert mode is not None
        assert mode["frequency_cm"] == pytest.approx(-1200.5)
        assert mode["displacements"] == [
            [0.0, 0.0, 0.0],
            [0.5, 0.0, 0.0],
            [-0.5, 0.0, 0.0],
        ]

    def test_absent_when_all_frequencies_real(self) -> None:
        freqs, columns = _orca_water_columns(1200.5)
        assert orca_parsing.parse_imaginary_mode(_orca_log(freqs, columns)) is None

    def test_most_negative_of_several_imaginary_modes(self) -> None:
        freqs, columns = _orca_water_columns(-300.0)
        freqs[8] = -1500.0
        mode = orca_parsing.parse_imaginary_mode(_orca_log(freqs, columns))
        assert mode is not None
        assert mode["frequency_cm"] == pytest.approx(-1500.0)
        assert mode["displacements"][1] == [0.0, 0.3, 0.0]

    def test_unclosed_matrix_section_not_committed(self) -> None:
        freqs, columns = _orca_water_columns(-1200.5)
        assert orca_parsing.parse_imaginary_mode(_orca_log(freqs, columns, closed=False)) is None

    def test_no_frequency_section_yields_none(self) -> None:
        assert orca_parsing.parse_imaginary_mode("ORCA TERMINATED\n") is None


# ---------------------------------------------------------------------------
# Overlap math
# ---------------------------------------------------------------------------

LINE_COORDS = np.array([[0.0, 0.0, 0.0], [1.0, 0.0, 0.0], [2.0, 0.0, 0.0]])


class TestReactionCoordinateOverlap:
    def test_pure_stretch_gives_unity(self) -> None:
        mode = np.array([[1.0, 0.0, 0.0], [0.0, 0.0, 0.0], [-1.0, 0.0, 0.0]])
        overlap = reaction_coordinate_overlap(mode, LINE_COORDS, [(1, 3)])
        assert overlap == pytest.approx(1.0)

    def test_perpendicular_rotor_mode_gives_zero(self) -> None:
        mode = np.array([[0.0, 1.0, 0.0], [0.0, 1.0, 0.0], [0.0, -1.0, 0.0]])
        overlap = reaction_coordinate_overlap(mode, LINE_COORDS, [(1, 3)])
        assert overlap == pytest.approx(0.0, abs=1e-12)

    def test_two_pairs_share_the_reaction_vector(self) -> None:
        stretch_a = np.array([[1.0, 0.0, 0.0], [0.0, 0.0, 0.0], [-1.0, 0.0, 0.0]])
        overlap = reaction_coordinate_overlap(stretch_a, LINE_COORDS, [(1, 2), (2, 3)])
        assert overlap == pytest.approx(1.0)
        # Atom 2 moving along the chain lies in the span of both bond stretches:
        # projection norm sqrt(2/3) of the unit mode.
        middle = np.array([[0.0, 0.0, 0.0], [1.0, 0.0, 0.0], [0.0, 0.0, 0.0]])
        assert reaction_coordinate_overlap(middle, LINE_COORDS, [(1, 2), (2, 3)]) == pytest.approx(
            np.sqrt(2.0 / 3.0)
        )

    def test_opposite_sign_stretches_do_not_cancel(self) -> None:
        # Forming bond (1,2) and breaking bond (2,3) stretched with opposite signs:
        # both lie in the span, so the overlap is 1 (a summed reaction vector gives 0).
        forming_minus_breaking = np.array([[1.0, 0.0, 0.0], [-2.0, 0.0, 0.0], [1.0, 0.0, 0.0]])
        overlap = reaction_coordinate_overlap(forming_minus_breaking, LINE_COORDS, [(1, 2), (2, 3)])
        assert overlap == pytest.approx(1.0)

    def test_duplicate_pair_adds_no_direction(self) -> None:
        mode = np.array([[1.0, 0.0, 0.0], [0.0, 0.0, 0.0], [-1.0, 0.0, 0.0]])
        single = reaction_coordinate_overlap(mode, LINE_COORDS, [(1, 3)])
        doubled = reaction_coordinate_overlap(mode, LINE_COORDS, [(1, 3), (3, 1)])
        assert doubled == pytest.approx(single)

    def test_partial_overlap_is_cosine(self) -> None:
        mode = np.array([[1.0, 1.0, 0.0], [0.0, 0.0, 0.0], [-1.0, 1.0, 0.0]])
        overlap = reaction_coordinate_overlap(mode, LINE_COORDS, [(1, 3)])
        assert overlap == pytest.approx(1.0 / np.sqrt(2.0))

    def test_degenerate_or_out_of_range_pairs_return_none(self) -> None:
        mode = np.ones((3, 3))
        coincident = np.zeros((3, 3))
        assert reaction_coordinate_overlap(mode, coincident, [(1, 2)]) is None
        assert reaction_coordinate_overlap(mode, LINE_COORDS, [(1, 9)]) is None
        assert reaction_coordinate_overlap(mode[:2], LINE_COORDS, [(1, 2)]) is None


# ---------------------------------------------------------------------------
# Check outcomes
# ---------------------------------------------------------------------------


def _native(geometry: list[Vector], mode: dict[str, Any] | None) -> NativeResult:
    metadata: dict[str, Any] = {}
    if mode is not None:
        metadata["imaginary_mode"] = mode
    return NativeResult(
        program=ProgramName.GAUSSIAN,
        terminated_normally=True,
        geometry_output=GeometryOutput.PRODUCED,
        final_geometry=ParsedGeometry(
            atoms=("C", "O", "O"),
            coordinates=tuple(tuple(point) for point in geometry),
        ),
        energies_hartree=FrozenDict({}),
        frequencies_cm=(),
        native_metadata=FrozenDict(metadata),
        produced_files=(),
        parser_diagnostics=(),
        log_file_name="job.log",
    )


def _inputs() -> ResolvedCalculationInputs:
    return ResolvedCalculationInputs(
        structure=structure("s0"),
        charge=0,
        multiplicity=1,
        freeze=None,
        resources=ResourceRequest.from_values(cores_per_item=4, memory_per_item="16GB"),
        native=FrozenDict({"keyword": "x"}),
        checkpoints=(),
        step_id=STEP_ID,
        work_item_id=WORK_ITEM_ID,
        logical_key=LOGICAL_KEY,
    )


def _run(
    geometry: list[Vector],
    mode: dict[str, Any] | None,
    params: dict[str, Any] | None,
) -> CheckOutcome:
    profile = PROFILES["standard"].apply(
        ProfileContext(
            work_item_id=WORK_ITEM_ID,
            step_id=STEP_ID,
            logical_key=LOGICAL_KEY,
            profile_name="standard",
            profile_version=PROFILES["standard"].contract_version,
            native_result=_native(geometry, mode),
            inputs=_inputs(),
            discovered_artifacts=ArtifactSet(),
        )
    )
    return TsModeCheck().run(
        CheckContext(
            check_name="ts_mode",
            work_item_id=WORK_ITEM_ID,
            step_id=STEP_ID,
            logical_key=LOGICAL_KEY,
            profile_output=profile,  # type: ignore[arg-type]
            inputs=_inputs(),
            params=FrozenDict(dict(params or {})),
        )
    )


GEOMETRY: list[Vector] = [(0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (2.0, 0.0, 0.0)]
REACTIVE_MODE = {
    "frequency_cm": -900.0,
    "displacements": [[1.0, 0.0, 0.0], [0.0, 0.0, 0.0], [-1.0, 0.0, 0.0]],
}
ROTOR_MODE = {
    "frequency_cm": -40.0,
    "displacements": [[0.0, 1.0, 0.0], [0.0, 1.0, 0.0], [0.0, -1.0, 0.0]],
}
PAIRS_13 = {"pairs": [[1, 3]]}


def _reason(outcome: CheckOutcome) -> str:
    assert outcome.passed is False
    assert outcome.diagnostic is not None
    assert outcome.diagnostic.code == CHECK_FAILED_CODE
    return str(outcome.diagnostic.details["reason"])


class TestTsModeCheck:
    def test_name_and_contract_version(self) -> None:
        check = TsModeCheck()
        assert check.name == "ts_mode"
        assert check.contract_version == "confflow.contract.check.ts_mode.v1"

    def test_reactive_mode_passes_with_default_threshold(self) -> None:
        assert _run(GEOMETRY, REACTIVE_MODE, PAIRS_13).passed is True

    def test_rotor_mode_fails_as_not_reactive_with_details(self) -> None:
        outcome = _run(GEOMETRY, ROTOR_MODE, PAIRS_13)
        assert _reason(outcome) == "ts_mode_not_reactive"
        details = outcome.diagnostic.details  # type: ignore[union-attr]
        assert details["overlap"] == pytest.approx(0.0, abs=1e-9)
        assert details["frequency_cm"] == pytest.approx(-40.0)
        assert details["min_overlap"] == pytest.approx(0.2)

    def test_explicit_threshold_is_honoured(self) -> None:
        partial = {
            "frequency_cm": -700.0,
            "displacements": [[1.0, 1.0, 0.0], [0.0, 0.0, 0.0], [-1.0, 1.0, 0.0]],
        }
        assert _run(GEOMETRY, partial, {"pairs": [[1, 3]], "min_overlap": 0.6}).passed is True
        outcome = _run(GEOMETRY, partial, {"pairs": [[1, 3]], "min_overlap": 0.9})
        assert _reason(outcome) == "ts_mode_not_reactive"
        assert outcome.diagnostic.details["overlap"] == pytest.approx(1.0 / np.sqrt(2.0))  # type: ignore[union-attr]

    def test_two_pairs_pass_on_chain_stretch(self) -> None:
        mode = {
            "frequency_cm": -800.0,
            "displacements": [[1.0, 0.0, 0.0], [0.0, 0.0, 0.0], [-1.0, 0.0, 0.0]],
        }
        assert _run(GEOMETRY, mode, {"pairs": [[1, 2], [2, 3]]}).passed is True

    def test_missing_imaginary_mode_is_no_data_failure(self) -> None:
        assert _reason(_run(GEOMETRY, None, PAIRS_13)) == "no_imaginary_mode"

    def test_malformed_displacements_are_no_data_failures(self) -> None:
        wrong_shape = {"frequency_cm": -900.0, "displacements": [[1.0, 0.0, 0.0]]}
        zero = {"frequency_cm": -900.0, "displacements": [[0.0, 0.0, 0.0]] * 3}
        assert _reason(_run(GEOMETRY, wrong_shape, PAIRS_13)) == "no_imaginary_mode"
        assert _reason(_run(GEOMETRY, zero, PAIRS_13)) == "no_imaginary_mode"
        assert _reason(_run(GEOMETRY, {"frequency_cm": -1.0, "displacements": "x"}, PAIRS_13)) == (
            "no_imaginary_mode"
        )

    @pytest.mark.parametrize(
        "params",
        [
            {},
            {"pairs": []},
            {"pairs": "1-3"},
            {"pairs": [[1]]},
            {"pairs": [[1, 2, 3]]},
            {"pairs": [[1, 1]]},
            {"pairs": [[0, 2]]},
            {"pairs": [[True, 2]]},
            {"pairs": [["1", "3"]]},
            {"pairs": [[1, 3]], "min_overlap": 1.5},
            {"pairs": [[1, 3]], "min_overlap": -0.1},
            {"pairs": [[1, 3]], "min_overlap": "high"},
            {"pairs": [[1, 3]], "min_overlap": True},
        ],
    )
    def test_invalid_parameters_fail_closed(self, params: dict[str, Any]) -> None:
        assert _reason(_run(GEOMETRY, REACTIVE_MODE, params)) == "invalid_check_parameter"

    def test_pair_outside_geometry_is_bond_atoms_missing(self) -> None:
        assert _reason(_run(GEOMETRY, REACTIVE_MODE, {"pairs": [[1, 9]]})) == "bond_atoms_missing"

    def test_coincident_pair_is_bond_atoms_missing(self) -> None:
        coincident = [(0.0, 0.0, 0.0), (0.0, 0.0, 0.0), (2.0, 0.0, 0.0)]
        assert _reason(_run(coincident, REACTIVE_MODE, {"pairs": [[1, 2]]})) == "bond_atoms_missing"


# ---------------------------------------------------------------------------
# Real saddle-point outputs (HCN -> HNC, External UMA frequency job)
# ---------------------------------------------------------------------------

REAL_PAIRS_NH = [(2, 3)]
REAL_PAIRS_CH = [(1, 3)]


class TestRealSaddlePointFixtures:
    def test_gaussian_imaginary_mode_from_real_log(self) -> None:
        text = (FIXTURES / "gaussian_external_uma_freq_ts.log").read_text()
        mode = gaussian_parsing.parse_imaginary_mode(text)
        assert mode is not None
        assert mode["frequency_cm"] == pytest.approx(-1056.1282)
        displacements = np.array(mode["displacements"])
        assert displacements.shape == (3, 3)
        assert displacements[2, :2] == pytest.approx([-0.20, 0.97], abs=1e-9)

    def test_gaussian_overlap_with_final_geometry(self) -> None:
        text = (FIXTURES / "gaussian_external_uma_freq_ts.log").read_text()
        mode = gaussian_parsing.parse_imaginary_mode(text)
        geometry = gaussian_parsing.parse_final_geometry(text)
        assert mode is not None and geometry is not None
        displacements = np.array(mode["displacements"])
        coordinates = np.array(geometry[1])
        nh = reaction_coordinate_overlap(displacements, coordinates, REAL_PAIRS_NH)
        ch = reaction_coordinate_overlap(displacements, coordinates, REAL_PAIRS_CH)
        both = reaction_coordinate_overlap(
            displacements, coordinates, REAL_PAIRS_NH + REAL_PAIRS_CH
        )
        assert nh is not None and ch is not None and both is not None
        assert nh == pytest.approx(0.563, abs=0.005)
        assert ch == pytest.approx(0.184, abs=0.005)
        assert both >= nh - 1e-12

    def test_orca_imaginary_mode_from_real_output(self) -> None:
        text = (FIXTURES / "orca_xtb2_optts_freq.out").read_text()
        mode = orca_parsing.parse_imaginary_mode(text)
        assert mode is not None
        assert mode["frequency_cm"] == pytest.approx(-1426.08)

    def test_orca_forming_bond_outweighs_spectator_bond(self) -> None:
        text = (FIXTURES / "orca_xtb2_optts_freq.out").read_text()
        mode = orca_parsing.parse_imaginary_mode(text)
        geometry = orca_parsing.parse_cartesian_blocks(text)
        assert mode is not None and geometry is not None
        displacements = np.array(mode["displacements"])
        coordinates = np.array(geometry[1])
        nh = reaction_coordinate_overlap(displacements, coordinates, REAL_PAIRS_NH)
        ch = reaction_coordinate_overlap(displacements, coordinates, REAL_PAIRS_CH)
        assert nh is not None and ch is not None
        assert nh > ch
