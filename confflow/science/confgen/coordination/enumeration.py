#!/usr/bin/env python3
"""Labeled enumeration, policy/proof filters, and symmetry accounting.

Pure science: stdlib + NumPy only.

Conventions
-----------
A *placement* is a tuple ``f`` of length ``n`` where ``f[i]`` is the vertex
index occupied by binding site ``i`` (declared site order).  There are
``n!`` labeled placements for ``n`` distinct monodentate sites.

A *shape class* is an orbit of placements under the shape proper rotation
group acting on vertices: ``(g . f)[i] = g[f[i]]``.  The canonical
representative is the lexicographic minimum of the orbit.

A *molecular orbit* quotients shape classes further by a supplied topology
automorphism (product action on site labels).  Constraints must be invariant
under that action before any sigma-level accounting is certified.

Count layers stay explicit: raw assignments, policy/proof exclusions, shape
classes, molecular orbits, realization targets, published leaves.  No unknown
count masquerades as exact.
"""

from __future__ import annotations

import itertools
import math
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from ..graph import CoordinationSpec, ForbiddenTrans, site_label_index
from .shapes import ShapeTemplate, get_shape, proper_rotation_group

__all__ = [
    "PlacementFilter",
    "ShapeClass",
    "MolecularOrbit",
    "CountLayers",
    "SiteGroup",
    "enumerate_labeled_placements",
    "placement_layer_counts",
    "apply_policy_filters",
    "canonical_representative",
    "shape_classes",
    "check_constraint_invariance",
    "compose_site_permutations",
    "close_site_group",
    "declared_site_group",
    "site_group_orbits",
    "molecular_burnside_count",
    "molecular_orbits",
    "burnside_orbit_count",
    "command_key",
    "normalize_command_key",
    "state_matches",
    "enumerate_targets",
]


@dataclass(frozen=True, slots=True)
class PlacementFilter:
    """One placement exclusion with provenance."""

    placement: tuple[int, ...]
    constraint_id: str
    verdict: str
    reason: str

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-compatible record."""
        return {
            "placement": list(self.placement),
            "constraint_id": self.constraint_id,
            "verdict": self.verdict,
            "reason": self.reason,
        }


@dataclass(frozen=True, slots=True)
class ShapeClass:
    """One proper-rotation orbit of placements."""

    id: str
    representative: tuple[int, ...]
    size: int
    members_excluded_by_policy: int = 0

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-compatible record."""
        return {
            "id": self.id,
            "representative": list(self.representative),
            "size": self.size,
            "members_excluded_by_policy": self.members_excluded_by_policy,
        }


@dataclass(frozen=True, slots=True)
class MolecularOrbit:
    """One sigma orbit of shape classes (topology level)."""

    id: str
    members: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-compatible record."""
        return {"id": self.id, "members": list(self.members)}


@dataclass(frozen=True, slots=True)
class CountLayers:
    """Explicit enumeration count layers."""

    raw_assignments: int
    policy_excluded: int
    proof_excluded: int
    admissible: int
    shape_classes_before_policy: int
    shape_classes: int
    molecular_orbits: int | None
    exact: bool = True

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-compatible record."""
        return {
            "raw_assignments": self.raw_assignments,
            "policy_excluded": self.policy_excluded,
            "proof_excluded": self.proof_excluded,
            "admissible": self.admissible,
            "shape_classes_before_policy": self.shape_classes_before_policy,
            "shape_classes": self.shape_classes,
            "molecular_orbits": self.molecular_orbits,
            "exact": self.exact,
        }


@dataclass(frozen=True, slots=True)
class SiteGroup:
    """A declared site-permutation group with completeness proof.

    ``elements`` is the closed group (identity first, sorted after);
    ``complete`` is True only when the generator closure finished within
    budget *and* satisfies the group axioms.  Molecular orbits are certified
    relative to this DECLARED group only — never as the discovered full
    molecular automorphism group.
    """

    generators: tuple[tuple[int, ...], ...]
    elements: tuple[tuple[int, ...], ...]
    complete: bool
    expansions: int
    budget: int

    @property
    def order(self) -> int | None:
        """Return the group order, or ``None`` when incomplete."""
        return len(self.elements) if self.complete else None

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-compatible record."""
        return {
            "generators": [list(gen) for gen in self.generators],
            "order": self.order,
            "complete": self.complete,
            "expansions": self.expansions,
            "budget": self.budget,
        }


def enumerate_labeled_placements(count: int) -> Iterator[tuple[int, ...]]:
    """Yield all ``count!`` labeled placements lazily (no materialization)."""
    if count < 1:
        raise ValueError("site count must be positive")
    yield from itertools.permutations(range(count))


def placement_layer_counts(count: int) -> int:
    """Return the exact raw labeled-assignment count (``count!``)."""
    return math.factorial(count)


def _placement_violations(
    placement: tuple[int, ...],
    spec: CoordinationSpec,
    template: ShapeTemplate,
) -> list[ForbiddenTrans]:
    site_ids = spec.site_ids
    hits: list[ForbiddenTrans] = []
    for constraint in spec.constraints:
        first = site_label_index(site_ids, constraint.sites[0])
        second = site_label_index(site_ids, constraint.sites[1])
        vertex_first = placement[first]
        vertex_second = placement[second]
        if template.opposite.get(vertex_first) == vertex_second:
            hits.append(constraint)
    return hits


def apply_policy_filters(
    placements: Iterator[tuple[int, ...]],
    spec: CoordinationSpec,
    template: ShapeTemplate,
) -> tuple[list[tuple[int, ...]], list[PlacementFilter]]:
    """Split placements into admissible and excluded with full provenance.

    ``REJECTED_BY_POLICY`` exclusions stay policy verdicts.  Only constraints
    carrying an enforced :class:`DonorBoundProof` yield ``PROVEN_INFEASIBLE``;
    anything else claiming proof fails closed.
    """
    admissible: list[tuple[int, ...]] = []
    excluded: list[PlacementFilter] = []
    for placement in placements:
        hits = _placement_violations(tuple(placement), spec, template)
        if not hits:
            admissible.append(tuple(placement))
            continue
        for hit in hits[:1]:
            if hit.classification == "PROVEN_INFEASIBLE":
                if hit.proof is None or not hit.proof.is_enforced():
                    raise ValueError(
                        f"constraint {hit.id}: PROVEN_INFEASIBLE without enforced hard bounds"
                    )
                verdict = "PROVEN_INFEASIBLE"
            else:
                verdict = "REJECTED_BY_POLICY"
            excluded.append(
                PlacementFilter(
                    placement=tuple(placement),
                    constraint_id=hit.id,
                    verdict=verdict,
                    reason=f"{hit.id} {hit.sites[0]}-trans-{hit.sites[1]}; {hit.provenance}",
                )
            )
    return admissible, excluded


def canonical_representative(
    placement: Sequence[int], group: Sequence[Sequence[int]]
) -> tuple[int, ...]:
    """Return the lexicographic minimum of the group orbit of *placement*."""
    current = tuple(int(value) for value in placement)
    best = current
    for perm in group:
        image = tuple(perm[value] for value in current)
        if image < best:
            best = image
    return best


def shape_classes(
    placements: Sequence[Sequence[int]],
    group: Sequence[Sequence[int]],
    prefix: str = "L",
) -> list[ShapeClass]:
    """Quotient placements into proper-rotation shape classes."""
    reps: dict[tuple[int, ...], int] = {}
    for placement in placements:
        rep = canonical_representative(placement, group)
        reps[rep] = reps.get(rep, 0) + 1
    ordered = sorted(reps)
    return [
        ShapeClass(id=f"{prefix}{index:02d}", representative=rep, size=reps[rep])
        for index, rep in enumerate(ordered)
    ]


def check_constraint_invariance(
    spec: CoordinationSpec, site_permutation: Sequence[int]
) -> tuple[bool, list[str]]:
    """Check forbidden-trans constraints are invariant under a site action.

    *site_permutation* maps old site position ``i`` to its image position
    ``site_permutation[i]``.  Invariance is required before sigma-level orbit
    accounting is certified; a non-invariant set fails closed.
    """
    perm = tuple(int(value) for value in site_permutation)
    site_ids = spec.site_ids
    forbidden = {frozenset((c.sites[0], c.sites[1])) for c in spec.constraints}
    reasons: list[str] = []
    for constraint in spec.constraints:
        first = site_ids[perm[site_label_index(site_ids, constraint.sites[0])]]
        second = site_ids[perm[site_label_index(site_ids, constraint.sites[1])]]
        if frozenset((first, second)) not in forbidden:
            reasons.append(
                f"constraint {constraint.id} maps to non-forbidden pair {{{first}, {second}}}"
            )
    return (not reasons, reasons)


def _relabel(placement: Sequence[int], site_permutation: Sequence[int]) -> tuple[int, ...]:
    """Apply the atom-permutation site relabeling to a placement.

    Matches the fixture convention: with ``inv`` the inverse site map, the
    relabeled placement is ``f'[i] = f[inv[i]]``.
    """
    perm = tuple(int(value) for value in site_permutation)
    count = len(perm)
    inv = [0] * count
    for old, new in enumerate(perm):
        inv[new] = old
    current = tuple(int(value) for value in placement)
    return tuple(current[inv[index]] for index in range(count))


def _sigma_relabel(placement: Sequence[int], site_permutation: Sequence[int]) -> tuple[int, ...]:
    """Backward-compatible alias for :func:`_relabel`."""
    return _relabel(placement, site_permutation)


def compose_site_permutations(first: Sequence[int], second: Sequence[int]) -> tuple[int, ...]:
    """Compose site actions: apply *second*, then *first*."""
    first_t = tuple(int(v) for v in first)
    second_t = tuple(int(v) for v in second)
    if len(first_t) != len(second_t):
        raise ValueError("site permutations must act on the same site count")
    return tuple(first_t[second_t[i]] for i in range(len(first_t)))


def close_site_group(
    generators: Sequence[Sequence[int]], count: int, budget: int = 10000
) -> SiteGroup:
    """Close generator site permutations into a verified group.

    Breadth-first closure with an expansion budget; ``complete`` requires the
    budget to hold *and* the identity/closure/inverse axioms to verify.
    """
    if budget < 1:
        raise ValueError("budget must be positive")
    gens = tuple(tuple(int(v) for v in gen) for gen in generators)
    for gen in gens:
        if sorted(gen) != list(range(count)):
            raise ValueError("site generator must permute all site positions")
    identity = tuple(range(count))
    elements: set[tuple[int, ...]] = {identity}
    frontier = [identity]
    expansions = 0
    exhausted = False
    while frontier and not exhausted:
        current = frontier.pop()
        for gen in gens:
            for composed in (
                compose_site_permutations(gen, current),
                compose_site_permutations(current, gen),
            ):
                expansions += 1
                if expansions > budget:
                    exhausted = True
                    break
                if composed not in elements:
                    elements.add(composed)
                    frontier.append(composed)
            if exhausted:
                break
    ordered = (identity,) + tuple(sorted(elements - {identity}))
    axioms = {"has_identity": True, "closed": True, "has_inverses": True}
    inverse_map: dict[tuple[int, ...], tuple[int, ...]] = {}
    for perm in ordered:
        inv = [0] * count
        for old, new in enumerate(perm):
            inv[new] = old
        inverse_map[perm] = tuple(inv)
    if any(inverse_map[perm] not in elements for perm in ordered):
        axioms["has_inverses"] = False
    complete = not exhausted and all(axioms.values())
    return SiteGroup(
        generators=gens,
        elements=ordered if complete else ordered,
        complete=complete,
        expansions=expansions,
        budget=budget,
    )


def declared_site_group(
    spec: CoordinationSpec,
    site_permutation: Sequence[int] | None = None,
    site_generators: Sequence[Sequence[int]] | None = None,
    budget: int = 10000,
) -> SiteGroup:
    """Build the declared site group from explicit generators.

    Generators come from *site_generators*, a single *site_permutation*, or —
    in future work — the spec's atom-permutation witnesses projected onto the
    donor set.  With no generators the group is the trivial complete group.
    """
    gens: list[tuple[int, ...]] = []
    if site_generators is not None:
        gens.extend(tuple(int(v) for v in gen) for gen in site_generators)
    if site_permutation is not None:
        gens.append(tuple(int(v) for v in site_permutation))
    if not gens:
        identity = tuple(range(len(spec.site_ids)))
        return SiteGroup(
            generators=(), elements=(identity,), complete=True, expansions=0, budget=budget
        )
    return close_site_group(gens, len(spec.site_ids), budget)


def site_group_orbits(
    classes: Sequence[ShapeClass],
    shape_group: Sequence[Sequence[int]],
    site_group: SiteGroup,
    spec: CoordinationSpec,
    prefix: str = "O",
) -> tuple[list[MolecularOrbit], dict[str, Any]]:
    """Quotient shape classes by the declared site-group product action.

    Fails closed unless the site group carries a completeness proof and the
    constraint set is invariant under every generator.  The admitted
    placement set is independently verified invariant under the group.
    Certified relative to the DECLARED group only.
    """
    if not site_group.complete:
        raise ValueError(
            "site group incomplete: molecular orbits refuse certification without "
            "a complete declared group basis"
        )
    assert site_group.order is not None
    for gen in site_group.generators:
        invariant, reasons = check_constraint_invariance(spec, gen)
        if not invariant:
            raise ValueError(f"constraint set not invariant under generator: {reasons}")
    by_rep = {cls.representative: cls.id for cls in classes}
    class_set = set(by_rep)
    # Admitted-set invariance: every group image of a class rep must land in
    # the admitted class set (product action closure on the admitted set).
    for cls in classes:
        for element in site_group.elements:
            image = canonical_representative(_relabel(cls.representative, element), shape_group)
            if image not in class_set:
                raise ValueError("site-group image leaves the admitted shape-class set")
    seen: set[tuple[int, ...]] = set()
    orbits: list[MolecularOrbit] = []
    for cls in classes:
        if cls.representative in seen:
            continue
        members = {
            by_rep[canonical_representative(_relabel(cls.representative, element), shape_group)]
            for element in site_group.elements
        }
        ordered_members = tuple(sorted(members))
        for member in ordered_members:
            rep = next(item.representative for item in classes if item.id == member)
            seen.add(rep)
        orbits.append(MolecularOrbit(id=f"{prefix}{len(orbits):02d}", members=ordered_members))
    audit = {
        "orbit_count": len(orbits),
        "declared_group_order": site_group.order,
        "group_complete": True,
        "constraint_invariant": True,
        "admitted_set_invariant": True,
        "certification_scope": (
            "orbits certified relative to the DECLARED site group only; "
            "not the full molecular automorphism group"
        ),
    }
    return orbits, audit


def molecular_orbits(
    classes: Sequence[ShapeClass],
    group: Sequence[Sequence[int]],
    site_permutation: Sequence[int],
    spec: CoordinationSpec,
    prefix: str = "O",
) -> tuple[list[MolecularOrbit], dict[str, Any]]:
    """Quotient shape classes by one site generator's declared group.

    Convenience wrapper closing the single generator (no involution
    hardcoding); certification scope is the declared group, reported in the
    audit.
    """
    site_group = declared_site_group(spec, site_permutation=site_permutation)
    return site_group_orbits(classes, group, site_group, spec, prefix)


def molecular_burnside_count(
    classes: Sequence[ShapeClass],
    shape_group: Sequence[Sequence[int]],
    site_group: SiteGroup,
) -> dict[str, Any]:
    """Independently certify molecular orbits via Burnside's lemma.

    Fixed shape classes are counted directly per site-group element over the
    canonical representatives — independent of the orbit-partition path, so
    equality with the partitioned count is a genuine cross-check of the
    molecular/product-action layer (not just the shape-rotation layer).
    """
    if not site_group.complete or site_group.order is None:
        raise ValueError("molecular Burnside requires a complete declared site group")
    reps = [cls.representative for cls in classes]
    fixed_counts: list[int] = []
    for element in site_group.elements:
        fixed = sum(
            1
            for rep in reps
            if canonical_representative(_relabel(rep, element), shape_group) == rep
        )
        fixed_counts.append(fixed)
    total = sum(fixed_counts)
    if total % site_group.order != 0:
        raise ValueError("molecular Burnside sum not divisible by declared group order")
    return {
        "orbit_count": total // site_group.order,
        "declared_group_order": site_group.order,
        "fixed_point_sum": total,
        "fixed_point_counts": fixed_counts,
        "class_count": len(reps),
        "exact": True,
    }


def command_key(
    center: int,
    shape_name: str,
    placement: Sequence[int],
    site_ids: Sequence[str] | None = None,
    vertex_names: Sequence[str] | None = None,
) -> dict[str, Any]:
    """Build the narrow coordination state key (COMMAND and OBSERVED form).

    The key carries indexed identity only: metal center index, shape name,
    canonical labeled placement (vertex indices in site order), and the
    indexed site-to-vertex-name mapping derived from it.  Shape-class display
    ids (``L04``) are provenance, never StateKey content.  Enumeration
    (commanded) and perception (observed) use exactly these keys and types so
    core parent locks compare indexed identity.
    """
    from .shapes import get_shape

    template = get_shape(shape_name)
    place = [int(v) for v in placement]
    if len(place) != template.coordination_number:
        raise ValueError("placement length incompatible with shape")
    if any(not 0 <= v < template.coordination_number for v in place):
        raise ValueError("placement holds out-of-range vertex indices")
    sites = list(site_ids) if site_ids is not None else [f"site{i}" for i in range(len(place))]
    if len(sites) != len(place):
        raise ValueError("site id count disagrees with placement length")
    names = list(vertex_names) if vertex_names is not None else list(template.vertex_names)
    return {
        "center": int(center),
        "shape": str(shape_name),
        "placement": list(place),
        "sites": {site: names[vertex] for site, vertex in zip(sites, place)},
    }


def normalize_command_key(key: Mapping[str, Any]) -> dict[str, Any]:
    """Validate and normalize a coordination key to canonical types."""
    if not isinstance(key, Mapping):
        raise ValueError("coordination key must be a mapping")
    try:
        center = int(key["center"])
        shape = str(key["shape"])
        placement = [int(v) for v in key["placement"]]
        sites = {str(site): str(vertex) for site, vertex in dict(key["sites"]).items()}
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError(f"malformed coordination key: {exc}") from exc
    canonical = command_key(center, shape, placement, list(sites))
    if canonical["sites"] != sites:
        raise ValueError("sites mapping disagrees with canonical placement")
    return canonical


def state_matches(expected: Mapping[str, Any], observed: Mapping[str, Any]) -> bool:
    """Return whether two coordination keys express identical indexed state.

    Compares center index, shape, canonical placement, and the full
    site-to-vertex mapping (sites must additionally equal the names derived
    from the placement, so a renamed-but-moved state never matches).
    """
    try:
        want = normalize_command_key(expected)
        got = normalize_command_key(observed)
    except ValueError:
        return False
    return bool(
        want["center"] == got["center"]
        and want["shape"] == got["shape"]
        and want["placement"] == got["placement"]
        and want["sites"] == got["sites"]
    )


def burnside_orbit_count(
    placements: Sequence[Sequence[int]], group: Sequence[Sequence[int]]
) -> dict[str, Any]:
    """Independently count orbits via Burnside's lemma on the admitted set.

    Fixed points are counted directly per group element, independent of the
    canonical-representative enumeration path, so equality of the two counts
    is a genuine cross-check.
    """
    admitted = [tuple(map(int, placement)) for placement in placements]
    member_set = set(admitted)
    fixed_counts: list[int] = []
    for perm in group:
        fixed = sum(
            1 for placement in admitted if tuple(perm[value] for value in placement) == placement
        )
        fixed_counts.append(fixed)
    order = len(group)
    total = sum(fixed_counts)
    if total % order != 0:
        raise ValueError("Burnside sum not divisible by group order; check group completeness")
    return {
        "orbit_count": total // order,
        "group_order": order,
        "fixed_point_sum": total,
        "fixed_point_counts": fixed_counts,
        "admitted_count": len(member_set),
        "exact": True,
    }


def enumerate_targets(
    spec: CoordinationSpec,
    shape_name: str,
    site_permutation: Sequence[int] | None = None,
    site_generators: Sequence[Sequence[int]] | None = None,
    group_budget: int = 10000,
) -> dict[str, Any]:
    """Run the full Declare-to-target pipeline for one shape.

    Returns raw/policy/shape/sigma layers, shape classes, filters, the
    declared site group with completeness proof, molecular orbits certified
    relative to that group, and two independent Burnside checks (shape
    rotations on admitted placements; site group on shape classes).
    Realization targets for the original nonsymmetric input equal the shape
    classes (12 for TS1 octahedral); group orbits never suppress geometry
    targets without an independent H_geom proof.
    """
    template = get_shape(shape_name)
    if template.coordination_number != spec.coordination_number:
        raise ValueError(f"shape {shape_name} incompatible with CN={spec.coordination_number}")
    group = proper_rotation_group(shape_name)
    raw = list(enumerate_labeled_placements(spec.coordination_number))
    admissible, excluded = apply_policy_filters(iter(raw), spec, template)
    policy_excluded = sum(1 for item in excluded if item.verdict == "REJECTED_BY_POLICY")
    proof_excluded = sum(1 for item in excluded if item.verdict == "PROVEN_INFEASIBLE")
    before = shape_classes(raw, group)
    classes = shape_classes(admissible, group)
    burnside = burnside_orbit_count(admissible, group)
    if burnside["orbit_count"] != len(classes):
        raise ValueError("Burnside cross-check disagrees with shape-class enumeration")
    orbits: list[MolecularOrbit] = []
    orbit_audit: dict[str, Any] = {"orbit_count": None}
    site_group: SiteGroup | None = None
    molecular_burnside: dict[str, Any] | None = None
    if site_permutation is not None or site_generators is not None:
        site_group = declared_site_group(
            spec,
            site_permutation=site_permutation,
            site_generators=site_generators,
            budget=group_budget,
        )
        orbits, orbit_audit = site_group_orbits(classes, group, site_group, spec)
        molecular_burnside = molecular_burnside_count(classes, group, site_group)
        if molecular_burnside["orbit_count"] != len(orbits):
            raise ValueError("molecular Burnside disagrees with site-group orbit partition")
    layers = CountLayers(
        raw_assignments=len(raw),
        policy_excluded=policy_excluded,
        proof_excluded=proof_excluded,
        admissible=len(admissible),
        shape_classes_before_policy=len(before),
        shape_classes=len(classes),
        molecular_orbits=(len(orbits) if orbits else None),
        exact=True,
    )
    return {
        "shape": shape_name,
        "group_order": len(group),
        "layers": layers,
        "shape_classes": classes,
        "shape_classes_before_policy": before,
        "excluded": excluded,
        "site_group": site_group,
        "molecular_orbits": orbits,
        "orbit_audit": orbit_audit,
        "burnside": burnside,
        "molecular_burnside": molecular_burnside,
        "realization_target_ids": [cls.id for cls in classes],
    }
