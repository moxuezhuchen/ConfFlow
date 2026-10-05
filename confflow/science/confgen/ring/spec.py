#!/usr/bin/env python3

"""Ring spec normalization (FIX-1A A4b, moved from planner).

Owns the ``rings`` top-level key. Loop moved verbatim from
``planner.normalize_spec`` (rings block). Error strings and order unchanged.

Interface note (root compat ruling): returns ONLY ``{"rings": ...}`` when the
key was present in the input; missing keys are filled with the historical
default (``rings -> []``) by the planner so default output bytes stay
identical.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

__all__ = ["has_indices", "normalize_spec", "validate_context"]


def has_indices(raw: Mapping[str, Any]) -> bool:
    """Return True when raw rings carry index-bearing content.

    Mirrors the pre-AG1 ``planner._spec_has_indices`` rings branch
    exactly (never raises; illegal shapes are False).
    """
    rings = raw.get("rings")
    if isinstance(rings, (list, tuple)) and len(rings) > 0:
        return True
    return False


def normalize_spec(raw: Mapping[str, Any], *, index_base: int) -> Mapping[str, Any]:
    """Normalize the owned ``rings`` key (structure-independent)."""
    if "rings" not in raw:
        return {}
    from confflow.science.confgen.planner import _convert_index_list

    rings = raw.get("rings", [])
    if not isinstance(rings, (list, tuple)):
        raise ValueError("spec rings must be a list of ring declarations")
    ring_entries: list[dict[str, Any]] = []
    for index, entry in enumerate(rings):
        if not isinstance(entry, Mapping):
            raise ValueError(f"$.rings[{index}] must be a mapping")
        if "index_base" in entry:
            raise ValueError(
                "index convention is top-level only; ring entries must not declare index_base"
            )
        converted = dict(entry)
        if entry.get("atoms") is not None:
            converted["atoms"] = _convert_index_list(
                entry["atoms"], base=index_base, path=f"$.rings[{index}].atoms"
            )
        ring_entries.append(converted)
    return {"rings": ring_entries}


def validate_context(resolved: Mapping[str, Any], context: Any) -> None:
    """Context-stage validation owned by ring (none at A4b; always passes)."""
    return None
