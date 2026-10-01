#!/usr/bin/env python3

"""ConfGen v3 ring-lane geometric realization.

A realization target assigns one declared template per enumerated ring system.
Each ring is embedded by proper-rotation Kabsch alignment of the template onto
the current ring atoms (stable anchor/traversal/direction: template index j
maps to traversal atom j), which satisfies ring closure constraints by
construction. Substituent fragments propagate rigidly through per-anchor local
frames; there are intentionally no independent ring-bond rotations.

Emitted states use the canonical StateKey rule (template id + canonical
internal torsion descriptor + anchor/traversal); the actual measured torsions
ride in per-system diagnostics and are the quantities checked by
geometry/parent-lock audits, never snapped before verification.

Fail-closed scope (all explicit, never silent):

- ring sizes other than 4/5/6 (including macrocycles): unsupported;
- traversal pairs that are not covalently bonded: unsupported;
- fused/bridged rings (extra intra-ring covalent bonds short-circuiting the
  traversal), overlapping systems, spiro sharing: unsupported;
- acyclic single bonds BETWEEN ring systems (biphenyl-like links): supported
  with an explicit linking-bond length audit (link_bond_atol);
- chelate content (metal atom inside a ring): unsupported;
- multi-anchor substituent fragments: unsupported;
- degenerate local frames or non-finite input: numerical failure;
- clash, ring-bond drift beyond tolerance, stereo parity flip: geometry failure;
- atoms covered by coordination binding sites: unsupported (coordination is
  owned by another lane; the ring stage must not silently damage it).

Dependencies: stdlib + NumPy only.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .geometry import (
    DegenerateFrameError,
    clash_pairs,
    kabsch_proper,
    local_frame,
    partition_substituents,
    rotation_between_frames,
    topological_distances,
)
from .perception import perceive_ring, ring_diagnostics, ring_state_dict
from .templates import get_template, template_coords

__all__ = [
    "METAL_ELEMENTS",
    "RingGeometryFailure",
    "RingNumericalFailure",
    "RingRealizeOutput",
    "RingSpec",
    "RingTolerances",
    "RingUnsupported",
    "SUPPORTED_RING_SIZES",
    "parse_ring_specs",
    "realize_rings",
    "realize_single_system",
    "validate_ring_system",
]

#: Isolated ring sizes in declared scope.
SUPPORTED_RING_SIZES = (4, 5, 6)

#: Elements treated as metals for chelate detection (unsupported scope).
METAL_ELEMENTS = frozenset(
    {
        "LI",
        "BE",
        "NA",
        "MG",
        "AL",
        "K",
        "CA",
        "SC",
        "TI",
        "V",
        "CR",
        "MN",
        "FE",
        "CO",
        "NI",
        "CU",
        "ZN",
        "GA",
        "RB",
        "SR",
        "Y",
        "ZR",
        "NB",
        "MO",
        "TC",
        "RU",
        "RH",
        "PD",
        "AG",
        "CD",
        "IN",
        "SN",
        "SB",
        "CS",
        "BA",
        "LA",
        "CE",
        "PR",
        "ND",
        "PM",
        "SM",
        "EU",
        "GD",
        "TB",
        "DY",
        "HO",
        "ER",
        "TM",
        "YB",
        "LU",
        "HF",
        "TA",
        "W",
        "RE",
        "OS",
        "IR",
        "PT",
        "AU",
        "HG",
        "TL",
        "PB",
        "BI",
        "PO",
        "FR",
        "RA",
        "AC",
        "TH",
        "PA",
        "U",
    }
)


class RingUnsupported(ValueError):
    """Explicitly unsupported requested ring generation (fail closed)."""


class RingNumericalFailure(ValueError):
    """Numerical failure: non-finite input or degenerate frame/solver state."""


class RingGeometryFailure(ValueError):
    """Geometric failure: clash, bond drift, or stereo parity violation."""


@dataclass(frozen=True, slots=True)
class RingTolerances:
    """Stage-specific realistic geometric tolerances for the ring solver."""

    ring_bond_atol: float = 0.08
    substituent_bond_atol: float = 1e-6
    clash_threshold: float = 0.65
    frame_det_min: float = 1e-8
    link_bond_atol: float = 0.15


@dataclass(frozen=True, slots=True)
class RingSpec:
    """One normalized ring axis entry."""

    id: str
    atoms: tuple[int, ...]
    treatment: str = "enumerate"
    templates: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class RingRealizeOutput:
    """Realization outcome over the full structure for a target."""

    coordinates: tuple[tuple[float, float, float], ...]
    states: tuple[dict[str, object], ...]
    audits: tuple[dict[str, object], ...]
    preserved: bool


def parse_ring_specs(raw_rings: object) -> tuple[RingSpec, ...]:
    """Normalize raw ring axis entries; raise ``RingUnsupported`` when invalid."""
    if raw_rings is None:
        return ()
    if not isinstance(raw_rings, (list, tuple)):
        raise RingUnsupported("rings spec must be a list")
    specs: list[RingSpec] = []
    seen: set[str] = set()
    for entry in raw_rings:
        if not isinstance(entry, dict):
            raise RingUnsupported("ring entry must be a mapping")
        ring_id = entry.get("id")
        atoms = entry.get("atoms")
        if not isinstance(ring_id, str) or not ring_id:
            raise RingUnsupported("ring entry needs a non-empty string id")
        if ring_id in seen:
            raise RingUnsupported(f"duplicate ring id {ring_id!r}")
        seen.add(ring_id)
        if (
            not isinstance(atoms, (list, tuple))
            or len(atoms) == 0
            or any(isinstance(v, bool) or not isinstance(v, int) or v < 0 for v in atoms)
            or len(set(atoms)) != len(atoms)
        ):
            raise RingUnsupported(f"ring {ring_id!r} needs distinct 0-based atom indices")
        treatment = entry.get("treatment", "enumerate")
        if treatment not in ("enumerate", "preserve_input"):
            raise RingUnsupported(f"ring {ring_id!r} has unsupported treatment {treatment!r}")
        templates = entry.get("templates", ())
        if templates is None:
            templates = ()
        if not isinstance(templates, (list, tuple)) or any(
            not isinstance(name, str) for name in templates
        ):
            raise RingUnsupported(f"ring {ring_id!r} templates must be names")
        specs.append(
            RingSpec(
                id=ring_id,
                atoms=tuple(int(v) for v in atoms),
                treatment=treatment,
                templates=tuple(templates),
            )
        )
    specs.sort(key=lambda item: item.id)
    return tuple(specs)


def _get_template_or_unsupported(name: str):
    try:
        return get_template(name)
    except KeyError as exc:
        raise RingUnsupported(f"unknown_template:{name}") from exc


def _as_adjacency(adjacency: object, n_atoms: int) -> list[list[int]]:
    """Normalize neighbour lists; raise numerical failure when malformed."""
    if not isinstance(adjacency, (list, tuple)) or len(adjacency) != n_atoms:
        raise RingNumericalFailure("adjacency length must match atom count")
    normalized: list[list[int]] = []
    for index, neighbours in enumerate(adjacency):
        if not isinstance(neighbours, (list, tuple)):
            raise RingNumericalFailure(f"adjacency[{index}] must be a neighbour list")
        row: list[int] = []
        for value in neighbours:
            if isinstance(value, bool) or not isinstance(value, int):
                raise RingNumericalFailure(f"adjacency[{index}] holds a non-integer")
            if value < 0 or value >= n_atoms or value == index:
                raise RingNumericalFailure(f"adjacency[{index}] holds an invalid index")
            row.append(value)
        normalized.append(sorted(set(row)))
    return normalized


def _check_finite(coords: np.ndarray) -> None:
    if not np.all(np.isfinite(coords)):
        raise RingNumericalFailure("nonfinite input coordinates")


def validate_ring_system(
    spec: RingSpec,
    adjacency: list[list[int]],
    elements: list[str],
    other_ring_atoms: frozenset[int],
    coordination_atoms: frozenset[int] = frozenset(),
) -> None:
    """Fail closed on every out-of-scope ring request; return ``None`` when clean."""
    n_atoms = len(adjacency)
    size = len(spec.atoms)
    if size not in SUPPORTED_RING_SIZES:
        raise RingUnsupported(f"unsupported_ring_size:{size}")
    if any(atom >= n_atoms for atom in spec.atoms):
        raise RingUnsupported("ring atom index out of range")
    ring_set = frozenset(spec.atoms)
    if ring_set & other_ring_atoms:
        raise RingUnsupported("overlapping_systems")
    if ring_set & coordination_atoms:
        raise RingUnsupported("coordination_overlap")
    for atom in spec.atoms:
        if elements[atom].upper() in METAL_ELEMENTS:
            raise RingUnsupported("chelate")
    for position, atom in enumerate(spec.atoms):
        nxt = spec.atoms[(position + 1) % size]
        if nxt not in adjacency[atom]:
            raise RingUnsupported("nonbonded_traversal")
    for position, atom in enumerate(spec.atoms):
        expected = {spec.atoms[(position - 1) % size], spec.atoms[(position + 1) % size]}
        ring_neighbours = set(adjacency[atom]) & ring_set
        if ring_neighbours != expected:
            raise RingUnsupported("fused_or_bridged")
    # Bonds from ring atoms to OTHER ring systems are ordinary acyclic
    # single-bond links (biphenyl-like), not fused/bridged scope violations.
    # The linking bond lengths are audited at realization (link_bond_atol);
    # only non-ring fragments touching two or more blocked atoms are
    # rejected as multi-anchor coupling (see partition_substituents).
    for name in spec.templates:
        try:
            template = get_template(name)
        except KeyError as exc:
            raise RingUnsupported(f"unknown_template:{name}") from exc
        if template.ring_size != size:
            raise RingUnsupported(f"template_size_mismatch:{name}")


def _signed_volume(a: np.ndarray, b: np.ndarray, c: np.ndarray, d: np.ndarray) -> float:
    return float(np.dot(b - a, np.cross(c - a, d - a)))


def realize_single_system(
    coords: np.ndarray,
    elements: list[str],
    adjacency: list[list[int]],
    spec: RingSpec,
    template_name: str,
    *,
    tolerances: RingTolerances | None = None,
    other_ring_atoms: frozenset[int] = frozenset(),
    blocked_atoms: frozenset[int] | None = None,
    coordination_atoms: frozenset[int] = frozenset(),
    topo: list[list[int]] | None = None,
) -> tuple[np.ndarray, dict[str, object], dict[str, object]]:
    """Embed one template; return (new_coords, state, audit).

    Raises :class:`RingUnsupported`, :class:`RingNumericalFailure`, or
    :class:`RingGeometryFailure`; never returns a silently damaged structure.
    """
    _check_finite(coords)
    resolved = RingTolerances() if tolerances is None else tolerances
    positions = np.asarray(coords, dtype=float)
    validate_ring_system(
        spec,
        adjacency,
        elements,
        other_ring_atoms,
        coordination_atoms=coordination_atoms,
    )
    template = _get_template_or_unsupported(template_name)
    if template.ring_size != len(spec.atoms):
        raise RingUnsupported(f"template_size_mismatch:{template_name}")
    ring_order = list(spec.atoms)
    ring_set = frozenset(ring_order)
    blocked = ring_set if blocked_atoms is None else frozenset(blocked_atoms) | ring_set
    substituents, multi_anchor = partition_substituents(
        len(elements), adjacency, ring_set, blocked=blocked
    )
    if multi_anchor:
        raise RingUnsupported(f"multi_anchor:{multi_anchor[0]['atom']}")
    old_ring = positions[ring_order]
    new_ring = template_coords(template)
    rotation, translation = kabsch_proper(new_ring, old_ring)
    try:
        placed_ring = new_ring @ rotation.T + translation
    except ValueError as exc:
        raise RingNumericalFailure("degenerate_alignment") from exc
    new_positions = positions.copy()
    new_positions[ring_order] = placed_ring
    size = len(ring_order)
    for position, atom in enumerate(ring_order):
        prev_old = positions[ring_order[(position - 1) % size]]
        next_old = positions[ring_order[(position + 1) % size]]
        prev_new = new_positions[ring_order[(position - 1) % size]]
        next_new = new_positions[ring_order[(position + 1) % size]]
        try:
            frame_old = local_frame(prev_old, positions[atom], next_old)
            frame_new = local_frame(prev_new, new_positions[atom], next_new)
            step = rotation_between_frames(frame_old, frame_new)
        except (DegenerateFrameError, ValueError) as exc:
            raise RingNumericalFailure("degenerate_frame") from exc
        for sub in substituents:
            if sub.anchor != atom:
                continue
            for member in sub.members:
                new_positions[member] = new_positions[atom] + step @ (
                    positions[member] - positions[atom]
                )
    audit: dict[str, object] = {"ring_id": spec.id, "template": template_name}
    ring_bonds_new = [
        float(
            np.linalg.norm(new_positions[ring_order[(k + 1) % size]] - new_positions[ring_order[k]])
        )
        for k in range(size)
    ]
    ring_bonds_old = [
        float(np.linalg.norm(positions[ring_order[(k + 1) % size]] - positions[ring_order[k]]))
        for k in range(size)
    ]
    drift = max(abs(n - o) for n, o in zip(ring_bonds_new, ring_bonds_old))
    audit["ring_bond_drift"] = drift
    audit["ring_bonds_new"] = [round(v, 6) for v in ring_bonds_new]
    if drift > resolved.ring_bond_atol:
        raise RingGeometryFailure(f"ring_bond:{drift:.4f}")
    worst_sub = 0.0
    for sub in substituents:
        old_len = float(np.linalg.norm(positions[sub.root] - positions[sub.anchor]))
        new_len = float(np.linalg.norm(new_positions[sub.root] - new_positions[sub.anchor]))
        worst_sub = max(worst_sub, abs(new_len - old_len))
    audit["substituent_bond_drift"] = worst_sub
    if worst_sub > resolved.substituent_bond_atol:
        raise RingGeometryFailure(f"substituent_bond:{worst_sub:.3e}")
    for sub in substituents:
        anchor_neighbours = [n for n in adjacency[sub.anchor] if n != sub.root]
        if len(anchor_neighbours) < 2:
            continue
        ref = anchor_neighbours[:2]
        old_vol = _signed_volume(
            positions[sub.anchor], positions[sub.root], positions[ref[0]], positions[ref[1]]
        )
        new_vol = _signed_volume(
            new_positions[sub.anchor],
            new_positions[sub.root],
            new_positions[ref[0]],
            new_positions[ref[1]],
        )
        if old_vol == 0.0 or new_vol == 0.0:
            continue
        if (old_vol > 0.0) != (new_vol > 0.0):
            raise RingGeometryFailure("stereo")
    audit["stereo_checked"] = True
    topo_matrix = topo if topo is not None else topological_distances(adjacency)
    clashes, fallback = clash_pairs(
        new_positions, elements, topo_matrix, threshold=resolved.clash_threshold
    )
    audit["clashes"] = [
        {"i": i, "j": j, "dist": round(d, 6), "limit": round(lim, 6)}
        for i, j, d, lim in clashes[:8]
    ]
    audit["fallback_radii"] = fallback
    if clashes:
        worst = clashes[0]
        raise RingGeometryFailure(f"clash:{worst[0]}-{worst[1]}:{worst[2]:.3f}")
    perception = perceive_ring(new_positions[ring_order])
    # Measured state (narrow identity); agreement with the commanded target
    # is checked tolerance-aware via ring_states_match, never assumed.
    state = ring_state_dict(perception, anchor=spec.atoms[0])
    audit["perceived"] = perception.best_template
    audit["diagnostics"] = ring_diagnostics(perception)
    return new_positions, state, audit


def realize_rings(
    coords: np.ndarray,
    elements: list[str],
    adjacency: list[list[int]] | tuple[tuple[int, ...], ...],
    specs: tuple[RingSpec, ...],
    assignment: dict[str, str],
    *,
    tolerances: RingTolerances | None = None,
    coordination_atoms: frozenset[int] = frozenset(),
) -> RingRealizeOutput:
    """Realize one full-structure target over sorted ring systems.

    ``assignment`` maps enumerated ring ids to template names;
    ``preserve_input`` systems are measured and reported with unchanged
    coordinates. Raises the ``Ring*`` errors fail-closed.
    """
    resolved = RingTolerances() if tolerances is None else tolerances
    positions = np.asarray(coords, dtype=float)
    _check_finite(positions)
    graph = _as_adjacency(adjacency, len(elements))
    all_ring_atoms: frozenset[int] = (
        frozenset().union(*(frozenset(spec.atoms) for spec in specs)) if specs else frozenset()
    )
    seen: dict[int, str] = {}
    for spec in specs:
        for atom in spec.atoms:
            if atom in seen:
                raise RingUnsupported("overlapping_systems")
            seen[atom] = spec.id
    topo_matrix = topological_distances(graph)
    working = positions.copy()
    states: list[dict[str, object]] = []
    audits: list[dict[str, object]] = []
    preserved = True
    for spec in specs:
        others = all_ring_atoms - frozenset(spec.atoms)
        if spec.treatment == "preserve_input":
            validate_ring_system(
                spec, graph, elements, others, coordination_atoms=coordination_atoms
            )
            perception = perceive_ring(working[list(spec.atoms)])
            states.append(ring_state_dict(perception, anchor=spec.atoms[0]))
            audits.append(
                {
                    "ring_id": spec.id,
                    "treatment": spec.treatment,
                    "preserved": True,
                    "diagnostics": ring_diagnostics(perception),
                }
            )
            continue
        if spec.id not in assignment:
            raise RingNumericalFailure(f"missing_assignment:{spec.id}")
        template_name = assignment[spec.id]
        pre_step = working.copy()
        new_working, state, audit = realize_single_system(
            working,
            elements,
            graph,
            spec,
            template_name,
            tolerances=resolved,
            other_ring_atoms=others,
            blocked_atoms=all_ring_atoms,
            coordination_atoms=coordination_atoms,
            topo=topo_matrix,
        )
        # Cross-system protection: no other ring atom may have moved.
        moved = [
            int(atom)
            for atom in others
            if float(np.linalg.norm(new_working[atom] - working[atom])) > 1e-9
        ]
        if moved:
            raise RingGeometryFailure(f"cross_system_damage:{moved[0]}")
        # Linking-bond audit: acyclic single bonds to other ring systems must
        # survive realization within link_bond_atol (fail closed, explicit).
        worst_link = 0.0
        worst_link_pair: tuple[int, int] | None = None
        for atom in spec.atoms:
            for neighbour in graph[atom]:
                if neighbour not in others:
                    continue
                old_len = float(np.linalg.norm(pre_step[atom] - pre_step[neighbour]))
                new_len = float(np.linalg.norm(new_working[atom] - new_working[neighbour]))
                drift = abs(new_len - old_len)
                if drift > worst_link:
                    worst_link = drift
                    worst_link_pair = (atom, neighbour)
        audit["treatment"] = spec.treatment
        audit["link_bond_drift"] = round(worst_link, 6)
        if worst_link_pair is not None:
            audit["link_bond_pair"] = list(worst_link_pair)
        if worst_link > resolved.link_bond_atol:
            raise RingGeometryFailure(f"link_bond:{worst_link:.4f}")
        working = new_working
        states.append(state)
        audits.append(audit)
        preserved = False
    frozen = tuple(tuple(float(v) for v in row) for row in working)
    return RingRealizeOutput(
        coordinates=frozen, states=tuple(states), audits=tuple(audits), preserved=preserved
    )
