#!/usr/bin/env python3

"""Graph mapping and frame comparison, moved to ``confflow.science`` (C5.3a).

The first group of tests moved here unchanged from
``tests/test_refine_graph_rmsd.py``.  The second group pins the *work* of the
mapping search (node counts, pruning and verdicts) on hydrogen-bearing
molecules; it never measures time.  ``DEFAULT_MAPPING_NODE_BUDGET`` (1000
nodes per pair), the candidate-priority ordering and the pairwise-distance
bound are the reasons these numbers are small, so a change in them shows up
here as a different count or verdict.
"""

from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np
import pytest

from confflow.science.frame_compare import compare_frames
from confflow.science.topology_mapping import (
    DEFAULT_MAPPING_NODE_BUDGET,
    build_graph,
)

SEED = 20260913
DATA = Path(__file__).parent / "data" / "molecules_h.json"


def _reference_kabsch_rmsd(coords1, coords2) -> float:
    """Independent proper-Kabsch reference (plan section 3.3)."""
    x = np.asarray(coords1, dtype=np.float64)
    y = np.asarray(coords2, dtype=np.float64)
    xc = x - x.mean(axis=0)
    yc = y - y.mean(axis=0)
    h = yc.T @ x
    u, _s, vt = np.linalg.svd(h)
    d = np.eye(3)
    d[-1, -1] = 1.0 if np.linalg.det(u @ vt) >= 0.0 else -1.0
    r = u @ d @ vt
    aligned = yc @ r
    return float(np.sqrt(np.mean(np.sum((xc - aligned) ** 2, axis=1))))


def _triangle(scale: float) -> np.ndarray:
    radius = (1.0 / math.sqrt(3.0)) * scale
    angles = np.deg2rad([90.0, 210.0, 330.0])
    return np.column_stack([radius * np.cos(angles), radius * np.sin(angles), np.zeros(3)])


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


def _renumbered_co_frames(o_distance: float):
    """Build reference C-O-H order versus renumbered H-C-O candidate."""
    reference = {
        "original_index": 0,
        "energy": -1.0,
        "atoms": ["C", "O", "H"],
        "coords": np.array([[0.0, 0.0, 0.0], [1.4, 0.0, 0.0], [2.3, 0.0, 0.0]]),
    }
    candidate = {
        "original_index": 1,
        "energy": 0.0,
        "atoms": ["H", "C", "O"],
        "coords": np.array([[2.3, 0.0, 0.0], [0.0, 0.0, 0.0], [o_distance, 0.0, 0.0]]),
    }
    return reference, candidate


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


def test_compare_frames_never_matches_across_topologies():
    from confflow.science.frame_compare import compare_frames

    prism = _prism_graph()
    k33 = _k33_graph()
    coords = np.array(
        [
            [0.0, 0.0, 0.0],
            [1.0, 0.0, 0.0],
            [0.0, 1.0, 0.0],
            [1.0, 1.0, 0.0],
            [0.0, 0.0, 1.0],
            [1.0, 0.0, 1.0],
        ]
    )
    candidate = {
        "original_index": 1,
        "energy": -1.0,
        "atoms": ["C"] * 6,
        "coords": coords,
        "graph": k33,
    }
    representative = {
        "original_index": 0,
        "energy": -1.0,
        "atoms": ["C"] * 6,
        "coords": coords,
        "graph": prism,
    }
    verdict = compare_frames(candidate, representative, threshold=10.0, heavy_only=False)
    assert verdict.status == "different_topology"
    assert verdict.witness_rmsd is None


def test_compare_frames_budget_exhaustion_is_unresolved_not_duplicate_or_distinct():
    from confflow.science.frame_compare import compare_frames

    path = np.array(
        [
            [0.0, 0.0, 0.0],
            [1.5, 0.0, 0.0],
            [2.0, 1.4, 0.0],
            [1.0, 2.5, 0.2],
            [1.0, 3.0, 1.6],
        ]
    )
    candidate = {
        "original_index": 1,
        "energy": -1.0,
        "atoms": ["C"] * 5,
        "coords": path[::-1].copy(),
    }
    representative = {
        "original_index": 0,
        "energy": -1.5,
        "atoms": ["C"] * 5,
        "coords": path,
    }
    verdict = compare_frames(
        candidate,
        representative,
        threshold=0.25,
        heavy_only=False,
        node_budget=3,
    )
    assert verdict.status == "unresolved"
    assert verdict.search_complete is False
    assert verdict.witness_rmsd is None


def test_compare_frames_threshold_is_strictly_less_than():
    from confflow.science.frame_compare import compare_frames, kabsch_rmsd

    left = {
        "original_index": 0,
        "energy": -2.0,
        "atoms": ["C", "C", "C"],
        "coords": _triangle(1.0),
    }
    right = {
        "original_index": 1,
        "energy": -1.0,
        "atoms": ["C", "C", "C"],
        "coords": _triangle(1.2),
    }
    exact = kabsch_rmsd(_triangle(1.0), _triangle(1.2))
    assert exact > 0.0
    equal_cutoff = compare_frames(left, right, threshold=exact, heavy_only=False)
    assert equal_cutoff.status == "distinct"
    above_cutoff = compare_frames(left, right, threshold=exact * (1.0 + 1e-9), heavy_only=False)
    assert above_cutoff.status == "duplicate"
    assert above_cutoff.witness_rmsd < above_cutoff.cutoff


def test_f1_noH_renumbered_distinct_heavy_geometry_is_not_deleted():
    reference, candidate = _renumbered_co_frames(1.6)
    # Correct heavy-atom distance is 0.1 A, above the 0.05 cutoff.
    correct = _reference_kabsch_rmsd(candidate["coords"][[1, 2]], reference["coords"][[0, 1]])
    assert correct == pytest.approx(0.1, abs=1e-9)

    for first, second in ((candidate, reference), (reference, candidate)):
        verdict = compare_frames(
            first, second, threshold=0.05, heavy_only=True, energy_tolerance=0.0
        )
        assert verdict.status == "distinct", verdict
        assert verdict.witness_rmsd is None


def test_f1_noH_renumbered_true_duplicate_is_deleted_both_directions():
    reference, candidate = _renumbered_co_frames(1.4)
    verdict_forward = compare_frames(
        candidate, reference, threshold=0.05, heavy_only=True, energy_tolerance=0.0
    )
    verdict_backward = compare_frames(
        reference, candidate, threshold=0.05, heavy_only=True, energy_tolerance=0.0
    )
    for verdict in (verdict_forward, verdict_backward):
        assert verdict.status == "duplicate", verdict
        assert verdict.witness_rmsd == pytest.approx(0.0, abs=1e-9)
        assert verdict.mapping is not None

    assert verdict_forward.mapping == (2, 0, 1)
    assert verdict_backward.mapping == (1, 2, 0)


def test_f1_noH_random_renumbering_duplicate_and_non_duplicate():
    base_atoms = ["C", "C", "O", "H"]
    base = np.array([[0.0, 0.0, 0.0], [1.5, 0.0, 0.0], [2.7, 0.0, 0.0], [2.7, 0.97, 0.0]])
    rng = np.random.default_rng(SEED)
    representative = {
        "original_index": 0,
        "energy": -1.0,
        "atoms": base_atoms,
        "coords": base,
    }
    for _ in range(6):
        permutation = rng.permutation(4)
        atoms = [base_atoms[j] for j in permutation]
        coords = base[permutation].copy()
        duplicate = {
            "original_index": 1,
            "energy": 0.0,
            "atoms": atoms,
            "coords": coords,
        }
        shifted = coords.copy()
        shifted[atoms.index("O")] += np.array([0.0, 0.0, 0.25])
        non_duplicate = {
            "original_index": 2,
            "energy": 0.0,
            "atoms": atoms,
            "coords": shifted,
        }

        duplicate_verdict = compare_frames(
            duplicate, representative, threshold=0.05, heavy_only=True, energy_tolerance=0.0
        )
        assert duplicate_verdict.status == "duplicate", (permutation, duplicate_verdict)
        assert duplicate_verdict.witness_rmsd == pytest.approx(0.0, abs=1e-9)

        non_duplicate_verdict = compare_frames(
            non_duplicate, representative, threshold=0.05, heavy_only=True, energy_tolerance=0.0
        )
        assert non_duplicate_verdict.status != "duplicate", (permutation, non_duplicate_verdict)


# ---------------------------------------------------------------------------
# Work pins on hydrogen-bearing molecules (counts and verdicts, never time)
# ---------------------------------------------------------------------------


def _molecule(name: str) -> dict:
    payload = json.loads(DATA.read_text(encoding="utf-8"))[name]
    return {"atoms": list(payload["atoms"]), "coords": np.array(payload["coords"], dtype=float)}


def _hydrogens(graph, carbon: int) -> list[int]:
    return [j for j in graph.adjacency[carbon] if graph.symbols[j] == "H"]


def _relabel(frame: dict, permutation: list[int]) -> dict:
    """Relabel the atoms: new atom ``i`` is old atom ``permutation[i]``."""
    return {
        "atoms": [frame["atoms"][p] for p in permutation],
        "coords": frame["coords"][permutation],
    }


def _work(verdict) -> tuple[int, int, int, bool]:
    work = verdict.search_work
    return (work.nodes, work.mappings, work.pruned, work.complete)


def test_default_node_budget_is_the_legacy_value():
    assert DEFAULT_MAPPING_NODE_BUDGET == 1000


def test_butane_methyl_hydrogen_permutation_is_one_legal_mapping_away():
    frame = _molecule("butane")
    graph = build_graph(frame["atoms"], frame["coords"]).graph
    first, last = _hydrogens(graph, 0), _hydrogens(graph, 3)
    permutation = list(range(14))
    permutation[first[0]], permutation[first[1]], permutation[first[2]] = (
        first[1],
        first[2],
        first[0],
    )
    permutation[last[0]], permutation[last[1]] = last[1], last[0]
    verdict = compare_frames(frame, _relabel(frame, permutation), threshold=0.25)
    assert verdict.status == "duplicate"
    assert verdict.mapping_kind == "graph_mapping"
    assert _work(verdict) == (14, 1, 0, False)


def test_tert_butyl_alcohol_methyl_swap_needs_one_mapping_within_budget():
    frame = _molecule("tbutanol")
    graph = build_graph(frame["atoms"], frame["coords"]).graph
    quaternary = [
        i
        for i, symbol in enumerate(graph.symbols)
        if symbol == "C" and sum(graph.symbols[j] == "C" for j in graph.adjacency[i]) == 3
    ][0]
    methyls = [j for j in graph.adjacency[quaternary] if graph.symbols[j] == "C"]
    permutation = list(range(15))
    first, second, third = methyls
    permutation[first], permutation[second] = second, first
    for x, y in zip(_hydrogens(graph, first), _hydrogens(graph, second)):
        permutation[x], permutation[y] = y, x
    cycle = _hydrogens(graph, third)
    permutation[cycle[0]], permutation[cycle[1]], permutation[cycle[2]] = (
        cycle[1],
        cycle[2],
        cycle[0],
    )
    verdict = compare_frames(frame, _relabel(frame, permutation), threshold=0.25)
    assert verdict.status == "duplicate"
    assert verdict.mapping_kind == "graph_mapping"
    assert _work(verdict) == (15, 1, 0, False)


def test_benzene_ring_relabelling_is_the_identity_mapping():
    frame = _molecule("benzene")
    graph = build_graph(frame["atoms"], frame["coords"]).graph
    carbons = [i for i, symbol in enumerate(graph.symbols) if symbol == "C"]
    ring = [carbons[0]]
    while len(ring) < 6:
        ring.append(
            [j for j in graph.adjacency[ring[-1]] if graph.symbols[j] == "C" and j not in ring][0]
        )
    for step in (2, -1):
        permutation = list(range(12))
        for k in range(6):
            target = ring[(step * k) % 6] if step == -1 else ring[(k + step) % 6]
            permutation[ring[k]] = target
            permutation[_hydrogens(graph, ring[k])[0]] = _hydrogens(graph, target)[0]
        verdict = compare_frames(frame, _relabel(frame, permutation), threshold=0.25)
        assert verdict.status == "duplicate"
        assert verdict.mapping_kind == "identity"
        assert verdict.search_work is None


def test_toluene_ortho_meta_swap_needs_a_graph_mapping():
    frame = _molecule("toluene")
    graph = build_graph(frame["atoms"], frame["coords"]).graph
    ipso = [
        i
        for i, symbol in enumerate(graph.symbols)
        if symbol == "C" and sum(graph.symbols[j] == "C" for j in graph.adjacency[i]) == 3
    ][0]
    ortho = [
        j
        for j in graph.adjacency[ipso]
        if graph.symbols[j] == "C" and sum(graph.symbols[k] == "C" for k in graph.adjacency[j]) == 2
    ]
    meta = [
        [k for k in graph.adjacency[o] if graph.symbols[k] == "C" and k != ipso][0] for o in ortho
    ]
    permutation = list(range(len(frame["atoms"])))
    for x, y in ((ortho[0], ortho[1]), (meta[0], meta[1])):
        permutation[x], permutation[y] = y, x
        hx, hy = _hydrogens(graph, x)[0], _hydrogens(graph, y)[0]
        permutation[hx], permutation[hy] = hy, hx
    verdict = compare_frames(frame, _relabel(frame, permutation), threshold=0.25)
    assert verdict.status == "duplicate"
    assert verdict.mapping_kind == "graph_mapping"
    assert _work(verdict) == (15, 1, 0, False)


def test_anti_and_gauche_butane_are_never_merged_and_the_search_is_pruned():
    anti, gauche = _molecule("butane_anti"), _molecule("butane_gauche")
    verdict = compare_frames(anti, gauche, threshold=0.25)
    assert verdict.status == "distinct"
    assert verdict.reason == "all_legal_mappings_pruned_above_cutoff"
    assert _work(verdict) == (576, 0, 246, True)


def test_a_tiny_budget_is_unresolved_never_a_duplicate():
    anti, gauche = _molecule("butane_anti"), _molecule("butane_gauche")
    verdict = compare_frames(anti, gauche, threshold=0.25, node_budget=5)
    assert verdict.status == "unresolved"
    assert verdict.reason == "mapping_budget_exhausted"
    assert _work(verdict) == (6, 0, 0, False)
