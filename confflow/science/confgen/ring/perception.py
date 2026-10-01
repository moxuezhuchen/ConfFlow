#!/usr/bin/env python3

"""ConfGen v3 ring-lane perception: measured ring state vs declared templates.

A measured ring (ordered coordinates in traversal order from the anchor) is
compared against every declared template of its size using the periodic RMS
torsion distance in the given traversal direction. Traversal anchor and
direction are stable spec authority: no reversed-direction matching is
attempted, so an oppositely traversed ring scores as a genuinely different
state (usually ambiguous). Explicit dead zones: no template claims authority beyond
``reject_deg`` RMS, and a runner-up within ``ambiguity_margin_deg`` (or a
best match inside the dead zone between ``match_deg`` and ``reject_deg``)
forces ``confidence="ambiguous"`` with alternatives and boundary flags, so
downstream publication of a claimed unambiguous state is prohibited.

Canonical StateKey rule: the state identity emitted by
:func:`ring_state_dict` carries the template id plus the CANONICAL internal
torsion descriptor of that template (identical discrete declarations of
noisy near-template geometries share one key). The ACTUAL measured torsions
live in :func:`ring_diagnostics` and are the quantities used by
geometry/parent-lock audits (see :func:`ring_states_match`); evidence is
never snapped to canonical before the distortion check runs.

Dependencies: stdlib + NumPy only.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

import numpy as np

from .geometry import circular_distance_deg, circular_rms_deg, ring_torsions
from .templates import RingTemplate, get_template, template_torsions, templates_for_size

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

#: Best-match RMS at or below this claims a template (degrees).
MATCH_DEG_DEFAULT = 12.0
#: Runner-up closer than this to the winner forces ambiguity (degrees).
MARGIN_DEG_DEFAULT = 8.0
#: Best-match RMS above this is unrecognized: explicit dead zone (degrees).
REJECT_DEG_DEFAULT = 30.0
#: Default per-torsion tolerance when matching commanded vs observed states.
COMMAND_TOL_DEG_DEFAULT = 12.0


@dataclass(frozen=True, slots=True)
class RingPerception:
    """Perception outcome for one ring system."""

    ring_size: int
    best_template: str | None
    best_distance_deg: float
    direction: str
    torsions_deg: tuple[float, ...]
    alternatives: tuple[tuple[str, float], ...]
    confidence: str
    margin_deg: float
    boundary_flags: tuple[str, ...] = ()


def _score_candidates(
    measured: np.ndarray, templates: tuple[RingTemplate, ...]
) -> list[tuple[str, float]]:
    """Score templates against measured torsions, ascending by distance."""
    scored = [
        (template.name, circular_rms_deg(measured, np.asarray(template_torsions(template))))
        for template in templates
    ]
    scored.sort(key=lambda item: (item[1], item[0]))
    return scored


def perceive_ring(
    ring_coords: np.ndarray,
    *,
    match_deg: float = MATCH_DEG_DEFAULT,
    ambiguity_margin_deg: float = MARGIN_DEG_DEFAULT,
    reject_deg: float = REJECT_DEG_DEFAULT,
) -> RingPerception:
    """Perceive the state of one ordered ring geometry.

    Parameters
    ----------
    ring_coords:
        Ordered ``(n, 3)`` positions; index 0 is the traversal anchor.
    """
    coords = np.asarray(ring_coords, dtype=float)
    if coords.ndim != 2 or coords.shape[1] != 3:
        raise ValueError(f"ring_coords must be (n,3), got {coords.shape}")
    ring_size = coords.shape[0]
    templates = templates_for_size(ring_size)
    if not templates:
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
        )
    measured = ring_torsions(coords)
    scored = _score_candidates(measured, templates)
    best_name, best_dist = scored[0]
    alternatives = tuple(scored[1:4])
    margin = float(alternatives[0][1] - best_dist) if alternatives else float("inf")
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
        best_template=best_name,
        best_distance_deg=float(best_dist),
        direction="as_given",
        torsions_deg=tuple(float(value) for value in measured),
        alternatives=alternatives,
        confidence=confidence,
        margin_deg=float(margin),
        boundary_flags=tuple(flags),
    )


def ring_state_dict(perception: RingPerception, *, anchor: int) -> dict[str, object]:
    """Return the canonical state-identity dict for one ring system.

    Canonical identity ONLY: template id, the CANONICAL internal torsion
    descriptor of that template, anchor, traversal direction. Noisy
    near-template geometries sharing one discrete declaration therefore
    share one StateKey. The actual measured torsions are diagnostics
    (:func:`ring_diagnostics`) and audit quantities, never key content.
    Treatment lives in scope/provenance; confidence, margins, and boundary
    flags live in perception diagnostics, never in the state key.
    """
    if perception.best_template is None:
        torsions: list[float] = []
    else:
        torsions = [
            round(value, 6) for value in template_torsions(get_template(perception.best_template))
        ]
    return {
        "template": perception.best_template,
        "torsions": torsions,
        "anchor": anchor,
        "direction": perception.direction,
    }


def commanded_state_dict(template_name: str, *, anchor: int) -> dict[str, object]:
    """Return the narrow commanded state for an enumeration target.

    Same keys as :func:`ring_state_dict` with the canonical (ideal-template)
    torsion descriptor. Commanded vs observed states must be compared with
    :func:`ring_states_match` (tolerance-aware), never by exact float
    dictionary equality.
    """
    template = get_template(template_name)
    return {
        "template": template_name,
        "torsions": [round(value, 6) for value in template_torsions(template)],
        "anchor": anchor,
        "direction": "as_given",
    }


def ring_diagnostics(perception: RingPerception) -> dict[str, object]:
    """Return perception diagnostics (evidence, never state identity).

    Carries the ACTUAL measured torsion descriptor plus confidence,
    distances, margins, boundary flags, and alternatives. Geometry and
    parent-lock audits run against these measured quantities; the measured
    descriptor is never snapped to canonical before verification.
    """
    return {
        "confidence": perception.confidence,
        "measured_torsions": [round(value, 6) for value in perception.torsions_deg],
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
    """Tolerance-aware comparison of commanded vs observed ring states.

    Template, anchor, and direction must agree exactly; each torsion must
    agree within ``torsion_atol_deg`` under periodic conventions. Supports
    both canonical-vs-canonical key comparison and canonical-vs-measured
    audit comparison (measured descriptors from diagnostics). Returns
    ``(match, evidence)``; never compares float descriptors for exact
    equality.
    """
    evidence: dict[str, object] = {"torsion_atol_deg": torsion_atol_deg}
    if not isinstance(commanded, Mapping) or not isinstance(observed, Mapping):
        evidence["reason"] = "non_mapping_state"
        return False, evidence
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
