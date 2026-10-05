#!/usr/bin/env python3
"""R2 rigid planar units + local orientation locks (topology-first).

Real F1 fixtures for rpdd; synthetic complete topologies (with H) for
cyclohexene / delta-valerolactone / morpholine / amide / imine /
aromatic / boundaries.  No existing fixtures are modified.
"""

from __future__ import annotations

import copy
import math
from pathlib import Path

import numpy as np
import pytest

from confflow.science.confgen.ring.rigid_units import (
    ABS_V_MIN,
    ANGLE_SUM_MIN,
    analyze_rigid_units,
)

FIX = Path(__file__).resolve().parents[1] / "fixtures" / "confgen" / "ring" / "rpdd"
FRAG = FIX / "input_ts_fragment.xyz"
CREST = FIX / "crest_conformers.xyz"

RING_RPDD = [0, 1, 3, 4, 5, 7]


def _load_xyz(path: Path, frame: int = 0) -> tuple[list[str], np.ndarray]:
    lines = path.read_text().splitlines()
    n = int(lines[0].strip())
    base = frame * (n + 2)
    els: list[str] = []
    xyz: list[list[float]] = []
    for line in lines[base + 2 : base + 2 + n]:
        parts = line.split()
        els.append(parts[0])
        xyz.append([float(x) for x in parts[1:4]])
    return els, np.array(xyz, dtype=float)


def _rpdd_adjacency() -> dict[int, set[int]]:
    adj: dict[int, set[int]] = {i: set() for i in range(22)}

    def link(a: int, b: int) -> None:
        adj[a].add(b)
        adj[b].add(a)

    for a, b in [
        (0, 1),
        (0, 7),
        (0, 9),
        (0, 11),
        (1, 2),
        (1, 3),
        (3, 4),
        (4, 5),
        (4, 8),
        (4, 10),
        (5, 6),
        (5, 7),
        (11, 12),
        (11, 13),
        (12, 14),
        (13, 16),
        (14, 18),
        (16, 18),
        (12, 15),
        (13, 17),
        (14, 19),
        (16, 20),
        (18, 21),
    ]:
        link(a, b)
    return adj


def _locks_by_center(res: object, center: int) -> list:
    return [lk for lk in res.local_orientation_locks if lk.center == center]  # type: ignore[attr-defined]


def test_rpdd_fragment_c2_sp2_distorted_no_lock() -> None:
    els, xyz = _load_xyz(FRAG)
    res = analyze_rigid_units(xyz, els, _rpdd_adjacency(), RING_RPDD)
    assert 1 in res.sp2_centers
    assert 5 in res.sp2_centers
    assert 0 not in res.sp2_centers
    # C2 has no orientation lock (topology sp2 never locks).
    assert _locks_by_center(res, 1) == []
    # C2 distorted ~339 deg, expectation string frozen.
    d1 = [d for d in res.distorted_input if d.atom == 1]
    assert len(d1) == 1
    assert d1[0].expected == ">=350"
    assert d1[0].observed == pytest.approx(339.0, abs=2.0)
    assert d1[0].observed < 350.0
    # Topology does not flip despite distortion: ester unit stays.
    kinds1 = [u for u in res.units if 1 in u.centers]
    assert len(kinds1) == 1 and kinds1[0].kind == "ester"


def test_rpdd_fragment_c1_lock_c6_normal() -> None:
    els, xyz = _load_xyz(FRAG)
    res = analyze_rigid_units(xyz, els, _rpdd_adjacency(), RING_RPDD)
    locks0 = _locks_by_center(res, 0)
    assert len(locks0) >= 1
    # Every lock carries sign/magnitude, no stereocenter semantics.
    for lk in locks0:
        assert lk.sign in (1, -1)
        assert lk.magnitude > ABS_V_MIN
        assert not hasattr(lk, "is_stereocenter")
    # C6 (index 5): sp2 ester, planar, no lock, no distorted.
    assert 5 in res.sp2_centers
    assert _locks_by_center(res, 5) == []
    assert [d for d in res.distorted_input if d.atom == 5] == []


def test_rpdd_crest1_two_ester_pins_no_distorted() -> None:
    els, xyz = _load_xyz(CREST, frame=0)
    res = analyze_rigid_units(xyz, els, _rpdd_adjacency(), RING_RPDD)
    assert tuple(res.sp2_centers) == (1, 5)
    pins = sorted(b for u in res.units for b in u.pinned_bonds)
    assert (1, 3) in pins
    assert (5, 7) in pins
    assert list(res.distorted_input) == []
    assert [d for d in res.distorted_input if d.atom in (1, 5)] == []


def _cyclohexene_system() -> tuple[np.ndarray, list[str], dict[int, set[int]], list[int]]:
    xyz = np.array(
        [
            [0.0, 0.0, 0.0],
            [1.34, 0.0, 0.0],
            [2.3, -1.3, 0.0],
            [1.6, -2.8, 0.3],
            [-0.1, -2.7, -0.2],
            [-0.77, -1.335, 0.0],
            [-0.545, 0.944, 0.0],
            [1.885, 0.944, 0.0],
            [3.0, -0.7, 0.5],
            [2.6, -1.9, -0.7],
            [2.0, -2.5, 1.2],
            [1.9, -3.7, -0.3],
            [-0.3, -3.4, 0.6],
            [-0.6, -2.9, -1.1],
            [-0.5, -0.6, 0.8],
            [-1.7, -1.5, -0.5],
        ],
        dtype=float,
    )
    els = ["C"] * 6 + ["H"] * 10
    adj: dict[int, set[int]] = {i: set() for i in range(16)}

    def link(a: int, b: int) -> None:
        adj[a].add(b)
        adj[b].add(a)

    for a, b in [
        (0, 1),
        (1, 2),
        (2, 3),
        (3, 4),
        (4, 5),
        (5, 0),
        (0, 6),
        (1, 7),
        (2, 8),
        (2, 9),
        (3, 10),
        (3, 11),
        (4, 12),
        (4, 13),
        (5, 14),
        (5, 15),
    ]:
        link(a, b)
    return xyz, els, adj, [0, 1, 2, 3, 4, 5]


def test_cyclohexene_positive_alkene() -> None:
    xyz, els, adj, ring = _cyclohexene_system()
    res = analyze_rigid_units(xyz, els, adj, ring)
    assert 0 in res.sp2_centers and 1 in res.sp2_centers
    alk = [u for u in res.units if u.kind == "alkene"]
    assert len(alk) == 1
    assert alk[0].pinned_bonds == ((0, 1),)
    assert tuple(sorted(alk[0].centers)) == (0, 1)


def _valerolactone_system() -> tuple[np.ndarray, list[str], dict[int, set[int]], list[int]]:
    xyz = np.array(
        [
            [0.0, 0.0, 0.0],
            [1.40, 0.25, 0.0],
            [2.65, -0.55, 0.1],
            [2.1, -2.0, -0.2],
            [0.4, -2.2, 0.2],
            [-0.6, -1.15, -0.1],
            [1.60, 1.42, 0.0],
            [3.4, -0.1, 0.6],
            [2.8, -0.9, -0.9],
            [2.7, -2.5, 0.5],
            [2.2, -2.3, -1.2],
            [0.3, -3.1, 0.8],
            [0.2, -2.4, -0.8],
            [-1.5, -1.3, 0.4],
            [-0.8, -1.3, -1.1],
        ],
        dtype=float,
    )
    els = ["O", "C", "C", "C", "C", "C", "O"] + ["H"] * 8
    adj: dict[int, set[int]] = {i: set() for i in range(15)}

    def link(a: int, b: int) -> None:
        adj[a].add(b)
        adj[b].add(a)

    for a, b in [
        (0, 1),
        (1, 2),
        (2, 3),
        (3, 4),
        (4, 5),
        (5, 0),
        (1, 6),
        (2, 7),
        (2, 8),
        (3, 9),
        (3, 10),
        (4, 11),
        (4, 12),
        (5, 13),
        (5, 14),
    ]:
        link(a, b)
    return xyz, els, adj, [0, 1, 2, 3, 4, 5]


def test_delta_valerolactone_positive_ester() -> None:
    xyz, els, adj, ring = _valerolactone_system()
    res = analyze_rigid_units(xyz, els, adj, ring)
    assert 1 in res.sp2_centers
    est = [u for u in res.units if u.kind == "ester"]
    assert len(est) == 1
    assert est[0].pinned_bonds == ((0, 1),)


def _morpholine_system() -> tuple[np.ndarray, list[str], dict[int, set[int]], list[int]]:
    xyz = np.array(
        [
            [1.43, 0.0, 0.0],
            [2.5, 0.9, 0.1],
            [2.0, 2.3, -0.1],
            [0.6, 2.5, 0.2],
            [-0.5, 1.6, -0.1],
            [-0.2, 0.1, 0.1],
            [3.4, 0.5, 0.5],
            [2.7, 0.7, -0.9],
            [2.3, 3.0, 0.6],
            [2.3, 2.4, -1.1],
            [0.4, 3.3, -0.4],
            [-0.4, 2.1, -1.0],
            [-1.4, 1.9, 0.4],
            [-1.0, -0.2, -0.4],
            [0.5, -0.4, 0.9],
        ],
        dtype=float,
    )
    els = ["O", "C", "C", "N", "C", "C"] + ["H"] * 9
    adj: dict[int, set[int]] = {i: set() for i in range(15)}

    def link(a: int, b: int) -> None:
        adj[a].add(b)
        adj[b].add(a)

    for a, b in [
        (0, 1),
        (1, 2),
        (2, 3),
        (3, 4),
        (4, 5),
        (5, 0),
        (1, 6),
        (1, 7),
        (2, 8),
        (2, 9),
        (3, 10),
        (4, 11),
        (4, 12),
        (5, 13),
        (5, 14),
    ]:
        link(a, b)
    return xyz, els, adj, [0, 1, 2, 3, 4, 5]


def test_morpholine_negative_no_planar_unit() -> None:
    xyz, els, adj, ring = _morpholine_system()
    res = analyze_rigid_units(xyz, els, adj, ring)
    # Complete saturated hetero topology: no sp2, no units, no distorted.
    assert list(res.sp2_centers) == []
    assert list(res.units) == []
    assert list(res.distorted_input) == []
    # Bare-C-ring impersonation guard: hetero atoms are really present.
    assert els[0] == "O" and els[3] == "N"


def _amide_system() -> tuple[np.ndarray, list[str], dict[int, set[int]], list[int]]:
    xyz = np.array(
        [
            [0.0, 0.0, 0.0],
            [1.40, 0.1, 0.0],
            [2.65, -0.75, 0.1],
            [2.0, -2.2, -0.2],
            [0.4, -2.3, 0.2],
            [-0.6, -1.2, -0.1],
            [1.62, 1.28, 0.0],
            [-0.65, 0.85, 0.0],
            [3.4, -0.3, 0.6],
            [2.8, -1.0, -0.9],
            [2.6, -2.7, 0.5],
            [2.1, -2.5, -1.2],
            [0.3, -3.2, 0.8],
            [0.2, -2.5, -0.8],
            [-1.5, -1.4, 0.4],
            [-0.8, -1.3, -1.1],
        ],
        dtype=float,
    )
    els = ["N", "C", "C", "C", "C", "C", "O", "H"] + ["H"] * 8
    adj: dict[int, set[int]] = {i: set() for i in range(16)}

    def link(a: int, b: int) -> None:
        adj[a].add(b)
        adj[b].add(a)

    for a, b in [
        (0, 1),
        (1, 2),
        (2, 3),
        (3, 4),
        (4, 5),
        (5, 0),
        (1, 6),
        (0, 7),
        (2, 8),
        (2, 9),
        (3, 10),
        (3, 11),
        (4, 12),
        (4, 13),
        (5, 14),
        (5, 15),
    ]:
        link(a, b)
    return xyz, els, adj, [0, 1, 2, 3, 4, 5]


def test_amide_positive() -> None:
    xyz, els, adj, ring = _amide_system()
    res = analyze_rigid_units(xyz, els, adj, ring)
    assert 1 in res.sp2_centers
    am = [u for u in res.units if u.kind == "amide"]
    assert len(am) == 1
    assert am[0].pinned_bonds == ((0, 1),)


def _imine_system() -> tuple[np.ndarray, list[str], dict[int, set[int]], list[int]]:
    xyz = np.array(
        [
            [0.0, 0.0, 0.0],
            [1.29, 0.12, 0.0],
            [2.55, -0.6, 0.1],
            [2.0, -2.0, -0.2],
            [0.4, -2.2, 0.2],
            [-0.85, -1.25, -0.1],
            [-0.7, 0.85, 0.0],
            [3.3, -0.3, 0.5],
            [2.6, -0.9, -0.9],
            [2.6, -2.5, 0.5],
            [2.1, -2.3, -1.2],
            [0.3, -3.1, 0.8],
            [0.2, -2.4, -0.8],
            [-1.7, -1.5, 0.4],
            [-1.0, -1.4, -1.1],
        ],
        dtype=float,
    )
    els = ["C", "N", "C", "C", "C", "C", "H"] + ["H"] * 8
    adj: dict[int, set[int]] = {i: set() for i in range(15)}

    def link(a: int, b: int) -> None:
        adj[a].add(b)
        adj[b].add(a)

    for a, b in [
        (0, 1),
        (1, 2),
        (2, 3),
        (3, 4),
        (4, 5),
        (5, 0),
        (0, 6),
        (2, 7),
        (2, 8),
        (3, 9),
        (3, 10),
        (4, 11),
        (4, 12),
        (5, 13),
        (5, 14),
    ]:
        link(a, b)
    return xyz, els, adj, [0, 1, 2, 3, 4, 5]


def test_imine_positive_distance_and_explicit() -> None:
    xyz, els, adj, ring = _imine_system()
    res = analyze_rigid_units(xyz, els, adj, ring)
    assert 0 in res.sp2_centers and 1 in res.sp2_centers
    im = [u for u in res.units if u.kind == "imine"]
    assert len(im) == 1
    assert im[0].pinned_bonds == ((0, 1),)
    # Explicit double agrees (priority path gives the same unit).
    res2 = analyze_rigid_units(xyz, els, adj, ring, typed_edges=[(0, 1, 2.0)])
    im2 = [u for u in res2.units if u.kind == "imine"]
    assert len(im2) == 1 and im2[0].pinned_bonds == ((0, 1),)


def test_aromatic_needs_explicit() -> None:
    xyz = np.array(
        [
            [1.39, 0, 0],
            [0.695, 1.204, 0],
            [-0.695, 1.204, 0],
            [-1.39, 0, 0],
            [-0.695, -1.204, 0],
            [0.695, -1.204, 0],
            [2.4, 0, 0],
            [1.2, 2.1, 0],
            [-1.2, 2.1, 0],
            [-2.4, 0, 0],
            [-1.2, -2.1, 0],
            [1.2, -2.1, 0],
        ],
        dtype=float,
    )
    els = ["C"] * 6 + ["H"] * 6
    adj: dict[int, set[int]] = {i: set() for i in range(12)}

    def link(a: int, b: int) -> None:
        adj[a].add(b)
        adj[b].add(a)

    for a, b in [
        (0, 1),
        (1, 2),
        (2, 3),
        (3, 4),
        (4, 5),
        (5, 0),
        (0, 6),
        (1, 7),
        (2, 8),
        (3, 9),
        (4, 10),
        (5, 11),
    ]:
        link(a, b)
    ring = [0, 1, 2, 3, 4, 5]
    # Short CC (~1.39) alone never yields aromatic.
    res = analyze_rigid_units(xyz, els, adj, ring)
    assert [u for u in res.units if u.kind == "aromatic_fused"] == []
    # Explicit 1.5 orders do.
    edges = [(0, 1, 1.5), (1, 2, 1.5), (2, 3, 1.5), (3, 4, 1.5), (4, 5, 1.5), (5, 0, 1.5)]
    res2 = analyze_rigid_units(xyz, els, adj, ring, typed_edges=edges)
    arom = [u for u in res2.units if u.kind == "aromatic_fused"]
    assert len(arom) == 1
    assert set(arom[0].centers) == set(ring)


def test_explicit_single_beats_short_distance() -> None:
    # C-O 1.25 short but declared single: stays single, ambiguity, no pin/lock.
    xyz = np.array(
        [
            [0.0, 0.0, 0.0],
            [1.51, 0.2, 0.0],
            [2.6, -0.6, 0.1],
            [2.0, -2.0, -0.2],
            [0.4, -2.2, 0.2],
            [-0.6, -1.2, -0.1],
            [1.51, 1.45, 0.0],
            [3.3, -0.3, 0.5],
            [2.7, -0.9, -0.9],
            [2.6, -2.5, 0.5],
            [2.1, -2.3, -1.2],
            [0.3, -3.1, 0.8],
            [0.2, -2.4, -0.8],
            [-1.5, -1.4, 0.4],
            [-0.8, -1.3, -1.1],
        ],
        dtype=float,
    )
    # Force C1-O6 to 1.25: move O6 to (1.51+0.3, 0.2+1.21, 0) approx 1.25?
    xyz[6] = np.array([1.51, 0.2 + 1.25, 0.0])
    els = ["O", "C", "C", "C", "C", "C", "O"] + ["H"] * 8
    adj: dict[int, set[int]] = {i: set() for i in range(15)}

    def link(a: int, b: int) -> None:
        adj[a].add(b)
        adj[b].add(a)

    for a, b in [
        (0, 1),
        (1, 2),
        (2, 3),
        (3, 4),
        (4, 5),
        (5, 0),
        (1, 6),
        (2, 7),
        (2, 8),
        (3, 9),
        (3, 10),
        (4, 11),
        (4, 12),
        (5, 13),
        (5, 14),
    ]:
        link(a, b)
    ring = [0, 1, 2, 3, 4, 5]
    res = analyze_rigid_units(xyz, els, adj, ring, typed_edges=[(1, 6, 1.0)])
    assert 1 not in res.sp2_centers
    assert [u for u in res.units if 1 in u.centers] == []
    assert any(
        a.atom == 1 and a.reason == "explicit_single_vs_short_distance" for a in res.ambiguities
    )
    assert [lk for lk in res.local_orientation_locks if lk.center == 1] == []


def test_ambiguity_two_carbonyl_candidates_no_lock() -> None:
    # Ring C with two short ring-C neighbors + H: two CC candidates
    # -> multiple_double_candidates, no pin, no lock.
    xyz = np.array(
        [
            [0.0, 0.0, 0.0],
            [1.34, 0.0, 0.0],
            [-0.67, 1.16, 0.0],
            [2.5, -1.0, 0.3],
            [2.0, -2.4, -0.3],
            [-0.67, -1.16, 0.0],
            [0.0, 0.0, 1.09],
            [1.34, 0.0, 1.09],
            [3.3, -0.6, 0.6],
            [2.7, -1.2, -0.8],
            [2.6, -2.9, 0.4],
            [2.1, -2.7, -1.2],
            [0.4, -3.4, 0.7],
            [0.4, -2.7, -0.8],
            [-1.2, -1.8, 0.3],
            [-0.7, -1.4, -1.4],
        ],
        dtype=float,
    )
    els = ["C"] * 6 + ["H"] + ["H"] * 9
    # c=0 neighbors: ring 1 (1.34 short), ring 5 (~1.39 short), exo H6.
    # Both ring CC short -> ambiguous.
    adj: dict[int, set[int]] = {i: set() for i in range(16)}

    def link(a: int, b: int) -> None:
        adj[a].add(b)
        adj[b].add(a)

    for a, b in [
        (0, 1),
        (1, 2),
        (2, 3),
        (3, 4),
        (4, 5),
        (5, 0),
        (0, 6),
        (1, 7),
        (2, 8),
        (2, 9),
        (3, 10),
        (3, 11),
        (4, 12),
        (4, 13),
        (5, 14),
        (5, 15),
    ]:
        link(a, b)
    ring = [0, 1, 2, 3, 4, 5]
    res = analyze_rigid_units(xyz, els, adj, ring)
    assert 0 not in res.sp2_centers
    assert any(a.atom == 0 and a.reason == "multiple_double_candidates" for a in res.ambiguities)
    assert [lk for lk in res.local_orientation_locks if lk.center == 0] == []


def test_saturated_carbon_never_sp2() -> None:
    # Cyclohexane: all CC ~1.54, CH2 x4 neighbors -> no sp2 anywhere.
    R = 1.60
    angs = [math.radians(60 * k) for k in range(6)]
    ring_xy = [
        (R * math.cos(a), R * math.sin(a), 0.25 * ((k % 2) * 2 - 1)) for k, a in enumerate(angs)
    ]
    xyz_list: list[list[float]] = [list(p) for p in ring_xy]
    # Two H per ring C: above/below.
    for x, y, z in ring_xy:
        xyz_list.append([x * 1.35, y * 1.35, z + 0.85])
        xyz_list.append([x * 1.35, y * 1.35, z - 0.85])
    xyz = np.array(xyz_list, dtype=float)
    els = ["C"] * 6 + ["H"] * 12
    adj: dict[int, set[int]] = {i: set() for i in range(18)}

    def link(a: int, b: int) -> None:
        adj[a].add(b)
        adj[b].add(a)

    for k in range(6):
        link(k, (k + 1) % 6)
        link(k, 6 + 2 * k)
        link(k, 6 + 2 * k + 1)
    ring = [0, 1, 2, 3, 4, 5]
    res = analyze_rigid_units(xyz, els, adj, ring)
    assert list(res.sp2_centers) == []
    assert list(res.units) == []
    assert list(res.distorted_input) == []


def _lock_probe_system(h: float) -> tuple[np.ndarray, list[str], dict[int, set[int]], list[int]]:
    # c=0 ring C with 4 neighbors (p, n ring + 2 exo H) -> never sp2
    # (tot==4 silent non-sp2), so lock evaluation is purely geometric.
    # p=(1,0,0), n=(0,1,0), s=(0,0,h): V = h exactly (unit vectors).
    xyz = np.array(
        [
            [0.0, 0.0, 0.0],
            [0.0, 1.0, 0.0],
            [1.5, 2.2, 0.2],
            [2.2, 1.0, -0.2],
            [1.4, -0.2, 0.2],
            [1.0, 0.0, 0.0],
            [0.0, 0.0, h],
            [0.0, 0.0, 2.0],
        ],
        dtype=float,
    )
    els = ["C"] * 6 + ["H", "H"]
    adj: dict[int, set[int]] = {i: set() for i in range(8)}

    def link(a: int, b: int) -> None:
        adj[a].add(b)
        adj[b].add(a)

    for a, b in [(0, 1), (1, 2), (2, 3), (3, 4), (4, 5), (5, 0), (0, 6), (0, 7)]:
        link(a, b)
    return xyz, els, adj, [0, 1, 2, 3, 4, 5]


def test_volume_threshold_boundary() -> None:
    for h, want in [(0.29, False), (0.31, True)]:
        xyz, els, adj, ring = _lock_probe_system(h)
        res = analyze_rigid_units(xyz, els, adj, ring)
        got = (
            len([lk for lk in res.local_orientation_locks if lk.center == 0 and lk.subst == 6]) > 0
        )
        assert got == want, h
    # Exactly 0.3 does not lock (strict >).
    xyz, els, adj, ring = _lock_probe_system(0.30)
    res = analyze_rigid_units(xyz, els, adj, ring)
    assert [lk for lk in res.local_orientation_locks if lk.center == 0 and lk.subst == 6] == []


def _angle_probe_system(dz: float) -> tuple[np.ndarray, list[str], dict[int, set[int]], list[int]]:
    # Carbonyl-like C1 with ring O0, ring C2, exo O3; N/A hetero layout minimal.
    # Center c=1, neighbors [0, 2, 3] with 3 at (-0.5,-0.866,dz) pattern.
    xyz = np.array(
        [
            [1.0, 0.0, 0.0],
            [0.0, 0.0, 0.0],
            [-0.5, 0.8660254, 0.0],
            [-0.5, -0.8660254, dz],
            [2.0, -1.0, 0.3],
            [2.5, 0.5, -0.2],
            [1.5, 1.5, 0.2],
        ],
        dtype=float,
    )
    # ring = [1, 2, 4, 5, 6, 0]? Simplify: use 6-ring [0,1,2,4,5,6]? Keep c=1
    # with ring neighbors 0 and 2; exo 3.
    els = ["O", "C", "C", "O", "C", "C", "C"]
    adj: dict[int, set[int]] = {i: set() for i in range(7)}

    def link(a: int, b: int) -> None:
        adj[a].add(b)
        adj[b].add(a)

    for a, b in [(0, 1), (1, 2), (2, 4), (4, 5), (5, 6), (6, 0), (1, 3)]:
        link(a, b)
    # Distances: force C1-O3 short (carbonyl) and C1-O0/C1-C2 plausible.
    # O3 at dz height changes angle sum but keeps C=O ~1.0-1.3? Recompute below.
    return xyz, els, adj, [0, 1, 2, 4, 5, 6]


def test_angle_sum_boundary() -> None:
    xyz_hi, els, adj, ring = _angle_probe_system(0.6)
    res_hi = analyze_rigid_units(xyz_hi, els, adj, ring)
    # Must be sp2 (exo O short) with no distorted (sum ~350.8).
    assert 1 in res_hi.sp2_centers
    assert [d for d in res_hi.distorted_input if d.atom == 1] == []
    xyz_lo, _, _, _ = _angle_probe_system(0.7)
    res_lo = analyze_rigid_units(xyz_lo, els, adj, ring)
    assert 1 in res_lo.sp2_centers
    got = [d for d in res_lo.distorted_input if d.atom == 1]
    assert len(got) == 1
    assert got[0].observed < ANGLE_SUM_MIN


def test_topology_not_flipped_by_distortion() -> None:
    els, xyz = _load_xyz(FRAG)
    res = analyze_rigid_units(xyz, els, _rpdd_adjacency(), RING_RPDD)
    # Even with angle sum 339 << 350, C2 stays ester sp2.
    assert 1 in res.sp2_centers
    assert any(u.kind == "ester" and 1 in u.centers for u in res.units)


def test_rigid_motion_invariance_and_input_immutable() -> None:
    els, xyz = _load_xyz(FRAG)
    adj = _rpdd_adjacency()
    xyz_before = xyz.copy()
    adj_before = copy.deepcopy(adj)
    els_before = list(els)
    res0 = analyze_rigid_units(xyz, els, adj, RING_RPDD)
    # Proper rotation + translation.
    t = math.radians(37.0)
    Rz = np.array([[math.cos(t), -math.sin(t), 0], [math.sin(t), math.cos(t), 0], [0, 0, 1]])
    xyz2 = xyz @ Rz.T + np.array([5.0, -3.0, 2.0])
    res2 = analyze_rigid_units(xyz2, els, adj, RING_RPDD)
    assert tuple(res2.sp2_centers) == tuple(res0.sp2_centers)
    assert [(u.kind, u.centers, u.pinned_bonds) for u in res2.units] == [
        (u.kind, u.centers, u.pinned_bonds) for u in res0.units
    ]
    assert sorted(d.atom for d in res2.distorted_input) == sorted(
        d.atom for d in res0.distorted_input
    )
    for a, b in zip(
        sorted(res0.local_orientation_locks, key=lambda L: (L.center, L.subst)),
        sorted(res2.local_orientation_locks, key=lambda L: (L.center, L.subst)),
    ):
        assert (a.center, a.subst, a.sign) == (b.center, b.subst, b.sign)
        assert b.magnitude == pytest.approx(a.magnitude, rel=1e-9)
    # Inputs untouched.
    assert np.array_equal(xyz, xyz_before)
    assert adj == adj_before
    assert els == els_before


def test_pinned_bonds_canonical_and_immutable() -> None:
    els, xyz = _load_xyz(CREST, frame=0)
    res = analyze_rigid_units(xyz, els, _rpdd_adjacency(), RING_RPDD)
    for u in res.units:
        for a, b in u.pinned_bonds:
            assert isinstance(a, int) and isinstance(b, int)
            assert a < b
        assert tuple(sorted(u.pinned_bonds)) == u.pinned_bonds
    with pytest.raises(AttributeError):
        res.units[0].pinned_bonds = ((9, 9),)  # type: ignore[misc]


def test_failures_unknown_bad_nonfinite() -> None:
    els, xyz = _load_xyz(FRAG)
    adj = _rpdd_adjacency()
    bad_els = list(els)
    bad_els[0] = "Xx"
    with pytest.raises(ValueError):
        analyze_rigid_units(xyz, bad_els, adj, RING_RPDD)
    with pytest.raises(ValueError):
        analyze_rigid_units(xyz, els, adj, [0, 1, 99, 4, 5, 7])
    xyz_bad = xyz.copy()
    xyz_bad[0, 0] = float("nan")
    with pytest.raises(ValueError):
        analyze_rigid_units(xyz_bad, els, adj, RING_RPDD)
    with pytest.raises(ValueError):
        analyze_rigid_units(xyz, els, adj, RING_RPDD, typed_edges=[(0, 99, 2.0)])


# ---- R2 root followup (v2): real TypedEdge / real context, no mocks ----


def test_root_single_authority_domain_elements() -> None:
    import confflow.science.confgen.ring.rigid_units as ru

    assert not hasattr(ru, "_PERIODIC_SYMBOLS")
    from confflow.domain.elements import canonical_element_symbol

    assert canonical_element_symbol("c") == "C"
    assert canonical_element_symbol("Cl") == "Cl"
    # Lowercase input accepted via the single authority.
    els, xyz = _load_xyz(FRAG)
    els_low = [e.lower() for e in els]
    res = analyze_rigid_units(xyz, els_low, _rpdd_adjacency(), RING_RPDD)
    assert 1 in res.sp2_centers


def test_root_tuple_adjacency_and_real_context() -> None:
    from confflow.domain.structure import StructureRecord
    from confflow.science.confgen.model import build_context

    xyz, els, adj, ring = _cyclohexene_system()
    tup = tuple(tuple(sorted(v)) for _, v in sorted(adj.items()))
    res_tup = analyze_rigid_units(xyz, els, tup, ring)
    res_dict = analyze_rigid_units(xyz, els, adj, ring)
    assert tuple(res_tup.sp2_centers) == tuple(res_dict.sp2_centers)
    # Real build_context channel: tuple adjacency + real TypedEdge objects.
    rec = StructureRecord(
        id="r2root", atoms=tuple(els), coordinates=tuple(map(tuple, xyz.tolist()))
    )
    ctx = build_context(rec, {})
    assert isinstance(ctx.adjacency, tuple)
    res_ctx = analyze_rigid_units(
        np.array(ctx.input_coords, dtype=float),
        list(ctx.structure.atoms),
        ctx.adjacency,
        ring,
        typed_edges=list(ctx.graph.edges),
    )
    assert isinstance(res_ctx.sp2_centers, tuple)


def test_root_ring_strict_rejects_float_bool_length() -> None:
    xyz, els, adj, _ = _cyclohexene_system()
    with pytest.raises(ValueError):
        analyze_rigid_units(xyz, els, adj, [0, 1, 2.0, 3, 4, 5])
    with pytest.raises(ValueError):
        analyze_rigid_units(xyz, els, adj, [0, 2, 3, True])
    with pytest.raises(ValueError):
        analyze_rigid_units(xyz, els, adj, [0, 1, 2])
    with pytest.raises(ValueError):
        analyze_rigid_units(xyz, els, adj, [0, 1, 2, 3, 4, 5, 6])
    with pytest.raises(ValueError):
        analyze_rigid_units(xyz, els, adj, ["0", "1", "2", "3", "4", "5"])


def test_root_triple_not_double_real_edge() -> None:
    from confflow.science.confgen.graph import EdgeType, TypedEdge

    xyz, els, adj, ring = _cyclohexene_system()
    te = TypedEdge(a=0, b=1, type=EdgeType.COVALENT, bond_order=3.0)
    res = analyze_rigid_units(xyz, els, adj, ring, typed_edges=[te])
    assert [u for u in res.units if u.kind == "alkene"] == []
    assert 0 not in res.sp2_centers and 1 not in res.sp2_centers
    assert any(a.reason == "explicit_triple_unsupported" for a in res.ambiguities)
    assert [lk for lk in res.local_orientation_locks if lk.center in (0, 1)] == []


def test_root_duplicate_conflict_fails_closed() -> None:
    xyz, els, adj, ring = _cyclohexene_system()
    with pytest.raises(ValueError):
        analyze_rigid_units(xyz, els, adj, ring, typed_edges=[(0, 1, 2.0), (0, 1, 1.0)])
    # Identical duplicates are idempotent (no conflict).
    res = analyze_rigid_units(xyz, els, adj, ring, typed_edges=[(0, 1, 2.0), (0, 1, 2.0)])
    assert any(u.kind == "alkene" for u in res.units)


def test_root_invalid_order_no_fallback_real_edge() -> None:
    from confflow.science.confgen.graph import EdgeType, TypedEdge

    xyz, els, adj, ring = _cyclohexene_system()
    # Out-of-range raw order: no distance fallback, ambiguity, no lock.
    res = analyze_rigid_units(xyz, els, adj, ring, typed_edges=[(0, 1, 99.0)])
    assert 0 not in res.sp2_centers and 1 not in res.sp2_centers
    assert any(a.reason == "invalid_bond_order" for a in res.ambiguities)
    assert [lk for lk in res.local_orientation_locks if lk.center in (0, 1)] == []
    # Bool order fails closed globally (real TypedEdge also rejects bool).
    with pytest.raises(ValueError):
        analyze_rigid_units(xyz, els, adj, ring, typed_edges=[(0, 1, True)])
    with pytest.raises(ValueError):
        TypedEdge(a=0, b=1, type=EdgeType.COVALENT, bond_order=True)  # type: ignore[arg-type]


def test_root_non_covalent_ignored_real_edges() -> None:
    from confflow.science.confgen.graph import EdgeType, TypedEdge

    xyz, els, adj, ring = _cyclohexene_system()
    # Stretch C0-C1 to 1.54 so distance alone gives no sp2; only a
    # covalent double would create one.
    xyz_long = xyz.copy()
    xyz_long[1] = xyz_long[0] + np.array([1.54, 0.0, 0.0])
    for kind in (EdgeType.COORDINATION, EdgeType.FORMING, EdgeType.BREAKING):
        te = TypedEdge(a=0, b=1, type=kind, bond_order=2.0)
        res = analyze_rigid_units(xyz_long, els, adj, ring, typed_edges=[te])
        assert 0 not in res.sp2_centers and 1 not in res.sp2_centers
        assert [u for u in res.units if u.kind in ("alkene", "imine")] == []
    # Real covalent double still pins.
    te_cov = TypedEdge(a=0, b=1, type=EdgeType.COVALENT, bond_order=2.0)
    res_cov = analyze_rigid_units(xyz_long, els, adj, ring, typed_edges=[te_cov])
    assert any(u.kind == "alkene" and u.pinned_bonds == ((0, 1),) for u in res_cov.units)


def test_root_unknown_aromatic_short_cc_no_forged_valence() -> None:
    xyz = np.array(
        [
            [1.39, 0, 0],
            [0.695, 1.204, 0],
            [-0.695, 1.204, 0],
            [-1.39, 0, 0],
            [-0.695, -1.204, 0],
            [0.695, -1.204, 0],
            [2.4, 0, 0],
            [1.2, 2.1, 0],
            [-1.2, 2.1, 0],
            [-2.4, 0, 0],
            [-1.2, -2.1, 0],
            [1.2, -2.1, 0],
        ],
        dtype=float,
    )
    els = ["C"] * 6 + ["H"] * 6
    adj: dict[int, set[int]] = {i: set() for i in range(12)}

    def link(a: int, b: int) -> None:
        adj[a].add(b)
        adj[b].add(a)

    for a, b in [
        (0, 1),
        (1, 2),
        (2, 3),
        (3, 4),
        (4, 5),
        (5, 0),
        (0, 6),
        (1, 7),
        (2, 8),
        (3, 9),
        (4, 10),
        (5, 11),
    ]:
        link(a, b)
    ring = [0, 1, 2, 3, 4, 5]
    res = analyze_rigid_units(xyz, els, adj, ring)
    assert [u for u in res.units if u.kind == "aromatic_fused"] == []
    # Unknown short CC must not forge unreasonable sp2 valence.
    assert list(res.sp2_centers) == []


def test_root_ambiguous_pyramid_no_lock() -> None:
    # Reuse the two-candidate ambiguous center: large out-of-plane H
    # must not create a lock when hybridization is unknown.
    xyz = np.array(
        [
            [0.0, 0.0, 0.0],
            [1.34, 0.0, 0.0],
            [-0.67, 1.16, 0.0],
            [2.5, -1.0, 0.3],
            [2.0, -2.4, -0.3],
            [-0.67, -1.16, 0.0],
            [0.0, 0.0, 1.5],
            [1.34, 0.0, 1.09],
            [3.3, -0.6, 0.6],
            [2.7, -1.2, -0.8],
            [2.6, -2.9, 0.4],
            [2.1, -2.7, -1.2],
            [0.4, -3.4, 0.7],
            [0.4, -2.7, -0.8],
            [-1.2, -1.8, 0.3],
            [-0.7, -1.4, -1.4],
        ],
        dtype=float,
    )
    els = ["C"] * 6 + ["H"] + ["H"] * 9
    adj: dict[int, set[int]] = {i: set() for i in range(16)}

    def link(a: int, b: int) -> None:
        adj[a].add(b)
        adj[b].add(a)

    for a, b in [
        (0, 1),
        (1, 2),
        (2, 3),
        (3, 4),
        (4, 5),
        (5, 0),
        (0, 6),
        (1, 7),
        (2, 8),
        (2, 9),
        (3, 10),
        (3, 11),
        (4, 12),
        (4, 13),
        (5, 14),
        (5, 15),
    ]:
        link(a, b)
    ring = [0, 1, 2, 3, 4, 5]
    res = analyze_rigid_units(xyz, els, adj, ring)
    assert any(a.atom == 0 and a.reason == "multiple_double_candidates" for a in res.ambiguities)
    assert [lk for lk in res.local_orientation_locks if lk.center == 0] == []
