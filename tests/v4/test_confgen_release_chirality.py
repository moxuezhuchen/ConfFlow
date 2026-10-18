#!/usr/bin/env python3
"""Released keep minima retain both donor arrangement and its handedness."""

from __future__ import annotations

from dataclasses import replace

import numpy as np

from confflow.science.confgen import explore
from confflow.science.confgen.graph import INPUT_SHAPE
from tests.v4.test_confgen_input_core_explore import (
    _build,
    _FakeCalc,
    _settings,
)


def _best_hand_rmsds(ex: explore._Explorer, xyz: np.ndarray) -> tuple[float, float]:
    """Return the best proper-rotation and mirror fits across graph-equivalent donor labels."""
    vectors = ex._donor_vectors(xyz)
    proper = min(explore._fit_rmsd(vectors[pm], ex.arr_vec, proper=True) for pm in ex.arr_perms)
    mirror = min(explore._fit_rmsd(vectors[pm], ex.arr_vec, proper=False) for pm in ex.arr_perms)
    return proper, mirror


class _MirrorCalc(_FakeCalc):
    """Fake a release optimizer that incorrectly reflects every relaxed structure."""

    def relax_many(self, requests):
        outcomes = super().relax_many(requests)
        mirror = np.array([-1.0, 1.0, 1.0])
        return [replace(outcome, coords=outcome.coords * mirror) for outcome in outcomes]


def _release_input(calc: _FakeCalc) -> tuple[explore._Explorer, object]:
    graph, spec, reference = _build("cd6_bpy3")
    ex = explore._Explorer(
        graph,
        spec,
        reference,
        INPUT_SHAPE,
        _settings(release=True),
        calc,
        map,
        None,
    )
    ex.pool.add(-1.0, reference, "t00", "test-input", "input")
    return ex, ex._release()


def test_release_keeps_the_input_delta_lambda_hand() -> None:
    ex, released = _release_input(_FakeCalc())

    assert ex.release_status == "done"
    assert ex.released == 1
    assert len(released) == 1
    assert ex.arr_chiral
    for minimum in released.minima:
        assert ex._same_arrangement(minimum.coords)
        proper, mirror = _best_hand_rmsds(ex, minimum.coords)
        assert proper < mirror, "released structure changed the input donor arrangement's hand"


def test_release_rejects_a_mirror_and_falls_back_to_the_input_hand() -> None:
    ex, published = _release_input(_MirrorCalc())

    assert ex.release_status == "failed"
    assert ex.fail["arrangement"] == 1
    assert len(published) == 1
    assert ex.arr_chiral
    for minimum in published.minima:
        assert ex._same_arrangement(minimum.coords)
        proper, mirror = _best_hand_rmsds(ex, minimum.coords)
        assert proper < mirror, "release fallback published the mirror donor arrangement"
