#!/usr/bin/env python3
"""Budgeted typed/stereo/site/reaction-preserving automorphism search.

Pure science: stdlib + NumPy only.

The search is a budgeted backtracking over atom permutations preserving, in
order: element labels, typed edges (COVALENT / COORDINATION / FORMING),
binding-site donor set, reaction pairs, and optional stereo labels.  The
budget counts node expansions; when it is exhausted the result is explicitly
``complete=False`` and no caller may certify complete symmetry accounting
from it.  ``require_complete`` fails closed on incomplete searches.

A supplied witness (for example the TS1 sigma) is validated for topology
preservation by :meth:`TypedGraph.validate_witness` plus site-action closure,
stereo, and reaction checks here.  Topology validity alone is never promoted
to stereo or geometric proof.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from ..graph import CoordinationSpec, TypedGraph

__all__ = [
    "AutomorphismSearchResult",
    "IncompleteGroupError",
    "search_automorphisms",
    "require_complete",
    "validate_supplied_witness",
    "validate_full_witness",
    "site_action_label_map",
    "permutation_parity",
]


class IncompleteGroupError(ValueError):
    """Raised when complete symmetry accounting is requested from a partial search."""


@dataclass(frozen=True, slots=True)
class AutomorphismSearchResult:
    """Budgeted automorphism search outcome."""

    generators: tuple[tuple[int, ...], ...]
    group_size: int | None
    complete: bool
    expansions: int
    budget: int
    stereo_labels_used: bool
    notes: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-compatible record."""
        return {
            "generators": [list(gen) for gen in self.generators],
            "group_size": self.group_size,
            "complete": self.complete,
            "expansions": self.expansions,
            "budget": self.budget,
            "stereo_labels_used": self.stereo_labels_used,
            "notes": list(self.notes),
        }


def permutation_parity(perm: Sequence[int]) -> int:
    """Return +1 for even and -1 for odd permutations."""
    order = list(int(value) for value in perm)
    visited = [False] * len(order)
    parity = 1
    for start in range(len(order)):
        if visited[start]:
            continue
        length = 0
        cursor = start
        while not visited[cursor]:
            visited[cursor] = True
            cursor = order[cursor]
            length += 1
        if length % 2 == 0:
            parity = -parity
    return parity


def _candidates(
    graph: TypedGraph,
    spec: CoordinationSpec,
    stereo_labels: Sequence[str | None] | None,
) -> list[list[int]]:
    elements = graph.elements
    donors = set(spec.donor_indices)
    reaction_atoms = {atom for pair in graph.reaction_pairs for atom in pair}
    buckets: dict[tuple[Any, ...], list[int]] = {}
    for index, element in enumerate(elements):
        key: tuple[Any, ...] = (
            element,
            index in donors,
            index == spec.metal_center,
            index in reaction_atoms,
        )
        if stereo_labels is not None:
            key = key + (stereo_labels[index],)
        buckets.setdefault(key, []).append(index)
    by_index: list[list[int]] = [[] for _ in range(graph.natoms)]
    for index in range(graph.natoms):
        element = elements[index]
        key = (
            element,
            index in donors,
            index == spec.metal_center,
            index in reaction_atoms,
        )
        if stereo_labels is not None:
            key = key + (stereo_labels[index],)
        by_index[index] = list(buckets[key])
    return by_index


def search_automorphisms(
    graph: TypedGraph,
    spec: CoordinationSpec,
    budget: int = 200000,
    max_automorphisms: int = 64,
    stereo_labels: Sequence[str | None] | None = None,
) -> AutomorphismSearchResult:
    """Search typed-graph automorphisms within an expansion *budget*.

    Every automorphism found (including the identity) is collected up to
    *max_automorphisms*.  Exhausting *budget* — or hitting the enumeration
    cap — before the backtracking completes yields ``complete=False`` with
    ``group_size=None``; no caller may certify complete symmetry accounting
    from such a result (see :func:`require_complete`).
    """
    if budget < 1:
        raise ValueError("budget must be positive")
    if max_automorphisms < 2:
        raise ValueError("max_automorphisms must be at least 2")
    if stereo_labels is not None and len(stereo_labels) != graph.natoms:
        raise ValueError("stereo label vector must cover all atoms")
    edge_set = graph.edge_set()
    # Typed adjacency for incremental consistency checks.
    adjacency: dict[int, dict[int, set[Any]]] = {}
    for a, b, kind in edge_set:
        adjacency.setdefault(a, {}).setdefault(b, set()).add(kind)
        adjacency.setdefault(b, {}).setdefault(a, set()).add(kind)
    donors = set(spec.donor_indices)
    allowed = _candidates(graph, spec, stereo_labels)
    # Order atoms by fewest candidates for deterministic pruning.
    order = sorted(range(graph.natoms), key=lambda i: (len(allowed[i]), i))
    image = [-1] * graph.natoms
    used = [False] * graph.natoms
    automorphisms: list[tuple[int, ...]] = []
    expansions = 0
    exhausted = False
    capped = False

    def _consistent(position: int) -> bool:
        atom = order[position]
        target = image[atom]
        for earlier in range(position):
            other = order[earlier]
            other_image = image[other]
            kinds = adjacency.get(atom, {}).get(other, set())
            mapped = adjacency.get(target, {}).get(other_image, set())
            if kinds != mapped:
                return False
        return True

    def _backtrack(position: int) -> None:
        nonlocal expansions, exhausted, capped
        if exhausted or capped:
            return
        if position == graph.natoms:
            perm = tuple(image)
            if any(perm[d] not in donors for d in donors):
                return
            if perm not in automorphisms:
                automorphisms.append(perm)
                if len(automorphisms) >= max_automorphisms:
                    capped = True
            return
        atom = order[position]
        for target in allowed[atom]:
            if used[target]:
                continue
            expansions += 1
            if expansions > budget:
                exhausted = True
                return
            image[atom] = target
            used[target] = True
            if _consistent(position):
                _backtrack(position + 1)
                if exhausted or capped:
                    image[atom] = -1
                    used[target] = False
                    return
            image[atom] = -1
            used[target] = False

    _backtrack(0)
    identity = tuple(range(graph.natoms))
    complete = not exhausted and not capped
    notes: list[str] = []
    if exhausted:
        notes.append("budget exhausted: group is partial and certifies nothing")
    if capped:
        notes.append(
            f"enumeration cap ({max_automorphisms}) reached: group may be larger; "
            "complete accounting refused"
        )
    if stereo_labels is None:
        notes.append("no stereo labels supplied: stereo preservation UNVERIFIED")
    if complete and identity not in automorphisms:
        notes.append("internal error: identity missing from complete search")
        complete = False
    generators = tuple(perm for perm in automorphisms if perm != identity)
    return AutomorphismSearchResult(
        generators=generators,
        group_size=(len(automorphisms) if complete else None),
        complete=complete,
        expansions=expansions,
        budget=budget,
        stereo_labels_used=stereo_labels is not None,
        notes=tuple(notes),
    )


def require_complete(result: AutomorphismSearchResult) -> AutomorphismSearchResult:
    """Fail closed unless the search ran to completion."""
    if not result.complete:
        raise IncompleteGroupError(
            f"automorphism search incomplete after {result.expansions} expansions "
            f"(budget {result.budget}); complete symmetry accounting is refused"
        )
    return result


def validate_supplied_witness(
    graph: TypedGraph,
    spec: CoordinationSpec,
    mapping: Sequence[int],
    stereo_labels: Sequence[str | None] | None = None,
) -> dict[str, Any]:
    """Audit a supplied witness (for example TS1 sigma) without over-claiming.

    Returns topology validity, site-action closure, reaction preservation,
    stereo status (``PRESERVED`` only with explicit labels that match,
    else ``UNVERIFIED``), and the explicit statement that topology validity
    is not stereo or H_geom proof.
    """
    report = graph.validate_witness(mapping)
    perm = tuple(int(value) for value in mapping)
    action = graph.induced_site_action(perm, spec.donor_indices) if report.topology_valid else None
    site_closed = action is not None
    reaction_ok = True
    if report.topology_valid:
        mapped_pairs = set()
        for a, b in graph.reaction_pairs:
            mapped_pairs.add((min(perm[a], perm[b]), max(perm[a], perm[b])))
        reaction_ok = mapped_pairs == set(graph.reaction_pairs)
    stereo_status = "UNVERIFIED"
    if stereo_labels is not None and report.topology_valid:
        stereo_status = (
            "PRESERVED"
            if all(
                stereo_labels[index] == stereo_labels[perm[index]] for index in range(graph.natoms)
            )
            else "VIOLATED"
        )
    return {
        "topology_valid": report.topology_valid,
        "involutive": report.involutive,
        "site_action_closed": site_closed,
        "reaction_preserving": bool(reaction_ok),
        "stereo_status": stereo_status,
        "donor_configuration_stereo": "UNSUPPORTED (no production stereo model)",
        "geometric_status": "UNVERIFIED",
        "sufficient_for_stereo_proof": False,
        "sufficient_for_hgeom_suppression": False,
        "parity": (permutation_parity(perm) if report.is_permutation else None),
        "report": report.to_dict(),
    }


def validate_full_witness(
    graph: TypedGraph,
    spec: CoordinationSpec,
    mapping: Sequence[int],
    provenance: str = "",
) -> dict[str, Any]:
    """Validate a full-atom witness for site-group authority.

    Beyond topology (element/typed-edge/involution): bond orders preserved on
    every edge, induced donor action closed on the binding-site set, reaction
    pairs preserved, and fragment roles respected (coordination-cut components
    map onto components).  A donor-only permutation with no backing full
    witness — or one that breaks any of these — fails closed and can never
    back molecular symmetry authority.  Stereo action stays uncertified
    (topology only); suppression additionally requires H_geom.
    """
    from .realization import partition_fragments

    base = validate_supplied_witness(graph, spec, mapping)
    perm = tuple(int(v) for v in mapping)
    bond_order_ok = True
    if base["topology_valid"]:
        for edge in graph.edges:
            image = (min(perm[edge.a], perm[edge.b]), max(perm[edge.a], perm[edge.b]))
            partner = next(
                (e for e in graph.edges if (e.a, e.b) == image and e.type is edge.type),
                None,
            )
            if partner is None or partner.bond_order != edge.bond_order:
                bond_order_ok = False
                break
    plan = partition_fragments(graph, spec.metal_center)
    member_of: dict[int, int] = {}
    for index, frag in enumerate(plan.fragments):
        for atom in frag:
            member_of[atom] = index
    fragment_ok = base["topology_valid"] and all(
        {perm[atom] for atom in frag} == set(plan.fragments[member_of[perm[frag[0]]]])
        for frag in plan.fragments
    )
    return {
        "topology_valid": base["topology_valid"],
        "involutive": base["involutive"],
        "bond_order_preserving": bool(bond_order_ok),
        "site_action_closed": base["site_action_closed"],
        "reaction_preserving": base["reaction_preserving"],
        "fragment_roles_preserved": bool(fragment_ok),
        "stereo_action": "uncertified-topology-only",
        "suppression_authority": "none (H_geom required)",
        "authority_valid": bool(
            base["topology_valid"] and bond_order_ok and fragment_ok and base["reaction_preserving"]
        ),
        "provenance": provenance,
    }


def site_action_label_map(spec: CoordinationSpec, action: dict[int, int]) -> dict[str, str]:
    """Render a donor-index action as a site-label map."""
    index_to_site = {site.donor: site.id for site in spec.binding_sites}
    return {index_to_site[donor]: index_to_site[image] for donor, image in sorted(action.items())}
