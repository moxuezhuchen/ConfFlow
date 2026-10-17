#!/usr/bin/env python3
"""Input-shape search targets and reference-relative coordination audit (input-core WP2, WP5).

The fixtures are the experimental input-core structures; the reference of every audit is the
input itself. ``cd7_ntb`` carries a kappa2-nitrate N that sits near the metal without being a
donor, which must not be read as coordination.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

from confflow.science.bonds import perceive_adjacency
from confflow.science.confgen import search
from confflow.science.confgen.graph import (
    INPUT_SHAPE,
    AtomRef,
    BindingSite,
    CoordinationSpec,
    EdgeType,
    TypedEdge,
    TypedGraph,
)
from confflow.science.data import get_atomic_number

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "confgen" / "coordination"
INPUT_CORE = FIXTURES / "input_core"
NAMES = ("cd6_bpy3", "ti7_salan", "zr8_onn", "cd7_ntb")


def _load(name: str) -> tuple[dict, list[str], np.ndarray]:
    meta = json.loads((INPUT_CORE / "index.json").read_text())[name]
    lines = (INPUT_CORE / f"{name}.xyz").read_text().splitlines()
    rows = [line.split() for line in lines[2 : 2 + int(lines[0])]]
    elements = [row[0] for row in rows]
    coords = np.array([[float(v) for v in row[1:4]] for row in rows])
    return meta, elements, coords


def _build(name: str) -> tuple[TypedGraph, CoordinationSpec, np.ndarray]:
    meta, elements, xyz = _load(name)
    metal, donors = int(meta["metal_center0"]), [int(d) for d in meta["donors0"]]
    numbers = [get_atomic_number(e) for e in elements]
    adjacency = perceive_adjacency(numbers, xyz, bond_scale=1.15)
    edges = [
        TypedEdge(a=i, b=j, type=EdgeType.COVALENT)
        for i, row in enumerate(adjacency)
        for j in row
        if i < j and metal not in (i, j)
    ]
    edges += [TypedEdge(a=metal, b=d, type=EdgeType.COORDINATION) for d in donors]
    atoms = tuple(AtomRef(index=i, element=e) for i, e in enumerate(elements))
    graph = TypedGraph(atoms, tuple(edges), metal, (), name)
    sites = tuple(BindingSite(id=f"D{d}", atoms=(d,)) for d in donors)
    spec = CoordinationSpec(metal_center=metal, binding_sites=sites, shapes=(INPUT_SHAPE,))
    return graph, spec, xyz


def _failed(name: str, coords: np.ndarray) -> tuple[str, ...]:
    graph, spec, reference = _build(name)
    return search.audit_structure(coords, graph, spec, reference, INPUT_SHAPE).failed_checks


def test_plan_targets_input_shape_is_identity_single_class() -> None:
    for name in ("ti7_salan", "zr8_onn"):
        graph, spec, _ = _build(name)
        cn = spec.coordination_number
        targets = search.plan_targets(graph, spec, INPUT_SHAPE)
        assert len(targets) == 1
        target = targets[0]
        assert target.id == "t00"
        assert target.placement == tuple(range(cn))
        assert target.command == tuple(range(cn))
        assert target.trans_pairs == ()


@pytest.mark.parametrize("name", NAMES)
def test_input_audit_of_input_is_clean(name: str) -> None:
    graph, spec, xyz = _build(name)
    record = search.audit_structure(xyz, graph, spec, xyz, INPUT_SHAPE)
    assert record.failed_checks == ()
    assert record.actual_class == tuple(range(spec.coordination_number))


def test_non_donor_moved_into_coordination_is_flagged() -> None:
    graph, spec, xyz = _build("ti7_salan")
    metal = graph.metal_center
    assert metal is not None
    donors = set(spec.donor_indices)
    # a carbon two covalent bonds from a donor, far enough from the metal in the input
    carbon = None
    for d in spec.donor_indices:
        for mid in graph.neighbors(d, EdgeType.COVALENT):
            for c in graph.neighbors(mid, EdgeType.COVALENT):
                if (
                    graph.atoms[c].element == "C"
                    and c not in donors
                    and c != metal
                    and np.linalg.norm(xyz[c] - xyz[metal]) > 2.5
                ):
                    carbon = c
                    break
            if carbon is not None:
                break
        if carbon is not None:
            break
    assert carbon is not None
    direction = (xyz[carbon] - xyz[metal]) / np.linalg.norm(xyz[carbon] - xyz[metal])
    moved = xyz.copy()
    moved[carbon] = xyz[metal] + 2.1 * direction
    assert "coordination_class" in _failed("ti7_salan", moved)


def test_removed_donor_is_flagged_by_metal_donor_distance() -> None:
    graph, spec, xyz = _build("zr8_onn")
    metal = graph.metal_center
    assert metal is not None
    donor = spec.donor_indices[0]
    vec = xyz[donor] - xyz[metal]
    stretched = xyz.copy()
    stretched[donor] = xyz[metal] + vec * (1.0 + 0.5 / np.linalg.norm(vec))
    assert "metal_donor_distance" in _failed("zr8_onn", stretched)
