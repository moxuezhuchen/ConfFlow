#!/usr/bin/env python3

"""ConfGen v3 ring-lane geometric realization (R6).

R6 deleted the Cartesian template path (``realize_single_system`` /
``realize_rings`` and their Kabsch/template imports). Retained: scope
validation (``validate_ring_system`` against ``constants`` alias names,
same errors), normalized specs, and the R3 CP-constrained solver
(``realize_cp_target`` plus ``_R3_*`` helpers, unchanged).

Fail-closed scope (all explicit, never silent):

- ring sizes other than 4/5/6 (including macrocycles): unsupported;
- traversal pairs that are not covalently bonded: unsupported;
- fused/bridged rings (extra intra-ring covalent bonds short-circuiting the
  traversal), overlapping systems, spiro sharing: unsupported;
- acyclic single bonds BETWEEN ring systems (biphenyl-like links): supported
  with an explicit linking-bond length audit (link_bond_atol);
- chelate content (metal atom inside a ring): unsupported;
- multi-anchor substituent fragments: unsupported;
- degenerate local frames or non-finite input: numerical failure;
- clash, ring-bond drift beyond tolerance, stereo parity flip: geometry failure;
- atoms covered by coordination binding sites: unsupported (coordination is
  owned by another lane; the ring stage must not silently damage it).

Dependencies: stdlib + NumPy only.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

import numpy as np

from .geometry import (
    DegenerateFrameError,
    clash_pairs,
    local_frame,
    partition_substituents,
    rotation_between_frames,
    topological_distances,
)

__all__ = [
    "METAL_ELEMENTS",
    "RingGeometryFailure",
    "RingNumericalFailure",
    "RingSpec",
    "RingTolerances",
    "RingUnsupported",
    "SUPPORTED_RING_SIZES",
    "parse_ring_specs",
    "realize_cp_target",
    "validate_ring_system",
]

#: Isolated ring sizes in declared scope.
SUPPORTED_RING_SIZES = (4, 5, 6)

#: Elements treated as metals for chelate detection (unsupported scope).
METAL_ELEMENTS = frozenset(
    {
        "LI",
        "BE",
        "NA",
        "MG",
        "AL",
        "K",
        "CA",
        "SC",
        "TI",
        "V",
        "CR",
        "MN",
        "FE",
        "CO",
        "NI",
        "CU",
        "ZN",
        "GA",
        "RB",
        "SR",
        "Y",
        "ZR",
        "NB",
        "MO",
        "TC",
        "RU",
        "RH",
        "PD",
        "AG",
        "CD",
        "IN",
        "SN",
        "SB",
        "CS",
        "BA",
        "LA",
        "CE",
        "PR",
        "ND",
        "PM",
        "SM",
        "EU",
        "GD",
        "TB",
        "DY",
        "HO",
        "ER",
        "TM",
        "YB",
        "LU",
        "HF",
        "TA",
        "W",
        "RE",
        "OS",
        "IR",
        "PT",
        "AU",
        "HG",
        "TL",
        "PB",
        "BI",
        "PO",
        "FR",
        "RA",
        "AC",
        "TH",
        "PA",
        "U",
    }
)


class RingUnsupported(ValueError):
    """Explicitly unsupported requested ring generation (fail closed)."""


class RingNumericalFailure(ValueError):
    """Numerical failure: non-finite input or degenerate frame/solver state."""


class RingGeometryFailure(ValueError):
    """Geometric failure: clash, bond drift, or stereo parity violation."""


@dataclass(frozen=True, slots=True)
class RingTolerances:
    """Stage-specific realistic geometric tolerances for the ring solver."""

    substituent_bond_atol: float = 1e-6
    clash_threshold: float = 0.65
    frame_det_min: float = 1e-8
    link_bond_atol: float = 0.15


@dataclass(frozen=True, slots=True)
class RingSpec:
    """One normalized ring axis entry."""

    id: str
    atoms: tuple[int, ...]
    treatment: str = "enumerate"
    templates: tuple[str, ...] = ()
    forms: tuple[str, ...] = ()


def parse_ring_specs(raw_rings: object) -> tuple[RingSpec, ...]:
    """Normalize raw ring axis entries; raise ``RingUnsupported`` when invalid."""
    if raw_rings is None:
        return ()
    if not isinstance(raw_rings, (list, tuple)):
        raise RingUnsupported("rings spec must be a list")
    specs: list[RingSpec] = []
    seen: set[str] = set()
    for entry in raw_rings:
        if not isinstance(entry, dict):
            raise RingUnsupported("ring entry must be a mapping")
        ring_id = entry.get("id")
        atoms = entry.get("atoms")
        if not isinstance(ring_id, str) or not ring_id:
            raise RingUnsupported("ring entry needs a non-empty string id")
        if ring_id in seen:
            raise RingUnsupported(f"duplicate ring id {ring_id!r}")
        seen.add(ring_id)
        if (
            not isinstance(atoms, (list, tuple))
            or len(atoms) == 0
            or any(isinstance(v, bool) or not isinstance(v, int) or v < 0 for v in atoms)
            or len(set(atoms)) != len(atoms)
        ):
            raise RingUnsupported(f"ring {ring_id!r} needs distinct 0-based atom indices")
        treatment = entry.get("treatment", "enumerate")
        if treatment not in ("enumerate", "preserve_input"):
            raise RingUnsupported(f"ring {ring_id!r} has unsupported treatment {treatment!r}")
        templates = entry.get("templates", ())
        if templates is None:
            templates = ()
        if not isinstance(templates, (list, tuple)) or any(
            not isinstance(name, str) for name in templates
        ):
            raise RingUnsupported(f"ring {ring_id!r} templates must be names")
        forms = entry.get("forms", ())
        if forms is None:
            forms = ()
        if not isinstance(forms, (list, tuple)) or any(not isinstance(name, str) for name in forms):
            raise RingUnsupported(f"ring {ring_id!r} forms must be names")
        if templates and forms:
            raise RingUnsupported(f"ring {ring_id!r} declares both templates and forms")
        specs.append(
            RingSpec(
                id=ring_id,
                atoms=tuple(int(v) for v in atoms),
                treatment=treatment,
                templates=tuple(templates),
                forms=tuple(forms),
            )
        )
    specs.sort(key=lambda item: item.id)
    return tuple(specs)


def _as_adjacency(adjacency: object, n_atoms: int) -> list[list[int]]:
    """Normalize neighbour lists; raise numerical failure when malformed."""
    if not isinstance(adjacency, (list, tuple)) or len(adjacency) != n_atoms:
        raise RingNumericalFailure("adjacency length must match atom count")
    normalized: list[list[int]] = []
    for index, neighbours in enumerate(adjacency):
        if not isinstance(neighbours, (list, tuple)):
            raise RingNumericalFailure(f"adjacency[{index}] must be a neighbour list")
        row: list[int] = []
        for value in neighbours:
            if isinstance(value, bool) or not isinstance(value, int):
                raise RingNumericalFailure(f"adjacency[{index}] holds a non-integer")
            if value < 0 or value >= n_atoms or value == index:
                raise RingNumericalFailure(f"adjacency[{index}] holds an invalid index")
            row.append(value)
        normalized.append(sorted(set(row)))
    return normalized


def validate_ring_system(
    spec: RingSpec,
    adjacency: list[list[int]],
    elements: list[str],
    other_ring_atoms: frozenset[int],
    coordination_atoms: frozenset[int] = frozenset(),
) -> None:
    """Fail closed on every out-of-scope ring request; return ``None`` when clean."""
    n_atoms = len(adjacency)
    size = len(spec.atoms)
    if size not in SUPPORTED_RING_SIZES:
        raise RingUnsupported(f"unsupported_ring_size:{size}")
    if any(atom >= n_atoms for atom in spec.atoms):
        raise RingUnsupported("ring atom index out of range")
    ring_set = frozenset(spec.atoms)
    if ring_set & other_ring_atoms:
        raise RingUnsupported("overlapping_systems")
    if ring_set & coordination_atoms:
        raise RingUnsupported("coordination_overlap")
    for atom in spec.atoms:
        if elements[atom].upper() in METAL_ELEMENTS:
            raise RingUnsupported("chelate")
    for position, atom in enumerate(spec.atoms):
        nxt = spec.atoms[(position + 1) % size]
        if nxt not in adjacency[atom]:
            raise RingUnsupported("nonbonded_traversal")
    for position, atom in enumerate(spec.atoms):
        expected = {spec.atoms[(position - 1) % size], spec.atoms[(position + 1) % size]}
        ring_neighbours = set(adjacency[atom]) & ring_set
        if ring_neighbours != expected:
            raise RingUnsupported("fused_or_bridged")
    # Bonds from ring atoms to OTHER ring systems are ordinary acyclic
    # single-bond links (biphenyl-like), not fused/bridged scope violations.
    # The linking bond lengths are audited at realization (link_bond_atol);
    # only non-ring fragments touching two or more blocked atoms are
    # rejected as multi-anchor coupling (see partition_substituents).
    # R6: alias names validated against constants table (same values/errors
    # as old registry, no geometry import).
    if spec.templates:
        from .constants import TEMPLATES_BY_SIZE as _NAMES_BY_SIZE

        for name in spec.templates:
            found_size: int | None = None
            for _size, _names in _NAMES_BY_SIZE.items():
                if name in _names:
                    found_size = int(_size)
                    break
            if found_size is None:
                raise RingUnsupported(f"unknown_template:{name}")
            if found_size != size:
                raise RingUnsupported(f"template_size_mismatch:{name}")


def _signed_volume(a: np.ndarray, b: np.ndarray, c: np.ndarray, d: np.ndarray) -> float:
    return float(np.dot(b - a, np.cross(c - a, d - a)))


# ---------------------------------------------------------------------------
# R3: CP-constrained realization (frozen constants + realize_cp_target).
#
# Frozen by R3 card (Q13/V17/root decisions). None of these constants is a
# user-tunable spec key; they live here (not in ConfgenTolerances fields,
# planner projection, schema, or contract) so serialization is unchanged.
# Only phase_defined_q_min is mirrored as a ClassVar on ConfgenTolerances
# for single-source documentation (not a dataclass field).
# ---------------------------------------------------------------------------

#: Dimensionless Q/rbar gate below which theta/phi are numerically
#: undefined (Q13/V17; numeric-domain gate, not a physical criterion).
_R3_PHASE_DEFINED_Q_MIN = 0.05

#: Weighted least-squares sigmas (residual = deviation / sigma).
_R3_BOND_SIGMA = 0.01  # angstrom, ring bonds anchored to INPUT values
_R3_ONETHREE_RIGID_SIGMA = 0.02  # angstrom, middle atom in R2 sp2_centers
_R3_ONETHREE_FREE_SIGMA = 0.15  # angstrom, all other ring 1-3 pairs
_R3_CP_SIGMA = 0.02  # dimensionless CP-coordinate caliber (see _r3_cp_residuals)
_R3_PINNED_TORSION_SIGMA_DEG = 7.5  # deg, toward nearest 0/180, pinned bonds only
_R3_CENTROID_SIGMA = 0.05  # angstrom, ring centroid stays put

#: Audit gates (fixed order; any failure -> RingGeometryFailure).
_R3_BOND_DRIFT_MAX = 0.02  # angstrom vs INPUT (tightened from legacy 0.08)
_R3_ANGLE_RIGID_MAX_DEG = 3.0  # interior-angle drift, center in sp2_centers
_R3_ANGLE_FREE_MAX_DEG = 12.0  # interior-angle drift, all other centers
_R3_PLANARITY_MAX_DEG = 15.0  # pinned-bond torsion deviation from 0/180
_R3_CP_REACHED_MAX_DEG = 15.0  # n5/n6 only; n4 uses sign+gate (never degrees)
_R3_CLASH_THRESHOLD = 0.65  # unchanged clash_pairs caliber (K0 decides later)
_R3_SUBSTITUENT_BOND_ATOL = 1e-6  # unchanged legacy caliber

#: Solver settings (frozen; never relaxed to fit tests).
_R3_SOLVER_MAX_NFEV = 2000
_R3_SOLVER_FTOL = 1e-8
_R3_SOLVER_XTOL = 1e-8
_R3_SOLVER_GTOL = 1e-8

#: Fixed audit order (puckering_amplitude always precedes cp_reached).
_R3_AUDIT_ORDER = (
    "ring_bond_drift",
    "ring_angle_drift",
    "conjugation_planarity",
    "puckering_amplitude",
    "cp_reached",
    "local_orientation",
    "clash",
)


def _propagate_substituents_rigid(
    old_positions: np.ndarray,
    new_positions: np.ndarray,
    ring_order: list[int],
    substituents: list,
) -> None:
    """Rigidly propagate substituents through per-anchor local frames.

    Verbatim relocation of the legacy ``realize_single_system`` inline
    block (old ``realization.py:366-383``): per anchor, ``local_frame``
    of (prev, center, next) old vs new, ``rotation_between_frames`` step,
    then ``new[member] = new[anchor] + step @ (old[member] - old[anchor])``.
    Only variable names / argument packaging changed; math order is
    unchanged. The legacy inline block is kept as-is (coexistence, zero
    regression risk); this helper is used solely by ``realize_cp_target``.
    """
    size = len(ring_order)
    for position, atom in enumerate(ring_order):
        prev_old = old_positions[ring_order[(position - 1) % size]]
        next_old = old_positions[ring_order[(position + 1) % size]]
        prev_new = new_positions[ring_order[(position - 1) % size]]
        next_new = new_positions[ring_order[(position + 1) % size]]
        try:
            frame_old = local_frame(prev_old, old_positions[atom], next_old)
            frame_new = local_frame(prev_new, new_positions[atom], next_new)
            step = rotation_between_frames(frame_old, frame_new)
        except (DegenerateFrameError, ValueError) as exc:
            raise RingNumericalFailure("degenerate_frame") from exc
        for sub in substituents:
            if sub.anchor != atom:
                continue
            for member in sub.members:
                new_positions[member] = new_positions[atom] + step @ (
                    old_positions[member] - old_positions[atom]
                )


def _r3_as_graph(adjacency: object, n_atoms: int) -> list[list[int]]:
    """Normalize adjacency (sequence rows or mapping) to sorted neighbour rows."""
    if isinstance(adjacency, dict) or (
        hasattr(adjacency, "items") and not isinstance(adjacency, (list, tuple))
    ):
        rows: list[list[int]] = []
        try:
            items = sorted(adjacency.items())  # type: ignore[union-attr]
        except TypeError as exc:
            raise RingNumericalFailure(f"bad adjacency mapping: {exc}") from exc
        by_key = {int(k): list(v) for k, v in items}
        for index in range(n_atoms):
            neighbours = by_key.get(index, [])
            row: list[int] = []
            for value in neighbours:
                if isinstance(value, bool) or not isinstance(value, int):
                    raise RingNumericalFailure(f"adjacency[{index}] holds a non-integer")
                if value < 0 or value >= n_atoms or value == index:
                    raise RingNumericalFailure(f"adjacency[{index}] holds an invalid index")
                row.append(value)
            rows.append(sorted(set(row)))
        return rows
    return _as_adjacency(adjacency, n_atoms)


def _r3_interior_angles(ring_xyz: np.ndarray) -> list[float]:
    """Interior angle at each ring atom: prev-center-next (degrees)."""
    xyz = np.asarray(ring_xyz, dtype=float)
    n = xyz.shape[0]
    out: list[float] = []
    for k in range(n):
        prev = xyz[(k - 1) % n] - xyz[k]
        nxt = xyz[(k + 1) % n] - xyz[k]
        n1 = float(np.linalg.norm(prev))
        n2 = float(np.linalg.norm(nxt))
        if n1 < 1e-12 or n2 < 1e-12:
            raise RingNumericalFailure("degenerate_ring_angle")
        cosang = max(-1.0, min(1.0, float(np.dot(prev, nxt) / (n1 * n2))))
        out.append(float(math.degrees(math.acos(cosang))))
    return out


def _r3_pinned_torsion_list(
    ring_xyz: np.ndarray, ring_order: list[int], pinned: list[tuple[int, int]]
) -> list[tuple[tuple[int, int], float]]:
    """Deviation of each pinned ring bond from nearest 0/180 (degrees).

    Torsion atoms are (prev_a, a, b, next_b) along the ring traversal.
    """
    from .geometry import dihedral_deg

    xyz = np.asarray(ring_xyz, dtype=float)
    pos = {atom: k for k, atom in enumerate(ring_order)}
    n = len(ring_order)
    out: list[tuple[tuple[int, int], float]] = []
    for pair in pinned:
        a, b = int(pair[0]), int(pair[1])
        if a not in pos or b not in pos:
            raise RingUnsupported(f"pinned_bond_not_on_ring:{a}-{b}")
        ia, ib = pos[a], pos[b]
        if (ia + 1) % n == ib:
            p_atom, q_atom = ring_order[(ia - 1) % n], ring_order[(ib + 1) % n]
            order = (p_atom, a, b, q_atom)
        elif (ib + 1) % n == ia:
            p_atom, q_atom = ring_order[(ib - 1) % n], ring_order[(ia + 1) % n]
            order = (p_atom, b, a, q_atom)
        else:
            raise RingUnsupported(f"pinned_bond_not_on_ring:{a}-{b}")
        idx = [ring_order.index(x) for x in order]
        try:
            tor = float(dihedral_deg(xyz[idx[0]], xyz[idx[1]], xyz[idx[2]], xyz[idx[3]]))
        except (DegenerateFrameError, ValueError) as exc:
            raise RingNumericalFailure("degenerate_pinned_torsion") from exc
        dev = min(abs(tor), 180.0 - abs(tor))
        out.append(((a, b), float(dev)))
    return out


def _r3_cp_residuals(
    ring_xyz: np.ndarray, form: Any, rbar: float
) -> tuple[np.ndarray, float, float, bool]:
    """CP-direction residuals (sigma _R3_CP_SIGMA); Q is never fixed.

    Returns (residuals, q, q_over_rbar, is_planar_target).
    n6 direction vector is (sinT cosP, sinT sinP, cosT); n5 is
    (cosP, sinP); residual is the vector difference / sigma. P targets
    (family "P") use q/rbar / sigma instead and close the weak floor.
    Non-P targets add the weak floor max(0, 0.05-q/rbar)/sigma. n4
    non-P adds a sign-consistency penalty (0 when signs agree, 2.0
    otherwise) and never fixes the target amplitude.
    """
    from .puckering import cremer_pople

    actual = cremer_pople(np.asarray(ring_xyz, dtype=float))
    q = float(actual.q)
    qor = q / float(rbar) if float(rbar) > 0 else float("inf")
    family = str(
        getattr(getattr(form, "cp_target", form), "family", None) or getattr(form, "family", "")
    )
    is_planar = family == "P"
    if is_planar:
        return np.asarray([qor / _R3_CP_SIGMA], dtype=float), q, qor, True
    n = int(getattr(form, "n", actual.n))
    parts: list[float] = []
    if n == 6:
        target = getattr(form, "cp_target", None)
        ta = math.radians(float(actual.theta))
        pa = math.radians(float(actual.phi))
        tt = math.radians(float(target.theta))  # type: ignore[union-attr]
        pt = math.radians(float(target.phi))  # type: ignore[union-attr]
        va = np.array([math.sin(ta) * math.cos(pa), math.sin(ta) * math.sin(pa), math.cos(ta)])
        vt = np.array([math.sin(tt) * math.cos(pt), math.sin(tt) * math.sin(pt), math.cos(tt)])
        parts.extend(((va - vt) / _R3_CP_SIGMA).tolist())
    elif n == 5:
        target = getattr(form, "cp_target", None)
        pa = math.radians(float(actual.phi))
        pt = math.radians(float(target.phi))  # type: ignore[union-attr]
        va = np.array([math.cos(pa), math.sin(pa)])
        vt = np.array([math.cos(pt), math.sin(pt)])
        parts.extend(((va - vt) / _R3_CP_SIGMA).tolist())
    elif n == 4:
        target = getattr(form, "cp_target", None)
        want = int(getattr(target, "sign", 0))  # type: ignore[union-attr]
        got = int(actual.sign)
        parts.append(0.0 if (got == want and got != 0) else 2.0)
    else:
        raise RingUnsupported(f"unsupported_ring_size:{n}")
    floor = max(0.0, _R3_PHASE_DEFINED_Q_MIN - qor) / _R3_CP_SIGMA
    parts.append(float(floor))
    return np.asarray(parts, dtype=float), q, qor, False


def _r3_mirror_ring(ring_xyz: np.ndarray) -> np.ndarray:
    """Mirror ring atoms across their mean plane (second start seed)."""
    xyz = np.asarray(ring_xyz, dtype=float)
    center = xyz.mean(axis=0)
    rel = xyz - center
    try:
        from .puckering import _ring_normal

        normal, _ = _ring_normal(rel)
        normal = np.asarray(normal, dtype=float)
    except (ValueError, TypeError):
        _, _, vt = np.linalg.svd(rel, full_matrices=False)
        normal = np.asarray(vt[-1], dtype=float)
    normal = normal / float(np.linalg.norm(normal))
    return np.asarray(center + rel - 2.0 * np.outer(rel @ normal, normal), dtype=float)


def _r3_residual_vector(
    x: np.ndarray,
    *,
    input_bonds: list[float],
    input_onethree: list[float],
    onethree_sigma: list[float],
    input_centroid: np.ndarray,
    ring_order: list[int],
    pinned: list[tuple[int, int]],
    form: Any,
    rbar: float,
) -> np.ndarray:
    """Full weighted residual vector for the ring-atom variables (3n)."""
    ring = np.asarray(x, dtype=float).reshape(-1, 3)
    n = ring.shape[0]
    bonds = [float(np.linalg.norm(ring[(k + 1) % n] - ring[k])) for k in range(n)]
    one3 = [float(np.linalg.norm(ring[(k + 2) % n] - ring[k])) for k in range(n)]
    parts: list[float] = []
    parts.extend(((np.asarray(bonds) - np.asarray(input_bonds)) / _R3_BOND_SIGMA).tolist())
    parts.extend(
        ((np.asarray(one3) - np.asarray(input_onethree)) / np.asarray(onethree_sigma)).tolist()
    )
    cp_res, _, _, _ = _r3_cp_residuals(ring, form, rbar)
    parts.extend([float(v) for v in np.asarray(cp_res, dtype=float).tolist()])
    if pinned:
        devs = _r3_pinned_torsion_list(ring, ring_order, pinned)
        parts.extend([dev / _R3_PINNED_TORSION_SIGMA_DEG for _, dev in devs])
    centroid = ring.mean(axis=0)
    parts.extend(((centroid - input_centroid) / _R3_CENTROID_SIGMA).tolist())
    arr = np.asarray(parts, dtype=float)
    if not np.all(np.isfinite(arr)):
        return np.full_like(arr, 1e6)
    return arr


def realize_cp_target(
    coords: Any,
    elements: Any,
    adjacency: Any,
    spec: Any,
    form: Any,
    *,
    rigid_units: Any,
    tolerances: Any = None,
    additional_starts: Any = (),
) -> tuple[np.ndarray, dict[str, object], dict[str, object]]:
    """Realize one ring toward a canonical CP target (R3, single-ring scope).

    Variables are the ring-atom coordinates only (3n). Ring bonds and
    ring 1-3 distances are anchored to INPUT values (never ideal values);
    CP direction follows ``form.cp_target``; Q is never fixed to a value
    (only a weak floor below phase_defined_q_min for non-P targets).
    Substituents propagate via the relocated legacy rigid-follow helper.
    Multi-start order: input geometry, mean-plane mirror, then
    ``additional_starts``; first audit-passing start wins, otherwise the
    minimal-residual start's audit is reported. Failures raise
    ``RingGeometryFailure``/``RingNumericalFailure``/``RingUnsupported``
    with the full audit attached as ``exc.audit``; the reason's first
    token is the failing audit-item name.
    """
    from .puckering import cp_distance, cremer_pople

    try:
        from ..tolerances import ConfgenTolerances

        _CT: Any = ConfgenTolerances
    except Exception:
        _CT = None

    xyz_in = np.asarray(coords, dtype=float)  # type: ignore[arg-type]
    if xyz_in.ndim != 2 or xyz_in.shape[1] != 3 or xyz_in.shape[0] == 0:
        raise RingNumericalFailure("bad coords shape")
    if not np.all(np.isfinite(xyz_in)):
        raise RingNumericalFailure("nonfinite input coordinates")
    n_atoms = xyz_in.shape[0]
    try:
        els = list(elements)  # type: ignore[arg-type]
    except TypeError as exc:
        raise RingNumericalFailure(f"bad elements: {exc}") from exc
    if len(els) != n_atoms:
        raise RingNumericalFailure("elements length must match atom count")
    graph = _r3_as_graph(adjacency, n_atoms)

    ring_order = [int(v) for v in list(getattr(spec, "atoms", None) or [])]
    if len(ring_order) not in SUPPORTED_RING_SIZES:
        raise RingUnsupported(f"unsupported_ring_size:{len(ring_order)}")
    if len(set(ring_order)) != len(ring_order) or any(a < 0 or a >= n_atoms for a in ring_order):
        raise RingUnsupported("ring atom index out of range")
    size = len(ring_order)
    if int(getattr(form, "n", size)) != size:
        raise RingUnsupported("form size mismatch")
    for k, atom in enumerate(ring_order):
        nxt = ring_order[(k + 1) % size]
        if nxt not in graph[atom]:
            raise RingUnsupported("nonbonded_traversal")
    ring_set = frozenset(ring_order)

    for attr in ("units", "local_orientation_locks", "sp2_centers"):
        if not hasattr(rigid_units, attr):
            raise RingNumericalFailure(f"rigid_units missing {attr}")
    sp2_set = frozenset(int(v) for v in rigid_units.sp2_centers)
    locks = list(rigid_units.local_orientation_locks)
    pinned: list[tuple[int, int]] = []
    for unit in list(rigid_units.units):
        for pair in list(getattr(unit, "pinned_bonds", ())):
            a, b = int(pair[0]), int(pair[1])
            pinned.append((a, b) if a < b else (b, a))
    pinned = sorted(set(pinned))

    clash_threshold = _R3_CLASH_THRESHOLD
    if tolerances is not None and _CT is not None and isinstance(tolerances, _CT):
        clash_threshold = float(tolerances.clash_threshold)

    old_positions = np.asarray(xyz_in, dtype=float).copy()
    input_ring = old_positions[ring_order]
    input_bonds = [
        float(np.linalg.norm(input_ring[(k + 1) % size] - input_ring[k])) for k in range(size)
    ]
    rbar = float(sum(input_bonds) / len(input_bonds))
    if not math.isfinite(rbar) or rbar <= 0:
        raise RingNumericalFailure("degenerate input ring bonds")
    input_onethree = [
        float(np.linalg.norm(input_ring[(k + 2) % size] - input_ring[k])) for k in range(size)
    ]
    input_centroid = input_ring.mean(axis=0)
    input_angles = _r3_interior_angles(input_ring)
    onethree_sigma = [
        (
            _R3_ONETHREE_RIGID_SIGMA
            if ring_order[(k + 1) % size] in sp2_set
            else _R3_ONETHREE_FREE_SIGMA
        )
        for k in range(size)
    ]

    substituents, multi_anchor = partition_substituents(n_atoms, graph, ring_set, blocked=ring_set)
    if multi_anchor:
        raise RingUnsupported(f"multi_anchor:{multi_anchor[0]['atom']}")

    extra: list[np.ndarray] = []
    if additional_starts is None:
        pass
    else:
        try:
            extra_items = list(additional_starts)  # type: ignore[arg-type]
        except TypeError as exc:
            raise RingNumericalFailure(f"bad additional_starts: {exc}") from exc
        for item in extra_items:
            arr = np.asarray(item, dtype=float)
            if arr.shape != (size, 3) or not np.all(np.isfinite(arr)):
                raise RingNumericalFailure("bad additional start shape")
            extra.append(arr.copy())
    starts = [input_ring.copy(), _r3_mirror_ring(input_ring)] + extra

    topo_matrix = topological_distances(graph)

    def _solve(
        start: np.ndarray,
    ) -> tuple[np.ndarray, float, dict[str, object], int, str]:
        from scipy.optimize import least_squares

        def fun(x: np.ndarray) -> np.ndarray:
            return _r3_residual_vector(
                x,
                input_bonds=input_bonds,
                input_onethree=input_onethree,
                onethree_sigma=onethree_sigma,
                input_centroid=input_centroid,
                ring_order=ring_order,
                pinned=pinned,
                form=form,
                rbar=rbar,
            )

        try:
            sol = least_squares(
                fun,
                np.asarray(start, dtype=float).reshape(-1),
                method="trf",
                max_nfev=_R3_SOLVER_MAX_NFEV,
                ftol=_R3_SOLVER_FTOL,
                xtol=_R3_SOLVER_XTOL,
                gtol=_R3_SOLVER_GTOL,
            )
        except Exception as exc:
            raise RingNumericalFailure(f"solver exception: {type(exc).__name__}") from exc
        ring_new = np.asarray(sol.x, dtype=float).reshape(size, 3)
        if not np.all(np.isfinite(ring_new)):
            raise RingNumericalFailure("solver produced non-finite coordinates")
        return (
            ring_new,
            float(sol.cost),
            {"nfev": int(sol.nfev), "success": bool(sol.success), "message": str(sol.message)},
            int(sol.nfev),
            str(sol.message),
        )

    def _audit(
        ring_new: np.ndarray, full_new: np.ndarray, solver_info: dict[str, object], start_index: int
    ) -> tuple[dict[str, object], str | None]:
        # Sequential gating (v2 root fix): items are evaluated in
        # _R3_AUDIT_ORDER; after the first failure, all later fixed items
        # are recorded as not_evaluated (passed False), never as passed.
        # In particular an amplitude failure leaves cp_reached as
        # not_evaluated/non-passed. Local orientation no longer skips
        # zero/non-finite volumes: any locked zero/non-finite volume or
        # sign mismatch (input-vs-lock or output-vs-lock) is a failure.
        # No .3 output magnitude threshold is added; the old-volume
        # non-zero condition comes from the lock itself.
        audit: dict[str, object] = {
            "ring_id": str(getattr(spec, "id", "")),
            "form": f"{getattr(form, 'family', '')}:{getattr(form, 'index', '')}",
            "n": size,
            "start_index": start_index,
            "solver": dict(solver_info),
        }
        failures: dict[str, str] = {}
        first_failed: str | None = None

        def _skipped(name: str) -> None:
            audit[name] = {
                "passed": False,
                "status": "not_evaluated",
                "skipped_after": first_failed,
            }

        new_bonds = [
            float(np.linalg.norm(ring_new[(k + 1) % size] - ring_new[k])) for k in range(size)
        ]
        drift = max(abs(nv - ov) for nv, ov in zip(new_bonds, input_bonds))
        audit["ring_bond_drift"] = {
            "value": float(drift),
            "limit": _R3_BOND_DRIFT_MAX,
            "passed": bool(drift <= _R3_BOND_DRIFT_MAX),
        }
        if drift > _R3_BOND_DRIFT_MAX:
            failures["ring_bond_drift"] = f"ring_bond_drift:{drift:.4f}>{_R3_BOND_DRIFT_MAX:.2f}"
            first_failed = "ring_bond_drift"
        if first_failed is not None:
            _skipped("ring_angle_drift")
            new_angles: list[float] = []
            worst_rigid = 0.0
            worst_free = 0.0
            angle_fail = False
        else:
            new_angles = _r3_interior_angles(ring_new)
            worst_rigid = 0.0
            worst_free = 0.0
            for k, atom in enumerate(ring_order):
                dev = abs(new_angles[k] - input_angles[k])
                if atom in sp2_set:
                    worst_rigid = max(worst_rigid, dev)
                else:
                    worst_free = max(worst_free, dev)
            angle_fail = (
                worst_rigid > _R3_ANGLE_RIGID_MAX_DEG or worst_free > _R3_ANGLE_FREE_MAX_DEG
            )
            audit["ring_angle_drift"] = {
                "rigid_value": float(worst_rigid),
                "rigid_limit": _R3_ANGLE_RIGID_MAX_DEG,
                "free_value": float(worst_free),
                "free_limit": _R3_ANGLE_FREE_MAX_DEG,
                "passed": bool(not angle_fail),
            }
            if angle_fail:
                failures["ring_angle_drift"] = (
                    f"ring_angle_drift:rigid{worst_rigid:.2f}>"
                    f"{_R3_ANGLE_RIGID_MAX_DEG:.1f} free{worst_free:.2f}>"
                    f"{_R3_ANGLE_FREE_MAX_DEG:.1f}"
                )
                first_failed = "ring_angle_drift"
        if first_failed is not None:
            _skipped("conjugation_planarity")
            worst_pin = 0.0
        else:
            devs = _r3_pinned_torsion_list(ring_new, ring_order, pinned)
            worst_pin = max((dev for _, dev in devs), default=0.0)
            audit["conjugation_planarity"] = {
                "value": float(worst_pin),
                "limit": _R3_PLANARITY_MAX_DEG,
                "per_bond": [{"bond": list(b), "dev": round(float(d), 4)} for b, d in devs],
                "passed": bool(worst_pin <= _R3_PLANARITY_MAX_DEG),
            }
            if worst_pin > _R3_PLANARITY_MAX_DEG:
                failures["conjugation_planarity"] = (
                    f"conjugation_planarity:{worst_pin:.2f}>{_R3_PLANARITY_MAX_DEG:.1f}"
                )
                first_failed = "conjugation_planarity"
        actual_cp = cremer_pople(ring_new)
        q_now = float(actual_cp.q)
        qor_now = q_now / rbar
        is_planar_target = str(getattr(form, "family", "")) == "P"
        if first_failed is not None:
            _skipped("puckering_amplitude")
            amp_pass = False
        else:
            amp_pass = True if is_planar_target else bool(qor_now >= _R3_PHASE_DEFINED_Q_MIN)
            audit["puckering_amplitude"] = {
                "q": float(q_now),
                "rbar": float(rbar),
                "q_over_rbar": float(qor_now),
                "limit": _R3_PHASE_DEFINED_Q_MIN,
                "exempt": bool(is_planar_target),
                "passed": bool(amp_pass),
            }
            if not amp_pass:
                failures["puckering_amplitude"] = (
                    f"puckering_amplitude:{qor_now:.4f}<{_R3_PHASE_DEFINED_Q_MIN:.2f}"
                )
                first_failed = "puckering_amplitude"
        target_cp = getattr(form, "cp_target", None)
        if first_failed is not None:
            _skipped("cp_reached")
        elif is_planar_target:
            cp_ok = bool(qor_now <= _R3_PHASE_DEFINED_Q_MIN)
            audit["cp_reached"] = {
                "q_over_rbar": float(qor_now),
                "limit": _R3_PHASE_DEFINED_Q_MIN,
                "passed": bool(cp_ok),
            }
            if not cp_ok:
                failures["cp_reached"] = (
                    f"cp_reached:planar{qor_now:.4f}>{_R3_PHASE_DEFINED_Q_MIN:.2f}"
                )
                first_failed = "cp_reached"
        elif size == 4:
            want_sign = int(getattr(target_cp, "sign", 0))
            got_sign = int(actual_cp.sign)
            cp_ok = bool(got_sign == want_sign and got_sign != 0)
            audit["cp_reached"] = {
                "want_sign": int(want_sign),
                "got_sign": int(got_sign),
                "q_over_rbar": float(qor_now),
                "passed": bool(cp_ok),
            }
            if not cp_ok:
                failures["cp_reached"] = f"cp_reached:sign{got_sign}!={want_sign}"
                first_failed = "cp_reached"
        else:
            dist = float(cp_distance(actual_cp, target_cp))
            cp_ok = bool(dist <= _R3_CP_REACHED_MAX_DEG)
            audit["cp_reached"] = {
                "distance": float(dist),
                "limit": _R3_CP_REACHED_MAX_DEG,
                "passed": bool(cp_ok),
            }
            if not cp_ok:
                failures["cp_reached"] = f"cp_reached:{dist:.2f}>{_R3_CP_REACHED_MAX_DEG:.1f}"
                first_failed = "cp_reached"
        if first_failed is not None:
            _skipped("local_orientation")
        else:
            checked = 0
            flipped: list[int] = []
            degenerate: list[int] = []
            input_inconsistent: list[int] = []
            checked_pairs: list[list[int]] = []
            for lock in locks:
                c, p, nn, s = int(lock.center), int(lock.prev), int(lock.next), int(lock.subst)
                want_sign = int(getattr(lock, "sign", 0))
                try:
                    old_vol = _signed_volume(
                        old_positions[c], old_positions[p], old_positions[nn], old_positions[s]
                    )
                    new_vol = _signed_volume(full_new[c], full_new[p], full_new[nn], full_new[s])
                except Exception:
                    if c not in degenerate:
                        degenerate.append(c)
                    continue
                if not math.isfinite(old_vol) or not math.isfinite(new_vol):
                    if c not in degenerate:
                        degenerate.append(c)
                    continue
                if old_vol == 0.0 or new_vol == 0.0:
                    if c not in degenerate:
                        degenerate.append(c)
                    continue
                old_sign = 1 if old_vol > 0.0 else -1
                if old_sign != want_sign:
                    if c not in input_inconsistent:
                        input_inconsistent.append(c)
                    continue
                new_sign = 1 if new_vol > 0.0 else -1
                checked += 1
                checked_pairs.append([c, s])
                if new_sign != want_sign:
                    flipped.append(c)
            local_ok = not flipped and not degenerate and not input_inconsistent
            audit["local_orientation"] = {
                "checked": checked,
                "locks_checked": checked_pairs,
                "flipped": sorted(flipped),
                "degenerate": sorted(degenerate),
                "input_inconsistent": sorted(input_inconsistent),
                "passed": bool(local_ok),
            }
            if not local_ok:
                if degenerate:
                    failures["local_orientation"] = (
                        f"local_orientation:degenerate_center{degenerate[0]}"
                    )
                elif input_inconsistent:
                    failures["local_orientation"] = (
                        f"local_orientation:input_mismatch_center{input_inconsistent[0]}"
                    )
                else:
                    failures["local_orientation"] = f"local_orientation:center{flipped[0]}"
                first_failed = "local_orientation"
        if first_failed is not None:
            _skipped("clash")
        else:
            clashes, fallback = clash_pairs(full_new, els, topo_matrix, threshold=clash_threshold)
            audit["clash"] = {
                "count": len(clashes),
                "pairs": [
                    {"i": i, "j": j, "dist": round(d, 6), "limit": round(lim, 6)}
                    for i, j, d, lim in clashes[:8]
                ],
                "fallback_radii": bool(fallback),
                "passed": bool(not clashes),
            }
            if clashes:
                worst = clashes[0]
                failures["clash"] = f"clash:{worst[0]}-{worst[1]}:{worst[2]:.3f}"
                first_failed = "clash"
        if first_failed is not None:
            audit["substituent_bond_drift"] = {
                "passed": False,
                "status": "not_evaluated",
                "skipped_after": first_failed,
            }
        else:
            worst_sub = 0.0
            for sub in substituents:
                old_len = float(np.linalg.norm(old_positions[sub.root] - old_positions[sub.anchor]))
                new_len = float(np.linalg.norm(full_new[sub.root] - full_new[sub.anchor]))
                worst_sub = max(worst_sub, abs(new_len - old_len))
            audit["substituent_bond_drift"] = {
                "value": float(worst_sub),
                "limit": _R3_SUBSTITUENT_BOND_ATOL,
                "passed": bool(worst_sub <= _R3_SUBSTITUENT_BOND_ATOL),
            }
            if worst_sub > _R3_SUBSTITUENT_BOND_ATOL:
                failures["substituent_bond_drift"] = f"substituent_bond_drift:{worst_sub:.3e}"
                first_failed = "substituent_bond_drift"
        first = next((failures[name] for name in _R3_AUDIT_ORDER if name in failures), None)
        if first is None and "substituent_bond_drift" in failures:
            first = failures["substituent_bond_drift"]
        return audit, first

    attempts: list[dict[str, Any]] = []
    for index, start in enumerate(starts):
        try:
            ring_new, cost, solver_info, _, _ = _solve(start)
        except RingNumericalFailure as exc:
            audit0: dict[str, Any] = {
                "ring_id": str(getattr(spec, "id", "")),
                "form": f"{getattr(form, 'family', '')}:{getattr(form, 'index', '')}",
                "n": size,
                "start_index": index,
                "solver": {"success": False, "error": str(exc)},
            }
            entry = {
                "index": index,
                "cost": float("inf"),
                "audit": audit0,
                "error": exc,
                "ring": None,
                "full": None,
                "reason": str(exc),
            }
            attempts.append(entry)
            continue
        full_new = old_positions.copy()
        full_new[ring_order] = ring_new
        try:
            _propagate_substituents_rigid(old_positions, full_new, ring_order, substituents)
        except RingNumericalFailure as exc:
            audit0 = {
                "ring_id": str(getattr(spec, "id", "")),
                "form": f"{getattr(form, 'family', '')}:{getattr(form, 'index', '')}",
                "n": size,
                "start_index": index,
                "solver": dict(solver_info),
            }
            attempts.append(
                {
                    "index": index,
                    "cost": float(cost),
                    "audit": audit0,
                    "error": exc,
                    "ring": ring_new,
                    "full": None,
                    "reason": str(exc),
                }
            )
            continue
        audit, reason = _audit(ring_new, full_new, {"cost": float(cost), **solver_info}, index)
        audit["residual_cost"] = float(cost)
        if reason is None:
            state = {
                "ring_id": str(getattr(spec, "id", "")),
                "family": str(getattr(form, "family", "")),
                "index": int(getattr(form, "index", 0)),
                "n": size,
                "rbar": float(rbar),
                "start_index": index,
            }
            return full_new, state, audit
        attempts.append(
            {
                "index": index,
                "cost": float(cost),
                "audit": audit,
                "error": None,
                "ring": ring_new,
                "full": full_new,
                "reason": reason,
            }
        )
    finite = [a for a in attempts if math.isfinite(float(a["cost"])) and a["full"] is not None]
    if finite:
        best = min(finite, key=lambda a: float(a["cost"]))
        audit = dict(best["audit"])
        reason = str(best["reason"])
        exc_out = RingGeometryFailure(reason)
        exc_out.audit = audit  # type: ignore[attr-defined]
        raise exc_out
    first = attempts[0] if attempts else None
    if first is not None and first["error"] is not None:
        err = first["error"]
        try:
            err.audit = dict(first["audit"])  # type: ignore[union-attr]
        except Exception:
            pass
        raise err
    audit = dict(first["audit"]) if first is not None else {}
    reason = str(first["reason"]) if first is not None else "solver_failed"
    exc_out = RingGeometryFailure(reason)
    exc_out.audit = audit  # type: ignore[attr-defined]
    raise exc_out
