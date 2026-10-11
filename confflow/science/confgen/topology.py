#!/usr/bin/env python3

"""ConfGen typed topology graph construction (search lane).

Builds the lane-B :class:`~confflow.science.confgen.graph.TypedGraph` and the
covalent adjacency of one structure from a normalized v4 ``topology`` section
and the resolved ``coordination`` section. The construction rules are fixed:

- explicit ``topology.bonds`` wins outright;
- otherwise covalent perception (``bond_scale``) plus ``add_bond``/``del_bond``
  corrections, or the persisted working topology when the record carries one;
- the record ``topology_patch`` applies on top of perception and corrections
  (never on a captured working graph);
- declared coordination metal-donor pairs are typed COORDINATION last.

Entries arrive internal 0-based; :func:`convert_index` and friends convert the
user index base once at the spec boundary (``search_spec``).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from confflow.domain.structure import StructureRecord
from confflow.domain.topology import TopologyPatch
from confflow.science.confgen.coordination import spec as coordination_spec
from confflow.science.confgen.graph import AtomRef, EdgeType, TypedEdge, TypedGraph

__all__ = [
    "TOPO_EDGE_KINDS",
    "TopologyBuildContext",
    "build_typed_graph",
    "convert_index",
    "convert_index_list",
    "convert_topo_entry",
    "validate_atom_declaration",
    "validate_topo_entry",
]

#: Spec-facing topology edge kinds (uppercase, matching the fixture
#: convention). BREAKING is accepted and preserved losslessly in scope but
#: has no lane B graph record yet.
TOPO_EDGE_KINDS: tuple[str, ...] = ("COVALENT", "COORDINATION", "FORMING", "BREAKING")


@dataclass(slots=True)
class TopologyBuildContext:
    """Mutable typed-graph build state shared with coordination's contribution.

    ``typed`` is keyed by ``(first, second, kind)`` with ``first < second``.
    """

    n_atoms: int
    adjacency: list[set[int]]
    typed: dict[tuple[int, int, Any], Any]
    explicit_covalent: set[tuple[int, int]]


def convert_index(value: Any, *, base: int, path: str) -> int:
    """Validate one index against the declared base, return 0-based."""
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{path} must be an integer atom index, got {value!r}")
    if value < base:
        raise ValueError(f"{path} index {value} below declared index_base {base}")
    return int(value) - base


def convert_index_list(values: Any, *, base: int, path: str) -> list[int]:
    """Validate/convert an index list to internal 0-based."""
    if isinstance(values, (str, bytes)) or not isinstance(values, Sequence):
        raise ValueError(f"{path} must be a list of atom indices")
    return [
        convert_index(item, base=base, path=f"{path}[{position}]")
        for position, item in enumerate(values)
    ]


def convert_topo_entry(item: Any, *, base: int, path: str) -> Any:
    """Convert one validated topology entry to internal 0-based."""
    if isinstance(item, Mapping):
        converted: dict[str, Any] = {
            "atoms": convert_index_list(item["atoms"], base=base, path=f"{path}.atoms"),
            "kind": str(item.get("kind", "COVALENT")),
        }
        if item.get("bond_order") is not None:
            converted["bond_order"] = float(item["bond_order"])
        if item.get("provenance") is not None:
            converted["provenance"] = str(item["provenance"])
        return converted
    return convert_index_list(item, base=base, path=path)


# ---------------------------------------------------------------------------
# Torsion axes


def validate_topo_entry(item: Any, *, path: str) -> Any:
    """Validate one topology entry shape (base-agnostic; see conversion).

    Plain pairs default to COVALENT. Typed mappings carry an uppercase kind
    (COVALENT/COORDINATION/FORMING/BREAKING), optional bond_order metadata,
    and optional provenance. Lowercase kinds fail closed (lane B normalizes
    them, but the spec boundary requires the uppercase fixture spelling).
    """
    if isinstance(item, Mapping):
        unknown = sorted(set(item) - {"atoms", "kind", "bond_order", "provenance"})
        if unknown:
            raise ValueError(f"{path} holds unknown keys {unknown}")
        atoms = item.get("atoms")
        if isinstance(atoms, (str, bytes)) or not isinstance(atoms, Sequence):
            raise ValueError(f"{path}.atoms must be an index pair")
        pair = list(atoms)
        if len(pair) != 2:
            raise ValueError(f"{path}.atoms must hold exactly two indices")
        kind = item.get("kind", "COVALENT")
        if kind not in TOPO_EDGE_KINDS:
            raise ValueError(f"{path}.kind must be one of {TOPO_EDGE_KINDS}, got {kind!r}")
        for name, value in (("first", pair[0]), ("second", pair[1])):
            if isinstance(value, bool) or not isinstance(value, int):
                raise ValueError(f"{path}.atoms {name} must be an integer, got {value!r}")
        if pair[0] == pair[1]:
            raise ValueError(f"{path}.atoms must name two distinct atoms")
        record: dict[str, Any] = {"atoms": [int(pair[0]), int(pair[1])], "kind": str(kind)}
        if item.get("bond_order") is not None:
            order = item["bond_order"]
            if isinstance(order, bool) or not isinstance(order, (int, float)):
                raise ValueError(f"{path}.bond_order must be a number or null")
            record["bond_order"] = float(order)
        if item.get("provenance") is not None:
            if not isinstance(item["provenance"], str):
                raise ValueError(f"{path}.provenance must be a string")
            record["provenance"] = str(item["provenance"])
        return record
    if isinstance(item, (str, bytes)) or not isinstance(item, Sequence):
        raise ValueError(f"{path} must be an index pair or typed edge mapping")
    pair = list(item)
    if len(pair) != 2:
        raise ValueError(f"{path} must hold exactly two indices")
    for name, value in (("first", pair[0]), ("second", pair[1])):
        if isinstance(value, bool) or not isinstance(value, int):
            raise ValueError(f"{path} {name} must be an integer, got {value!r}")
    if pair[0] == pair[1]:
        raise ValueError(f"{path} must name two distinct atoms")
    return [int(pair[0]), int(pair[1])]


def validate_atom_declaration(item: Any, *, path: str) -> dict[str, Any]:
    """Validate one topology atom declaration (roles/stereo/labels)."""
    if not isinstance(item, Mapping):
        raise ValueError(f"{path} must be a mapping")
    unknown = sorted(set(item) - {"index", "label", "role", "stereo"})
    if unknown:
        raise ValueError(f"{path} holds unknown keys {unknown}")
    if "index" not in item:
        raise ValueError(f"{path} must carry 'index'")
    shape: dict[str, Any] = {"index": item["index"]}
    if item.get("label") is not None:
        if not isinstance(item["label"], str):
            raise ValueError(f"{path}.label must be a string")
        shape["label"] = item["label"]
    shape["role"] = str(item.get("role", ""))
    if not isinstance(shape["role"], str):
        raise ValueError(f"{path}.role must be a string")
    stereo = item.get("stereo")
    if stereo is not None and not isinstance(stereo, str):
        raise ValueError(f"{path}.stereo must be a string or null")
    shape["stereo"] = stereo
    return shape


# NOTE (AG2 generic): the overlay implementation is declared by the
# owning component via ``registry.legacy_compat``. The planner keeps the
# old symbol/signature and loads it through ``resolve_compat`` (no kernel
# component import); ``_build_typed_graph`` calls components via the
# registry instance instead.


def _apply_structure_patch(
    structure: StructureRecord,
    n_atoms: int,
    adjacency: list[set[int]],
    typed: dict[tuple[int, int, Any], Any],
    _check: Any,
) -> None:
    """Apply the record's TopologyPatch on top of perception + corrections.

    Runs after ``add_bond``/``del_bond`` so the declared correction intent
    wins over raw perception.  Simultaneous spec corrections and a record
    patch are rejected earlier by :func:`check_spec_patch_conflict`, so a
    patch here never meets spec add/del entries.  Patched covalent edges
    are recorded as lane B records with ``topology_patch`` provenance.
    """
    from confflow.science.confgen.graph import EdgeType, TypedEdge

    # A captured working graph already incorporates the root patch and any
    # explicit scientific overlays. Never replay historical corrections on it.
    if structure.working_topology is not None:
        return
    patch = getattr(structure, "topology_patch", None)
    if patch is None:
        return
    if isinstance(patch, dict):
        patch = TopologyPatch.from_dict(patch)
    if not isinstance(patch, TopologyPatch) or patch.is_empty:
        return
    for first_one, second_one in patch.add_edges:
        first, second = first_one - 1, second_one - 1
        _check(first, "topology_patch.add_edges")
        _check(second, "topology_patch.add_edges")
        pair = (min(first, second), max(first, second))
        for _a, _b, kind in list(typed):
            if (_a, _b) == pair and kind is not EdgeType.COVALENT:
                raise ValueError(
                    f"topology_patch adds {pair} already carrying typed role "
                    f"{kind.value}; contradictory kinds for one pair fail closed"
                )
        adjacency[first].add(second)
        adjacency[second].add(first)
        typed[(pair[0], pair[1], EdgeType.COVALENT)] = TypedEdge(
            a=pair[0],
            b=pair[1],
            type=EdgeType.COVALENT,
            provenance="topology_patch",
        )
    for first_one, second_one in patch.delete_edges:
        first, second = first_one - 1, second_one - 1
        _check(first, "topology_patch.delete_edges")
        _check(second, "topology_patch.delete_edges")
        adjacency[first].discard(second)
        adjacency[second].discard(first)
        typed.pop((min(first, second), max(first, second), EdgeType.COVALENT), None)


def _topo_edge(item: Any) -> tuple[tuple[int, int], Any, dict[str, Any]]:
    """Normalize a converted (0-based) topology entry to (pair, EdgeType, extra)."""
    from confflow.science.confgen.graph import EdgeType, normalize_edge_kind

    if isinstance(item, Mapping):
        first, second = item["atoms"]
        extra: dict[str, Any] = {}
        if item.get("bond_order") is not None:
            extra["bond_order"] = float(item["bond_order"])
        if item.get("provenance") is not None:
            extra["provenance"] = str(item["provenance"])
        return (int(first), int(second)), normalize_edge_kind(item.get("kind", "COVALENT")), extra
    first, second = item
    return (int(first), int(second)), EdgeType.COVALENT, {}


def build_typed_graph(
    structure: StructureRecord,
    topology: Mapping[str, Any],
    resolved: Mapping[str, Any],
) -> tuple[list[list[int]], TypedGraph]:
    """Build covalent adjacency plus the lane B typed-graph authority.

    ``topology`` and ``resolved`` are the normalized (internal 0-based) spec
    sections produced by ``search_spec.normalize_search_spec``. ``tolerances``
    are read from the resolved spec (``bond_scale``, default 1.15).
    """
    from confflow.domain.elements import atomic_number
    from confflow.science.bonds import perceive_adjacency
    from confflow.science.topology import check_spec_patch_conflict as _check_patch_conflict

    if not isinstance(topology, Mapping):
        raise ValueError("topology must be a mapping")
    n_atoms = len(structure.atoms)
    elements = list(structure.atoms)
    try:
        _check_patch_conflict(topology, structure, where="confgen topology")
    except Exception as exc:
        raise ValueError(str(exc)) from exc
    metal_center = _metal_center(resolved, n_atoms)
    tolerances = resolved.get("tolerances", {}) if isinstance(resolved, Mapping) else {}
    bond_scale = float(tolerances.get("bond_scale", 1.15))

    def _check(value: int, path: str) -> None:
        if value < 0 or value >= n_atoms:
            raise ValueError(f"{path} index {value} out of range for {n_atoms} atoms")

    if topology.get("bonds") is not None:
        pairs = topology["bonds"]
        if isinstance(pairs, (str, bytes)) or not isinstance(pairs, Sequence):
            raise ValueError("topology.bonds must be a list of entries")
        forming: list[list[int]] = []
        for index, item in enumerate(pairs):
            (first, second), kind, _extra = _topo_edge(item)
            _check(first, f"topology.bonds[{index}]")
            _check(second, f"topology.bonds[{index}]")
            if kind is EdgeType.FORMING:
                forming.append([first, second])
        mapping = {
            "bonds": list(pairs),
            "atoms": list(topology.get("atoms", []) or []),
            "metal_center": metal_center,
            "reaction_pairs": forming,
            "source": "v3-spec-explicit",
        }
        graph = TypedGraph.from_mapping(
            mapping, elements, convention="internal0", source="v3-spec-explicit"
        )
        if graph.natoms != n_atoms:  # defensive; from_mapping builds from elements
            raise ValueError("typed graph atom count mismatches structure")
        return [list(row) for row in _covalent_adjacency(graph)], graph

    stored_graph = getattr(structure, "working_topology", None)
    if stored_graph is not None:
        # Authoritative persisted graph: never re-perceive moved geometry.
        perceived = [[int(v) for v in row] for row in stored_graph]
        if len(perceived) != n_atoms:
            raise ValueError("persisted working_topology row count mismatches atom count")
    else:
        try:
            numbers = [atomic_number(symbol) for symbol in structure.atoms]
        except Exception as exc:
            raise ValueError(f"bond perception failed: {exc}") from exc
        try:
            perceived = perceive_adjacency(
                numbers,
                [tuple(point) for point in structure.coordinates],
                bond_scale=bond_scale,
            )
        except ValueError as exc:
            raise ValueError(f"bond perception failed: {exc}") from exc
    if len(perceived) != n_atoms:
        raise ValueError("perceived adjacency row count mismatches atom count")
    adjacency: list[set[int]] = [set(row) for row in perceived]
    typed: dict[tuple[int, int, Any], TypedEdge] = {}
    for first in range(n_atoms):
        for second in adjacency[first]:
            if second > first:
                edge = TypedEdge(a=first, b=second, type=EdgeType.COVALENT)
                typed[(first, second, EdgeType.COVALENT)] = edge
    reaction_pairs = []
    explicit_covalent: set[tuple[int, int]] = set()
    typed_non_covalent: set[tuple[int, int]] = set()
    for key in ("add_bond", "del_bond"):
        entries = topology.get(key) or []
        if isinstance(entries, (str, bytes)) or not isinstance(entries, Sequence):
            raise ValueError(f"topology.{key} must be a list of entries")
        for index, item in enumerate(entries):
            (first, second), kind, extra = _topo_edge(item)
            _check(first, f"topology.{key}[{index}]")
            _check(second, f"topology.{key}[{index}]")
            pair = (min(first, second), max(first, second))
            if key == "add_bond":
                if kind is EdgeType.COVALENT:
                    if pair in typed_non_covalent:
                        raise ValueError(
                            f"topology.add_bond[{index}] declares COVALENT for pair "
                            f"{pair} already given a typed non-covalent role; "
                            "contradictory kinds for one pair fail closed"
                        )
                    explicit_covalent.add(pair)
                    adjacency[first].add(second)
                    adjacency[second].add(first)
                else:
                    if pair in explicit_covalent:
                        raise ValueError(
                            f"topology.add_bond[{index}] declares {kind.value} for pair "
                            f"{pair} already declared COVALENT; contradictory kinds "
                            "for one pair fail closed"
                        )
                    # Authoritative typed overlay: a declared non-covalent
                    # role replaces the distance-guessed COVALENT edge, so
                    # the pair leaves the covalent-only adjacency.
                    typed.pop((pair[0], pair[1], EdgeType.COVALENT), None)
                    adjacency[first].discard(second)
                    adjacency[second].discard(first)
                    typed_non_covalent.add(pair)
                edge = TypedEdge(
                    a=pair[0],
                    b=pair[1],
                    type=kind,
                    bond_order=extra.get("bond_order"),
                    provenance=extra.get("provenance", "add_bond"),
                )
                typed[(pair[0], pair[1], kind)] = edge
                if kind is EdgeType.FORMING:
                    reaction_pairs.append(pair)
            else:
                if kind is not EdgeType.COVALENT:
                    raise ValueError(
                        f"topology.del_bond[{index}] must name a covalent pair; "
                        "typed-edge removal is not a perception correction"
                    )
                # Silent discard of absent pairs.
                adjacency[first].discard(second)
                adjacency[second].discard(first)
                typed.pop((pair[0], pair[1], EdgeType.COVALENT), None)
    _apply_structure_patch(structure, n_atoms, adjacency, typed, _check)
    build = TopologyBuildContext(
        n_atoms=n_atoms,
        adjacency=adjacency,
        typed=typed,
        explicit_covalent=explicit_covalent,
    )
    coordination_spec.contribute_topology(resolved, build)
    atoms = [AtomRef(index=position, element=symbol) for position, symbol in enumerate(elements)]
    for decl in topology.get("atoms", []) or []:
        position = int(decl["index"])
        _check(position, "topology.atoms declaration")
        atoms[position] = AtomRef(
            index=position,
            element=atoms[position].element,
            label=decl.get("label"),
            role=str(decl.get("role", "")),
            stereo=decl.get("stereo"),
        )
    graph = TypedGraph(
        atoms=tuple(atoms),
        edges=tuple(typed.values()),
        metal_center=metal_center,
        reaction_pairs=tuple(reaction_pairs),
        source="v3-spec-perceived",
    )
    resolved_adjacency = [sorted(row) for row in adjacency]
    if _covalent_adjacency(graph) != tuple(tuple(row) for row in resolved_adjacency):
        raise ValueError("covalent adjacency diverged from typed graph authority")
    return resolved_adjacency, graph


def _metal_center(resolved: Mapping[str, Any], n_atoms: int) -> int | None:
    """Return the declared metal center (validated by coordination), or None."""
    metadata = coordination_spec.graph_metadata(resolved, n_atoms)
    if metadata is None:
        return None
    return metadata.get("metal_center")


def _covalent_adjacency(graph: TypedGraph) -> tuple[tuple[int, ...], ...]:
    """Return covalent-only neighbour lists from the authority graph."""
    return tuple(graph.neighbors(index, EdgeType.COVALENT) for index in range(graph.natoms))
