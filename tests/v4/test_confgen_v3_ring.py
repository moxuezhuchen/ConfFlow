#!/usr/bin/env python3

"""ConfGen v3 ring-lane tests (ring worker owned).

Scientific coverage for isolated covalent 4/5/6 rings: template closure and
distinctness, chair roundtrips with tolerance-aware commanded/observed
matching, substituted-frame propagation, independent tetrahedral chiral parity
(positive flip preservation and mirrored negative), ambiguous-boundary
perception, singular-frame fail-closure, atom identity, explicit unsupported
scope (fused/overlap/macrocycle/chelate/bridge coupling), single-bond-linked
multi-ring systems, preserve-input reporting, and the RingStage adapter on the
frozen core API (narrow state identity, tuple enumeration, no stage sampling).
"""

from __future__ import annotations

from collections.abc import Iterator

import numpy as np
import pytest

from confflow.domain import StructureRecord
from confflow.science.confgen.model import (
    ConfgenStateKey,
    WorkingRealization,
    build_context,
)
from confflow.science.confgen.ring import (
    BACKEND_NAME,
    BOND_LENGTH,
    TEMPLATE_REGISTRY,
    RingNumericalFailure,
    RingStage,
    RingTolerances,
    commanded_state_dict,
    get_template,
    perceive_ring,
    realize_rings,
    realize_single_system,
    ring_states_match,
    template_bond_spread,
    template_coords,
    template_torsions,
    templates_for_size,
)
from confflow.science.confgen.ring.geometry import (
    circular_rms_deg,
    clash_pairs,
    topological_distances,
)
from confflow.science.confgen.ring.realization import (
    RingGeometryFailure,
    parse_ring_specs,
)


def _ring_adjacency(size: int) -> list[list[int]]:
    graph: list[list[int]] = [[] for _ in range(size)]
    for position in range(size):
        first = position
        second = (position + 1) % size
        graph[first].append(second)
        graph[second].append(first)
    return graph


def _bonds_of(graph: list[list[int]]) -> list[list[int]]:
    seen: set[tuple[int, int]] = set()
    bonds: list[list[int]] = []
    for first, row in enumerate(graph):
        for second in row:
            key = (min(first, second), max(first, second))
            if key not in seen:
                seen.add(key)
                bonds.append([first, second])
    return bonds


def _record(record_id: str, elements: list[str], coords: np.ndarray) -> StructureRecord:
    return StructureRecord(
        id=record_id,
        atoms=tuple(elements),
        coordinates=tuple(tuple(float(v) for v in row) for row in coords),
    )


def _methylcyclohexane() -> tuple[np.ndarray, list[str], list[list[int]]]:
    ring = template_coords(get_template("chair_A_6"))
    methyl = np.asarray([[ring[0, 0], ring[0, 1] + 1.0, ring[0, 2] + 1.0]])
    coords = np.vstack([ring, methyl])
    elements = ["C"] * 6 + ["C"]
    graph = _ring_adjacency(6)
    graph.append([0])
    graph[0].append(6)
    return coords, elements, graph


def _chiral_methyl_fluoro() -> tuple[np.ndarray, list[str], list[list[int]]]:
    """Chair ring with a chiral C0: ring-prev, ring-next, methyl-C, fluoro-F."""
    ring = template_coords(get_template("chair_A_6"))
    methyl = np.asarray([[ring[0, 0], ring[0, 1] + 1.0, ring[0, 2] + 1.0]])
    fluoro = np.asarray([[ring[0, 0] + 1.0, ring[0, 1] - 0.7, ring[0, 2] - 0.7]])
    coords = np.vstack([ring, methyl, fluoro])
    elements = ["C"] * 6 + ["C", "F"]
    graph = _ring_adjacency(6)
    graph.append([0])
    graph.append([0])
    graph[0].extend([6, 7])
    return coords, elements, graph


def _chiral_signs(coords: np.ndarray) -> tuple[float, float]:
    """Ordered-neighbour signed volumes at C0: roles (prev, next, Me) and (prev, next, F)."""
    p0 = coords[0]
    triple = np.cross(coords[5] - p0, coords[1] - p0)
    return (
        float(np.dot(coords[6] - p0, triple)),
        float(np.dot(coords[7] - p0, triple)),
    )


def _linked_rings() -> tuple[np.ndarray, list[str], list[list[int]]]:
    """Two chair rings joined by one acyclic single bond (biphenyl-like).

    Ring B is placed by proper rotation so its bulk extends away from ring A
    across a 1.54 A linking bond: ordinary disconnected multi-ring chemistry,
    supported scope with a linking-bond audit.
    """
    chair = template_coords(get_template("chair_A_6"))
    centroid = chair.mean(axis=0)
    outward = (chair[0] - centroid) / float(np.linalg.norm(chair[0] - centroid))
    joint = chair[0] + BOND_LENGTH * outward
    axis = np.cross(outward, np.array([0.0, 0.0, 1.0]))
    axis = axis / float(np.linalg.norm(axis))
    turn = -np.eye(3) + 2.0 * np.outer(axis, axis)
    ring_b = (chair - chair[0]) @ turn.T + joint
    coords = np.vstack([chair, ring_b])
    elements = ["C"] * 12
    graph: list[list[int]] = [[] for _ in range(12)]
    for offset in (0, 6):
        for position in range(6):
            first = offset + position
            second = offset + (position + 1) % 6
            graph[first].append(second)
            graph[second].append(first)
    graph[0].append(6)
    graph[6].append(0)
    return coords, elements, graph


def _context_for(record: StructureRecord, graph: list[list[int]], extra: dict | None = None):
    # Real core integration under the global index convention: root
    # index_base: 1 with spec-facing 1-based topology bonds; core normalizes
    # everything to internal 0-based exactly once. The stage works internal.
    spec: dict = {
        "index_base": 1,
        "topology": {"bonds": [[a + 1, b + 1] for a, b in _bonds_of(graph)]},
    }
    if extra:
        spec.update(extra)
    return build_context(record, spec)


def _parent_of(record: StructureRecord) -> WorkingRealization:
    return WorkingRealization(structure=record, state_key=ConfgenStateKey(), provenance={})


def test_templates_have_exact_closure_and_distinct_states() -> None:
    assert set(TEMPLATE_REGISTRY) == {
        "planar_4",
        "pucker_up_4",
        "pucker_down_4",
        "planar_5",
        "envelope_5",
        "twist_5",
        "chair_A_6",
        "chair_B_6",
        "boat_6",
        "twist_boat_6",
    }
    for name, template in TEMPLATE_REGISTRY.items():
        assert template_bond_spread(template) < 1e-9, name
        assert all(np.isfinite(template_torsions(template))), name
    for size in (4, 5, 6):
        descriptors = [np.asarray(template_torsions(t)) for t in templates_for_size(size)]
        for first in range(len(descriptors)):
            for second in range(first + 1, len(descriptors)):
                distance = circular_rms_deg(descriptors[first], descriptors[second])
                assert distance > 5.0, (size, first, second)


def test_chair_roundtrip_matches_commanded_state() -> None:
    coords = template_coords(get_template("chair_A_6"))
    graph = _ring_adjacency(6)
    perception = perceive_ring(coords)
    assert perception.best_template == "chair_A_6"
    assert perception.confidence == "reported"
    assert perception.best_distance_deg == pytest.approx(0.0, abs=1e-9)
    specs = parse_ring_specs([{"id": "r1", "atoms": [0, 1, 2, 3, 4, 5]}])
    output = realize_rings(coords, ["C"] * 6, graph, specs, {"r1": "chair_B_6"})
    assert not output.preserved
    commanded = commanded_state_dict("chair_B_6", anchor=0)
    match, evidence = ring_states_match(commanded, output.states[0])
    assert match, evidence
    assert set(output.states[0]) == {"template", "torsions", "anchor", "direction"}


def test_ring_states_match_rejects_mismatches() -> None:
    commanded = commanded_state_dict("chair_A_6", anchor=0)
    assert ring_states_match(commanded, dict(commanded))[0] is True
    observed = dict(commanded)
    observed["template"] = "chair_B_6"
    match, evidence = ring_states_match(commanded, observed)
    assert match is False
    assert evidence["reason"] == "mismatch:template"
    observed = dict(commanded)
    observed["anchor"] = 3
    assert ring_states_match(commanded, observed)[0] is False
    observed = dict(commanded)
    torsions = list(observed["torsions"])
    torsions[0] = float(torsions[0]) + 45.0
    observed["torsions"] = torsions
    match, evidence = ring_states_match(commanded, observed)
    assert match is False
    assert evidence["reason"] == "torsion_outside_tolerance"
    noisy = dict(commanded)
    noisy["torsions"] = [float(v) + 2.0 for v in commanded["torsions"]]
    assert ring_states_match(commanded, noisy)[0] is True


def test_near_template_geometries_share_one_canonical_key() -> None:
    # Identical discrete declarations of noisy near-template geometries get
    # identical StateKeys (canonical descriptor), while measured torsions
    # stay in diagnostics and differ.
    from confflow.science.confgen.ring import ring_diagnostics, ring_state_dict

    ideal = template_coords(get_template("chair_A_6"))
    keys: list[dict] = []
    measured_sets: list[list[float]] = []
    for seed in (11, 12):
        rng = np.random.default_rng(seed)
        noisy = ideal + 0.03 * rng.standard_normal((6, 3))
        perception = perceive_ring(noisy)
        assert perception.best_template == "chair_A_6"
        assert perception.confidence == "reported"
        keys.append(ring_state_dict(perception, anchor=0))
        measured_sets.append(ring_diagnostics(perception)["measured_torsions"])
    assert keys[0] == keys[1]
    assert keys[0] == commanded_state_dict("chair_A_6", anchor=0)
    assert measured_sets[0] != measured_sets[1]
    assert keys[0] != commanded_state_dict("chair_B_6", anchor=0)


def test_physical_distortion_caught_by_lock_matcher() -> None:
    # A physically distorted ring (same traversal) fails the tolerance-aware
    # lock match against the canonical commanded state even though both
    # carry the same template label vocabulary.
    from confflow.science.confgen.ring import ring_diagnostics

    chair = template_coords(get_template("chair_A_6"))
    boat = template_coords(get_template("boat_6"))
    commanded = commanded_state_dict("chair_A_6", anchor=0)
    mild = chair + 0.03 * np.random.default_rng(3).standard_normal((6, 3))
    mild_perception = perceive_ring(mild)
    mild_observed = {
        "template": mild_perception.best_template,
        "torsions": ring_diagnostics(mild_perception)["measured_torsions"],
        "anchor": 0,
        "direction": "as_given",
    }
    assert ring_states_match(commanded, mild_observed)[0] is True
    blend = 0.75 * chair + 0.25 * boat
    distorted = perceive_ring(blend)
    distorted_observed = {
        "template": distorted.best_template,
        "torsions": ring_diagnostics(distorted)["measured_torsions"],
        "anchor": 0,
        "direction": "as_given",
    }
    match, evidence = ring_states_match(commanded, distorted_observed)
    assert match is False
    assert evidence["reason"] == "torsion_outside_tolerance"
    assert evidence["worst_torsion_deg"] > 10.0


def test_substituted_frame_propagation_preserves_bonds() -> None:
    coords, elements, graph = _methylcyclohexane()
    specs = parse_ring_specs([{"id": "r1", "atoms": [0, 1, 2, 3, 4, 5]}])
    old_sub = float(np.linalg.norm(coords[6] - coords[0]))
    output = realize_rings(coords, elements, graph, specs, {"r1": "chair_B_6"})
    realized = np.asarray(output.coordinates)
    new_sub = float(np.linalg.norm(realized[6] - realized[0]))
    assert new_sub == pytest.approx(old_sub, abs=1e-9)
    assert list(elements) == ["C"] * 7
    assert realized.shape == (7, 3)


def test_ring_flip_preserves_chiral_parity_positive() -> None:
    coords, elements, graph = _chiral_methyl_fluoro()
    before = _chiral_signs(coords)
    assert all(abs(value) > 1e-6 for value in before)
    specs = parse_ring_specs([{"id": "r1", "atoms": [0, 1, 2, 3, 4, 5]}])
    output = realize_rings(coords, elements, graph, specs, {"r1": "chair_B_6"})
    after = _chiral_signs(np.asarray(output.coordinates))
    assert np.sign(after[0]) == np.sign(before[0])
    assert np.sign(after[1]) == np.sign(before[1])
    commanded = commanded_state_dict("chair_B_6", anchor=0)
    assert ring_states_match(commanded, output.states[0])[0] is True


def test_mirrored_input_is_detected_negative() -> None:
    coords, elements, graph = _chiral_methyl_fluoro()
    mirrored = coords.copy()
    mirrored[:, 0] = -mirrored[:, 0]
    assert perceive_ring(mirrored[:6]).best_template == "chair_B_6"
    specs = parse_ring_specs([{"id": "r1", "atoms": [0, 1, 2, 3, 4, 5]}])
    plain = realize_rings(coords, elements, graph, specs, {"r1": "chair_A_6"})
    flipped = realize_rings(mirrored, elements, graph, specs, {"r1": "chair_A_6"})
    plain_signs = _chiral_signs(np.asarray(plain.coordinates))
    flipped_signs = _chiral_signs(np.asarray(flipped.coordinates))
    assert np.sign(flipped_signs[0]) != np.sign(plain_signs[0])
    assert np.sign(flipped_signs[1]) != np.sign(plain_signs[1])
    # The ring itself still realizes as commanded; only substituent parity flips.
    commanded = commanded_state_dict("chair_A_6", anchor=0)
    assert ring_states_match(commanded, flipped.states[0])[0] is True


def test_noisy_chair_still_reported() -> None:
    rng = np.random.default_rng(0)
    coords = template_coords(get_template("chair_A_6")) + 0.02 * rng.standard_normal((6, 3))
    perception = perceive_ring(coords)
    assert perception.best_template == "chair_A_6"
    assert perception.confidence == "reported"


def test_chair_boat_blend_is_ambiguous_with_alternatives() -> None:
    chair = template_coords(get_template("chair_A_6"))
    boat = template_coords(get_template("boat_6"))
    blend = 0.5 * (chair + boat)
    perception = perceive_ring(blend)
    assert perception.confidence == "ambiguous"
    assert len(perception.alternatives) >= 1
    assert perception.margin_deg < 30.0 or "unrecognized" in perception.boundary_flags
    assert perception.boundary_flags != ()


def test_traversal_direction_is_stable_authority() -> None:
    envelope = template_coords(get_template("envelope_5"))
    opposite = envelope[[0, 4, 3, 2, 1]]
    perception = perceive_ring(opposite)
    assert perception.direction == "as_given"
    assert not (perception.best_template == "envelope_5" and perception.confidence == "reported")
    assert perception.confidence == "ambiguous"


def test_unsupported_ring_size_perceived_ambiguous() -> None:
    rng = np.random.default_rng(1)
    perception = perceive_ring(rng.standard_normal((7, 3)))
    assert perception.best_template is None
    assert perception.confidence == "ambiguous"
    assert "unsupported_ring_size" in perception.boundary_flags


def test_singular_frame_fails_closed_numerical() -> None:
    coords = np.array([[float(i), 0.0, 0.0] for i in range(4)])
    graph = _ring_adjacency(4)
    specs = parse_ring_specs([{"id": "r1", "atoms": [0, 1, 2, 3]}])
    with pytest.raises(RingNumericalFailure, match="degenerate_frame"):
        realize_single_system(coords, ["C"] * 4, graph, specs[0], "pucker_up_4")


def test_nonfinite_input_fails_closed() -> None:
    coords = template_coords(get_template("chair_A_6")).copy()
    coords[0, 0] = float("inf")
    specs = parse_ring_specs([{"id": "r1", "atoms": [0, 1, 2, 3, 4, 5]}])
    with pytest.raises(RingNumericalFailure, match="nonfinite"):
        realize_rings(coords, ["C"] * 6, _ring_adjacency(6), specs, {"r1": "chair_A_6"})


def test_realization_preserves_atom_identity_and_input() -> None:
    coords, elements, graph = _methylcyclohexane()
    snapshot = coords.copy()
    specs = parse_ring_specs([{"id": "r1", "atoms": [0, 1, 2, 3, 4, 5]}])
    output = realize_rings(coords, elements, graph, specs, {"r1": "chair_A_6"})
    np.testing.assert_allclose(coords, snapshot)
    assert len(output.coordinates) == 7
    assert output.states[0]["template"] == "chair_A_6"


def test_fused_pair_sharing_bond_rejected() -> None:
    elements = ["C"] * 10
    graph: list[list[int]] = [[] for _ in range(10)]
    for first, second in [(0, 1), (1, 2), (2, 3), (3, 4), (4, 5), (5, 0)]:
        graph[first].append(second)
        graph[second].append(first)
    for first, second in [(4, 5), (5, 6), (6, 7), (7, 8), (8, 9), (9, 4)]:
        graph[first].append(second)
        graph[second].append(first)
    rng = np.random.default_rng(2)
    coords = rng.standard_normal((10, 3))
    specs = parse_ring_specs(
        [
            {"id": "a", "atoms": [0, 1, 2, 3, 4, 5]},
            {"id": "b", "atoms": [4, 5, 6, 7, 8, 9]},
        ]
    )
    with pytest.raises(ValueError, match="overlapping_systems"):
        realize_rings(coords, elements, graph, specs, {"a": "chair_A_6", "b": "chair_A_6"})


def test_extra_intra_ring_bond_rejected_as_fused() -> None:
    graph = _ring_adjacency(6)
    graph[0].append(3)
    graph[3].append(0)
    specs = parse_ring_specs([{"id": "a", "atoms": [0, 1, 2, 3, 4, 5]}])
    with pytest.raises(ValueError, match="fused_or_bridged"):
        realize_rings(np.zeros((6, 3)), ["C"] * 6, graph, specs, {"a": "chair_A_6"})


def test_overlapping_systems_rejected() -> None:
    coords = np.zeros((8, 3))
    graph: list[list[int]] = [[] for _ in range(8)]
    specs = parse_ring_specs(
        [{"id": "a", "atoms": [0, 1, 2, 3]}, {"id": "b", "atoms": [3, 4, 5, 6]}]
    )
    with pytest.raises(ValueError, match="overlapping_systems"):
        realize_rings(coords, ["C"] * 8, graph, specs, {"a": "planar_4", "b": "planar_4"})


def test_macrocycle_unsupported() -> None:
    specs = parse_ring_specs([{"id": "big", "atoms": list(range(8))}])
    graph: list[list[int]] = [[] for _ in range(8)]
    for position in range(8):
        graph[position].append((position + 1) % 8)
        graph[position].append((position - 1) % 8)
    with pytest.raises(ValueError, match="unsupported_ring_size"):
        realize_rings(np.zeros((8, 3)), ["C"] * 8, graph, specs, {"big": "planar_4"})


def test_chelate_ring_unsupported() -> None:
    coords = template_coords(get_template("planar_4"))
    elements = ["C", "C", "FE", "C"]
    specs = parse_ring_specs([{"id": "r1", "atoms": [0, 1, 2, 3]}])
    with pytest.raises(ValueError, match="chelate"):
        realize_rings(coords, elements, _ring_adjacency(4), specs, {"r1": "planar_4"})


def test_bridging_methylene_is_multi_anchor_coupling() -> None:
    # Norbornane-like bridge atom bonded to ring atoms 0 and 2: a non-ring
    # fragment touching two blocked atoms is explicitly unsupported coupling.
    coords = np.zeros((7, 3))
    graph = _ring_adjacency(6)
    graph.append([0, 2])
    graph[0].append(6)
    graph[2].append(6)
    specs = parse_ring_specs([{"id": "r1", "atoms": [0, 1, 2, 3, 4, 5]}])
    with pytest.raises(ValueError, match="multi_anchor"):
        realize_rings(coords, ["C"] * 7, graph, specs, {"r1": "chair_A_6"})


def test_nonbonded_traversal_unsupported() -> None:
    specs = parse_ring_specs([{"id": "r1", "atoms": [0, 1, 2, 4]}])
    with pytest.raises(ValueError, match="nonbonded_traversal"):
        realize_rings(np.zeros((6, 3)), ["C"] * 6, _ring_adjacency(6), specs, {"r1": "planar_4"})


def test_unknown_template_unsupported() -> None:
    specs = parse_ring_specs([{"id": "r1", "atoms": [0, 1, 2, 3], "templates": ["sofa_4"]}])
    with pytest.raises(ValueError, match="unknown_template"):
        realize_rings(np.zeros((4, 3)), ["C"] * 4, _ring_adjacency(4), specs, {"r1": "sofa_4"})


def test_clash_audit_reports_geometry_failure() -> None:
    coords, elements, graph = _methylcyclohexane()
    specs = parse_ring_specs([{"id": "r1", "atoms": [0, 1, 2, 3, 4, 5]}])
    strict = RingTolerances(clash_threshold=10.0)
    with pytest.raises(RingGeometryFailure, match="clash"):
        realize_rings(coords, elements, graph, specs, {"r1": "chair_A_6"}, tolerances=strict)


def test_clash_pairs_detects_overlap() -> None:
    coords = np.array(
        [
            [0.0, 0.0, 0.0],
            [1.5, 0.0, 0.0],
            [3.0, 0.0, 0.0],
            [1.5, 1.0, 0.0],
            [0.05, 0.0, 0.0],
        ]
    )
    graph = [[1], [0, 2], [1, 3], [2, 4], [3]]
    topo = topological_distances(graph)
    assert topo[0][4] == 4
    clashes, fallback = clash_pairs(coords, ["C"] * 5, topo)
    assert not fallback
    assert clashes
    assert clashes[0][0] == 0 and clashes[0][1] == 4


def test_multiple_systems_sorted_and_deterministic() -> None:
    chair = template_coords(get_template("chair_A_6"))
    shift = np.array([20.0, 0.0, 0.0])
    coords = np.vstack([chair, chair + shift])
    elements = ["C"] * 12
    graph: list[list[int]] = [[] for _ in range(12)]
    for offset in (0, 6):
        for position in range(6):
            first = offset + position
            second = offset + (position + 1) % 6
            graph[first].append(second)
            graph[second].append(first)
    specs = parse_ring_specs(
        [
            {"id": "zeta", "atoms": [6, 7, 8, 9, 10, 11]},
            {"id": "alpha", "atoms": [0, 1, 2, 3, 4, 5]},
        ]
    )
    assert [spec.id for spec in specs] == ["alpha", "zeta"]
    assignment = {"alpha": "chair_B_6", "zeta": "boat_6"}
    first = realize_rings(coords, elements, graph, specs, assignment)
    second = realize_rings(coords, elements, graph, specs, assignment)
    assert [state["template"] for state in first.states] == ["chair_B_6", "boat_6"]
    np.testing.assert_allclose(np.asarray(first.coordinates), np.asarray(second.coordinates))


def test_single_bond_linked_rings_are_supported() -> None:
    coords, elements, graph = _linked_rings()
    specs = parse_ring_specs(
        [
            {"id": "a", "atoms": [0, 1, 2, 3, 4, 5]},
            {"id": "b", "atoms": [6, 7, 8, 9, 10, 11]},
        ]
    )
    output = realize_rings(coords, elements, graph, specs, {"a": "chair_A_6", "b": "chair_A_6"})
    realized = np.asarray(output.coordinates)
    old_link = float(np.linalg.norm(coords[0] - coords[6]))
    new_link = float(np.linalg.norm(realized[0] - realized[6]))
    assert new_link == pytest.approx(old_link, abs=1e-9)
    assert [state["template"] for state in output.states] == ["chair_A_6", "chair_A_6"]
    assert all(audit["link_bond_drift"] < 1e-9 for audit in output.audits)


def test_flip_across_link_fails_closed_on_link_bond() -> None:
    coords, elements, graph = _linked_rings()
    specs = parse_ring_specs(
        [
            {"id": "a", "atoms": [0, 1, 2, 3, 4, 5]},
            {"id": "b", "atoms": [6, 7, 8, 9, 10, 11]},
        ]
    )
    with pytest.raises(RingGeometryFailure, match="link_bond"):
        realize_rings(coords, elements, graph, specs, {"a": "boat_6", "b": "chair_A_6"})


def test_preserve_input_reports_measurement() -> None:
    coords = template_coords(get_template("boat_6"))
    specs = parse_ring_specs(
        [{"id": "r1", "atoms": [0, 1, 2, 3, 4, 5], "treatment": "preserve_input"}]
    )
    output = realize_rings(coords, ["C"] * 6, _ring_adjacency(6), specs, {})
    assert output.preserved
    np.testing.assert_allclose(np.asarray(output.coordinates), coords)
    assert output.states[0]["template"] == "boat_6"
    assert set(output.states[0]) == {"template", "torsions", "anchor", "direction"}
    assert output.audits[0]["treatment"] == "preserve_input"


def test_stage_estimate_and_lazy_enumeration() -> None:
    stage = RingStage(
        {
            "rings": [
                {"id": "r1", "atoms": [0, 1, 2, 3, 4, 5]},
                {"id": "r0", "atoms": [6, 7, 8, 9]},
            ]
        }
    )
    assert [spec.id for spec in stage.specs] == ["r0", "r1"]
    assert stage.axis == "rings"
    record = _record("seed", ["C"] * 10, np.zeros((10, 3)))
    graph = _ring_adjacency(6) + [[v + 6 for v in row] for row in _ring_adjacency(4)]
    context = _context_for(record, graph)
    parent = _parent_of(record)
    estimate = stage.estimate(parent, context)
    assert estimate.exact is True
    assert estimate.declared_count == 4 * 3
    assert estimate.upper_bound == 4 * 3
    assert "basis" in estimate.details
    targets = stage.enumerate_targets(parent, context)
    assert isinstance(targets, Iterator)
    first = next(targets)
    assert first.target_id == "rings:000000"
    assert first.ordinal == 0
    assert set(first.state_value) == {"r0", "r1"}
    assert first.provenance["treatments"] == {"r0": "enumerate", "r1": "enumerate"}
    commanded = first.state_value["r1"]
    assert set(commanded) == {"template", "torsions", "anchor", "direction"}
    rest = list(targets)
    assert len(rest) == 12 - 1
    assert rest[-1].target_id == "rings:000011"
    assert rest[-1].ordinal == 11


def test_axis_spec_index_base_1_converts_once() -> None:
    # Raw workflow section declares index_base: 1 (converted once); resolved
    # normalized sections are internal-0 and used as-is (never subtracted).
    raw = RingStage({"index_base": 1, "rings": [{"id": "r1", "atoms": [1, 2, 3, 4, 5, 6]}]})
    assert raw.specs[0].atoms == (0, 1, 2, 3, 4, 5)
    internal = RingStage({"rings": [{"id": "r1", "atoms": [0, 1, 2, 3, 4, 5]}]})
    assert internal.specs[0].atoms == (0, 1, 2, 3, 4, 5)
    with pytest.raises(ValueError, match="index_base"):
        RingStage({"index_base": 2, "rings": []})
    coords = template_coords(get_template("chair_A_6"))
    record = _record("seed", ["C"] * 6, coords)
    context = _context_for(record, _ring_adjacency(6))
    parent = _parent_of(record)
    assert set(context.resolved_spec["index_convention"].split(":")[0].split("-")) >= {"internal"}
    assert context.resolved_spec["index_base"] == 0
    result = raw.realize(parent, next(raw.enumerate_targets(parent, context)), context)
    assert result.status == "realized"


def test_stage_realize_and_perceive_roundtrip_on_core_context() -> None:
    coords, _, _ = _methylcyclohexane()
    elements = ["C"] * 6 + ["C"]
    graph = _ring_adjacency(6)
    graph.append([0])
    graph[0].append(6)
    record = _record("seed", elements, coords)
    context = _context_for(record, graph)
    stage = RingStage({"rings": [{"id": "r1", "atoms": [0, 1, 2, 3, 4, 5]}]})
    parent = _parent_of(record)
    target = next(stage.enumerate_targets(parent, context))
    result = stage.realize(parent, target, context)
    assert result.status == "realized"
    assert result.backend == BACKEND_NAME
    assert result.structure is not None
    assert result.structure.id == "seed-rings-000000"
    assert result.structure.parent_ids[-1] == "seed"
    assert list(result.structure.atoms) == elements
    perception = stage.perceive(result.structure, context)
    assert set(perception.best_key) == {"r1"}
    state = perception.best_key["r1"]
    assert set(state) == {"template", "torsions", "anchor", "direction"}
    match, evidence = ring_states_match(target.state_value["r1"], state)
    assert match, evidence
    assert perception.confidence == "reported"


def test_stage_scope_and_diagnostics_placement() -> None:
    coords, _, _ = _methylcyclohexane()
    elements = ["C"] * 6 + ["C"]
    graph = _ring_adjacency(6)
    graph.append([0])
    graph[0].append(6)
    record = _record("seed", elements, coords)
    context = _context_for(record, graph)
    stage = RingStage({"rings": [{"id": "r1", "atoms": [0, 1, 2, 3, 4, 5]}]})
    parent = _parent_of(record)
    target = next(stage.enumerate_targets(parent, context))
    result = stage.realize(parent, target, context)
    assert result.status == "realized"
    audit = dict(result.evidence[0])
    assert audit["treatment"] == "enumerate"
    assert "diagnostics" in audit
    assert audit["diagnostics"]["confidence"] == "reported"
    assert "measured_torsions" in audit["diagnostics"]
    perception = stage.perceive(result.structure, context)
    assert "treatment" not in perception.best_key["r1"]
    assert "confidence" not in perception.best_key["r1"]
    assert "measured_torsions" not in perception.best_key["r1"]
    assert stage.axis_ids(context) == ("r1",)


def test_stage_audit_target_verifies_measured_geometry() -> None:
    coords, _, _ = _methylcyclohexane()
    elements = ["C"] * 6 + ["C"]
    graph = _ring_adjacency(6)
    graph.append([0])
    graph[0].append(6)
    record = _record("seed", elements, coords)
    context = _context_for(record, graph)
    stage = RingStage({"rings": [{"id": "r1", "atoms": [0, 1, 2, 3, 4, 5]}]})
    parent = _parent_of(record)
    target = next(stage.enumerate_targets(parent, context))
    result = stage.realize(parent, target, context)
    assert result.status == "realized"
    ok, measured, evidence = stage.audit_target(result.structure, target, parent, context)
    assert ok is True
    assert evidence == []
    assert set(measured) == {"r1"}
    assert measured["r1"] == commanded_state_dict(target.state_value["r1"]["template"], anchor=0)
    # Wrong commanded template against the same geometry fails the audit.
    bad = target.__class__(
        axis=target.axis,
        target_id=target.target_id,
        state_value={"r1": commanded_state_dict("boat_6", anchor=0)},
        ordinal=target.ordinal,
        provenance=dict(target.provenance),
    )
    ok, measured, evidence = stage.audit_target(result.structure, bad, parent, context)
    assert ok is False
    assert evidence
    assert evidence[0]["kind"] == "drift"
    assert "measured_torsions" in evidence[0]


def test_stage_verify_locked_catches_tampering() -> None:
    coords = template_coords(get_template("chair_A_6"))
    record = _record("seed", ["C"] * 6, coords)
    context = _context_for(record, _ring_adjacency(6))
    stage = RingStage({"rings": [{"id": "r1", "atoms": [0, 1, 2, 3, 4, 5]}]})
    parent = _parent_of(record)
    target = next(stage.enumerate_targets(parent, context))
    result = stage.realize(parent, target, context)
    assert result.status == "realized"
    locked = {"r1": commanded_state_dict("chair_A_6", anchor=0)}
    ok, snapped, evidence = stage.verify_locked(result.structure, locked, context)
    assert ok is True
    assert evidence == []
    assert snapped["r1"] == locked["r1"]
    # Physically distort one ring atom: the lock must fail, with measured
    # (not snapped) distortion evidence.
    tampered = np.asarray(result.structure.coordinates).copy()
    tampered[2] += np.array([0.0, 0.0, 0.25])
    tampered_record = _record("tampered", ["C"] * 6, tampered)
    ok, snapped, evidence = stage.verify_locked(tampered_record, locked, context)
    assert ok is False
    assert evidence
    assert evidence[0]["kind"] == "drift"
    assert evidence[0]["measured_torsions"] != evidence[0]["observed"]["torsions"]
    # Unknown locked ring id is a lock error, never silent acceptance.
    ok, _, evidence = stage.verify_locked(result.structure, {"nope": locked["r1"]}, context)
    assert ok is False
    assert evidence[0]["kind"] == "lock_error"


def test_stage_unsupported_fails_closed_explicitly() -> None:
    stage = RingStage({"rings": [{"id": "big", "atoms": list(range(8))}]})
    graph: list[list[int]] = [[] for _ in range(8)]
    for position in range(8):
        graph[position].append((position + 1) % 8)
        graph[position].append((position - 1) % 8)
    record = _record("seed", ["C"] * 8, np.zeros((8, 3)))
    context = _context_for(record, graph)
    parent = _parent_of(record)
    estimate = stage.estimate(parent, context)
    assert estimate.exact is True
    assert estimate.declared_count == 0
    assert estimate.details["unsupported"] != []
    with pytest.raises(ValueError, match="unsupported_ring_size"):
        next(stage.enumerate_targets(parent, context))
    stage4 = RingStage({"rings": [{"id": "r1", "atoms": [0, 1, 2, 3]}]})
    record4 = _record("seed", ["C"] * 4, np.zeros((4, 3)))
    context4 = _context_for(record4, _ring_adjacency(4))
    parent4 = _parent_of(record4)
    target = next(stage4.enumerate_targets(parent4, context4))
    bad_target = target.__class__(
        axis=target.axis,
        target_id=target.target_id,
        state_value={"r1": {"template": "sofa_4"}},
        ordinal=target.ordinal,
        provenance=dict(target.provenance),
    )
    result = stage4.realize(parent4, bad_target, context4)
    assert result.status == "unsupported"
    assert "unknown_template" in result.reason
    assert result.structure is None


def test_stage_refuses_coordination_overlap() -> None:
    coords = template_coords(get_template("chair_A_6"))
    record = _record("seed", ["C"] * 6, coords)
    context = _context_for(
        record,
        _ring_adjacency(6),
        extra={"coordination": {"metal_center": 1, "binding_sites": []}},
    )
    stage = RingStage({"rings": [{"id": "r1", "atoms": [0, 1, 2, 3, 4, 5]}]})
    parent = _parent_of(record)
    result = stage.realize(parent, next(stage.enumerate_targets(parent, context)), context)
    assert result.status == "unsupported"
    assert result.reason == "coordination_overlap"


def test_stage_perceive_marks_ambiguity() -> None:
    chair = template_coords(get_template("chair_A_6"))
    boat = template_coords(get_template("boat_6"))
    record = _record("blend", ["C"] * 6, 0.5 * (chair + boat))
    context = _context_for(record, _ring_adjacency(6))
    stage = RingStage({"rings": [{"id": "r1", "atoms": [0, 1, 2, 3, 4, 5]}]})
    perception = stage.perceive(record, context)
    assert perception.confidence == "ambiguous"
    assert perception.boundary_flags != ()
    assert len(perception.alternatives) >= 1


def test_perception_discovers_out_of_scope_template() -> None:
    # Enumeration restricted to chairs, but perception of a boat geometry
    # still honestly reports the boat template (no hidden scope filter).
    stage = RingStage(
        {"rings": [{"id": "r1", "atoms": [0, 1, 2, 3, 4, 5], "templates": ["chair_A_6"]}]}
    )
    record = _record("seed", ["C"] * 6, np.zeros((6, 3)))
    context = _context_for(record, _ring_adjacency(6))
    parent = _parent_of(record)
    assert stage.estimate(parent, context).declared_count == 1
    assert len(list(stage.enumerate_targets(parent, context))) == 1
    boat_record = _record("boat", ["C"] * 6, template_coords(get_template("boat_6")))
    perception = stage.perceive(boat_record, context)
    assert perception.best_key["r1"]["template"] == "boat_6"


def test_bond_length_nominal_documented() -> None:
    assert BOND_LENGTH == pytest.approx(1.54)
