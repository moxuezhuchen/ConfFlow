#!/usr/bin/env python3

"""V4 ``refine`` recognises symmetry-equivalent relabellings (C5.3b).

Every structure here carries hydrogens.  ``refine`` must merge two records
that differ only by a symmetry-equivalent relabelling of atoms of the same
element (methyl hydrogens, the arms of a tert-butyl group, the two ortho/meta
positions of a ring) and must never merge records whose geometry really
differs.  The independent reference is RDKit's symmetry-aware
``rdMolAlign.GetBestRMS``: ``refine`` merges a pair exactly when RDKit's best
RMSD is below the threshold.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

from confflow.domain.errors import DomainError
from confflow.domain.structure import StructureRecord
from confflow.execution.transform_executor import (
    REFINE_DEFAULT_MAPPING_BUDGET,
    TransformExecutor,
)
from confflow.science.cluster import kabsch_rmsd
from confflow.science.topology_mapping import DEFAULT_MAPPING_NODE_BUDGET, build_graph

DATA = Path(__file__).resolve().parent.parent / "science" / "data" / "molecules_h.json"
THRESHOLD = 0.25

SMILES = {
    "butane": "CCCC",
    "butane_anti": "CCCC",
    "butane_gauche": "CCCC",
    "tbutanol": "CC(C)(C)O",
    "benzene": "c1ccccc1",
    "toluene": "Cc1ccccc1",
    "butanol2_anti": "CC(O)CC",
    "butanol2_gauche": "CC(O)CC",
}


def _molecule(name: str) -> tuple[list[str], np.ndarray]:
    payload = json.loads(DATA.read_text(encoding="utf-8"))[name]
    return list(payload["atoms"]), np.array(payload["coords"], dtype=float)


def _record(identifier: str, atoms: list[str], coords: np.ndarray) -> StructureRecord:
    return StructureRecord(
        id=identifier,
        atoms=tuple(atoms),
        coordinates=tuple(tuple(float(x) for x in row) for row in coords),
        charge=0,
        multiplicity=1,
    )


def _hydrogens(graph, carbon: int) -> list[int]:
    return [j for j in graph.adjacency[carbon] if graph.symbols[j] == "H"]


def _carbons_with(graph, carbon_neighbours: int) -> list[int]:
    return [
        i
        for i, symbol in enumerate(graph.symbols)
        if symbol == "C"
        and sum(graph.symbols[j] == "C" for j in graph.adjacency[i]) == carbon_neighbours
    ]


def _symmetric_permutation(name: str) -> list[int]:
    """Return a permutation of atom labels that is a symmetry of the molecular graph."""
    atoms, coords = _molecule(name)
    graph = build_graph(atoms, coords).graph
    permutation = list(range(len(atoms)))
    if name == "butane":
        first, last = _hydrogens(graph, 0), _hydrogens(graph, 3)
        permutation[first[0]], permutation[first[1]], permutation[first[2]] = (
            first[1],
            first[2],
            first[0],
        )
        permutation[last[0]], permutation[last[1]] = last[1], last[0]
    elif name == "tbutanol":
        quaternary = _carbons_with(graph, 3)[0]
        first, second, third = [j for j in graph.adjacency[quaternary] if graph.symbols[j] == "C"]
        permutation[first], permutation[second] = second, first
        for x, y in zip(_hydrogens(graph, first), _hydrogens(graph, second)):
            permutation[x], permutation[y] = y, x
        cycle = _hydrogens(graph, third)
        permutation[cycle[0]], permutation[cycle[1]], permutation[cycle[2]] = (
            cycle[1],
            cycle[2],
            cycle[0],
        )
    elif name == "toluene":
        ipso = _carbons_with(graph, 3)[0]
        ortho = [j for j in graph.adjacency[ipso] if _carbons_with_neighbour_count(graph, j) == 2]
        meta = [
            [k for k in graph.adjacency[o] if graph.symbols[k] == "C" and k != ipso][0]
            for o in ortho
        ]
        for x, y in ((ortho[0], ortho[1]), (meta[0], meta[1])):
            permutation[x], permutation[y] = y, x
            hx, hy = _hydrogens(graph, x)[0], _hydrogens(graph, y)[0]
            permutation[hx], permutation[hy] = hy, hx
    elif name == "benzene":
        carbons = [i for i, symbol in enumerate(graph.symbols) if symbol == "C"]
        ring = [carbons[0]]
        while len(ring) < 6:
            ring.append(
                [j for j in graph.adjacency[ring[-1]] if graph.symbols[j] == "C" and j not in ring][
                    0
                ]
            )
        for k in range(6):
            target = ring[(k + 2) % 6]
            permutation[ring[k]] = target
            permutation[_hydrogens(graph, ring[k])[0]] = _hydrogens(graph, target)[0]
    else:  # pragma: no cover - guard against a typo in the parametrisation
        raise AssertionError(name)
    return permutation


def _carbons_with_neighbour_count(graph, index: int) -> int:
    return (
        sum(graph.symbols[j] == "C" for j in graph.adjacency[index])
        if graph.symbols[index] == "C"
        else -1
    )


def _kept_ids(records: list[StructureRecord], **native) -> list[str]:
    kept, _notes = TransformExecutor()._refine(records, native)
    return [record.id for record in kept]


def _rdkit_best_rms(name: str, coords_a: np.ndarray, coords_b: np.ndarray) -> float:
    """Symmetry-aware RMSD of two coordinate sets of the same molecule (RDKit)."""
    from rdkit import Chem
    from rdkit.Chem import rdMolAlign
    from rdkit.Geometry import Point3D

    mol = Chem.AddHs(Chem.MolFromSmiles(SMILES[name]))

    def with_coordinates(coords: np.ndarray):
        copy = Chem.Mol(mol)
        conformer = Chem.Conformer(copy.GetNumAtoms())
        for index, (x, y, z) in enumerate(coords):
            conformer.SetAtomPosition(index, Point3D(float(x), float(y), float(z)))
        copy.RemoveAllConformers()
        copy.AddConformer(conformer, assignId=True)
        return copy

    return float(rdMolAlign.GetBestRMS(with_coordinates(coords_b), with_coordinates(coords_a)))


@pytest.mark.parametrize("name", ["butane", "tbutanol", "toluene", "benzene"])
def test_symmetric_relabelling_is_merged_and_agrees_with_rdkit(name: str) -> None:
    atoms, coords = _molecule(name)
    permutation = _symmetric_permutation(name)
    relabelled = coords[permutation]
    assert _rdkit_best_rms(name, coords, relabelled) < THRESHOLD
    # For the non-trivial relabellings the fixed-index RMSD is large, so a merge
    # can only come from the legal-mapping search (benzene's ring rotation is a
    # rigid symmetry of the coordinates and stays small).
    fixed_index = kabsch_rmsd(coords, relabelled)
    assert (fixed_index > THRESHOLD) is (name != "benzene")
    records = [_record("a", atoms, coords), _record("b", atoms, relabelled)]
    assert _kept_ids(records, rmsd_threshold_angstrom=THRESHOLD) == ["a"]


@pytest.mark.parametrize(
    ("first", "second"),
    [("butane_anti", "butane_gauche"), ("butanol2_anti", "butanol2_gauche")],
)
def test_different_conformers_are_never_merged_and_agree_with_rdkit(first, second) -> None:
    atoms, coords_a = _molecule(first)
    _atoms, coords_b = _molecule(second)
    assert _rdkit_best_rms(first, coords_a, coords_b) > THRESHOLD
    records = [_record("a", atoms, coords_a), _record("b", atoms, coords_b)]
    assert _kept_ids(records, rmsd_threshold_angstrom=THRESHOLD) == ["a", "b"]


def test_symmetric_relabelling_of_one_conformer_does_not_hide_a_different_one() -> None:
    atoms, anti = _molecule("butane_anti")
    _atoms, gauche = _molecule("butane_gauche")
    graph = build_graph(atoms, anti).graph
    first = _hydrogens(graph, 0)
    permutation = list(range(len(atoms)))
    permutation[first[0]], permutation[first[1]], permutation[first[2]] = (
        first[1],
        first[2],
        first[0],
    )
    records = [
        _record("a", atoms, anti),
        _record("b", atoms, gauche),
        _record("c", atoms, anti[permutation]),
    ]
    assert _kept_ids(records, rmsd_threshold_angstrom=THRESHOLD) == ["a", "b"]


def test_result_does_not_depend_on_input_order() -> None:
    atoms, coords = _molecule("butane")
    permutation = _symmetric_permutation("butane")
    records = [_record("a", atoms, coords), _record("b", atoms, coords[permutation])]
    assert _kept_ids(records) == _kept_ids(list(reversed(records))) == ["a"]


def test_exhausted_mapping_budget_keeps_both_and_says_so() -> None:
    atoms, coords = _molecule("butane")
    permutation = _symmetric_permutation("butane")
    records = [_record("a", atoms, coords), _record("b", atoms, coords[permutation])]
    kept, notes = TransformExecutor()._refine(records, {"mapping_budget": 5})
    assert [record.id for record in kept] == ["a", "b"]
    assert any("unresolved" in note and "b" in note for note in notes)


def test_threshold_zero_merges_nothing_because_the_comparison_is_strict() -> None:
    atoms, coords = _molecule("butane")
    records = [_record("a", atoms, coords), _record("b", atoms, coords)]
    assert _kept_ids(records, rmsd_threshold_angstrom=0.0) == ["a", "b"]
    assert _kept_ids(records) == ["a"]


@pytest.mark.parametrize("budget", [-1, True, 1.5, "10"])
def test_mapping_budget_must_be_a_non_negative_integer(budget) -> None:
    atoms, coords = _molecule("butane")
    with pytest.raises(DomainError, match="mapping_budget"):
        TransformExecutor()._refine([_record("a", atoms, coords)], {"mapping_budget": budget})


def test_default_mapping_budget_is_the_science_default() -> None:
    assert REFINE_DEFAULT_MAPPING_BUDGET == DEFAULT_MAPPING_NODE_BUDGET == 1000
