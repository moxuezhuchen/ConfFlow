#!/usr/bin/env python3

"""``refine`` uses the shared ConfGen identity: mirror images, energies and labels.

Covers the three parts of the identity that the symmetry tests do not reach: mirror
images (merged by default, kept apart with ``mirror: False``), electronic energies
bound through ``results`` (a gap above ``E_TOL`` keeps both records, a smaller gap keeps
the lower-energy one), and a relabelling that changes which indices are bonded while the
molecule stays the same.
"""

from __future__ import annotations

import numpy as np
import pytest

from confflow.domain.result import ResultSet, ScientificResult
from confflow.domain.structure import StructureRecord
from confflow.domain.units import Unit
from confflow.execution.transform_executor import (
    REFINE_DEFAULT_RMSD_THRESHOLD_ANGSTROM,
    TransformExecutor,
)
from confflow.science.confgen import identity

HARTREE_TO_KCAL = identity.HARTREE_TO_KCAL


def _embedded(smiles: str) -> tuple[list[str], np.ndarray]:
    from rdkit import Chem
    from rdkit.Chem import rdDistGeom

    mol = Chem.AddHs(Chem.MolFromSmiles(smiles))
    params = rdDistGeom.ETKDGv3()
    params.randomSeed = 42
    assert rdDistGeom.EmbedMolecule(mol, params) == 0
    atoms = [atom.GetSymbol() for atom in mol.GetAtoms()]
    return atoms, np.array(mol.GetConformer().GetPositions(), dtype=float)


def _record(identifier: str, atoms: list[str], coords: np.ndarray) -> StructureRecord:
    return StructureRecord(
        id=identifier,
        atoms=tuple(atoms),
        coordinates=tuple(tuple(float(x) for x in row) for row in coords),
        charge=0,
        multiplicity=1,
    )


def _energy(subject_id: str, hartree: float) -> ScientificResult:
    return ScientificResult(
        kind="energy", value=hartree, unit=Unit.HARTREE, subject_structure_id=subject_id
    )


def _refine(records, native=None, results=None):
    return TransformExecutor()._refine(records, dict(native or {}), results)


def _kept_ids(records, native=None, results=None) -> list[str]:
    kept, _notes = _refine(records, native, results)
    return [record.id for record in kept]


@pytest.fixture(scope="module")
def chiral():
    return _embedded("CC[C@H](O)Cl")


def test_default_threshold_is_the_shared_identity_tolerance() -> None:
    assert REFINE_DEFAULT_RMSD_THRESHOLD_ANGSTROM == identity.R_TOL == 0.2


def test_mirror_image_merges_by_default_and_stays_apart_without_mirror(chiral) -> None:
    atoms, coords = chiral
    mirrored = coords * np.array([-1.0, 1.0, 1.0])
    records = [_record("a", atoms, coords), _record("b", atoms, mirrored)]
    assert _kept_ids(records) == ["a"]
    assert _kept_ids(records, {"mirror": True}) == ["a"]
    assert _kept_ids(records, {"mirror": False}) == ["a", "b"]


def test_identical_geometry_with_one_kcal_gap_keeps_both(chiral) -> None:
    atoms, coords = chiral
    records = [_record("a", atoms, coords), _record("b", atoms, coords)]
    results = ResultSet((_energy("a", -76.0), _energy("b", -76.0 + 1.0 / HARTREE_TO_KCAL)))
    assert _kept_ids(records, results=results) == ["a", "b"]


def test_identical_geometry_with_tenth_kcal_gap_keeps_the_lower_energy_record(chiral) -> None:
    atoms, coords = chiral
    records = [_record("a", atoms, coords), _record("b", atoms, coords)]
    # "b" has the larger id but the lower energy: it must be the survivor.
    results = ResultSet((_energy("a", -76.0), _energy("b", -76.0 - 0.1 / HARTREE_TO_KCAL)))
    kept, notes = _refine(records, results=results)
    assert [record.id for record in kept] == ["b"]
    assert any("dropped a as duplicate of b" in note for note in notes)


def test_without_energies_the_first_id_survives(chiral) -> None:
    atoms, coords = chiral
    records = [_record("b", atoms, coords), _record("a", atoms, coords)]
    assert _kept_ids(records) == ["a"]


def test_relabelling_that_moves_the_bonds_is_merged_through_the_graph_mapping() -> None:
    # Swapping the coordinates of the two carbons of ethanol gives the same molecule
    # under different indices: the perceived bonding graph is isomorphic to the
    # original's with labels 0 and 1 exchanged, so the fixed-index RMSD is large but the
    # graph mapping recovers the original geometry exactly.
    atoms, coords = _embedded("CCO")
    assert atoms[:2] == ["C", "C"]
    swapped = coords[[1, 0, *range(2, len(atoms))]]
    records = [_record("a", atoms, coords), _record("b", atoms, swapped)]
    kept, notes = _refine(records)
    assert [record.id for record in kept] == ["a"]
    assert any("dropped b as duplicate of a" in note for note in notes)
    assert not any("unresolved" in note for note in notes)
