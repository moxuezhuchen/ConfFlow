#!/usr/bin/env python3

"""Graph mapping: topology grouping, typed non-covalent edges and the node budget.

Frame comparison lives in the ConfGen identity now; these tests cover the graph layer
that ``refine`` still uses for its isomorphism step.
"""

from __future__ import annotations

import numpy as np
import pytest

from confflow.science.topology_mapping import DEFAULT_MAPPING_NODE_BUDGET


def _prism_graph():
    from confflow.science.topology_mapping import graph_from_adjacency

    adjacency = [
        (1, 2, 3),
        (0, 2, 4),
        (0, 1, 5),
        (0, 4, 5),
        (1, 3, 5),
        (2, 3, 4),
    ]
    return graph_from_adjacency(["C"] * 6, adjacency)


def _k33_graph():
    from confflow.science.topology_mapping import graph_from_adjacency

    adjacency = [
        (3, 4, 5),
        (3, 4, 5),
        (3, 4, 5),
        (0, 1, 2),
        (0, 1, 2),
        (0, 1, 2),
    ]
    return graph_from_adjacency(["C"] * 6, adjacency)


def test_prism_and_k33_share_cheap_invariants_but_are_not_isomorphic():
    from confflow.science.topology_mapping import find_isomorphism, graphs_may_be_isomorphic

    prism = _prism_graph()
    k33 = _k33_graph()
    assert sorted(prism.degree_sequence) == sorted(k33.degree_sequence)
    assert graphs_may_be_isomorphic(prism, k33)  # 3-regular WL cannot separate them

    result = find_isomorphism(prism, k33, node_budget=100_000)
    assert result.status == "non_isomorphic"
    assert result.mapping is None
    assert result.stats.complete is True

    same = find_isomorphism(prism, k33, node_budget=100_000)  # deterministic repeat
    assert same.status == result.status


def test_group_frames_by_topology_separates_prism_and_k33():
    from confflow.science.topology_mapping import group_frames_by_topology

    prism = _prism_graph()
    k33 = _k33_graph()
    frames = []
    for idx, graph in enumerate([prism, prism, k33, k33]):
        frames.append(
            {
                "original_index": idx,
                "energy": -1.0 - idx * 0.01,
                "atoms": ["C"] * 6,
                "coords": np.zeros((6, 3)),
                "graph": graph,
            }
        )
    clusters = group_frames_by_topology(frames, node_budget=100_000)
    sizes = sorted(len(cluster.frames) for cluster in clusters if cluster.status == "confirmed")
    assert sizes == [2, 2]
    assigned = {
        frame["original_index"]: cluster.cluster_id
        for cluster in clusters
        for frame in cluster.frames
    }
    assert assigned[0] == assigned[1]
    assert assigned[2] == assigned[3]
    assert assigned[0] != assigned[2]


def test_default_node_budget_is_the_legacy_value():
    assert DEFAULT_MAPPING_NODE_BUDGET == 1000


# ---------------------------------------------------------------------------
# Typed non-covalent edges (C5.3c-1)
# ---------------------------------------------------------------------------


def _typed_pair(kind_a: str, kind_b: str):
    """Two six-atom rings that differ only in where a typed edge sits."""
    from confflow.science.topology_mapping import graph_from_adjacency, with_typed_edges

    ring = [[1, 5], [0, 2], [1, 3], [2, 4], [3, 5], [4, 0]]
    base = graph_from_adjacency(["C"] * 6, ring)
    return (
        with_typed_edges(base, [(0, 3, kind_a)]),
        with_typed_edges(base, [(0, 2, kind_b)]),
    )


def test_typed_edges_are_normalised_and_validated():
    from confflow.science.topology_mapping import graph_from_adjacency, with_typed_edges

    base = graph_from_adjacency(["C", "C", "C"], [[1], [0], []])
    graph = with_typed_edges(base, [(2, 0, "FORMING"), (0, 2, "FORMING")])
    assert graph.typed_edges == ((0, 2, "FORMING"),)
    assert base.typed_edges == ()
    with pytest.raises(ValueError):
        with_typed_edges(base, [(1, 1, "FORMING")])
    with pytest.raises(ValueError):
        with_typed_edges(base, [(0, 7, "FORMING")])


def test_a_mapping_must_preserve_typed_edges():
    from confflow.science.topology_mapping import (
        MappingSearch,
        fixed_index_isomorphism,
        graphs_may_be_isomorphic,
        validate_mapping,
    )

    across, adjacent = _typed_pair("FORMING", "FORMING")
    # Same covalent ring and one FORMING edge each, but across the ring versus
    # between ring neighbours-of-neighbours: no covalent automorphism maps one
    # onto the other.
    assert graphs_may_be_isomorphic(across, adjacent)
    assert not fixed_index_isomorphism(across, adjacent)
    search = MappingSearch(across, adjacent, 10_000)
    assert list(search.iter_mappings()) == []
    assert not validate_mapping(across, adjacent, list(range(6)))
    # The same typed edge in the same place is an isomorphism, and the search
    # finds a mapping that preserves it.
    same, _ = _typed_pair("FORMING", "FORMING")
    again, _ = _typed_pair("FORMING", "FORMING")
    found = list(MappingSearch(same, again, 10_000).iter_mappings())
    assert found and all(validate_mapping(same, again, list(m)) for m in found)


def test_different_kinds_are_never_isomorphic():
    from confflow.science.topology_mapping import graphs_may_be_isomorphic

    forming, _ = _typed_pair("FORMING", "FORMING")
    breaking, _ = _typed_pair("BREAKING", "BREAKING")
    assert not graphs_may_be_isomorphic(forming, breaking)
