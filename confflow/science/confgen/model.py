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
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:  # Annotation only; runtime uses local import (avoid model->registry cycle).
    from confflow.science.confgen.kernel_records import RetryResult
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
    "RETRY_DEFAULT_PHASE",
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
    # A5 single-axis gate: ids of active components in registry order,
    # determined by the registry (not the solver). Defaults to () for
    # hand-constructed contexts; ``build_context`` always fills it explicitly
    # after path expansion. Coordination reads
    # ``active_components == (own id,)`` instead of rings/torsions presence.
    active_components: tuple[str, ...] = ()

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
        # A5: normalize active_components last (same compat rule: old
        # hand-constructed contexts without the field stay valid as ()).
        # Accepts tuple/list of str; None means ().
        active = self.active_components
        if active is None:
            object.__setattr__(self, "active_components", ())
        elif isinstance(active, (list, tuple)):
            object.__setattr__(self, "active_components", tuple(str(v) for v in active))
        else:
            raise ValueError("active_components must be a tuple of component ids")


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
    # AG1 generic: run each component expand hook in registry order
    # (typed graph exists, context not yet built). Only torsions sets
    # the hook (verbatim moved body); customs may add their own.
    for _descriptor in resolved_registry._ordered():
        _expand_hook = _descriptor.expand_context_spec
        if _expand_hook is not None:
            _expand_hook(resolved, structure, adjacency)
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
    # A5: active components in registry order (after path expansion, so
    # path-expanded torsions count). Determined by the registry, not the
    # solver; the coordination single-axis gate reads this tuple.
    active_ids = tuple(_d.id for _d in resolved_registry._ordered() if _d.is_active(resolved))
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
        active_components=active_ids,
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
class RetryFirstPass:
    """Immutable first-pass snapshot row for one target (D0 protocol).

    Built per parent from the input-only pass: the target's terminal
    status value and reason, whether its failure came from a stage
    exception (never science-retryable), and the accepted structure for
    successes (``None`` otherwise). The engine hands the whole per-parent
    table to :meth:`GenerationStage.retry_solve` so alternate starts
    derive from explicit data, never shared caches.
    """

    target_id: str
    ordinal: int
    status: str
    reason: str
    solver_error: bool
    structure: StructureRecord | None


#: Generic default retry phase id (D0.2 protocol).
#:
#: Stages that only override :meth:`GenerationStage.retry_solve` run one
#: phase with this id; the default :meth:`GenerationStage.retry_solve_phase`
#: delegates to :meth:`GenerationStage.retry_solve` for exactly this id.
#: Stage-owned multi-phase declarations use generic ids of their own
#: choosing (non-empty unique strings, declaration order is execution
#: order). The kernel never interprets ids beyond order/identity.
RETRY_DEFAULT_PHASE: str = "default"


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

    def report_statistics(self, snapshot: tuple[Any, ...]) -> Mapping[str, Any] | None:
        """Return this component's additive report fragment, if any (L-D3).

        Generic logic seam: the engine passes a read-only tuple of bound
        telemetry events for this component (possibly empty) and expects
        either ``None`` (default: no fragment, golden bytes unchanged) or
        a mapping fragment stored under
        ``scope["component_statistics"][component_id]``. The engine writes
        the new scope key only when at least one component returns a
        non-empty mapping; fragments never overwrite existing scope or
        terminal fields. The snapshot is read-only; stages must not
        write caches here.
        """
        return None

    def retry_solve(
        self,
        parent: StageParentProtocol,
        target: GenerationTarget,
        context: MolecularContext,
        should_cancel: CancelProbe | None,
        first_pass: tuple[RetryFirstPass, ...],
    ) -> RealizationResult | None:
        """Alternate-start second attempt for one failed target (optional).

        Two-pass hook (D0 protocol). The engine calls this only for targets
        whose first (input-only) attempt ended in a solve-failure terminal
        state, and only after re-running the policy/suppression gates, so an
        excluded or suppressed target is never solved here. ``first_pass``
        carries the immutable per-parent snapshot (every target's terminal
        status, reason, solver-error flag and, for successes, the accepted
        structure) so D1/D2 can derive alternate starts without any shared
        cache. Returning ``None`` (the default) declines the retry and the
        failure record stands. A returned outcome replaces the stale
        failure (with its deferred subtree revoked and counters
        reconciled) and travels the full post-solve path
        (drift/lock/perception/audit accounting and child expansion), never
        publishing directly. The hook receives the run's real cancellation
        probe and must let ``EngineCancelledError`` propagate.
        """
        return None

    def retry_phases(self) -> tuple[str, ...] | None:
        """Declare stage-owned retry phases in execution order (optional).

        D0.2 generic protocol. ``None`` (the default) means exactly one
        generic phase (:data:`RETRY_DEFAULT_PHASE`) delegating to
        :meth:`retry_solve`, preserving D0 single-stage bytes. A tuple
        declares two or more generic phase ids; ids must be non-empty
        unique strings and run in declaration order. The declaration is
        stage-owned data only; the kernel validates fail-closed, keeps no
        global or stage-instance run cache, and never interprets ids.
        """
        return None

    def retry_solve_phase(
        self,
        parent: StageParentProtocol,
        target: GenerationTarget,
        context: MolecularContext,
        should_cancel: CancelProbe | None,
        first_pass: tuple[RetryFirstPass, ...],
        phase_id: str,
        phase_snapshot: tuple[RetryFirstPass, ...],
    ) -> RealizationResult | RetryResult | None:
        """Phase-aware alternate-start attempt for one failed target (optional).

        D0.2 generic protocol. The engine calls this once per phase, in
        declaration order, only for targets whose *current* terminal (at
        that phase start) is a solve-failure terminal, and only after
        re-running the policy/suppression gates via the full post-solve
        path. ``first_pass`` is the permanent immutable input-only table;
        ``phase_snapshot`` is the frozen per-phase table built from current
        terminals at that phase start (prior-phase accepted structures
        included; targets within one phase all see the same snapshot).
        The default delegates the generic phase to :meth:`retry_solve`
        and declines any other id, so stages overriding only
        :meth:`retry_solve` keep D0 bytes exactly. L-D3 additionally
        allows returning the frozen ``RetryResult`` wrapper (outcome
        plus component-owned telemetry rows); plain outcomes and
        ``None`` keep the legacy semantics and the old ``retry_solve``
        direct API is unchanged.
        """
        if phase_id != RETRY_DEFAULT_PHASE:
            return None
        return self.retry_solve(parent, target, context, should_cancel, first_pass)

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
