#!/usr/bin/env python3

"""DG seeds for ``shape="input"`` (input-core template and input-admitting bounds)."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

from confflow.domain.elements import atomic_number
from confflow.science.bonds import perceive_adjacency
from confflow.science.confgen.dg_seed import DGSeedError, DGSeedSettings, generate_dg_seeds
from confflow.science.confgen.graph import (
    INPUT_SHAPE,
    AtomRef,
    BindingSite,
    CoordinationSpec,
    EdgeType,
    TypedEdge,
    TypedGraph,
    load_xyz_frame,
)

INPUT_CORE = Path(__file__).resolve().parents[1] / "fixtures/confgen/coordination/input_core"
FAILED_BEFORE_FIX = ("cd6_bpy3", "ti7_salan", "zr8_onn")
DIST_TOL = 0.05
ANGLE_TOL_DEG = 10.0


def _load_input_core(name: str):
    """Return ``(graph, spec, reference, donors)`` for one input-core fixture."""
    index = json.loads((INPUT_CORE / "index.json").read_text())
    entry = index[name]
    donors = tuple(int(d) for d in entry["donors0"])
    elements, coords = load_xyz_frame(INPUT_CORE / f"{name}.xyz")
    reference = np.asarray(coords, dtype=float)
    numbers = [atomic_number(symbol) for symbol in elements]
    neighbours = perceive_adjacency(numbers, reference.tolist(), bond_scale=1.15)
    metal = int(entry["metal_center0"])
    edges: list[TypedEdge] = []
    for first, peers in enumerate(neighbours):
        for second in peers:
            if first < second and first != metal and second != metal:
                edges.append(TypedEdge(first, second, EdgeType.COVALENT))
    edges.extend(TypedEdge(metal, donor, EdgeType.COORDINATION) for donor in donors)
    atoms = tuple(
        AtomRef(index=i, element=symbol, role="metal_center" if i == metal else "")
        for i, symbol in enumerate(elements)
    )
    graph = TypedGraph(atoms=atoms, edges=tuple(edges), metal_center=metal)
    spec = CoordinationSpec(
        metal_center=metal,
        binding_sites=tuple(BindingSite(id=f"D{donor}", atoms=(donor,)) for donor in donors),
        shapes=(INPUT_SHAPE,),
    )
    return graph, spec, reference, donors


def _angle_deg(coords: np.ndarray, first: int, centre: int, second: int) -> float:
    a = coords[first] - coords[centre]
    b = coords[second] - coords[centre]
    cosine = float(np.dot(a, b) / (np.linalg.norm(a) * np.linalg.norm(b)))
    return float(np.degrees(np.arccos(np.clip(cosine, -1.0, 1.0))))


@pytest.mark.parametrize("name", FAILED_BEFORE_FIX)
def test_input_core_starts_reproduce_input_core(name: str) -> None:
    graph, spec, reference, donors = _load_input_core(name)
    settings = DGSeedSettings(count=4, random_seed=7, timeout_seconds=60)
    result = generate_dg_seeds(
        graph, spec, reference, list(range(len(donors))), INPUT_SHAPE, settings
    )
    assert len(result.coords) >= 3
    metal = spec.metal_center
    for start in result.coords:
        for donor in donors:
            ref_dist = np.linalg.norm(reference[metal] - reference[donor])
            start_dist = np.linalg.norm(start[metal] - start[donor])
            assert abs(start_dist - ref_dist) <= DIST_TOL
        for i, first in enumerate(donors):
            for second in donors[i + 1 :]:
                ref_angle = _angle_deg(reference, first, metal, second)
                start_angle = _angle_deg(start, first, metal, second)
                assert abs(start_angle - ref_angle) <= ANGLE_TOL_DEG


def test_input_shape_rejects_non_identity_placement() -> None:
    graph, spec, reference, _ = _load_input_core("cd6_bpy3")
    placement = [1, 0, 2, 3, 4, 5]
    with pytest.raises(DGSeedError, match="shape 'input' takes the identity placement"):
        generate_dg_seeds(graph, spec, reference, placement, INPUT_SHAPE, DGSeedSettings(count=1))
