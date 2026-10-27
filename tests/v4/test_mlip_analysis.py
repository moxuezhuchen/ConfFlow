#!/usr/bin/env python3

"""Focused tests for the pure MLIP analysis helpers (bond check, calibration, xyz)."""

from __future__ import annotations

import pytest

from confflow.domain._immutable import FrozenDict
from confflow.domain.structure import StructureRecord
from confflow.execution import mlip_analysis as analysis

HARTREE_TO_KCAL = 627.5094740631

WATER_ATOMS = ("O", "H", "H")
WATER = ((0.0, 0.0, 0.0), (0.96, 0.0, 0.0), (-0.24, 0.93, 0.0))


def _water_record(coords=WATER) -> StructureRecord:
    return StructureRecord(
        id="w",
        atoms=WATER_ATOMS,
        coordinates=tuple(tuple(float(v) for v in xyz) for xyz in coords),
        charge=0,
        multiplicity=1,
        metadata=FrozenDict({}),
    )


def _output(
    record: StructureRecord, energy: float, row=None, max_ratio=None
) -> analysis.MlipOutput:
    return analysis.MlipOutput(
        record=record,
        stage="relax",
        energy=energy,
        coords=record.coordinates,
        meta={},
        row=row or {"converged": True, "n_evals": 3, "wall_s": 0.5},
        max_ratio=max_ratio,
        pair=None,
        changed=False,
    )


def test_perceive_bonds_finds_the_two_oh_bonds_only() -> None:
    assert analysis.perceive_bonds(WATER_ATOMS, WATER) == frozenset({(0, 1), (0, 2)})


def test_perceive_bonds_on_a_single_atom_is_empty() -> None:
    assert analysis.perceive_bonds(("O",), ((0.0, 0.0, 0.0),)) == frozenset()


def test_distance_is_euclidean() -> None:
    assert analysis.distance((0.0, 0.0, 0.0), (3.0, 4.0, 0.0)) == pytest.approx(5.0)


def test_fix_deviation_is_zero_without_pairs_and_measures_the_change() -> None:
    moved = ((0.0, 0.0, 0.0), (0.90, 0.0, 0.0), (-0.24, 0.93, 0.0))
    assert analysis.fix_deviation((), WATER, moved) == 0.0
    assert analysis.fix_deviation(((0, 1),), WATER, moved) == pytest.approx(0.06)


def test_fix_deviation_measures_against_per_pair_targets() -> None:
    moved = ((0.0, 0.0, 0.0), (0.90, 0.0, 0.0), (-0.24, 0.93, 0.0))
    # None keeps the input distance as the reference (0.96 -> 0.90, deviation 0.06).
    assert analysis.fix_deviation(((0, 1),), WATER, moved, (None,)) == pytest.approx(0.06)
    # An explicit target is the reference: |0.90 - 0.95| = 0.05.
    assert analysis.fix_deviation(((0, 1),), WATER, moved, (0.95,)) == pytest.approx(0.05)
    # Mixed targets: pair (0,1) has target 0.95 (dev 0.05); pair (0,2) uses its
    # input distance (unchanged, dev 0.0), so the largest deviation is 0.05.
    pairs = ((0, 1), (0, 2))
    assert analysis.fix_deviation(pairs, WATER, moved, (0.95, None)) == pytest.approx(0.05)
    with pytest.raises(ValueError):
        analysis.fix_deviation(pairs, WATER, moved, (0.95,))


def test_stretch_check_reports_ratio_pair_and_graph_change() -> None:
    stretched = ((0.0, 0.0, 0.0), (1.6, 0.0, 0.0), (-0.24, 0.93, 0.0))
    ratio, pair, changed = analysis.stretch_check(_water_record(), stretched, ())
    assert ratio == pytest.approx(1.6 / 0.96, rel=1e-3)
    assert pair == (0, 1)
    assert changed is True


def test_stretch_check_ignores_fixed_pairs() -> None:
    stretched = ((0.0, 0.0, 0.0), (1.6, 0.0, 0.0), (-0.24, 0.93, 0.0))
    ratio, pair, changed = analysis.stretch_check(_water_record(), stretched, ((0, 1),))
    assert ratio == pytest.approx(1.0)
    assert pair == (0, 2)
    assert changed is False


def test_stretch_check_without_bonds_returns_none() -> None:
    lone = StructureRecord(
        id="ar",
        atoms=("Ar", "Ar"),
        coordinates=((0.0, 0.0, 0.0), (6.0, 0.0, 0.0)),
        charge=0,
        multiplicity=1,
        metadata=FrozenDict({}),
    )
    assert analysis.stretch_check(lone, lone.coordinates, ()) == (None, None, False)


def test_mean_of_empty_and_filled_sequences() -> None:
    assert analysis.mean([]) is None
    assert analysis.mean([1.0, 2.0, 4.0]) == pytest.approx(7.0 / 3.0)


def test_ranks_share_the_mean_rank_on_ties() -> None:
    assert analysis._ranks([2.0, 1.0, 1.0]) == [3.0, 1.5, 1.5]
    assert analysis._ranks([5.0, 5.0, 5.0]) == [2.0, 2.0, 2.0]
    assert analysis._ranks([]) == []


def test_spearman_identity_reversal_and_undefined_cases() -> None:
    assert analysis.spearman([1.0, 2.0, 3.0], [10.0, 20.0, 30.0]) == pytest.approx(1.0)
    assert analysis.spearman([1.0, 2.0, 3.0], [30.0, 20.0, 10.0]) == pytest.approx(-1.0)
    assert analysis.spearman([1.0], [2.0]) is None
    assert analysis.spearman([1.0, 2.0], [1.0, 2.0, 3.0]) is None
    assert analysis.spearman([1.0, 1.0], [2.0, 3.0]) is None


def test_calibration_needs_two_pairs_and_reports_basis() -> None:
    assert analysis.calibration([("a", -76.0, -76.0)], "sp_all_inputs") is None
    pairs = [("a", -76.0, -76.00), ("b", -75.9, -76.02), ("c", -76.1, -75.99)]
    result = analysis.calibration(pairs, "relaxed_outputs")
    assert result is not None
    assert result["basis"] == "relaxed_outputs"
    assert result["n"] == 3
    assert result["input_lowest"] == "c"
    assert result["spearman"] == pytest.approx(-1.0)
    assert result["kcal_above_mlip_lowest"] == pytest.approx((-75.99 - (-76.02)) * HARTREE_TO_KCAL)


def test_xyz_text_writes_one_frame_per_output_with_comment() -> None:
    record = _water_record()
    outputs = [_output(record, -76.0, max_ratio=1.1)]
    text = analysis.xyz_text(outputs, {"w": "s00001"})
    lines = text.splitlines()
    assert lines[0] == "3"
    assert lines[1].startswith("key=s00001 energy_eh=-76.0000000000 stage=relax")
    assert "converged=True n_evals=3 wall_s=0.5 max_bond_stretch=1.1" in lines[1]
    assert lines[2].startswith("O 0.0000000000 0.0000000000 0.0000000000")
    assert len(lines) == 5


def test_xyz_text_for_no_outputs_is_empty() -> None:
    assert analysis.xyz_text([], {}) == ""


def test_module_exposes_bond_scale_matching_refine_default() -> None:
    assert analysis.BOND_SCALE == pytest.approx(1.15)
