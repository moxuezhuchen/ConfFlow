"""Geometric invariants of experimental closed-ring proposals."""

import itertools

import numpy as np
import pytest

from tools.experiments.confgen_ring_moves import (
    invert_pendant_ring,
    pendant_rings,
    ring_arcs,
    rotate_arc,
)


def _example():
    edges = [(i, (i + 1) % 6) for i in range(6)] + [(1, 6), (6, 7), (4, 8)]
    graph = [{} for _ in range(9)]
    for a, b in edges:
        graph[a][b] = graph[b][a] = "COVALENT"
    phi = np.arange(6) * np.pi / 3
    ring = np.column_stack((np.cos(phi), np.sin(phi), 0.2 * (-1.0) ** np.arange(6)))
    coords = np.vstack((ring, ring[1] + [0, 0, 1], ring[1] + [0, 1, 1], ring[4] + [0, 0, -1]))
    return graph, edges, coords


def test_all_graph_bonds_and_fixed_atoms_survive_ring_rotation():
    graph, edges, coords = _example()
    arcs = ring_arcs(graph, [range(6)], frozenset({4, 8}))
    assert arcs
    saw_branch = False
    for arc in arcs:
        moved = rotate_arc(coords, arc, 117)
        fixed = sorted(set(range(len(coords))) - set(arc.moving))
        np.testing.assert_array_equal(moved[fixed], coords[fixed])
        for a, b in edges:
            assert np.linalg.norm(moved[a] - moved[b]) == pytest.approx(
                np.linalg.norm(coords[a] - coords[b]), abs=1e-12
            )
        if 1 in arc.moving:
            saw_branch = True
            assert {6, 7} <= set(arc.moving)
        np.testing.assert_allclose(rotate_arc(moved, arc, -117), coords, atol=1e-12)
    assert saw_branch


def test_rotation_is_equivariant_under_rigid_transform():
    graph, _, coords = _example()
    arc = ring_arcs(graph, [range(6)])[0]
    q, _ = np.linalg.qr(np.random.default_rng(12).normal(size=(3, 3)))
    q[:, -1] *= np.linalg.det(q)
    shift = np.array([5, -7, 2])
    np.testing.assert_allclose(
        rotate_arc(coords @ q + shift, arc, 63),
        rotate_arc(coords, arc, 63) @ q + shift,
        atol=1e-12,
    )


def test_no_move_for_frozen_or_rigid_ring():
    graph, _, _ = _example()
    assert not ring_arcs(graph, [range(6)], frozenset(range(9)))
    assert not ring_arcs(graph, [range(6)], rigid_edges=frozenset({(0, 1)}))
    assert not ring_arcs(graph, [])


def test_bridged_graph_does_not_detach_cross_links():
    graph, edges, coords = _example()
    graph[1][4] = graph[4][1] = "COVALENT"
    edges.append((1, 4))
    for arc in ring_arcs(graph, [range(6)]):
        moved = rotate_arc(coords, arc, 90)
        for a, b in edges:
            assert np.linalg.norm(moved[a] - moved[b]) == pytest.approx(
                np.linalg.norm(coords[a] - coords[b]), abs=1e-12
            )


def test_degenerate_geometry_fails_closed():
    graph, _, coords = _example()
    arc = ring_arcs(graph, [range(6)])[0]
    coords[arc.anchors[1]] = coords[arc.anchors[0]]
    with pytest.raises(ValueError, match="distinct anchors"):
        rotate_arc(coords, arc, 60)


def test_ring_inversion_preserves_all_local_geometry_and_stereochemical_hands():
    graph, edges, coords = _example()
    (part,) = pendant_rings(graph, [range(6)], frozenset({8}))
    assert part.anchor == 4
    moved = invert_pendant_ring(coords, part)
    assert np.max(np.abs(moved - coords)) > 0.1
    np.testing.assert_array_equal(moved[[3, 4, 5, 8]], coords[[3, 4, 5, 8]])
    for a, b in edges:
        assert np.linalg.norm(moved[a] - moved[b]) == pytest.approx(
            np.linalg.norm(coords[a] - coords[b]), abs=1e-12
        )
    for center, neighbors in enumerate(graph):
        for a, b in itertools.combinations(neighbors, 2):
            # Dot products plus unchanged lengths imply unchanged bond angles.
            assert np.dot(moved[a] - moved[center], moved[b] - moved[center]) == pytest.approx(
                np.dot(coords[a] - coords[center], coords[b] - coords[center]), abs=1e-12
            )
        for triple in itertools.combinations(neighbors, 3):
            old = np.linalg.det(coords[list(triple)] - coords[center])
            new = np.linalg.det(moved[list(triple)] - moved[center])
            assert new == pytest.approx(old, abs=1e-12)
    np.testing.assert_allclose(invert_pendant_ring(moved, part), coords, atol=1e-12)


def test_ring_inversion_is_equivariant_and_independent_of_ring_traversal():
    graph, _, coords = _example()
    (part,) = pendant_rings(graph, [range(6)], frozenset({8}))
    (reverse,) = pendant_rings(graph, [list(reversed(range(6)))], frozenset({8}))
    q, _ = np.linalg.qr(np.random.default_rng(12).normal(size=(3, 3)))
    q[:, -1] *= np.linalg.det(q)
    shift = np.array([5, -7, 2])
    np.testing.assert_allclose(
        invert_pendant_ring(coords @ q + shift, part),
        invert_pendant_ring(coords, part) @ q + shift,
        atol=1e-12,
    )
    np.testing.assert_allclose(
        invert_pendant_ring(coords, reverse), invert_pendant_ring(coords, part), atol=1e-12
    )


def test_ring_inversion_rejects_multiple_fixed_attachments_and_external_bridges():
    graph, _, _ = _example()
    assert not pendant_rings(graph, [range(6)], frozenset({7, 8}))
    assert not pendant_rings(graph, [range(6)], frozenset({0, 3}))
    assert not pendant_rings(graph, [range(6)], rigid_edges=frozenset({(0, 1)}))
    graph[7][8] = graph[8][7] = "COVALENT"
    assert not pendant_rings(graph, [range(6)])


@pytest.mark.parametrize("degenerate", ["zero_length", "collinear", "nonfinite"])
def test_ring_inversion_fails_closed_on_degenerate_geometry(degenerate):
    graph, _, coords = _example()
    (part,) = pendant_rings(graph, [range(6)], frozenset({8}))
    if degenerate == "zero_length":
        coords[3] = coords[4]
    elif degenerate == "collinear":
        coords[5] = 2 * coords[4] - coords[3]
    else:
        coords[0, 0] = np.nan
    with pytest.raises(ValueError):
        invert_pendant_ring(coords, part)
