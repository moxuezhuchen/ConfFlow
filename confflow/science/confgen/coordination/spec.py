#!/usr/bin/env python3

"""Coordination spec normalization (FIX-1A A4b, moved from planner).

Owns the ``coordination`` top-level key. Body moved verbatim from
``planner.normalize_spec`` (coordination block) plus ``_convert_coordination``
(moved, AST-identical apart from module path). Error strings and order
unchanged. Topology overlay (``_overlay_declared_coordination_scope``) moved
here as ``contribute_topology`` in A4c (body verbatim, only the five locals
become ``build.*``); edge order/digest/messages unchanged.

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

__all__ = ["contribute_topology", "normalize_spec", "validate_context"]


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


def contribute_topology(resolved: Mapping[str, Any], build: Any) -> None:
    """Authoritatively type declared metal-site edges (perceived path only).

    A4c move of ``planner._overlay_declared_coordination_scope``: body
    verbatim apart from the five locals becoming ``build.*``
    (``n_atoms``/``adjacency``/``typed``/``explicit_covalent`` plus the
    shared ``check_index`` which this overlay does not need). Distance
    perception guesses every close pair COVALENT, including metal-ligand
    contacts. The declared coordination scope (metal_center plus
    binding-site donor atoms, internal 0-based) overrides the guess: each
    metal-donor pair loses its guessed COVALENT edge/adjacency and gains a
    COORDINATION record, so fragment decomposition and ring/torsion
    mechanics never see metal-ligand pseudo-bonds. An explicit COVALENT
    declaration for a declared metal-donor pair is a contradiction and
    fails closed; perception guesses are overridden silently. Pairs never
    declared stay exactly as perceived/corrected.
    """
    from confflow.science.confgen.graph import EdgeType, TypedEdge

    n_atoms = build.n_atoms
    adjacency = build.adjacency
    typed = build.typed
    explicit_covalent = build.explicit_covalent
    coordination = resolved.get("coordination") if isinstance(resolved, Mapping) else None
    if not isinstance(coordination, Mapping):
        return
    metal = coordination.get("metal_center")
    if metal is None:
        return
    if isinstance(metal, bool) or not isinstance(metal, int):
        raise ValueError(f"resolved coordination metal_center malformed: {metal!r}")
    if metal < 0 or metal >= n_atoms:
        raise ValueError(f"resolved coordination metal_center out of range: {metal!r}")
    sites = coordination.get("binding_sites")
    if sites is None:
        return
    if not isinstance(sites, (list, tuple)):
        raise ValueError("$.coordination.binding_sites must be a list")
    donors: set[int] = set()
    for position, site in enumerate(sites):
        if not isinstance(site, Mapping):
            raise ValueError(f"$.coordination.binding_sites[{position}] must be a mapping")
        for atom in site.get("atoms") or []:
            if isinstance(atom, bool) or not isinstance(atom, int):
                raise ValueError(
                    f"$.coordination.binding_sites[{position}].atoms holds "
                    f"a non-integer index {atom!r}"
                )
            if atom < 0 or atom >= n_atoms:
                raise ValueError(
                    f"$.coordination.binding_sites[{position}] index {atom} "
                    f"out of range for {n_atoms} atoms"
                )
            donors.add(int(atom))
    for donor in sorted(donors):
        if donor == metal:
            raise ValueError("coordination scope binds the metal to itself")
        pair = (min(metal, donor), max(metal, donor))
        if pair in explicit_covalent:
            raise ValueError(
                f"topology declares COVALENT for pair {pair} inside the declared "
                "coordination scope; contradictory kinds for one pair fail closed"
            )
        typed.pop((pair[0], pair[1], EdgeType.COVALENT), None)
        adjacency[metal].discard(donor)
        adjacency[donor].discard(metal)
        typed[(pair[0], pair[1], EdgeType.COORDINATION)] = TypedEdge(
            a=pair[0],
            b=pair[1],
            type=EdgeType.COORDINATION,
            provenance="declared-binding-site",
        )
