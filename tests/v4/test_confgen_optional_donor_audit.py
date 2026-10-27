#!/usr/bin/env python3

"""Detached optional donors in the DG seeds and the search audit (C3, plan section 2).

A declared optional donor that is off in a sub-search is in ``CoordinationSpec.detached_donors``,
not in ``donor_indices``. It has a lower bound of 1.5 x its input distance to the metal, no core
pin and no metal-substituent bounds; it is never a restraint, never audited for distance or
orientation, and never a new coordination contact when it nears the metal again.
"""

from __future__ import annotations

import numpy as np
import pytest
import rdkit.DistanceGeometry as rdg

from confflow.science.confgen.dg_seed import (
    DETACHED_DISTANCE_FACTOR,
    DGSeedSettings,
    generate_dg_seeds,
)
from confflow.science.confgen.graph import INPUT_SHAPE, CoordinationSpec, EdgeType
from confflow.science.confgen.search import (
    SearchRecord,
    audit_structure,
    distance_restraints,
    optional_bound_state,
)
from tests.v4.test_confgen_input_core_dg import _load_input_core


def _spec(graph_spec: CoordinationSpec, detached: tuple[int, ...]) -> CoordinationSpec:
    """Return ``graph_spec`` with ``detached`` declared off (bound sites unchanged)."""
    return CoordinationSpec(
        metal_center=graph_spec.metal_center,
        binding_sites=graph_spec.binding_sites,
        shapes=(INPUT_SHAPE,),
        detached_donors=detached,
    )


def _split(name: str = "ti7_salan"):
    """Return (graph, full spec, reference, donors, detached atom, bound-only spec)."""
    graph, full, reference, donors = _load_input_core(name)
    detached = donors[-1]
    bound_donors = donors[:-1]
    bound_spec = CoordinationSpec(
        metal_center=full.metal_center,
        binding_sites=tuple(site for site in full.binding_sites if site.donor != detached),
        shapes=(INPUT_SHAPE,),
        detached_donors=(detached,),
    )
    assert bound_spec.donor_indices == tuple(bound_donors)
    return graph, full, reference, donors, detached, bound_spec


def _captured_bounds(monkeypatch, graph, spec, reference) -> np.ndarray:
    """Return the DG bounds matrix handed to RDKit triangle smoothing for one seed call."""
    seen: list[np.ndarray] = []
    original = rdg.DoTriangleSmoothing

    def spy(matrix):
        seen.append(np.array(matrix, dtype=float, copy=True))
        return original(matrix)

    monkeypatch.setattr(rdg, "DoTriangleSmoothing", spy)
    generate_dg_seeds(
        graph,
        spec,
        reference,
        list(range(len(spec.binding_sites))),
        INPUT_SHAPE,
        DGSeedSettings(count=1, random_seed=3, timeout_seconds=30),
    )
    assert len(seen) == 1
    return seen[0]


def _metal_pair(matrix: np.ndarray, metal: int, atom: int) -> tuple[float, float]:
    """Return (lower, upper) of the metal-atom pair in a bounds matrix."""
    low, high = (metal, atom) if metal < atom else (atom, metal)
    return float(matrix[high, low]), float(matrix[low, high])


def test_detached_donor_gets_open_upper_and_scaled_lower_bound(monkeypatch) -> None:
    graph, _full, reference, _donors, detached, spec = _split()
    matrix = _captured_bounds(monkeypatch, graph, spec, reference)
    ref = float(np.linalg.norm(reference[0] - reference[detached]))
    lower, upper = _metal_pair(matrix, 0, detached)
    assert lower == pytest.approx(DETACHED_DISTANCE_FACTOR * ref, abs=1.0e-6)
    assert upper > 5.0  # open: not the input distance plus a core tolerance


def test_detached_donor_is_not_pinned_to_its_input_distance(monkeypatch) -> None:
    graph, _full, reference, _donors, detached, spec = _split()
    matrix = _captured_bounds(monkeypatch, graph, spec, reference)
    ref = float(np.linalg.norm(reference[0] - reference[detached]))
    lower, upper = _metal_pair(matrix, 0, detached)
    assert not (lower > ref - 0.1 and upper < ref + 0.1)


def test_detached_donor_neighbours_have_no_metal_substituent_bound(monkeypatch) -> None:
    graph, full, reference, _donors, detached, spec = _split()
    matrix = _captured_bounds(monkeypatch, graph, spec, reference)
    neighbours = [n for n in graph.neighbors(detached, EdgeType.COVALENT) if n != spec.metal_center]
    assert neighbours
    for atom in neighbours:
        ref = float(np.linalg.norm(reference[0] - reference[atom]))
        lower, upper = _metal_pair(matrix, 0, atom)
        # a tight metal-substituent bound would sit within 0.25 of the input distance
        assert not (abs(lower - ref) < 0.25 and abs(upper - ref) < 0.25)


def test_empty_detached_matrices_are_identical(monkeypatch) -> None:
    graph, full, reference, _donors = _load_input_core("ti7_salan")
    plain = _captured_bounds(monkeypatch, graph, full, reference)
    explicit = _captured_bounds(monkeypatch, graph, _spec(full, ()), reference)
    assert np.array_equal(plain, explicit)


def test_detached_donor_must_not_be_a_bound_donor() -> None:
    with pytest.raises(ValueError, match="must not be bound donors"):
        _spec(_load_input_core("ti7_salan")[1], (_load_input_core("ti7_salan")[3][0],))


def test_distance_restraints_skip_detached_donor() -> None:
    graph, _full, reference, _donors, detached, spec = _split()
    pairs = {(r.i, r.j) for r in distance_restraints(graph, spec, reference)}
    assert (0, detached) not in pairs and (detached, 0) not in pairs
    assert len(pairs) == len(spec.donor_indices)


def test_contact_rule_ignores_detached_donor_nearing_metal() -> None:
    graph, full, reference, _donors, detached, spec = _split()
    near = np.array(reference, dtype=float)
    direction = near[detached] - near[0]
    near[detached] = near[0] + 0.75 * direction  # closer to the metal than its input site
    declared = audit_structure(near, graph, spec, reference, INPUT_SHAPE)
    assert "coordination_class" not in declared.failed_checks
    assert all(c.atom != detached for c in declared.coordination_contacts)
    # control: the same donor, not declared at all, is a new contact
    undeclared = audit_structure(
        near,
        graph,
        CoordinationSpec(
            metal_center=0,
            binding_sites=spec.binding_sites,
            shapes=(INPUT_SHAPE,),
        ),
        reference,
        INPUT_SHAPE,
    )
    assert "coordination_class" in undeclared.failed_checks


def test_audit_ignores_detached_donor_metal_distance_and_orientation() -> None:
    graph, _full, reference, _donors, detached, spec = _split()
    moved = np.array(reference, dtype=float)
    direction = moved[detached] - moved[0]
    moved[detached] = moved[0] + 1.5 * direction  # far from its input metal distance
    record = audit_structure(moved, graph, spec, reference, INPUT_SHAPE)
    assert "metal_donor_distance" not in record.failed_checks
    assert "donor_orientation" not in record.failed_checks


def test_audit_still_flags_bound_donor_metal_distance() -> None:
    graph, _full, reference, donors, _detached, spec = _split()
    moved = np.array(reference, dtype=float)
    bound = donors[0]
    moved[bound] = moved[0] + 1.5 * (moved[bound] - moved[0])  # control: bound donor moves
    record = audit_structure(moved, graph, spec, reference, INPUT_SHAPE)
    assert "metal_donor_distance" in record.failed_checks


def test_search_record_to_dict_unchanged_without_optional_bound() -> None:
    record = SearchRecord(
        target="t00", start=0, small_ring_torsions=True, energy_eh=-1.0, passed=True
    )
    payload = record.to_dict()
    assert "optional_bound" not in payload
    tagged = SearchRecord(
        target="t00",
        start=0,
        small_ring_torsions=True,
        energy_eh=-1.0,
        passed=True,
        optional_bound={105: True, 99: False},
    )
    assert tagged.to_dict()["optional_bound"] == {105: True, 99: False}
    assert set(tagged.to_dict()) == set(payload) | {"optional_bound"}


def test_optional_bound_state_thresholds() -> None:
    coords = np.zeros((4, 3))
    coords[1] = [2.0, 0.0, 0.0]  # 2.0 at ref 2.0: bound
    coords[2] = [2.5, 0.0, 0.0]  # 2.5 at ref 2.0: 1.25x, bound under 1.3x
    coords[3] = [2.7, 0.0, 0.0]  # 2.7 at ref 2.0: 1.35x, detached
    state = optional_bound_state(coords, 0, {1: 2.0, 2: 2.0, 3: 2.0})
    assert state == {1: True, 2: True, 3: False}
    assert optional_bound_state(coords, 0, {2: 2.0}, slack=1.1) == {2: False}
    assert optional_bound_state(coords, 0, {}) == {}
    with pytest.raises(ValueError):
        optional_bound_state(coords, 0, {1: 2.0}, slack=0.0)
