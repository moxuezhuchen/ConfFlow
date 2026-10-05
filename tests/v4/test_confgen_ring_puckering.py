#!/usr/bin/env python3

"""R1 puckering math tests (direction: table -> geometry -> back-computed CP).

Covers authoritative CP tables, <0.5 deg recovery, zero-torsion bonds
computed from the inverse-CP construction, full shift/reverse covariance
with the fixed shift-first-then-reverse order, winding twins, rigid-motion
invariance, pole stability, and degenerate-input handling.
"""

from __future__ import annotations

import math

import numpy as np
import pytest

from confflow.science.confgen.ring.geometry import ring_torsions
from confflow.science.confgen.ring.puckering import (
    CanonicalForm,
    CPCoords,
    canonical_forms,
    cp_distance,
    cp_to_coords,
    cremer_pople,
    relabel,
)

TOL_DEG = 0.5


def _permute(xyz: np.ndarray, shift: int, reverse: bool) -> np.ndarray:
    n = xyz.shape[0]
    idx = [(shift - j) % n if reverse else (shift + j) % n for j in range(n)]
    return xyz[np.array(idx)]


def _rot_x(deg: float) -> np.ndarray:
    t = math.radians(deg)
    return np.array([[1, 0, 0], [0, math.cos(t), -math.sin(t)], [0, math.sin(t), math.cos(t)]])


def _rot_z(deg: float) -> np.ndarray:
    t = math.radians(deg)
    return np.array([[math.cos(t), -math.sin(t), 0], [math.sin(t), math.cos(t), 0], [0, 0, 1]])


def _is_polar(theta: float) -> bool:
    return min(theta, 180.0 - theta) < 0.75


def test_counts_and_families() -> None:
    f4 = canonical_forms(4)
    f5 = canonical_forms(5)
    f6 = canonical_forms(6)
    assert len(f4) == 3
    assert len(f5) == 20
    assert len(f6) == 38
    assert sorted(f.family for f in f4) == ["B+", "B-", "P"]
    assert sum(1 for f in f5 if f.family == "E") == 10
    assert sum(1 for f in f5 if f.family == "T") == 10
    counts: dict[str, int] = {}
    for f in f6:
        counts[f.family] = counts.get(f.family, 0) + 1
    assert counts == {"C": 2, "B": 6, "TB": 6, "E": 12, "H": 12}
    with pytest.raises(ValueError):
        canonical_forms(7)


def test_authoritative_tables() -> None:
    forms6 = {(f.family, f.index): f.cp_target for f in canonical_forms(6)}
    assert forms6[("C", 0)].theta == 0.0
    assert forms6[("C", 1)].theta == 180.0
    for k in range(6):
        assert forms6[("B", k)].theta == 90.0
        assert forms6[("B", k)].phi == pytest.approx(60.0 * k)
        assert forms6[("TB", k)].theta == 90.0
        assert forms6[("TB", k)].phi == pytest.approx(30.0 + 60.0 * k)
        assert forms6[("E", k)].theta == 54.7
        assert forms6[("E", k)].phi == pytest.approx(60.0 * k)
        assert forms6[("E", 6 + k)].theta == 125.3
        assert forms6[("H", k)].theta == 50.8
        assert forms6[("H", k)].phi == pytest.approx(30.0 + 60.0 * k)
        assert forms6[("H", 6 + k)].theta == 129.2
    forms5 = {(f.family, f.index): f.cp_target for f in canonical_forms(5)}
    for k in range(10):
        assert forms5[("E", k)].phi == pytest.approx(36.0 * k)
        assert forms5[("T", k)].phi == pytest.approx(18.0 + 36.0 * k)


def test_roundtrip_all_forms() -> None:
    for form in canonical_forms(6):
        back = cremer_pople(cp_to_coords(form.cp_target))
        assert cp_distance(back, form.cp_target) < TOL_DEG, form
        if _is_polar(form.cp_target.theta):
            continue
        assert abs(back.theta - form.cp_target.theta) < TOL_DEG
    for form in canonical_forms(5):
        back = cremer_pople(cp_to_coords(form.cp_target))
        assert cp_distance(back, form.cp_target) < TOL_DEG, form
    for form in canonical_forms(4):
        back = cremer_pople(cp_to_coords(form.cp_target))
        assert back.sign == form.cp_target.sign
        assert abs(back.q - form.cp_target.q) < 1e-9


def test_zero_torsion_matches_construction() -> None:
    for form in list(canonical_forms(6)) + list(canonical_forms(5)):
        xyz = cp_to_coords(form.cp_target)
        tors = ring_torsions(xyz)
        expect: list[str] = []
        for j in range(xyz.shape[0]):
            if abs(float(tors[j])) < 3.0:
                expect.append(f"{(j + 1) % xyz.shape[0]}-{(j + 2) % xyz.shape[0]}")
        assert tuple(expect) == form.zero_torsion_bonds, form


def test_three_plan_examples_verbatim() -> None:
    lut = {(f.family, f.cp_target.theta, round(f.cp_target.phi, 9)): f for f in canonical_forms(6)}
    assert lut[("E", 54.7, 0)].zero_torsion_bonds == ("2-3", "3-4")
    assert lut[("H", 50.8, 30)].zero_torsion_bonds == ("3-4",)
    assert lut[("B", 90.0, 0)].zero_torsion_bonds == ("1-2", "4-5")


def test_shift_keeps_family() -> None:
    for form in list(canonical_forms(6)) + list(canonical_forms(5)):
        for s in range(form.n):
            out = relabel(form, shift=s, reverse=False)
            assert out.family == form.family
            assert out.n == form.n
    assert relabel(canonical_forms(4)[1], shift=1).cp_target.sign == -1
    assert relabel(canonical_forms(4)[1], shift=2).cp_target.sign == 1


def test_full_covariance_all_shifts_reverses() -> None:
    # Fixed order: shift first (possibly repeated), then one reverse.
    for form in canonical_forms(6):
        xyz = cp_to_coords(form.cp_target)
        for s in range(6):
            for rev in (False, True):
                got = cremer_pople(_permute(xyz, s, rev))
                want = relabel(form, shift=s, reverse=rev).cp_target
                assert cp_distance(got, want) < TOL_DEG, (form, s, rev)
    for form in canonical_forms(5):
        xyz = cp_to_coords(form.cp_target)
        for s in range(5):
            for rev in (False, True):
                got = cremer_pople(_permute(xyz, s, rev))
                want = relabel(form, shift=s, reverse=rev).cp_target
                assert cp_distance(got, want) < TOL_DEG, (form, s, rev)


def test_relabel_identity_and_composition() -> None:
    for form in canonical_forms(6):
        assert relabel(form) is form
        assert relabel(form, shift=6) is form
        # shift-first-then-reverse: reverse after one shift equals the
        # composed prediction, checked against permuted xyz elsewhere;
        # here check determinism of the declared order.
        once = relabel(relabel(form, shift=1), reverse=True)
        both = relabel(form, shift=1, reverse=True)
        assert once == both


def test_construction_winding_twin() -> None:
    # Opposite planar winding builds the theta-flipped / phi+180 twin.
    form = next(f for f in canonical_forms(6) if (f.family, f.index) == ("E", 0))
    good = cp_to_coords(form.cp_target)
    assert cp_distance(cremer_pople(good), form.cp_target) < TOL_DEG
    n = 6
    rbar = 1.45
    theta = math.radians(form.cp_target.theta)
    phi = math.radians(form.cp_target.phi)
    q2 = form.cp_target.q * math.sin(theta)
    q3 = form.cp_target.q * math.cos(theta)
    f2 = math.sqrt(2.0 / 6.0)
    f1 = math.sqrt(1.0 / 6.0)
    js = np.arange(n, dtype=float)
    z = f2 * q2 * np.cos(phi + 4.0 * math.pi * js / 6.0) + f1 * q3 * np.where(
        js % 2 == 0, 1.0, -1.0
    )
    radius = rbar / (2.0 * math.sin(math.pi / n))
    angles = +2.0 * math.pi * js / n  # winding=+1 (opposite)
    flipped = np.column_stack((radius * np.cos(angles), radius * np.sin(angles), z))
    twin = cremer_pople(flipped)
    assert abs(twin.theta - (180.0 - form.cp_target.theta)) < TOL_DEG
    assert abs(((twin.phi - (form.cp_target.phi + 180.0)) + 180.0) % 360.0 - 180.0) < TOL_DEG


def test_rigid_motion_invariance() -> None:
    rng_forms = list(canonical_forms(6))[:6] + list(canonical_forms(5))[:4]
    for form in rng_forms:
        xyz = cp_to_coords(form.cp_target)
        for rot, vec in [
            (_rot_x(90.0), np.array([1.0, 2.0, 3.0])),
            (_rot_z(37.0), np.array([-4.0, 0.5, 8.0])),
        ]:
            moved = xyz @ rot.T + vec
            assert cp_distance(cremer_pople(moved), form.cp_target) < 1e-6


def test_chair_pole_phi_ignored_and_stable() -> None:
    chairs = [f for f in canonical_forms(6) if f.family == "C"]
    assert len(chairs) == 2
    for form in chairs:
        back = cremer_pople(cp_to_coords(form.cp_target))
        assert back.phi == 0.0
    a = CPCoords(n=6, q=0.6, theta=0.0, phi=0.0)
    b = CPCoords(n=6, q=0.6, theta=0.0, phi=200.0)
    assert cp_distance(a, b) == pytest.approx(0.0)
    c = CPCoords(n=6, q=0.6, theta=0.0, phi=0.0)
    d = CPCoords(n=6, q=0.6, theta=180.0, phi=0.0)
    assert cp_distance(c, d) == pytest.approx(180.0)


def test_degenerate_inputs_raise_and_flat_is_finite() -> None:
    with pytest.raises(ValueError):
        cremer_pople(np.zeros((6, 2)))
    with pytest.raises(ValueError):
        cremer_pople(np.zeros((7, 3)))
    bad = np.zeros((6, 3))
    bad[0, 0] = np.nan
    with pytest.raises(ValueError):
        cremer_pople(bad)
    with pytest.raises(ValueError):
        cremer_pople(np.zeros((5, 3)))  # all coincident -> vanishing normal
    with pytest.raises(ValueError):
        cp_to_coords(CPCoords(n=6, q=0.6, theta=90.0, phi=0.0), n=5)
    with pytest.raises(ValueError):
        cp_to_coords(CPCoords(n=5, q=0.4, theta=0.0, phi=0.0), rbar=-1.0)
    flat_hex = np.array(
        [
            [math.cos(2.0 * math.pi * j / 6.0), math.sin(2.0 * math.pi * j / 6.0), 0.0]
            for j in range(6)
        ]
    )
    flat = cremer_pople(flat_hex)
    assert math.isfinite(flat.q) and math.isfinite(flat.theta) and math.isfinite(flat.phi)
    with pytest.raises(ValueError):
        cp_distance(
            CPCoords(n=6, q=0.6, theta=90.0, phi=0.0),
            CPCoords(n=5, q=0.4, theta=0.0, phi=0.0),
        )


def test_n4_roundtrip_and_relabel_covariance() -> None:
    for form in canonical_forms(4):
        xyz = cp_to_coords(form.cp_target)
        back = cremer_pople(xyz)
        assert back.sign == form.cp_target.sign
        for s in range(4):
            for rev in (False, True):
                got = cremer_pople(_permute(xyz, s, rev))
                want = relabel(form, shift=s, reverse=rev).cp_target
                assert got.sign == want.sign


def test_cp_distance_angle_regressions() -> None:
    # ROOT-EARLY-FINDINGS blocker: no polar shortcut; the full spherical
    # angle holds from near-coincident through antipodal separations.
    near_a = CPCoords(n=6, q=0.6, theta=0.7, phi=0.0)
    near_b = CPCoords(n=6, q=0.6, theta=0.7, phi=180.0)
    assert cp_distance(near_a, near_b) == pytest.approx(1.4, abs=1e-9)
    pole_a = CPCoords(n=6, q=0.6, theta=0.0, phi=0.0)
    pole_b = CPCoords(n=6, q=0.6, theta=0.0, phi=200.0)
    assert cp_distance(pole_a, pole_b) == pytest.approx(0.0, abs=1e-12)
    anti_a = CPCoords(n=6, q=0.6, theta=0.0, phi=0.0)
    anti_b = CPCoords(n=6, q=0.6, theta=180.0, phi=123.0)
    assert cp_distance(anti_a, anti_b) == pytest.approx(180.0, abs=1e-9)
    tiny_a = CPCoords(n=6, q=0.6, theta=90.0, phi=0.0)
    tiny_b = CPCoords(n=6, q=0.6, theta=90.0, phi=1e-10)
    assert cp_distance(tiny_a, tiny_b) == pytest.approx(1e-10, abs=1e-12)
    equa_a = CPCoords(n=6, q=0.6, theta=90.0, phi=0.0)
    equa_b = CPCoords(n=6, q=0.6, theta=90.0, phi=180.0)
    assert cp_distance(equa_a, equa_b) == pytest.approx(180.0, abs=1e-9)


def _vertical_mirror(beta: float) -> np.ndarray:
    normal = np.array([-math.sin(beta), math.cos(beta), 0.0])
    return np.eye(3) - 2.0 * np.outer(normal, normal)


def _inplane_c2(beta: float) -> np.ndarray:
    axis = np.array([math.cos(beta), math.sin(beta), 0.0])
    return 2.0 * np.outer(axis, axis) - np.eye(3)


def _reversed_about(xyz: np.ndarray, shift: int) -> np.ndarray:
    n = xyz.shape[0]
    return xyz[np.array([(shift - j) % n for j in range(n)])]


def test_family_symmetries_rigid() -> None:
    """Rigid isometries of the constructed geometries (PLAN S0.3 column).

    Independent of CP back-computation: index permutations and isometry
    matrices are derived here from closed forms, without calling
    ``relabel`` or ``cremer_pople``. B/E carry a vertical mirror through
    opposite atoms (even shifts, axis angle -pi*s/6); TB carries a C2
    axis through opposite atoms (even shifts, same axis, 180-degree
    rotation); H carries a C2 axis through opposite bond midpoints (odd
    shifts). Every form of each family must match at least one shift
    exactly (1e-9 scale, far from any science tolerance).
    """
    cases = (
        ("B", _vertical_mirror, (0, 2, 4)),
        ("E", _vertical_mirror, (0, 2, 4)),
        ("TB", _inplane_c2, (0, 2, 4)),
        ("H", _inplane_c2, (1, 3, 5)),
    )
    for family, matrix, shifts in cases:
        forms = [f for f in canonical_forms(6) if f.family == family]
        assert forms
        for form in forms:
            xyz = cp_to_coords(form.cp_target)
            matched = False
            for shift in shifts:
                beta = -math.pi * shift / 6.0
                moved = xyz @ matrix(beta)
                if float(np.max(np.abs(_reversed_about(xyz, shift) - moved))) < 1e-9:
                    matched = True
                    break
            assert matched, (family, form.index)


def test_envelope_five_atom_coplanarity() -> None:
    """Best five atoms of each E form lie on a plane to 1e-3 A.

    The residual (measured 2.44e-04 A) comes from the authoritative
    one-decimal Boeyens value theta=54.7 deg: at the exact
    arccos(1/sqrt(3)) it vanishes to 1e-16. This documents the source;
    it neither changes the authoritative table nor touches the 15-degree
    science thresholds owned by R3/R4.
    """
    for form in canonical_forms(6):
        if form.family != "E":
            continue
        xyz = cp_to_coords(form.cp_target)
        best = min(_best_plane_residual(np.delete(xyz, m, axis=0)) for m in range(6))
        assert best < 1e-3, (form.index, best)


def _best_plane_residual(pts: np.ndarray) -> float:
    centered = np.asarray(pts, dtype=float) - np.asarray(pts, dtype=float).mean(axis=0)
    _, _, vectors = np.linalg.svd(centered)
    return float(np.max(np.abs(centered @ vectors[-1])))


def test_canonical_form_immutability() -> None:
    form: CanonicalForm = canonical_forms(6)[0]
    with pytest.raises(AttributeError):
        form.family = "X"  # type: ignore[misc]
