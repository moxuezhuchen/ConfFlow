#!/usr/bin/env python3

"""V4-5 Gaussian QST helper tests.

Fixtures are synthetic in content but grammatical in form;
no test shells out to Gaussian.  Error assertions always require the
``native_input_error:`` prefix, mirroring
:mod:`confflow.programs.gaussian.rendering`.
"""

from __future__ import annotations

import pytest

from confflow.domain.structure import StructureRecord
from confflow.programs.gaussian import named as gaussian_named

WATER_ATOMS = ("O", "H", "H")

FORWARD_ROWS = (
    ("O", 0.0, 0.0, 0.0),
    ("H", 0.76, 0.59, 0.0),
    ("H", 0.76, -0.59, 0.0),
)


_Z_BY_SYMBOL = {"H": 1, "He": 2, "C": 6, "N": 7, "O": 8, "F": 9}


def _expected_coords(
    rows: tuple[tuple[str, float, float, float], ...],
) -> tuple[tuple[float, float, float], ...]:
    """Return the coordinate triples of handcrafted fixture rows."""
    return tuple((x, y, z) for _, x, y, z in rows)


def _assert_native_input_error(excinfo: pytest.ExceptionInfo[BaseException]) -> None:
    """Require the ``native_input_error:`` prefix on an error message."""
    assert "native_input_error:" in str(excinfo.value)


def _slot(
    slot_id: str,
    atoms: tuple[str, ...] = WATER_ATOMS,
    coords: tuple[tuple[float, float, float], ...] = (
        (0.0, 0.0, 0.0),
        (0.76, 0.59, 0.0),
        (-0.76, 0.59, 0.0),
    ),
    charge: int | None = 0,
    multiplicity: int | None = 1,
) -> StructureRecord:
    """Build a QST slot structure record."""
    return StructureRecord(
        id=slot_id,
        atoms=atoms,
        coordinates=coords,
        charge=charge,
        multiplicity=multiplicity,
    )


REACTANT_ATOMS = ("O", "H", "H")
REACTANT_COORDS = ((0.0, 0.0, 0.0), (0.76, 0.59, 0.0), (-0.76, 0.59, 0.0))
PRODUCT_COORDS = ((0.1, 0.0, -0.0), (0.86, 0.59, 0.0), (-0.66, 0.59, 0.0))
GUESS_COORDS = ((0.05, 0.0, 0.0), (0.8, 0.6, 0.1), (-0.7, 0.55, -0.05))

# Verified against real Gaussian 16: the first (reactant) spec is untitled
# (the job title card serves as its title) and every later spec requires
# its own title line PLUS a trailing blank line before charge/multiplicity.
EXPECTED_QST2 = "\n".join(
    [
        "0 1",
        "O 0.00000000 0.00000000 0.00000000",
        "H 0.76000000 0.59000000 0.00000000",
        "H -0.76000000 0.59000000 0.00000000",
        "",
        "product",
        "",
        "0 1",
        "O 0.10000000 0.00000000 0.00000000",
        "H 0.86000000 0.59000000 0.00000000",
        "H -0.66000000 0.59000000 0.00000000",
    ]
    + [""]
)

EXPECTED_QST3 = "\n".join(
    [
        "0 1",
        "O 0.00000000 0.00000000 0.00000000",
        "H 0.76000000 0.59000000 0.00000000",
        "H -0.76000000 0.59000000 0.00000000",
        "",
        "product",
        "",
        "0 1",
        "O 0.10000000 0.00000000 0.00000000",
        "H 0.86000000 0.59000000 0.00000000",
        "H -0.66000000 0.59000000 0.00000000",
        "",
        "guess",
        "",
        "0 1",
        "O 0.05000000 0.00000000 0.00000000",
        "H 0.80000000 0.60000000 0.10000000",
        "H -0.70000000 0.55000000 -0.05000000",
    ]
    + [""]
)


class TestValidateQstSlots:
    """QST slot charge/multiplicity/count gates."""

    def test_matching_slots_pass(self) -> None:
        gaussian_named.validate_qst_slots(
            reactant=_slot("r"),
            product=_slot("p"),
            charge=0,
            multiplicity=1,
        )

    def test_undeclared_slot_values_inherit(self) -> None:
        gaussian_named.validate_qst_slots(
            reactant=_slot("r", charge=None, multiplicity=None),
            product=_slot("p", charge=None, multiplicity=None),
            guess=_slot("g", charge=None, multiplicity=None),
            charge=0,
            multiplicity=1,
        )

    def test_missing_reactant_raises(self) -> None:
        with pytest.raises(ValueError) as excinfo:
            gaussian_named.validate_qst_slots(
                reactant=None, product=_slot("p"), charge=0, multiplicity=1
            )
        _assert_native_input_error(excinfo)

    def test_charge_conflict_raises(self) -> None:
        with pytest.raises(ValueError) as excinfo:
            gaussian_named.validate_qst_slots(
                reactant=_slot("r", charge=0),
                product=_slot("p", charge=1),
                charge=0,
                multiplicity=1,
            )
        _assert_native_input_error(excinfo)

    def test_multiplicity_conflict_raises(self) -> None:
        with pytest.raises(ValueError) as excinfo:
            gaussian_named.validate_qst_slots(
                reactant=_slot("r"),
                product=_slot("p"),
                guess=_slot("g", multiplicity=3),
                charge=0,
                multiplicity=1,
            )
        _assert_native_input_error(excinfo)

    def test_atom_count_mismatch_raises(self) -> None:
        with pytest.raises(ValueError) as excinfo:
            gaussian_named.validate_qst_slots(
                reactant=_slot("r"),
                product=_slot(
                    "p",
                    atoms=("O", "H"),
                    coords=((0.0, 0.0, 0.0), (0.76, 0.59, 0.0)),
                ),
                charge=0,
                multiplicity=1,
            )
        _assert_native_input_error(excinfo)

    def test_guess_count_mismatch_raises(self) -> None:
        with pytest.raises(ValueError) as excinfo:
            gaussian_named.validate_qst_slots(
                reactant=_slot("r"),
                product=_slot("p"),
                guess=_slot(
                    "g",
                    atoms=("O", "H", "H", "H"),
                    coords=(
                        (0.0, 0.0, 0.0),
                        (0.76, 0.59, 0.0),
                        (-0.76, 0.59, 0.0),
                        (0.0, 0.0, 1.0),
                    ),
                ),
                charge=0,
                multiplicity=1,
            )
        _assert_native_input_error(excinfo)

    def test_unresolved_charge_raises(self) -> None:
        with pytest.raises(ValueError) as excinfo:
            gaussian_named.validate_qst_slots(
                reactant=_slot("r"), product=_slot("p"), charge=None, multiplicity=1
            )
        _assert_native_input_error(excinfo)


class TestRenderQstMoleculeSpecs:
    """Golden QST2/QST3 section rendering."""

    def test_qst2_golden(self) -> None:
        assert (
            gaussian_named.render_qst_molecule_specs(
                reactant_atoms=REACTANT_ATOMS,
                reactant_coords=REACTANT_COORDS,
                product_atoms=REACTANT_ATOMS,
                product_coords=PRODUCT_COORDS,
                charge=0,
                multiplicity=1,
            )
            == EXPECTED_QST2
        )

    def test_qst3_golden(self) -> None:
        assert (
            gaussian_named.render_qst_molecule_specs(
                reactant_atoms=REACTANT_ATOMS,
                reactant_coords=REACTANT_COORDS,
                product_atoms=REACTANT_ATOMS,
                product_coords=PRODUCT_COORDS,
                guess_atoms=REACTANT_ATOMS,
                guess_coords=GUESS_COORDS,
                charge=0,
                multiplicity=1,
            )
            == EXPECTED_QST3
        )

    def test_half_guess_raises(self) -> None:
        with pytest.raises(ValueError) as excinfo:
            gaussian_named.render_qst_molecule_specs(
                reactant_atoms=REACTANT_ATOMS,
                reactant_coords=REACTANT_COORDS,
                product_atoms=REACTANT_ATOMS,
                product_coords=PRODUCT_COORDS,
                guess_atoms=REACTANT_ATOMS,
                guess_coords=None,
                charge=0,
                multiplicity=1,
            )
        _assert_native_input_error(excinfo)

    def test_unresolved_charge_raises(self) -> None:
        with pytest.raises(ValueError) as excinfo:
            gaussian_named.render_qst_molecule_specs(
                reactant_atoms=REACTANT_ATOMS,
                reactant_coords=REACTANT_COORDS,
                product_atoms=REACTANT_ATOMS,
                product_coords=PRODUCT_COORDS,
                charge=None,
                multiplicity=1,
            )
        _assert_native_input_error(excinfo)

    def test_coordinate_count_mismatch_raises(self) -> None:
        with pytest.raises(ValueError) as excinfo:
            gaussian_named.render_qst_molecule_specs(
                reactant_atoms=REACTANT_ATOMS,
                reactant_coords=REACTANT_COORDS[:2],
                product_atoms=REACTANT_ATOMS,
                product_coords=PRODUCT_COORDS,
                charge=0,
                multiplicity=1,
            )
        _assert_native_input_error(excinfo)

    def test_unknown_symbol_raises(self) -> None:
        with pytest.raises(ValueError) as excinfo:
            gaussian_named.render_qst_molecule_specs(
                reactant_atoms=("O", "H", "Xx"),
                reactant_coords=REACTANT_COORDS,
                product_atoms=REACTANT_ATOMS,
                product_coords=PRODUCT_COORDS,
                charge=0,
                multiplicity=1,
            )
        _assert_native_input_error(excinfo)

    def test_non_finite_coordinate_raises(self) -> None:
        bad = ((0.0, 0.0, 0.0), (0.76, 0.59, float("inf")), (-0.76, 0.59, 0.0))
        with pytest.raises(ValueError) as excinfo:
            gaussian_named.render_qst_molecule_specs(
                reactant_atoms=REACTANT_ATOMS,
                reactant_coords=bad,
                product_atoms=REACTANT_ATOMS,
                product_coords=PRODUCT_COORDS,
                charge=0,
                multiplicity=1,
            )
        _assert_native_input_error(excinfo)


class TestParseQstRoute:
    """QST flavor detection and route/slot mismatch gates."""

    def test_qst2_detected(self) -> None:
        assert gaussian_named.parse_qst_route("B3LYP/6-31G* QST2 Opt", has_guess=False) == "qst2"

    def test_qst3_detected_case_insensitive(self) -> None:
        assert gaussian_named.parse_qst_route("#p b3lyp qst3 opt", has_guess=True) == "qst3"

    def test_qst2_with_guess_raises(self) -> None:
        with pytest.raises(ValueError) as excinfo:
            gaussian_named.parse_qst_route("B3LYP QST2 Opt", has_guess=True)
        _assert_native_input_error(excinfo)

    def test_qst3_without_guess_raises(self) -> None:
        with pytest.raises(ValueError) as excinfo:
            gaussian_named.parse_qst_route("B3LYP QST3 Opt", has_guess=False)
        _assert_native_input_error(excinfo)

    def test_non_qst_route_raises(self) -> None:
        with pytest.raises(ValueError) as excinfo:
            gaussian_named.parse_qst_route("B3LYP Opt Freq", has_guess=False)
        _assert_native_input_error(excinfo)

    def test_bare_qst_raises(self) -> None:
        with pytest.raises(ValueError) as excinfo:
            gaussian_named.parse_qst_route("B3LYP QST Opt", has_guess=False)
        _assert_native_input_error(excinfo)

    def test_both_flavors_raise(self) -> None:
        with pytest.raises(ValueError) as excinfo:
            gaussian_named.parse_qst_route("B3LYP QST2 QST3 Opt", has_guess=True)
        _assert_native_input_error(excinfo)

    def test_non_boolean_has_guess_raises(self) -> None:
        with pytest.raises(ValueError) as excinfo:
            gaussian_named.parse_qst_route("B3LYP QST2 Opt", has_guess="yes")  # type: ignore[arg-type]
        _assert_native_input_error(excinfo)


QST_SUCCESS_ENERGY = -76.0580000000


def _qst_orientation_block(
    rows: tuple[tuple[str, float, float, float], ...],
) -> list[str]:
    """Format a real ``Standard orientation`` table for fixture rows."""
    lines = [
        "                         Standard orientation:",
        " ---------------------------------------------------------------------",
        " Center     Atomic      Atomic             Coordinates (Angstroms)",
        " Number     Number       Type             X           Y           Z",
        " ---------------------------------------------------------------------",
    ]
    for center, (symbol, x, y, z) in enumerate(rows, start=1):
        lines.append(
            f"      {center}          {_Z_BY_SYMBOL[symbol]}           0"
            f"        {x:.6f}    {y:.6f}   {z:.6f}"
        )
    lines.append(" ---------------------------------------------------------------------")
    return lines


def _qst_log(*, converged: bool = True) -> str:
    """Assemble a minimal vendor-credible QST2 output log.

    Grammar-faithful to real Gaussian 16 QST2 output (vendor
    ``test1048.log``: ``Standard orientation`` tables with atomic
    numbers, ``SCF Done`` energies, ``-- Stationary point found.``,
    ``Normal termination``); content is synthetic, never copied vendor
    text.  The error shape mirrors a live adapter-rendered QST2 run
    (real g16 enters ``Berny optimization ... Search for a saddle
    point ... LST/QST climbing`` then ``Error termination via Lnk1e``).
    """
    lines = [
        " Entering Gaussian System, Link 0=g16",
        " Gaussian Test Job: synthetic QST2 opt",
        *_qst_orientation_block(FORWARD_ROWS),
        f" SCF Done:  E(RHF) =  {QST_SUCCESS_ENERGY!r}     A.U. after   12 cycles",
    ]
    if converged:
        lines.append("    -- Stationary point found.")
        lines.append(" Normal termination of Gaussian 16")
    else:
        lines.append(" LST/QST climbing along tangent vector")
        lines.append(" Error termination via Lnk1e")
    return "\n".join(lines) + "\n"


class TestQstOutputParsing:
    """QST OUTPUT parsing against real Gaussian 16 log grammar.

    Rendering acceptance (real g16 consumes the adapter QST2 input and
    drives the QST optimizer) is necessary but not sufficient: the
    output facts must come from the same native grammar the vendor
    emits.  These tests parse vendor-credible logs through the
    production parser (never a fake dialect).
    """

    def test_successful_qst2_output_parses(self) -> None:
        from confflow.programs.gaussian import parsing as gaussian_parsing

        text = _qst_log(converged=True)
        assert gaussian_parsing.termination_reached(text) is True
        energies, sources = gaussian_parsing.parse_energies(text)
        assert energies["electronic"] == QST_SUCCESS_ENERGY
        assert sources["electronic"] == "scf_done"
        geometry = gaussian_parsing.parse_final_geometry(text)
        assert geometry is not None
        atoms, coords = geometry
        assert atoms == WATER_ATOMS
        assert coords == _expected_coords(FORWARD_ROWS)

    def test_error_terminated_qst2_output_is_not_terminated(self) -> None:
        from confflow.programs.gaussian import parsing as gaussian_parsing

        text = _qst_log(converged=False)
        assert gaussian_parsing.termination_reached(text) is False
        energies, _ = gaussian_parsing.parse_energies(text)
        assert energies["electronic"] == QST_SUCCESS_ENERGY

    def test_adapter_qst2_result_end_to_end(self, tmp_path) -> None:
        from confflow.domain._immutable import FrozenDict
        from confflow.domain.resources import ResourceRequest
        from confflow.execution.native import (
            GeometryOutput,
            ResolvedCalculationInputs,
        )
        from confflow.programs.gaussian.adapter import GaussianProgramAdapter

        reactant = _slot("r")
        product = _slot(
            "p",
            coords=(
                (0.1, 0.0, 0.0),
                (0.86, 0.59, 0.0),
                (-0.66, 0.59, 0.0),
            ),
        )
        inputs = ResolvedCalculationInputs(
            structure=reactant,
            charge=0,
            multiplicity=1,
            freeze=(),
            resources=ResourceRequest(cores_per_item=1, memory_per_item_bytes=2 * 1024**3),
            native=FrozenDict({"keyword": "HF/STO-3G Opt(QST2)"}),
            checkpoints=(),
            extra_structures=FrozenDict({"reactant": (reactant,), "product": (product,)}),
            step_id="s",
            work_item_id="w",
            logical_key="qstjob",
            seed=None,
        )
        adapter = GaussianProgramAdapter()
        materialized = adapter.materialize_native_input(inputs)
        assert materialized.metadata["mode"] == "qst2"
        log_name = "qstjob.log"
        (tmp_path / log_name).write_text(_qst_log(converged=True))
        result = adapter.parse_native_result(
            work_dir=str(tmp_path),
            log_file_name=log_name,
            materialized=materialized,
        )
        assert result.terminated_normally is True
        assert result.geometry_output == GeometryOutput.PRODUCED
        assert result.final_geometry is not None
        assert result.final_geometry.atoms == WATER_ATOMS
        assert result.energies_hartree["electronic"] == QST_SUCCESS_ENERGY
        assert result.native_metadata["electronic_source"] == "scf_done"

    def test_adapter_qst3_mode_renders_and_parses(self, tmp_path) -> None:
        from confflow.domain._immutable import FrozenDict
        from confflow.domain.resources import ResourceRequest
        from confflow.execution.native import (
            GeometryOutput,
            ResolvedCalculationInputs,
        )
        from confflow.programs.gaussian.adapter import GaussianProgramAdapter

        reactant = _slot("r")
        product = _slot(
            "p",
            coords=(
                (0.1, 0.0, 0.0),
                (0.86, 0.59, 0.0),
                (-0.66, 0.59, 0.0),
            ),
        )
        guess = _slot(
            "g",
            coords=(
                (0.05, 0.0, 0.0),
                (0.8, 0.6, 0.1),
                (-0.7, 0.55, -0.05),
            ),
        )
        inputs = ResolvedCalculationInputs(
            structure=reactant,
            charge=0,
            multiplicity=1,
            freeze=(),
            resources=ResourceRequest(cores_per_item=1, memory_per_item_bytes=2 * 1024**3),
            native=FrozenDict({"keyword": "HF/STO-3G Opt(QST3)"}),
            checkpoints=(),
            extra_structures=FrozenDict(
                {
                    "reactant": (reactant,),
                    "product": (product,),
                    "guess": (guess,),
                }
            ),
            step_id="s",
            work_item_id="w",
            logical_key="qstjob",
            seed=None,
        )
        adapter = GaussianProgramAdapter()
        materialized = adapter.materialize_native_input(inputs)
        assert materialized.metadata["mode"] == "qst3"
        log_name = "qstjob.log"
        (tmp_path / log_name).write_text(_qst_log(converged=True))
        result = adapter.parse_native_result(
            work_dir=str(tmp_path),
            log_file_name=log_name,
            materialized=materialized,
        )
        assert result.terminated_normally is True
        assert result.geometry_output == GeometryOutput.PRODUCED
        assert result.energies_hartree["electronic"] == QST_SUCCESS_ENERGY
