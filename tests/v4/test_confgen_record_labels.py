"""Per-structure report fields of published ConfGen records and the donor-stretch limit.

Each published record carries its reaction-pair distances, the largest metal-donor distance over
the input distance (and that donor), and the reminder-scan units it flips. ``max_donor_stretch``
turns the stretch report into a rejection.
"""

from __future__ import annotations

from collections import Counter

import numpy as np
import pytest

from confflow.science.confgen import explore
from tests.v4.test_confgen_explore import _butane, _FakeCalc, _pt_toy, _settings


def test_metal_records_report_reaction_distances_and_donor_stretch() -> None:
    graph, spec, ref = _pt_toy()
    run = explore.explore(graph, spec, ref, "square_planar", _settings(), _FakeCalc(), map)
    assert run.records
    donors = set(spec.donor_indices)
    metal = graph.metal_center
    for rec in run.records:
        assert len(rec.reaction_distances) == len(graph.reaction_pairs) == 1
        a, b = graph.reaction_pairs[0]
        assert rec.reaction_distances[0] == pytest.approx(
            float(np.linalg.norm(rec.coords[a] - rec.coords[b]))
        )
        assert rec.donor_stretch_atom in donors
        d = rec.donor_stretch_atom
        ratio = np.linalg.norm(rec.coords[d] - rec.coords[metal]) / np.linalg.norm(
            ref[d] - ref[metal]
        )
        assert rec.donor_stretch == pytest.approx(float(ratio), abs=1e-4)
        assert (
            rec.donor_stretch
            >= max(
                np.linalg.norm(rec.coords[k] - rec.coords[metal])
                / np.linalg.norm(ref[k] - ref[metal])
                for k in donors
            )
            - 1e-4
        )
        row = rec.to_dict()
        assert row["reaction_distances"] == [pytest.approx(rec.reaction_distances[0])]
        assert row["donor_stretch"] == rec.donor_stretch
        assert row["donor_stretch_atom"] == rec.donor_stretch_atom


def test_metal_free_records_have_no_donor_stretch_and_flips_match_the_scan() -> None:
    graph, ref = _butane()
    run = explore.explore(graph, None, ref, None, _settings(budget=60), _FakeCalc(), map)
    units = run.stats["state_changes"]["units"]
    assert all("members" not in unit for unit in units)  # internal, not in the summary
    seen: Counter[int] = Counter()
    for rec in run.records:
        assert rec.reaction_distances == () and rec.donor_stretch is None
        assert rec.donor_stretch_atom is None
        assert rec.state_flips is not None
        assert all(1 <= u <= len(units) for u in rec.state_flips)
        seen.update(rec.state_flips)
        assert rec.to_dict()["state_flips"] == list(rec.state_flips)
    for number, unit in enumerate(units, 1):  # published records are a subset of the scanned pool
        assert seen[number] <= unit["structures"]


def test_flips_map_scan_members_to_minimum_ids(monkeypatch: pytest.MonkeyPatch) -> None:
    """Unit members (indices into the ordered final pool) become per-minimum unit numbers."""
    graph, ref = _butane()
    sizes: list[int] = []

    def _fake(identity: object, reference: object, structures: list, energies: list, **kw: object):
        n = len(structures)
        sizes.append(n)
        units = [{"structures": 1, "members": [0]}, {"structures": 2, "members": [0, n - 1]}]
        return {"aligned": True, "scanned": n, "units": units}

    monkeypatch.setattr(explore.state_scan, "scan_states", _fake)
    run = explore.explore(graph, None, ref, None, _settings(budget=60), _FakeCalc(), map)
    assert sizes and sizes[0] >= 2
    assert all("members" not in unit for unit in run.stats["state_changes"]["units"])
    flips = [rec.state_flips for rec in run.records]
    assert flips[0] == (1, 2)  # records are lowest first, like the ordered pool
    assert all(f in ((), (2,)) for f in flips[1:])


def test_scan_failure_leaves_flips_unscanned(monkeypatch: pytest.MonkeyPatch) -> None:
    graph, ref = _butane()

    def _boom(*args: object, **kwargs: object) -> dict[str, object]:
        raise RuntimeError("scan fault")

    monkeypatch.setattr(explore.state_scan, "scan_states", _boom)
    run = explore.explore(graph, None, ref, None, _settings(budget=40), _FakeCalc(), map)
    assert run.stats["state_changes"]["units"] == []
    assert all(rec.state_flips is None for rec in run.records)


def _explorer(**kw: object) -> tuple[explore._Explorer, np.ndarray]:
    graph, spec, ref = _pt_toy()
    ex = explore._Explorer(
        graph, spec, ref, "square_planar", _settings(**kw), _FakeCalc(), map, None, None
    )
    return ex, ref


def _stretched(ref: np.ndarray, factor: float) -> np.ndarray:
    """Push donor 2 (not in the reaction pair) away from the metal (atom 0)."""
    xyz = ref.copy()
    xyz[2] = xyz[0] + factor * (xyz[2] - xyz[0])
    return xyz


def test_donor_stretch_reports_the_worst_donor() -> None:
    ex, ref = _explorer()
    assert ex._donor_stretch(ref) == (pytest.approx(1.0), ex.donors[0])
    ratio, atom = ex._donor_stretch(_stretched(ref, 1.3))
    assert atom == 2 and ratio == pytest.approx(1.3)


@pytest.mark.parametrize("free", [False, True])
def test_max_donor_stretch_rejects_in_search_and_release(free: bool) -> None:
    ex, ref = _explorer(max_donor_stretch=1.1)
    assert "donor_stretch" in ex._audit(_stretched(ref, 1.3), True, free)[1]
    assert "donor_stretch" not in ex._audit(_stretched(ref, 1.05), True, free)[1]
    assert ex._audit(ref, True, free)[0] is not None


def test_without_a_limit_stretch_is_only_reported() -> None:
    ex, ref = _explorer()
    assert "donor_stretch" not in ex._audit(_stretched(ref, 1.3), True, True)[1]
