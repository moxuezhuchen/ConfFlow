#!/usr/bin/env python3
"""DG search pipeline as a pure module: targets, starts, restraints, audit, run."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import numpy as np
import pytest

from confflow.science.confgen import search
from confflow.science.confgen.coordination import enumeration, perception, shapes
from tests.v4._dg_search_helpers import _SHAPE, _embed, _toy, build_topology


def _topo(els, ref, forbid=(), shape=_SHAPE):
    sel = (0, [1, 2, 3, 4], [(1, 3)], list(forbid), 1.25)
    graph, spec, _ = build_topology(els, ref, sel, shape)
    return graph, spec


def _cmd(ref, spec):
    sites = [s.id for s in spec.binding_sites]
    seen = perception.perceive_donors(np.asarray(ref), 0, [1, 2, 3, 4], sites, _SHAPE)
    group = shapes.proper_rotation_group(_SHAPE)
    return enumeration.canonical_representative(tuple(seen.best_class), group)


def test_plan_targets() -> None:
    els, ref = _toy()
    graph, spec = _topo(els, ref)
    tgts = search.plan_targets(graph, spec, _SHAPE)
    assert [t.id for t in tgts] == ["t00", "t01", "t02"]
    assert all(len(t.placement) == 4 and t.command for t in tgts)
    full = enumeration.enumerate_targets(spec, _SHAPE)
    assert len(full["shape_classes"]) == 3
    _, limited_spec = _topo(els, ref, [(1, 2)])
    limited = search.plan_targets(graph, limited_spec, _SHAPE)
    limited_classes = enumeration.enumerate_targets(limited_spec, _SHAPE)["shape_classes"]
    want = sorted(tuple(c.representative) for c in limited_classes)
    assert sorted(tuple(t.placement) for t in limited) == want
    assert len(want) == 2
    assert all(all(len(pair) == 2 for pair in t.trans_pairs) for t in tgts)
    assert [(t.id, t.placement) for t in search.plan_targets(graph, None, None)] == [("t00", ())]


def test_plan_errors() -> None:
    els, ref = _toy()
    graph, spec = _topo(els, ref)
    with pytest.raises(search.SearchError):
        search.plan_targets(graph, spec, None)
    with pytest.raises(search.SearchError):
        search.plan_targets(graph, spec, "octahedral")
    with pytest.raises(search.SearchError):
        search.audit_structure(ref, graph, None, ref, _SHAPE)


def test_generate_split(monkeypatch: pytest.MonkeyPatch) -> None:
    els, ref = _toy()
    graph, spec = _topo(els, ref)
    tgts = search.plan_targets(graph, spec, _SHAPE)
    calls: list[tuple[int, int, bool]] = []

    def _spy(graph, spec, reference, placement, shape, settings):
        calls.append((settings.count, settings.random_seed, settings.small_ring_torsions))
        return SimpleNamespace(coords=(np.asarray(ref),) * settings.count, stereo_centers=(3,))

    monkeypatch.setattr(search, "generate_dg_seeds", _spy)
    found = search.generate_starts(graph, spec, ref, _SHAPE, tgts, search.SearchSettings(count=3))
    assert [(c, on) for c, _, on in calls] == [(2, True), (1, False)] * 3
    assert [s for _, s, _ in calls] == [1001, 1002, 1003, 1004, 1005, 1006]
    assert [s.small_ring_torsions for s in found.starts] == [True, True, False] * 3
    assert [s.index for s in found.starts] == [0, 1, 2] * 3
    assert [s.target for s in found.starts] == ["t00"] * 3 + ["t01"] * 3 + ["t02"] * 3
    assert found.stereo_centers == (3,) and found.failures == ()
    off_settings = search.SearchSettings(count=2, small_ring_torsions="off")
    off = search.generate_starts(graph, spec, ref, _SHAPE, tgts[:1], off_settings)
    assert [s.small_ring_torsions for s in off.starts] == [False, False]
    on_settings = search.SearchSettings(count=2, small_ring_torsions="on")
    on = search.generate_starts(graph, spec, ref, _SHAPE, tgts[:1], on_settings)
    assert [s.small_ring_torsions for s in on.starts] == [True, True]


def test_generate_error(monkeypatch: pytest.MonkeyPatch) -> None:
    from confflow.science.confgen.dg_seed import DGSeedError

    els, ref = _toy()
    graph, spec = _topo(els, ref)
    tgts = search.plan_targets(graph, spec, _SHAPE)
    calls: list[int] = []

    def _flaky(*args, **kwargs):
        calls.append(1)
        if len(calls) <= 2:
            raise DGSeedError("infeasible bounds for this class")
        return SimpleNamespace(coords=(np.asarray(ref),), stereo_centers=())

    monkeypatch.setattr(search, "generate_dg_seeds", _flaky)
    found = search.generate_starts(graph, spec, ref, _SHAPE, tgts, search.SearchSettings(count=2))
    assert found.failures == (("t00", "infeasible bounds for this class"),)
    assert [s.target for s in found.starts] == ["t01"] * 2 + ["t02"] * 2
    assert found.stereo_centers == ()


def test_restraints() -> None:
    els, ref = _toy()
    graph, spec = _topo(els, ref)
    got = search.distance_restraints(graph, spec, ref)
    assert [(r.i, r.j) for r in got] == [(0, 1), (0, 2), (0, 3), (0, 4), (1, 3)]
    for r in got:
        assert r.target == float(np.linalg.norm(ref[r.i] - ref[r.j]))
    free = search.distance_restraints(graph, None, ref)
    assert [(r.i, r.j) for r in free] == [(1, 3)]
    assert free[0].target == float(np.linalg.norm(ref[1] - ref[3]))


def test_audit() -> None:
    els, ref = _toy()
    graph, spec = _topo(els, ref)
    cmd = _cmd(ref, spec)
    clean = search.audit_structure(ref, graph, spec, ref, _SHAPE, cmd)
    assert (clean.failed_checks, clean.skipped_checks) == ((), ())
    unconverged = search.audit_structure(ref, graph, spec, ref, _SHAPE, cmd, converged=False)
    assert unconverged.failed_checks == ("converged",)
    broken = np.array(ref, copy=True)
    broken[5] += (3.0, 0.0, 0.0)
    assert "topology" in search.audit_structure(broken, graph, spec, ref, _SHAPE, cmd).failed_checks
    swapped = np.array(ref, copy=True)
    swapped[[1, 3]] = swapped[[3, 1]]
    wrong_class = search.audit_structure(swapped, graph, spec, ref, _SHAPE, cmd)
    assert "coordination_class" in wrong_class.failed_checks
    stretched = np.array(ref, copy=True)
    stretched[3, 0] += 0.10
    stretched_audit = search.audit_structure(stretched, graph, spec, ref, _SHAPE, cmd)
    assert "reaction_distance" in stretched_audit.failed_checks
    pulled = np.array(ref, copy=True)
    pulled[2, 0] += 0.10
    pulled_audit = search.audit_structure(pulled, graph, spec, ref, _SHAPE, cmd)
    assert pulled_audit.failed_checks == ("metal_donor_distance",)
    clashed = np.array(ref, copy=True)
    clashed[8] = clashed[5] + (0.30, 0.0, 0.0)
    clashed_audit = search.audit_structure(clashed, graph, spec, ref, _SHAPE, cmd)
    assert "contacts" in clashed_audit.failed_checks
    skipped = search.audit_structure(ref, graph, spec, ref, _SHAPE, cmd, (99,))
    assert skipped.failed_checks == ()


def test_donor_orientation() -> None:
    els, ref = _toy()
    graph, spec = _topo(els, ref)
    cmd = _cmd(ref, spec)
    ref_audit = search.audit_structure(ref, graph, spec, ref, _SHAPE, cmd)
    assert "donor_orientation" not in ref_audit.failed_checks
    unit = (ref[0] - ref[1]) / np.linalg.norm(ref[0] - ref[1])
    perp = np.cross(unit, (0.0, 0.0, 1.0))
    perp /= np.linalg.norm(perp)
    bond = float(np.linalg.norm(ref[5] - ref[1]))

    def _placed(deg: float) -> np.ndarray:
        moved = np.array(ref, copy=True)
        moved[5] = ref[1] + bond * (np.cos(np.radians(deg)) * unit + np.sin(np.radians(deg)) * perp)
        return moved

    tilted = search.audit_structure(_placed(170.0), graph, spec, ref, _SHAPE, cmd)
    assert tilted.failed_checks == ("donor_orientation",)
    flat = search.audit_structure(_placed(130.0), graph, spec, ref, _SHAPE, cmd)
    assert flat.failed_checks == ()


def test_stereo_metal_free() -> None:
    from confflow.science.confgen.dg_seed import DGSeedSettings, generate_dg_seeds
    from confflow.science.confgen.graph import AtomRef, EdgeType, TypedEdge, TypedGraph

    mol, els, ref = _embed("CC[C@H](O)Cl")
    bonds = sorted(tuple(sorted((b.GetBeginAtomIdx(), b.GetEndAtomIdx()))) for b in mol.GetBonds())
    atoms = tuple(AtomRef(index=i, element=e) for i, e in enumerate(els))
    edges = tuple(TypedEdge(a=a, b=b, type=EdgeType.COVALENT) for a, b in bonds)
    topo = TypedGraph(atoms=atoms, edges=edges)
    seeds = generate_dg_seeds(topo, None, ref, [], "bogus", DGSeedSettings(1, 7))
    assert seeds.stereo_centers == (2,)
    hydros = [n.GetIdx() for n in mol.GetAtomWithIdx(1).GetNeighbors() if n.GetSymbol() == "H"]
    swapped = np.array(ref, copy=True)
    swapped[hydros[::-1]] = swapped[hydros]
    got = search.audit_structure(swapped, topo, None, ref, None, (), seeds.stereo_centers)
    assert got.failed_checks == ()
    mirrored = np.array(ref, copy=True)
    mirrored[:, 0] *= -1.0
    flipped = search.audit_structure(mirrored, topo, None, ref, None, (), seeds.stereo_centers)
    assert flipped.failed_checks == ("stereo",)


def test_settings_errors() -> None:
    els, ref = _toy()
    graph, spec = _topo(els, ref)
    assert search.SearchSettings().count == 8
    cases = [
        search.SearchSettings(count=0),
        search.SearchSettings(count=True),
        search.SearchSettings(seed=1.5),
        search.SearchSettings(small_ring_torsions="x"),
        search.SearchSettings(embed_timeout=-1),
        search.SearchSettings(bond_scale=0.0),
    ]
    for bad in cases:
        with pytest.raises(search.SearchError):
            search.generate_starts(graph, spec, ref, _SHAPE, (), bad)


def test_radius_refuses_element_without_covalent_radius() -> None:
    from confflow.science.confgen.search import _radius

    assert _radius("C") > 0.0
    with pytest.raises(ValueError, match="no usable covalent radius"):
        _radius("Og")


def _cl4() -> tuple[Any, Any, np.ndarray]:
    """Pt with four Cl donors in a square: no hydrogens, no reaction pair."""
    els = ["Pt", "Cl", "Cl", "Cl", "Cl"]
    ref = np.array(
        [[0.0, 0.0, 0.0], [2.0, 0.0, 0.0], [-2.0, 0.0, 0.0], [0.0, 2.0, 0.0], [0.0, -2.0, 0.0]]
    )
    topo, spec, _ = build_topology(els, ref, (0, [1, 2, 3, 4], [], [], 1.25), _SHAPE)
    return topo, spec, ref


def test_ambiguous_perception_still_fails_coordination_class() -> None:
    topo, spec, ref = _cl4()
    planned = {t.command for t in search.plan_targets(topo, spec, _SHAPE)}
    lifted = np.array(ref, copy=True)
    lifted[1] = (0.0, 0.0, 2.0)
    audit = search.audit_structure(
        lifted, topo, spec, ref, _SHAPE, (), (), True, 1.25, allowed=planned
    )
    assert "coordination_class" in audit.failed_checks
    assert audit.actual_class is None


def test_audit_without_allowed_keeps_commanded_rule() -> None:
    els, ref = _toy()
    graph, spec = _topo(els, ref)
    cmd = _cmd(ref, spec)
    own = search.audit_structure(ref, graph, spec, ref, _SHAPE, cmd)
    assert own.failed_checks == () and own.actual_class == cmd
    other = next(t.command for t in search.plan_targets(graph, spec, _SHAPE) if t.command != cmd)
    off = search.audit_structure(ref, graph, spec, ref, _SHAPE, other)
    assert "coordination_class" in off.failed_checks and off.actual_class == cmd


def test_class_outside_planned_set_is_refused() -> None:
    els = ["Pt", "Cl", "Cl", "Cl", "Cl"]
    ref = np.array(
        [[0.0, 0.0, 0.0], [2.0, 0.0, 0.0], [-2.0, 0.0, 0.0], [0.0, 2.0, 0.0], [0.0, -2.0, 0.0]]
    )
    topo, spec, _ = build_topology(els, ref, (0, [1, 2, 3, 4], [], [(1, 2)], 1.25), _SHAPE)
    planned = {t.command for t in search.plan_targets(topo, spec, _SHAPE)}
    ref_cls = _cmd(ref, spec)
    assert ref_cls not in planned
    audit = search.audit_structure(
        ref, topo, spec, ref, _SHAPE, (), (), True, 1.25, allowed=planned
    )
    assert "coordination_class" in audit.failed_checks
    assert audit.actual_class == ref_cls


def _swap_13(ref: np.ndarray) -> np.ndarray:
    """Swap two donor positions: the trans partition changes, the class does not match."""
    moved = np.array(ref, copy=True)
    moved[[1, 3]] = ref[[3, 1]]
    return moved


def test_actual_class_in_planned_set_is_accepted() -> None:
    topo, spec, ref = _cl4()
    planned = {t.command for t in search.plan_targets(topo, spec, _SHAPE)}
    moved = _swap_13(ref)
    audit = search.audit_structure(
        moved, topo, spec, ref, _SHAPE, (), (), True, 1.25, allowed=planned
    )
    assert "coordination_class" not in audit.failed_checks
    assert audit.actual_class in planned and audit.actual_class != _cmd(ref, spec)
    commanded = search.audit_structure(moved, topo, spec, ref, _SHAPE, _cmd(ref, spec))
    assert "coordination_class" in commanded.failed_checks


def _place(xyz: np.ndarray, i: int, j: int, ratio: float, els, direction=None) -> np.ndarray:
    """Copy of ``xyz`` with atom ``j`` at ``ratio`` x the covalent radii sum from atom ``i``."""
    moved = np.array(xyz, copy=True)
    unit = np.asarray(xyz[j] - xyz[i] if direction is None else direction, dtype=float)
    unit = unit / np.linalg.norm(unit)
    reach = ratio * (search._radius(els[i]) + search._radius(els[j]))
    moved[j] = xyz[i] + reach * unit
    return moved


def test_reference_bond_stretch_is_tolerated_until_it_breaks() -> None:
    els, ref = _toy()
    graph, spec = _topo(els, ref)
    cmd = _cmd(ref, spec)
    assert (
        "topology" not in search.audit_structure(ref, graph, spec, ref, _SHAPE, cmd).failed_checks
    )
    # atom 5 is a hydrogen bonded to atom 1 (N) in the reference
    stretched = _place(ref, 1, 5, 1.40, els)
    got = search.audit_structure(stretched, graph, spec, ref, _SHAPE, cmd)
    assert "topology" not in got.failed_checks
    broken = _place(ref, 1, 5, 1.50, els)
    got = search.audit_structure(broken, graph, spec, ref, _SHAPE, cmd)
    assert "topology" in got.failed_checks


def test_new_bond_forms_only_below_bonding_distance() -> None:
    els, ref = _toy()
    els = [*els, "C"]
    ref = np.vstack([ref, [[10.0, 0.0, 0.0]]])  # free carbon, unbonded in the reference
    graph, spec = _topo(els, ref)
    cmd = _cmd(ref, spec)
    assert (
        "topology" not in search.audit_structure(ref, graph, spec, ref, _SHAPE, cmd).failed_checks
    )
    # the carbon sits next to hydrogen 5 (bonded to N1), pointing away from the complex
    away = np.array([0.5, -0.866, 0.0])
    close = _place(ref, 5, 11, 1.05, els, direction=away)
    got = search.audit_structure(close, graph, spec, ref, _SHAPE, cmd)
    assert "topology" in got.failed_checks
    apart = _place(ref, 5, 11, 1.15, els, direction=away)
    got = search.audit_structure(apart, graph, spec, ref, _SHAPE, cmd)
    assert "topology" not in got.failed_checks


def test_donor_pair_counts_as_bond_only_at_bonding_distance() -> None:
    els, ref = _toy()
    # donors 1 (N) and 3 (Cl) sit 1.20 x radii sum apart: too far to be a bond
    loose = _place(ref, 1, 3, 1.20, els)
    topo, spec, _ = build_topology(els, loose, (0, [1, 2, 3, 4], [], [], 1.25), _SHAPE)
    cmd = _cmd(loose, spec)
    assert (
        "topology"
        not in search.audit_structure(loose, topo, spec, loose, _SHAPE, cmd).failed_checks
    )
    apart = _place(loose, 1, 3, 1.50, els)
    got = search.audit_structure(apart, topo, spec, loose, _SHAPE, cmd)
    assert "topology" not in got.failed_checks


def test_windows_reproduce_the_full_request() -> None:
    els, ref = _toy()
    graph, spec = _topo(els, ref)
    tgts = search.plan_targets(graph, spec, _SHAPE)
    full = search.generate_starts(
        graph, spec, ref, _SHAPE, tgts[:1], search.SearchSettings(count=5)
    )
    parts = [
        search.generate_starts(
            graph, spec, ref, _SHAPE, tgts[:1], search.SearchSettings(count=5, window=w)
        )
        for w in ((0, 2), (2, 3), (3, 5))
    ]
    got = [s.coords for p in parts for s in p.starts]
    assert len(got) == len(full.starts) == 5
    for a, b in zip(got, (s.coords for s in full.starts)):
        np.testing.assert_allclose(a, b)
    with pytest.raises(search.SearchError, match="window"):
        search.generate_starts(
            graph, spec, ref, _SHAPE, tgts[:1], search.SearchSettings(count=5, window=(3, 9))
        )
