#!/usr/bin/env python3

"""ConfGen v3 shared scientific model (CORE lane, frozen).

Frozen dataclasses for the Declare -> Enumerate -> Realize -> Perceive ->
Account pipeline. Dependencies: stdlib + ``confflow.domain`` records only.
No legacy runner imports, no orchestration, no energy optimization.

Frozen semantics (see ``docs/plans/confgen-v3-upgrade.md``):

- Labeled :class:`ConfgenStateKey` expresses indexed physical state.
  Canonical orbit identity (:class:`OrbitIdentity`) is separate, as are
  realization target ids and stable ordinals.
- State identity never includes sampling, tolerances, or config.
- Atom indices are internal zero-based; workflow inputs document and
  validate the 1-based convention at typed boundaries.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:  # Annotation only; runtime uses local import (avoid model->registry cycle).
    from confflow.science.confgen.registry import ComponentRegistry

from confflow.domain._immutable import FrozenDict
from confflow.domain.elements import canonical_element_symbol
from confflow.domain.structure import StructureRecord
from confflow.science.confgen.graph import (
    AtomRef,
    EdgeType,
    ScopedAtomRef,
    TypedEdge,
    TypedGraph,
)
from confflow.science.confgen.wire_v3_constants import V3_AXIS_ORDER as AXIS_ORDER

__all__ = [
    "AXIS_ORDER",
    "SCHEMA_VERSION",
    "AtomRef",
    "EdgeType",
    "GenerationStage",
    "GenerationTarget",
    "MolecularContext",
    "OrbitIdentity",
    "PerceptionResult",
    "RealizationResult",
    "StageEstimate",
    "ScopedAtomRef",
    "TerminalStatus",
    "TypedEdge",
    "TypedGraph",
    "WorkingRealization",
    "ConfgenStateKey",
    "build_context",
    "covalent_adjacency_of",
    "edge_kind_of",
]

#: ConfGen v3 schema version carried on every state key and spec.
SCHEMA_VERSION: int = 3

#: Typed-graph authority lives in lane B ``graph.py``; core uses those
#: classes directly (single authority, no competing definitions).
#: FORMING/COORDINATION/BREAKING edges are never ordinary covalent bonds.


def covalent_adjacency_of(graph: TypedGraph) -> tuple[tuple[int, ...], ...]:
    """Return covalent-only neighbour lists from the authority graph."""
    return tuple(graph.neighbors(index, EdgeType.COVALENT) for index in range(graph.natoms))


def edge_kind_of(graph: TypedGraph, first: int, second: int) -> EdgeType | None:
    """Return the typed edge kind joining two atoms, if any."""
    low, high = (first, second) if first <= second else (second, first)
    for edge in graph.edges:
        if edge.a == low and edge.b == high:
            return edge.type
    return None


class TerminalStatus(str, Enum):
    """The single terminal status of one realization target.

    Every enumerated target ends in exactly one of these. Drift and
    out-of-scope discoveries are evidence/events attached to records, never
    second terminal statuses.
    """

    EXPANDED = "expanded"
    PUBLISHED_LEAF = "published_leaf"
    FAILED_GEOMETRY = "failed_geometry"
    FAILED_NUMERICAL = "failed_numerical"
    FAILED_DRIFT = "failed_drift"
    FAILED_ATOM_ORDER = "failed_atom_order"
    UNSUPPORTED = "unsupported"
    UNRESOLVED = "unresolved"
    REJECTED_BY_POLICY = "rejected_by_policy"
    DEFERRED_SAMPLED_OUT = "deferred_sampled_out"
    DEFERRED_PARENT_FAILED = "deferred_parent_failed"
    SUPPRESSED_BY_VERIFIED_SYMMETRY = "suppressed_by_verified_symmetry"


def _freeze_json(value: Any, *, path: str = "$") -> Any:
    """Defensively copy *value* into plain JSON-compatible containers."""
    if value is None or isinstance(value, (bool, int, str)):
        if isinstance(value, float):
            raise ValueError(f"{path} must hold finite JSON values")
        return value
    if isinstance(value, float):
        import math

        if not math.isfinite(value):
            raise ValueError(f"{path} holds a non-finite float")
        return value
    if isinstance(value, Mapping):
        frozen: dict[str, Any] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise ValueError(f"{path} mapping keys must be strings")
            frozen[key] = _freeze_json(item, path=f"{path}.{key}")
        return frozen
    if isinstance(value, (list, tuple)):
        return [_freeze_json(item, path=f"{path}[{index}]") for index, item in enumerate(value)]
    raise ValueError(f"{path} holds a non-JSON-compatible value of type {type(value).__name__}")


@dataclass(frozen=True, slots=True)
class ConfgenStateKey:
    """Labeled physical-state identity for one realization node.

    Only state identity: sampling, tolerances, budgets, and config never
    appear here. Orbit identity is the separate :class:`OrbitIdentity`.
    """

    coordination: Any = None
    rings: Mapping[str, Any] = field(default_factory=FrozenDict)
    torsions: Mapping[str, Any] = field(default_factory=FrozenDict)
    schema_version: int = SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != SCHEMA_VERSION:
            raise ValueError(
                f"schema_version must be {SCHEMA_VERSION}, got {self.schema_version!r}"
            )
        object.__setattr__(
            self, "coordination", _freeze_json(self.coordination, path="$.coordination")
        )
        for name in ("rings", "torsions"):
            frozen = _freeze_json(dict(getattr(self, name)), path=f"$.{name}")
            object.__setattr__(self, name, FrozenDict(frozen))

    def to_dict(self) -> dict[str, Any]:
        """Return a canonical JSON-compatible representation."""
        return {
            "schema_version": int(self.schema_version),
            "coordination": _freeze_json(self.coordination, path="$.coordination"),
            "rings": {key: _freeze_json(val) for key, val in dict(self.rings).items()},
            "torsions": {key: _freeze_json(val) for key, val in dict(self.torsions).items()},
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> ConfgenStateKey:
        """Rebuild a key from :meth:`to_dict` output (fail closed)."""
        if not isinstance(payload, Mapping):
            raise ValueError("state key payload must be a mapping")
        version = payload.get("schema_version", SCHEMA_VERSION)
        if version != SCHEMA_VERSION:
            raise ValueError(f"state key schema_version must be {SCHEMA_VERSION}")
        return cls(
            coordination=_freeze_json(payload.get("coordination"), path="$.coordination"),
            rings=_freeze_json(dict(payload.get("rings", {})), path="$.rings"),
            torsions=_freeze_json(dict(payload.get("torsions", {})), path="$.torsions"),
            schema_version=SCHEMA_VERSION,
        )


@dataclass(frozen=True, slots=True)
class OrbitIdentity:
    """Canonical orbit identity, kept separate from the labeled state key."""

    axis: str
    orbit_key: str
    members: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.axis not in AXIS_ORDER:
            raise ValueError(f"orbit axis must be one of {AXIS_ORDER}, got {self.axis!r}")
        if not isinstance(self.orbit_key, str) or not self.orbit_key:
            raise ValueError("orbit_key must be a non-empty string")
        object.__setattr__(self, "members", tuple(self.members))


@dataclass(frozen=True, slots=True)
class MolecularContext:
    """Immutable scientific authority for one engine run.

    ``graph`` is the lane B typed-graph authority (explicit spec topology
    wins; otherwise legacy covalent perception plus declared add/del_bond
    corrections, all built as lane B records). ``adjacency`` is the
    covalent-only neighbour list retained separately for legacy torsion
    mechanics. ``input_coords`` is the reference geometry for relative
    grids. Atom order of ``structure`` is the guard authority.
    ``atom_refs`` is the authoritative scoped atom identity built once at
    the boundary (structure id, index, expected element, typed covalent
    radius, local-environment hash) and validated against the graph; it
    is never rebuilt downstream and never carried in the StateKey.
    """

    structure: StructureRecord
    adjacency: tuple[tuple[int, ...], ...]
    graph: TypedGraph
    resolved_spec: Mapping[str, Any]
    tolerances: Any
    input_state_key: ConfgenStateKey
    input_coords: tuple[tuple[float, float, float], ...]
    inherited_scope: Mapping[str, Any] = field(default_factory=FrozenDict)
    atom_refs: tuple[ScopedAtomRef, ...] = ()
    # A4a registry channel: holds the resolved ComponentRegistry instance for
    # this run. Defaults to None at construction for backward compat; the
    # post-init fills the shared immutable default (local import, no top-level
    # model->registry cycle). ``build_context`` always passes the resolved
    # instance explicitly.
    registry: ComponentRegistry | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.structure, StructureRecord):
            raise ValueError("structure must be a StructureRecord")
        adjacency = tuple(tuple(sorted(int(v) for v in row)) for row in self.adjacency)
        n_atoms = len(self.structure.atoms)
        if len(adjacency) != n_atoms:
            raise ValueError("adjacency must list one neighbour row per atom")
        for index, row in enumerate(adjacency):
            for other in row:
                if other < 0 or other >= n_atoms:
                    raise ValueError(f"adjacency[{index}] holds out-of-range atom {other}")
                if other == index:
                    raise ValueError(f"adjacency[{index}] holds a self loop")
        object.__setattr__(self, "adjacency", adjacency)
        if not isinstance(self.graph, TypedGraph):
            raise ValueError("graph must be the lane B TypedGraph authority")
        if self.graph.natoms != n_atoms:
            raise ValueError("graph atom count mismatches structure atom count")
        try:
            graph_elements = tuple(
                canonical_element_symbol(atom.element) for atom in self.graph.atoms
            )
        except Exception as exc:
            raise ValueError(f"graph elements invalid: {exc}") from exc
        if graph_elements != tuple(self.structure.atoms):
            raise ValueError("graph elements mismatch input structure atoms")
        if covalent_adjacency_of(self.graph) != adjacency:
            raise ValueError("adjacency must equal the graph covalent adjacency")
        if not isinstance(self.resolved_spec, FrozenDict):
            object.__setattr__(self, "resolved_spec", FrozenDict(dict(self.resolved_spec)))
        if not isinstance(self.input_state_key, ConfgenStateKey):
            raise ValueError("input_state_key must be a ConfgenStateKey")
        coords = tuple((float(x), float(y), float(z)) for x, y, z in self.input_coords)
        if len(coords) != n_atoms:
            raise ValueError("input_coords must hold one triple per atom")
        object.__setattr__(self, "input_coords", coords)
        if not isinstance(self.inherited_scope, FrozenDict):
            object.__setattr__(self, "inherited_scope", FrozenDict(dict(self.inherited_scope)))
        refs = tuple(self.atom_refs)
        for ref in refs:
            if not isinstance(ref, ScopedAtomRef):
                raise ValueError("atom_refs must hold ScopedAtomRef records")
        object.__setattr__(self, "atom_refs", refs)
        if refs:
            from confflow.science.confgen.graph import (
                build_scoped_refs,
                check_scoped_refs,
            )

            if len(refs) != self.graph.natoms:
                raise ValueError("atom_refs count disagrees with graph atom count")
            problems = check_scoped_refs(self.graph, refs, self.structure.id)
            if problems:
                raise ValueError(
                    "atom_refs fail the graph authority audit: " + "; ".join(problems[:5])
                )
            fresh = build_scoped_refs(self.graph, self.structure.id)
            if list(fresh) != list(refs):
                raise ValueError(
                    "atom_refs disagree with the graph authority "
                    "(stale radius/environment for this structure)"
                )
        # A4a: fill default registry last so pre-existing error/order checks
        # above are unchanged (V24 old hand-constructed contexts stay valid).
        if self.registry is None:
            from confflow.science.confgen.registry import default_registry

            object.__setattr__(self, "registry", default_registry())


def _expand_typed_paths(
    resolved: dict[str, Any],
    structure: StructureRecord,
    adjacency: Sequence[Sequence[int]],
) -> None:
    """Expand Phase 0 path declarations into torsion axes (deferred).

    Runs at context build time -- on the final working topology after
    perception plus add/del_bond corrections -- with the SAME pure resolver
    the legacy executor uses. Path-expanded rotors use the relative-rotation
    grid model with ``rotate_side`` derived from the explicitly chosen
    moving endpoint (``move=start`` behaves like the ``left`` side of the
    traversal-ordered bond, ``move=end`` like ``right``). Any canonical-bond
    collision with an explicitly declared torsion axis (any model) fails
    closed with ``ROTOR_SAMPLING_CONFLICT``; identical path duplicates
    deduplicate with merged provenance. The expansion record
    (``paths_resolved``) audits rotors, warnings, and the topology digest.
    """
    from confflow.domain.elements import atomic_number
    from confflow.science.bonds import covalent_radii
    from confflow.science.confgen.planner import resolve_torsion_axes
    from confflow.science.confgen.torsion.paths import (
        ROTOR_SAMPLING_CONFLICT,
        ParsedPath,
        PathResolutionError,
        canonical_grid_size,
        canonicalize_rotors,
        resolve_paths,
    )

    entries = resolved.get("paths") or []
    parsed = [
        ParsedPath(
            start=int(item["start"]),
            end=int(item["end"]),
            move=str(item["move"]),
            angles=tuple(float(a) for a in item["angles"]),
            source=str(item.get("source", f"$.paths[{i}]")),
            raw_start=int(item["start"]) + 1,
            raw_end=int(item["end"]) + 1,
        )
        for i, item in enumerate(entries)
    ]
    n_atoms = len(structure.atoms)
    try:
        numbers = [atomic_number(symbol) for symbol in structure.atoms]
        radii = covalent_radii(numbers)
    except ValueError as exc:
        raise ValueError(f"path short-bond assessment failed: {exc}") from exc
    strict = bool(resolved.get("strict_path_bond_check", False))
    try:
        resolution = resolve_paths(
            parsed,
            [list(row) for row in adjacency],
            n_atoms=n_atoms,
            coords=[tuple(p) for p in structure.coordinates],
            radii=list(radii),
            strict_bond_check=strict,
        )
        rotors = canonicalize_rotors(resolution.rotors)
    except PathResolutionError as exc:
        raise ValueError(str(exc)) from exc
    explicit: dict[tuple[int, int], str] = {}
    for position, entry in enumerate(resolved.get("torsions", []) or []):
        bond = entry.get("bond")
        atoms = entry.get("atoms")
        if bond is not None:
            key = (min(int(bond[0]), int(bond[1])), max(int(bond[0]), int(bond[1])))
        elif atoms is not None:
            key = (min(int(atoms[1]), int(atoms[2])), max(int(atoms[1]), int(atoms[2])))
        else:
            continue
        explicit[key] = str(entry.get("id", f"$.torsions[{position}]"))
    typed_torsions = list(resolved.get("torsions", []) or [])
    records: list[dict[str, Any]] = []
    for ordinal, rotor in enumerate(rotors):
        if rotor.bond in explicit:
            raise ValueError(
                f"{ROTOR_SAMPLING_CONFLICT}: path-expanded bond "
                f"{rotor.bond[0] + 1}-{rotor.bond[1] + 1} "
                f"({', '.join(rotor.sources)}) collides with explicitly "
                f"declared torsion {explicit[rotor.bond]!r}; declare one "
                "sampling per bond (grids are never unioned)"
            )
        first, second = rotor.ordered
        axis_id = f"path{ordinal + 1}:{rotor.bond[0] + 1}-{rotor.bond[1] + 1}"
        typed_torsions.append(
            {
                "id": axis_id,
                "bond": [int(first), int(second)],
                "model": "relative_rotation_grid",
                "angles": [float(a) for a in rotor.angles],
                "treatment": "enumerate",
                "rotate_side": "left" if rotor.moving_atom == first else "right",
            }
        )
        records.append(
            {
                "id": axis_id,
                "bond": [rotor.bond[0] + 1, rotor.bond[1] + 1],
                "ordered": [first + 1, second + 1],
                "moving_atom": rotor.moving_atom + 1,
                "moving": [a + 1 for a in rotor.moving],
                "fixed": [a + 1 for a in rotor.fixed],
                "angles": [float(a) for a in rotor.angles],
                "sources": list(rotor.sources),
                "index_base": 1,
            }
        )
    resolve_torsion_axes(tuple(typed_torsions), index_base=0)
    resolved["torsions"] = typed_torsions
    resolved["paths_resolved"] = {
        "driving_id": structure.id,
        "driving_geometry_digest": structure.geometry_digest,
        "atom_symbols": list(structure.atoms),
        "declared_paths": [
            {
                "source": item.source,
                "start": item.raw_start,
                "end": item.raw_end,
                "move": item.move,
                "route": [atom + 1 for atom in item.route],
                "angles": list(item.angles),
                "index_base": 1,
            }
            for item in resolution.declared_paths
        ],
        "rotors": records,
        "warnings": list(resolution.warnings),
        "topology_digest": resolution.topology_digest,
        "declared_cartesian_size": int(resolution.raw_cartesian_size),
        "raw_cartesian_size": int(canonical_grid_size(rotors)),
    }


def build_context(
    structure: StructureRecord,
    spec: Mapping[str, Any],
    input_state_key: ConfgenStateKey | None = None,
    *,
    inherited_scope: Mapping[str, Any] | None = None,
    registry: ComponentRegistry | None = None,
) -> MolecularContext:
    """Build the immutable scientific context for one input structure.

    The spec is normalized first (spec-facing atom indices use the 1-based
    workflow convention and are converted to internal 0-based here).
    Typed-graph authority: explicit ``spec["topology"]["bonds"]`` wins
    outright; otherwise legacy covalent perception at ``bond_scale``
    (default 1.15) plus ``add_bond``/``del_bond`` corrections. FORMING and
    COORDINATION edges are recorded in the typed graph and never enter the
    covalent adjacency used by torsion mechanics. Raises :class:`ValueError`
    fail-closed on malformed topology or indices.

    The optional ``inherited_scope`` carries resolved scope/reference
    descriptors for every entry of a non-empty ``input_state_key`` (see
    ``core-chaining-hook.md``): torsion defining refs (model, bond/atoms,
    frame, rotate_side, chemical states, label, and -- for relative
    grids -- the absolute ``reference_frame_value`` snapshotted on the
    prior run's input reference); ring atom lists; the coordination
    metal/donor refs. Scope/reference information never enters the
    :class:`ConfgenStateKey`; the engine audits carried axes from exactly
    these descriptors and fails closed (``INHERITED_STATE_SCOPE_MISSING``)
    when any entry lacks defining metadata.
    """
    from confflow.science.confgen.planner import build_typed_graph, normalize_spec
    from confflow.science.confgen.registry import resolve_registry
    from confflow.science.confgen.tolerances import resolve_tolerances

    if not isinstance(structure, StructureRecord):
        raise ValueError("structure must be a StructureRecord")
    if not isinstance(spec, Mapping):
        raise ValueError("spec must be a mapping")
    resolved_registry = resolve_registry(registry)
    resolved = normalize_spec(spec, registry=resolved_registry)
    tolerances = resolve_tolerances(resolved.get("tolerances", {}))
    adjacency, graph = build_typed_graph(
        structure, resolved.get("topology", {}), resolved, registry=resolved_registry
    )
    if resolved.get("paths"):
        _expand_typed_paths(resolved, structure, adjacency)
    key = input_state_key if input_state_key is not None else ConfgenStateKey()
    if not isinstance(key, ConfgenStateKey):
        raise ValueError("input_state_key must be a ConfgenStateKey")
    scope = dict(inherited_scope) if inherited_scope is not None else {}
    if not isinstance(scope, dict):
        raise ValueError("inherited_scope must be a mapping")
    from confflow.science.confgen.graph import build_scoped_refs, check_scoped_refs

    atom_refs = build_scoped_refs(graph, structure.id)
    ref_problems = check_scoped_refs(graph, atom_refs, structure.id)
    if ref_problems:
        raise ValueError(
            "scoped atom references fail the graph authority audit: " + "; ".join(ref_problems[:5])
        )
    # A4b: component context validation (only checks that already ran at
    # context stage may live here; builtins are no-ops at A4b, so order and
    # bytes are unchanged). Hooks run in registry order after the typed
    # graph exists; no normalize-stage check is moved later here.
    provisional = MolecularContext(
        structure=structure,
        adjacency=tuple(tuple(row) for row in adjacency),
        graph=graph,
        resolved_spec=FrozenDict(resolved),
        tolerances=tolerances,
        input_state_key=key,
        input_coords=tuple(tuple(point) for point in structure.coordinates),
        inherited_scope=FrozenDict(scope),
        atom_refs=atom_refs,
        registry=resolved_registry,
    )
    for _descriptor in resolved_registry._ordered():
        _validate = _descriptor.validate_context
        if _validate is not None:
            _validate(resolved, provisional)
    return provisional


@dataclass(frozen=True, slots=True)
class WorkingRealization:
    """One realized node in the conditional generation tree."""

    structure: StructureRecord
    state_key: ConfgenStateKey
    parent_realization_id: str | None = None
    generation_axis: str | None = None
    locked_axes: tuple[str, ...] = ()
    provenance: Mapping[str, Any] = field(default_factory=FrozenDict)

    def __post_init__(self) -> None:
        if not isinstance(self.structure, StructureRecord):
            raise ValueError("structure must be a StructureRecord")
        if not isinstance(self.state_key, ConfgenStateKey):
            raise ValueError("state_key must be a ConfgenStateKey")
        if self.generation_axis is not None and self.generation_axis not in AXIS_ORDER:
            raise ValueError(f"generation_axis must be one of {AXIS_ORDER}")
        object.__setattr__(self, "locked_axes", tuple(self.locked_axes))
        for axis in self.locked_axes:
            if axis not in AXIS_ORDER:
                raise ValueError(f"locked axis {axis!r} is not a known generation axis")
        if not isinstance(self.provenance, FrozenDict):
            object.__setattr__(self, "provenance", FrozenDict(dict(self.provenance)))


@dataclass(frozen=True, slots=True)
class GenerationTarget:
    """One symbolic enumeration target of a stage (no geometry yet)."""

    axis: str
    target_id: str
    state_value: Mapping[str, Any]
    ordinal: int
    provenance: Mapping[str, Any] = field(default_factory=FrozenDict)

    def __post_init__(self) -> None:
        if self.axis not in AXIS_ORDER:
            raise ValueError(f"target axis must be one of {AXIS_ORDER}, got {self.axis!r}")
        if not isinstance(self.target_id, str) or not self.target_id:
            raise ValueError("target_id must be a non-empty string")
        if isinstance(self.ordinal, bool) or not isinstance(self.ordinal, int) or self.ordinal < 0:
            raise ValueError("ordinal must be an integer >= 0")
        frozen = _freeze_json(dict(self.state_value), path="$.state_value")
        object.__setattr__(self, "state_value", FrozenDict(frozen))
        if not isinstance(self.provenance, FrozenDict):
            object.__setattr__(self, "provenance", FrozenDict(dict(self.provenance)))


@dataclass(frozen=True, slots=True)
class StageEstimate:
    """Symbolic count estimate for one stage under one parent."""

    declared_count: int
    upper_bound: int
    exact: bool
    details: Mapping[str, Any] = field(default_factory=FrozenDict)

    def __post_init__(self) -> None:
        for name in ("declared_count", "upper_bound"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError(f"{name} must be an integer >= 0")
        if self.upper_bound < self.declared_count:
            raise ValueError("upper_bound must cover declared_count")
        if not isinstance(self.exact, bool):
            raise ValueError("exact must be a bool")
        details = dict(self.details)
        if "basis" not in details or not isinstance(details["basis"], str):
            raise ValueError("details must carry a 'basis' string")
        if details.get("scope_coverage") not in ("exact", "upper_bound"):
            raise ValueError("details must carry scope_coverage 'exact' or 'upper_bound'")
        if not isinstance(self.details, FrozenDict):
            object.__setattr__(self, "details", FrozenDict(details))


@dataclass(frozen=True, slots=True)
class RealizationResult:
    """Outcome of realizing one target (geometry attempt finished)."""

    structure: StructureRecord | None
    status: str
    reason: str
    backend: str
    evidence: tuple[Mapping[str, Any], ...] = ()

    def __post_init__(self) -> None:
        if self.status not in (
            "realized",
            "geometry_failure",
            "numerical_failure",
            "unsupported",
        ):
            raise ValueError(f"unknown realization status {self.status!r}")
        if not isinstance(self.reason, str) or not self.reason:
            raise ValueError("reason must be a non-empty string")
        if not isinstance(self.backend, str) or not self.backend:
            raise ValueError("backend must be a non-empty string")
        if self.status == "realized" and self.structure is None:
            raise ValueError("realized results must carry a structure")
        if self.status != "realized" and self.structure is not None:
            raise ValueError("failed results must not carry a structure")
        object.__setattr__(
            self, "evidence", tuple(FrozenDict(dict(item)) for item in self.evidence)
        )


@dataclass(frozen=True, slots=True)
class PerceptionResult:
    """Stage-local perception of one realized structure."""

    best_key: Mapping[str, Any]
    alternatives: tuple[Mapping[str, Any], ...] = ()
    confidence: str = "reported"
    margins: Mapping[str, Any] = field(default_factory=FrozenDict)
    boundary_flags: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.confidence not in ("verified", "reported", "ambiguous"):
            raise ValueError(f"unknown perception confidence {self.confidence!r}")
        object.__setattr__(
            self, "best_key", FrozenDict(_freeze_json(dict(self.best_key), path="$.best_key"))
        )
        object.__setattr__(
            self,
            "alternatives",
            tuple(FrozenDict(_freeze_json(dict(item))) for item in self.alternatives),
        )
        if not isinstance(self.margins, FrozenDict):
            frozen = _freeze_json(dict(self.margins), path="$.margins")
            object.__setattr__(self, "margins", FrozenDict(frozen))
        object.__setattr__(self, "boundary_flags", tuple(self.boundary_flags))


class StageParentProtocol(Protocol):
    """Structural parent view for stages (only ``structure``/``provenance``)."""

    @property
    def structure(self) -> StructureRecord: ...

    @property
    def provenance(self) -> Mapping[str, Any]: ...


class GenerationStage(ABC):
    """Interface every generation stage (C/R/T) implements.

    Constructors accept the resolved spec mapping and read their own axis
    section from it. Enumeration is symbolic: a lazy iterator in stable
    order with no geometry generation (never a materialized grid).
    Realization reports numerical/geometric failure explicitly and never
    silently labels drift a success. Perception returns complete axis maps
    keyed by axis id so the engine can audit ancestor states by indexed
    identity; commanded keys are never accepted without geometry
    measurement.
    """

    def __init__(self, axis_spec: Mapping[str, Any]) -> None:
        raise NotImplementedError

    @property
    @abstractmethod
    def axis(self) -> str:
        """Return the stage axis (one of AXIS_ORDER)."""
        raise NotImplementedError

    def is_conditional(self, context: MolecularContext) -> bool:
        """Return True when enumeration depends on the parent state.

        Unconditional stages enumerate identically under every parent, so
        symbolic products are exact. Conditional stages must report
        ``scope_coverage="upper_bound"`` unless they can symbolically
        enumerate the conditional tree exactly.
        """
        return False

    def axis_ids(self, context: MolecularContext) -> tuple[str, ...] | None:
        """Return the stage's axis ids for perception coverage checks.

        ``None`` means unknown (engine skips the coverage check and records
        the gap). Torsion returns its enumerate-axis ids.
        """
        return None

    #: Lock-reference contract for ancestor audits (engine-read, never set
    #: per call). ``"input"`` (default) verifies carried locks against the
    #: run input reference geometry; ``"parent"`` verifies against the
    #: accepted parent realization geometry. Parent-relative stages
    #: (coordination binding integrity, ring template locks) must opt
    #: into ``"parent"``: the accepted parent realization itself may
    #: deviate from the run input within solver tolerances, and an
    #: unchanged accepted geometry must pass the child-stage lock
    #: ("preserve parent state, not original input geometry").
    #: Input-cumulative stages (relative torsion grids whose labels are
    #: defined from the run input) must keep ``"input"``.
    lock_reference: str = "input"

    #: Rigid bond-length integrity audit on fresh realizations (engine-read,
    #: never set per call). Only torsion does rigid rotation, so only the
    #: torsion stage opts into ``True``. The engine reads this attribute
    #: (or the registry descriptor fallback for legacy stages without it)
    #: and never compares axis strings itself. (FIX-1A A3.)
    check_bond_integrity: bool = False

    #: Whether this axis carries verified inherited locks into child keys
    #: (engine-read). Only torsion carries inherited torsion locks; other
    #: axes return ``False``. Legacy stages without the attribute resolve
    #: via the registry descriptor; the kernel never guesses by id.
    #: (FIX-1A A3.)
    carries_inherited_locks: bool = False

    def preserved_entries(self, resolved: Mapping[str, Any]) -> list[dict[str, Any]]:
        """Return this stage's ``preserve_input`` entries (FIX-1A A3).

        The engine concatenates every bound stage's entries generically
        (reverse execution order preserves the legacy torsions-then-rings
        byte order); each component owns its axis string and entry shape.
        Default is no entries.
        """
        return []

    def report_section(self, resolved: Mapping[str, Any]) -> dict[str, Any] | None:
        """Return this stage's report fragment, if any (FIX-1A A3).

        Only coordination contributes ``donor_configuration``; other
        stages return ``None``. The engine merges fragments generically;
        the wire adapter only converts key payloads (no new imports).
        """
        return None

    def describe_scope(self, resolved: Mapping[str, Any]) -> Mapping[str, Any] | None:
        """Return this stage's inherited-scope descriptor slice (FIX-1A A3).

        Per-component scope helpers live in each component's ``scope.py``
        and are owned there; the engine calls this hook generically when
        present. Default ``None`` (no slice); A4d continues the opaque
        inherited-state work without changing behavior here.
        """
        return None

    def fallback_lock(
        self,
        locked_state: Mapping[str, Any],
        structure: Any,
        context: MolecularContext,
    ) -> tuple[str, list[dict[str, Any]], dict[str, Any], Any] | None:
        """Verify one ancestor lock without a ``verify_locked`` hook.

        Component-owned fallback (FIX-1A A3). The ring component returns
        the tolerance-aware matcher verdict verbatim; other components
        return ``None`` so the engine uses the generic exact discrete
        comparison. Legacy explicit stages without this hook resolve via
        their registry descriptor; unknown axes keep the existing
        fail-closed errors. The kernel never inspects source or guesses
        by component id.
        """
        return None

    @abstractmethod
    def estimate(self, parent: StageParentProtocol, context: MolecularContext) -> StageEstimate:
        """Return the symbolic count estimate under *parent*."""
        raise NotImplementedError

    @abstractmethod
    def enumerate_targets(
        self, parent: StageParentProtocol, context: MolecularContext
    ) -> Iterable[GenerationTarget]:
        """Enumerate symbolic targets lazily in stable order (no geometry)."""
        raise NotImplementedError

    @abstractmethod
    def realize(
        self,
        parent: StageParentProtocol,
        target: GenerationTarget,
        context: MolecularContext,
    ) -> RealizationResult:
        """Realize one target starting from the parent geometry."""
        raise NotImplementedError

    @abstractmethod
    def perceive(self, structure: StructureRecord, context: MolecularContext) -> PerceptionResult:
        """Perceive the complete stage-local state of a realized structure."""
        raise NotImplementedError


#: Cancellation probe accepted by engine runs.
CancelProbe = Callable[[], bool]
