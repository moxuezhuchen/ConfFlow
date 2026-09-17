#!/usr/bin/env python3

"""Topology inference is shared and consistent across consumers."""

from __future__ import annotations

import numpy as np
import pytest

from confflow.blocks.refine.rmsd_engine import get_topology_hash_worker
from confflow.blocks.refine.topology import build_graph
from confflow.core.bonding import (
    UnknownElementError,
    build_adjacency,
    covalent_radius,
    infer_bond_pairs,
)
from confflow.core.chem_validation import load_mol_from_xyz

_WATER_ATOMS = ["O", "H", "H"]
_WATER_COORDS = [[0.0, 0.0, 0.0], [0.96, 0.0, 0.0], [-0.24, 0.93, 0.0]]
_WATER_NUMBERS = [8, 1, 1]


def _edge_set(adjacency) -> set[frozenset[int]]:
    return {frozenset((i, j)) for i, row in enumerate(adjacency) for j in row}


def test_covalent_radius_lookup():
    assert covalent_radius(6) == pytest.approx(0.77)
    assert covalent_radius(0) is None
    assert covalent_radius(9999) is None


def test_minimum_distance_safeguard():
    assert infer_bond_pairs([6, 6], [[0.0, 0.0, 0.0], [0.1, 0.0, 0.0]], bond_scale=1.2) == []
    assert infer_bond_pairs([6, 6], [[0.0, 0.0, 0.0], [1.5, 0.0, 0.0]], bond_scale=1.2) == [(0, 1)]


def test_unknown_element_fails_closed():
    with pytest.raises(UnknownElementError):
        infer_bond_pairs([0, 1], [[0.0, 0.0, 0.0], [1.0, 0.0, 0.0]], bond_scale=1.2)


def test_same_scale_yields_same_topology_across_consumers(tmp_path):
    scale = 1.2
    adjacency = build_adjacency(_WATER_NUMBERS, _WATER_COORDS, bond_scale=scale)
    expected = _edge_set(adjacency)
    assert expected  # sanity: water has O-H bonds

    # refine graph layer
    graph_build = build_graph(_WATER_ATOMS, np.array(_WATER_COORDS), bond_scale=scale)
    assert graph_build.status == "ok"
    assert graph_build.graph is not None
    assert _edge_set(graph_build.graph.adjacency) == expected

    # confgen/chem_validation RDKit loader
    xyz = tmp_path / "water.xyz"
    lines = [str(len(_WATER_ATOMS)), "water"]
    lines += [f"{atom} {x} {y} {z}" for atom, (x, y, z) in zip(_WATER_ATOMS, _WATER_COORDS)]
    xyz.write_text("\n".join(lines) + "\n", encoding="utf-8")

    mol = load_mol_from_xyz(str(xyz), scale)
    mol_edges = {
        frozenset((bond.GetBeginAtomIdx(), bond.GetEndAtomIdx())) for bond in mol.GetBonds()
    }
    assert mol_edges == expected


def test_topology_hash_worker_consistent_and_fails_closed():
    coords = np.array(_WATER_COORDS, dtype=np.float64)
    first = get_topology_hash_worker((_WATER_ATOMS, coords))
    second = get_topology_hash_worker((_WATER_ATOMS, coords))
    assert first == second
    assert first != "error"

    assert get_topology_hash_worker((["X", "H"], coords[:2])) == "error"
