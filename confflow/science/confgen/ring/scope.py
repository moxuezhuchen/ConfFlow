#!/usr/bin/env python3

"""Ring scope helpers (FIX-1A A3).

Component-owned slices moved verbatim from the engine's scope/report and
lock-fallback helpers: ``preserve_input`` entry scan, scope descriptor
slice, and the tolerance-aware ancestor-lock fallback matcher
(byte-identical labels, tolerances, and evidence keys). The engine calls
these via the stage/descriptor hooks generically; it never mentions the
``"rings"`` literal itself.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

__all__ = [
    "describe_scope",
    "fallback_lock",
    "preserved_entries",
    "report_section",
]


def preserved_entries(resolved: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Return ring ``preserve_input`` entries (moved verbatim from engine)."""
    preserved: list[dict[str, Any]] = []
    for entry in resolved.get("rings", []) or []:
        if isinstance(entry, Mapping) and entry.get("treatment") == "preserve_input":
            preserved.append({"axis": "rings", "id": entry.get("id")})
    return preserved


def report_section(resolved: Mapping[str, Any]) -> dict[str, Any] | None:
    """Ring contributes no report section."""
    _ = resolved
    return None


def describe_scope(resolved: Mapping[str, Any]) -> Mapping[str, Any] | None:
    """Return the ring scope slice of the resolved spec, if present."""
    rings = resolved.get("rings", [])
    if isinstance(rings, (list, tuple)) and len(rings) > 0:
        return {"rings": list(rings)}
    return None


def fallback_lock(
    stage: Any,
    locked_state: Mapping[str, Any],
    structure: Any,
    context: Any,
) -> tuple[str, list[dict[str, Any]], dict[str, Any], Any]:
    """Verify ring ancestor locks without a stage hook (moved verbatim).

    Byte-identical to the engine's former ``axis == "rings"`` branch:
    perceive via the stage, ambiguous perception stays ambiguous,
    unmeasured ids stay ambiguous, tolerance-aware match over measured
    states with ``rings.<id>`` labels and template-mismatch
    out-of-scope flags. When ``ring_states_match`` cannot be imported,
    fall back to the original generic missing/drift/exact discrete
    comparison over the already-perceived ``best`` (no second perceive
    call; component owns the ``rings.<key>`` label).
    """
    from confflow.science.confgen.engine import _RunState

    try:
        perception = stage.perceive(structure, context)
    except Exception as exc:
        return (
            "ambiguous",
            [
                {
                    "kind": "anomaly",
                    "anomaly": "AMBIGUOUS_KEY",
                    "detail": f"lock perception raised {type(exc).__name__}",
                }
            ],
            {},
            "unknown",
        )
    best = dict(perception.best_key)
    if perception.confidence == "ambiguous":
        return (
            "ambiguous",
            [
                {
                    "kind": "anomaly",
                    "anomaly": "AMBIGUOUS_KEY",
                    "detail": "ancestor lock perception ambiguous",
                }
            ],
            {},
            "unknown",
        )
    try:
        from confflow.science.confgen.ring.perception import ring_states_match
    except ImportError:
        ring_states_match = None  # type: ignore[assignment]
    if ring_states_match is not None:
        drifted: list[dict[str, Any]] = []
        for ring_id, commanded in locked_state.items():
            observed = best.get(ring_id)
            if not isinstance(observed, Mapping):
                return (
                    "ambiguous",
                    [
                        {
                            "kind": "anomaly",
                            "anomaly": "AMBIGUOUS_KEY",
                            "detail": f"ancestor ring {ring_id!r} unmeasured",
                        }
                    ],
                    {},
                    "unknown",
                )
            match, match_evidence = ring_states_match(commanded, observed)
            if not match:
                payload: dict[str, Any] = {
                    "kind": "drift",
                    "axis": f"rings.{ring_id}",
                    "detail": "ancestor ring lock drifted",
                    "match_evidence": dict(match_evidence),
                    "observed": dict(observed),
                }

                def _form_key(m: Any) -> Any:
                    if not isinstance(m, Mapping):
                        return True
                    if "form" in m:
                        return (m.get("form"), m.get("index"))
                    return m.get("template")

                payload["out_of_scope"] = bool(
                    _form_key(observed) != _form_key(commanded)
                    if isinstance(commanded, Mapping)
                    else True
                )
                drifted.append(payload)
        if drifted:
            return "drift", drifted, {}, _RunState._aggregate_oos(drifted)
        return "ok", [], {}, False
    # ring_states_match unavailable (ImportError): original A2 behavior
    # falls through to the generic discrete comparison over the
    # already-perceived best (no second perceive call).
    missing = [key for key in locked_state if key not in best]
    if missing:
        return (
            "ambiguous",
            [
                {
                    "kind": "anomaly",
                    "anomaly": "AMBIGUOUS_KEY",
                    "detail": f"ancestor lock axes unmeasured: {missing}",
                }
            ],
            {},
            "unknown",
        )
    drifted_generic: list[dict[str, Any]] = []
    for key, expected in locked_state.items():
        if best.get(key) != expected:
            drifted_generic.append(
                {
                    "kind": "drift",
                    "axis": f"rings.{key}",
                    "detail": "ancestor lock drifted",
                    "expected": expected,
                    "measured": best.get(key),
                    "out_of_scope": "unknown",
                }
            )
    if drifted_generic:
        return (
            "drift",
            drifted_generic,
            {k: best[k] for k in locked_state},
            "unknown",
        )
    return "ok", [], {}, False
