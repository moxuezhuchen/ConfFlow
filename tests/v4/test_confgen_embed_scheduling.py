"""Embedding time limits and DG start scheduling in the explorer.

``reembed_timeout`` bounds local re-embedding only; the global DG starts keep ``embed_timeout``.
Every DG start is its own parallel task, sliced as a window ``(i, i + 1)`` with one seed per
target, and item names carry the true start index.
"""

from __future__ import annotations

import numpy as np
import pytest

from confflow.science.confgen import explore, search
from tests.v4._dg_search_helpers import _embed, build_topology
from tests.v4.test_confgen_explore import _FakeCalc, _settings


def _graph_ref(smiles: str):
    _, els, ref = _embed(smiles)
    graph, _, _ = build_topology(els, ref, (None, [], [], [], 1.25), None)
    return graph, np.asarray(ref, dtype=float)


def _explorer(smiles: str, **kw) -> explore._Explorer:
    graph, ref = _graph_ref(smiles)
    return explore._Explorer(graph, None, ref, None, _settings(**kw), _FakeCalc(), map, None)


class _Capture:
    """Parallel map stand-in: records the tasks and answers them with ``reply(task)``."""

    def __init__(self, reply) -> None:
        self.reply, self.tasks = reply, []

    def __call__(self, fn, tasks):
        self.tasks = list(tasks)
        return [self.reply(t) for t in self.tasks]


@pytest.mark.parametrize(
    ("reembed", "embed", "want"),
    [(10, 120, 10), (10, 5, 5), (10, 0, 10), (0, 0, 0), (0, 30, 30)],
)
def test_reembed_limit_is_smallest_positive_timeout(reembed: int, embed: int, want: int) -> None:
    st = _settings(reembed_timeout=reembed, embed_timeout=embed)
    assert explore._reembed_limit(st) == want


def test_negative_reembed_timeout_is_refused() -> None:
    graph, ref = _graph_ref("CCCC")
    with pytest.raises(search.SearchError):
        explore.explore(graph, None, ref, None, _settings(reembed_timeout=-1), _FakeCalc(), map)


@pytest.mark.parametrize(("embed", "want"), [(120, 10), (4, 4)])
def test_reembed_tasks_carry_the_timeout(embed: int, want: int) -> None:
    ex = _explorer("CCCCCO", reembed_timeout=10, embed_timeout=embed)
    ex.pool.add_many([(-1.0, ex.ref, "seed", "dg", "")], None)
    ex.pmap = _Capture(lambda t: (None, "embed"))
    ex._reembed(12)
    tasks = ex.pmap.tasks
    assert tasks, "a flexible molecule must yield re-embedding tasks"
    assert all(t[-1] == explore._reembed_limit(ex.st) == want for t in tasks)


def test_dg_tasks_are_single_starts_with_true_names() -> None:
    ex = _explorer("CCCCCO")
    target = ex.targets[0]
    ex.pmap = _Capture(lambda t: (t[4].id, [] if t[8][0] == 1 else [np.zeros((3, 3))], None, ()))
    items = ex._dg([(target, 4)], "dg0")
    tasks = ex.pmap.tasks
    assert [t[8] for t in tasks] == [(0, 1), (1, 2), (2, 3), (3, 4)]
    assert len({t[6] for t in tasks}) == 1, "all starts of one target share a seed"
    assert [name for name, _, _ in items] == [
        f"dg0/{target.id}_00", f"dg0/{target.id}_02", f"dg0/{target.id}_03"]  # fmt: skip
    assert ex.fail["dg_no_start"] == 1


def test_dg_windows_match_whole_window_per_start() -> None:
    ex = _explorer("CCCCCO")
    target = ex.targets[0]
    seed = 424242

    def args(window):
        return (ex.graph, ex.spec, ex.ref, ex.shape, target, 3, seed, ex.st, window)

    whole = explore._dg_task(args((0, 3)))[1]
    split = [explore._dg_task(args((i, i + 1)))[1] for i in range(3)]
    assert len(whole) == 3
    for i in range(3):
        assert len(split[i]) == 1
        assert np.array_equal(whole[i], split[i][0])
