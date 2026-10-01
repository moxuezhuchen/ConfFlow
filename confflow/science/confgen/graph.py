#!/usr/bin/env python3
"""Typed molecular topology graph for ConfGen v3 coordination science.

Lane-B owned single typed-graph authority (per the shared graph gate):
stdlib + NumPy only.  No legacy runner imports, no orchestration imports,
no energy evaluation.

The typed graph is the fixed context authority for coordination enumeration:

- ``COVALENT`` edges carry ordinary bonding (with lossless ``bond_order``).
- ``COORDINATION`` edges link the single metal center to each donor atom.
- ``FORMING`` edges are reaction relations (for example the TS O74-C79
  forming contact).  Geometric integrity audits must never reinterpret a
  reaction edge as a covalent bond.
- ``BREAKING`` edges mark bonds declared broken along the reaction path;
  they are authority context, never ordinary covalent bonds.

Public edge kinds are UPPERCASE, matching the fixture convention; lowercase
core spellings are accepted on input and normalized explicitly.  Atom indices
are internal zero-based throughout this module.  Fixture files and workflow
spec mappings use 1-based indices; loaders convert under an explicit declared
convention (``fixture1`` or ``internal0``).  Workflow parsers build records
from config mappings via :meth:`TypedGraph.from_mapping` — file paths are a
test/fixture convenience, never the workflow authority.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any

__all__ = [
    "EdgeType",
    "AtomRef",
    "ScopedAtomRef",
    "TypedEdge",
    "BindingSite",
    "ForbiddenTrans",
    "BoundEvidence",
    "DonorBoundProof",
    "CoordinationSpec",
    "TypedGraph",
    "AutomorphismReport",
    "CoordinationMap",
    "UnsupportedTopologyError",
    "SUPPORTED_SHAPES",
    "CN_SHAPES",
    "CN_RANGE",
    "EDGE_KINDS",
    "INDEX_CONVENTIONS",
    "COVALENT_RADII",
    "normalize_edge_kind",
    "verify_bound_evidence",
    "fragment_distance_interval",
    "build_scoped_refs",
    "check_scoped_refs",
    "load_typed_topology",
    "load_xyz_frame",
    "extract_coordination_map",
    "site_label_index",
]

#: Shapes with registered ideal templates (coordination lane authority).
SUPPORTED_SHAPES: tuple[str, ...] = (
    "tetrahedral",
    "square_planar",
    "trigonal_bipyramidal",
    "square_pyramidal",
    "octahedral",
    "trigonal_prismatic",
)

#: Supported coordination numbers and their admissible shapes.
CN_SHAPES: dict[int, tuple[str, ...]] = {
    4: ("tetrahedral", "square_planar"),
    5: ("trigonal_bipyramidal", "square_pyramidal"),
    6: ("octahedral", "trigonal_prismatic"),
}

#: Inclusive coordination-number range for the single-metal model.
CN_RANGE: tuple[int, int] = (4, 6)


class EdgeType(str, Enum):
    """Typed topology edge kinds (public uppercase, fixture convention)."""

    COVALENT = "COVALENT"
    COORDINATION = "COORDINATION"
    FORMING = "FORMING"
    BREAKING = "BREAKING"


#: Public edge-kind vocabulary (uppercase authority).
EDGE_KINDS: tuple[str, ...] = tuple(kind.value for kind in EdgeType)

#: Explicit index-convention vocabulary for mapping/file boundaries.
INDEX_CONVENTIONS: tuple[str, ...] = ("internal0", "fixture1")

#: Lowercase core spellings accepted on input, normalized explicitly.
_LOWERCASE_KINDS: dict[str, EdgeType] = {
    "covalent": EdgeType.COVALENT,
    "coordination": EdgeType.COORDINATION,
    "forming": EdgeType.FORMING,
    "breaking": EdgeType.BREAKING,
}


def normalize_edge_kind(value: Any) -> EdgeType:
    """Normalize an edge-kind spelling to the uppercase authority.

    Accepts :class:`EdgeType`, exact uppercase names, and the lowercase core
    spellings (normalized explicitly).  Anything else fails closed.
    """
    if isinstance(value, EdgeType):
        return value
    if isinstance(value, str):
        text = value.strip()
        if text in EDGE_KINDS:
            return EdgeType(text)
        lowered = text.lower()
        if lowered in _LOWERCASE_KINDS:
            return _LOWERCASE_KINDS[lowered]
    raise UnsupportedTopologyError(f"unknown edge kind {value!r}")


class UnsupportedTopologyError(ValueError):
    """Fail-closed error for topologies outside the lane scope."""


@dataclass(frozen=True, slots=True)
class AtomRef:
    """One atom in the typed graph (zero-based index).

    ``role`` carries workflow roles (``metal_center``, ``binding_site:...``,
    ``forming_atom``, ...); ``stereo`` carries an optional stereo declaration
    label owned by the coordination/stereo audit (never inferred here).
    """

    index: int
    element: str
    label: str | None = None
    role: str = ""
    stereo: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.index, int) or isinstance(self.index, bool) or self.index < 0:
            raise UnsupportedTopologyError(
                f"atom index must be a non-negative int, got {self.index!r}"
            )
        if not isinstance(self.element, str) or not self.element:
            raise UnsupportedTopologyError("atom element must be a non-empty string")
        object.__setattr__(self, "element", self.element.strip())
        if self.label is not None and not isinstance(self.label, str):
            raise UnsupportedTopologyError("atom label must be a string or None")
        if not isinstance(self.role, str):
            raise UnsupportedTopologyError("atom role must be a string")
        if self.stereo is not None and not isinstance(self.stereo, str):
            raise UnsupportedTopologyError("atom stereo declaration must be a string or None")

    def to_mapping(self, *, convention: str = "internal0") -> dict[str, Any]:
        """Return a config-mapping record for this atom."""
        if convention == "fixture1":
            index: Any = self.index + 1
        elif convention == "internal0":
            index = self.index
        else:
            raise UnsupportedTopologyError(f"unknown index convention {convention!r}")
        record: dict[str, Any] = {"index": index, "element": self.element}
        if self.label is not None:
            record["label"] = self.label
        if self.role:
            record["role"] = self.role
        if self.stereo is not None:
            record["stereo"] = self.stereo
        return record


@dataclass(frozen=True, slots=True)
class TypedEdge:
    """One typed undirected edge (zero-based endpoints, ``a < b``).

    ``bond_order`` is lossless covalent metadata (``None`` when undeclared);
    ``provenance`` names the record source.  Kind spellings are normalized to
    the uppercase authority on construction.
    """

    a: int
    b: int
    type: EdgeType
    bond_order: float | None = None
    provenance: str = "explicit"

    def __post_init__(self) -> None:
        for name, value in (("a", self.a), ("b", self.b)):
            if not isinstance(value, int) or isinstance(value, bool) or value < 0:
                raise UnsupportedTopologyError(f"edge {name} must be a non-negative int")
        if self.a == self.b:
            raise UnsupportedTopologyError("self-loop edges are not supported")
        if self.a > self.b:
            a, b = self.b, self.a
            object.__setattr__(self, "a", a)
            object.__setattr__(self, "b", b)
        try:
            kind = normalize_edge_kind(self.type)
        except UnsupportedTopologyError as exc:
            raise UnsupportedTopologyError(f"edge kind invalid: {exc}") from exc
        object.__setattr__(self, "type", kind)
        if self.bond_order is not None:
            order = self.bond_order
            if isinstance(order, bool) or not isinstance(order, (int, float)):
                raise UnsupportedTopologyError("bond_order must be a number or None")
            if not (0.0 < float(order) <= 4.0):
                raise UnsupportedTopologyError(f"bond_order out of range: {order!r}")
            object.__setattr__(self, "bond_order", float(order))
        if not isinstance(self.provenance, str) or not self.provenance:
            raise UnsupportedTopologyError("edge provenance must be a non-empty string")

    @property
    def pair(self) -> tuple[int, int]:
        """Return the canonical unordered atom pair."""
        return (self.a, self.b)

    def to_mapping(self, *, convention: str = "internal0") -> dict[str, Any]:
        """Return a config-mapping record for this edge."""
        if convention == "fixture1":
            atoms: Any = [self.a + 1, self.b + 1]
        elif convention == "internal0":
            atoms = [self.a, self.b]
        else:
            raise UnsupportedTopologyError(f"unknown index convention {convention!r}")
        record: dict[str, Any] = {"atoms": atoms, "kind": self.type.value}
        if self.bond_order is not None:
            record["bond_order"] = self.bond_order
        if self.provenance != "explicit":
            record["provenance"] = self.provenance
        return record


@dataclass(frozen=True, slots=True)
class BindingSite:
    """One monodentate binding site (single donor atom)."""

    id: str
    kind: str = "atom"
    atoms: tuple[int, ...] = ()
    hapticity: int = 1

    def __post_init__(self) -> None:
        if not self.id or not isinstance(self.id, str):
            raise UnsupportedTopologyError("binding site id must be a non-empty string")
        if self.kind != "atom":
            raise UnsupportedTopologyError(
                f"binding site {self.id!r}: only kind='atom' is supported, got {self.kind!r}"
            )
        atoms = tuple(self.atoms)
        object.__setattr__(self, "atoms", atoms)
        if self.hapticity != 1 or len(atoms) != 1:
            raise UnsupportedTopologyError(
                f"binding site {self.id!r}: only monodentate hapticity=1 sites are "
                "supported (multi-hapto/chelate is explicitly out of scope)"
            )

    @property
    def donor(self) -> int:
        """Return the single donor atom index."""
        return self.atoms[0]


@dataclass(frozen=True, slots=True)
class ForbiddenTrans:
    """A declared forbidden-trans constraint between two site labels.

    ``classification`` is ``REJECTED_BY_POLICY`` unless a machine-verifiable
    :class:`DonorBoundProof` upgrades it to ``PROVEN_INFEASIBLE``.  Nothing in
    this module auto-upgrades policy to proof.
    """

    id: str
    sites: tuple[str, str]
    classification: str = "REJECTED_BY_POLICY"
    provenance: str = ""
    proof: DonorBoundProof | None = None

    def __post_init__(self) -> None:
        if not self.id:
            raise UnsupportedTopologyError("constraint id must be non-empty")
        sites = (str(self.sites[0]), str(self.sites[1]))
        object.__setattr__(self, "sites", sites)
        if sites[0] == sites[1]:
            raise UnsupportedTopologyError("forbidden-trans sites must be distinct")
        if self.classification == "PROVEN_INFEASIBLE" and (
            self.proof is None or not self.proof.is_enforced()
        ):
            raise UnsupportedTopologyError(
                f"constraint {self.id}: PROVEN_INFEASIBLE requires an enforced DonorBoundProof"
            )
        if self.classification not in ("REJECTED_BY_POLICY", "PROVEN_INFEASIBLE"):
            raise UnsupportedTopologyError(
                f"constraint {self.id}: unknown classification {self.classification!r}"
            )


#: Covalent radii (Angstrom) for scoped atom references.  Fixed table,
#: documented; shared with the coordination clash guard.
COVALENT_RADII: dict[str, float] = {
    "H": 0.31,
    "C": 0.76,
    "N": 0.71,
    "O": 0.66,
    "Al": 1.21,
    "F": 0.57,
    "P": 1.07,
    "S": 1.05,
    "Cl": 1.02,
}


@dataclass(frozen=True, slots=True)
class BoundEvidence:
    """Verifiable numeric evidence for a donor-pair infeasibility bound.

    ``interval`` is the achievable donor-donor separation under the declared
    model (same rigid fragment plus frozen flex tolerances);
    ``trans_required`` is the opposite-vertex separation range the forbidden
    trans placement would require.  Disjoint intervals exclude the trans
    placement *within that model* — never a universal infeasibility proof.
    """

    pair: tuple[str, str]
    interval: tuple[float, float]
    trans_required: tuple[float, float]
    basis: str

    def excludes_trans(self) -> bool:
        """Return whether the achievable interval excludes trans geometry."""
        (lo, hi), (tlo, thi) = self.interval, self.trans_required
        return hi < tlo or lo > thi


def verify_bound_evidence(evidence: BoundEvidence) -> bool:
    """Verify bound evidence numerically (finite, ordered, disjoint)."""
    if not isinstance(evidence, BoundEvidence):
        return False
    try:
        (lo, hi), (tlo, thi) = evidence.interval, evidence.trans_required
        values = [float(lo), float(hi), float(tlo), float(thi)]
    except (TypeError, ValueError):
        return False
    import math

    if not all(math.isfinite(v) for v in values):
        return False
    if not (lo <= hi and tlo <= thi and values[0] >= 0.0):
        return False
    if not evidence.basis:
        return False
    return evidence.excludes_trans()


def fragment_distance_interval(
    graph: TypedGraph,
    coordinates: Any,
    donor_a: int,
    donor_b: int,
    bond_tol: float = 0.05,
    angle_tol_deg: float = 3.0,
) -> tuple[float, float] | None:
    """Compute the achievable donor separation interval, or ``None``.

    Donors on the same covalent component (cutting COORDINATION edges) sit at
    a fixed input distance widened by a first-order flex slop (path bonds
    times ``bond_tol`` plus path length times the angle tolerance in
    radians).  Donors on different components have no computable interval
    without a global solve: returns ``None`` (proof unsupported there).
    """
    import math

    import numpy as np

    coords = np.asarray(coordinates, dtype=float)
    adjacency: dict[int, set[int]] = {i: set() for i in range(graph.natoms)}
    for edge in graph.edges:
        if edge.type is EdgeType.COORDINATION:
            continue
        adjacency[edge.a].add(edge.b)
        adjacency[edge.b].add(edge.a)
    previous: dict[int, int | None] = {donor_a: None}
    queue = [donor_a]
    while queue:
        node = queue.pop(0)
        if node == donor_b:
            break
        for neighbor in sorted(adjacency[node]):
            if neighbor not in previous:
                previous[neighbor] = node
                queue.append(neighbor)
    if donor_b not in previous:
        return None
    path = [donor_b]
    while path[-1] != donor_a:
        parent = previous[path[-1]]
        assert parent is not None
        path.append(parent)
    path = path[::-1]
    fixed = float(np.linalg.norm(coords[donor_a] - coords[donor_b]))
    # First-order flex slop: bond stretches accumulate along the path and
    # angle bends swing each segment (arc approx).  Conservative by
    # construction within the frozen tolerances.
    path_length = sum(
        float(np.linalg.norm(coords[path[i]] - coords[path[i + 1]])) for i in range(len(path) - 1)
    )
    slop = (len(path) - 1) * bond_tol + path_length * math.radians(angle_tol_deg)
    return (max(0.0, fixed - slop), fixed + slop)


@dataclass(frozen=True, slots=True)
class ScopedAtomRef:
    """One atom scoped to its input structure and local environment.

    Distinct from the bare graph node (:class:`AtomRef`): carries the input
    ``structure_id``, the ``expected_element`` asserted at the boundary, the
    typed covalent ``radius``, and a short ``env_hash`` over the sorted local
    environment (own element plus neighbor ``element:kind`` pairs).  Output
    identity mapping stays the plain 0-based atom index; no UUIDs.
    """

    atom_index: int
    structure_id: str
    expected_element: str
    radius: float
    env_hash: str

    def __post_init__(self) -> None:
        if not isinstance(self.atom_index, int) or self.atom_index < 0:
            raise UnsupportedTopologyError("scoped atom index must be >= 0")
        if not self.structure_id or not isinstance(self.structure_id, str):
            raise UnsupportedTopologyError("scoped structure_id must be non-empty")
        if not self.expected_element or not isinstance(self.expected_element, str):
            raise UnsupportedTopologyError("scoped expected_element must be non-empty")
        if not isinstance(self.radius, float) or not self.radius > 0:
            raise UnsupportedTopologyError("scoped radius must be positive")
        if not self.env_hash or not isinstance(self.env_hash, str):
            raise UnsupportedTopologyError("scoped env_hash must be non-empty")


def build_scoped_refs(graph: TypedGraph, structure_id: str) -> tuple[ScopedAtomRef, ...]:
    """Build scoped references for every graph atom in index order."""
    import hashlib

    refs: list[ScopedAtomRef] = []
    for atom in graph.atoms:
        tags: list[str] = []
        for neighbor in graph.neighbors(atom.index):
            kind = graph.edge_kind(atom.index, neighbor)
            if kind is None:
                raise UnsupportedTopologyError("neighbor without a typed edge")
            tags.append(f"{graph.atoms[neighbor].element}:{kind.value}")
        env = [atom.element] + sorted(tags)
        digest = hashlib.sha256("|".join(env).encode()).hexdigest()[:16]
        refs.append(
            ScopedAtomRef(
                atom_index=atom.index,
                structure_id=str(structure_id),
                expected_element=atom.element,
                radius=float(COVALENT_RADII.get(atom.element, 1.0)),
                env_hash=digest,
            )
        )
    return tuple(refs)


def check_scoped_refs(
    graph: TypedGraph, refs: Sequence[ScopedAtomRef], structure_id: str
) -> list[str]:
    """Fail-closed audit of scoped references against the graph authority."""
    problems: list[str] = []
    expected = {ref.atom_index: ref for ref in refs}
    for atom in graph.atoms:
        ref = expected.get(atom.index)
        if ref is None:
            problems.append(f"atom {atom.index}: scoped reference missing")
            continue
        if ref.structure_id != structure_id:
            problems.append(f"atom {atom.index}: structure scope mismatch")
        if ref.expected_element != atom.element:
            problems.append(
                f"atom {atom.index}: expected {ref.expected_element} vs graph {atom.element}"
            )
    if len(expected) != graph.natoms:
        problems.append("scoped reference count disagrees with graph size")
    return problems


@dataclass(frozen=True, slots=True)
class DonorBoundProof:
    """Hard geometric bounds backing a PROVEN_INFEASIBLE upgrade.

    A proof is only valid when the bounds were declared up front *and*
    enforced by realization and audit.  The enforcement record names the
    backend and audit that enforced the bounds.  Crucially, string fields
    alone never constitute proof: ``evidence`` must carry a verifiable
    :class:`BoundEvidence` record (measured intervals plus a trans
    requirement the interval excludes), otherwise ``is_enforced`` is False
    and PROVEN_INFEASIBLE fails closed — even for internal helpers.  The
    public workflow schema stays policy-only by intent.
    """

    pair: tuple[str, str]
    min_distance: float | None = None
    max_distance: float | None = None
    declared_bounds: tuple[tuple[str, float], ...] = ()
    enforced_by: str = ""
    audit_ref: str = ""
    evidence: BoundEvidence | None = None

    def is_enforced(self) -> bool:
        """Return whether this proof carries verified enforced hard bounds."""
        if not self.enforced_by or not self.audit_ref:
            return False
        if not self.declared_bounds:
            return False
        if self.min_distance is None and self.max_distance is None:
            return False
        if self.evidence is None or not verify_bound_evidence(self.evidence):
            return False
        return True


@dataclass(frozen=True, slots=True)
class CoordinationSpec:
    """Resolved coordination scope for one metal center."""

    metal_center: int
    binding_sites: tuple[BindingSite, ...]
    shapes: tuple[str, ...] = ("auto",)
    treatment: str = "enumerate"
    constraints: tuple[ForbiddenTrans, ...] = ()
    witnesses: tuple[tuple[int, ...], ...] = ()
    budgets: Mapping[str, int] | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.metal_center, int) or self.metal_center < 0:
            raise UnsupportedTopologyError("metal_center must be a non-negative int")
        sites = tuple(self.binding_sites)
        object.__setattr__(self, "binding_sites", sites)
        donors = [site.donor for site in sites]
        if len(set(donors)) != len(donors):
            raise UnsupportedTopologyError("duplicate donor atoms across binding sites")
        if self.metal_center in donors:
            raise UnsupportedTopologyError("metal center must not be a donor atom")
        cn = len(sites)
        if not CN_RANGE[0] <= cn <= CN_RANGE[1]:
            raise UnsupportedTopologyError(
                f"coordination number {cn} outside supported range {CN_RANGE}"
            )
        shapes = tuple(self.shapes)
        if shapes == ("auto",):
            object.__setattr__(self, "shapes", CN_SHAPES[cn])
        else:
            for shape in shapes:
                if shape not in SUPPORTED_SHAPES:
                    raise UnsupportedTopologyError(f"unknown coordination shape {shape!r}")
                if shape not in CN_SHAPES[cn]:
                    raise UnsupportedTopologyError(f"shape {shape!r} is not admissible at CN={cn}")
            object.__setattr__(self, "shapes", shapes)

    @property
    def coordination_number(self) -> int:
        """Return the number of binding sites."""
        return len(self.binding_sites)

    @property
    def site_ids(self) -> tuple[str, ...]:
        """Return site labels in declared order."""
        return tuple(site.id for site in self.binding_sites)

    @property
    def donor_indices(self) -> tuple[int, ...]:
        """Return donor atom indices in declared site order."""
        return tuple(site.donor for site in self.binding_sites)


@dataclass
class TypedGraph:
    """Immutable typed molecular topology.

    Attributes
    ----------
    atoms:
        Atom records in fixed order; position equals zero-based index.
    edges:
        Typed undirected edges.
    metal_center:
        Zero-based index of the single metal center, or ``None``.
    reaction_pairs:
        Zero-based FORMING pairs (reaction relations, never covalent).
    """

    atoms: tuple[AtomRef, ...]
    edges: tuple[TypedEdge, ...]
    metal_center: int | None = None
    reaction_pairs: tuple[tuple[int, int], ...] = ()
    source: str = ""

    def __post_init__(self) -> None:
        atoms = tuple(self.atoms)
        object.__setattr__(self, "atoms", atoms)
        edges = tuple(self.edges)
        object.__setattr__(self, "edges", edges)
        for index, atom in enumerate(atoms):
            if atom.index != index:
                raise UnsupportedTopologyError(
                    f"atom order must be fixed with index==position, got {atom.index} at {index}"
                )
        count = len(atoms)
        seen: set[tuple[int, int, EdgeType]] = set()
        for edge in edges:
            if edge.a >= count or edge.b >= count:
                raise UnsupportedTopologyError(f"edge {edge} references missing atom")
            key = (edge.a, edge.b, edge.type)
            if key in seen:
                raise UnsupportedTopologyError(f"duplicate typed edge {key}")
            seen.add(key)
        if self.metal_center is not None and not 0 <= self.metal_center < count:
            raise UnsupportedTopologyError("metal_center out of range")
        for pair in self.reaction_pairs:
            if pair[0] == pair[1]:
                raise UnsupportedTopologyError("reaction self-pair is not supported")

    @property
    def natoms(self) -> int:
        """Return the atom count."""
        return len(self.atoms)

    @property
    def n_atoms(self) -> int:
        """Return the atom count (alias unifying the core spelling)."""
        return len(self.atoms)

    @property
    def elements(self) -> tuple[str, ...]:
        """Return element symbols in atom order."""
        return tuple(atom.element for atom in self.atoms)

    def edge_set(self) -> set[tuple[int, int, EdgeType]]:
        """Return the typed edge set with ordered endpoints."""
        return {(edge.a, edge.b, edge.type) for edge in self.edges}

    def neighbors(self, index: int, edge_type: EdgeType | None = None) -> tuple[int, ...]:
        """Return neighbor indices of *index*, optionally filtered by type."""
        out: list[int] = []
        for edge in self.edges:
            if edge_type is not None and edge.type is not edge_type:
                continue
            if edge.a == index:
                out.append(edge.b)
            elif edge.b == index:
                out.append(edge.a)
        return tuple(sorted(out))

    def covalent_adjacency(self) -> tuple[tuple[int, ...], ...]:
        """Return neighbour lists over COVALENT edges only, sorted per atom.

        Shared-graph contract: FORMING/COORDINATION/BREAKING edges never
        enter this adjacency; legacy torsion mechanics consume it directly.
        """
        rows: list[set[int]] = [set() for _ in range(len(self.atoms))]
        for edge in self.edges:
            if edge.type is not EdgeType.COVALENT:
                continue
            rows[edge.a].add(edge.b)
            rows[edge.b].add(edge.a)
        return tuple(tuple(sorted(row)) for row in rows)

    def edge_kind(self, first: int, second: int) -> EdgeType | None:
        """Return the kind of the edge joining two atoms, if any."""
        pair = (min(first, second), max(first, second))
        for edge in self.edges:
            if (edge.a, edge.b) == pair:
                return edge.type
        return None

    def pairs_of_kind(self, kind: Any) -> tuple[tuple[int, int], ...]:
        """Return canonical pairs carrying *kind*, in declaration order."""
        wanted = normalize_edge_kind(kind)
        return tuple((edge.a, edge.b) for edge in self.edges if edge.type is wanted)

    def coordination_donors(self) -> tuple[int, ...]:
        """Return COORDINATION neighbors of the metal center."""
        if self.metal_center is None:
            return ()
        return self.neighbors(self.metal_center, EdgeType.COORDINATION)

    def stereo_declarations(self) -> dict[int, str]:
        """Return atom indices carrying explicit stereo declarations."""
        return {atom.index: atom.stereo for atom in self.atoms if atom.stereo is not None}

    def to_mapping(self, *, convention: str = "internal0") -> dict[str, Any]:
        """Return a config-mapping serialization of this graph."""
        if convention not in INDEX_CONVENTIONS:
            raise UnsupportedTopologyError(f"unknown index convention {convention!r}")
        shift = 1 if convention == "fixture1" else 0
        return {
            "indexing": convention,
            "atoms": [atom.to_mapping(convention=convention) for atom in self.atoms],
            "bonds": [edge.to_mapping(convention=convention) for edge in self.edges],
            "metal_center": (self.metal_center + shift) if self.metal_center is not None else None,
            "reaction_pairs": [[a + shift, b + shift] for a, b in self.reaction_pairs],
            "source": self.source,
        }

    @classmethod
    def from_mapping(
        cls,
        mapping: Mapping[str, Any],
        elements: Sequence[str],
        *,
        convention: str = "fixture1",
        source: str = "workflow-spec",
    ) -> TypedGraph:
        """Build typed records from a workflow config mapping (no file paths).

        The workflow parser authority: ``mapping["bonds"]`` holds bare
        index pairs (covalent) or ``{"atoms": [...], "kind": ...,
        "bond_order": ..., "provenance": ...}`` records; ``mapping["atoms"]``
        optionally declares roles/stereo/labels by index; ``metal_center``
        and ``reaction_pairs`` follow *convention*.  Only ``internal0`` and
        ``fixture1`` conventions are accepted, declared explicitly.
        """

        def _checked_index(value: Any, *, path: str) -> int:
            if isinstance(value, bool) or not isinstance(value, int):
                raise UnsupportedTopologyError(f"{path} must be an integer, got {value!r}")
            return int(value) - shift

        if convention not in INDEX_CONVENTIONS:
            raise UnsupportedTopologyError(f"unknown index convention {convention!r}")
        if not isinstance(mapping, Mapping):
            raise UnsupportedTopologyError("topology mapping must be a mapping")
        shift = 1 if convention == "fixture1" else 0
        atoms = [AtomRef(index=i, element=str(element)) for i, element in enumerate(elements)]
        for entry in mapping.get("atoms", []) or []:
            if not isinstance(entry, Mapping) or "index" not in entry:
                raise UnsupportedTopologyError("atom entries must be mappings with 'index'")
            index = _checked_index(entry["index"], path="atom index")
            if not 0 <= index < len(atoms):
                raise UnsupportedTopologyError(f"atom index {entry['index']!r} out of range")
            current = atoms[index]
            atoms[index] = AtomRef(
                index=index,
                element=current.element,
                label=entry.get("label", current.label),
                role=str(entry.get("role", current.role or "")),
                stereo=entry.get("stereo", current.stereo),
            )
        raw_bonds = mapping.get("bonds", []) or []
        if isinstance(raw_bonds, (str, bytes)):
            raise UnsupportedTopologyError("topology bonds must be a list of entries")
        edges: list[TypedEdge] = []
        for position, item in enumerate(raw_bonds):
            if isinstance(item, Mapping):
                pair = item.get("atoms")
                if pair is None or len(list(pair)) != 2:
                    raise UnsupportedTopologyError(
                        f"bonds[{position}].atoms must hold exactly two indices"
                    )
                first = _checked_index(pair[0], path=f"bonds[{position}].atoms[0]")
                second = _checked_index(pair[1], path=f"bonds[{position}].atoms[1]")
                kind = normalize_edge_kind(item.get("kind", "COVALENT"))
                edges.append(
                    TypedEdge(
                        a=first,
                        b=second,
                        type=kind,
                        bond_order=item.get("bond_order"),
                        provenance=str(item.get("provenance", "explicit")),
                    )
                )
            else:
                pair = list(item)
                if len(pair) != 2:
                    raise UnsupportedTopologyError(
                        f"bonds[{position}] must hold exactly two indices"
                    )
                edges.append(
                    TypedEdge(
                        a=_checked_index(pair[0], path=f"bonds[{position}][0]"),
                        b=_checked_index(pair[1], path=f"bonds[{position}][1]"),
                        type=EdgeType.COVALENT,
                        provenance="explicit",
                    )
                )
        metal = mapping.get("metal_center")
        metal_center: int | None = None
        if metal is not None:
            metal_center = _checked_index(metal, path="metal_center")
        reaction_pairs = tuple(
            (
                _checked_index(pair[0], path="reaction_pairs"),
                _checked_index(pair[1], path="reaction_pairs"),
            )
            for pair in (mapping.get("reaction_pairs", []) or [])
        )
        return cls(
            atoms=tuple(atoms),
            edges=tuple(edges),
            metal_center=metal_center,
            reaction_pairs=reaction_pairs,
            source=str(mapping.get("source", source)),
        )

    def validate_witness(self, mapping: Sequence[int]) -> AutomorphismReport:
        """Validate an atom permutation as a typed-graph automorphism.

        Checks element preservation, typed-edge preservation, and involution.
        Stereo parity and geometric symmetry are *not* claimed here; they need
        separate stereo labels and :mod:`hgeom` witnesses.
        """
        perm = tuple(int(value) for value in mapping)
        complete = len(perm) == self.natoms and sorted(perm) == list(range(self.natoms))
        element_ok = bool(complete) and all(
            self.atoms[index].element == self.atoms[perm[index]].element
            for index in range(self.natoms)
        )
        edge_set = self.edge_set()
        mapped_edges = set()
        if complete:
            for a, b, kind in edge_set:
                pa, pb = perm[a], perm[b]
                mapped_edges.add((min(pa, pb), max(pa, pb), kind))
        edges_ok = bool(complete) and mapped_edges == edge_set
        involutive = bool(complete) and all(
            perm[perm[index]] == index for index in range(self.natoms)
        )
        return AutomorphismReport(
            mapping=perm,
            is_permutation=bool(complete),
            element_preserving=element_ok,
            typed_edge_preserving=edges_ok,
            involutive=involutive,
            stereo_status="UNVERIFIED",
            geometric_status="UNVERIFIED",
        )

    def induced_site_action(
        self, mapping: Sequence[int], site_donors: Sequence[int]
    ) -> dict[int, int] | None:
        """Return the induced donor-index action, or ``None`` if not closed."""
        perm = tuple(int(value) for value in mapping)
        donors = set(site_donors)
        action: dict[int, int] = {}
        for donor in site_donors:
            image = perm[donor]
            if image not in donors:
                return None
            action[donor] = image
        return action


@dataclass(frozen=True, slots=True)
class AutomorphismReport:
    """Witness validation outcome (topology level only)."""

    mapping: tuple[int, ...]
    is_permutation: bool
    element_preserving: bool
    typed_edge_preserving: bool
    involutive: bool
    stereo_status: str = "UNVERIFIED"
    geometric_status: str = "UNVERIFIED"

    @property
    def topology_valid(self) -> bool:
        """Return whether the topology-level witness checks pass."""
        return self.is_permutation and self.element_preserving and self.typed_edge_preserving

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-compatible summary."""
        return {
            "is_permutation": self.is_permutation,
            "element_preserving": self.element_preserving,
            "typed_edge_preserving": self.typed_edge_preserving,
            "involutive": self.involutive,
            "stereo_status": self.stereo_status,
            "geometric_status": self.geometric_status,
            "topology_valid": self.topology_valid,
        }


@dataclass(frozen=True, slots=True)
class CoordinationMap:
    """Isolated pure mapping extraction (no Refine edits, no geometry)."""

    metal_center: int
    site_donors: dict[str, int]
    coordination_edges: tuple[tuple[int, int], ...]
    reaction_pairs: tuple[tuple[int, int], ...]
    covalent_adjacency: dict[int, tuple[int, ...]]

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-compatible representation."""
        return {
            "metal_center": self.metal_center,
            "site_donors": dict(self.site_donors),
            "coordination_edges": [list(edge) for edge in self.coordination_edges],
            "reaction_pairs": [list(pair) for pair in self.reaction_pairs],
            "covalent_adjacency": {str(k): list(v) for k, v in self.covalent_adjacency.items()},
        }


def site_label_index(site_ids: Sequence[str], label: str) -> int:
    """Return the declared position of *label*, failing closed when absent."""
    try:
        return list(site_ids).index(label)
    except ValueError as exc:
        raise UnsupportedTopologyError(f"unknown binding site {label!r}") from exc


def load_typed_topology(path: str | Path) -> tuple[TypedGraph, CoordinationSpec, dict[str, Any]]:
    """Load a fixture typed-topology document.

    Returns the graph, a coordination spec built from its coordination block,
    and the raw document for provenance.  Only fixture/test code and the pure
    mapper call this; production contexts build :class:`TypedGraph` directly.
    """
    raw: dict[str, Any] = json.loads(Path(path).read_text())
    coord = raw.get("coordination", {})
    metal_1 = int(coord.get("metal_center_1based", 0)) - 1
    labels = [str(label) for label in coord.get("site_labels", [])]
    donors_1 = [int(value) for value in coord.get("binding_sites_1based", [])]
    roles: dict[int, str] = {metal_1: "metal_center"}
    for label, donor_1 in zip(labels, donors_1):
        roles[donor_1 - 1] = f"binding_site:{label}"
    for rel in raw.get("reaction_relations", []):
        for key in ("a_1based", "b_1based"):
            roles.setdefault(int(rel[key]) - 1, "forming_atom")
    atoms = tuple(
        AtomRef(
            index=int(entry["index_0based"]),
            element=str(entry["element"]),
            role=roles.get(int(entry["index_0based"]), ""),
        )
        for entry in raw["atoms"]
    )
    edges: list[TypedEdge] = []
    for entry in raw["edges"]:
        kind = normalize_edge_kind(entry["type"])
        edges.append(
            TypedEdge(a=int(entry["a_1based"]) - 1, b=int(entry["b_1based"]) - 1, type=kind)
        )
    coord = raw.get("coordination", {})
    metal_1 = int(coord.get("metal_center_1based", 0)) - 1
    labels = [str(label) for label in coord.get("site_labels", [])]
    donors_1 = [int(value) for value in coord.get("binding_sites_1based", [])]
    sites = tuple(
        BindingSite(id=label, kind="atom", atoms=(donor_1 - 1,), hapticity=1)
        for label, donor_1 in zip(labels, donors_1)
    )
    reaction_pairs = tuple(
        (int(rel["a_1based"]) - 1, int(rel["b_1based"]) - 1)
        for rel in raw.get("reaction_relations", [])
    )
    reference_shape = str(coord.get("reference_shape", "octahedral"))
    graph = TypedGraph(
        atoms=atoms,
        edges=tuple(edges),
        metal_center=metal_1,
        reaction_pairs=reaction_pairs,
        source=str(raw.get("source", "")),
    )
    spec = CoordinationSpec(
        metal_center=metal_1,
        binding_sites=sites,
        shapes=(reference_shape,),
    )
    return graph, spec, raw


def load_xyz_frame(
    path: str | Path, frame: int = 0
) -> tuple[tuple[str, ...], tuple[tuple[float, float, float], ...]]:
    """Load one XYZ frame, returning ``(elements, coordinates)`` tuples."""
    lines = Path(path).read_text().splitlines()
    index = 0
    current = 0
    while index < len(lines):
        if not lines[index].strip():
            index += 1
            continue
        count = int(lines[index].strip())
        block = lines[index + 2 : index + 2 + count]
        if len(block) != count:
            raise UnsupportedTopologyError(f"truncated XYZ frame in {path}")
        if current == frame:
            elements: list[str] = []
            coords: list[tuple[float, float, float]] = []
            for row in block:
                parts = row.split()
                elements.append(parts[0])
                coords.append((float(parts[1]), float(parts[2]), float(parts[3])))
            return tuple(elements), tuple(coords)
        current += 1
        index += count + 2
    raise UnsupportedTopologyError(f"frame {frame} missing in {path}")


def extract_coordination_map(graph: TypedGraph, spec: CoordinationSpec) -> CoordinationMap:
    """Extract a pure coordination map without touching Refine code paths.

    Isolated helper for the coordinator-approved Refine boundary: it exposes
    donor/metal identity and typed adjacency only.  Any Refine-path edit still
    requires coordinator approval; this mapper alone is safe to share.
    """
    if spec.metal_center != graph.metal_center and graph.metal_center is not None:
        raise UnsupportedTopologyError("spec metal center disagrees with typed graph")
    site_donors = {site.id: site.donor for site in spec.binding_sites}
    coordination_edges = tuple(
        (min(spec.metal_center, donor), max(spec.metal_center, donor))
        for donor in spec.donor_indices
    )
    adjacency: dict[int, tuple[int, ...]] = {
        index: graph.neighbors(index, EdgeType.COVALENT) for index in range(graph.natoms)
    }
    return CoordinationMap(
        metal_center=spec.metal_center,
        site_donors=site_donors,
        coordination_edges=coordination_edges,
        reaction_pairs=tuple(graph.reaction_pairs),
        covalent_adjacency=adjacency,
    )
