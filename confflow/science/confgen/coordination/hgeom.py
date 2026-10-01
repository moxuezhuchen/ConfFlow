#!/usr/bin/env python3
"""Geometric symmetry (H_geom) verification for coordination target suppression.

Pure science: stdlib + NumPy only.

A geometric symmetry witness is a pair ``(sigma, rotation)``: an atom
permutation ``sigma`` and a proper spatial rotation ``R`` satisfying
``X[sigma(i)] ~= R . X[i]`` for every in-scope atom.  Suppression is allowed
only when *all* witnesses verify:

- key: declared ``(sigma, R)``, scope, declared site action, declared
  template vertex permutation ``rho``, tolerances;
- proper fit: Kabsch ``det == +1`` RMSD within tolerance *and* the fitted
  rotation matches the declared ``R``;
- displacement: per-atom ``|X[sigma(i)] - R.X[i]|`` within tolerance;
- internal coordinates preserved over in-scope covalent edges;
- vertex action: the induced template permutation
  ``rho(f(i)) = f(sigma(i))`` on the perceived assignment ``f`` is a proper
  template rotation equal to the declared ``rho``;
- StateKey stabilizer: the labeled coordination state is exactly invariant
  under the ``(sigma, rho)`` action;
- stereo: declared labels preserved under ``sigma`` plus an ordered-neighbor
  parity audit (odd parity on a ``sigma``-fixed tetrahedral center refuses
  suppression; full R/S-E/Z cross-center certification awaits the production
  stereo model and is declared as a limitation);
- perception margins: real re-perception of the original and transformed
  geometries yields the same class with measured margins above threshold
  (constant-key callbacks cannot satisfy the margin requirement);
- closure: ``sigma^order`` is the identity on scope and ``R^order`` is the
  identity matrix.

Topology-level sigma validity alone never suppresses geometry targets.
Ambiguity anywhere disables suppression and retains all targets.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

import numpy as np

from ..graph import EdgeType, TypedGraph

__all__ = [
    "HGeomKey",
    "HGeomVerdict",
    "SuppressionDisabledError",
    "verify_hgeom",
    "suppression_decision",
    "decide_orbit_suppression",
    "apply_permutation",
    "rotate_about_center",
    "induced_template_action",
    "state_key_stabilized",
    "audit_stereo",
    "rotation_angle_between",
]


class SuppressionDisabledError(ValueError):
    """Raised when geometry suppression is attempted without full witnesses."""


@dataclass(frozen=True, slots=True)
class HGeomKey:
    """Declared geometric symmetry key: ``(sigma, R)`` plus declarations."""

    kind: str
    mapping: tuple[int, ...]
    rotation: tuple[tuple[float, float, float], ...]
    center: tuple[float, float, float]
    order: int = 2
    scope: tuple[int, ...] = ()
    declared_site_action: dict[str, str] | None = None
    declared_rho: tuple[int, ...] | None = None
    tolerance_rmsd: float = 0.10
    tolerance_displacement: float = 0.15
    tolerance_internal: float = 0.05
    tolerance_rotation_deg: float = 2.0
    margin_tol: float = 0.50

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-compatible record."""
        return {
            "kind": self.kind,
            "mapping": list(self.mapping),
            "rotation": [list(row) for row in self.rotation],
            "center": list(self.center),
            "order": self.order,
            "scope": list(self.scope),
            "declared_site_action": (
                dict(self.declared_site_action) if self.declared_site_action is not None else None
            ),
            "declared_rho": list(self.declared_rho) if self.declared_rho is not None else None,
            "tolerance_rmsd": self.tolerance_rmsd,
            "tolerance_displacement": self.tolerance_displacement,
            "tolerance_internal": self.tolerance_internal,
            "tolerance_rotation_deg": self.tolerance_rotation_deg,
            "margin_tol": self.margin_tol,
        }


@dataclass(frozen=True, slots=True)
class HGeomVerdict:
    """H_geom verification outcome."""

    suppress_allowed: bool
    witnesses: dict[str, Any]
    reasons: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-compatible record."""
        return {
            "suppress_allowed": self.suppress_allowed,
            "witnesses": {k: v for k, v in self.witnesses.items()},
            "reasons": list(self.reasons),
        }


def apply_permutation(coords: np.ndarray, mapping: Sequence[int]) -> np.ndarray:
    """Return coordinates relabeled by the atom permutation."""
    return np.asarray(coords, dtype=float)[list(int(v) for v in mapping)]


def rotate_about_center(
    coords: np.ndarray, rotation: np.ndarray, center: Sequence[float]
) -> np.ndarray:
    """Apply a spatial rotation about *center*."""
    array = np.asarray(coords, dtype=float)
    pivot = np.asarray(center, dtype=float)
    return (array - pivot) @ np.asarray(rotation, dtype=float).T + pivot


def rotation_angle_between(first: np.ndarray, second: np.ndarray) -> float:
    """Return the rotation angle (degrees) mapping *first* onto *second*."""
    relative = np.asarray(second, dtype=float) @ np.asarray(first, dtype=float).T
    trace = float(np.trace(relative))
    cosine = max(-1.0, min(1.0, (trace - 1.0) / 2.0))
    return float(np.degrees(np.arccos(cosine)))


def induced_template_action(
    assignment: Sequence[int],
    site_permutation: Sequence[int],
    group: Sequence[Sequence[int]],
) -> tuple[int, ...] | None:
    """Compute the induced template vertex permutation ``rho``.

    Given the perceived site-to-vertex assignment ``f`` and the site action
    ``sigma``, the induced action satisfies ``rho(f(i)) = f(sigma(i))``.
    Returns ``rho`` as a vertex permutation when it is well-defined and a
    member of the proper template *group*; otherwise ``None``.
    """
    place = tuple(int(v) for v in assignment)
    sigma = tuple(int(v) for v in site_permutation)
    count = len(place)
    if sorted(sigma) != list(range(count)):
        return None
    rho: dict[int, int] = {}
    for site in range(count):
        source, target = place[site], place[sigma[site]]
        if source in rho and rho[source] != target:
            return None
        rho[source] = target
    vertices = sorted(set(place))
    if sorted(rho) != vertices or sorted(rho.values()) != vertices:
        return None
    size = max(vertices) + 1
    full = list(range(size))
    for source, target in rho.items():
        full[source] = target
    candidate = tuple(full)
    if candidate not in {tuple(g) for g in group}:
        return None
    return candidate


def state_key_stabilized(
    state_value: Mapping[str, str] | Any,
    site_ids: Sequence[str],
    site_permutation: Sequence[int],
    rho: Sequence[int],
    vertex_names: Sequence[str],
) -> bool:
    """Check exact invariance of the labeled coordination state.

    With ``f`` the site-to-vertex-name map, ``sigma`` the site action, and
    ``rho`` the vertex action, the stabilizer equation is
    ``f(sigma(i)) == rho_name(f(i))`` for every site ``i``.
    """
    sites = list(site_ids)
    names = list(vertex_names)
    rho_list = [int(v) for v in rho]
    mapping = dict(state_value) if not isinstance(state_value, dict) else state_value
    name_to_vertex = {name: k for k, name in enumerate(names)}
    try:
        for position, site in enumerate(sites):
            image_site = sites[site_permutation[position]]
            left = name_to_vertex[mapping[image_site]]
            right = rho_list[name_to_vertex[mapping[site]]]
            if left != right:
                return False
    except (KeyError, IndexError, TypeError):
        return False
    return True


def audit_stereo(
    coordinates: np.ndarray,
    transformed: np.ndarray,
    graph: TypedGraph,
    mapping: Sequence[int],
    stereo_labels: Sequence[str | None] | None,
    volume_tolerance: float = 1e-3,
) -> dict[str, Any]:
    """Audit stereo declarations under an atom permutation.

    Beyond raw label preservation, every tetrahedral (exactly-4-covalent-
    neighbor) center undergoes a numbering-independent orientation check:
    with the canonical (index-sorted) neighbor orders at a center and its
    image, the signed chiral volumes must agree up to the computed neighbor-
    correspondence parity for a proper map.  An improper map flips the
    relative sign and is reported as a violation.  Degenerate (near-planar)
    centers are skipped transparently.  Full R/S-E/Z assignment remains the
    production stereo model's job and is declared as a limitation.
    """
    from .realization import signed_volume

    coords = np.asarray(coordinates, dtype=float)
    moved = np.asarray(transformed, dtype=float)
    perm = tuple(int(v) for v in mapping)
    centers: list[dict[str, Any]] = []
    violations: list[str] = []
    skipped: list[int] = []
    nontrivial = any(perm[i] != i for i in range(graph.natoms))
    ez_marks = 0
    if stereo_labels is not None:
        for index, label in enumerate(stereo_labels):
            if isinstance(label, str) and label.strip().upper() in ("E", "Z"):
                ez_marks += 1
                # E/Z adjudication needs CIP rules the audit does not own:
                # nontrivial permutations over E/Z-declared centers are never
                # advertised proper — fail closed and mark unsupported.
                if nontrivial and perm[index] != index:
                    violations.append(
                        f"E/Z-declared atom {index} moves under the map: "
                        "E/Z stereo unsupported, suppression refused"
                    )
    if ez_marks and nontrivial:
        violations.append(
            "E/Z stereo declarations present: no properness advertised without CIP adjudication"
        )
    for index in range(graph.natoms):
        neighbors = graph.neighbors(index, EdgeType.COVALENT)
        if len(neighbors) != 4:
            continue
        image = perm[index]
        image_neighbors = graph.neighbors(image, EdgeType.COVALENT)
        if len(image_neighbors) != 4:
            violations.append(f"stereocenter {index} image {image} is not tetrahedral")
            continue
        order = sorted(neighbors)
        image_order = sorted(image_neighbors)
        # Parity taking the sigma-mapped order onto the canonical image order.
        mapped = [perm[n] for n in order]
        rank = {value: pos for pos, value in enumerate(image_order)}
        sequence = [rank[value] for value in mapped]
        inversions = sum(1 for a in range(4) for b in range(a + 1, 4) if sequence[a] > sequence[b])
        parity = 1 if inversions % 2 == 0 else -1
        volume_before = signed_volume(coords, index, order)
        volume_after = signed_volume(moved, image, image_order)
        entry: dict[str, Any] = {
            "center": index,
            "image": image,
            "neighbor_parity": parity,
        }
        if stereo_labels is not None:
            own = stereo_labels[index]
            other = stereo_labels[image]
            entry["label"] = own
            entry["image_label"] = other
            if own != other:
                violations.append(
                    f"stereocenter {index} label {own!r} maps to {other!r} at {image}"
                )
        if abs(volume_before) < volume_tolerance or abs(volume_after) < volume_tolerance:
            skipped.append(index)
            entry["volume_check"] = "skipped-degenerate"
        else:
            consistent = (volume_after > 0.0) == ((parity * volume_before) > 0.0)
            entry["volume_check"] = "consistent" if consistent else "FLIPPED"
            if not consistent:
                violations.append(
                    f"stereocenter {index} chiral volume flips under the map (improper)"
                )
        centers.append(entry)
    return {
        "centers_audited": len(centers),
        "centers": centers,
        "skipped_degenerate": skipped,
        "ez_declarations": ez_marks,
        "violations": violations,
        "limitation": (
            "ordered-neighbor parity plus chiral-volume consistency and label "
            "preservation only; E/Z adjudication unsupported (fail closed); "
            "full R/S-E/Z assignment requires the production stereo model"
        ),
    }


def _kabsch_fit(
    coords: np.ndarray, transformed: np.ndarray, scope: Sequence[int]
) -> tuple[float, float, np.ndarray, np.ndarray]:
    """Proper Kabsch fit on scope atoms; return rmsd, det, rotation, disp."""
    from .shapes import kabsch_proper_rotation

    scope_list = [int(v) for v in scope]
    src = np.asarray(coords, dtype=float)[scope_list]
    tgt = np.asarray(transformed, dtype=float)[scope_list]
    src_c = src - src.mean(axis=0)
    tgt_c = tgt - tgt.mean(axis=0)
    rotation = kabsch_proper_rotation(src_c, tgt_c)
    det = float(np.linalg.det(rotation))
    fitted = src_c @ rotation.T
    disp = np.linalg.norm(fitted - tgt_c, axis=1)
    rmsd = float(np.sqrt(np.mean(disp**2)))
    return rmsd, det, rotation, disp


def verify_hgeom(
    coordinates: np.ndarray,
    key: HGeomKey | None,
    graph: TypedGraph,
    metal: int,
    donors: Sequence[int],
    site_ids: Sequence[str],
    stereo_labels: Sequence[str | None] | None = None,
    perceive: Callable[[np.ndarray], Any] | None = None,
    perception_margins: tuple[float, float] | None = None,
    state_value: Mapping[str, str] | None = None,
    site_permutation: Sequence[int] | None = None,
    template: Any | None = None,
) -> HGeomVerdict:
    """Verify the ``(sigma, R)`` geometric symmetry; fail closed otherwise.

    *perceive* re-perceives a geometry to a canonical class key (real
    perception; constant callbacks cannot supply the required measured
    *perception_margins*).  *state_value* is the parent labeled coordination
    state audited by the stabilizer equation.  *template* supplies the proper
    group, vertex names, and count for the ``rho`` audit.
    """
    witnesses: dict[str, Any] = {}
    reasons: list[str] = []
    coords = np.asarray(coordinates, dtype=float)

    if key is None:
        return HGeomVerdict(
            suppress_allowed=False, witnesses={"key": None}, reasons=("missing H_geom key",)
        )
    witnesses["key"] = key.to_dict()
    if key.kind != "rotation-permutation":
        reasons.append(f"unsupported H_geom key kind {key.kind!r}; need 'rotation-permutation'")
    perm = tuple(int(v) for v in key.mapping)
    if sorted(perm) != list(range(graph.natoms)):
        reasons.append("H_geom mapping is not a permutation of all atoms")
        return HGeomVerdict(suppress_allowed=False, witnesses=witnesses, reasons=tuple(reasons))
    scope = tuple(int(v) for v in key.scope) or tuple(range(graph.natoms))
    witnesses["scope_size"] = len(scope)
    rotation = np.asarray(key.rotation, dtype=float)
    if rotation.shape != (3, 3):
        reasons.append("declared rotation is not 3x3")
        return HGeomVerdict(suppress_allowed=False, witnesses=witnesses, reasons=tuple(reasons))
    det_declared = float(np.linalg.det(rotation))
    witnesses["declared_rotation_det"] = det_declared
    if not det_declared > 0.0:
        reasons.append(f"declared rotation is improper (det={det_declared:.4f})")
    order = int(key.order)
    if order < 2:
        reasons.append("symmetry order must be >= 2")
        order = 2
    if not np.allclose(np.linalg.matrix_power(rotation, order), np.eye(3), atol=1e-6):
        reasons.append(f"declared rotation is not order-{order} (R^order != I)")

    # Pair equation: X[sigma(i)] ~= R . X[i] on scope.
    rotated = rotate_about_center(coords, rotation, key.center)
    pair_disp = np.linalg.norm(
        apply_permutation(coords, perm)[list(scope)] - rotated[list(scope)], axis=1
    )
    max_pair_disp = float(np.max(pair_disp)) if len(pair_disp) else float("inf")
    witnesses["pair_equation"] = {"max_displacement": max_pair_disp}
    if not max_pair_disp <= key.tolerance_displacement:
        reasons.append(
            f"pair-equation displacement {max_pair_disp:.4f}A exceeds {key.tolerance_displacement}A"
        )

    # Proper fit on scope, and the fitted rotation must match declared R.
    transformed = apply_permutation(coords, perm)
    rmsd, det, fitted, disp = _kabsch_fit(coords, transformed, scope)
    witnesses["proper_fit"] = {
        "rmsd": rmsd,
        "det": det,
        "proper": bool(det > 0.0),
        "max_displacement": float(np.max(disp)) if len(disp) else None,
        "rotation_vs_declared_deg": rotation_angle_between(fitted, rotation),
    }
    if not det > 0.0:
        reasons.append("Kabsch fit is improper (det <= 0)")
    if not rmsd <= key.tolerance_rmsd:
        reasons.append(f"scope RMSD {rmsd:.4f}A exceeds tolerance {key.tolerance_rmsd}A")
    max_disp = float(np.max(disp)) if len(disp) else float("inf")
    if not max_disp <= key.tolerance_displacement:
        reasons.append(
            f"max displacement {max_disp:.4f}A exceeds tolerance {key.tolerance_displacement}A"
        )
    angle_gap = rotation_angle_between(fitted, rotation)
    if not angle_gap <= key.tolerance_rotation_deg:
        reasons.append(
            f"fitted rotation differs from declared R by {angle_gap:.2f}deg "
            f"(tolerance {key.tolerance_rotation_deg}deg)"
        )

    # Internal-coordinate preservation over in-scope covalent edges.
    scope_set = set(scope)
    violations: list[float] = []
    for edge in graph.edges:
        if edge.type is not EdgeType.COVALENT:
            continue
        if edge.a not in scope_set or edge.b not in scope_set:
            continue
        before = float(np.linalg.norm(coords[edge.a] - coords[edge.b]))
        after = float(np.linalg.norm(transformed[edge.a] - transformed[edge.b]))
        violations.append(abs(before - after))
    max_internal = float(max(violations)) if violations else 0.0
    witnesses["internal_coords"] = {"edges_checked": len(violations), "max_violation": max_internal}
    if not max_internal <= key.tolerance_internal:
        reasons.append(
            f"internal-coordinate violation {max_internal:.4f}A exceeds {key.tolerance_internal}A"
        )

    # Closure: sigma^order is the identity on scope.
    composed = list(scope)
    for _ in range(order):
        composed = [perm[i] for i in composed]
    closed = all(v == i for v, i in zip(composed, scope))
    witnesses["closure_sigma_power_is_identity"] = bool(closed)
    if not closed:
        reasons.append(f"sigma^{order} is not the identity on scope")

    # Perception, rho, and StateKey stabilizer audits need the template.
    donor_list = [int(d) for d in donors]
    if template is None or site_permutation is None or perceive is None or state_value is None:
        missing = [
            name
            for name, value in (
                ("template", template),
                ("site_permutation", site_permutation),
                ("perceive", perceive),
                ("state_value", state_value),
            )
            if value is None
        ]
        reasons.append(f"rho/stabilizer audit unavailable (missing: {', '.join(missing)})")
        witnesses["rho"] = "MISSING"
    else:
        from .enumeration import canonical_representative
        from .shapes import proper_rotation_group

        group = proper_rotation_group(template.name)
        try:
            class_before = perceive(coords)
            class_after = perceive(transformed)
        except Exception as exc:
            class_before = class_after = None
            reasons.append(f"perception callback raised: {type(exc).__name__}: {exc}")
        same_class = class_before is not None and canonical_representative(
            tuple(class_before), group
        ) == canonical_representative(tuple(class_after), group)
        witnesses["transformed_audit"] = {
            "class_before": list(class_before) if class_before is not None else None,
            "class_after": list(class_after) if class_after is not None else None,
            "same_class": bool(same_class),
        }
        if not same_class:
            reasons.append("transformed geometry re-perceives to a different class")
        if perception_margins is None:
            reasons.append("perception margins missing: constant callbacks cannot suppress")
            witnesses["perception_margins"] = "MISSING"
        else:
            margin_before, margin_after = (
                float(perception_margins[0]),
                float(perception_margins[1]),
            )
            witnesses["perception_margins"] = {
                "before": margin_before,
                "after": margin_after,
            }
            if not min(margin_before, margin_after) >= key.margin_tol:
                reasons.append(
                    f"perception margins ({margin_before:.3f}, {margin_after:.3f}) below "
                    f"{key.margin_tol}: ambiguous, suppression refused"
                )
        # Rho from the perceived assignment + declared site action.
        rho = None
        if class_before is not None:
            f_vertices = _class_to_vertices(class_before, template, list(site_ids))
            if f_vertices is not None:
                rho = induced_template_action(f_vertices, site_permutation, group)
        witnesses["rho"] = list(rho) if rho is not None else None
        if rho is None:
            reasons.append("induced template action rho undefined or not a proper rotation")
        else:
            if key.declared_rho is not None and tuple(rho) != tuple(
                int(v) for v in key.declared_rho
            ):
                reasons.append(f"induced rho {list(rho)} != declared rho {list(key.declared_rho)}")
            state_map = (
                state_value.get("sites", state_value)
                if isinstance(state_value, Mapping)
                else state_value
            )
            stabilized = state_key_stabilized(
                state_map, site_ids, site_permutation, rho, template.vertex_names
            )
            witnesses["state_key_stabilized"] = bool(stabilized)
            if not stabilized:
                reasons.append("parent StateKey not stabilized by the (sigma, rho) action")
        # Declared-vs-induced site action on donors.
        donor_set = set(donor_list)
        if not all(perm[d] in donor_set for d in donor_list):
            reasons.append("H_geom mapping does not close on the donor set")
        elif key.declared_site_action is not None:
            index_to_site = {d: s for d, s in zip(donor_list, site_ids)}
            induced = {index_to_site[d]: index_to_site[perm[d]] for d in donor_list}
            witnesses["induced_site_action"] = dict(induced)
            if induced != dict(key.declared_site_action):
                reasons.append(
                    f"induced site action {induced} != declared {key.declared_site_action}"
                )

    # Stereo audit: labels plus chiral-volume consistency (never raw equality).
    stereo = audit_stereo(coords, transformed, graph, perm, stereo_labels)
    witnesses["stereo"] = {
        "centers_audited": stereo["centers_audited"],
        "skipped_degenerate": stereo["skipped_degenerate"],
        "violations": stereo["violations"],
        "limitation": stereo["limitation"],
    }
    if stereo_labels is None:
        reasons.append("stereo labels missing: stereo scope unverifiable")
    elif stereo["violations"]:
        reasons.extend(stereo["violations"])

    allowed = not reasons
    return HGeomVerdict(suppress_allowed=bool(allowed), witnesses=witnesses, reasons=tuple(reasons))


def _class_to_vertices(
    class_key: Sequence[int], template: Any, site_ids: Sequence[str]
) -> tuple[int, ...] | None:
    """Interpret a perceived class key as a site-ordered vertex placement."""
    try:
        values = tuple(int(v) for v in class_key)
    except (TypeError, ValueError):
        return None
    if len(values) != template.coordination_number:
        return None
    if any(not 0 <= v < template.coordination_number for v in values):
        return None
    return values


def suppression_decision(
    coordinates: np.ndarray,
    key: HGeomKey | None,
    graph: TypedGraph,
    metal: int,
    donors: Sequence[int],
    site_ids: Sequence[str],
    orbits: Sequence[Mapping[str, Any]],
    group_elements: Sequence[Sequence[int]],
    template: Any | None = None,
    stereo_labels: Sequence[str | None] | None = None,
    perceive: Callable[[np.ndarray], Any] | None = None,
    perception_margins: tuple[float, float] | None = None,
    state_value: Mapping[str, str] | None = None,
    site_permutation: Sequence[int] | None = None,
    parent_locks_ok: bool | None = None,
    treatment: str | None = None,
) -> dict[str, Any]:
    """Decide geometry-target suppression over verified group orbits.

    The unsafe generic form (suppress arbitrary caller strings) is removed:
    suppression requires orbit records holding real canonical placements, a
    group-closed passing set, full witnesses from :func:`verify_hgeom`, and
    declared parent C/R/T locks.  Each orbit retains exactly one
    representative; every suppressed placement carries its representative
    plus the witness.  Anything less fails closed to retaining everything
    with zero suppressions.  ``preserve_input`` treatment never suppresses.
    """
    verdict = verify_hgeom(
        coordinates,
        key,
        graph,
        metal,
        donors,
        site_ids,
        stereo_labels,
        perceive,
        perception_margins,
        state_value,
        site_permutation,
        template,
    )
    shape_group: tuple[tuple[int, ...], ...] | None = None
    if template is not None:
        from .shapes import proper_rotation_group

        shape_group = proper_rotation_group(template.name)
    return decide_orbit_suppression(
        orbits,
        group_elements,
        verdict,
        parent_locks_ok=parent_locks_ok,
        treatment=treatment,
        coordination_number=(template.coordination_number if template is not None else None),
        shape_group=shape_group,
    )


def decide_orbit_suppression(
    orbits: Sequence[Mapping[str, Any]],
    group_elements: Sequence[Sequence[int]],
    verdict: HGeomVerdict,
    parent_locks_ok: bool | None = None,
    treatment: str | None = None,
    coordination_number: int | None = None,
    shape_group: Sequence[Sequence[int]] | None = None,
) -> dict[str, Any]:
    """Partition suppression over orbit records with a verified verdict.

    Pure orbit-accounting step, testable without geometry: retains one
    representative per orbit and attaches ``(representative, witness)`` to
    each suppressed placement.  Arbitrary (non-orbit-member) strings,
    non-group-closed passing sets, missing/failed parent locks, and
    ``preserve_input`` treatment all fail closed to retain-everything.
    """
    members: list[tuple[int, ...]] = []
    representatives: dict[str, tuple[int, ...]] = {}
    try:
        for position, orbit in enumerate(orbits):
            rep = tuple(int(v) for v in orbit["representative"])
            group = tuple(tuple(int(v) for v in member) for member in orbit["members"])
            if rep not in group:
                raise ValueError("representative is not an orbit member")
            if coordination_number is not None:
                for placement in group:
                    if len(placement) != coordination_number or any(
                        not 0 <= v < coordination_number for v in placement
                    ):
                        raise ValueError(f"placement {placement} outside template range")
            orbit_id = str(orbit.get("id", f"orbit-{position:02d}"))
            representatives[orbit_id] = rep
            members.extend(group)
    except (KeyError, TypeError, ValueError) as exc:
        return {
            "suppression": "DISABLED",
            "retained": [],
            "suppressed": [],
            "reason": f"malformed orbit records: {exc}",
            "verdict": verdict.to_dict(),
        }
    passing = set(members)
    # Closure of the FULL passing set under every group element, compared
    # canonically under proper shape rotations; per-element order alone never
    # certifies this.  Non-closed sets fall back to identity: retain
    # everything, suppress nothing.
    from .enumeration import canonical_representative

    closed = True
    elements = [tuple(int(v) for v in element) for element in group_elements]
    for element in elements:
        count = len(element)
        inv = [0] * count
        try:
            for old, new in enumerate(element):
                inv[new] = old
        except IndexError:
            closed = False
            break
        for placement in passing:
            if len(placement) != count:
                closed = False
                break
            image = tuple(placement[inv[i]] for i in range(count))
            if shape_group is not None:
                image = canonical_representative(image, shape_group)
            if image not in passing:
                closed = False
                break
        if not closed:
            break
    if treatment == "preserve_input":
        return {
            "suppression": "DISABLED",
            "retained": [list(placement) for placement in sorted(passing)],
            "suppressed": [],
            "reason": "preserve_input treatment never suppresses targets",
            "verdict": verdict.to_dict(),
        }
    if parent_locks_ok is not True:
        return {
            "suppression": "DISABLED",
            "retained": [list(placement) for placement in sorted(passing)],
            "suppressed": [],
            "reason": "parent C/R/T locks not declared held",
            "verdict": verdict.to_dict(),
        }
    if not verdict.suppress_allowed:
        return {
            "suppression": "DISABLED",
            "retained": [list(placement) for placement in sorted(passing)],
            "suppressed": [],
            "reason": f"witnesses incomplete: {list(verdict.reasons)}",
            "verdict": verdict.to_dict(),
        }
    if not closed:
        return {
            "suppression": "DISABLED",
            "retained": [list(placement) for placement in sorted(passing)],
            "suppressed": [],
            "reason": "passing set not group-closed: identity fallback retains everything",
            "verdict": verdict.to_dict(),
        }
    retained_set = set(representatives.values())
    orbit_members: dict[str, set[tuple[int, ...]]] = {}
    for position, orbit in enumerate(orbits):
        orbit_id = str(orbit.get("id", f"orbit-{position:02d}"))
        orbit_members[orbit_id] = {tuple(int(v) for v in member) for member in orbit["members"]}
    suppressed = [
        {
            "placement": list(placement),
            "representative": list(representatives[orbit_id]),
            "witness": verdict.witnesses.get("rho"),
        }
        for orbit_id in sorted(representatives)
        for placement in sorted(orbit_members[orbit_id] - retained_set)
    ]
    return {
        "suppression": "ALLOWED",
        "retained": [list(placement) for placement in sorted(retained_set)],
        "suppressed": suppressed,
        "reason": "verified group orbits with full witnesses and held parent locks",
        "verdict": verdict.to_dict(),
    }
