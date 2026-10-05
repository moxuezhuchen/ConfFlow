#!/usr/bin/env python3

"""ConfGen ring puckering coordinates (FIX-1R R1, pure math, no callers).

Cremer-Pople coordinates and canonical regular forms for 4/5/6-membered
rings. This module is standalone: stdlib + NumPy only, no ``confflow``
imports, no template I/O.

Authority and conventions (must not be "reverse-fitted" from geometry):

- CP target tables are authoritative (PLAN R1 + S0.3 + Boeyens regular
  values, V18). Geometry is only ever a test object: table -> construct
  geometry -> back-compute CP -> must recover the table entry.
- Forward convention (verbatim): ``atoms[0]`` is ``j=0``;
  ``R' = sum c_j sin(2 pi j / n)``, ``R'' = sum c_j cos(2 pi j / n)``
  (``c_j`` = demeaned coords), ``normal = R' x R''`` normalized,
  ``z_j = c_j . normal``; ``q2 cos phi = sqrt(2/n) sum z_j cos(4 pi j / n)``,
  ``q2 sin phi = -sqrt(2/n) sum z_j sin(4 pi j / n)`` (strict minus sign),
  ``q3 = sqrt(1/n) sum z_j (-1)^j`` (n even). Inverse:
  ``z_j = sqrt(2/n) q2 cos(phi + 4 pi j / n) + sqrt(1/n) q3 (-1)^j``.
- Construction convention (fixed by root math evidence
  ``/tmp/fix1r-r1-math-order-root/evidence.json``): :func:`cp_to_coords`
  uses ``winding=-1`` (clockwise, ``angle_j = -2 pi j / n``),
  ``height = +z_j`` (never negated; negation is the theta-flip/phi+180
  twin), ``normal = R' x R''``, ``atoms[0] = j0``. The opposite winding
  recovers the theta-flipped / phi+180 twin. Do not conflate this with
  relabel covariance.
- Relabel combination order (ROOT-ORDER-DECISION, explicit previously
  undefined order; single-step PLAN formulas unchanged): first cyclically
  left-shift by ``shift % n``, then reverse about the new anchor as
  ``[a0, a(n-1), ..., a1]``. CP transforms apply strictly in the same
  order: first apply the shift formula ``shift`` times, then apply the
  reverse formula once. For n=6 one shift is
  ``theta' = 180 - theta, phi' = phi + 120``; reverse is
  ``theta' = 180 - theta, phi' = 180 - phi``. For n=5 one shift is
  ``phi' = phi + 144``; reverse is ``phi' = 180 - phi``. For n=4 an odd
  shift flips the butterfly sign, and reversal flips it as well (the
  reversed traversal flips the R'xR'' normal); P (sign 0) is invariant.
- Theta sources: Boeyens regular values quoted to one decimal as in
  PLAN S0.3: E ``54.7`` / ``125.3`` deg (``arccos(1/sqrt(3))`` ~ 54.7356
  deg north), H ``50.8`` / ``129.2`` deg (Boeyens half-chair regular
  value), B/TB equatorial ``90`` deg, C polar ``0`` / ``180`` deg.
  Amplitudes (``Q6 = 0.6``, ``q5 = 0.4``, ``q4 = 0.3`` Angstrom at
  ``rbar = 1.45``) are conventional test amplitudes, not physical claims;
  only directions/phases are authoritative.
- ``zero_torsion_bonds`` labels use 0-based atom indices (``"2-3"`` means
  atoms 2 and 3 in 0-based order), matching the root math evidence file.
  Each entry is the middle bond ``((j+1)%n, (j+2)%n)`` of a torsion
  ``tau_j = dihedral(j, j+1, j+2, j+3)`` with ``|tau| < 3`` deg on the
  ideal geometry built from the authoritative CP target at rbar 1.45.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

__all__ = [
    "CanonicalForm",
    "CPCoords",
    "canonical_forms",
    "cp_distance",
    "cp_to_coords",
    "cremer_pople",
    "relabel",
]

#: Conventional test amplitudes (Angstrom). Not physical claims; only the
#: CP directions/phases in the tables below are authoritative.
Q6_DEFAULT = 0.6
Q5_DEFAULT = 0.4
Q4_DEFAULT = 0.3
RBAR_DEFAULT = 1.45

#: Torsion magnitude below which a ring bond counts as a zero-torsion bond.
ZERO_TORSION_DEG = 3.0

#: Failure threshold quoted by PLAN R1 (kept here for documentation only;
#: tests compare against 0.5 deg explicitly).
CP_TOL_DEG = 0.5

#: Degenerate-normal threshold (squared norm of R' x R'').
_NORMAL_NORM_MIN = 1e-20

#: q2 amplitude below which phi is declared undefined (chair pole / flat).
_Q2_PHI_DEFINED_MIN = 1e-9

#: Q amplitude below which theta/phi are both declared undefined (flat).
_Q_FLAT_MIN = 1e-12


def _norm360(angle: float) -> float:
    """Wrap degrees to [0, 360)."""
    wrapped = float(angle) % 360.0
    if wrapped < 0.0:
        wrapped += 360.0
    return wrapped


def _ang_diff_deg(first: float, second: float) -> float:
    """Absolute circular distance between two degree angles."""
    diff = abs(_norm360(first) - _norm360(second)) % 360.0
    return float(min(diff, 360.0 - diff))


@dataclass(frozen=True, slots=True)
class CPCoords:
    """Cremer-Pople coordinates for one ring size.

    Attributes
    ----------
    n:
        Ring size (4, 5, or 6).
    q:
        Puckering amplitude in Angstrom: ``Q`` for n=6, ``q2`` for n=5,
        butterfly ``q`` for n=4.
    theta:
        Polar angle in degrees, n=6 only (0/180 = chair poles).
    phi:
        Phase in degrees, n=5/n=6 only. Set to 0.0 when undefined
        (chair pole or flat input) for determinism.
    sign:
        Butterfly sign, n=4 only: +1 (B+), -1 (B-), 0 (planar P).
    """

    n: int
    q: float
    theta: float
    phi: float
    sign: int = 0


@dataclass(frozen=True, slots=True)
class CanonicalForm:
    """One canonical regular ring form.

    Attributes
    ----------
    n:
        Ring size.
    family:
        ``"P"`` / ``"B+"`` / ``"B-"`` (n=4), ``"E"`` / ``"T"`` (n=5),
        ``"C"`` / ``"B"`` / ``"TB"`` / ``"E"`` / ``"H"`` (n=6).
    index:
        Position within the family (see :func:`canonical_forms`).
    cp_target:
        Authoritative CP target (defines the form, never fitted).
    zero_torsion_bonds:
        Middle bonds with ``|tau| < 3`` deg on the ideal geometry built
        from ``cp_target`` at rbar 1.45; 0-based ``"a-b"`` labels.
        Computed from the inverse CP construction, never hand-filled
        and never read from Cartesian templates.
    """

    n: int
    family: str
    index: int
    cp_target: CPCoords
    zero_torsion_bonds: tuple[str, ...]


# ---------------------------------------------------------------------------
# Internal forward/inverse math (exactly the PLAN formulas)
# ---------------------------------------------------------------------------


def _check_coords(coords: np.ndarray, n: int) -> np.ndarray:
    arr = np.asarray(coords, dtype=float)
    if arr.ndim != 2 or arr.shape[1] != 3 or arr.shape[0] != n:
        raise ValueError(f"coords must be ({n},3), got {arr.shape}")
    if not np.all(np.isfinite(arr)):
        raise ValueError("coords contain NaN or inf")
    return arr


def _mean_centered(arr: np.ndarray) -> np.ndarray:
    return arr - arr.mean(axis=0)


def _ring_normal(centered: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    n = centered.shape[0]
    js = np.arange(n, dtype=float)
    s = np.sin(2.0 * np.pi * js / n)
    c = np.cos(2.0 * np.pi * js / n)
    rprime = s @ centered
    rdouble = c @ centered
    normal = np.cross(rprime, rdouble)
    norm = float(np.linalg.norm(normal))
    if norm * norm < _NORMAL_NORM_MIN or not np.isfinite(norm) or norm == 0.0:
        raise ValueError("degenerate ring frame: R' x R'' vanishes")
    return normal / norm, centered


def _forward_n6(z: np.ndarray) -> tuple[float, float, float]:
    f2 = float(np.sqrt(2.0 / 6.0))
    f1 = float(np.sqrt(1.0 / 6.0))
    js = np.arange(6, dtype=float)
    sc = float(np.sum(z * np.cos(4.0 * np.pi * js / 6.0)))
    ss = float(np.sum(z * np.sin(4.0 * np.pi * js / 6.0)))
    q2c = f2 * sc
    q2s = -f2 * ss  # strict minus sign; do not change
    q2 = float(np.hypot(q2c, q2s))
    alt = np.array([1.0 if j % 2 == 0 else -1.0 for j in range(6)])
    q3 = float(f1 * np.sum(z * alt))
    q = float(np.hypot(q2, q3))
    if q < _Q_FLAT_MIN:
        return 0.0, 0.0, 0.0
    theta = float(np.degrees(np.arctan2(q2, q3)))
    phi = 0.0 if q2 < _Q2_PHI_DEFINED_MIN else _norm360(float(np.degrees(np.arctan2(q2s, q2c))))
    return q, theta, phi


def _forward_n5(z: np.ndarray) -> tuple[float, float]:
    f = float(np.sqrt(2.0 / 5.0))
    js = np.arange(5, dtype=float)
    sc = float(np.sum(z * np.cos(4.0 * np.pi * js / 5.0)))
    ss = float(np.sum(z * np.sin(4.0 * np.pi * js / 5.0)))
    q2c = f * sc
    q2s = -f * ss  # strict minus sign; do not change
    amp = float(np.hypot(q2c, q2s))
    if amp < _Q_FLAT_MIN:
        return 0.0, 0.0
    phi = _norm360(float(np.degrees(np.arctan2(q2s, q2c))))
    return amp, phi


def _z_from_cp_n6(q: float, theta_deg: float, phi_deg: float) -> np.ndarray:
    theta = float(np.radians(theta_deg))
    phi = float(np.radians(phi_deg))
    q2 = float(q) * float(np.sin(theta))
    q3 = float(q) * float(np.cos(theta))
    f2 = float(np.sqrt(2.0 / 6.0))
    f1 = float(np.sqrt(1.0 / 6.0))
    js = np.arange(6, dtype=float)
    alt = np.array([1.0 if j % 2 == 0 else -1.0 for j in range(6)])
    return f2 * q2 * np.cos(phi + 4.0 * np.pi * js / 6.0) + f1 * q3 * alt


def _z_from_cp_n5(amp: float, phi_deg: float) -> np.ndarray:
    phi = float(np.radians(phi_deg))
    f = float(np.sqrt(2.0 / 5.0))
    js = np.arange(5, dtype=float)
    return f * float(amp) * np.cos(phi + 4.0 * np.pi * js / 5.0)


def _z_from_cp_n4(q: float, sign: int) -> np.ndarray:
    if sign == 0:
        return np.zeros(4)
    amp = abs(float(q)) / 2.0
    s = 1.0 if sign > 0 else -1.0
    return np.array([s * amp * (1.0 if j % 2 == 0 else -1.0) for j in range(4)])


def _ideal_xyz(z: np.ndarray, rbar: float, winding: int = -1) -> np.ndarray:
    n = z.shape[0]
    if float(rbar) <= 0.0 or not np.isfinite(float(rbar)):
        raise ValueError(f"rbar must be positive finite, got {rbar}")
    radius = float(rbar) / (2.0 * float(np.sin(np.pi / n)))
    js = np.arange(n, dtype=float)
    angles = float(winding) * 2.0 * np.pi * js / n
    xyz = np.column_stack(
        (radius * np.cos(angles), radius * np.sin(angles), np.asarray(z, dtype=float))
    )
    return xyz


def _dihedral_deg(p0: np.ndarray, p1: np.ndarray, p2: np.ndarray, p3: np.ndarray) -> float:
    b0 = np.asarray(p1, dtype=float) - np.asarray(p0, dtype=float)
    v1 = np.asarray(p2, dtype=float) - np.asarray(p1, dtype=float)
    v2 = np.asarray(p3, dtype=float) - np.asarray(p2, dtype=float)
    n0 = np.cross(b0, v1)
    n1 = np.cross(v1, v2)
    axis = float(np.linalg.norm(v1))
    if axis < 1e-12:
        raise ValueError("degenerate central bond in torsion")
    unit = v1 / axis
    y = float(np.dot(np.cross(n0, n1), unit))
    x = float(np.dot(n0, n1))
    if x == 0.0 and y == 0.0:
        raise ValueError("degenerate torsion (collinear outer bonds)")
    ang = float(np.degrees(np.arctan2(y, x)))
    wrapped = ang % 360.0
    if wrapped > 180.0:
        wrapped -= 360.0
    if wrapped <= -180.0:
        wrapped += 360.0
    return wrapped


def _torsions_of(xyz: np.ndarray) -> np.ndarray:
    n = xyz.shape[0]
    out = np.empty(n)
    for j in range(n):
        out[j] = _dihedral_deg(xyz[j % n], xyz[(j + 1) % n], xyz[(j + 2) % n], xyz[(j + 3) % n])
    return out


def _zero_bonds_from_xyz(xyz: np.ndarray) -> tuple[str, ...]:
    n = xyz.shape[0]
    tors = _torsions_of(np.asarray(xyz, dtype=float))
    bonds: list[str] = []
    for j in range(n):
        if abs(float(tors[j])) < ZERO_TORSION_DEG:
            a = (j + 1) % n
            b = (j + 2) % n
            bonds.append(f"{a}-{b}")
    return tuple(bonds)


# ---------------------------------------------------------------------------
# Authoritative tables first, geometry second (V18)
# ---------------------------------------------------------------------------


def _table_n6() -> list[tuple[str, int, float, float]]:
    rows: list[tuple[str, int, float, float]] = []
    rows.append(("C", 0, 0.0, 0.0))
    rows.append(("C", 1, 180.0, 0.0))
    for k in range(6):
        rows.append(("B", k, 90.0, 60.0 * k))
    for k in range(6):
        rows.append(("TB", k, 90.0, 30.0 + 60.0 * k))
    for k in range(6):
        rows.append(("E", k, 54.7, 60.0 * k))
    for k in range(6):
        rows.append(("E", 6 + k, 125.3, 60.0 * k))
    for k in range(6):
        rows.append(("H", k, 50.8, 30.0 + 60.0 * k))
    for k in range(6):
        rows.append(("H", 6 + k, 129.2, 30.0 + 60.0 * k))
    return rows


def _table_n5() -> list[tuple[str, int, float]]:
    rows: list[tuple[str, int, float]] = []
    for k in range(10):
        rows.append(("E", k, 36.0 * k))
    for k in range(10):
        rows.append(("T", k, 18.0 + 36.0 * k))
    return rows


def _table_n4() -> list[tuple[str, int, float, int]]:
    return [("P", 0, 0.0, 0), ("B+", 0, Q4_DEFAULT, 1), ("B-", 0, Q4_DEFAULT, -1)]


_forms_cache: dict[int, tuple[CanonicalForm, ...]] = {}


def canonical_forms(n: int) -> tuple[CanonicalForm, ...]:
    """Return the canonical regular forms for ring size n.

    n=4 gives P/B+/B- (3), n=5 gives 10 E + 10 T (20), n=6 gives
    2 C + 6 B + 6 TB + 12 E + 12 H (38). CP targets come from the
    authoritative tables; ``zero_torsion_bonds`` is computed from the
    inverse-CP ideal geometry at rbar 1.45, never hand-filled.
    """
    if n not in (4, 5, 6):
        raise ValueError(f"canonical_forms supports n=4/5/6, got {n}")
    cached = _forms_cache.get(n)
    if cached is not None:
        return cached
    forms: list[CanonicalForm] = []
    if n == 6:
        for family, index, theta, phi in _table_n6():
            target = CPCoords(n=6, q=Q6_DEFAULT, theta=theta, phi=_norm360(phi))
            xyz = _ideal_xyz(_z_from_cp_n6(target.q, target.theta, target.phi), RBAR_DEFAULT, -1)
            forms.append(
                CanonicalForm(
                    n=6,
                    family=family,
                    index=index,
                    cp_target=target,
                    zero_torsion_bonds=_zero_bonds_from_xyz(xyz),
                )
            )
    elif n == 5:
        for family, index, phi in _table_n5():
            target = CPCoords(n=5, q=Q5_DEFAULT, theta=0.0, phi=_norm360(phi))
            xyz = _ideal_xyz(_z_from_cp_n5(target.q, target.phi), RBAR_DEFAULT, -1)
            forms.append(
                CanonicalForm(
                    n=5,
                    family=family,
                    index=index,
                    cp_target=target,
                    zero_torsion_bonds=_zero_bonds_from_xyz(xyz),
                )
            )
    else:
        for family, index, q, sign in _table_n4():
            target = CPCoords(n=4, q=q, theta=0.0, phi=0.0, sign=sign)
            xyz = _ideal_xyz(_z_from_cp_n4(target.q, target.sign), RBAR_DEFAULT, -1)
            forms.append(
                CanonicalForm(
                    n=4,
                    family=family,
                    index=index,
                    cp_target=target,
                    zero_torsion_bonds=_zero_bonds_from_xyz(xyz),
                )
            )
    result = tuple(forms)
    _forms_cache[n] = result
    return result


def cremer_pople(coords: np.ndarray) -> CPCoords:
    """Compute Cremer-Pople coordinates with the fixed PLAN convention.

    ``atoms[0]`` is ``j=0``; normal is ``R' x R''`` normalized; the
    ``q2 sin`` branch carries the strict minus sign. Raises ValueError on
    degenerate input (wrong shape, NaN/inf, vanishing normal, non-positive
    or non-finite sizes are rejected by the caller via shape).
    """
    arr = np.asarray(coords, dtype=float)
    if arr.ndim != 2 or arr.shape[1] != 3 or arr.shape[0] not in (4, 5, 6):
        raise ValueError(f"coords must be (n,3) with n=4/5/6, got {arr.shape}")
    if not np.all(np.isfinite(arr)):
        raise ValueError("coords contain NaN or inf")
    n = arr.shape[0]
    centered = _mean_centered(arr)
    if not np.all(np.isfinite(centered)):
        raise ValueError("coords contain NaN or inf")
    normal, _ = _ring_normal(centered)
    z = centered @ normal
    if n == 6:
        q, theta, phi = _forward_n6(np.asarray(z, dtype=float))
        return CPCoords(n=6, q=q, theta=theta, phi=phi)
    if n == 5:
        amp, phi = _forward_n5(np.asarray(z, dtype=float))
        return CPCoords(n=5, q=amp, theta=0.0, phi=phi)
    s = float(np.sum(np.asarray(z, dtype=float) * np.array([1.0, -1.0, 1.0, -1.0])))
    q = abs(s) / 2.0
    if q < _Q_FLAT_MIN:
        return CPCoords(n=4, q=0.0, theta=0.0, phi=0.0, sign=0)
    return CPCoords(n=4, q=q, theta=0.0, phi=0.0, sign=1 if s > 0 else -1)


def cp_to_coords(cp: CPCoords, n: int | None = None, rbar: float = RBAR_DEFAULT) -> np.ndarray:
    """Build ideal geometry from a CP target (test / R3 start only).

    Uses winding=-1 (clockwise planar projection), height=+z from the
    inverse CP formula, ``atoms[0]`` as ``j=0``. The opposite winding
    gives the theta-flipped / phi+180 twin, never this point.
    """
    size = cp.n if n is None else int(n)
    if size not in (4, 5, 6):
        raise ValueError(f"cp_to_coords supports n=4/5/6, got {size}")
    if size != cp.n:
        raise ValueError(f"cp.n={cp.n} does not match n={size}")
    if not np.isfinite(float(rbar)) or float(rbar) <= 0.0:
        raise ValueError(f"rbar must be positive finite, got {rbar}")
    if size == 6:
        z = _z_from_cp_n6(cp.q, cp.theta, cp.phi)
    elif size == 5:
        z = _z_from_cp_n5(cp.q, cp.phi)
    else:
        z = _z_from_cp_n4(cp.q, cp.sign)
    return _ideal_xyz(np.asarray(z, dtype=float), float(rbar), -1)


def cp_distance(a: CPCoords, b: CPCoords) -> float:
    """CP distance in degrees (n=5/6) or signed-q gap (n=4).

    n=6 uses the spherical angle between unit vectors
    ``(sinT cosP, sinT sinP, cosT)`` via
    ``atan2(norm(cross), dot)`` for stability at all separations
    (near-coincident through antipodal). At a true pole ``sinT`` is
    exactly 0 so phi naturally has no effect; no polar shortcut is
    used. n=5 uses the circular phase distance. n=4 uses
    ``|sign_a q_a - sign_b q_b|`` in Angstrom.
    """
    if a.n != b.n:
        raise ValueError(f"cp_distance needs equal n, got {a.n} vs {b.n}")
    if a.n == 6:
        ta = float(np.radians(a.theta))
        tb = float(np.radians(b.theta))
        pa = float(np.radians(a.phi))
        pb = float(np.radians(b.phi))
        va = np.array([np.sin(ta) * np.cos(pa), np.sin(ta) * np.sin(pa), np.cos(ta)])
        vb = np.array([np.sin(tb) * np.cos(pb), np.sin(tb) * np.sin(pb), np.cos(tb)])
        cross_norm = float(np.linalg.norm(np.cross(va, vb)))
        dot = float(np.dot(va, vb))
        return float(np.degrees(np.arctan2(cross_norm, dot)))
    if a.n == 5:
        return _ang_diff_deg(a.phi, b.phi)
    sa = 0.0 if a.sign == 0 else (1.0 if a.sign > 0 else -1.0) * abs(float(a.q))
    sb = 0.0 if b.sign == 0 else (1.0 if b.sign > 0 else -1.0) * abs(float(b.q))
    return abs(sa - sb)


def _predict_n6(theta: float, phi: float, shift: int, reverse: bool) -> tuple[float, float]:
    t = float(theta)
    p = _norm360(phi)
    for _ in range(int(shift) % 6):
        t, p = 180.0 - t, _norm360(p + 120.0)
    if reverse:
        t, p = 180.0 - t, _norm360(180.0 - p)
    return t, _norm360(p)


def _predict_n5(phi: float, shift: int, reverse: bool) -> float:
    p = _norm360(float(phi) + int(shift) * 144.0)
    if reverse:
        p = _norm360(180.0 - p)
    return _norm360(p)


def relabel(form: CanonicalForm, shift: int = 0, reverse: bool = False) -> CanonicalForm:
    """Map a canonical form across traversal relabelling (family fixed).

    Combination order is fixed once: apply the cyclic shift formula
    ``shift % n`` times first, then apply the reverse formula once about
    the new anchor. Single-step formulas are the PLAN/ROOT ones quoted in
    the module docstring. The result is the same-family canonical form
    nearest the predicted CP target (exact up to float noise).
    """
    n = form.n
    s = int(shift) % n
    rev = bool(reverse)
    if s == 0 and not rev:
        return form
    pool = [c for c in canonical_forms(n) if c.family == form.family]
    if n == 6:
        pred_t, pred_p = _predict_n6(form.cp_target.theta, form.cp_target.phi, s, rev)
        pred = CPCoords(n=6, q=form.cp_target.q, theta=pred_t, phi=pred_p)
        best: CanonicalForm | None = None
        best_d = float("inf")
        for cand in pool:
            d = cp_distance(pred, cand.cp_target)
            if d < best_d:
                best_d = d
                best = cand
        assert best is not None
        return best
    if n == 5:
        pred_p = _predict_n5(form.cp_target.phi, s, rev)
        pred = CPCoords(n=5, q=form.cp_target.q, theta=0.0, phi=pred_p)
        best = min(pool, key=lambda c: cp_distance(pred, c.cp_target))
        return best
    if form.cp_target.sign == 0:
        return form
    # Physical reversal flips the R'xR'' normal, hence the measured z
    # signs, on top of the index permutation; an odd shift flips as well.
    sign = form.cp_target.sign
    if rev:
        sign = -sign
    if s % 2 == 1:
        sign = -sign
    if sign == form.cp_target.sign:
        return form
    for cand in pool:
        if cand.cp_target.sign == sign:
            return cand
    for cand in canonical_forms(4):
        if cand.cp_target.sign == sign:
            return cand
    raise ValueError("no n=4 relabel target found")
