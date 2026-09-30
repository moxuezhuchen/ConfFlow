"""Realistic atom counts and fragment invariants for conformer generation."""

from __future__ import annotations

import numpy as np
import pytest
from rdkit import Chem
from rdkit.Chem import AllChem

from confflow.blocks.confgen.rotations import _build_chain_rotations
from confflow.domain import FrozenDict, StructureRecord
from confflow.domain.completion import WorkItemStatus
from confflow.execution.confgen_executor import ConfgenExecutor
from confflow.science.bonds import covalent_radii
from tests.v4.test_repair_executors import _ctx, _item, _sci


@pytest.mark.parametrize("smiles", ["CCCC", "CCCCC", "CCCC.O"])
def test_generation_preserves_bonds_and_unrelated_fragments(tmp_path, smiles):
    mol = Chem.AddHs(Chem.MolFromSmiles(smiles))
    assert AllChem.EmbedMolecule(mol, randomSeed=42) == 0
    assert AllChem.MMFFOptimizeMolecule(mol, maxIters=1000) == 0
    original = mol.GetConformer().GetPositions()
    fragments = Chem.GetMolFrags(mol)
    if len(fragments) > 1:
        original[list(fragments[1])] += [10, 3, 2]
    for index, point in enumerate(original):
        mol.GetConformer().SetAtomPosition(index, tuple(point))
    rec = StructureRecord(
        id="seed",
        atoms=tuple(atom.GetSymbol() for atom in mol.GetAtoms()),
        coordinates=tuple(tuple(point) for point in original),
        charge=0,
        multiplicity=1,
    )
    sci = _sci(seed=42, native=FrozenDict({"chains": ["2-3"], "chain_angles": "0,90"}))
    result = ConfgenExecutor().execute(_item("scan:seed", "scan", [rec]), _ctx(sci, str(tmp_path)))
    assert result.status is WorkItemStatus.COMPLETED, result.diagnostics
    assert len(result.structures) == 2
    assert not np.allclose(result.structures[1].coordinates, original)
    for member in result.structures:
        xyz = np.asarray(member.coordinates)
        for bond in mol.GetBonds():
            first, second = bond.GetBeginAtomIdx(), bond.GetEndAtomIdx()
            assert np.linalg.norm(xyz[first] - xyz[second]) == pytest.approx(
                np.linalg.norm(original[first] - original[second]), abs=1e-12
            )
        if len(fragments) > 1:
            np.testing.assert_allclose(xyz[list(fragments[1])], original[list(fragments[1])])
    legacy_bonds, _ = _build_chain_rotations(mol, [[1, 2]], [[[0, 90]]], None, "left")
    if len(fragments) > 1:
        assert set(legacy_bonds[0][2]).isdisjoint(fragments[1])


def test_radii_are_positive_and_match_every_atom():
    numbers = [6, 6, 8, 1, 1, 1, 1, 1, 1]
    radii = covalent_radii(numbers)
    assert len(radii) == len(numbers)
    assert all(radius > 0 for radius in radii)
    assert radii[0] == radii[1]
    assert radii[3:] == [radii[3]] * 6
