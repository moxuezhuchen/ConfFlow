#!/usr/bin/env python3
"""Explorer with ``shape="input"``: one identity target, the input arrangement kept (input-core WP3).

The engine is the deterministic fake of ``test_confgen_explore``: relaxations return their input
geometry, so the search is driven by real DG starts and the input-core fixtures only.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from confflow.science.bonds import perceive_adjacency
from confflow.science.confgen import explore, search
from confflow.science.confgen.calculator import CalculatorInfo, RelaxOutcome
from confflow.science.confgen.graph import (
    INPUT_SHAPE,
    AtomRef,
    BindingSite,
    CoordinationSpec,
    EdgeType,
    TypedEdge,
    TypedGraph,
)
from confflow.science.data import get_atomic_number

INPUT_CORE = (
    Path(__file__).resolve().parents[1] / "fixtures" / "confgen" / "coordination" / "input_core"
)


class _FakeCalc:
    """Deterministic engine: relax returns the input geometry with a quadratic energy."""

    def __init__(self) -> None:
        self.info = CalculatorInfo("fake", "fake", "0", frozenset({"restrained_relax"}))

    def relax_many(self, requests):
        return [
            RelaxOutcome(
                coords=np.asarray(q.coords, dtype=float),
                energy_eh=float(np.sum(np.asarray(q.coords, dtype=float) ** 2)) * 1e-4,
                converged=True,
            )
            for q in requests
        ]


def _build(name: str) -> tuple[TypedGraph, CoordinationSpec, np.ndarray]:
    """Return (typed graph, spec, input geometry) of one input-core fixture (metal is atom 0)."""
    meta = json.loads((INPUT_CORE / "index.json").read_text())[name]
    lines = (INPUT_CORE / f"{name}.xyz").read_text().splitlines()
    rows = [line.split() for line in lines[2 : 2 + int(lines[0])]]
    elements = [row[0] for row in rows]
    xyz = np.array([[float(v) for v in row[1:4]] for row in rows])
    metal, donors = int(meta["metal_center0"]), [int(d) for d in meta["donors0"]]
    numbers = [get_atomic_number(e) for e in elements]
    adjacency = perceive_adjacency(numbers, xyz, bond_scale=1.15)
    edges = [
        TypedEdge(a=i, b=j, type=EdgeType.COVALENT)
        for i, row in enumerate(adjacency)
        for j in row
        if i < j and metal not in (i, j)
    ]
    edges += [TypedEdge(a=metal, b=d, type=EdgeType.COORDINATION) for d in donors]
    atoms = tuple(AtomRef(index=i, element=e) for i, e in enumerate(elements))
    graph = TypedGraph(atoms, tuple(edges), metal, (), name)
    sites = tuple(BindingSite(id=f"D{d}", atoms=(d,)) for d in donors)
    spec = CoordinationSpec(metal_center=metal, binding_sites=sites, shapes=(INPUT_SHAPE,))
    return graph, spec, xyz


def _settings(**kw: Any) -> explore.ExploreSettings:
    base: dict[str, Any] = {
        "budget": 16,
        "window_kcal": 6.0,
        "seed": 3,
        "starts": 3,
        "batch": 16,
        "release": False,
        "embed_timeout": 60,
    }
    base.update(kw)
    return explore.ExploreSettings(**base)


def _cap_starts(monkeypatch: pytest.MonkeyPatch, keep: int = 4) -> None:
    """Embed only the first ``keep`` starts of each DG round (the full rounds take minutes)."""
    real = search.generate_starts

    def _capped(*args: Any, **kwargs: Any) -> search.SearchStarts:
        window = args[-1].window
        if window is not None and window[0] >= keep:
            return search.SearchStarts()
        return real(*args, **kwargs)

    monkeypatch.setattr(search, "generate_starts", _capped)


def _angles(xyz: np.ndarray, metal: int, donors: list[int]) -> np.ndarray:
    v = np.array([xyz[d] - xyz[metal] for d in donors])
    v /= np.linalg.norm(v, axis=1)[:, None]
    return np.degrees(np.arccos(np.clip(v @ v.T, -1.0, 1.0)))


@pytest.mark.parametrize("name", ["ti7_salan", "zr8_onn"])
def test_input_search_runs_on_the_input_arrangement(
    name: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: dict[str, Any] = {}
    real = explore._Explorer._release

    def _spy(self: Any) -> Any:
        seen["ex"] = self
        return real(self)

    monkeypatch.setattr(explore._Explorer, "_release", _spy)
    _cap_starts(monkeypatch)
    graph, spec, ref = _build(name)
    run = explore.explore(graph, spec, ref, INPUT_SHAPE, _settings(), _FakeCalc(), map)
    assert run.stats["stop"] in {"budget", "no_moves"}
    assert run.stats["targets_searched"] == ["t00"]
    assert run.stats["input_shape_fit"] is None
    assert run.stats["relaxations"] > 0
    assert run.records, "the input arrangement should publish at least one minimum"
    ex = seen["ex"]
    metal = graph.metal_center
    donors = list(spec.donor_indices)
    assert metal is not None and ex.arr_ref is not None
    for rec in run.records:
        assert rec.target == "t00"
        dev = min(
            float(np.abs(_angles(rec.coords, metal, donors)[np.ix_(pm, pm)]
                         - _angles(ref, metal, donors)).max())
            for pm in ex.arr_perms
        )  # fmt: skip
        assert dev <= explore._ARR_TOL


def test_input_shape_refuses_enumerate() -> None:
    graph, spec, ref = _build("ti7_salan")
    with pytest.raises(search.SearchError, match="arrangement 'keep' only"):
        explore.explore(
            graph, spec, ref, INPUT_SHAPE, _settings(arrangement="enumerate"), _FakeCalc(), map
        )


def test_input_audit_rejects_new_coordination_and_accepts_input() -> None:
    graph, spec, xyz = _build("ti7_salan")
    metal = graph.metal_center
    assert metal is not None
    donors = set(spec.donor_indices)
    carbon = None
    for d in spec.donor_indices:  # a carbon two covalent bonds from a donor, far from the metal
        for mid in graph.neighbors(d, EdgeType.COVALENT):
            for c in graph.neighbors(mid, EdgeType.COVALENT):
                if (
                    graph.atoms[c].element == "C"
                    and c not in donors
                    and c != metal
                    and np.linalg.norm(xyz[c] - xyz[metal]) > 2.5
                ):
                    carbon = c
                    break
            if carbon is not None:
                break
        if carbon is not None:
            break
    assert carbon is not None
    direction = (xyz[carbon] - xyz[metal]) / np.linalg.norm(xyz[carbon] - xyz[metal])
    moved = xyz.copy()
    moved[carbon] = xyz[metal] + 2.1 * direction
    ex = explore._Explorer(graph, spec, xyz, INPUT_SHAPE, _settings(), _FakeCalc(), map, None)
    label, reasons = ex._audit(moved, True, False)
    assert label is None
    assert "coordination_class" in reasons
    assert ex._audit(xyz, True, False) == ("t00", ())


@pytest.mark.parametrize("name", ["ti7_salan", "zr8_onn"])
def test_budget_one_keeps_actual_input_core_without_embedding(name, monkeypatch) -> None:
    graph, spec, ref = _build(name)
    snapshots = []
    requests = []

    class Recording(_FakeCalc):
        def relax_many(self, batch):
            requests.extend(batch)
            return super().relax_many(batch)

    def no_dg(args):
        pytest.fail("input-only budget must not invoke embedding")

    monkeypatch.setattr(explore, "_dg_task", no_dg)
    run = explore.explore(
        graph, spec, ref, INPUT_SHAPE, _settings(budget=1), Recording(), checkpoint=snapshots.append
    )
    assert [request.key for request in requests] == ["input/reference"]
    np.testing.assert_array_equal(requests[0].coords, ref)
    assert len(run.records) == 1 and run.records[0].target == "t00"
    np.testing.assert_array_equal(run.records[0].coords, ref)
    assert run.stats["input_seed"]["status"] == "accepted"
    assert snapshots[0].stats["input_seed"]["published"] is True
