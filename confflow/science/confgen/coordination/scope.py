#!/usr/bin/env python3

"""Coordination scope helpers (FIX-1A A3).

Component-owned slices moved verbatim from the engine's report-scope
helpers: the ``donor_configuration`` report fragment and the scope
descriptor slice. Coordination holds no ``preserve_input`` entries in
the engine report (its ``preserve_input`` treatment deactivates the
level). The engine merges each stage's fragment generically; the wire
adapter only converts key payloads.
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
    """Coordination contributes no preserved entries (matches engine)."""
    _ = resolved
    return []


def report_section(resolved: Mapping[str, Any]) -> dict[str, Any] | None:
    """Return the coordination report fragment (moved verbatim from engine)."""
    return {
        "donor_configuration": (
            dict(resolved.get("coordination") or {})
            if resolved.get("coordination") is not None
            else None
        )
    }


def describe_scope(resolved: Mapping[str, Any]) -> Mapping[str, Any] | None:
    """Return the coordination scope slice of the resolved spec, if present."""
    coordination = resolved.get("coordination")
    if isinstance(coordination, Mapping) and coordination is not None:
        return {"coordination": dict(coordination)}
    return None
