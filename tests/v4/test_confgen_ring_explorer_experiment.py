"""Budget, parent and random-stream contracts of the experimental search adapter."""

import random
from collections import Counter
from types import SimpleNamespace

import numpy as np

from confflow.science.confgen.explore import _Explorer
from tools.experiments.confgen_ring_explorer import RingInversionExplorer
from tools.experiments.confgen_ring_moves import pendant_rings


def _engine():
    graph = [{} for _ in range(8)]
    for a, b in [(i, (i + 1) % 6) for i in range(6)] + [(0, 6), (3, 7)]:
        graph[a][b] = graph[b][a] = "COVALENT"
    phi = np.arange(6) * np.pi / 3
    ring = np.column_stack((np.cos(phi), np.sin(phi), 0.2 * (-1.0) ** np.arange(6)))
    xyz = np.vstack((ring, ring[0] + [0, 0, 1], ring[3] + [0, 0, -1]))
    e = RingInversionExplorer.__new__(RingInversionExplorer)
    e.ring_parts = pendant_rings(graph, [range(6)], frozenset({6}))
    e.ring_cursor = 0
    e.ring_counts = Counter()
    e.ring_proposals = []
    e.pool = SimpleNamespace(minima=[SimpleNamespace(id=7, coords=xyz)])
    e.tetrahedral_states = e.double_bond_states = e.torsion_states = ()
    e.topo = SimpleNamespace(clashes=lambda x: False)
    e.generation = {"reembed": dict(cpu_s=0.0, map_wall_s=0.0)}
    e.round_index, e.nopt = 3, 80
    e.rng = random.Random(34)
    return e, xyz


def test_replacements_keep_quota_parent_and_production_random_stream(monkeypatch):
    e, xyz = _engine()
    items = [(f"reembed/m00007_{k:02d}", xyz.copy(), "reembed:7") for k in range(4)]
    called = []

    def original(self, quota):
        called.append(quota)
        return list(items)

    monkeypatch.setattr(_Explorer, "_reembed", original)
    state = e.rng.getstate()
    result = e._reembed(4)
    assert called == [4]
    assert len(result) == len(items)
    assert sum(":ring:" in origin for _, _, origin in result) == 1
    assert e.rng.getstate() == state
    assert {int(origin.split(":")[1]) for _, _, origin in result} == {7}
    assert e.nopt == 80
    assert len(e.ring_proposals) == 1
    assert e.ring_counts["attempts"] == 1  # no duplicate inversion of this parent/ring
    for _, coords, origin in result:
        if ":ring:" in origin:
            np.testing.assert_array_equal(coords[6], xyz[6])
    for _, coords, _ in items:
        np.testing.assert_array_equal(coords, xyz)  # proposals never mutate parent coordinates


def test_inapplicable_or_rejected_move_returns_original_proposals(monkeypatch):
    e, xyz = _engine()
    items = [(f"reembed/m00007_{k:02d}", xyz.copy(), "reembed:7") for k in range(4)]
    monkeypatch.setattr(_Explorer, "_reembed", lambda self, quota: items)
    e.topo = SimpleNamespace(clashes=lambda x: True)
    assert e._reembed(4) is items
    assert not e.ring_proposals
    assert e.ring_counts["precheck_rejected"] == 1
    e.ring_parts = ()
    state = e.rng.getstate()
    assert e._reembed(4) is items
    assert e.rng.getstate() == state


def test_single_slot_is_reserved_for_existing_reembedding(monkeypatch):
    e, xyz = _engine()
    items = [("reembed/m00007_00", xyz.copy(), "reembed:7")]
    monkeypatch.setattr(_Explorer, "_reembed", lambda self, quota: items)
    assert e._reembed(1) is items
    assert not e.ring_counts
