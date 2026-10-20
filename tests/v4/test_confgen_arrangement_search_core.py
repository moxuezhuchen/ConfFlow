#!/usr/bin/env python3
"""``arrangement: search``: free-arrangement DG starts, arrangement groups and publication."""

from __future__ import annotations

import numpy as np
import pytest

from confflow.science.confgen import dg_seed, explore
from confflow.science.confgen.graph import (
    INPUT_SHAPE,
    AtomRef,
    BindingSite,
    CoordinationSpec,
    EdgeType,
    TypedEdge,
    TypedGraph,
)
from confflow.science.confgen.identity import HARTREE_TO_KCAL
from tests.v4.test_confgen_input_core_explore import _build, _FakeCalc, _settings

KCAL = 1.0 / HARTREE_TO_KCAL


def _pt_cl2br2() -> tuple[TypedGraph, CoordinationSpec]:
    atoms = (
        AtomRef(0, "Pt"),
        AtomRef(1, "Cl"),
        AtomRef(2, "Cl"),
        AtomRef(3, "Br"),
        AtomRef(4, "Br"),
    )
    edges = tuple(TypedEdge(a=0, b=i, type=EdgeType.COORDINATION) for i in range(1, 5))
    graph = TypedGraph(atoms, edges, 0, (), "pt_cl2br2")
    sites = tuple(BindingSite(id=f"D{i}", atoms=(i,)) for i in range(1, 5))
    return graph, CoordinationSpec(metal_center=0, binding_sites=sites, shapes=(INPUT_SHAPE,))


def _square(order: tuple[int, int, int, int], r: float = 2.3) -> np.ndarray:
    """Square-planar PtX4: donor ``order[k]`` sits on the k-th corner (corners 0/2 trans)."""
    corners = [(r, 0.0, 0.0), (0.0, r, 0.0), (-r, 0.0, 0.0), (0.0, -r, 0.0)]
    xyz = np.zeros((5, 3))
    for k, atom in enumerate(order):
        xyz[atom] = corners[k]
    return xyz


CIS = (1, 2, 3, 4)  # Cl, Cl adjacent: cis
TRANS = (1, 3, 2, 4)  # Cl on corners 0 and 2: trans


def _tetra(r: float = 2.3) -> np.ndarray:
    s = r / np.sqrt(3.0)
    return np.array([[0, 0, 0], [s, s, s], [s, -s, -s], [-s, s, -s], [-s, -s, s]], dtype=float)


def _explorer(arrangement: str = "search", **kw: object) -> explore._Explorer:
    graph, spec = _pt_cl2br2()
    st = _settings(arrangement=arrangement, **kw)
    return explore._Explorer(graph, spec, _square(CIS), INPUT_SHAPE, st, _FakeCalc(), map, None)


# ----------------------------------------------------------------------------- groups
def test_reference_is_a0_and_relabelled_cis_stays_in_it() -> None:
    ex = _explorer()
    assert ex._arrangement_of(_square(CIS)) == "a0"
    assert ex._arrangement_of(_square((2, 1, 4, 3))) == "a0"  # Cl/Br swapped, still cis
    assert ex._arrangement_of(_square(TRANS)) == "a1"
    assert ex._arrangement_of(_square(TRANS, r=2.5)) == "a1"
    assert ex._arrangement_of(_tetra()) == "a2"


def test_mirror_arrangement_joins_the_group_without_another_hand() -> None:
    graph, spec, xyz = _build("cd6_bpy3")
    ex = explore._Explorer(graph, spec, xyz, INPUT_SHAPE, _settings(arrangement="search"),
                           _FakeCalc(), map, None)  # fmt: skip
    assert not ex.tetrahedral_states and not ex.torsion_states
    assert ex._arrangement_of(xyz * np.array([-1.0, 1.0, 1.0])) == "a0"


def test_mirror_arrangement_is_its_own_group_when_another_hand_is_kept() -> None:
    graph, spec, xyz = _build("cd6_bpy3")
    heavy = [i for i, a in enumerate(graph.atoms) if a.element == "C"]
    quad = None
    for e in graph.edges:  # any C-C bond with C neighbours on both ends gives a torsion
        if e.type is not EdgeType.COVALENT or {e.a, e.b} - set(heavy):
            continue
        left = [
            f.a if f.b == e.a else f.b
            for f in graph.edges
            if e.a in (f.a, f.b) and f.type is EdgeType.COVALENT and e.b not in (f.a, f.b)
        ]
        right = [
            f.a if f.b == e.b else f.b
            for f in graph.edges
            if e.b in (f.a, f.b) and f.type is EdgeType.COVALENT and e.a not in (f.a, f.b)
        ]
        if left and right and left[0] != right[0]:
            quad = (left[0], e.a, e.b, right[0])
            break
    assert quad is not None
    st = _settings(arrangement="search", fixed_states=(quad,))
    ex = explore._Explorer(graph, spec, xyz, INPUT_SHAPE, st, _FakeCalc(), map, None)
    assert ex.arr_leaders[0][2]  # the Delta/Lambda hand now separates groups
    assert ex._arrangement_of(xyz * np.array([-1.0, 1.0, 1.0])) == "a1"


def test_folded_permutations_match_the_full_automorphisms() -> None:
    graph, spec, xyz = _build("cd6_bpy3")
    ex = explore._Explorer(graph, spec, xyz, INPUT_SHAPE, _settings(arrangement="search"),
                           _FakeCalc(), map, None)  # fmt: skip
    sites = [s.atoms[0] for s in spec.binding_sites]
    assert ex.id.automorphisms() is not None
    assert ex._folded_donor_perms(sites) == ex._donor_perms()
    assert len(ex._donor_perms()) > 1


# ----------------------------------------------------------------------------- audit and scope
def test_search_accepts_another_arrangement_and_keep_rejects_it() -> None:
    trans = _square(TRANS)
    assert _explorer("search")._audit(trans, True, True)[0] is not None
    assert _explorer("keep")._audit(trans, True, True) == (None, ("arrangement",))


def test_free_share_and_scope() -> None:
    ex = _explorer("search")
    assert ex.free_share == explore._FREE_DG_SHARE
    assert ex._stats()["arrangement_scope"] == "all_arrangements"
    keep = _explorer("keep")
    assert keep.free_share == 0.0
    assert keep._stats()["arrangement_scope"] == "input_arrangement"


def test_dg_schedule_alternates_free_starts(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[bool] = []

    def record(args):
        seen.append(bool(args[-1]))
        return args[4].id, [], None, ()

    monkeypatch.setattr(explore, "_dg_task", record)
    ex = _explorer("search", budget=40)
    ex._dg([(ex.targets[0], 6)], "dg0")
    ex._dg([(ex.targets[0], 3)], "dgm1")
    assert seen == [False, True] * 4 + [False]
    seen.clear()
    keep = _explorer("keep", budget=40)
    keep._dg([(keep.targets[0], 4)], "dg0")
    assert seen == [False] * 4


# ----------------------------------------------------------------------------- free DG starts
def test_free_start_drops_the_core_template_but_keeps_metal_donor_distances() -> None:
    graph, spec, xyz = _build("cd6_bpy3")
    donors = [s.atoms[0] for s in spec.binding_sites]
    common = dict(count=2, random_seed=7, timeout_seconds=60)
    fixed = dg_seed.generate_dg_seeds(graph, spec, xyz, list(range(6)), INPUT_SHAPE,
                                      dg_seed.DGSeedSettings(**common))  # fmt: skip
    free = dg_seed.generate_dg_seeds(graph, spec, xyz, list(range(6)), INPUT_SHAPE,
                                     dg_seed.DGSeedSettings(**common, free_arrangement=True))  # fmt: skip
    assert fixed.coords and free.coords
    for start in fixed.coords:  # the core coordinate map pins the metal
        assert np.allclose(start[0], xyz[0], atol=1e-3)
    for start in free.coords:
        assert not np.allclose(start[0], xyz[0], atol=1e-3)
        # bounds, not a coordinate map: ETKDG meets them only roughly (no force-field type for
        # the metal); the restrained relaxation then holds the metal-donor distances
        dev = [abs(np.linalg.norm(start[d] - start[0]) - np.linalg.norm(xyz[d] - xyz[0]))
               for d in donors]  # fmt: skip
        assert np.mean(dev) < 0.4 and max(dev) < 1.0


# ----------------------------------------------------------------------------- publication
def test_publication_uses_arrangement_and_group_windows() -> None:
    ex = _explorer("search", window_kcal=6.0)
    base = -1.0
    rows = [
        (base, _square(TRANS), "trans lowest"),  # a1, overall lowest
        (base + 4 * KCAL, _square(TRANS, 2.5), "trans in group window"),
        (base + 7 * KCAL, _square(TRANS, 2.7), "trans outside its window"),
        (base + 10 * KCAL, _square(CIS), "input arrangement, always published"),
        (base + 15 * KCAL, _square(CIS, 2.5), "within the a0 group window"),
        (base + 17 * KCAL, _square(CIS, 2.7), "outside the a0 group window"),
        (base + 8 * KCAL, _tetra(), "arrangement above the arrangement window"),
    ]
    for energy, xyz, origin in rows:
        mid = ex.pool.add(energy, xyz, "t00", origin, "t00#")
        assert mid is not None
        ex.arr_of.setdefault(id(ex.pool), {})[mid] = ex._arrangement_of(xyz)
    published = {m.origin for m in ex._publishable(ex.pool)}
    assert published == {
        "trans lowest",
        "trans in group window",
        "input arrangement, always published",
        "within the a0 group window",
    }
    run = ex._result(ex.pool, ex._stats())
    assert [r.arrangement for r in run.records] == ["a1", "a1", "a0", "a0"]
    table = {row["id"]: row for row in run.stats["arrangements"]}
    assert table["a0"]["input"] and not table["a1"]["input"]
    assert table["a1"]["rel_kcal"] == pytest.approx(0.0)
    assert table["a0"]["rel_kcal"] == pytest.approx(10.0)
    assert (table["a0"]["minima"], table["a0"]["published"]) == (3, 2)
    assert (table["a2"]["minima"], table["a2"]["published"]) == (1, 0)
    assert run.records[0].to_dict()["arrangement"] == "a1"


def test_metal_free_publication_is_the_plain_window() -> None:
    graph = TypedGraph((AtomRef(0, "C"), AtomRef(1, "O")),
                       (TypedEdge(a=0, b=1, type=EdgeType.COVALENT),), None, (), "co")  # fmt: skip
    ex = explore._Explorer(graph, None, np.array([[0.0, 0, 0], [1.13, 0, 0]]), None,
                           _settings(), _FakeCalc(), map, None)  # fmt: skip
    assert ex.free_share == 0.0
    ex.pool.add(-1.0, ex.ref, "t00", "a", "t00#")
    assert [m.origin for m in ex._publishable(ex.pool)] == ["a"]
    assert ex._arrangement(ex.pool, ex.pool.minima[0]) is None
