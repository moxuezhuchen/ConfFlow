#!/usr/bin/env python3

"""ConfGen v4 search-spec normalization (search lane).

Minimal science-level authority for the single DG-search engine: normalize a
typed v4 mapping (0/1-based under an explicit ``index_base``) into a resolved
internal-0-based spec, build the lane-B typed graph with bit-identical
construction rules, and resolve the trimmed coordination section into a
lane-B :class:`CoordinationSpec` plus its single commanded shape.

Only v4 keys are accepted: ``schema_version``, ``index_base``, ``seed``,
``topology``, ``tolerances`` (``bond_scale`` only), ``coordination``
(``metal_center``/``binding_sites``/``shapes``/``constraints``) and
``search`` (ignored here; executor-level settings). A ``schema_version: 3``
document fails closed with the removal message shared with the workflow
schema. Removed engine keys (rings/torsions/paths/sampling/limits and the
coordination realization keys) fail closed as unknown keys.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from confflow.science.confgen import graph as _graph
from confflow.science.confgen.graph import CoordinationSpec

__all__ = [
    "DEFAULT_BOND_SCALE",
    "DEFAULT_SEARCH_STARTS_WITH_METAL",
    "DEFAULT_SEARCH_STARTS_WITHOUT_METAL",
    "REMOVED_ENGINES_MESSAGE",
    "SearchContext",
    "build_search_context",
    "normalize_search_spec",
    "resolve_search_coordination",
    "resolve_search_starts",
]

#: Engine-removal message for ``schema_version: 3`` documents. Pinned copy of
#: ``confflow.workflow.v4.confgen_schema.REMOVED_ENGINES_MESSAGE`` (identical
#: text, pinned by test): the science package must not import the workflow
#: package, so the canonical constant cannot be shared by import here.
REMOVED_ENGINES_MESSAGE = (
    "confgen schema_version 3 is no longer supported: the ring, torsion, path "
    "and coordination-realization engines were removed; the confgen step now "
    "runs the DG search engine only, declared with schema_version: 4"
)

#: Default perception scale shared with the legacy typed-graph authority.
DEFAULT_BOND_SCALE: float = 1.15

#: ``starts`` default per coordination class when ``coordination`` is declared.
DEFAULT_SEARCH_STARTS_WITH_METAL: int = 8

#: ``starts`` default in total without a metal center.
DEFAULT_SEARCH_STARTS_WITHOUT_METAL: int = 400

_TOP_LEVEL_KEYS = frozenset(
    {
        "schema_version",
        "index_base",
        "index_convention",
        "seed",
        "topology",
        "tolerances",
        "coordination",
        "search",
    }
)

_TOPO_KEYS = frozenset({"bonds", "add_bond", "del_bond", "atoms"})

_COORD_KEYS = frozenset({"metal_center", "binding_sites", "shapes", "constraints"})


@dataclass(frozen=True, slots=True)
class SearchContext:
    """Immutable search authority for one input structure."""

    adjacency: tuple[tuple[int, ...], ...]
    graph: _graph.TypedGraph
    coordination_section: Mapping[str, Any] | None
    spec: CoordinationSpec | None
    shape: str | None
    seed: int


def _has_indices(raw: Mapping[str, Any]) -> bool:
    """Return True when any v4 index-bearing section carries content."""
    topology = raw.get("topology")
    if isinstance(topology, Mapping):
        for key in ("bonds", "add_bond", "del_bond", "atoms"):
            entries = topology.get(key)
            if isinstance(entries, (list, tuple)) and len(entries) > 0:
                return True
    coordination = raw.get("coordination")
    if isinstance(coordination, Mapping):
        if coordination.get("metal_center") is not None:
            return True
        sites = coordination.get("binding_sites")
        if isinstance(sites, (list, tuple)) and len(sites) > 0:
            return True
    return False


def _check_bond_scale(value: Any) -> float:
    """Validate one bond_scale declaration, return a finite positive float."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"tolerances.bond_scale must be a positive number, got {value!r}")
    scale = float(value)
    if not math.isfinite(scale) or scale <= 0.0:
        raise ValueError(f"tolerances.bond_scale must be a positive number, got {value!r}")
    return scale


def normalize_search_spec(raw: Mapping[str, Any]) -> dict[str, Any]:
    """Normalize a raw v4 spec into a resolved internal-0-based spec.

    The ``search`` key is executor-level and ignored here. Fail-closed on
    unknown keys, bad versions, missing index conventions, and malformed
    topology/tolerance/coordination sections. Graph construction rules
    (explicit bonds win, perception plus add/del corrections, persisted
    working topology, topology patch) live in ``planner.build_typed_graph``
    and are unchanged.
    """
    from confflow.science.confgen.planner import (
        _convert_index,
        _convert_index_list,
        _convert_topo_entry,
        _validate_atom_declaration,
        _validate_topo_entry,
    )

    if not isinstance(raw, Mapping):
        raise ValueError("spec must be a mapping")
    unknown = sorted(set(raw) - _TOP_LEVEL_KEYS)
    if unknown:
        raise ValueError(f"spec holds unknown keys {unknown}; allowed {sorted(_TOP_LEVEL_KEYS)}")
    version = raw.get("schema_version", 4)
    if version == 3:
        raise ValueError(REMOVED_ENGINES_MESSAGE)
    if version != 4:
        raise ValueError(f"spec schema_version must be 4, got {version!r}")

    spec: dict[str, Any] = {"schema_version": 4}

    declared_base = raw.get("index_base")
    if declared_base is not None and declared_base not in (0, 1):
        raise ValueError("spec index_base must be 0 or 1 when declared")
    if declared_base is None and _has_indices(raw):
        raise ValueError(
            "spec carries atom indices but declares no index_base; "
            "declare index_base: 0 or 1 explicitly"
        )
    base = int(declared_base) if declared_base is not None else 0
    marker = raw.get("index_convention")
    if marker is not None and marker != "internal-0-based:normalized":
        raise ValueError(f"spec index_convention marker {marker!r} is not recognized")
    spec["index_convention"] = "internal-0-based:normalized"
    spec["index_base"] = 0

    topology = raw.get("topology", {})
    if not isinstance(topology, Mapping):
        raise ValueError("spec topology must be a mapping")
    unknown_topo = sorted(set(topology) - _TOPO_KEYS)
    if unknown_topo:
        raise ValueError(f"spec topology holds unknown keys {unknown_topo}")
    topo: dict[str, Any] = {}
    for key in ("bonds", "add_bond", "del_bond"):
        if topology.get(key) is not None:
            pairs = topology[key]
            if isinstance(pairs, (str, bytes)) or not isinstance(pairs, Sequence):
                raise ValueError(f"spec topology.{key} must be a list of index entries")
            validated: list[Any] = []
            for index, item in enumerate(pairs):
                shape = _validate_topo_entry(item, path=f"spec topology.{key}[{index}]")
                validated.append(
                    _convert_topo_entry(shape, base=base, path=f"spec topology.{key}[{index}]")
                )
            topo[key] = validated
    if topology.get("atoms") is not None:
        declarations = topology["atoms"]
        if isinstance(declarations, (str, bytes)) or not isinstance(declarations, Sequence):
            raise ValueError("spec topology.atoms must be a list of atom declarations")
        converted_decls: list[dict[str, Any]] = []
        for index, item in enumerate(declarations):
            shape = _validate_atom_declaration(item, path=f"spec topology.atoms[{index}]")
            converted_decls.append(
                {
                    "index": _convert_index(
                        shape["index"], base=base, path=f"spec topology.atoms[{index}].index"
                    ),
                    "label": shape.get("label"),
                    "role": shape.get("role", ""),
                    "stereo": shape.get("stereo"),
                }
            )
        topo["atoms"] = converted_decls
    if "bonds" in topo and ("add_bond" in topo or "del_bond" in topo):
        raise ValueError(
            "explicit topology.bonds wins outright and cannot combine with add/del_bond"
        )
    spec["topology"] = topo

    tolerances = raw.get("tolerances", {})
    if tolerances is None:
        tolerances = {}
    if not isinstance(tolerances, Mapping):
        raise ValueError("spec tolerances must be a mapping")
    unknown_tol = sorted(set(tolerances) - {"bond_scale"})
    if unknown_tol:
        raise ValueError(
            f"spec tolerances holds unknown keys {unknown_tol}; allowed ['bond_scale']"
        )
    scale = tolerances.get("bond_scale", DEFAULT_BOND_SCALE)
    spec["tolerances"] = {"bond_scale": _check_bond_scale(scale)}

    coordination = raw.get("coordination")
    if coordination is not None and not isinstance(coordination, Mapping):
        raise ValueError("spec coordination must be a mapping or null")
    if coordination is None:
        spec["coordination"] = None
    else:
        unknown_coord = sorted(set(coordination) - _COORD_KEYS)
        if unknown_coord:
            raise ValueError(
                f"spec coordination holds unknown keys {unknown_coord}; "
                f"allowed {sorted(_COORD_KEYS)}"
            )
        converted = dict(coordination)
        if coordination.get("metal_center") is not None:
            converted["metal_center"] = _convert_index(
                coordination["metal_center"], base=base, path="$.coordination.metal_center"
            )
        sites = coordination.get("binding_sites")
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
                        site["atoms"],
                        base=base,
                        path=f"$.coordination.binding_sites[{position}].atoms",
                    )
                converted_sites.append(entry)
            converted["binding_sites"] = converted_sites
        spec["coordination"] = converted

    seed = raw.get("seed")
    if seed is not None and (isinstance(seed, bool) or not isinstance(seed, int)):
        raise ValueError(f"$.seed must be an integer or null, got {seed!r}")
    spec["seed"] = seed
    return spec


def resolve_search_coordination(section: Mapping[str, Any]) -> CoordinationSpec:
    """Validate a resolved 0-based coordination section into a lane-B spec.

    Unknown keys fail closed (this rejects the removed realization keys:
    ``backend``, ``budgets``, ``treatment``, ``donor_configuration``,
    ``site_group`` and section ``tolerances``). ``shapes`` must name exactly
    one registered shape; admissibility at the declared coordination number
    is enforced by :class:`CoordinationSpec`.
    """
    if not isinstance(section, Mapping):
        raise ValueError("coordination section must be a mapping")
    unknown = sorted(set(section) - _COORD_KEYS)
    if unknown:
        raise ValueError(
            f"coordination section holds unknown keys {unknown}; allowed {sorted(_COORD_KEYS)}"
        )
    metal = section.get("metal_center")
    if isinstance(metal, bool) or not isinstance(metal, int) or metal < 0:
        raise ValueError(f"coordination metal_center must be a non-negative int, got {metal!r}")
    raw_sites = section.get("binding_sites")
    if not isinstance(raw_sites, (list, tuple)) or not 4 <= len(raw_sites) <= 6:
        raise ValueError("$.coordination.binding_sites must hold 4 to 6 sites")
    sites: list[_graph.BindingSite] = []
    seen: set[str] = set()
    for position, entry in enumerate(raw_sites):
        path = f"$.coordination.binding_sites[{position}]"
        if not isinstance(entry, Mapping):
            raise ValueError(f"{path} must be a mapping")
        unknown_site = sorted(set(entry) - {"id", "kind", "atoms", "hapticity"})
        if unknown_site:
            raise ValueError(f"{path} holds unknown keys {unknown_site}")
        site_id = entry.get("id")
        if not isinstance(site_id, str) or not site_id:
            raise ValueError(f"{path}.id must be a non-empty string")
        if site_id in seen:
            raise ValueError(f"duplicate coordination binding site id {site_id!r}")
        seen.add(site_id)
        if entry.get("kind", "atom") != "atom":
            raise ValueError(f"{path}.kind must be 'atom'")
        atoms = entry.get("atoms")
        if not isinstance(atoms, (list, tuple)) or len(atoms) != 1:
            raise ValueError(f"{path}.atoms must hold exactly one atom index")
        atom = atoms[0]
        if isinstance(atom, bool) or not isinstance(atom, int) or atom < 0:
            raise ValueError(f"{path}.atoms must hold a non-negative int, got {atom!r}")
        if entry.get("hapticity", 1) != 1:
            raise ValueError(f"{path}.hapticity must be 1")
        sites.append(_graph.BindingSite(id=site_id, kind="atom", atoms=(int(atom),), hapticity=1))
    shapes = section.get("shapes")
    if not isinstance(shapes, (list, tuple)) or len(shapes) != 1:
        raise ValueError("$.coordination.shapes must name exactly one shape")
    shape = str(shapes[0])
    if shape == "auto":
        raise ValueError("$.coordination.shapes must name exactly one shape, not 'auto'")
    constraints: list[_graph.ForbiddenTrans] = []
    for position, entry in enumerate(section.get("constraints", []) or []):
        path = f"$.coordination.constraints[{position}]"
        if not isinstance(entry, Mapping):
            raise ValueError(f"{path} must be a mapping")
        unknown_entry = sorted(set(entry) - {"id", "kind", "sites", "classification", "provenance"})
        if unknown_entry:
            raise ValueError(f"{path} holds unknown keys {unknown_entry}")
        if entry.get("kind", "FORBIDDEN_TRANS") != "FORBIDDEN_TRANS":
            raise ValueError(f"{path}.kind must be 'FORBIDDEN_TRANS'")
        pair = entry.get("sites")
        if not isinstance(pair, (list, tuple)) or len(pair) != 2:
            raise ValueError(f"{path}.sites must hold exactly two site ids")
        first, second = str(pair[0]), str(pair[1])
        if first == second:
            raise ValueError(f"{path}.sites must name two distinct sites")
        for label in (first, second):
            if label not in seen:
                raise ValueError(f"{path}.sites names unknown binding site {label!r}")
        if entry.get("classification", "REJECTED_BY_POLICY") != "REJECTED_BY_POLICY":
            raise ValueError(f"{path}.classification must be 'REJECTED_BY_POLICY'")
        provenance = entry.get("provenance")
        if not isinstance(provenance, str) or not provenance:
            raise ValueError(f"{path}.provenance must be a non-empty string")
        constraints.append(
            _graph.ForbiddenTrans(
                id=str(entry.get("id", f"C{position:02d}")),
                sites=(first, second),
                classification="REJECTED_BY_POLICY",
                provenance=provenance,
            )
        )
    try:
        return CoordinationSpec(
            metal_center=int(metal),
            binding_sites=tuple(sites),
            shapes=(shape,),
            constraints=tuple(constraints),
        )
    except _graph.UnsupportedTopologyError as exc:
        raise ValueError(str(exc)) from exc


def resolve_search_starts(search: Mapping[str, Any] | None, *, has_coordination: bool) -> int:
    """Resolve the DG-start count (``None`` means the run-time default).

    ``None`` resolves to 8 per coordination class when ``coordination`` is
    declared, else 400.
    """
    raw = search.get("starts") if isinstance(search, Mapping) else None
    if raw is None:
        return (
            DEFAULT_SEARCH_STARTS_WITH_METAL
            if has_coordination
            else (DEFAULT_SEARCH_STARTS_WITHOUT_METAL)
        )
    if isinstance(raw, bool) or not isinstance(raw, int) or raw < 1:
        raise ValueError(f"search.starts must be an integer >= 1 or null, got {raw!r}")
    return int(raw)


def build_search_context(structure: Any, native: Mapping[str, Any]) -> SearchContext:
    """Build the immutable search authority for one input structure.

    ``native`` is the typed v4 scope (the ``search`` key, when present, is
    executor-level and ignored here). Raises :class:`ValueError` fail-closed
    on malformed topology or indices. A confgen step that consumes the
    members of an earlier confgen step treats them as plain structures: no
    chained state is read here.
    """
    from confflow.science.confgen.planner import build_typed_graph

    if not isinstance(native, Mapping):
        raise ValueError("spec must be a mapping")
    spec = dict(native)
    spec.pop("search", None)
    seed = spec.get("seed")
    if seed is None:
        raise ValueError(
            "confgen v4 search requires an explicit top-level seed "
            "(the seed is the sole stochastic authority)"
        )
    if isinstance(seed, bool) or not isinstance(seed, int):
        raise ValueError(f"confgen v4 seed must be an integer, got {seed!r}")
    resolved = normalize_search_spec(spec)
    adjacency, graph = build_typed_graph(structure, resolved.get("topology", {}), resolved)
    section = resolved.get("coordination")
    if section is None:
        resolved_section: Mapping[str, Any] | None = None
        lane_spec: CoordinationSpec | None = None
        shape: str | None = None
    else:
        resolved_section = dict(section)
        lane_spec = resolve_search_coordination(resolved_section)
        shape = lane_spec.shapes[0]
    return SearchContext(
        adjacency=tuple(tuple(row) for row in adjacency),
        graph=graph,
        coordination_section=resolved_section,
        spec=lane_spec,
        shape=shape,
        seed=int(seed),
    )
