#!/usr/bin/env python3
"""Coordination configuration hand: re-embedding, arrangement chirality, search-scope stats.

Uses the input-core fixtures and the deterministic fake engine of
``test_confgen_input_core_explore``; no RDKit relaxation is involved beyond local embedding.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any

import numpy as np
import pytest

from confflow.science.confgen import explore
from confflow.science.confgen.graph import (
    INPUT_SHAPE,
    AtomRef,
    BindingSite,
    CoordinationSpec,
    EdgeType,
    TypedEdge,
    TypedGraph,
)
from tests.v4.test_confgen_input_core_explore import (
    _build,
    _cap_starts,
    _FakeCalc,
    _settings,
)


def _kabsch(p: np.ndarray, q: np.ndarray, proper: bool) -> float:
    """Centred RMSD of ``p`` onto ``q`` under the best proper (or improper) orthogonal fit."""
    p = p - p.mean(axis=0)
    q = q - q.mean(axis=0)
    u, s, vt = np.linalg.svd(p.T @ q)
    d = np.eye(3)
    d[2, 2] = np.sign(np.linalg.det(u @ vt)) if proper else -np.sign(np.linalg.det(u @ vt))
    rot = vt.T @ d @ u.T  # minimises |p @ rot - q|
    fitted = p @ rot
    return float(np.sqrt(np.mean(np.sum((fitted - q) ** 2, axis=1))))


def _same_hand(out: np.ndarray, ref: np.ndarray, core: list[int]) -> bool:
    return _kabsch(out[core], ref[core], True) < _kabsch(out[core], ref[core], False)


def _mirror(xyz: np.ndarray) -> np.ndarray:
    return xyz * np.array([-1.0, 1.0, 1.0])


# ----------------------------------------------------------------------------- A: re-embedding
@pytest.mark.parametrize("name", ["cd6_bpy3", "ti7_salan"])
def test_reembed_keeps_the_core_hand(name: str) -> None:
    graph, spec, xyz = _build(name)
    ex = explore._Explorer(graph, spec, xyz, INPUT_SHAPE, _settings(), _FakeCalc(), map, None)
    core = [ex.metal, *ex.donors]
    limit = explore._reembed_limit(ex.st)
    attempts = ok = 0
    for free in ex._free_sets()[:4]:
        for seed in range(1, 7):
            out, _err = explore._reembed_task(
                (ex.elements, ex.bonds, ex.metal, xyz, frozenset(free), ex.re_pairs, seed, limit)
            )
            attempts += 1
            if out is None:
                continue
            ok += 1
            assert _same_hand(out, xyz, core), f"{name}: re-embedding returned the mirror core"
    assert 2 * ok >= attempts, f"{name}: only {ok}/{attempts} re-embeddings succeeded"


# ----------------------------------------------------------------------------- B: arrangement hand
def test_whole_molecule_mirror_is_rejected_by_keep() -> None:
    graph, spec, xyz = _build("cd6_bpy3")
    ex = explore._Explorer(graph, spec, xyz, INPUT_SHAPE, _settings(), _FakeCalc(), map, None)
    assert ex.arr_chiral is True
    assert ex._same_arrangement(xyz)
    assert not ex._same_arrangement(_mirror(xyz))


# ----------------------------------------------------------------------------- achiral reference
def _square_planar_pt() -> tuple[TypedGraph, CoordinationSpec, np.ndarray]:
    """Pt with four Cl in the xy plane: the arrangement is its own mirror image."""
    atoms = (AtomRef(0, "Pt"),) + tuple(AtomRef(i, "Cl") for i in range(1, 5))
    edges = tuple(TypedEdge(a=0, b=i, type=EdgeType.COORDINATION) for i in range(1, 5))
    graph = TypedGraph(atoms, edges, 0, (), "pt_cl4")
    sites = tuple(BindingSite(id=f"D{i}", atoms=(i,)) for i in range(1, 5))
    spec = CoordinationSpec(metal_center=0, binding_sites=sites, shapes=(INPUT_SHAPE,))
    xyz = np.array([[0.0, 0.0, 0.0], [2.3, 0.0, 0.0], [0.0, 2.3, 0.0],
                    [-2.3, 0.0, 0.0], [0.0, -2.3, 0.0]])  # fmt: skip
    return graph, spec, xyz


def test_achiral_arrangement_skips_the_hand_test() -> None:
    graph, spec, xyz = _square_planar_pt()
    ex = explore._Explorer(graph, spec, xyz, INPUT_SHAPE, _settings(), _FakeCalc(), map, None)
    assert ex.arr_ref is not None
    assert ex.arr_chiral is False
    assert ex._same_arrangement(xyz)
    assert ex._same_arrangement(_mirror(xyz))


# ----------------------------------------------------------------------------- C: stats scope
def test_stats_report_template_and_arrangement_scope() -> None:
    graph, spec, xyz = _build("cd6_bpy3")
    keep = explore._Explorer(graph, spec, xyz, INPUT_SHAPE, _settings(), _FakeCalc(), map, None)
    stats = keep._stats()
    assert stats["template"] == "input"
    assert stats["arrangement_scope"] == "input_arrangement"

    octa = replace(spec, shapes=("octahedral",))
    unclassified = explore._Explorer(
        graph, octa, xyz, "octahedral", _settings(), _FakeCalc(), map, None
    )
    assert unclassified.arr_ref is None
    stats = unclassified._stats()
    assert stats["template"] == "octahedral"
    assert stats["arrangement_scope"] == "all_classes_input_unclassified"

    enum = explore._Explorer(
        graph, octa, xyz, "octahedral", _settings(arrangement="enumerate"), _FakeCalc(), map, None
    )
    assert enum._stats()["arrangement_scope"] == "all_classes"


# ----------------------------------------------------------------------------- D: release
def test_release_publishes_only_arrangement_preserving_structures(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: dict[str, Any] = {}
    real = explore._Explorer._release

    def _spy(self: Any) -> Any:
        seen["ex"] = self
        return real(self)

    monkeypatch.setattr(explore._Explorer, "_release", _spy)
    _cap_starts(monkeypatch)
    graph, spec, ref = _build("ti7_salan")
    run = explore.explore(
        graph, spec, ref, INPUT_SHAPE, _settings(budget=12, release=True), _FakeCalc(), map
    )
    assert run.records, "the input arrangement should publish at least one minimum"
    ex = seen["ex"]
    for rec in run.records:
        assert ex._same_arrangement(rec.coords)
