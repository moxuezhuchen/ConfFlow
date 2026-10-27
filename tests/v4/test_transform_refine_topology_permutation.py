#!/usr/bin/env python3

"""``refine`` groups renumbered working topologies together and keeps different graphs apart.

The grouping key used to hold the raw ``working_topology`` adjacency, so the same molecule
with its atoms renumbered landed in different groups before the graph isomorphism check ran.
The key now carries a renumbering-invariant fingerprint; the isomorphism check in ``_cluster``
still decides, so equal fingerprints alone never merge two structures.
"""

from __future__ import annotations

import numpy as np

from confflow.domain.structure import StructureRecord
from confflow.execution.transform_executor import TransformExecutor


def _ethanol_with_hydrogens() -> tuple[tuple[str, ...], np.ndarray, tuple[tuple[int, ...], ...]]:
    from rdkit import Chem
    from rdkit.Chem import AllChem

    mol = Chem.AddHs(Chem.MolFromSmiles("CCO"))
    assert AllChem.EmbedMolecule(mol, randomSeed=42) == 0
    atoms = tuple(atom.GetSymbol() for atom in mol.GetAtoms())
    graph = tuple(
        tuple(sorted(neighbour.GetIdx() for neighbour in atom.GetNeighbors()))
        for atom in mol.GetAtoms()
    )
    return atoms, np.array(mol.GetConformer().GetPositions(), dtype=float), graph


def _renumber(graph: tuple[tuple[int, ...], ...], perm: list[int]) -> tuple[tuple[int, ...], ...]:
    """Return ``graph`` with atom ``perm[new]`` relabelled as ``new``."""
    inverse = {old: new for new, old in enumerate(perm)}
    return tuple(tuple(sorted(inverse[j] for j in graph[old])) for old in perm)


def _record(
    name: str,
    atoms: tuple[str, ...],
    coords: np.ndarray,
    topology: tuple[tuple[int, ...], ...],
) -> StructureRecord:
    return StructureRecord(
        name,
        atoms,
        coords.tolist(),
        charge=0,
        multiplicity=1,
        working_topology=topology,
    )


def test_renumbered_working_topology_is_one_conformer() -> None:
    atoms, xyz, graph = _ethanol_with_hydrogens()
    perm = list(range(len(atoms)))
    perm[0], perm[1] = perm[1], perm[0]
    a = _record("a", atoms, xyz, graph)
    b = _record("b", tuple(atoms[i] for i in perm), xyz[perm], _renumber(graph, perm))

    kept, notes = TransformExecutor()._refine([a, b], {})

    assert [record.id for record in kept] == ["a"], notes


def test_different_working_topology_with_same_atoms_is_not_merged() -> None:
    atoms, xyz, graph = _ethanol_with_hydrogens()
    # Move one hydrogen from its carbon to the oxygen; same atoms and coordinates.
    hydrogen = next(
        i
        for i, row in enumerate(graph)
        if atoms[i] == "H" and len(row) == 1 and atoms[row[0]] == "C"
    )
    carbon = graph[hydrogen][0]
    oxygen = next(i for i, symbol in enumerate(atoms) if symbol == "O")
    moved = [list(row) for row in graph]
    moved[hydrogen] = [oxygen]
    moved[carbon] = [j for j in moved[carbon] if j != hydrogen]
    moved[oxygen] = sorted([*moved[oxygen], hydrogen])
    moved_graph = tuple(tuple(row) for row in moved)
    assert moved_graph != graph

    a = _record("a", atoms, xyz, graph)
    b = _record("b", atoms, xyz, moved_graph)

    kept, notes = TransformExecutor()._refine([a, b], {})

    assert sorted(record.id for record in kept) == ["a", "b"], notes


def test_equal_fingerprint_non_isomorphic_graphs_are_not_merged() -> None:
    # A 6-ring and two 3-rings: every carbon has two carbon neighbours, so the local
    # fingerprints agree, but the bonding graphs are not isomorphic.
    atoms = ("C",) * 6
    coords = np.arange(18, dtype=float).reshape(6, 3) * 0.1
    ring6 = ((1, 5), (0, 2), (1, 3), (2, 4), (3, 5), (0, 4))
    two_rings3 = ((1, 2), (0, 2), (0, 1), (4, 5), (3, 5), (3, 4))

    a = _record("a", atoms, coords, ring6)
    b = _record("b", atoms, coords, two_rings3)

    kept, notes = TransformExecutor()._refine([a, b], {})

    assert sorted(record.id for record in kept) == ["a", "b"], notes
