#!/usr/bin/env python3

"""Coordination spec normalization (FIX-1A A4b, moved from planner).

Owns the ``coordination`` top-level key. Body moved verbatim from
``planner.normalize_spec`` (coordination block) plus ``_convert_coordination``
(moved, AST-identical apart from module path). Error strings and order
unchanged. Topology overlay (``_overlay_declared_coordination_scope``) stays
in the planner until A4c and is NOT moved here.

Interface note (root compat ruling): component ``normalize_spec`` receives the
whole raw spec plus the resolved top-level ``index_base`` and returns ONLY the
keys it owns that were present in the input. Missing builtin keys are filled
with historical defaults by the planner (``coordination -> None``) so default
output bytes stay identical; custom components must not invent defaults for
undeclared keys.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

__all__ = ["normalize_spec", "validate_context"]


def _convert_coordination(section: Mapping[str, Any], *, base: int) -> dict[str, Any]:
    """Convert documented coordination index fields to internal 0-based.

    Converted fields: ``metal_center``, ``binding_sites[].atoms``. Shapes
    outside this documented contract fail closed when base==1 (never
    silently leave 1-based indices unconverted); deeper semantic validation
    stays lane B owned.
    """
    from confflow.science.confgen.planner import _convert_index, _convert_index_list

    converted = dict(section)
    if section.get("metal_center") is not None:
        converted["metal_center"] = _convert_index(
            section["metal_center"], base=base, path="$.coordination.metal_center"
        )
    sites = section.get("binding_sites")
    if sites is not None:
        if not isinstance(sites, (list, tuple)):
            raise ValueError("$.coordination.binding_sites must be a list")
        converted_sites: list[Any] = []
        for position, site in enumerate(sites):
            if not isinstance(site, Mapping):
                raise ValueError(f"$.coordination.binding_sites[{position}] must be a mapping")
            entry = dict(site)
            if site.get("atoms") is not None:
                entry["atoms"] = _convert_index_list(
                    site["atoms"], base=base, path=f"$.coordination.binding_sites[{position}].atoms"
                )
            converted_sites.append(entry)
        converted["binding_sites"] = converted_sites
    return converted


def normalize_spec(raw: Mapping[str, Any], *, index_base: int) -> Mapping[str, Any]:
    """Normalize the owned ``coordination`` key (structure-independent)."""
    if "coordination" not in raw:
        return {}
    coordination = raw.get("coordination")
    if coordination is not None and not isinstance(coordination, Mapping):
        raise ValueError("spec coordination must be a mapping or null")
    if isinstance(coordination, Mapping) and "index_base" in coordination:
        raise ValueError(
            "index convention is top-level only; coordination must not declare index_base"
        )
    return {
        "coordination": (
            _convert_coordination(coordination, base=index_base)
            if coordination is not None
            else None
        )
    }


def validate_context(resolved: Mapping[str, Any], context: Any) -> None:
    """Context-stage validation owned by coordination (none at A4b).

    Current status: no context-stage (atom-count/graph) checks exist for
    coordination in ``build_context``/``build_typed_graph``; the declared-scope
    overlay stays in the planner until A4c. Always passes; kept explicit so a
    future check has a home without moving normalize-stage errors later
    (which would change error timing).
    """
    return None
