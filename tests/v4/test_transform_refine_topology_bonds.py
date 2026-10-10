#!/usr/bin/env python3

"""``refine`` with a declared ``topology_bonds`` (C5.3c-1).

``topology_bonds`` is a ConfGen v3 ``topology`` (``bonds`` or ``add_bond`` /
``del_bond``, typed edges, ``atoms``, ``index_base``) plus ``coordination`` and
``bond_scale``.  ``refine`` builds the graph through the very function ConfGen
uses, so one declaration must give one edge set on both routes, and a mapping
must preserve the typed non-covalent edges.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from confflow.domain.errors import DomainError
from confflow.domain.structure import StructureRecord
from confflow.execution.transform_executor import TransformExecutor
from confflow.science.confgen.search_spec import normalize_search_spec
from confflow.science.confgen.topology import build_typed_graph

DATA = Path(__file__).resolve().parent.parent / "science" / "data" / "molecules_h.json"
THRESHOLD = 0.25


def _butane() -> tuple[list[str], np.ndarray]:
    payload = json.loads(DATA.read_text(encoding="utf-8"))["butane"]
    return list(payload["atoms"]), np.array(payload["coords"], dtype=float)


def _record(identifier: str, atoms: list[str], coords: np.ndarray) -> StructureRecord:
    return StructureRecord(
        id=identifier,
        atoms=tuple(atoms),
        coordinates=tuple(tuple(float(x) for x in row) for row in coords),
        charge=0,
        multiplicity=1,
    )


def _iron_square() -> StructureRecord:
    corners = [(0.0, 0.0, 0.0), (1.5, 0.0, 0.0), (1.5, 1.5, 0.0), (0.0, 1.5, 0.0)]
    return _record("fe", ["C", "C", "C", "C", "Fe"], np.array([*corners, (0.75, 0.75, 1.9)]))


def _butane_bonds_1based(atoms: list[str], coords: np.ndarray) -> list[list[int]]:
    from confflow.science.topology_mapping import build_graph

    graph = build_graph(atoms, coords, bond_scale=1.15).graph
    return [[i + 1, j + 1] for i, row in enumerate(graph.adjacency) for j in row if i < j]


def _edges_via_confgen(record: StructureRecord, spec: dict[str, Any]) -> set[tuple[int, int, str]]:
    """Return the edges of the ConfGen route: normalise the spec, build the typed graph."""
    resolved = normalize_search_spec(spec)
    _adjacency, graph = build_typed_graph(record, resolved.get("topology", {}), resolved)
    return {(edge.a, edge.b, edge.type.value) for edge in graph.edges}


def _edges_via_refine(
    record: StructureRecord, topology_bonds: dict[str, Any]
) -> set[tuple[int, int, str]]:
    """Return the edges of the refine route: the comparison graph the executor builds."""
    declared = TransformExecutor._declared_topology({"topology_bonds": topology_bonds})
    graph = TransformExecutor._frame(record, 1.2, declared)["graph"]
    covalent = {(i, j, "COVALENT") for i, row in enumerate(graph.adjacency) for j in row if i < j}
    return covalent | set(graph.typed_edges)


def _spec_of(topology_bonds: dict[str, Any]) -> dict[str, Any]:
    spec: dict[str, Any] = {
        "schema_version": 4,
        "index_base": topology_bonds.get("index_base", 1),
        "topology": {
            key: topology_bonds[key]
            for key in ("bonds", "add_bond", "del_bond", "atoms")
            if key in topology_bonds
        },
    }
    if "coordination" in topology_bonds:
        spec["coordination"] = topology_bonds["coordination"]
    if "bond_scale" in topology_bonds:
        spec["tolerances"] = {"bond_scale": topology_bonds["bond_scale"]}
    return spec


def _declarations() -> list[tuple[str, StructureRecord, dict[str, Any]]]:
    atoms, coords = _butane()
    butane = _record("b", atoms, coords)
    bonds = _butane_bonds_1based(atoms, coords)
    return [
        (
            "bonds-with-forming",
            butane,
            {"bonds": [*bonds, {"atoms": [1, 12], "kind": "FORMING"}]},
        ),
        (
            "bonds-with-breaking-0-based",
            butane,
            {
                "index_base": 0,
                "bonds": [
                    *[[i - 1, j - 1] for i, j in bonds if (i, j) != (1, 2)],
                    {"atoms": [0, 1], "kind": "BREAKING"},
                ],
            },
        ),
        (
            "add-and-del",
            butane,
            {
                "add_bond": [{"atoms": [1, 4], "kind": "FORMING"}],
                "del_bond": [[1, 2]],
            },
        ),
        (
            "coordination-scope",
            _iron_square(),
            {"coordination": {"metal_center": 5, "binding_sites": [{"atoms": [1, 2, 3, 4]}]}},
        ),
        ("explicit-bond-scale", butane, {"add_bond": [[1, 4]], "bond_scale": 1.3}),
    ]


@pytest.mark.parametrize(
    ("record", "topology_bonds"),
    [
        pytest.param(r, t, id=name)
        for name, r, t in _declarations()
        if name not in {"bonds-with-breaking-0-based", "add-and-del"}
    ],
)
def test_one_declaration_gives_the_same_edge_set_on_both_routes(record, topology_bonds) -> None:
    via_confgen = _edges_via_confgen(record, _spec_of(topology_bonds))
    via_refine = _edges_via_refine(record, topology_bonds)
    assert via_confgen == via_refine
    assert via_refine, "the declaration must produce edges"


def test_the_declared_kinds_really_appear() -> None:
    by_name = {name: (record, topology) for name, record, topology in _declarations()}
    forming = _edges_via_refine(*by_name["bonds-with-forming"])
    assert (0, 11, "FORMING") in forming
    breaking = _edges_via_refine(*by_name["bonds-with-breaking-0-based"])
    assert (0, 1, "BREAKING") in breaking
    coordination = _edges_via_refine(*by_name["coordination-scope"])
    assert {edge for edge in coordination if edge[2] == "COORDINATION"} == {
        (0, 4, "COORDINATION"),
        (1, 4, "COORDINATION"),
        (2, 4, "COORDINATION"),
        (3, 4, "COORDINATION"),
    }


def test_index_base_zero_and_one_describe_the_same_topology() -> None:
    atoms, coords = _butane()
    record = _record("b", atoms, coords)
    one = {"add_bond": [{"atoms": [1, 4], "kind": "FORMING"}], "index_base": 1}
    zero = {"add_bond": [{"atoms": [0, 3], "kind": "FORMING"}], "index_base": 0}
    assert _edges_via_refine(record, one) == _edges_via_refine(record, zero)
    assert _edges_via_refine(record, {"add_bond": one["add_bond"]}) == _edges_via_refine(
        record, one
    )


def _relabelled_pair(forming: tuple[int, int]) -> tuple[list[StructureRecord], dict[str, Any]]:
    """Two records with identical geometry, the second with two H labels swapped.

    ``forming`` (1-based) names the FORMING pair of the shared declaration.  Both
    labellings are valid descriptions of the same coordinates, so the declaration
    attaches the reactive edge to different physical atoms in the two records.
    """
    atoms, coords = _butane()
    swapped = list(range(len(atoms)))
    swapped[11], swapped[12] = swapped[12], swapped[11]  # two hydrogens of one methyl
    declaration = {
        "bonds": [
            *_butane_bonds_1based(atoms, coords),
            {"atoms": list(forming), "kind": "FORMING"},
        ]
    }
    return [_record("a", atoms, coords), _record("b", atoms, coords[swapped])], declaration


def test_a_reactive_edge_on_a_different_physical_atom_prevents_the_merge() -> None:
    records, declaration = _relabelled_pair((1, 12))
    # Control: without the reactive edge the symmetric relabelling merges.
    plain, _ = TransformExecutor()._refine(records, {})
    assert [record.id for record in plain] == ["a"]
    # With the typed edge the two records describe different reaction situations.
    kept, _ = TransformExecutor()._refine(records, {"topology_bonds": declaration})
    assert [record.id for record in kept] == ["a", "b"]


def test_a_symmetric_reactive_edge_still_merges_equivalent_relabellings() -> None:
    atoms, coords = _butane()
    swapped = list(range(len(atoms)))
    swapped[11], swapped[12] = swapped[12], swapped[11]
    records = [_record("a", atoms, coords), _record("b", atoms, coords[swapped])]
    declaration = {
        "bonds": [
            *_butane_bonds_1based(atoms, coords),
            {"atoms": [1, 4], "kind": "FORMING"},  # carbon-carbon: untouched by the swap
        ]
    }
    kept, _ = TransformExecutor()._refine(records, {"topology_bonds": declaration})
    assert [record.id for record in kept] == ["a"]


def test_without_topology_bonds_the_perceived_behaviour_is_unchanged() -> None:
    atoms, coords = _butane()
    swapped = list(range(len(atoms)))
    swapped[4], swapped[5] = swapped[5], swapped[4]
    records = [_record("a", atoms, coords), _record("b", atoms, coords[swapped])]
    assert [r.id for r in TransformExecutor()._refine(records, {})[0]] == ["a"]
    assert [r.id for r in TransformExecutor()._refine(records, {"bond_scale": 1.2})[0]] == ["a"]


@pytest.mark.parametrize(
    ("topology_bonds", "message"),
    [
        ("nope", "must be a mapping"),
        ({"surprise": 1}, "unknown members"),
        ({"index_base": 2, "add_bond": [[1, 4]]}, "invalid"),
        ({"add_bond": [[1, 99]]}, "does not fit"),
        ({"bonds": [[1, 2]], "atoms": [{"index": 99}]}, "does not fit"),
    ],
)
def test_invalid_declarations_fail_closed(topology_bonds, message) -> None:
    atoms, coords = _butane()
    records = [_record("a", atoms, coords)]
    with pytest.raises(DomainError, match=message):
        TransformExecutor()._refine(records, {"topology_bonds": topology_bonds})


def test_the_atom_count_of_the_structure_is_checked() -> None:
    atoms, coords = _butane()
    with pytest.raises(DomainError, match="does not fit"):
        TransformExecutor()._refine(
            [_record("a", atoms[:6], coords[:6])],
            {"topology_bonds": {"bonds": [[1, 2], [2, 13]]}},
        )


def test_bond_scale_must_not_be_given_twice() -> None:
    atoms, coords = _butane()
    with pytest.raises(DomainError, match="both bond_scale and topology_bonds"):
        TransformExecutor()._refine(
            [_record("a", atoms, coords)],
            {"bond_scale": 1.2, "topology_bonds": {"add_bond": [[1, 4]]}},
        )


def test_a_declared_topology_and_a_record_authoritative_graph_fail_closed() -> None:
    """Two topology authorities are refused, exactly as ConfGen refuses them."""
    import dataclasses

    from confflow.domain.topology import TopologyPatch

    atoms, coords = _butane()
    record = dataclasses.replace(
        _record("a", atoms, coords), topology_patch=TopologyPatch(add_edges=((1, 4),))
    )
    with pytest.raises(DomainError, match="declare one authority"):
        TransformExecutor()._refine([record], {"topology_bonds": {"add_bond": [[1, 12]]}})


def test_without_a_declaration_the_record_topology_decides_the_refine_graph() -> None:
    """The record-level working topology (input simplification) is the default source."""
    import dataclasses

    from confflow.domain.topology import TopologyPatch

    atoms, coords = _butane()
    plain = _record("a", atoms, coords)
    patched = dataclasses.replace(plain, id="b", topology_patch=TopologyPatch(add_edges=((1, 4),)))
    frames = [TransformExecutor._frame(record, 1.2) for record in (plain, patched)]
    assert frames[0]["graph"].adjacency != frames[1]["graph"].adjacency
    assert 3 in frames[1]["graph"].adjacency[0]
