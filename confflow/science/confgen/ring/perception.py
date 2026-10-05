#!/usr/bin/env python3

"""ConfGen v3 ring-lane perception: CP-based regular-form assignment (R4).

A measured ring (ordered coordinates in traversal order from the anchor) is
assigned to the nearest canonical regular form (R1 puckering tables, CP
distance) with an explicit flat gate. Traversal anchor and direction are
spec authority: CP transforms covariantly under relabelling (R1 relabel),
so moving the anchor only changes the form index, never the family and
never forces ambiguity. No reversed-direction matching is attempted; an
oppositely traversed ring scores as its relabelled form (family invariant).

Gates (component-local constants, NOT global tolerances -- contract
tolerances stay 12 keys):
- MATCH 15 deg, MARGIN 8 deg, REJECT 35 deg (CP angular distance, n=5/6).
- n=4 uses signed-q semantics (Angstrom gap, never degrees): flat when
  q/rbar <= 0.05 (explicit-P numeric planar domain); otherwise sign decides
  B+/B-.
- Flat gate (all n): q/rbar (n=4: q/rbar; n=5: q2/rbar; n=6: Q/rbar) <
  phase_defined_q_min (0.05) -> flat, confidence ambiguous (unless the
  calling stage knows the spec explicitly declares P).

Canonical StateKey rule: state identity is (form family + index + anchor +
direction); measured CP, torsions, distances, margins and boundary flags
ride in diagnostics. Geometry/parent-lock audits run against measured CP
via cp_distance (n=5/6) or sign+q gate (n=4), never against snapped evidence.

Dependencies: stdlib + NumPy + puckering only (no template I/O).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

import numpy as np

from .geometry import ring_torsions
from .puckering import CanonicalForm, CPCoords, canonical_forms, cp_distance, cremer_pople

__all__ = [
    "COMMAND_TOL_DEG_DEFAULT",
    "MATCH_DEG_DEFAULT",
    "MARGIN_DEG_DEFAULT",
    "REJECT_DEG_DEFAULT",
    "RingPerception",
    "commanded_state_dict",
    "perceive_ring",
    "ring_diagnostics",
    "ring_state_dict",
    "ring_states_match",
]

#: Best-match CP distance at or below this claims a form (degrees, n=5/6).
MATCH_DEG_DEFAULT = 15.0
#: Runner-up closer than this to the winner forces ambiguity (degrees).
MARGIN_DEG_DEFAULT = 8.0
#: Best-match CP distance above this is unrecognized (degrees).
REJECT_DEG_DEFAULT = 35.0
#: Default per-torsion tolerance when matching commanded vs observed states.
#: Kept for backward-compatible alias paths; CP audits use MATCH (15 deg).
COMMAND_TOL_DEG_DEFAULT = 15.0

#: Dimensionless flat gate (mirrors ConfgenTolerances.phase_defined_q_min;
#: kept local so contract/schema serialization is unchanged).
_PHASE_DEFINED_Q_MIN = 0.05


@dataclass(frozen=True, slots=True)
class RingPerception:
    """Perception outcome for one ring system (R4 CP form)."""

    ring_size: int
    best_template: str | None
    best_distance_deg: float
    direction: str
    torsions_deg: tuple[float, ...]
    alternatives: tuple[tuple[str, float], ...]
    confidence: str
    margin_deg: float
    boundary_flags: tuple[str, ...] = ()
    measured_cp: CPCoords | None = None
    q_over_rbar: float = float("inf")
    best_form: CanonicalForm | None = None


def _rbar_of(coords: np.ndarray) -> float:
    xyz = np.asarray(coords, dtype=float)
    n = xyz.shape[0]
    total = 0.0
    for k in range(n):
        total += float(np.linalg.norm(xyz[(k + 1) % n] - xyz[k]))
    return float(total / n) if n else float("inf")


def _precise_name(form: CanonicalForm) -> str:
    if form.family == "P" and form.n in (5, 6):
        return "P_0"
    return f"{form.family}_{form.index}"


def perceive_ring(
    ring_coords: np.ndarray,
    *,
    match_deg: float = MATCH_DEG_DEFAULT,
    ambiguity_margin_deg: float = MARGIN_DEG_DEFAULT,
    reject_deg: float = REJECT_DEG_DEFAULT,
) -> RingPerception:
    """Perceive the CP form of one ordered ring geometry.

    Parameters
    ----------
    ring_coords:
        Ordered ``(n, 3)`` positions; index 0 is the traversal anchor.
    """
    coords = np.asarray(ring_coords, dtype=float)
    if coords.ndim != 2 or coords.shape[1] != 3:
        raise ValueError(f"ring_coords must be (n,3), got {coords.shape}")
    ring_size = coords.shape[0]
    if ring_size not in (4, 5, 6):
        return RingPerception(
            ring_size=ring_size,
            best_template=None,
            best_distance_deg=float("inf"),
            direction="as_given",
            torsions_deg=tuple(float(v) for v in ring_torsions(coords)),
            alternatives=(),
            confidence="ambiguous",
            margin_deg=0.0,
            boundary_flags=("unsupported_ring_size",),
            measured_cp=None,
            q_over_rbar=float("inf"),
            best_form=None,
        )
    measured_tors = tuple(float(v) for v in ring_torsions(coords))
    try:
        cp = cremer_pople(coords)
    except ValueError:
        return RingPerception(
            ring_size=ring_size,
            best_template=None,
            best_distance_deg=float("inf"),
            direction="as_given",
            torsions_deg=measured_tors,
            alternatives=(),
            confidence="ambiguous",
            margin_deg=0.0,
            boundary_flags=("degenerate_cp",),
            measured_cp=None,
            q_over_rbar=float("inf"),
            best_form=None,
        )
    rbar = _rbar_of(coords)
    qor = float(cp.q / rbar) if rbar and rbar != float("inf") else float("inf")
    # Flat gate first (planar P domain for n=4; flat/ambiguous for n=5/6).
    if not (qor >= _PHASE_DEFINED_Q_MIN):
        if ring_size == 4:
            # Planar 4-ring is the real P form (reported, not ambiguous).
            forms = canonical_forms(4)
            best = next(f for f in forms if f.family == "P")
            return RingPerception(
                ring_size=4,
                best_template=_precise_name(best),
                best_distance_deg=0.0,
                direction="as_given",
                torsions_deg=measured_tors,
                alternatives=tuple(
                    (_precise_name(f), float(cp_distance(cp, f.cp_target)))
                    for f in forms
                    if f is not best
                )[:3],
                confidence="reported",
                margin_deg=float("inf"),
                boundary_flags=(),
                measured_cp=cp,
                q_over_rbar=qor,
                best_form=best,
            )
        return RingPerception(
            ring_size=ring_size,
            best_template=None,
            best_distance_deg=float("inf"),
            direction="as_given",
            torsions_deg=measured_tors,
            alternatives=(),
            confidence="ambiguous",
            margin_deg=0.0,
            boundary_flags=("flat",),
            measured_cp=cp,
            q_over_rbar=qor,
            best_form=None,
        )
    if ring_size == 4:
        # Non-planar 4-ring: sign decides B+/B- (Å semantics, never degrees).
        want = 1 if cp.sign > 0 else -1
        forms = canonical_forms(4)
        best = next(f for f in forms if f.cp_target.sign == want)
        alts = tuple(
            (_precise_name(f), float(cp_distance(cp, f.cp_target))) for f in forms if f is not best
        )
        return RingPerception(
            ring_size=4,
            best_template=_precise_name(best),
            best_distance_deg=float(cp_distance(cp, best.cp_target)),
            direction="as_given",
            torsions_deg=measured_tors,
            alternatives=alts[:3],
            confidence="reported",
            margin_deg=float("inf"),
            boundary_flags=(),
            measured_cp=cp,
            q_over_rbar=qor,
            best_form=best,
        )
    # n=5/6: nearest regular form by CP distance.
    pool = list(canonical_forms(ring_size))
    scored = sorted(
        ((f, float(cp_distance(cp, f.cp_target))) for f in pool),
        key=lambda t: (t[1], _precise_name(t[0])),
    )
    best, best_dist = scored[0]
    alternatives = tuple((_precise_name(f), d) for f, d in scored[1:4])
    margin = float(scored[1][1] - best_dist) if len(scored) > 1 else float("inf")
    flags: list[str] = []
    if best_dist > reject_deg:
        confidence = "ambiguous"
        flags.append("unrecognized")
    elif best_dist > match_deg:
        confidence = "ambiguous"
        flags.append("dead_zone")
    elif margin < ambiguity_margin_deg:
        confidence = "ambiguous"
        flags.append("small_margin")
    else:
        confidence = "reported"
    return RingPerception(
        ring_size=ring_size,
        best_template=_precise_name(best),
        best_distance_deg=float(best_dist),
        direction="as_given",
        torsions_deg=measured_tors,
        alternatives=alternatives,
        confidence=confidence,
        margin_deg=float(margin),
        boundary_flags=tuple(flags),
        measured_cp=cp,
        q_over_rbar=qor,
        best_form=best,
    )


def ring_state_dict(perception: RingPerception, *, anchor: int) -> dict[str, object]:
    """Return the canonical R4 state-identity dict for one ring system.

    Identity ONLY: form family, index within the family, anchor (global
    0-based atom id of traversal position 0), traversal direction. Noisy
    near-form geometries sharing one discrete declaration share one key.
    Measured CP/torsions are diagnostics, never key content.
    """
    if perception.best_form is None or perception.best_template is None:
        # Flat n=5/6 (or degenerate): emit an explicit flat marker keyed by
        # anchor so accounting stays honest; audits route it to ambiguous.
        return {
            "form": "flat",
            "index": 0,
            "anchor": anchor,
            "direction": perception.direction,
        }
    form = perception.best_form
    # Synthetic P for n=5/6 reports family P index 0.
    return {
        "form": form.family,
        "index": form.index,
        "anchor": anchor,
        "direction": perception.direction,
    }


def commanded_state_dict(template_name: str, *, anchor: int) -> dict[str, object]:
    """Return the narrow commanded state for an enumeration target (R4).

    Accepts precise form names ("B_2", "C_0", "P_0", "B+_0", ...) and Q3
    old-template aliases ("chair_A_6", "boat_6", ...; "twist_5" warns
    deprecated). Family selectors ("B") are rejected here (use enumeration
    expansion); they name many forms, not one target.
    """
    from .forms import alias_templates_to_forms, form_by_name

    token = str(template_name)
    # Try precise form first (needs size; infer from token registry).
    resolved = None
    last_exc: Exception | None = None
    for n in (4, 5, 6):
        try:
            resolved = form_by_name(token, n)
            break
        except KeyError as exc:
            last_exc = exc
            continue
    if resolved is None:
        # Try old-template alias (single-form aliases only).
        for n in (4, 5, 6):
            try:
                forms = alias_templates_to_forms([token], n)
                if len(forms) == 1:
                    resolved = forms[0]
                    break
            except KeyError:
                continue
    if resolved is None:
        raise KeyError(f"unknown ring form/template {token!r}") from last_exc
    if resolved.family == "P" and resolved.n in (5, 6):
        return {"form": "P", "index": 0, "anchor": anchor, "direction": "as_given"}
    return {
        "form": resolved.family,
        "index": resolved.index,
        "anchor": anchor,
        "direction": "as_given",
    }


def ring_diagnostics(perception: RingPerception) -> dict[str, object]:
    """Return perception diagnostics (evidence, never state identity).

    Carries measured CP (family/index-independent), q/rbar, distances,
    margins, boundary flags and alternatives. Geometry and parent-lock
    audits run against measured CP; nothing is snapped before verification.
    """
    cp = perception.measured_cp
    if cp is not None:
        measured_cp: dict[str, object] | None = {
            "n": cp.n,
            "q": round(float(cp.q), 6),
            "theta": round(float(cp.theta), 6),
            "phi": round(float(cp.phi), 6),
            "sign": int(cp.sign),
            "q_over_rbar": (
                round(float(perception.q_over_rbar), 6)
                if perception.q_over_rbar != float("inf")
                else None
            ),
        }
    else:
        measured_cp = None
    return {
        "confidence": perception.confidence,
        "measured_torsions": [round(value, 6) for value in perception.torsions_deg],
        "measured_cp": measured_cp,
        "distance_deg": (
            round(perception.best_distance_deg, 6)
            if perception.best_distance_deg != float("inf")
            else None
        ),
        "margin_deg": (
            round(perception.margin_deg, 6) if perception.margin_deg != float("inf") else None
        ),
        "boundary_flags": list(perception.boundary_flags),
        "alternatives": [
            {"template": name, "distance_deg": round(dist, 6)}
            for name, dist in perception.alternatives
        ],
    }


def ring_states_match(
    commanded: object,
    observed: object,
    *,
    torsion_atol_deg: float = COMMAND_TOL_DEG_DEFAULT,
) -> tuple[bool, dict[str, object]]:
    """Compare commanded vs observed R4 ring states (form identity).

    Form, index, anchor and direction must agree exactly. Old
    template-shaped keys ({"template",...}) are compared by template name
    for backward-compatible alias paths. CP-distance evidence is attached
    when both sides carry it; torsion tolerance is retained only for the
    legacy template-shaped fallback.
    """
    from .geometry import circular_distance_deg

    evidence: dict[str, object] = {"torsion_atol_deg": torsion_atol_deg}
    if not isinstance(commanded, Mapping) or not isinstance(observed, Mapping):
        evidence["reason"] = "non_mapping_state"
        return False, evidence
    # R4 form-shaped keys.
    if "form" in commanded or "form" in observed:
        for key in ("form", "index", "anchor", "direction"):
            if commanded.get(key) != observed.get(key):
                evidence["reason"] = f"mismatch:{key}"
                evidence["commanded"] = commanded.get(key)
                evidence["observed"] = observed.get(key)
                return False, evidence
        return True, evidence
    # Legacy template-shaped fallback (alias rewrite period).
    for key in ("template", "anchor", "direction"):
        if commanded.get(key) != observed.get(key):
            evidence["reason"] = f"mismatch:{key}"
            evidence["commanded"] = commanded.get(key)
            evidence["observed"] = observed.get(key)
            return False, evidence
    commanded_tors = commanded.get("torsions")
    observed_tors = observed.get("torsions")
    if (
        not isinstance(commanded_tors, (list, tuple))
        or not isinstance(observed_tors, (list, tuple))
        or len(commanded_tors) != len(observed_tors)
    ):
        evidence["reason"] = "torsion_shape"
        return False, evidence
    worst = 0.0
    for first, second in zip(commanded_tors, observed_tors):
        try:
            worst = max(worst, circular_distance_deg(float(first), float(second)))
        except (TypeError, ValueError):
            evidence["reason"] = "non_numeric_torsion"
            return False, evidence
    evidence["worst_torsion_deg"] = round(worst, 6)
    if worst > torsion_atol_deg:
        evidence["reason"] = "torsion_outside_tolerance"
        return False, evidence
    return True, evidence
