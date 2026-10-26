#!/usr/bin/env python3

"""BREAKING topology pairs are restrained like FORMING pairs in ConfGen.

A BREAKING edge enters ``reaction_pairs`` through both the ``bonds`` and the
``add_bond`` routes, a pair declared twice (in either order) is listed once,
and FORMING pairs keep their behaviour.
"""

from __future__ import annotations

from typing import Any

import pytest

from confflow.domain.structure import StructureRecord
from confflow.science.confgen.graph import EdgeType
from confflow.science.confgen.search_spec import normalize_search_spec
from confflow.science.confgen.topology import build_typed_graph


def _chain() -> StructureRecord:
    """Four carbons 3 A apart on a line: no perceived bonds, so only declared edges exist."""
    coords = tuple((3.0 * index, 0.0, 0.0) for index in range(4))
    return StructureRecord(
        id="chain",
        atoms=("C", "C", "C", "C"),
        coordinates=coords,
        charge=0,
        multiplicity=1,
    )


def _resolved() -> dict[str, Any]:
    return normalize_search_spec({"schema_version": 4, "index_base": 1})


def _build(topology: dict[str, Any]):
    _adjacency, graph = build_typed_graph(_chain(), topology, _resolved())
    return graph


@pytest.mark.parametrize("route", ["bonds", "add_bond"])
def test_breaking_edge_is_listed_as_reaction_pair(route: str) -> None:
    topology = (
        {"bonds": [{"atoms": [0, 3], "kind": "BREAKING"}]}
        if route == "bonds"
        else {"add_bond": [{"atoms": [0, 3], "kind": "BREAKING"}]}
    )
    graph = _build(topology)
    assert graph.edge_kind(0, 3) is EdgeType.BREAKING
    assert graph.reaction_pairs == ((0, 3),)


@pytest.mark.parametrize("route", ["bonds", "add_bond"])
def test_pair_declared_again_in_either_order_is_listed_once(route: str) -> None:
    declared = {"reaction_pairs": [[3, 0]]}
    if route == "bonds":
        topology = {**declared, "bonds": [{"atoms": [0, 3], "kind": "BREAKING"}]}
    else:
        topology = {**declared, "add_bond": [{"atoms": [0, 3], "kind": "BREAKING"}]}
    graph = _build(topology)
    assert graph.reaction_pairs == ((3, 0),)


def test_repeated_declared_pairs_are_kept_at_first_occurrence() -> None:
    topology = {
        "reaction_pairs": [[3, 0], [0, 3], [1, 2]],
        "bonds": [
            {"atoms": [0, 3], "kind": "BREAKING"},
            {"atoms": [1, 2], "kind": "FORMING"},
        ],
    }
    graph = _build(topology)
    assert graph.reaction_pairs == ((3, 0), (1, 2))


def test_forming_edges_keep_reaction_pair_behaviour() -> None:
    bonds = _build({"bonds": [{"atoms": [0, 3], "kind": "FORMING"}]})
    add_bond = _build({"add_bond": [{"atoms": [0, 3], "kind": "FORMING"}]})
    for graph in (bonds, add_bond):
        assert graph.edge_kind(0, 3) is EdgeType.FORMING
        assert graph.reaction_pairs == ((0, 3),)
    assert _build({}).reaction_pairs == ()
