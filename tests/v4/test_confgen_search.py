#!/usr/bin/env python3
"""DG search pipeline as a pure module: targets, starts, restraints, audit, run."""

from __future__ import annotations

from types import SimpleNamespace

import numpy as np
import pytest

from confflow.science.confgen import search
from confflow.science.confgen.coordination import enumeration, perception, shapes
from tests.v4._dg_search_helpers import _SHAPE, _embed, _toy, build_topology


def _topo(els, ref, forbid=(), shape=_SHAPE):
    sel = (0, [1, 2, 3, 4], [(1, 3)], list(forbid), 1.25)
    graph, spec, _ = build_topology(els, ref, sel, shape)
    return graph, spec


_WANT_SKIPPED = ("coordination_class", "metal_donor_distance", "donor_orientation")


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
    assert [(i, j) for i, j, _ in got] == [(0, 1), (0, 2), (0, 3), (0, 4), (1, 3)]
    for i, j, d in got:
        assert d == float(np.linalg.norm(ref[i] - ref[j]))
    free = search.distance_restraints(graph, None, ref)
    assert [(i, j) for i, j, _ in free] == [(1, 3)]
    assert free[0][2] == float(np.linalg.norm(ref[1] - ref[3]))


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


def test_run_search_metal(monkeypatch: pytest.MonkeyPatch) -> None:
    els, ref = _toy()
    graph, spec = _topo(els, ref)

    def _echo(*args, **kwargs):
        return SimpleNamespace(coords=(np.asarray(ref),), stereo_centers=())

    monkeypatch.setattr(search, "generate_dg_seeds", _echo)
    settings = search.SearchSettings(count=1, seed=7, fragment_charges=((1, -1),))

    def _relax(starts, restraints):
        assert [(i, j) for i, j, _ in restraints] == [(0, 1), (0, 2), (0, 3), (0, 4), (1, 3)]
        outs = []
        for s in starts:
            outs.append(search.RelaxOutcome(np.asarray(s.coords), -100.0, True, 0.2))
        return outs

    run = search.run_search(graph, spec, ref, _SHAPE, settings, _relax)
    assert run.totals == {"targets": 3, "generated": 3, "passed": 1, "relaxed": 3}
    assert sorted(t.passed for t in run.targets) == [0, 0, 1]
    assert all((t.generated, t.relaxed) == (1, 1) for t in run.targets)
    assert all("coordination_class" in r.failed_checks for r in run.records if not r.passed)
    assert all(r.small_ring_torsions for r in run.records)
    assert run.summary()["fragment_charges"] == [{"atom": 2, "charge": -1}]


def test_run_search_free(monkeypatch: pytest.MonkeyPatch) -> None:
    mol, els, ref = _embed("C[C@H]1CC[C@@H](O)C1")
    topo, spec, _ = build_topology(els, ref, (None, [], [(0, 1)], [], 1.25), None)
    assert spec is None

    def _echo(*args, **kwargs):
        return SimpleNamespace(coords=(np.asarray(ref),) * args[-1].count, stereo_centers=())

    monkeypatch.setattr(search, "generate_dg_seeds", _echo)
    broken = np.array(ref, copy=True)
    broken[0] += (3.0, 0.0, 0.0)
    energies = [-100.0, -99.0, -99.0, None, -96.0, None]
    walls = [0.2, 0.2, 0.25, 0.5, 0.2, 0.1]

    def _relax(starts, restraints):
        assert [(i, j) for i, j, _ in restraints] == [(0, 1)]
        outs = []
        for pos, start in enumerate(starts):
            coords = None if pos == 3 else (broken if pos == 2 else np.asarray(start.coords))
            outs.append(search.RelaxOutcome(coords, energies[pos], pos != 3, walls[pos]))
        return outs

    run = search.run_search(topo, None, ref, None, search.SearchSettings(count=6), _relax)
    assert run.totals == {"targets": 1, "generated": 6, "passed": 4, "relaxed": 5}
    assert [r.start for r in run.passing] == [0, 1, 4, 5]
    assert [t.passed for t in run.targets] == [4]
    assert all(r.skipped_checks == _WANT_SKIPPED for r in run.records)
    assert "topology" in run.records[2].failed_checks
    assert run.records[3].failed_checks == ("converged",)
    assert run.records[5].passed and run.records[5].energy_eh is None
    assert run.records[2].coords is None and run.passing[0].coords is not None
    assert run.summary()["fragment_charges"] == []


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


def test_cancel_and_mismatch(monkeypatch: pytest.MonkeyPatch) -> None:
    els, ref = _toy()
    graph, spec = _topo(els, ref)
    settings = search.SearchSettings(count=1)
    calls: list[int] = []

    def _echo(*args, **kwargs):
        calls.append(1)
        return SimpleNamespace(coords=(np.asarray(ref),), stereo_centers=())

    monkeypatch.setattr(search, "generate_dg_seeds", _echo)
    assert issubclass(search.SearchCancelled, search.SearchError)
    assert issubclass(search.SearchError, ValueError)
    with pytest.raises(search.SearchCancelled):
        search.run_search(graph, spec, ref, _SHAPE, settings, lambda s, r: [], lambda: True)
    assert calls == []
    with pytest.raises(search.SearchError, match="relax returned"):
        search.run_search(graph, spec, ref, _SHAPE, settings, lambda s, r: [], None)
    assert search.SearchRun().summary() == (
        {"targets": [], "structures": [], "fragment_charges": [], "totals": {}}
    )
