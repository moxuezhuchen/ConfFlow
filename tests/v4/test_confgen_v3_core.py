#!/usr/bin/env python3

"""ConfGen typed-topology tests (search lane).

Covers the surviving graph-construction surface: v4 spec normalization and its
index-base rule, the typed graph built from explicit bonds, perception with
``add_bond``/``del_bond`` corrections, declared coordination metal-donor typing,
the schema-4 wire, and pure coordination enumeration over the built graph.
The engine, ring, torsion, sampling, certificate and state-key tests of the
removed engines were deleted with those engines.
"""

from __future__ import annotations

from typing import Any

import pytest

from confflow.domain import StructureRecord
from confflow.science.confgen.graph import BindingSite, CoordinationSpec, EdgeType
from confflow.science.confgen.search_spec import build_search_context, normalize_search_spec
from confflow.workflow.v4.confgen_schema import ConfgenModelV3
from tests.v4._helpers.repair import _butane


def _pentane(struct_id: str) -> StructureRecord:
    """Five-carbon chain with two measurable interior bonds."""
    return StructureRecord(
        id=struct_id,
        atoms=("C", "C", "C", "C", "C"),
        coordinates=(
            (0.0, 0.0, 0.0),
            (1.5, 0.0, 0.0),
            (3.0, 0.4, 0.0),
            (4.5, 0.4, 0.0),
            (6.0, 0.8, 0.0),
        ),
        charge=0,
        multiplicity=1,
    )


def _tetra_mn4() -> tuple[StructureRecord, dict[str, Any]]:
    """Synthetic Fe(N)4 with explicit COORDINATION edges (1-based spec)."""
    directions = [(1, 1, 1), (1, -1, -1), (-1, 1, -1), (-1, -1, 1)]
    coords = [(0.0, 0.0, 0.0)] + [
        (2.0 * d[0] / 1.732, 2.0 * d[1] / 1.732, 2.0 * d[2] / 1.732) for d in directions
    ]
    record = StructureRecord(
        id="mn4",
        atoms=("Fe", "N", "N", "N", "N"),
        coordinates=tuple(coords),
        charge=0,
        multiplicity=1,
    )
    spec = {
        "schema_version": 4,
        "index_base": 1,
        "seed": 3,
        "topology": {"bonds": [{"atoms": [1, i], "kind": "COORDINATION"} for i in (2, 3, 4, 5)]},
        "coordination": {
            "metal_center": 1,
            "shapes": ["tetrahedral"],
            "binding_sites": [
                {"id": f"s{i}", "kind": "atom", "atoms": [i + 1], "hapticity": 1}
                for i in range(1, 5)
            ],
        },
    }
    return record, spec


def _edge_kind(graph: Any, first: int, second: int) -> EdgeType | None:
    """Return the typed edge kind joining two atoms (any kind), if any."""
    low, high = (first, second) if first <= second else (second, first)
    for edge in graph.edges:
        if (edge.a, edge.b) == (low, high):
            return edge.type
    return None


def _build(record: StructureRecord, spec: dict[str, Any]) -> Any:
    """Build the search context (adjacency plus typed graph) for one spec."""
    return build_search_context(record, spec)


def test_normalization_idempotent_topology_and_coordination():
    """Repeated normalization is a validated no-op on every section."""
    raw = {
        "schema_version": 4,
        "index_base": 1,
        "topology": {
            "bonds": [
                [1, 2],
                {"atoms": [3, 4], "kind": "FORMING"},
                {"atoms": [4, 5], "kind": "BREAKING", "bond_order": 1.5},
            ]
        },
        "coordination": {
            "metal_center": 6,
            "shapes": ["octahedral"],
            "binding_sites": [{"id": "s", "kind": "atom", "atoms": [1], "hapticity": 1}],
        },
        "seed": 11,
    }
    once = normalize_search_spec(raw)
    assert normalize_search_spec(once) == once == normalize_search_spec(normalize_search_spec(once))
    assert once["index_base"] == 0
    assert "index_base" not in once["topology"]
    assert once["topology"]["bonds"][2]["kind"] == "BREAKING"
    assert once["coordination"]["metal_center"] == 5


def test_undeclared_index_base_fails_closed():
    """Indices without an explicit convention are refused, never guessed."""
    with pytest.raises(ValueError, match="index_base"):
        normalize_search_spec({"schema_version": 4, "topology": {"bonds": [[0, 1]]}})


def test_typed_graph_kinds_and_covalent_separation():
    """FORMING/COORDINATION/BREAKING never enter covalent adjacency."""
    context = _build(
        _pentane("p"),
        {
            "schema_version": 4,
            "index_base": 1,
            "seed": 1,
            "topology": {
                "bonds": [
                    [1, 2],
                    [2, 3],
                    [3, 4],
                    [4, 5],
                    {"atoms": [1, 5], "kind": "FORMING"},
                    {"atoms": [2, 4], "kind": "BREAKING", "bond_order": 1.0},
                ]
            },
        },
    )
    kinds = {(e.a, e.b): e.type for e in context.graph.edges}
    assert kinds[(0, 4)] is EdgeType.FORMING
    assert kinds[(1, 3)] is EdgeType.BREAKING
    assert context.graph.reaction_pairs == ((0, 4),)
    covalent = [list(row) for row in context.adjacency]
    assert covalent[0] == [1] and covalent[4] == [3]  # typed extras excluded
    assert context.graph.elements == tuple(_pentane("p").atoms)


def test_add_bond_forming_overlays_guessed_covalent():
    """Declared FORMING replaces the guessed COV pair end to end."""
    context = _build(
        _butane("b"),
        {
            "schema_version": 4,
            "index_base": 1,
            "seed": 1,
            "topology": {"add_bond": [{"atoms": [1, 2], "kind": "FORMING"}]},
        },
    )
    assert _edge_kind(context.graph, 0, 1) is EdgeType.FORMING
    assert 1 not in context.adjacency[0] and 0 not in context.adjacency[1]
    assert context.graph.reaction_pairs == ((0, 1),)


def test_add_bond_breaking_overlays_guessed_covalent():
    """Declared BREAKING stays a typed edge, never covalent, never dropped."""
    context = _build(
        _butane("b"),
        {
            "schema_version": 4,
            "index_base": 1,
            "seed": 1,
            "topology": {"add_bond": [{"atoms": [1, 2], "kind": "BREAKING", "bond_order": 1.0}]},
        },
    )
    assert _edge_kind(context.graph, 0, 1) is EdgeType.BREAKING
    assert 1 not in context.adjacency[0] and 0 not in context.adjacency[1]
    assert any(e.type is EdgeType.BREAKING for e in context.graph.edges)


def test_typed_forming_add_bond_rides_workflow_wire():
    """Typed FORMING add_bond survives schema -> wire -> normalisation.

    Water keeps both perceived O-H bonds, gains the H-H reaction pair,
    and the pair stays out of the covalent adjacency.
    """
    record = StructureRecord(
        id="water",
        atoms=("O", "H", "H"),
        coordinates=((0.0, 0.0, 0.0), (0.757, 0.587, 0.0), (-0.757, 0.587, 0.0)),
    )
    bare = _build(record, {"schema_version": 4, "index_base": 1, "seed": 1})
    assert [list(row) for row in bare.adjacency] == [[1, 2], [0], [0]]
    scope = ConfgenModelV3.model_validate(
        {
            "schema_version": 4,
            "index_base": 1,
            "seed": 1,
            "topology": {"add_bond": [{"atoms": [2, 3], "kind": "FORMING"}]},
        }
    )
    wire = scope.scientific_native()
    assert wire["topology"]["add_bond"] == [
        {"atoms": [2, 3], "kind": "FORMING", "provenance": "explicit"}
    ]
    context = _build(record, dict(wire))
    assert [list(row) for row in context.adjacency] == [list(row) for row in bare.adjacency]
    assert context.graph.reaction_pairs == ((1, 2),)
    assert _edge_kind(context.graph, 1, 2) is EdgeType.FORMING
    assert 2 not in context.adjacency[1] and 1 not in context.adjacency[2]


def test_contradictory_explicit_kinds_fail_closed():
    """One pair carrying two declared kinds is refused, never guessed."""
    record, _ = _tetra_mn4()
    scope = {
        "coordination": {
            "metal_center": 1,
            "shapes": ["tetrahedral"],
            "binding_sites": [{"id": "s1", "kind": "atom", "atoms": [2], "hapticity": 1}],
        },
    }
    # Explicit COV against the declared coordination scope.
    with pytest.raises(ValueError, match="contradictory"):
        _build(
            record,
            {
                "schema_version": 4,
                "index_base": 1,
                "seed": 1,
                "topology": {"add_bond": [[1, 2]]},
                **scope,
            },
        )
    # Explicit COV against a declared FORMING on the same pair.
    with pytest.raises(ValueError, match="contradictory"):
        _build(
            _butane("b"),
            {
                "schema_version": 4,
                "index_base": 1,
                "seed": 1,
                "topology": {"add_bond": [[1, 2], {"atoms": [1, 2], "kind": "FORMING"}]},
            },
        )


def test_perceived_coordination_scope_types_metal_site_edges():
    """Perceived path: declared metal-donor pairs are COORDINATION, never COV.

    Same MN4 molecule with NO explicit graph: distance perception guesses
    metal-ligand COVALENT edges, and the declared coordination scope
    authoritatively retypes them. The metal row stays covalently empty.
    """
    from confflow.science.confgen.coordination.enumeration import enumerate_targets

    record, _ = _tetra_mn4()
    spec = {
        "schema_version": 4,
        "index_base": 1,
        "seed": 3,
        "coordination": {
            "metal_center": 1,
            "shapes": ["tetrahedral"],
            "binding_sites": [
                {"id": f"s{i}", "kind": "atom", "atoms": [i + 1], "hapticity": 1}
                for i in range(1, 5)
            ],
        },
    }
    context = _build(record, spec)
    for donor in (1, 2, 3, 4):
        assert _edge_kind(context.graph, 0, donor) is EdgeType.COORDINATION
    assert all(row == () for row in context.adjacency)  # no covalent leakage
    assert context.graph.coordination_donors() == (1, 2, 3, 4)
    lane_spec = CoordinationSpec(
        metal_center=0,
        binding_sites=tuple(BindingSite(id=f"s{i}", atoms=(i,)) for i in range(1, 5)),
        shapes=("tetrahedral",),
    )
    pipe = enumerate_targets(lane_spec, "tetrahedral")
    assert pipe["layers"].raw_assignments == 24
    assert pipe["layers"].shape_classes == 2


def test_coordination_enumeration_over_built_graph_matches_declared_donors():
    """Real enumeration (24 -> 2) over the graph built from the explicit topology."""
    from confflow.science.confgen.coordination.enumeration import enumerate_targets

    record, spec = _tetra_mn4()
    context = _build(record, spec)
    assert context.graph.metal_center == 0
    assert context.graph.coordination_donors() == (1, 2, 3, 4)
    assert all(row == () for row in context.adjacency)  # no covalent leakage
    lane_spec = CoordinationSpec(
        metal_center=0,
        binding_sites=tuple(BindingSite(id=f"s{i}", atoms=(i,)) for i in range(1, 5)),
        shapes=("tetrahedral",),
    )
    assert [s.donor for s in lane_spec.binding_sites] == list(context.graph.coordination_donors())
    pipe = enumerate_targets(lane_spec, "tetrahedral")
    assert pipe["layers"].raw_assignments == 24
    assert pipe["layers"].shape_classes == 2
