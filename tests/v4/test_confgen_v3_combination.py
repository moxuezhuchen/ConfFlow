#!/usr/bin/env python3

"""ConfGen v3 C/R/T combination test (ring-lane owned P3 file, no engine edits).

Independent 36-leaf run through the REAL default engine stages
(CoordinationStage / RingStage / TorsionStage, no fakes): a monodentate
tetrahedral center with 4 distinct sites (2 shape classes) x an isolated
cyclohexane chair_A/chair_B axis (2 templates) x two independent acyclic
4-atom absolute torsions (3 setpoints each). Fragments are unconnected
spectator-separated with realistic bond geometry; input atomic order is
preserved throughout.

Covers: 36 stable leaf ordinals with complete C/R/T keys, no intermediate
publication, real perception locks at leaves, certificate/count equations,
near-template perturbation key invariance, and an adversarial wrapper test
proving upstream tampering is caught (never silently published).
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping
from dataclasses import replace

import numpy as np

from confflow.domain.structure import StructureRecord
from confflow.science.confgen.accounting import (
    verify_count_equations,
    verify_terminal_equations,
)
from confflow.science.confgen.engine import ConfgenEngine, thaw_snapshot
from confflow.science.confgen.model import (
    GenerationTarget,
    MolecularContext,
    RealizationResult,
    WorkingRealization,
    build_context,
)
from confflow.science.confgen.ring.geometry import dihedral_deg
from confflow.science.confgen.ring.stage import RingStage
from tests.v4._helpers.ring_inputs import frozen_coords

TORSION_ANGLES = (60.0, 180.0, 300.0)
RING_TEMPLATES = ("chair_A_6", "chair_B_6")
# R4 alias (Q3): chair_A_6->C_1, chair_B_6->C_0 (form identity).
RING_FORMS = {("C", 1), ("C", 0)}


def _chain(origin: np.ndarray, dihedral_deg_: float = 180.0) -> np.ndarray:
    """Build a realistic butane-like C4 chain (1.54 A, 112 deg)."""
    bond, angle = 1.54, np.radians(112.0)
    p0 = np.array([0.0, 0.0, 0.0])
    p1 = np.array([bond, 0.0, 0.0])
    p2 = p1 + bond * np.array([-np.cos(np.pi - angle), np.sin(np.pi - angle), 0.0])
    b0, b1 = p1 - p0, p2 - p1
    e1 = b1 / np.linalg.norm(b1)
    tmp = b0 - np.dot(b0, e1) * e1
    normal = tmp / np.linalg.norm(tmp)
    perp = np.cross(e1, normal)
    dih = np.radians(dihedral_deg_)
    direction = -np.cos(np.pi - angle) * (-e1) + np.sin(np.pi - angle) * (
        np.cos(dih) * normal + np.sin(dih) * perp
    )
    direction = direction / np.linalg.norm(direction)
    p3 = p2 + bond * direction
    return np.vstack([p0, p1, p2, p3]) + origin


def _build_fixture(noise: float = 0.0, seed: int = 0) -> tuple[StructureRecord, dict]:
    """Build the 19-atom combination fixture plus its 1-based spec."""
    tetra = np.array([[1.0, 1.0, 1.0], [1.0, -1.0, -1.0], [-1.0, 1.0, -1.0], [-1.0, -1.0, 1.0]])
    tetra = tetra / np.linalg.norm(tetra[0]) * 2.0
    center = np.vstack([np.zeros((1, 3)), tetra])
    ring = frozen_coords("chair_A_6") + np.array([30.0, 0.0, 0.0])
    chain_a = _chain(np.array([-30.0, 0.0, 0.0]))
    chain_b = _chain(np.array([0.0, 30.0, 0.0]))
    coords = np.vstack([center, ring, chain_a, chain_b])
    if noise > 0.0:
        coords = coords + noise * np.random.default_rng(seed).standard_normal(coords.shape)
    elements = ["ZN", "N", "O", "S", "P"] + ["C"] * 14
    record = StructureRecord(
        id="combo-seed",
        atoms=tuple(elements),
        coordinates=tuple(tuple(float(v) for v in row) for row in coords),
    )
    covalent = [
        (5, 6),
        (6, 7),
        (7, 8),
        (8, 9),
        (9, 10),
        (10, 5),
        (11, 12),
        (12, 13),
        (13, 14),
        (15, 16),
        (16, 17),
        (17, 18),
    ]
    bonds = [{"atoms": [a + 1, b + 1], "kind": "COVALENT"} for a, b in covalent]
    for donor in range(1, 5):
        bonds.append({"atoms": [1, donor + 1], "kind": "COORDINATION"})
    spec = {
        "index_base": 1,
        "topology": {"bonds": bonds},
        "coordination": {
            "metal_center": 1,
            "binding_sites": [
                {"id": f"site{i}", "kind": "atom", "atoms": [i + 2], "hapticity": 1}
                for i in range(4)
            ],
            "shapes": ["tetrahedral"],
            "treatment": "enumerate",
        },
        "rings": [
            {
                "id": "ring1",
                "atoms": [6, 7, 8, 9, 10, 11],
                "templates": ["chair_A_6", "chair_B_6"],
            }
        ],
        "torsions": [
            {
                "id": "t1",
                "atoms": [12, 13, 14, 15],
                "model": "absolute_dihedral_grid",
                "angles": list(TORSION_ANGLES),
            },
            {
                "id": "t2",
                "atoms": [16, 17, 18, 19],
                "model": "absolute_dihedral_grid",
                "angles": list(TORSION_ANGLES),
            },
        ],
    }
    return record, spec


def _run_fixture(noise: float = 0.0, seed: int = 0):
    """Build the context and run the REAL default engine (all real stages)."""
    record, spec = _build_fixture(noise=noise, seed=seed)
    context = build_context(record, spec)
    run = ConfgenEngine().run(context)
    return context, run


def _normalize(value):
    """Normalize key forms for comparison (tuples become lists)."""
    if isinstance(value, Mapping):
        return {key: _normalize(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_normalize(item) for item in value]
    return value


def _leaf_key(leaf) -> dict:
    return leaf.state_key.to_dict()


def test_combination_36_leaves_real_stages() -> None:
    _, run = _run_fixture()
    assert len(run.leaves) == 36
    statuses = Counter(record.status.value for record in run.target_records)
    assert statuses["published_leaf"] == 36
    assert statuses.get("failed_geometry", 0) == 0
    assert statuses.get("failed_numerical", 0) == 0
    assert statuses.get("failed_drift", 0) == 0
    assert statuses.get("unsupported", 0) == 0
    assert not any("deferred" in status for status in statuses)
    # No intermediate publication: every published leaf comes from the last level.
    for record in run.target_records:
        if record.status.value == "published_leaf":
            assert record.axis == "torsions"
    # Stable unique leaf identities in deterministic DFS order.
    leaf_ids = [leaf.structure.id for leaf in run.leaves]
    assert len(set(leaf_ids)) == 36
    # Complete C/R/T keys on every leaf.
    coordinations, rings, torsions = set(), set(), Counter()
    for leaf in run.leaves:
        key = _leaf_key(leaf)
        coordination = key["coordination"]
        assert coordination["center"] == 0
        assert coordination["shape"] == "tetrahedral"
        coordinations.add(tuple(coordination["placement"]))
        ring = key["rings"]["ring1"]
        assert (ring["form"], ring["index"]) in RING_FORMS
        assert ring["anchor"] == 5
        assert ring["direction"] == "as_given"
        rings.add((ring["form"], ring["index"]))
        assert set(key["torsions"]) == {"t1", "t2"}
        for axis_id in ("t1", "t2"):
            value = float(key["torsions"][axis_id])
            assert min(abs((value - a + 180.0) % 360.0 - 180.0) for a in TORSION_ANGLES) < 1e-9
            torsions[(axis_id, round(value, 6))] += 1
    assert len(coordinations) == 2
    assert rings == RING_FORMS
    assert len(torsions) == 2 * 3
    assert all(count == 2 * 2 * 3 for count in torsions.values())
    combos = Counter()
    for leaf in run.leaves:
        torsions_key = _leaf_key(leaf)["torsions"]
        combos[(round(float(torsions_key["t1"]), 6), round(float(torsions_key["t2"]), 6))] += 1
    assert len(combos) == 3 * 3
    assert all(count == 2 * 2 for count in combos.values())
    # Input atomic order preserved on every leaf (canonical element symbols).
    for leaf in run.leaves:
        assert list(leaf.structure.atoms) == ["Zn", "N", "O", "S", "P"] + ["C"] * 14
    # Certificate and count equations.
    assert run.certificate.equations_ok is True
    ok, _ = verify_terminal_equations(run.target_records)
    assert ok is True
    counts = run.report_json()["counts"]
    ok, _ = verify_count_equations(run.target_records, raw=counts["raw"], sampled=counts["sampled"])
    assert ok is True
    # Deterministic rerun reproduces identical keys.
    _, rerun = _run_fixture()
    assert [_leaf_key(leaf) for leaf in rerun.leaves] == [_leaf_key(leaf) for leaf in run.leaves]


def test_combination_real_perception_locks() -> None:
    from confflow.science.confgen.coordination.stage import CoordinationStage, adapt_to_core

    context, run = _run_fixture()
    assert len(run.leaves) == 36
    resolved = context.resolved_spec
    ring_stage = RingStage(thaw_snapshot(resolved))
    coordination_stage = adapt_to_core(CoordinationStage(dict(resolved["coordination"])))
    for leaf in run.leaves:
        key = _leaf_key(leaf)
        # Ring lock: fresh canonical perception equals the locked key exactly.
        observed = ring_stage.perceive(leaf.structure, context).best_key["ring1"]
        assert observed == key["rings"]["ring1"]
        # Torsion locks: measured four-atom dihedrals match key setpoints.
        positions = np.asarray(leaf.structure.coordinates)
        frames = {"t1": (11, 12, 13, 14), "t2": (15, 16, 17, 18)}
        for axis_id, frame in frames.items():
            measured = dihedral_deg(*(positions[i] for i in frame))
            expected = float(key["torsions"][axis_id])
            assert abs((measured - expected + 180.0) % 360.0 - 180.0) < 1.0
        # Coordination lock: discrete re-perception equals the locked key
        # (normalized for container-type differences between key forms).
        perceived = coordination_stage.perceive(leaf.structure, context)
        assert _normalize(perceived.best_key) == _normalize(key["coordination"])


def test_combination_perturbation_key_invariance() -> None:
    _, run = _run_fixture()
    _, perturbed = _run_fixture(noise=0.02, seed=7)
    assert len(perturbed.leaves) == 36

    def signature(leaf) -> tuple:
        key = _leaf_key(leaf)
        torsions = tuple(
            sorted((axis_id, round(float(key["torsions"][axis_id]), 6)) for axis_id in ("t1", "t2"))
        )
        return (
            str(sorted(key["coordination"].items())),
            str(sorted(key["rings"]["ring1"].items())),
            str(torsions),
        )

    assert Counter(signature(leaf) for leaf in perturbed.leaves) == Counter(
        signature(leaf) for leaf in run.leaves
    )


class _TamperingRingStage(RingStage):
    """Adversarial wrapper over the REAL ring stage: corrupts its own output."""

    def realize(
        self,
        parent: WorkingRealization,
        target: GenerationTarget,
        context: MolecularContext,
    ) -> RealizationResult:
        result = super().realize(parent, target, context)
        if result.status != "realized" or result.structure is None:
            return result
        coords = np.asarray(result.structure.coordinates, dtype=float).copy()
        coords[5] += np.array([0.0, 0.0, 0.30])
        tampered = replace(result.structure, coordinates=tuple(map(tuple, coords)))
        return RealizationResult(
            structure=tampered,
            status="realized",
            reason=result.reason,
            backend=result.backend,
            evidence=result.evidence,
        )


def test_combination_adversarial_tamper_caught() -> None:
    from confflow.science.confgen.coordination.stage import CoordinationStage, adapt_to_core
    from confflow.science.confgen.torsion.stage import TorsionStage

    record, spec = _build_fixture()
    context = build_context(record, spec)
    resolved = thaw_snapshot(context.resolved_spec)
    engine = ConfgenEngine(
        stages=[
            adapt_to_core(CoordinationStage(dict(resolved["coordination"]))),
            _TamperingRingStage(resolved),
            TorsionStage(resolved),
        ]
    )
    run = engine.run(context)
    published = [r for r in run.target_records if r.status.value == "published_leaf"]
    assert len(published) < 36
    # R4 rewrite (Q3/CP boundary): the 0.30A tamper pushes the ring to the
    # CP dead-zone/small-margin boundary, so the tampered branch routes to
    # unresolved/ambiguous_perception (not failed_drift). Prove the tampered
    # branch is unpublished with a specific AMBIGUOUS_KEY audit, and the
    # healthy branch still publishes 18 leaves.
    assert len(published) == 18
    tampered = [
        r for r in run.target_records if r.target_id == "rings:000000" and r.axis == "rings"
    ]
    assert len(tampered) == 2 and all(r.status.value == "unresolved" for r in tampered)
    for rec in tampered:
        assert rec.reason == "ambiguous_perception"
        anomalies = [dict(e).get("anomaly") for e in rec.evidence]
        assert "AMBIGUOUS_KEY" in anomalies
    healthy = [r for r in run.target_records if r.target_id == "rings:000001" and r.axis == "rings"]
    assert all(r.status.value == "expanded" for r in healthy)
    ok, _ = verify_terminal_equations(run.target_records)
    assert ok is True
