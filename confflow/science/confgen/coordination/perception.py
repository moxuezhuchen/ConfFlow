#!/usr/bin/env python3
"""Coordination perception audit: assign realized frames to shape classes.

Pure science: stdlib + NumPy only.

For a realized geometry, perception extracts donor directions about the metal
center, scales the ideal template by the mean metal-donor distance, and finds
the vertex assignment minimizing the proper-Kabsch RMSD.  The report carries
the best key (stage-local site-to-vertex state), alternatives within a margin
window, confidence, margins, and boundary flags.  Ambiguity — a small margin
or a large best RMSD — prohibits suppression or publication of a claimed
unambiguous state; the frame is reported with alternatives instead.

Existing SCINE frames audit perception but never substitute for generated
targets: every realized structure in the audit trail must come from the
geometric realization backend.
"""

from __future__ import annotations

import itertools
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

import numpy as np

from .enumeration import canonical_representative, command_key
from .shapes import get_shape, kabsch_rmsd, proper_rotation_group

__all__ = [
    "PerceptionResult",
    "FrameAudit",
    "perceive_donors",
    "audit_frames",
    "donor_directions",
]


@dataclass(frozen=True, slots=True)
class PerceptionResult:
    """Per-frame assignment audit.

    ``best_key`` is the canonical shape-class assignment (rotation-gauge
    invariant, matching enumeration state form); ``labeled_key`` is the
    gauge-explicit best labeled placement with identical RMSD.
    """

    best_key: dict[str, str]
    best_rmsd: float
    alternatives: tuple[dict[str, Any], ...]
    confidence: float
    margin: float
    boundary_flags: tuple[str, ...]
    shape: str
    unambiguous: bool
    best_class: tuple[int, ...] = ()
    labeled_key: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-compatible record."""
        return {
            "best_key": dict(self.best_key),
            "best_rmsd": self.best_rmsd,
            "alternatives": [dict(item) for item in self.alternatives],
            "confidence": self.confidence,
            "margin": self.margin,
            "boundary_flags": list(self.boundary_flags),
            "shape": self.shape,
            "unambiguous": self.unambiguous,
            "best_class": list(self.best_class),
            "labeled_key": dict(self.labeled_key),
        }


@dataclass(frozen=True, slots=True)
class FrameAudit:
    """Named-frame perception outcome."""

    name: str
    result: PerceptionResult
    expected_key: dict[str, str] | None = None
    matches_expected: bool | None = None

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-compatible record."""
        return {
            "name": self.name,
            "result": self.result.to_dict(),
            "expected_key": dict(self.expected_key) if self.expected_key is not None else None,
            "matches_expected": self.matches_expected,
        }


def donor_directions(
    coordinates: np.ndarray, metal: int, donors: Sequence[int]
) -> tuple[np.ndarray, np.ndarray, float]:
    """Return centered donor offsets, unit directions, and mean bond length."""
    coords = np.asarray(coordinates, dtype=float)
    center = coords[metal]
    offsets = np.array([coords[d] - center for d in donors])
    lengths = np.linalg.norm(offsets, axis=1)
    if bool(np.any(lengths < 1e-9)):
        raise ValueError("degenerate metal-donor distance in perception input")
    mean_length = float(np.mean(lengths))
    return offsets, offsets / lengths[:, None], mean_length


def perceive_donors(
    coordinates: np.ndarray,
    metal: int,
    donors: Sequence[int],
    site_ids: Sequence[str],
    shape_name: str,
    rmsd_tolerance: float = 0.35,
    margin_tolerance: float = 0.05,
    alternative_window: float = 0.15,
    max_assignments: int = 720,
) -> PerceptionResult:
    """Assign donor geometry to labeled vertex placements for one shape.

    Every labeled placement (up to *max_assignments*) is fitted; the best RMSD
    wins.  ``unambiguous`` requires the best RMSD within *rmsd_tolerance* and
    a margin to the runner-up of at least *margin_tolerance*.
    """
    template = get_shape(shape_name)
    count = len(donors)
    if count != template.coordination_number:
        raise ValueError(f"shape {shape_name} needs {template.coordination_number} donors")
    offsets, _, mean_length = donor_directions(coordinates, metal, donors)
    scaled = np.asarray(template.vertices, dtype=float) * mean_length
    sites = list(site_ids)
    scored: list[tuple[float, tuple[int, ...]]] = []
    total = 0
    for placement in itertools.permutations(range(count)):
        total += 1
        if total > max_assignments:
            raise ValueError("assignment enumeration exceeded max_assignments budget")
        target = scaled[list(placement)]
        rmsd, _ = kabsch_rmsd(offsets, target)
        scored.append((rmsd, tuple(placement)))
    scored.sort(key=lambda item: (item[0], item[1]))
    # Class-level accounting: placements differing by a proper rotation fit
    # identically (rotation gauge), so margins and alternatives are computed
    # over distinct shape classes, not labeled placements.
    group = proper_rotation_group(shape_name)
    by_class: dict[tuple[int, ...], tuple[float, tuple[int, ...]]] = {}
    for rmsd, placement in scored:
        rep = canonical_representative(placement, group)
        if rep not in by_class or rmsd < by_class[rep][0]:
            by_class[rep] = (rmsd, placement)
    ranked = sorted(by_class.items(), key=lambda item: (item[1][0], item[0]))
    best_rep, (best_rmsd, best_placement) = ranked[0]
    runner_rmsd = ranked[1][1][0] if len(ranked) > 1 else float("inf")
    margin = float(runner_rmsd - best_rmsd)
    labeled_key = {sites[i]: template.vertex_names[best_placement[i]] for i in range(count)}
    # Unified OBSERVED key: center/shape/canonical placement/indexed mapping —
    # the same keys and types enumeration commands, so core parent locks
    # compare indexed identity (class display ids stay out of the key).
    best_key = command_key(metal, shape_name, [int(v) for v in best_rep], sites)
    alternatives = tuple(
        {
            "key": {sites[i]: template.vertex_names[placement[i]] for i in range(count)},
            "rmsd": rmsd,
            "shape_class": list(rep),
        }
        for rep, (rmsd, placement) in ranked[1:]
        if rmsd - best_rmsd <= alternative_window
    )
    flags: list[str] = []
    if best_rmsd > rmsd_tolerance:
        flags.append("HIGH_BEST_RMSD")
    if not margin >= margin_tolerance:
        flags.append("SMALL_MARGIN")
    if alternatives:
        flags.append("HAS_ALTERNATIVES")
    confidence = float(min(1.0, max(0.0, 1.0 - best_rmsd / max(rmsd_tolerance, 1e-12))))
    if margin < float("inf"):
        confidence = float(min(confidence, min(1.0, margin / max(margin_tolerance, 1e-12))))
    unambiguous = not flags
    return PerceptionResult(
        best_key=best_key,
        best_rmsd=float(best_rmsd),
        alternatives=alternatives,
        confidence=confidence,
        margin=float(margin),
        boundary_flags=tuple(flags),
        shape=shape_name,
        unambiguous=bool(unambiguous),
        best_class=tuple(int(v) for v in best_rep),
        labeled_key=labeled_key,
    )


def audit_frames(
    frames: Sequence[tuple[str, np.ndarray]],
    metal: int,
    donors: Sequence[int],
    site_ids: Sequence[str],
    shape_name: str,
    expected: dict[str, dict[str, str]] | None = None,
    **kwargs: Any,
) -> list[FrameAudit]:
    """Perceive named frames and compare against expected keys when given."""
    audits: list[FrameAudit] = []
    for name, coords in frames:
        result = perceive_donors(coords, metal, donors, site_ids, shape_name, **kwargs)
        expected_key = expected.get(name) if expected else None
        match: bool | None = None
        if expected_key is not None:
            match = result.best_key == expected_key
        audits.append(
            FrameAudit(name=name, result=result, expected_key=expected_key, matches_expected=match)
        )
    return audits
