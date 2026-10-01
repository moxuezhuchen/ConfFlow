#!/usr/bin/env python3
"""Geometric realization of coordination targets by rigid-fragment motion.

Pure science: stdlib + NumPy + SciPy only.  No energy evaluation.

Model
-----
The typed graph is cut at COORDINATION edges; connected components over
covalent/forming/breaking edges are rigid fragments.  Each fragment moves as
a rigid body (proper rotation about its input centroid plus translation, 6
parameters); the metal-bearing fragment is the fixed lab anchor.  A
constrained least-squares solve trades donor-to-target attraction against
inter-fragment clash penalties and cross-fragment reaction-edge restraints.

Every intra-fragment bond length, bond angle, and stereo parity is preserved
*by construction* (proper rigid motion) and verified post hoc to numerical
precision.  Monodentate fragments translate/rotate one donor into place;
multidentate fragments (for example a bidentate N-C-C-N ligand) place all
their donors simultaneously, succeeding only when the target vertices are
geometrically consistent with the rigid ligand — otherwise the target
honestly fails.

Terminal statuses: ``REALIZED`` requires donor errors, clash, reaction-edge
checks, stereo preservation, *and* re-perception of the generated geometry
to the exact target class.  An unchanged parent is never labeled realized
without that re-perception gate.  ``DRIFTED`` means the solver converged
away from the target (evidence, never success); ``UNRESOLVED`` means the
solver failed or the budget ran out.  Nothing hardcodes fixture outputs, and
existing SCINE frames never substitute for generated structures.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

import numpy as np

from ..graph import COVALENT_RADII, EdgeType, TypedGraph
from .shapes import ShapeTemplate, kabsch_proper_rotation

__all__ = [
    "RealizationResult",
    "TargetRecord",
    "FeasibilityReport",
    "FragmentPlan",
    "COVALENT_RADII",
    "FLEX_BACKEND_NAME",
    "partition_fragments",
    "realize_target",
    "realize_flexible",
    "double_bond_audit",
    "proof_contradiction_alert",
    "feasibility_spike",
    "signed_volume",
    "stereo_guard",
]

BACKEND_NAME = "scipy-trf-rigid-fragment-ls"


@dataclass(frozen=True, slots=True)
class TargetRecord:
    """One realization target: stage-local state plus stable ordinal."""

    target_id: str
    state_value: dict[str, str]
    placement: tuple[int, ...]
    ordinal: int
    provenance: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-compatible record."""
        return {
            "target_id": self.target_id,
            "state_value": dict(self.state_value),
            "placement": list(self.placement),
            "ordinal": self.ordinal,
            "provenance": {k: v for k, v in self.provenance.items()},
        }


@dataclass(frozen=True, slots=True)
class RealizationResult:
    """Terminal realization outcome for one target."""

    target_id: str
    structure: tuple[tuple[float, float, float], ...] | None
    status: str
    reason: str
    backend: str
    evidence: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-compatible record."""
        return {
            "target_id": self.target_id,
            "structure": (
                [list(point) for point in self.structure] if self.structure is not None else None
            ),
            "status": self.status,
            "reason": self.reason,
            "backend": self.backend,
            "evidence": dict(self.evidence),
        }


@dataclass(frozen=True, slots=True)
class FragmentPlan:
    """Rigid-fragment partition of the typed graph."""

    fragments: tuple[tuple[int, ...], ...]
    movable: tuple[int, ...]
    anchor: int
    donor_fragment: dict[int, int]

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-compatible record."""
        return {
            "fragments": [list(frag) for frag in self.fragments],
            "movable": list(self.movable),
            "anchor": self.anchor,
            "donor_fragment": dict(self.donor_fragment),
        }


def partition_fragments(graph: TypedGraph, metal: int) -> FragmentPlan:
    """Cut COORDINATION edges; components over the chemical skeleton.

    The skeleton keeps COVALENT, FORMING, and BREAKING edges: reaction
    relations stay inside fragments and are preserved exactly by rigid
    motion.  The fragment containing *metal* is the fixed anchor.
    """
    adjacency: dict[int, set[int]] = {i: set() for i in range(graph.natoms)}
    for edge in graph.edges:
        if edge.type is EdgeType.COORDINATION:
            continue
        adjacency[edge.a].add(edge.b)
        adjacency[edge.b].add(edge.a)
    seen: set[int] = set()
    fragments: list[tuple[int, ...]] = []
    for start in range(graph.natoms):
        if start in seen:
            continue
        stack = [start]
        component: set[int] = set()
        while stack:
            node = stack.pop()
            if node in seen:
                continue
            seen.add(node)
            component.add(node)
            stack.extend(adjacency[node] - seen)
        fragments.append(tuple(sorted(component)))
    # Deterministic order: anchor first, then by smallest atom index.
    anchor = next(i for i, frag in enumerate(fragments) if metal in frag)
    order = [anchor] + [i for i in range(len(fragments)) if i != anchor]
    ordered = tuple(fragments[i] for i in order)
    anchor_new = 0
    member_of: dict[int, int] = {}
    for index, frag in enumerate(ordered):
        for atom in frag:
            member_of[atom] = index
    return FragmentPlan(
        fragments=ordered,
        movable=tuple(i for i in range(len(ordered)) if i != anchor_new),
        anchor=anchor_new,
        donor_fragment={},
    )


def signed_volume(coords: np.ndarray, center: int, neighbors: Sequence[int]) -> float:
    """Return the signed volume of the tetrahedron around *center*."""
    selected = np.asarray([coords[n] for n in neighbors[:4]], dtype=float)
    mat = np.column_stack(
        [selected[1] - selected[0], selected[2] - selected[0], selected[3] - selected[0]]
    )
    return float(np.linalg.det(mat) / 6.0)


def stereo_guard(
    before: np.ndarray,
    after: np.ndarray,
    graph: TypedGraph,
    volume_tolerance: float = 1e-3,
) -> dict[str, Any]:
    """Detect signed-volume flips at 4-coordinated covalent centers."""
    flips: list[int] = []
    checked = 0
    for index in range(graph.natoms):
        neighbors = graph.neighbors(index, EdgeType.COVALENT)
        if len(neighbors) < 4:
            continue
        checked += 1
        pick = tuple(sorted(neighbors)[:4])
        before_v = signed_volume(before, index, pick)
        after_v = signed_volume(after, index, pick)
        if abs(before_v) < volume_tolerance or abs(after_v) < volume_tolerance:
            continue
        if (before_v > 0.0) != (after_v > 0.0):
            flips.append(index)
    return {"centers_checked": checked, "flips": flips, "preserved": not flips}


def _bond_angle_deg(coords: np.ndarray, center: int, first: int, second: int) -> float:
    """Return the bond angle first-center-second in degrees."""
    first_v = np.asarray(coords[first], dtype=float) - np.asarray(coords[center], dtype=float)
    second_v = np.asarray(coords[second], dtype=float) - np.asarray(coords[center], dtype=float)
    first_n = float(np.linalg.norm(first_v))
    second_n = float(np.linalg.norm(second_v))
    if first_n < 1e-12 or second_n < 1e-12:
        return float("nan")
    cosine = max(-1.0, min(1.0, float(first_v @ second_v) / (first_n * second_n)))
    return float(np.degrees(np.arccos(cosine)))


def _dihedral_cos(coords: np.ndarray, a: int, b: int, c: int, d: int) -> float | None:
    """Return cos(dihedral a-b-c-d), or None when degenerate."""
    array = np.asarray(coords, dtype=float)
    b0, b1, b2 = array[b] - array[a], array[c] - array[b], array[d] - array[c]
    n0 = np.cross(b0, b1)
    n1 = np.cross(b1, b2)
    denom = float(np.linalg.norm(n0) * np.linalg.norm(n1))
    if denom < 1e-12:
        return None
    return max(-1.0, min(1.0, float(n0 @ n1) / denom))


def _point_plane_distance(coords: np.ndarray, point: int, a: int, b: int, c: int) -> float | None:
    """Return the distance of *point* from the plane (a, b, c), or None."""
    array = np.asarray(coords, dtype=float)
    normal = np.cross(array[b] - array[a], array[c] - array[a])
    denom = float(np.linalg.norm(normal))
    if denom < 1e-12:
        return None
    return abs(float((array[point] - array[a]) @ normal) / denom)


def double_bond_audit(
    before: np.ndarray,
    after: np.ndarray,
    graph: TypedGraph,
    planarity_tol: float = 0.15,
    cos_tol: float = 0.10,
) -> dict[str, Any]:
    """Audit declared double-bond (bond_order 2) geometry preservation.

    For every covalent edge with declared ``bond_order == 2``, substituent
    planarity and pairwise cis/trans (dihedral-cosine) relationships must be
    preserved; a flip is a violation (Cartesian penalties alone cannot be
    trusted to hold double-bond state).  Bonds without declared order or
    without adjudicable substituents are skipped transparently and reported.
    """
    checked = 0
    skipped: list[str] = []
    violations: list[str] = []
    max_planarity = 0.0
    max_cos_drift = 0.0
    for edge in graph.edges:
        if edge.type is not EdgeType.COVALENT or edge.bond_order != 2:
            continue
        subs_a = [n for n in graph.neighbors(edge.a, EdgeType.COVALENT) if n != edge.b]
        subs_b = [n for n in graph.neighbors(edge.b, EdgeType.COVALENT) if n != edge.a]
        if not subs_a or not subs_b:
            skipped.append(f"{edge.a}-{edge.b}: no substituent pair")
            continue
        checked += 1
        # Planarity of the substituent set around the double bond.
        ref_plane = (edge.a, edge.b, subs_a[0])
        for sub in subs_a[1:] + subs_b:
            before_d = _point_plane_distance(before, sub, *ref_plane)
            after_d = _point_plane_distance(after, sub, *ref_plane)
            if before_d is None or after_d is None:
                continue
            max_planarity = max(max_planarity, after_d)
            if after_d - before_d > planarity_tol:
                violations.append(
                    f"double bond {edge.a}-{edge.b}: substituent {sub} leaves plane "
                    f"({before_d:.3f}A -> {after_d:.3f}A)"
                )
        # Cis/trans relationships via dihedral cosines.
        for sa in subs_a:
            for sb in subs_b:
                cos_before = _dihedral_cos(before, sa, edge.a, edge.b, sb)
                cos_after = _dihedral_cos(after, sa, edge.a, edge.b, sb)
                if cos_before is None or cos_after is None:
                    continue
                drift = abs(cos_after - cos_before)
                max_cos_drift = max(max_cos_drift, drift)
                if drift > cos_tol:
                    violations.append(
                        f"double bond {edge.a}-{edge.b}: cis/trans drift "
                        f"({cos_before:.3f} -> {cos_after:.3f})"
                    )
    return {
        "double_bonds_audited": checked,
        "skipped": skipped,
        "max_planarity_violation": max_planarity,
        "max_cos_drift": max_cos_drift,
        "violations": violations,
        "preserved": not violations,
    }


def _rodrigues_matrix(rvec: np.ndarray) -> np.ndarray:
    angle = float(np.linalg.norm(rvec))
    if angle < 1e-12:
        return np.eye(3)
    axis = rvec / angle
    kx, ky, kz = (float(v) for v in axis)
    skew = np.array([[0.0, -kz, ky], [kz, 0.0, -kx], [-ky, kx, 0.0]])
    return np.eye(3) + np.sin(angle) * skew + (1.0 - np.cos(angle)) * (skew @ skew)


def realize_target(
    coordinates: np.ndarray,
    graph: TypedGraph,
    metal: int,
    donors: Sequence[int],
    site_ids: Sequence[str],
    placement: Sequence[int],
    template: ShapeTemplate,
    target_id: str = "T00",
    perceive: Callable[[np.ndarray], Any] | None = None,
    realize_tol: float = 0.45,
    intra_bond_tol: float = 0.05,
    intra_angle_tol_deg: float = 3.0,
    reaction_tol: float = 0.25,
    clash_scale: float = 0.70,
    weight_target: float = 3.0,
    weight_clash: float = 2.0,
    weight_reaction: float = 4.0,
    max_nfev: int = 120,
) -> RealizationResult:
    """Realize one labeled placement by rigid-fragment motion.

    *perceive* maps generated coordinates to a canonical shape-class key;
    ``REALIZED`` additionally requires the re-perceived class to equal the
    target class, so an unchanged parent is never labeled realized without
    that gate.  Without a callback no ``REALIZED`` verdict is issued.
    """
    coords = np.asarray(coordinates, dtype=float)
    donor_list = [int(d) for d in donors]
    place = tuple(int(v) for v in placement)
    if len(donor_list) != template.coordination_number or len(place) != len(donor_list):
        raise ValueError("donor/placement count incompatible with template")
    plan = partition_fragments(graph, metal)
    member_of: dict[int, int] = {}
    for index, frag in enumerate(plan.fragments):
        for atom in frag:
            member_of[atom] = index
    donor_fragment = {d: member_of[d] for d in donor_list}
    if any(frag == plan.anchor for frag in donor_fragment.values()):
        return RealizationResult(
            target_id=target_id,
            structure=None,
            status="UNRESOLVED",
            reason="donor shares the metal anchor fragment; no motion model",
            backend=BACKEND_NAME,
            evidence={"attempts": 0, "fragment_plan": plan.to_dict()},
        )

    center = coords[metal]
    ref_lengths = np.array([np.linalg.norm(coords[d] - center) for d in donor_list])
    if bool(np.any(ref_lengths < 1e-9)):
        return RealizationResult(
            target_id=target_id,
            structure=None,
            status="UNRESOLVED",
            reason="degenerate input metal-donor distance",
            backend=BACKEND_NAME,
            evidence={"attempts": 0, "fragment_plan": plan.to_dict()},
        )
    # Rigid placement orientation: proper rotation mapping input donor unit
    # directions onto target vertex directions (bond lengths preserved by
    # construction through per-site reference lengths).
    verts = np.asarray(template.vertices, dtype=float)
    input_dirs = np.array([(coords[d] - center) / ref for d, ref in zip(donor_list, ref_lengths)])
    ideal_dirs = np.array(
        [verts[place[i]] / np.linalg.norm(verts[place[i]]) for i in range(len(donor_list))]
    )
    rotation = kabsch_proper_rotation(input_dirs, ideal_dirs)
    to_lab = rotation.T
    oriented = np.array([to_lab @ verts[place[i]] for i in range(len(donor_list))])
    oriented /= np.linalg.norm(oriented, axis=1, keepdims=True)
    targets = np.array([center + oriented[i] * ref_lengths[i] for i in range(len(donor_list))])
    initial_donor_error = float(
        np.max(np.linalg.norm(np.array([coords[d] for d in donor_list]) - targets, axis=1))
    )

    centroids = {i: coords[list(frag)].mean(axis=0) for i, frag in enumerate(plan.fragments)}
    movable = list(plan.movable)
    param_index = {frag: pos for pos, frag in enumerate(movable)}
    elements = graph.elements

    # Clash pairs: cross-fragment, metal excluded (metal-donor distances are
    # design variables, not clash candidates).
    frag_of = member_of
    clash_pairs: list[tuple[int, int, float]] = []
    for a in range(graph.natoms):
        if a == metal:
            continue
        for b in range(a + 1, graph.natoms):
            if b == metal or frag_of[a] == frag_of[b]:
                continue
            floor = clash_scale * (
                COVALENT_RADII.get(elements[a], 1.0) + COVALENT_RADII.get(elements[b], 1.0)
            )
            clash_pairs.append((a, b, floor))
    # Cross-fragment reaction relations (FORMING/BREAKING split by the cut).
    reaction_refs: list[tuple[int, int, float]] = []
    for edge in graph.edges:
        if edge.type not in (EdgeType.FORMING, EdgeType.BREAKING):
            continue
        if frag_of[edge.a] == frag_of[edge.b]:
            continue
        reaction_refs.append(
            (edge.a, edge.b, float(np.linalg.norm(coords[edge.a] - coords[edge.b])))
        )

    base = coords.copy()

    def _pose(vector: np.ndarray) -> np.ndarray:
        trial = base.copy()
        for frag in movable:
            block = vector[param_index[frag] * 6 : param_index[frag] * 6 + 6]
            rot = _rodrigues_matrix(block[:3])
            trial[list(plan.fragments[frag])] = (
                (base[list(plan.fragments[frag])] - centroids[frag]) @ rot.T
                + centroids[frag]
                + block[3:]
            )
        return trial

    def _residuals(vector: np.ndarray) -> np.ndarray:
        trial = _pose(vector)
        parts: list[np.ndarray] = [
            weight_target
            * np.array([trial[d] - targets[i] for i, d in enumerate(donor_list)]).reshape(-1)
        ]
        if clash_pairs:
            parts.append(
                weight_clash
                * np.array(
                    [
                        max(0.0, floor - float(np.linalg.norm(trial[a] - trial[b])))
                        for a, b, floor in clash_pairs
                    ]
                )
            )
        if reaction_refs:
            parts.append(
                weight_reaction
                * np.array(
                    [
                        (float(np.linalg.norm(trial[a] - trial[b])) - ref) / max(ref, 1e-9)
                        for a, b, ref in reaction_refs
                    ]
                )
            )
        return np.concatenate(parts)

    x0 = np.zeros(6 * len(movable))
    posed, solve_info = _solve_rigid_pose(
        _pose,
        _residuals,
        x0,
        max_nfev,
    )
    if posed is None:
        return RealizationResult(
            target_id=target_id,
            structure=None,
            status="UNRESOLVED",
            reason=solve_info["reason"],
            backend=BACKEND_NAME,
            evidence={
                "attempts": 1,
                "fragment_plan": plan.to_dict(),
                **solve_info["evidence"],
            },
        )
    realized = posed
    verdict = _audit_geometry(
        coords,
        realized,
        graph,
        donor_list,
        targets,
        place,
        template,
        target_id,
        perceive,
        frag_of,
        _full_clash_pairs(graph, clash_scale),
        _all_reaction_refs(coords, graph),
        realize_tol=realize_tol,
        intra_bond_tol=intra_bond_tol,
        intra_angle_tol_deg=intra_angle_tol_deg,
        reaction_tol=reaction_tol,
        backend_name=BACKEND_NAME,
        success_note="rigid-fragment solve within tolerance and re-perceived to target class",
        extra_evidence={
            "attempts": 1,
            "nfev": solve_info["evidence"].get("nfev"),
            "cost": solve_info["evidence"].get("cost"),
            "solver_converged": solve_info["evidence"].get("solver_converged"),
            "solver_message": solve_info["evidence"].get("solver_message"),
            "initial_donor_error": initial_donor_error,
            "fragment_plan": plan.to_dict(),
            "target_positions": [list(map(float, row)) for row in targets],
        },
    )
    return verdict


def _solve_rigid_pose(
    pose: Callable[[np.ndarray], np.ndarray],
    residuals: Callable[[np.ndarray], np.ndarray],
    x0: np.ndarray,
    max_nfev: int,
) -> tuple[np.ndarray | None, dict[str, Any]]:
    """Run the rigid-fragment least squares; return final pose or None.

    Returns ``(posed_coords, info)`` with solver flags/cost in ``info``.
    ``None`` is returned only when no usable iterate exists (exception or
    non-finite output); iteration limits still yield their final iterate so
    callers audit honestly instead of discarding evidence.
    """
    from scipy.optimize import least_squares

    try:
        solution = least_squares(
            residuals, np.asarray(x0, dtype=float), method="trf", max_nfev=max_nfev
        )
    except Exception as exc:
        return None, {
            "reason": f"solver exception: {type(exc).__name__}: {exc}",
            "evidence": {},
        }
    posed = pose(solution.x)
    if not np.all(np.isfinite(posed)):
        return None, {
            "reason": f"solver produced non-finite coordinates: {solution.message}",
            "evidence": {"nfev": int(solution.nfev), "cost": float(solution.cost)},
        }
    return np.asarray(posed, dtype=float), {
        "reason": "",
        "evidence": {
            "nfev": int(solution.nfev),
            "cost": float(solution.cost),
            "solver_converged": bool(solution.success),
            "solver_message": str(solution.message),
        },
    }


FLEX_BACKEND_NAME = "scipy-lbfgsb-flexible-internal-ls"


def realize_flexible(
    coordinates: np.ndarray,
    graph: TypedGraph,
    metal: int,
    donors: Sequence[int],
    site_ids: Sequence[str],
    placement: Sequence[int],
    template: ShapeTemplate,
    target_id: str = "T00",
    perceive: Callable[[np.ndarray], Any] | None = None,
    realize_tol: float = 0.45,
    intra_bond_tol: float = 0.05,
    intra_angle_tol_deg: float = 3.0,
    reaction_tol: float = 0.25,
    clash_scale: float = 0.70,
    weight_target: float = 3.0,
    weight_bond: float = 40.0,
    weight_angle133: float = 10.0,
    weight_clash: float = 20.0,
    weight_reaction: float = 40.0,
    weight_stereo: float = 5.0,
    weight_planar: float = 20.0,
    clash_shortlist_cutoff: float = 5.0,
    maxiter: int = 400,
    warm_max_nfev: int = 40,
) -> RealizationResult:
    """Realize one placement by constrained internal-coordinate relaxation.

    All non-metal atoms move in Cartesian space under analytic-gradient
    L-BFGS-B against harmonic internal-coordinate restraints anchored at the
    input geometry: donor-to-target attraction, covalent bond lengths, 1-3
    angle distances, non-bonded clash floors, reaction-edge lengths, and
    signed chiral-volume restraints (penalized only when the input sign
    flips).  The start point is the rigid-fragment solution, so large
    rearrangements ride rigid motion while local strain relaxes flexibly —
    including multidentate ligands, whose donors move together under intact
    restraints.  Typed graph, reaction edges, and atom indices are preserved;
    the terminal audit (bonds/angles/stereo/clash/re-perception gate) is the
    same honest gate as the rigid backend.
    """
    from scipy.optimize import minimize

    coords = np.asarray(coordinates, dtype=float)
    donor_list = [int(d) for d in donors]
    place = tuple(int(v) for v in placement)
    if len(donor_list) != template.coordination_number or len(place) != len(donor_list):
        raise ValueError("donor/placement count incompatible with template")
    plan = partition_fragments(graph, metal)
    member_of: dict[int, int] = {}
    for index, frag in enumerate(plan.fragments):
        for atom in frag:
            member_of[atom] = index
    if any(member_of[d] == plan.anchor for d in donor_list):
        return RealizationResult(
            target_id=target_id,
            structure=None,
            status="UNRESOLVED",
            reason="donor shares the metal anchor fragment; no motion model",
            backend=FLEX_BACKEND_NAME,
            evidence={"attempts": 0, "fragment_plan": plan.to_dict()},
        )
    center = coords[metal]
    ref_lengths = np.array([np.linalg.norm(coords[d] - center) for d in donor_list])
    if bool(np.any(ref_lengths < 1e-9)):
        return RealizationResult(
            target_id=target_id,
            structure=None,
            status="UNRESOLVED",
            reason="degenerate input metal-donor distance",
            backend=FLEX_BACKEND_NAME,
            evidence={"attempts": 0, "fragment_plan": plan.to_dict()},
        )
    verts = np.asarray(template.vertices, dtype=float)
    input_dirs = np.array([(coords[d] - center) / ref for d, ref in zip(donor_list, ref_lengths)])
    ideal_dirs = np.array(
        [verts[place[i]] / np.linalg.norm(verts[place[i]]) for i in range(len(donor_list))]
    )
    rotation = kabsch_proper_rotation(input_dirs, ideal_dirs)
    oriented = np.array([rotation.T @ verts[place[i]] for i in range(len(donor_list))])
    oriented /= np.linalg.norm(oriented, axis=1, keepdims=True)
    targets = np.array([center + oriented[i] * ref_lengths[i] for i in range(len(donor_list))])
    initial_donor_error = float(
        np.max(np.linalg.norm(np.array([coords[d] for d in donor_list]) - targets, axis=1))
    )

    # Warm start: full rigid verdict with the same perception gate; an
    # already-realized rigid pose when available, else the input geometry.
    warm = realize_target(
        coords,
        graph,
        metal,
        donors,
        site_ids,
        placement,
        template,
        target_id=target_id,
        perceive=perceive,
        realize_tol=realize_tol,
        intra_bond_tol=intra_bond_tol,
        intra_angle_tol_deg=intra_angle_tol_deg,
        reaction_tol=reaction_tol,
        clash_scale=clash_scale,
        max_nfev=warm_max_nfev,
    )
    start = np.array(warm.structure if warm.structure is not None else coords, dtype=float)
    rigid_warm_donor_error = float(
        np.max(np.linalg.norm(np.array([start[d] for d in donor_list]) - targets, axis=1))
    )

    free = [i for i in range(graph.natoms) if i != metal]
    elements = graph.elements
    # Restraint tables anchored at the INPUT geometry.
    bond_rows: list[tuple[int, int, float]] = [
        (edge.a, edge.b, float(np.linalg.norm(coords[edge.a] - coords[edge.b])))
        for edge in graph.edges
        if edge.type is EdgeType.COVALENT
    ]
    angle133_rows: list[tuple[int, int, float]] = []
    for atom in range(graph.natoms):
        cov = graph.neighbors(atom, EdgeType.COVALENT)
        for pos, first in enumerate(cov):
            for second in cov[pos + 1 :]:
                if member_of[first] != member_of[atom] or member_of[second] != member_of[atom]:
                    continue
                angle133_rows.append(
                    (first, second, float(np.linalg.norm(coords[first] - coords[second])))
                )
    bonded = {(min(edge.a, edge.b), max(edge.a, edge.b)) for edge in graph.edges}
    clash_rows: list[tuple[int, int, float]] = []
    for a in range(graph.natoms):
        for b in range(a + 1, graph.natoms):
            if (a, b) in bonded or a == metal or b == metal:
                continue
            if float(np.linalg.norm(coords[a] - coords[b])) > clash_shortlist_cutoff:
                continue
            floor = clash_scale * (
                COVALENT_RADII.get(elements[a], 1.0) + COVALENT_RADII.get(elements[b], 1.0)
            )
            clash_rows.append((a, b, floor))
    reaction_rows = _all_reaction_refs(coords, graph)
    stereo_rows: list[tuple[int, int, int, int, float]] = []
    for atom in range(graph.natoms):
        neighbors = graph.neighbors(atom, EdgeType.COVALENT)
        if len(neighbors) < 4:
            continue
        pick = tuple(sorted(neighbors)[:4])
        volume = signed_volume(coords, atom, pick)
        if abs(volume) < 1e-3:
            continue
        stereo_rows.append((pick[0], pick[1], pick[2], pick[3], 1.0 if volume > 0.0 else -1.0))
    # Double-bond planarity restraints: substituents held in the input plane
    # so Cartesian penalties cannot silently flip E/Z state.
    planar_rows: list[tuple[int, int, int, int]] = []
    for edge in graph.edges:
        if edge.type is not EdgeType.COVALENT or edge.bond_order != 2:
            continue
        subs_a = [n for n in graph.neighbors(edge.a, EdgeType.COVALENT) if n != edge.b]
        subs_b = [n for n in graph.neighbors(edge.b, EdgeType.COVALENT) if n != edge.a]
        if not subs_a or not subs_b:
            continue
        for sub in subs_a[1:] + subs_b:
            planar_rows.append((sub, edge.a, edge.b, subs_a[0]))

    base = coords.copy()
    x0 = np.array([start[i] for i in free]).reshape(-1)

    def _objective(vector: np.ndarray) -> tuple[float, np.ndarray]:
        trial = base.copy()
        trial[free] = vector.reshape(len(free), 3)
        grad = np.zeros_like(trial)
        energy = 0.0
        for rank, donor in enumerate(donor_list):
            diff = trial[donor] - targets[rank]
            energy += weight_target * float(diff @ diff)
            grad[donor] += 2.0 * weight_target * diff
        for a, b, ref in bond_rows:
            delta = trial[a] - trial[b]
            dist = float(np.linalg.norm(delta)) + 1e-12
            residual = (dist - ref) / max(ref, 1e-9)
            energy += weight_bond * residual * residual
            force = 2.0 * weight_bond * residual * delta / (dist * max(ref, 1e-9))
            if a != metal:
                grad[a] += force
            if b != metal:
                grad[b] -= force
        for a, b, ref in angle133_rows:
            delta = trial[a] - trial[b]
            dist = float(np.linalg.norm(delta)) + 1e-12
            residual = (dist - ref) / max(ref, 1e-9)
            energy += weight_angle133 * residual * residual
            force = 2.0 * weight_angle133 * residual * delta / (dist * max(ref, 1e-9))
            grad[a] += force
            grad[b] -= force
        for a, b, floor in clash_rows:
            delta = trial[a] - trial[b]
            dist = float(np.linalg.norm(delta)) + 1e-12
            if dist < floor:
                residual = floor - dist
                energy += weight_clash * residual * residual
                force = -2.0 * weight_clash * residual * delta / dist
                if a != metal:
                    grad[a] += force
                if b != metal:
                    grad[b] -= force
        for a, b, ref in reaction_rows:
            delta = trial[a] - trial[b]
            dist = float(np.linalg.norm(delta)) + 1e-12
            residual = (dist - ref) / max(ref, 1e-9)
            energy += weight_reaction * residual * residual
            force = 2.0 * weight_reaction * residual * delta / (dist * max(ref, 1e-9))
            if a != metal:
                grad[a] += force
            if b != metal:
                grad[b] -= force
        for p0, p1, p2, p3, sign in stereo_rows:
            mat = np.column_stack(
                [trial[p1] - trial[p0], trial[p2] - trial[p0], trial[p3] - trial[p0]]
            )
            volume = float(np.linalg.det(mat)) / 6.0
            signed = sign * volume
            if signed < 0.0:
                energy += weight_stereo * signed * signed
                cross12 = np.cross(trial[p2] - trial[p0], trial[p3] - trial[p0]) / 6.0
                cross20 = np.cross(trial[p3] - trial[p0], trial[p1] - trial[p0]) / 6.0
                cross01 = np.cross(trial[p1] - trial[p0], trial[p2] - trial[p0]) / 6.0
                push = 2.0 * weight_stereo * signed * sign
                grad[p1] += push * cross12
                grad[p2] += push * cross20
                grad[p3] += push * cross01
                grad[p0] -= push * (cross12 + cross20 + cross01)
        for sub, a, b, ref in planar_rows:
            edge1 = trial[b] - trial[a]
            edge2 = trial[ref] - trial[a]
            normal = np.cross(edge1, edge2)
            norm = float(np.linalg.norm(normal)) + 1e-12
            unit = normal / norm
            dist = float((trial[sub] - trial[a]) @ unit)
            energy += weight_planar * dist * dist
            push = 2.0 * weight_planar * dist * unit
            if sub != metal:
                grad[sub] += push
            if a != metal:
                grad[a] -= push
        return energy, grad[free].reshape(-1)

    try:
        solution = minimize(
            _objective,
            x0,
            method="L-BFGS-B",
            jac=True,
            options={"maxiter": maxiter, "ftol": 1e-9, "gtol": 1e-5},
        )
    except Exception as exc:
        return RealizationResult(
            target_id=target_id,
            structure=None,
            status="UNRESOLVED",
            reason=f"solver exception: {type(exc).__name__}: {exc}",
            backend=FLEX_BACKEND_NAME,
            evidence={"attempts": 1, "fragment_plan": plan.to_dict()},
        )
    # Audit-driven verdicts: the optimizer flag is evidence, while REALIZED /
    # DRIFTED follow the geometric audit of the final iterate (an iteration
    # limit with a verifying point is not a failure of the science).
    solver_ok = bool(solution.success)
    solver_note = str(solution.message)
    realized = base.copy()
    realized[free] = np.asarray(solution.x, dtype=float).reshape(len(free), 3)
    if not np.all(np.isfinite(realized)):
        return RealizationResult(
            target_id=target_id,
            structure=None,
            status="UNRESOLVED",
            reason=f"solver produced non-finite coordinates: {solver_note}",
            backend=FLEX_BACKEND_NAME,
            evidence={
                "attempts": 1,
                "nit": int(solution.nit),
                "cost": float(solution.fun),
                "fragment_plan": plan.to_dict(),
            },
        )
    verdict = _audit_geometry(
        coords,
        realized,
        graph,
        donor_list,
        targets,
        place,
        template,
        target_id,
        perceive,
        member_of,
        _full_clash_pairs(graph, clash_scale),
        _all_reaction_refs(coords, graph),
        realize_tol=realize_tol,
        intra_bond_tol=intra_bond_tol,
        intra_angle_tol_deg=intra_angle_tol_deg,
        reaction_tol=reaction_tol,
        backend_name=FLEX_BACKEND_NAME,
        success_note=(
            "flexible internal-coordinate solve within tolerance and re-perceived"
            + ("" if solver_ok else " (verified at iteration limit)")
        ),
        extra_evidence={
            "attempts": 1,
            "nit": int(solution.nit),
            "cost": float(solution.fun),
            "solver_converged": solver_ok,
            "solver_message": solver_note,
            "initial_donor_error": initial_donor_error,
            "rigid_warm_donor_error": rigid_warm_donor_error,
            "rigid_warm_status": warm.status,
            "fragment_plan": plan.to_dict(),
            "target_positions": [list(map(float, row)) for row in targets],
        },
    )
    if verdict.status == "UNRESOLVED":
        return verdict
    if not solver_ok and verdict.status == "DRIFTED":
        return RealizationResult(
            target_id=verdict.target_id,
            structure=verdict.structure,
            status="DRIFTED",
            reason=verdict.reason + f" [solver: {solver_note}]",
            backend=verdict.backend,
            evidence=verdict.evidence,
        )
    return verdict


def proof_contradiction_alert(
    placement: Sequence[int],
    site_ids: Sequence[str],
    proofs: Sequence[Any],
    template: ShapeTemplate,
) -> list[str]:
    """Flag realized geometries that contradict proven-excluded states.

    Call only with audited REALIZED geometries (all hard bounds satisfied):
    when such a geometry places a proof pair trans, the geometry refutes the
    bound's assumptions and the bound needs review — a diagnostic alert,
    never a proof certificate.  Non-realized iterates cannot refute bounds
    whose assumptions they violate, so they never raise here.
    """
    alerts: list[str] = []
    sites = list(site_ids)
    place = [int(v) for v in placement]
    opposite = template.opposite
    for proof in proofs:
        pair = tuple(proof.pair) if hasattr(proof, "pair") else None
        if pair is None or len(pair) != 2:
            continue
        try:
            pos_a = sites.index(pair[0])
            pos_b = sites.index(pair[1])
        except ValueError:
            continue
        if opposite.get(place[pos_a]) == place[pos_b]:
            alerts.append(
                f"PROOF_CONTRADICTED diagnostic: realized {pair[0]}-trans-{pair[1]} "
                "satisfies all hard bounds; bound assumptions need review"
            )
    return alerts


def _audit_geometry(
    coords: np.ndarray,
    realized: np.ndarray,
    graph: TypedGraph,
    donor_list: Sequence[int],
    targets: np.ndarray,
    place: tuple[int, ...],
    template: ShapeTemplate,
    target_id: str,
    perceive: Callable[[np.ndarray], Any] | None,
    frag_of: dict[int, int],
    clash_pairs: Sequence[tuple[int, int, float]],
    reaction_refs: Sequence[tuple[int, int, float]],
    *,
    realize_tol: float,
    intra_bond_tol: float,
    intra_angle_tol_deg: float,
    reaction_tol: float,
    backend_name: str,
    success_note: str,
    extra_evidence: dict[str, Any],
) -> RealizationResult:
    """Shared terminal audit: metrics, stereo guard, re-perception gate."""
    donor_err = np.linalg.norm(np.array([realized[d] for d in donor_list]) - targets, axis=1)
    max_donor_err = float(np.max(donor_err))
    # Intra-fragment integrity audit (exact by construction; verified here).
    bond_devs: list[float] = []
    for edge in graph.edges:
        if edge.type is not EdgeType.COVALENT:
            continue
        if frag_of[edge.a] != frag_of[edge.b]:
            continue
        before = float(np.linalg.norm(coords[edge.a] - coords[edge.b]))
        after = float(np.linalg.norm(realized[edge.a] - realized[edge.b]))
        bond_devs.append(abs(before - after))
    max_bond_dev = float(max(bond_devs)) if bond_devs else 0.0
    # Intra-fragment bond angles in degrees (frozen coordination vocabulary:
    # coordination_angle_atol_deg); exact by rigid construction, verified here.
    angle_devs: list[float] = []
    for atom in range(graph.natoms):
        cov = [n for n in graph.neighbors(atom, EdgeType.COVALENT) if frag_of[n] == frag_of[atom]]
        for pos, first in enumerate(cov):
            for second in cov[pos + 1 :]:
                if frag_of[first] != frag_of[atom] or frag_of[second] != frag_of[atom]:
                    continue
                before = _bond_angle_deg(coords, atom, first, second)
                after = _bond_angle_deg(realized, atom, first, second)
                if before != before or after != after:  # NaN guards degenerate input
                    continue
                angle_devs.append(abs(before - after))
    max_angle_dev = float(max(angle_devs)) if angle_devs else 0.0
    min_clash_gap = float("inf")
    for a, b, floor in clash_pairs:
        gap = float(np.linalg.norm(realized[a] - realized[b])) - floor
        if gap < min_clash_gap:
            min_clash_gap = gap
    reaction_violations = [
        abs(float(np.linalg.norm(realized[a] - realized[b])) - ref) for a, b, ref in reaction_refs
    ]
    max_reaction_viol = float(max(reaction_violations)) if reaction_violations else 0.0
    guard = stereo_guard(coords, realized, graph)
    double_bonds = double_bond_audit(coords, realized, graph)
    # Observed-key determination: the callback returns either a canonical
    # placement tuple (or target-id string), or a rich ``(key, info)`` pair
    # whose info mapping may carry ``unambiguous``.  Bare keys are treated as
    # definite; only an explicit unambiguous flag (or a callback failure, or
    # no callback at all) yields ambiguity.
    perceived: Any = None
    key_match: bool | None = None
    observed_unambiguous = True
    if perceive is not None:
        try:
            perceived = perceive(realized)
            from .enumeration import canonical_representative
            from .shapes import proper_rotation_group

            group = proper_rotation_group(template.name)
            if (
                isinstance(perceived, tuple)
                and len(perceived) == 2
                and isinstance(perceived[1], Mapping)
            ):
                info = dict(perceived[1])
                perceived = perceived[0]
                observed_unambiguous = bool(info.get("unambiguous", True))
            if isinstance(perceived, str):
                key_match = perceived == target_id
            else:
                key_match = canonical_representative(
                    tuple(int(v) for v in perceived), group
                ) == canonical_representative(tuple(place), group)
        except Exception as exc:
            key_match = None
            observed_unambiguous = False
            perceived = f"ERROR: {type(exc).__name__}: {exc}"
    else:
        observed_unambiguous = False
    evidence: dict[str, Any] = {
        "max_donor_error": max_donor_err,
        "donor_errors": [float(v) for v in donor_err],
        "max_intra_bond_deviation": max_bond_dev,
        "max_intra_angle_deviation_deg": max_angle_dev,
        "min_clash_gap": min_clash_gap,
        "max_reaction_violation": max_reaction_viol,
        "stereo_guard": guard,
        "double_bond_audit": double_bonds,
        "perceived_class": list(perceived) if isinstance(perceived, (tuple, list)) else perceived,
        "perception_gate_passed": key_match,
        "observed_unambiguous": observed_unambiguous,
    }
    evidence.update(extra_evidence)
    geometry_valid = bool(
        max_donor_err <= realize_tol
        and max_bond_dev <= intra_bond_tol
        and max_angle_dev <= intra_angle_tol_deg
        and min_clash_gap >= 0.0
        and max_reaction_viol <= reaction_tol
        and guard["preserved"]
        and double_bonds["preserved"]
    )
    evidence["geometry_valid"] = geometry_valid
    if key_match is True and geometry_valid:
        evidence["drift_routing"] = None
        return RealizationResult(
            target_id=target_id,
            structure=tuple(tuple(map(float, row)) for row in realized),
            status="REALIZED",
            reason=success_note,
            backend=backend_name,
            evidence=evidence,
        )
    if key_match is True:
        # Observed key equals the target key but quality gates fail: geometry
        # audit failure, never StateKey drift.  Rejected geometry unpublished.
        reasons = _quality_reasons(
            max_donor_err,
            realize_tol,
            max_bond_dev,
            intra_bond_tol,
            max_angle_dev,
            intra_angle_tol_deg,
            min_clash_gap,
            max_reaction_viol,
            reaction_tol,
            guard,
            double_bonds,
        )
        evidence["drift_routing"] = "observed_state_diagnostic_only"
        return RealizationResult(
            target_id=target_id,
            structure=None,
            status="UNRESOLVED",
            reason="geometry audit failure at the commanded state: " + "; ".join(reasons),
            backend=backend_name,
            evidence=evidence,
        )
    if key_match is False and observed_unambiguous:
        # Definite unambiguous observed key differs: genuine StateKey drift.
        if geometry_valid:
            evidence["drift_routing"] = "realized_via_drift_candidate"
        else:
            evidence["drift_routing"] = "observed_state_diagnostic_only"
            reasons = _quality_reasons(
                max_donor_err,
                realize_tol,
                max_bond_dev,
                intra_bond_tol,
                max_angle_dev,
                intra_angle_tol_deg,
                min_clash_gap,
                max_reaction_viol,
                reaction_tol,
                guard,
                double_bonds,
            )
            evidence["drift_quality"] = reasons
        return RealizationResult(
            target_id=target_id,
            structure=None,
            status="DRIFTED",
            reason=(
                f"observed state {evidence['perceived_class']} != target {target_id}; "
                f"geometry_valid={geometry_valid}"
            ),
            backend=backend_name,
            evidence=evidence,
        )
    evidence["drift_routing"] = "observed_state_diagnostic_only"
    return RealizationResult(
        target_id=target_id,
        structure=None,
        status="UNRESOLVED",
        reason="no definite observed key (ambiguous perception or missing callback): AMBIGUOUS_KEY",
        backend=backend_name,
        evidence=evidence,
    )


def _quality_reasons(
    max_donor_err: float,
    realize_tol: float,
    max_bond_dev: float,
    intra_bond_tol: float,
    max_angle_dev: float,
    intra_angle_tol_deg: float,
    min_clash_gap: float,
    max_reaction_viol: float,
    reaction_tol: float,
    guard: dict[str, Any],
    double_bonds: dict[str, Any],
) -> list[str]:
    """List failed geometric quality gates for UNRESOLVED diagnostics."""
    reasons: list[str] = []
    if max_donor_err > realize_tol:
        reasons.append(f"donor drift {max_donor_err:.3f}A exceeds {realize_tol}A")
    if max_bond_dev > intra_bond_tol:
        reasons.append(
            f"intra-fragment bond deviation {max_bond_dev:.3e}A exceeds {intra_bond_tol}A"
        )
    if max_angle_dev > intra_angle_tol_deg:
        reasons.append(
            f"intra-fragment angle deviation {max_angle_dev:.3e}deg exceeds {intra_angle_tol_deg}deg"
        )
    if min_clash_gap < 0.0:
        reasons.append(f"clash gap {min_clash_gap:.3f}A below floor")
    if max_reaction_viol > reaction_tol:
        reasons.append(f"reaction-edge violation {max_reaction_viol:.3f}A exceeds {reaction_tol}A")
    if not guard["preserved"]:
        reasons.append(f"signed-volume flips at atoms {guard['flips']}")
    if not double_bonds["preserved"]:
        reasons.append(f"double-bond violations: {double_bonds['violations']}")
    return reasons


def _full_clash_pairs(graph: TypedGraph, clash_scale: float) -> list[tuple[int, int, float]]:
    """All non-bonded pairs (any fragment) with clash floors for auditing."""
    bonded = set()
    for edge in graph.edges:
        bonded.add((min(edge.a, edge.b), max(edge.a, edge.b)))
    elements = graph.elements
    pairs: list[tuple[int, int, float]] = []
    for a in range(graph.natoms):
        for b in range(a + 1, graph.natoms):
            if (a, b) in bonded:
                continue
            floor = clash_scale * (
                COVALENT_RADII.get(elements[a], 1.0) + COVALENT_RADII.get(elements[b], 1.0)
            )
            pairs.append((a, b, floor))
    return pairs


def _all_reaction_refs(coords: np.ndarray, graph: TypedGraph) -> list[tuple[int, int, float]]:
    """All FORMING/BREAKING pairs with input lengths, wherever they lie."""
    return [
        (edge.a, edge.b, float(np.linalg.norm(coords[edge.a] - coords[edge.b])))
        for edge in graph.edges
        if edge.type in (EdgeType.FORMING, EdgeType.BREAKING)
    ]


def feasibility_spike(
    coordinates: np.ndarray,
    graph: TypedGraph,
    metal: int,
    donors: Sequence[int],
    site_ids: Sequence[str],
    representatives: Sequence[tuple[str, tuple[int, ...]]],
    template: ShapeTemplate,
    perceive: Callable[[np.ndarray], Any] | None = None,
    backend: str = "rigid",
    proofs: Sequence[Any] | None = None,
    **kwargs: Any,
) -> FeasibilityReport:
    """Run realization over shape-class representatives and account honestly.

    *backend* selects ``"rigid"`` (rigid-fragment least squares) or
    ``"flexible"`` (constrained internal-coordinate L-BFGS-B relaxation warm
    started from the rigid pose).  Verdicts follow observed-key equality plus
    geometric quality (correction: exact-key-plus-clash-failure is
    UNRESOLVED, never StateKey drift).  *proofs* (BoundEvidence records)
    are checked against REALIZED geometries only: a realized trans placement
    raises a PROOF_CONTRADICTED diagnostic, never a certificate.
    """
    if backend not in ("rigid", "flexible"):
        raise ValueError(f"unknown realization backend {backend!r}")
    worker = realize_target if backend == "rigid" else realize_flexible
    call_kwargs = dict(kwargs)
    if backend == "flexible" and "max_nfev" in call_kwargs and "warm_max_nfev" not in call_kwargs:
        call_kwargs["warm_max_nfev"] = call_kwargs.pop("max_nfev")
    results: list[RealizationResult] = []
    for _ordinal, (target_id, placement) in enumerate(representatives):
        result = worker(
            coordinates,
            graph,
            metal,
            donors,
            site_ids,
            placement,
            template,
            target_id=target_id,
            perceive=perceive,
            **call_kwargs,
        )
        if result.status == "REALIZED" and proofs:
            alerts = proof_contradiction_alert(placement, list(site_ids), proofs, template)
            if alerts:
                evidence = dict(result.evidence)
                evidence["proof_contradictions"] = alerts
                result = RealizationResult(
                    target_id=result.target_id,
                    structure=result.structure,
                    status=result.status,
                    reason=result.reason,
                    backend=result.backend,
                    evidence=evidence,
                )
        results.append(result)
    budgets = {"max_nfev": int(kwargs.get("max_nfev", 120))}
    if backend == "flexible":
        budgets["maxiter"] = int(kwargs.get("maxiter", 400))
    return FeasibilityReport(
        template=template.name,
        results=tuple(results),
        budgets=budgets,
    )


@dataclass(frozen=True, slots=True)
class FeasibilityReport:
    """Honest N-target feasibility outcome."""

    template: str
    results: tuple[RealizationResult, ...]
    budgets: dict[str, Any]

    def summary(self) -> dict[str, Any]:
        """Return status counts plus per-target reasons."""
        counts: dict[str, int] = {}
        for result in self.results:
            counts[result.status] = counts.get(result.status, 0) + 1
        return {
            "template": self.template,
            "counts": counts,
            "total": len(self.results),
            "budgets": dict(self.budgets),
            "targets": [
                {
                    "target_id": result.target_id,
                    "status": result.status,
                    "reason": result.reason,
                    "backend": result.backend,
                }
                for result in self.results
            ],
        }
