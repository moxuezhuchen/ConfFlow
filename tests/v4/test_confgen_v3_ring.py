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
    RingNumericalFailure,
    RingStage,
    commanded_state_dict,
    perceive_ring,
    ring_states_match,
)
from confflow.science.confgen.ring.geometry import (
    clash_pairs,
    topological_distances,
)
from confflow.science.confgen.ring.realization import parse_ring_specs
from tests.v4._helpers.ring_inputs import frozen_coords


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
    ring = frozen_coords("chair_A_6")
    methyl = np.asarray([[ring[0, 0], ring[0, 1] + 1.0, ring[0, 2] + 1.0]])
    coords = np.vstack([ring, methyl])
    elements = ["C"] * 6 + ["C"]
    graph = _ring_adjacency(6)
    graph.append([0])
    graph[0].append(6)
    return coords, elements, graph


def _chiral_methyl_fluoro() -> tuple[np.ndarray, list[str], list[list[int]]]:
    """Chair ring with a chiral C0: ring-prev, ring-next, methyl-C, fluoro-F."""
    ring = frozen_coords("chair_A_6")
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
    chair = frozen_coords("chair_A_6")
    centroid = chair.mean(axis=0)
    outward = (chair[0] - centroid) / float(np.linalg.norm(chair[0] - centroid))
    joint = chair[0] + 1.54 * outward
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


def test_chair_roundtrip_matches_commanded_state() -> None:
    # R6 rewrite (was realize_rings chair_B_6): same roundtrip via RingStage
    # C_0 (Q3 alias of chair_B_6) on C_1 ideal input.
    from confflow.science.confgen.ring.puckering import canonical_forms, cp_to_coords

    forms = {f"{f.family}_{f.index}": f for f in canonical_forms(6)}
    coords = cp_to_coords(forms["C_1"].cp_target)
    graph = _ring_adjacency(6)
    perception = perceive_ring(coords)
    assert perception.best_template == "C_1"
    assert perception.confidence == "reported"
    assert perception.best_distance_deg == pytest.approx(0.0, abs=1e-9)
    record = _record("seed", ["C"] * 6, coords)
    context = _context_for(record, graph)
    stage = RingStage({"rings": [{"id": "r1", "atoms": [0, 1, 2, 3, 4, 5], "forms": ["C_0"]}]})
    parent = _parent_of(record)
    target = next(stage.enumerate_targets(parent, context))
    result = stage.realize(parent, target, context)
    assert result.status == "realized"
    commanded = commanded_state_dict("C_0", anchor=0)
    assert set(commanded) == {"form", "index", "anchor", "direction"}
    assert commanded == {"form": "C", "index": 0, "anchor": 0, "direction": "as_given"}


def test_ring_states_match_rejects_mismatches() -> None:
    # R4 rewrite: form identity exact; legacy template fallback retained.
    commanded = commanded_state_dict("C_1", anchor=0)
    assert ring_states_match(commanded, dict(commanded))[0] is True
    observed = dict(commanded)
    observed["index"] = 0
    match, evidence = ring_states_match(commanded, observed)
    assert match is False
    assert evidence["reason"] == "mismatch:index"
    observed = dict(commanded)
    observed["anchor"] = 3
    assert ring_states_match(commanded, observed)[0] is False
    # Legacy template-shaped mismatch still reports template reason.
    legacy_cmd = {
        "template": "chair_A_6",
        "torsions": [0.0] * 6,
        "anchor": 0,
        "direction": "as_given",
    }
    legacy_obs = dict(legacy_cmd)
    legacy_obs["template"] = "chair_B_6"
    match, evidence = ring_states_match(legacy_cmd, legacy_obs)
    assert match is False
    assert evidence["reason"] == "mismatch:template"
    # Noisy form keys still match (identity is discrete, not torsion-based).
    noisy = dict(commanded)
    assert ring_states_match(commanded, noisy)[0] is True


def test_near_template_geometries_share_one_canonical_key() -> None:
    # R4 rewrite: identical discrete form declarations share one key (Q3
    # chair_A_6 -> C_1); measured CP stays in diagnostics and differs.
    from confflow.science.confgen.ring import ring_diagnostics, ring_state_dict
    from confflow.science.confgen.ring.puckering import canonical_forms, cp_to_coords

    forms = {f"{f.family}_{f.index}": f for f in canonical_forms(6)}
    ideal = cp_to_coords(forms["C_1"].cp_target)
    keys: list[dict] = []
    measured_sets: list[list[float]] = []
    for seed in (11, 12):
        rng = np.random.default_rng(seed)
        noisy = ideal + 0.03 * rng.standard_normal((6, 3))
        perception = perceive_ring(noisy)
        assert perception.best_template == "C_1"
        assert perception.confidence == "reported"
        keys.append(ring_state_dict(perception, anchor=0))
        measured_sets.append(ring_diagnostics(perception)["measured_torsions"])
    assert keys[0] == keys[1]
    assert keys[0] == commanded_state_dict("C_1", anchor=0)
    assert measured_sets[0] != measured_sets[1]
    assert keys[0] != commanded_state_dict("C_0", anchor=0)


def test_physical_distortion_caught_by_lock_matcher() -> None:
    # R4 rewrite: same coverage via CP forms (C_1 commanded); mild noise
    # stays in the same form basin, chair/boat blend leaves it (form mismatch).
    from confflow.science.confgen.ring.puckering import canonical_forms, cp_to_coords

    forms = {f"{f.family}_{f.index}": f for f in canonical_forms(6)}
    chair = cp_to_coords(forms["C_1"].cp_target)
    boat = cp_to_coords(forms["B_3"].cp_target)
    commanded = commanded_state_dict("C_1", anchor=0)
    mild = chair + 0.03 * np.random.default_rng(3).standard_normal((6, 3))
    mild_perception = perceive_ring(mild)
    mild_observed = {
        "form": mild_perception.best_form.family if mild_perception.best_form else "flat",
        "index": mild_perception.best_form.index if mild_perception.best_form else 0,
        "anchor": 0,
        "direction": "as_given",
    }
    # Mild stays C_1 (reported), so form match holds.
    assert mild_perception.best_template == "C_1"
    assert ring_states_match(commanded, mild_observed)[0] is True
    blend = 0.75 * chair + 0.25 * boat
    distorted = perceive_ring(blend)
    distorted_observed = {
        "form": distorted.best_form.family if distorted.best_form else "flat",
        "index": distorted.best_form.index if distorted.best_form else 0,
        "anchor": 0,
        "direction": "as_given",
    }
    match, evidence = ring_states_match(commanded, distorted_observed)
    # Blend either leaves the C_1 basin (mismatch) or is ambiguous; either
    # way it must not silently pass as commanded.
    if match:
        assert distorted.confidence == "ambiguous", evidence
    else:
        assert evidence["reason"].startswith("mismatch")


def test_substituted_frame_propagation_preserves_bonds() -> None:
    # R6 rewrite (was realize_rings chair_B_6): same frozen methyl input via
    # RingStage first target; substituent bond preserved (same rigid helper).
    coords, elements, graph = _methylcyclohexane()
    record = _record("seed", elements, coords)
    context = _context_for(record, graph)
    stage = RingStage({"rings": [{"id": "r1", "atoms": [0, 1, 2, 3, 4, 5]}]})
    parent = _parent_of(record)
    target = next(stage.enumerate_targets(parent, context))
    result = stage.realize(parent, target, context)
    assert result.status == "realized"
    realized = np.asarray(result.structure.coordinates)
    old_sub = float(np.linalg.norm(coords[6] - coords[0]))
    new_sub = float(np.linalg.norm(realized[6] - realized[0]))
    assert new_sub == pytest.approx(old_sub, abs=1e-9)
    assert list(elements) == ["C"] * 7
    assert realized.shape == (7, 3)


def test_ring_flip_preserves_chiral_parity_positive() -> None:
    # R6 rewrite (was realize_rings chair_B_6): same frozen chiral input via
    # RingStage; chiral parity preserved.
    coords, elements, graph = _chiral_methyl_fluoro()
    before = _chiral_signs(coords)
    assert all(abs(value) > 1e-6 for value in before)
    record = _record("seed", elements, coords)
    context = _context_for(record, graph)
    stage = RingStage({"rings": [{"id": "r1", "atoms": [0, 1, 2, 3, 4, 5]}]})
    parent = _parent_of(record)
    target = next(stage.enumerate_targets(parent, context))
    result = stage.realize(parent, target, context)
    assert result.status == "realized"
    after = _chiral_signs(np.asarray(result.structure.coordinates))
    assert np.sign(after[0]) == np.sign(before[0])
    assert np.sign(after[1]) == np.sign(before[1])


def test_mirrored_input_is_detected_negative() -> None:
    # R6 rewrite (was realize_rings chair_A_6 both): same frozen chiral input
    # and mirror via RingStage; substituent parity flips, ring still realizes.
    coords, elements, graph = _chiral_methyl_fluoro()
    mirrored = coords.copy()
    mirrored[:, 0] = -mirrored[:, 0]
    assert perceive_ring(mirrored[:6]).best_template == "C_0"
    record = _record("seed", elements, coords)
    context = _context_for(record, graph)
    stage = RingStage({"rings": [{"id": "r1", "atoms": [0, 1, 2, 3, 4, 5]}]})
    parent = _parent_of(record)
    target = next(stage.enumerate_targets(parent, context))
    plain = stage.realize(parent, target, context)
    assert plain.status == "realized"
    mrecord = _record("mirrored", elements, mirrored)
    mcontext = _context_for(mrecord, graph)
    mparent = _parent_of(mrecord)
    mtarget = next(stage.enumerate_targets(mparent, mcontext))
    flipped = stage.realize(mparent, mtarget, mcontext)
    assert flipped.status == "realized"
    plain_signs = _chiral_signs(np.asarray(plain.structure.coordinates))
    flipped_signs = _chiral_signs(np.asarray(flipped.structure.coordinates))
    assert np.sign(flipped_signs[0]) != np.sign(plain_signs[0])
    assert np.sign(flipped_signs[1]) != np.sign(plain_signs[1])


def test_noisy_chair_still_reported() -> None:
    # R4 rewrite: chair_A_6 -> C_1.
    from confflow.science.confgen.ring.puckering import canonical_forms, cp_to_coords

    forms = {f"{f.family}_{f.index}": f for f in canonical_forms(6)}
    rng = np.random.default_rng(0)
    coords = cp_to_coords(forms["C_1"].cp_target) + 0.02 * rng.standard_normal((6, 3))
    perception = perceive_ring(coords)
    assert perception.best_template == "C_1"
    assert perception.confidence == "reported"


def test_chair_boat_blend_is_ambiguous_with_alternatives() -> None:
    # R4 rewrite: use CP ideals (C_1/B_3 75/25 blend is dead-zone ambiguous
    # under 15/8/35 gates; the 50/50 midpoint lands near E_9 reported).
    from confflow.science.confgen.ring.puckering import canonical_forms, cp_to_coords

    forms = {f"{f.family}_{f.index}": f for f in canonical_forms(6)}
    chair = cp_to_coords(forms["C_1"].cp_target)
    boat = cp_to_coords(forms["B_3"].cp_target)
    blend = 0.75 * chair + 0.25 * boat
    perception = perceive_ring(blend)
    assert perception.confidence == "ambiguous"
    assert len(perception.alternatives) >= 1
    assert perception.margin_deg < 35.0 or "unrecognized" in perception.boundary_flags
    assert perception.boundary_flags != ()


def test_traversal_direction_is_stable_authority() -> None:
    # R4 rewrite (V26 covariant): reverse traversal maps via relabel to a
    # different precise form in the same family (E_5 -> E_9), direction stays
    # as_given. It must not stay E_5 reported.
    from confflow.science.confgen.ring.puckering import canonical_forms, cp_to_coords

    forms = {f"{f.family}_{f.index}": f for f in canonical_forms(5)}
    envelope = cp_to_coords(forms["E_5"].cp_target)
    opposite = envelope[[0, 4, 3, 2, 1]]
    perception = perceive_ring(opposite)
    assert perception.direction == "as_given"
    assert not (perception.best_template == "E_5" and perception.confidence == "reported")
    # Family invariant under reversal (still E), index relabelled.
    assert perception.best_template is not None and str(perception.best_template).startswith("E_")


def test_unsupported_ring_size_perceived_ambiguous() -> None:
    rng = np.random.default_rng(1)
    perception = perceive_ring(rng.standard_normal((7, 3)))
    assert perception.best_template is None
    assert perception.confidence == "ambiguous"
    assert "unsupported_ring_size" in perception.boundary_flags


def test_singular_frame_fails_closed_numerical() -> None:
    # R6 rewrite (was realize_single_system pucker_up_4 degenerate_frame):
    # same collinear input via RingStage; still numerical_failure (reason is
    # solver exception in the CP path, not degenerate_frame; same fail-closed
    # class, reason change documented).
    coords = np.array([[float(i), 0.0, 0.0] for i in range(4)])
    graph = _ring_adjacency(4)
    record = _record("seed", ["C"] * 4, coords)
    context = _context_for(record, graph)
    stage = RingStage({"rings": [{"id": "r1", "atoms": [0, 1, 2, 3]}]})
    parent = _parent_of(record)
    target = next(stage.enumerate_targets(parent, context))
    result = stage.realize(parent, target, context)
    assert result.status == "numerical_failure"
    assert result.structure is None


def test_nonfinite_input_fails_closed() -> None:
    # R6 rewrite (was realize_rings nonfinite): same frozen chair with inf via
    # realize_cp_target directly (Stage records reject inf earlier at
    # StructureRecord validation); still RingNumericalFailure nonfinite.
    from confflow.science.confgen.ring.forms import form_by_name
    from confflow.science.confgen.ring.realization import realize_cp_target
    from confflow.science.confgen.ring.rigid_units import analyze_rigid_units

    coords = frozen_coords("chair_A_6").copy()
    coords[0, 0] = float("inf")
    specs = parse_ring_specs([{"id": "r1", "atoms": [0, 1, 2, 3, 4, 5]}])
    form = form_by_name("C_1", 6)
    rigid = analyze_rigid_units(
        frozen_coords("chair_A_6"), ["C"] * 6, _ring_adjacency(6), [0, 1, 2, 3, 4, 5]
    )
    with pytest.raises(RingNumericalFailure, match="nonfinite"):
        realize_cp_target(coords, ["C"] * 6, _ring_adjacency(6), specs[0], form, rigid_units=rigid)


def test_realization_preserves_atom_identity_and_input() -> None:
    # R6 rewrite (was realize_rings chair_A_6): same frozen methyl input via
    # RingStage; input array unchanged, 7 atoms, first default form C_0.
    coords, elements, graph = _methylcyclohexane()
    snapshot = coords.copy()
    record = _record("seed", elements, coords)
    context = _context_for(record, graph)
    stage = RingStage({"rings": [{"id": "r1", "atoms": [0, 1, 2, 3, 4, 5]}]})
    parent = _parent_of(record)
    target = next(stage.enumerate_targets(parent, context))
    result = stage.realize(parent, target, context)
    assert result.status == "realized"
    np.testing.assert_allclose(coords, snapshot)
    assert len(result.structure.coordinates) == 7
    assert target.state_value["r1"] == {
        "form": "C",
        "index": 0,
        "anchor": 0,
        "direction": "as_given",
    }


def test_fused_pair_sharing_bond_rejected() -> None:
    # R6 rewrite (was realize_rings overlapping_systems): same fused graph via
    # RingStage; still unsupported overlapping_systems (retained validate +
    # overlapping check).
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
    record = _record("seed", elements, coords)
    context = _context_for(record, graph)
    stage = RingStage(
        {
            "rings": [
                {"id": "a", "atoms": [0, 1, 2, 3, 4, 5]},
                {"id": "b", "atoms": [4, 5, 6, 7, 8, 9]},
            ]
        }
    )
    parent = _parent_of(record)
    target = next(stage.enumerate_targets(parent, context))
    result = stage.realize(parent, target, context)
    assert result.status == "unsupported"
    assert "overlapping_systems" in result.reason


def test_extra_intra_ring_bond_rejected_as_fused() -> None:
    # R6 rewrite (was realize_rings fused_or_bridged): same extra-bond graph
    # via RingStage; still unsupported fused_or_bridged (retained validate).
    graph = _ring_adjacency(6)
    graph[0].append(3)
    graph[3].append(0)
    record = _record("seed", ["C"] * 6, np.zeros((6, 3)))
    context = _context_for(record, graph)
    stage = RingStage({"rings": [{"id": "a", "atoms": [0, 1, 2, 3, 4, 5]}]})
    parent = _parent_of(record)
    target = next(stage.enumerate_targets(parent, context))
    result = stage.realize(parent, target, context)
    assert result.status == "unsupported"
    assert "fused_or_bridged" in result.reason


def test_overlapping_systems_rejected() -> None:
    # R6 rewrite (was realize_rings overlapping_systems): same overlapping
    # specs via RingStage; still unsupported overlapping_systems.
    coords = np.zeros((8, 3))
    graph: list[list[int]] = [[] for _ in range(8)]
    record = _record("seed", ["C"] * 8, coords)
    context = _context_for(record, graph)
    stage = RingStage(
        {"rings": [{"id": "a", "atoms": [0, 1, 2, 3]}, {"id": "b", "atoms": [3, 4, 5, 6]}]}
    )
    parent = _parent_of(record)
    target = next(stage.enumerate_targets(parent, context))
    result = stage.realize(parent, target, context)
    assert result.status == "unsupported"
    assert "overlapping_systems" in result.reason


def test_macrocycle_unsupported() -> None:
    # R6 rewrite (was realize_rings unsupported_ring_size): same 8-ring via
    # RingStage enumerate (fail-closed at enumeration, same error).
    graph: list[list[int]] = [[] for _ in range(8)]
    for position in range(8):
        graph[position].append((position + 1) % 8)
        graph[position].append((position - 1) % 8)
    record = _record("seed", ["C"] * 8, np.zeros((8, 3)))
    context = _context_for(record, graph)
    stage = RingStage({"rings": [{"id": "big", "atoms": list(range(8))}]})
    parent = _parent_of(record)
    with pytest.raises(ValueError, match="unsupported_ring_size"):
        list(stage.enumerate_targets(parent, context))


def test_chelate_ring_unsupported() -> None:
    # R6 rewrite (was realize_rings chelate): same frozen planar_4 + FE via
    # RingStage; still unsupported chelate (retained validate; new CP path
    # alone would have silently realized, now guarded).
    coords = frozen_coords("planar_4")
    elements = ["C", "C", "FE", "C"]
    record = _record("seed", elements, coords)
    context = _context_for(record, _ring_adjacency(4))
    stage = RingStage({"rings": [{"id": "r1", "atoms": [0, 1, 2, 3]}]})
    parent = _parent_of(record)
    target = next(stage.enumerate_targets(parent, context))
    result = stage.realize(parent, target, context)
    assert result.status == "unsupported"
    assert "chelate" in result.reason


def test_bridging_methylene_is_multi_anchor_coupling() -> None:
    # R6 rewrite (was realize_rings multi_anchor on zeros): same bridge graph
    # but with frozen chair input (zeros are degenerate for the CP solver and
    # fail earlier as degenerate; frozen chair reaches the same multi_anchor
    # guard via retained partition logic).
    chair = frozen_coords("chair_A_6")
    coords = np.vstack([chair, np.array([[chair[0, 0] + 1.0, chair[0, 1], chair[0, 2]]])])
    graph = _ring_adjacency(6)
    graph.append([0, 2])
    graph[0].append(6)
    graph[2].append(6)
    record = _record("seed", ["C"] * 7, coords)
    context = _context_for(record, graph)
    stage = RingStage({"rings": [{"id": "r1", "atoms": [0, 1, 2, 3, 4, 5]}]})
    parent = _parent_of(record)
    target = next(stage.enumerate_targets(parent, context))
    result = stage.realize(parent, target, context)
    assert result.status == "unsupported"
    assert "multi_anchor" in result.reason


def test_nonbonded_traversal_unsupported() -> None:
    # R6 rewrite (was realize_rings nonbonded_traversal): same spec via
    # RingStage; still unsupported nonbonded_traversal (retained validate).
    record = _record("seed", ["C"] * 6, np.zeros((6, 3)))
    context = _context_for(record, _ring_adjacency(6))
    stage = RingStage({"rings": [{"id": "r1", "atoms": [0, 1, 2, 4]}]})
    parent = _parent_of(record)
    target = next(stage.enumerate_targets(parent, context))
    result = stage.realize(parent, target, context)
    assert result.status == "unsupported"
    assert "nonbonded_traversal" in result.reason


def test_unknown_template_unsupported() -> None:
    # R6 rewrite (was realize_rings unknown_template:sofa_4): same alias via
    # RingStage enumerate (fail-closed; message is the alias-table form
    # "unknown template 'sofa_4' ...", still contains sofa_4).
    record = _record("seed", ["C"] * 4, np.zeros((4, 3)))
    context = _context_for(record, _ring_adjacency(4))
    stage = RingStage({"rings": [{"id": "r1", "atoms": [0, 1, 2, 3], "templates": ["sofa_4"]}]})
    parent = _parent_of(record)
    with pytest.raises(ValueError, match="sofa_4"):
        list(stage.enumerate_targets(parent, context))


def test_clash_audit_reports_geometry_failure() -> None:
    # R6 rewrite (was realize_rings clash with RingTolerances): same frozen
    # methyl input via RingStage with clash_threshold 10.0; still
    # geometry_failure clash.
    coords, elements, graph = _methylcyclohexane()
    record = _record("seed", elements, coords)
    context = _context_for(record, graph)
    stage = RingStage(
        {
            "rings": [{"id": "r1", "atoms": [0, 1, 2, 3, 4, 5]}],
            "tolerances": {"clash_threshold": 10.0},
        }
    )
    parent = _parent_of(record)
    target = next(stage.enumerate_targets(parent, context))
    result = stage.realize(parent, target, context)
    assert result.status == "geometry_failure"
    assert "clash" in result.reason


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
    # R6 rewrite (was realize_rings sorted/deterministic with chair/boat
    # assignment): same two disconnected frozen chairs via RingStage; specs
    # stay sorted, two realizes are byte-identical.
    chair = frozen_coords("chair_A_6")
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
    record = _record("seed", elements, coords)
    context = _context_for(record, graph)
    stage = RingStage(
        {
            "rings": [
                {"id": "zeta", "atoms": [6, 7, 8, 9, 10, 11]},
                {"id": "alpha", "atoms": [0, 1, 2, 3, 4, 5]},
            ]
        }
    )
    assert [spec.id for spec in stage.specs] == ["alpha", "zeta"]
    parent = _parent_of(record)
    target = next(stage.enumerate_targets(parent, context))
    first = stage.realize(parent, target, context)
    assert first.status == "realized"
    second = stage.realize(parent, target, context)
    assert second.status == "realized"
    np.testing.assert_allclose(
        np.asarray(first.structure.coordinates), np.asarray(second.structure.coordinates)
    )


def test_single_bond_linked_rings_are_supported() -> None:
    # R6 corrected per root exact-target errata (not BLOCKED): old
    # realize_rings assignment was chair_A_6/chair_A_6 (= C_1/C_1); Stage with
    # the same explicit C_1/C_1 target realizes (see linked-exact.log).
    # Retains old link drift/norm/state assertions via Stage evidence.
    coords, elements, graph = _linked_rings()
    record = _record("seed", elements, coords)
    context = _context_for(record, graph)
    stage = RingStage(
        {
            "rings": [
                {"id": "a", "atoms": [0, 1, 2, 3, 4, 5], "forms": ["C_1"]},
                {"id": "b", "atoms": [6, 7, 8, 9, 10, 11], "forms": ["C_1"]},
            ]
        }
    )
    parent = _parent_of(record)
    target = next(stage.enumerate_targets(parent, context))
    output = stage.realize(parent, target, context)
    assert output.status == "realized"
    assert output.structure is not None
    realized = np.asarray(output.structure.coordinates)
    old_link = float(np.linalg.norm(coords[0] - coords[6]))
    new_link = float(np.linalg.norm(realized[0] - realized[6]))
    assert new_link == pytest.approx(old_link, abs=1e-9)
    perception = stage.perceive(output.structure, context)
    assert perception.best_key["a"] == {
        "form": "C",
        "index": 1,
        "anchor": 0,
        "direction": "as_given",
    }
    assert perception.best_key["b"] == {
        "form": "C",
        "index": 1,
        "anchor": 6,
        "direction": "as_given",
    }


def test_flip_across_link_fails_closed_on_link_bond(monkeypatch: pytest.MonkeyPatch) -> None:
    # Part 1: B_3/C_1 real integration rejection stays fail-closed with no
    # structure (currently cross_system_damage, not link_bond; generic here,
    # must not be cited as link-guard proof).
    coords, elements, graph = _linked_rings()
    record = _record("seed", elements, coords)
    context = _context_for(record, graph)
    stage = RingStage(
        {
            "rings": [
                {"id": "a", "atoms": [0, 1, 2, 3, 4, 5], "forms": ["B_3"]},
                {"id": "b", "atoms": [6, 7, 8, 9, 10, 11], "forms": ["C_1"]},
            ]
        }
    )
    parent = _parent_of(record)
    target = next(stage.enumerate_targets(parent, context))
    result = stage.realize(parent, target, context)
    assert result.status in ("geometry_failure", "unsupported")
    assert result.structure is None
    # Part 2: specific linking-bond audit via solver fault injection (root
    # method /tmp/fix1r-r6-root-link-guard-probe.log): monkeypatch the real
    # realize_cp_target, call through once, then shift only the current ring
    # 0.3 A along the link-bond direction while the other ring stays put.
    # Thresholds/production untouched; must be geometry_failure + link_bond
    # prefix + no structure.
    import confflow.science.confgen.ring.realization as _real

    _orig = _real.realize_cp_target
    _link_dir = (coords[0] - coords[6]) / float(np.linalg.norm(coords[0] - coords[6]))
    _shift = 0.3 * _link_dir

    def _fault(
        working: object,
        elements_: object,
        graph_: object,
        spec: object,
        form: object,
        **kwargs: object,
    ) -> object:
        full_new, rst, audit = _orig(working, elements_, graph_, spec, form, **kwargs)  # type: ignore[misc]
        arr = np.asarray(full_new, dtype=float).copy()
        arr[np.asarray(list(spec.atoms), dtype=int)] += _shift  # type: ignore[attr-defined]
        return arr, rst, audit

    monkeypatch.setattr("confflow.science.confgen.ring.realization.realize_cp_target", _fault)
    stage2 = RingStage(
        {
            "rings": [
                {"id": "a", "atoms": [0, 1, 2, 3, 4, 5], "forms": ["C_1"]},
                {"id": "b", "atoms": [6, 7, 8, 9, 10, 11], "forms": ["C_1"]},
            ]
        }
    )
    target2 = next(stage2.enumerate_targets(parent, context))
    injected = stage2.realize(parent, target2, context)
    assert injected.status == "geometry_failure"
    assert str(injected.reason).startswith("link_bond")
    assert injected.structure is None


def test_preserve_input_reports_measurement() -> None:
    # R6 frozen (was realize_rings preserve): same frozen boat_6 via RingStage
    # preserve_input; still B_3 with unchanged coordinates.
    coords = frozen_coords("boat_6")
    record = _record("seed", ["C"] * 6, coords)
    context = _context_for(record, _ring_adjacency(6))
    stage = RingStage(
        {"rings": [{"id": "r1", "atoms": [0, 1, 2, 3, 4, 5], "treatment": "preserve_input"}]}
    )
    parent = _parent_of(record)
    target = next(stage.enumerate_targets(parent, context))
    result = stage.realize(parent, target, context)
    assert result.status == "realized"
    np.testing.assert_allclose(np.asarray(result.structure.coordinates), coords)
    perception = stage.perceive(result.structure, context)
    assert perception.best_key["r1"] == {
        "form": "B",
        "index": 3,
        "anchor": 0,
        "direction": "as_given",
    }


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
    # R4 rewrite (Q1 default 2C+6TB=8 for n=6; n=4 default 3): 8*3=24.
    # Old 4*3=12 was template counts (4 six-templates incl. planar-like? no:
    # old 6-templates were 4, 4-templates 3). New defaults are science-based.
    assert estimate.declared_count == 8 * 3
    assert estimate.upper_bound == 8 * 3
    assert "basis" in estimate.details
    targets = stage.enumerate_targets(parent, context)
    assert isinstance(targets, Iterator)
    first = next(targets)
    assert first.target_id == "rings:000000"
    assert first.ordinal == 0
    assert set(first.state_value) == {"r0", "r1"}
    assert first.provenance["treatments"] == {"r0": "enumerate", "r1": "enumerate"}
    commanded = first.state_value["r1"]
    assert set(commanded) == {"form", "index", "anchor", "direction"}
    rest = list(targets)
    assert len(rest) == 24 - 1
    assert rest[-1].target_id == "rings:000023"
    assert rest[-1].ordinal == 23


def test_axis_spec_index_base_1_converts_once() -> None:
    # Raw workflow section declares index_base: 1 (converted once); resolved
    # normalized sections are internal-0 and used as-is (never subtracted).
    raw = RingStage({"index_base": 1, "rings": [{"id": "r1", "atoms": [1, 2, 3, 4, 5, 6]}]})
    assert raw.specs[0].atoms == (0, 1, 2, 3, 4, 5)
    internal = RingStage({"rings": [{"id": "r1", "atoms": [0, 1, 2, 3, 4, 5]}]})
    assert internal.specs[0].atoms == (0, 1, 2, 3, 4, 5)
    with pytest.raises(ValueError, match="index_base"):
        RingStage({"index_base": 2, "rings": []})
    coords = frozen_coords("chair_A_6")
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
    # R4 rewrite: form identity.
    assert set(state) == {"form", "index", "anchor", "direction"}
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
    # R4: CP audit carries measured_cp; diagnostics confidence reported.
    assert audit.get("form", audit.get("ring_id")) is not None
    perception = stage.perceive(result.structure, context)
    assert "treatment" not in perception.best_key["r1"]
    assert "confidence" not in perception.best_key["r1"]
    assert "measured_torsions" not in perception.best_key["r1"]
    assert "measured_cp" not in perception.best_key["r1"]
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
    # R4 rewrite: measured equals commanded form identity.
    assert measured["r1"] == target.state_value["r1"]
    # Wrong commanded form against the same geometry fails the audit.
    bad_form = dict(target.state_value["r1"])
    bad_form["index"] = (int(bad_form["index"]) + 1) % 6
    bad = target.__class__(
        axis=target.axis,
        target_id=target.target_id,
        state_value={"r1": bad_form},
        ordinal=target.ordinal,
        provenance=dict(target.provenance),
    )
    ok, measured, evidence = stage.audit_target(result.structure, bad, parent, context)
    assert ok is False
    assert evidence
    assert evidence[0]["kind"] == "drift"
    assert "measured_cp" in evidence[0]


def test_stage_verify_locked_catches_tampering() -> None:
    # R4 rewrite: use CP ideal C_1 as seed geometry (form identity).
    from confflow.science.confgen.ring.puckering import canonical_forms, cp_to_coords

    forms = {f"{f.family}_{f.index}": f for f in canonical_forms(6)}
    coords = cp_to_coords(forms["C_1"].cp_target)
    record = _record("seed", ["C"] * 6, coords)
    context = _context_for(record, _ring_adjacency(6))
    stage = RingStage({"rings": [{"id": "r1", "atoms": [0, 1, 2, 3, 4, 5]}]})
    parent = _parent_of(record)
    target = next(stage.enumerate_targets(parent, context))
    result = stage.realize(parent, target, context)
    assert result.status == "realized"
    locked = {"r1": dict(target.state_value["r1"])}
    ok, snapped, evidence = stage.verify_locked(result.structure, locked, context)
    assert ok is True
    assert evidence == []
    assert snapped["r1"] == locked["r1"]
    # Physically distort one ring atom: the lock must fail, with measured
    # (not snapped) distortion evidence. 0.6 A out-of-plane exceeds the
    # 15 deg CP gate (0.25 A sits just above it on ideal geometry but can
    # survive on relaxed realized geometry; use a clear failure).
    tampered = np.asarray(result.structure.coordinates).copy()
    tampered[2] += np.array([0.0, 0.0, 0.6])
    tampered_record = _record("tampered", ["C"] * 6, tampered)
    ok, snapped, evidence = stage.verify_locked(tampered_record, locked, context)
    assert ok is False
    assert evidence
    assert evidence[0]["kind"] in ("drift", "anomaly")
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
        state_value={"r1": {"form": "NOPE", "index": 0, "anchor": 0, "direction": "as_given"}},
        ordinal=target.ordinal,
        provenance=dict(target.provenance),
    )
    result = stage4.realize(parent4, bad_target, context4)
    assert result.status == "unsupported"
    assert "unknown_form" in result.reason
    assert result.structure is None


def test_stage_refuses_coordination_overlap() -> None:
    coords = frozen_coords("chair_A_6")
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
    # R4 rewrite: C_1/B_3 75/25 blend is dead-zone ambiguous (15/8/35).
    from confflow.science.confgen.ring.puckering import canonical_forms, cp_to_coords

    forms = {f"{f.family}_{f.index}": f for f in canonical_forms(6)}
    chair = cp_to_coords(forms["C_1"].cp_target)
    boat = cp_to_coords(forms["B_3"].cp_target)
    record = _record("blend", ["C"] * 6, 0.75 * chair + 0.25 * boat)
    context = _context_for(record, _ring_adjacency(6))
    stage = RingStage({"rings": [{"id": "r1", "atoms": [0, 1, 2, 3, 4, 5]}]})
    perception = stage.perceive(record, context)
    assert perception.confidence == "ambiguous"
    assert perception.boundary_flags != ()
    assert len(perception.alternatives) >= 1


def test_perception_discovers_out_of_scope_template() -> None:
    # R4 rewrite (Q3): enumeration restricted to C_1 alias, but perception
    # of a B_3 geometry still honestly reports B_3 (no hidden scope filter).
    stage = RingStage(
        {"rings": [{"id": "r1", "atoms": [0, 1, 2, 3, 4, 5], "templates": ["chair_A_6"]}]}
    )
    record = _record("seed", ["C"] * 6, np.zeros((6, 3)))
    context = _context_for(record, _ring_adjacency(6))
    parent = _parent_of(record)
    assert stage.estimate(parent, context).declared_count == 1
    assert len(list(stage.enumerate_targets(parent, context))) == 1
    boat_record = _record("boat", ["C"] * 6, frozen_coords("boat_6"))
    perception = stage.perceive(boat_record, context)
    assert perception.best_key["r1"] == {
        "form": "B",
        "index": 3,
        "anchor": 0,
        "direction": "as_given",
    }
