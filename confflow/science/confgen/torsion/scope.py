#!/usr/bin/env python3

"""Torsion scope helpers (FIX-1A A3).

Component-owned slices moved verbatim from the engine's scope/report
helpers: ``preserve_input`` entry scan, scope descriptor slice. Torsion
owns its ``verify_locked`` hook, so no fallback matcher is needed here
(the engine's generic exact comparison only serves legacy stages
without hooks, preserving the pre-existing outcome). The engine calls
these via the stage/descriptor hooks generically.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

__all__ = [
    "describe_scope",
    "preserved_entries",
    "report_section",
]


def preserved_entries(resolved: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Return torsion ``preserve_input`` entries (moved verbatim from engine)."""
    preserved: list[dict[str, Any]] = []
    for entry in resolved.get("torsions", []) or []:
        if isinstance(entry, Mapping) and entry.get("treatment") == "preserve_input":
            preserved.append({"axis": "torsions", "id": entry.get("id")})
    return preserved


def report_section(resolved: Mapping[str, Any]) -> dict[str, Any] | None:
    """Torsion contributes no report section."""
    _ = resolved
    return None


def describe_scope(resolved: Mapping[str, Any]) -> Mapping[str, Any] | None:
    """Return the torsion scope slice of the resolved spec, if present."""
    torsions = resolved.get("torsions", [])
    if isinstance(torsions, (list, tuple)) and len(torsions) > 0:
        return {"torsions": list(torsions)}
    return None
