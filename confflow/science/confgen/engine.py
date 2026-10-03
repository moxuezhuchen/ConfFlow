#!/usr/bin/env python3

"""ConfGen v3 conditional generation engine (CORE lane).

Deterministic conditional DFS in AXIS_ORDER (Coordination -> Ring ->
Torsion) over symbolic targets, with strict upstream lock re-perception
after each realization, leaf-only publication, fatal atom-order guard,
drift evidence routing with observed/out-of-scope keys, and
single-terminal-status accounting. Sampling applies to the symbolic leaf
index space BEFORE geometry (streaming, O(cap) memory); failed parents
account descendants as compressed deferred ranges without expansion.

Hard lock rule (user requirement): every downstream stage preserves ALL
upstream C/R/T states. After each realization the engine re-perceives every
locked ancestor axis (stage `verify_locked` hook when present, else
perceive plus exact discrete compare, else the ring tolerance-aware
matcher) and compares indexed discrete identities with measured
deviations/margins. ANY locked-axis drift marks the target DRIFTED -- never
a publication with a stale parent StateKey.

Terminal-status equations (see also ``core-api-ready.md``):
- realize() failure -> mapped FAILED_*/UNSUPPORTED record, reason kept.
- Declared exclusion match -> REJECTED_BY_POLICY (never an auto proof).
- Atom-order violation -> CONFGEN_ATOM_ORDER_VIOLATION FATAL: the run
  aborts with no EngineRun and no partial output (never ordinary
  partial-success). The TerminalStatus member is reserved, never emitted.
- Parent-lock integrity/nonfinite failure -> FAILED_NUMERICAL record.
- Perception ambiguous/incomplete -> UNRESOLVED + AMBIGUOUS_KEY anomaly.
- Unexpected stage errors (realize/perceive/audit raising) are accounted
  explicitly (FAILED_NUMERICAL `stage_error` / UNRESOLVED + anomaly), never
  silent and never a published state.
- Stage audit hook (`audit_target`, when provided) mismatch -> FAILED_DRIFT
  + observed/out-of-scope evidence. Drift into a proof-excluded state
  invalidates the certificate (proof contradiction), never silently.
- Audits pass + more levels -> EXPANDED internal node (never published);
  last level -> PUBLISHED_LEAF (leaf-only outputs).
"""

from __future__ import annotations

from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

import numpy as np

from confflow.domain._immutable import FrozenDict
from confflow.science.confgen.accounting import (
    TargetRecord,
    build_certificate,
    certificate_category_counts,
    deferred_range_record,
    enumeration_digest,
    verify_count_equations,
    verify_terminal_equations,
)
from confflow.science.confgen.model import (
    AXIS_ORDER,
    ConfgenStateKey,
    GenerationStage,
    GenerationTarget,
    MolecularContext,
    TerminalStatus,
    WorkingRealization,
)
from confflow.science.confgen.perception import audit_parent_locks, check_atom_order
from confflow.science.confgen.planner import (
    MixedRadixGrid,
    check_limits,
    deferred_ranges,
    preflight,
    sample_indices,
    sampling_of,
)
from confflow.science.topology import inherit_topology_kwargs

__all__ = [
    "AtomOrderViolationError",
    "EngineCancelledError",
    "EngineRun",
    "ConfgenEngine",
    "InheritedScopeError",
    "InheritedTorsionLock",
    "UnsupportedAxisError",
    "combine_state_key",
    "inherited_torsion_locks",
    "thaw_snapshot",
]


class UnsupportedAxisError(ValueError):
    """Fail-closed error for explicitly unsupported requested generation."""


class AtomOrderViolationError(ValueError):
    """Fatal atom identity/order violation (CONFGEN_ATOM_ORDER_VIOLATION).

    Raised immediately: the run aborts with no EngineRun and no partial
    output. Atom order is never an ordinary per-target failure.
    """


class EngineCancelledError(RuntimeError):
    """Raised when a run's cancellation probe fires (no partial run returned)."""


class EngineConsistencyError(RuntimeError):
    """Raised when geometry DFS diverges from symbolic enumeration."""


class InheritedScopeError(ValueError):
    """Fail-closed error for unauditable chained (inherited) state."""


@dataclass(frozen=True, slots=True)
class InheritedTorsionLock:
    """One carried torsion axis audited against its prior absolute frame.

    Relative-grid labels are reference-dependent: the lock stores the
    absolute frame value snapshotted on the prior run's input reference
    plus the carried label, so ``expected_absolute`` is the physical
    dihedral the driving geometry must still embody. A new-reference
    delta of 0 never masquerades as the old label (e.g. old 120).
    Absolute/chemical labels are absolute setpoints directly.
    """

    axis_id: str
    frame: tuple[int, int, int, int]
    expected_absolute: float
    label: Any
    model: str


def _key_nonempty(key: ConfgenStateKey) -> bool:
    """Return True when the incoming state key carries any C/R/T entries."""
    if key.coordination is not None:
        return True
    return bool(key.rings) or bool(key.torsions)


def _resolve_inherited_frame(
    descriptor: Mapping[str, Any],
    entry: Mapping[str, Any],
    adjacency: Sequence[Sequence[int]],
) -> tuple[int, int, int, int]:
    """Resolve the absolute 4-atom frame for one carried torsion axis.

    The descriptor's snapshotted frame wins when it is four distinct
    in-range ints (the reference value was measured with it); otherwise
    the core frame rule (minimum-index neighbors, mirroring
    ``TorsionStage._frame_for``) derives it from the declared bond.
    Anything else fails closed.
    """
    frame = descriptor.get("frame")
    n_atoms = len(adjacency)
    if frame is not None:
        atoms = list(frame)
        if (
            len(atoms) == 4
            and all(isinstance(a, bool) is False and isinstance(a, int) for a in atoms)
            and len(set(int(a) for a in atoms)) == 4
            and all(0 <= int(a) < n_atoms for a in atoms)
        ):
            return (int(atoms[0]), int(atoms[1]), int(atoms[2]), int(atoms[3]))
        raise InheritedScopeError(
            "INHERITED_STATE_SCOPE_MISSING: inherited torsion frame "
            f"{frame!r} is not four distinct in-range atom indices"
        )
    bond = entry.get("bond")
    if bond is None:
        raise InheritedScopeError(
            "INHERITED_STATE_SCOPE_MISSING: carried torsion needs an explicit "
            "four-atom frame descriptor (chemical/absolute) or a bond for "
            "frame resolution"
        )
    first, second = int(bond[0]), int(bond[1])
    near = [n for n in adjacency[first] if n != second]
    far = [n for n in adjacency[second] if n != first]
    if not near or not far:
        raise InheritedScopeError(
            "INHERITED_STATE_SCOPE_MISSING: carried torsion bond has no "
            "measurable dihedral frame (terminal pair)"
        )
    return (min(near), first, second, min(far))


def inherited_torsion_locks(context: MolecularContext) -> list[InheritedTorsionLock]:
    """Build audited locks for carried (non-re-enumerated) torsion axes.

    Every torsion entry of a non-empty ``input_state_key`` must either be
    re-declared downstream as ``enumerate`` (fresh realization overwrites
    the stale label) or carried with a complete scope descriptor plus a
    matching downstream ``preserve_input`` declaration (model, bond/atoms,
    rotate_side identical). Missing descriptors, dropped axes, redefined
    refs, and reference-dependent relative labels without a snapshotted
    absolute ``reference_frame_value`` fail closed with
    ``INHERITED_STATE_SCOPE_MISSING``. Rings/coordination entries that no
    active downstream stage re-enumerates fail closed explicitly (lane
    audit hooks required; core never silently carries lane-owned state).
    Scope/reference information never enters the StateKey.
    """
    from confflow.science.confgen.torsion.measure import wrap_degrees

    key = context.input_state_key
    if not _key_nonempty(key):
        return []
    scope = context.inherited_scope
    if not isinstance(scope, Mapping) or not scope:
        raise InheritedScopeError(
            "INHERITED_STATE_SCOPE_MISSING: non-empty incoming confgen_state "
            "carries no inherited scope descriptors; chained state cannot "
            "be audited"
        )
    resolved = context.resolved_spec
    downstream_torsions = {
        str(entry.get("id")): entry
        for entry in (resolved.get("torsions", []) or [])
        if isinstance(entry, Mapping)
    }
    torsion_scope = scope.get("torsions", {})
    if not isinstance(torsion_scope, Mapping):
        raise InheritedScopeError(
            "INHERITED_STATE_SCOPE_MISSING: inherited scope has no torsion section"
        )
    locks: list[InheritedTorsionLock] = []
    for axis_id, label in dict(key.torsions).items():
        axis = str(axis_id)
        wounded = f"torsions.{axis}"
        descriptor = torsion_scope.get(axis)
        if not isinstance(descriptor, Mapping):
            raise InheritedScopeError(
                f"INHERITED_STATE_SCOPE_MISSING: no scope descriptor for {wounded}"
            )
        entry = downstream_torsions.get(axis)
        if entry is None:
            raise InheritedScopeError(
                f"INHERITED_STATE_SCOPE_MISSING: downstream spec drops inherited "
                f"{wounded}; declare it (enumerate or preserve_input) or refuse "
                "the chain"
            )
        if str(entry.get("treatment", "enumerate")) == "enumerate":
            continue  # freshly realized and audited; stale label overwritten
        if str(entry.get("model")) != str(descriptor.get("model")):
            raise InheritedScopeError(
                f"INHERITED_STATE_SCOPE_MISSING: downstream {wounded} redefines "
                f"model {entry.get('model')!r} over inherited "
                f"{descriptor.get('model')!r}"
            )
        for ref in ("bond", "atoms"):
            expected = descriptor.get(ref)
            observed = entry.get(ref)
            if expected is None and observed is None:
                continue
            if (
                expected is None
                or observed is None
                or [int(a) for a in observed] != [int(a) for a in expected]
            ):
                raise InheritedScopeError(
                    f"INHERITED_STATE_SCOPE_MISSING: downstream {wounded} {ref} "
                    f"{observed!r} differs from the inherited {expected!r}"
                )
        if str(entry.get("rotate_side", "left")) != str(descriptor.get("rotate_side", "left")):
            raise InheritedScopeError(
                f"INHERITED_STATE_SCOPE_MISSING: downstream {wounded} rotate_side "
                "differs from the inherited descriptor"
            )
        model = str(descriptor.get("model"))
        frame = _resolve_inherited_frame(descriptor, entry, context.adjacency)
        if model == "chemical":
            states = descriptor.get("states") or {}
            if not isinstance(states, Mapping) or label not in states:
                raise InheritedScopeError(
                    f"INHERITED_STATE_SCOPE_MISSING: inherited {wounded} label "
                    f"{label!r} is not a declared chemical state"
                )
            if entry.get("atoms") is not None:
                # Four-atom chemical frame: absolute setpoint.
                expected_absolute = float(states[label])
            else:
                # Bond-only chemical applies relatively: lock against the
                # prior absolute frame like a relative grid.
                reference = descriptor.get("reference_frame_value")
                if (
                    reference is None
                    or isinstance(reference, bool)
                    or not isinstance(reference, (int, float))
                ):
                    raise InheritedScopeError(
                        f"INHERITED_STATE_SCOPE_MISSING: inherited {wounded} "
                        "is reference-dependent (bond-only chemical) with no "
                        "snapshotted absolute reference_frame_value; "
                        "re-enumerate it"
                    )
                import math as _math_chem

                if not _math_chem.isfinite(float(reference)):
                    raise InheritedScopeError(
                        f"INHERITED_STATE_SCOPE_MISSING: inherited {wounded} "
                        "reference_frame_value is not finite"
                    )
                expected_absolute = float(wrap_degrees(float(reference) + float(states[label])))
        elif model == "absolute_dihedral_grid":
            if isinstance(label, bool) or not isinstance(label, (int, float)):
                raise InheritedScopeError(
                    f"INHERITED_STATE_SCOPE_MISSING: inherited {wounded} label "
                    f"{label!r} is not a measured angle"
                )
            expected_absolute = float(label)
        elif model == "relative_rotation_grid":
            reference = descriptor.get("reference_frame_value")
            if (
                reference is None
                or isinstance(reference, bool)
                or not isinstance(reference, (int, float))
            ):
                raise InheritedScopeError(
                    f"INHERITED_STATE_SCOPE_MISSING: inherited {wounded} is "
                    "reference-dependent (relative grid) with no snapshotted "
                    "absolute reference_frame_value; re-enumerate it"
                )
            if isinstance(label, bool) or not isinstance(label, (int, float)):
                raise InheritedScopeError(
                    f"INHERITED_STATE_SCOPE_MISSING: inherited {wounded} label "
                    f"{label!r} is not a rotation angle"
                )
            import math as _math

            if not _math.isfinite(float(reference)):
                raise InheritedScopeError(
                    f"INHERITED_STATE_SCOPE_MISSING: inherited {wounded} "
                    "reference_frame_value is not finite"
                )
            expected_absolute = float(wrap_degrees(float(reference) + float(label)))
        else:
            raise InheritedScopeError(
                f"INHERITED_STATE_SCOPE_MISSING: inherited {wounded} names unknown model {model!r}"
            )
        locks.append(
            InheritedTorsionLock(
                axis_id=axis,
                frame=frame,
                expected_absolute=float(expected_absolute),
                label=label,
                model=model,
            )
        )
    ring_scope = scope.get("rings", {})
    if not isinstance(ring_scope, Mapping):
        raise InheritedScopeError(
            "INHERITED_STATE_SCOPE_MISSING: inherited scope has no ring section"
        )
    downstream_rings = {
        str(entry.get("id"))
        for entry in (resolved.get("rings", []) or [])
        if isinstance(entry, Mapping)
    }
    for ring_id in dict(key.rings):
        if str(ring_id) in downstream_rings:
            continue  # active ring stage freshly realizes and audits it
        if not isinstance(ring_scope.get(str(ring_id)), Mapping):
            raise InheritedScopeError(
                f"INHERITED_STATE_SCOPE_MISSING: no scope descriptor for rings.{ring_id}"
            )
        raise InheritedScopeError(
            f"INHERITED_STATE_SCOPE_MISSING: inherited rings.{ring_id} is "
            "lane-owned state with no active downstream ring stage; "
            "re-enumerate it (carried lane audit hooks pending)"
        )
    if key.coordination is not None:
        coordination = resolved.get("coordination")
        active = (
            isinstance(coordination, Mapping)
            and coordination.get("treatment", "enumerate") != "preserve_input"
        )
        if not active:
            detail = scope.get("coordination")
            if not isinstance(detail, Mapping):
                raise InheritedScopeError(
                    "INHERITED_STATE_SCOPE_MISSING: no scope descriptor for coordination"
                )
            raise InheritedScopeError(
                "INHERITED_STATE_SCOPE_MISSING: inherited coordination is "
                "lane-owned state with no active downstream coordination "
                "stage; re-enumerate it (carried lane audit hooks pending)"
            )
    return locks


def check_inherited_torsion_locks(
    coords: Any, locks: Sequence[InheritedTorsionLock], tolerance_deg: float
) -> tuple[bool, list[dict[str, Any]]]:
    """Verify carried torsion locks against one geometry (absolute frames).

    Returns (ok, evidence). Any deviation outside tolerance is lock drift;
    the caller routes it to FAILED_DRIFT (never a publication with a stale
    parent key) or fails the chain closed when no target context exists.
    """
    import numpy as np

    from confflow.science.confgen.torsion.measure import measure_dihedral, wrap_degrees

    evidence: list[dict[str, Any]] = []
    ok = True
    for lock in locks:
        try:
            measured = float(measure_dihedral(np.asarray(coords, dtype=float), *lock.frame))
        except ValueError as exc:
            ok = False
            evidence.append(
                {
                    "kind": "drift",
                    "axis": f"torsions.{lock.axis_id}",
                    "detail": "inherited torsion frame unmeasurable",
                    "expected": float(lock.expected_absolute),
                    "measured": None,
                    "out_of_scope": True,
                    "inherited": True,
                    "frame": list(lock.frame),
                    "diagnostic": str(exc)[:200],
                }
            )
            continue
        deviation = abs(wrap_degrees(measured - lock.expected_absolute))
        if deviation <= float(tolerance_deg):
            continue
        ok = False
        evidence.append(
            {
                "kind": "drift",
                "axis": f"torsions.{lock.axis_id}",
                "detail": "inherited torsion lock drifted",
                "expected": float(lock.expected_absolute),
                "measured": float(measured),
                "deviation_deg": float(deviation),
                "tolerance_deg": float(tolerance_deg),
                "out_of_scope": "unknown",
                "inherited": True,
                "frame": list(lock.frame),
            }
        )
    return ok, evidence


def thaw_snapshot(resolved: Mapping[str, Any]) -> dict[str, Any]:
    """Return a plain-data snapshot of the resolved spec for constructors."""
    from confflow.domain._immutable import FrozenDict, thaw_value

    if isinstance(resolved, FrozenDict):
        return resolved.thaw()
    snapshot: dict[str, Any] = thaw_value(dict(resolved))
    return snapshot


def combine_state_key(
    parent_key: ConfgenStateKey, axis: str, state_value: Mapping[str, Any]
) -> ConfgenStateKey:
    """Merge a stage-local state value into the complete labeled key."""
    if axis == "coordination":
        return ConfgenStateKey(
            coordination=dict(state_value),
            rings=dict(parent_key.rings),
            torsions=dict(parent_key.torsions),
        )
    if axis == "rings":
        merged = dict(parent_key.rings)
        merged.update(dict(state_value))
        return ConfgenStateKey(
            coordination=parent_key.coordination,
            rings=merged,
            torsions=dict(parent_key.torsions),
        )
    if axis == "torsions":
        merged = dict(parent_key.torsions)
        merged.update(dict(state_value))
        return ConfgenStateKey(
            coordination=parent_key.coordination,
            rings=dict(parent_key.rings),
            torsions=merged,
        )
    raise ValueError(f"unknown generation axis {axis!r}")


@dataclass(frozen=True, slots=True)
class EngineRun:
    """Result of one engine run: leaf-only outputs plus audit records."""

    leaves: tuple[WorkingRealization, ...] = ()
    target_records: tuple[TargetRecord, ...] = ()
    report: Mapping[str, Any] = field(default_factory=FrozenDict)
    certificate: Any = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "leaves", tuple(self.leaves))
        object.__setattr__(self, "target_records", tuple(self.target_records))
        if not isinstance(self.report, FrozenDict):
            object.__setattr__(self, "report", FrozenDict(dict(self.report)))

    def report_json(self) -> dict[str, Any]:
        """Return the report as plain JSON-compatible data."""
        report = self.report
        if isinstance(report, FrozenDict):
            return report.thaw()
        return dict(report)


def _exclusion_match(
    axis: str, state_value: Mapping[str, Any], exclusions: Sequence[Mapping[str, Any]]
) -> str | None:
    """Return the matching exclusion reason, if any (exact JSON match)."""
    for exclusion in exclusions:
        if exclusion.get("axis") != axis:
            continue
        match = exclusion.get("match", {})
        if all(key in state_value and state_value[key] == value for key, value in match.items()):
            return str(exclusion.get("reason", "excluded"))
    return None


def _valid_observed_key(value: Any) -> bool:
    """Return True for a complete canonical observed state key.

    Per the CORE/B drift handshake the stage promises
    ``{center, shape, placement, sites}`` (or ``None`` when the drifted
    geometry is unperceivable). Anything else is malformed and routes to
    geometric failure, never to a drifted-state publication.
    """
    return isinstance(value, Mapping) and all(
        key in value for key in ("center", "shape", "placement", "sites")
    )


def _native_drift_observation(
    evidence: Sequence[Mapping[str, Any]],
) -> tuple[bool, dict[str, Any] | None]:
    """Split native stage verdicts into (is_drift, valid_observed|None).

    ``native_status == "DRIFTED"`` marks physical-state drift (valid
    geometry perceiving to a different state) as opposed to native
    geometric failure (solver breakdown, clash/bond/angle/stereo
    violation). Only a complete observed key routes to FAILED_DRIFT;
    drift without a perceivable state stays FAILED_GEOMETRY.
    """
    found = False
    observed: dict[str, Any] | None = None
    for item in evidence:
        if not isinstance(item, Mapping):
            continue
        if item.get("native_status") != "DRIFTED":
            continue
        found = True
        candidate = item.get("observed_key")
        if _valid_observed_key(candidate):
            observed = dict(candidate)
            break
    return found, observed


def _native_observation_quality(
    evidence: Sequence[Mapping[str, Any]],
) -> bool:
    """Return True iff the stage asserts gate-passed perception quality.

    Stages promise a ``perception_gate_passed`` verdict alongside native
    evidence. Only an explicitly gate-passed observation backs
    realization-quality claims (``geometry_valid``) and hard-bound proof
    contradictions; anything else is diagnostic evidence only. An
    invalid constraint point cannot refute a proof whose assumptions it
    violates.
    """
    for item in evidence:
        if isinstance(item, Mapping) and item.get("perception_gate_passed") is True:
            return True
    return False


def _keys_equal(first: Mapping[str, Any], second: Mapping[str, Any]) -> bool:
    """Compare commanded vs observed keys canonically (StateKey drift test).

    Drift is ``K_observed != K_target``: thawed canonical-JSON equality
    over the complete labeled keys (tuples/lists normalize together).
    Non-serializable content never compares equal.
    """
    import json

    from confflow.domain._immutable import thaw_value

    try:
        return json.dumps(
            thaw_value(dict(first)), sort_keys=True, separators=(",", ":")
        ) == json.dumps(thaw_value(dict(second)), sort_keys=True, separators=(",", ":"))
    except (TypeError, ValueError):
        return False


def _validate_driving_inherited(
    context: MolecularContext, locks: Sequence[InheritedTorsionLock]
) -> None:
    """Fail the chain closed when the driving geometry breaks carried locks."""
    tolerance = float(context.tolerances.dihedral_atol_deg)
    ok, evidence = check_inherited_torsion_locks(context.structure.coordinates, locks, tolerance)
    if not ok:
        broken = "; ".join(
            f"{item.get('axis')} measured={item.get('measured')} expected={item.get('expected')}"
            for item in evidence
            if isinstance(item, Mapping)
        )
        raise InheritedScopeError(
            "INHERITED_STATE_SCOPE_MISSING: driving geometry does not embody "
            f"inherited torsion state ({broken}); refusing to publish stale state"
        )


class ConfgenEngine:
    """Conditional DFS engine over C/R/T stage levels."""

    def __init__(
        self,
        stages: Sequence[GenerationStage] | None = None,
        *,
        allow_preserve_input: bool = False,
        backend: str = "geometric-rodrigues",
    ) -> None:
        self._explicit_stages = list(stages) if stages is not None else None
        self._allow_preserve_input = bool(allow_preserve_input)
        self._backend = str(backend)

    # -- stage registry -------------------------------------------------

    @staticmethod
    def _load_stage(axis: str, resolved: Mapping[str, Any]) -> GenerationStage:
        """Load the stage for one axis (lazy; fail closed when unavailable).

        Constructors receive a plain-data snapshot (dicts/lists) of the
        resolved spec content, which stages must treat as read-only; the
        authoritative frozen spec stays on the context.
        """
        snapshot = thaw_snapshot(resolved)
        if axis == "torsions":
            from confflow.science.confgen.torsion.stage import TorsionStage

            return TorsionStage(snapshot)
        if axis == "rings":
            try:
                from confflow.science.confgen.ring.stage import RingStage
            except ImportError as exc:
                raise UnsupportedAxisError(
                    "rings requested but the ring stage module is unavailable"
                ) from exc
            return RingStage(snapshot)
        if axis == "coordination":
            try:
                from confflow.science.confgen.coordination.stage import (
                    CoordinationStage,
                    adapt_to_core,
                )
            except ImportError as exc:
                raise UnsupportedAxisError(
                    "coordination requested but the coordination stage module is unavailable"
                ) from exc
            section = snapshot.get("coordination")
            if not isinstance(section, Mapping):
                raise UnsupportedAxisError(
                    "coordination requested but the resolved coordination section is missing"
                )
            try:
                stage = adapt_to_core(CoordinationStage(dict(section)))
            except UnsupportedAxisError:
                raise
            except Exception as exc:
                raise UnsupportedAxisError(
                    f"coordination requested but the protocol binding failed: {exc}"
                ) from exc
            if getattr(stage, "axis", None) != "coordination":
                raise UnsupportedAxisError("coordination binding returned a foreign stage")
            return stage
        raise UnsupportedAxisError(f"unknown generation axis {axis!r}")

    def _levels(self, resolved: Mapping[str, Any]) -> list[tuple[str, GenerationStage]]:
        """Return active (axis, stage) levels in AXIS_ORDER."""
        if self._explicit_stages is not None:
            levels = [(stage.axis, stage) for stage in self._explicit_stages]
            axes = [axis for axis, _ in levels]
            if sorted(axes) != sorted(set(axes)):
                raise ValueError("duplicate stage axes in explicit stage list")
            order = {axis: position for position, axis in enumerate(AXIS_ORDER)}
            levels.sort(key=lambda item: order.get(item[0], len(order)))
            return levels
        levels = []
        coordination = resolved.get("coordination")
        if isinstance(coordination, Mapping) and coordination is not None:
            if coordination.get("treatment", "enumerate") != "preserve_input":
                levels.append(("coordination", self._load_stage("coordination", resolved)))
        rings = resolved.get("rings", [])
        if isinstance(rings, (list, tuple)) and len(rings) > 0:
            levels.append(("rings", self._load_stage("rings", resolved)))
        torsions = resolved.get("torsions", [])
        if isinstance(torsions, (list, tuple)) and len(torsions) > 0:
            levels.append(("torsions", self._load_stage("torsions", resolved)))
        return levels

    # -- run --------------------------------------------------------------

    def _preserve_input_run(
        self,
        context: MolecularContext,
        inherited: Sequence[InheritedTorsionLock] = (),
    ) -> EngineRun:
        """Produce the single audited preserve-input leaf (zero axes)."""
        from confflow.domain.structure import StructureRecord

        if not self._allow_preserve_input:
            raise UnsupportedAxisError(
                "spec requests no generation axes; set allow_preserve_input=True "
                "to emit the single audited preserve-input leaf"
            )
        if inherited:
            # Zero axes with an incoming key: the single leaf still
            # validates the carried scope/key on the input geometry before
            # any scientific REALIZED certificate is minted.
            _validate_driving_inherited(context, inherited)
        seed = context.resolved_spec.get("seed")
        coords = np.asarray(context.structure.coordinates, dtype=float)
        if not np.all(np.isfinite(coords)):
            raise UnsupportedAxisError("preserve-input refused: input coordinates not finite")
        leaf_structure = StructureRecord(
            id=f"{context.structure.id}:v3:preserve",
            atoms=tuple(context.structure.atoms),
            coordinates=tuple(tuple(point) for point in context.structure.coordinates),
            charge=context.structure.charge,
            multiplicity=context.structure.multiplicity,
            parent_ids=(context.structure.id,),
            lineage_root_id=context.structure.lineage_root_id,
            role="confgen-preserve-input",
            ordinal=0,
            group_key=context.structure.group_key,
            metadata=FrozenDict({"preserved": True, "backend": self._backend}),
            **inherit_topology_kwargs(context.structure, context.adjacency),
        )
        leaf = WorkingRealization(
            structure=leaf_structure,
            state_key=context.input_state_key,
            parent_realization_id=None,
            generation_axis=None,
            locked_axes=(),
            provenance=FrozenDict(
                {
                    "leaf_ordinal": 0,
                    "leaf_target_id": "preserve:000000",
                    "seed": seed,
                    "backend": self._backend,
                    "preserved": True,
                }
            ),
        )
        records = [
            TargetRecord(
                target_id="preserve:000000",
                axis="preserve",
                ordinal=0,
                state_value=FrozenDict({}),
                complete_key=FrozenDict(context.input_state_key.to_dict()),
                status=TerminalStatus.PUBLISHED_LEAF,
                reason="preserve_input",
            )
        ]
        counts: dict[str, Any] = {
            "raw": 1,
            "policy_excluded": 0,
            "sampled": 1,
            "deferred_sampled_out": 0,
            "deferred_parent_failed_leaves": 0,
            "realized": 1,
            "published": 1,
            "failed": {},
            "status_counts": {TerminalStatus.PUBLISHED_LEAF.value: 1},
        }
        categories = certificate_category_counts(records)
        counts["target_categories"] = categories["target_categories"]
        counts["unit_basis"] = {
            "target_categories": "mixed diagnostic records; no certificate equation",
            "realized": "preserved input geometry; no stage attempts",
            "published": "final leaves",
            "authoritative_sections": ["leaf_certificate", "attempt_ledger"],
        }
        counts["internal_expanded"] = categories["internal_expanded"]
        counts["anomalies"] = categories["anomalies"]
        counts["estimated_deferred_leaves"] = categories["estimated_deferred_leaves"]
        counts["equation_exact"] = categories["equation_exact"]
        certificate = build_certificate(records, counts)
        enumeration = {
            "per_axis": {},
            "total_declared": 1,
            "total_upper_bound": 1,
            "exact": True,
            "basis": "no generation axes requested: single preserve-input leaf",
            "sampling": {"cap": None, "seed": seed, "capped": False, "sorted": True},
            "sampled": 1,
            "deferred_sampled_out": 0,
            "policy_excluded": 0,
            "digest": "",
        }
        from confflow.science.confgen.accounting import enumeration_digest as _enum_digest

        enumeration["digest"] = _enum_digest(
            {
                "total_declared": 1,
                "total_upper_bound": 1,
                "exact": True,
                "basis": enumeration["basis"],
            },
            {"cap": None, "seed": seed, "sampled": 1},
            [],
        )
        realization = {
            "attempted": 1,
            "realized": 1,
            "published": 1,
            "suppressed": 0,
            "realization_attempts": 1,
            "failed": {},
            "status_counts": {TerminalStatus.PUBLISHED_LEAF.value: 1},
            "terminal_equations_ok": certificate.equations_ok,
            "terminal_detail": "ok",
            "count_equations_ok": True,
            "count_details": {"raw": 1, "sampled": 1, "attempted": 1, "published": 1, "ok": True},
            "proof_contradictions": [],
        }
        report = {
            "schema_version": 3,
            "axis_order": list(AXIS_ORDER),
            "levels": [],
            "counts": counts,
            "enumeration": enumeration,
            "realization": realization,
            "leaf_certificate": {
                "total": 1,
                "leaf_categories": {
                    "REALIZED": 1,
                    "DRIFTED": 0,
                    "UNRESOLVED": 0,
                    "DEFERRED": 0,
                    "REALIZATION_SUPPRESSED_BY_VERIFIED_SYMMETRY": 0,
                    "REJECTED": 0,
                },
                "anomalies": {"AMBIGUOUS_KEY": 0},
                "equations_ok": True,
                "equation_exact": True,
                "count_details": realization["count_details"],
                "basis": "single preserve-input final leaf",
            },
            "attempt_ledger": {
                "issued_attempts": 0,
                "geometry_successful": 0,
                "internal_expanded": 0,
                "published_leaves": 0,
                "attempted_without_success": 0,
                "by_status": {},
                "suppressed_skipped": 0,
                "policy_screened": 0,
                "deferred_unissued": 0,
                "basis": "preserve-input leaf: no stage attempts issued",
            },
            "drift_events": [],
            "scope": {
                "donor_configuration": None,
                "preserved_axes": [],
                "excluded_axes": [],
                "breaking_pairs": [],
                "unsupported": [],
            },
            "sampling": {"cap": None, "seed": seed, "capped": False, "sorted": True},
            "suppression": {
                "supported": False,
                "scope": "preserve-input leaf: no enumeration targets",
                "disabled_reason": "preserve-input",
                "suppressed": 0,
            },
            "inherited": {
                "entries": [
                    {
                        "axis": f"torsions.{lock.axis_id}",
                        "model": lock.model,
                        "frame": list(lock.frame),
                        "expected_absolute": float(lock.expected_absolute),
                    }
                    for lock in inherited
                ],
                "audited": bool(inherited),
                "basis": (
                    "carried torsion locks re-measured on the input geometry "
                    "against prior absolute frames"
                    if inherited
                    else "no incoming chained state"
                ),
            },
            "preflight": {
                "total_declared": 1,
                "total_upper_bound": 1,
                "exact": True,
                "basis": "no generation axes requested: single preserve-input leaf",
            },
            "certificate": {
                "equations_ok": certificate.equations_ok,
                "digest": certificate.digest,
                "basis": "terminal equations verify over target records",
            },
            "provenance": {
                "backend": self._backend,
                "input_id": context.structure.id,
                "seed": seed,
            },
        }
        return EngineRun(
            leaves=(leaf,),
            target_records=tuple(records),
            report=FrozenDict(report),
            certificate=certificate,
        )

    def _symbolic_paths(
        self,
        stages: Sequence[GenerationStage],
        axes: Sequence[str],
        root: WorkingRealization,
        context: MolecularContext,
    ) -> list[tuple[int, ...]]:
        """Enumerate exact conditional leaf paths symbolically (no geometry).

        Ghost parents reuse the nearest available geometry with merged keys;
        valid for key-driven enumeration (documented assumption: stages whose
        enumeration reads parent geometry must declare it; deterministic
        enumeration required). The geometry DFS must visit exactly these
        paths (checked at finish; mismatch invalidates the certificate).
        """
        paths: list[tuple[int, ...]] = []
        last = len(stages) - 1

        def rec(level: int, ghost: WorkingRealization, path: tuple[int, ...]) -> None:
            for target in stages[level].enumerate_targets(ghost, context):
                child_path = path + (int(target.ordinal),)
                if level == last:
                    paths.append(child_path)
                else:
                    ghost_child = WorkingRealization(
                        structure=ghost.structure,
                        state_key=combine_state_key(
                            ghost.state_key, axes[level], dict(target.state_value)
                        ),
                    )
                    rec(level + 1, ghost_child, child_path)

        rec(0, root, ())
        return paths

    def run(
        self,
        context: MolecularContext,
        should_cancel: Callable[[], bool] | None = None,
    ) -> EngineRun:
        """Run the conditional DFS and return leaves plus audit records."""
        resolved = context.resolved_spec
        seed = resolved.get("seed")
        cap, sampling_seed = sampling_of(resolved)
        inherited = inherited_torsion_locks(context)
        if inherited:
            # The driving geometry must still embody the carried state;
            # otherwise the chain is refused before any geometry is spent.
            _validate_driving_inherited(context, inherited)
        levels = self._levels(resolved)
        if not levels:
            return self._preserve_input_run(context, inherited=inherited)
        stages = [stage for _, stage in levels]
        axes = [axis for axis, _ in levels]

        report_preflight = preflight(context, stages)
        limits = dict(resolved.get("limits", {}) or {})
        if cap is not None:
            limits["_sampling_cap"] = int(cap)
        check_limits(report_preflight, limits)

        conditional = any(stage.is_conditional(context) for stage in stages)
        if conditional and cap is not None:
            raise ValueError(
                "sampling cap over a conditional tree requires explicit tree "
                "weights for global uniform sampling; refusing biased cap"
            )
        # Verified-symmetry suppression scope (conservative): single-axis
        # runs only, and never under a sampling cap (a representative may
        # be unsampled) nor with top-level exclusions (a representative
        # may be policy-rejected; per-target representative state is
        # unknown at proposal time). Disabled scope realizes normally
        # with an explicit report basis; the certificate linkage
        # (representative published) remains as backstop.
        if len(stages) != 1:
            suppression_disabled = "combined-axes"
        elif cap is not None:
            suppression_disabled = "sampling-cap"
        elif resolved.get("exclusions"):
            suppression_disabled = "exclusions-present"
        else:
            suppression_disabled = None
        per_axis = report_preflight.per_axis
        level_counts = [int(per_axis[axis].declared_count) for axis in axes]
        if any(count < 1 for count in level_counts):
            raise ValueError(f"degenerate declared level counts {level_counts}")
        # Conditional root products are conservative UPPER bounds, never exact
        # leaf totals: later enumeration may vary counts per parent.
        preflight_exact = bool(report_preflight.exact) and not conditional
        preflight_basis = report_preflight.basis
        if conditional:
            preflight_basis += "; conditional tree: root product is a conservative upper bound"
        grid = MixedRadixGrid(level_counts)
        root = WorkingRealization(structure=context.structure, state_key=context.input_state_key)
        if conditional:
            # EXACT symbolic conditional expansion (no geometry): enumerate
            # the key-driven tree to completion and count true leaf states.
            # Never report the root product as raw when counts vary.
            leaf_paths = self._symbolic_paths(stages, axes, root, context)
            leaf_total = len(leaf_paths)
            path_index = {path: position for position, path in enumerate(leaf_paths)}
        else:
            leaf_paths = []
            path_index = {}
            leaf_total = grid.total
        sampled_seq = sample_indices(leaf_total, cap, sampling_seed if cap is not None else None)
        capped = cap is not None and cap < leaf_total
        sampled_set = set(sampled_seq) if capped else None
        allowed: dict[tuple[int, tuple[int, ...]], set[int]] = {}
        if capped:
            assert sampled_set is not None
            for flat in sampled_set:
                combo = grid.index_to_combo(flat)
                for level in range(len(axes)):
                    allowed.setdefault((level, combo[:level]), set()).add(combo[level])
        deferred_leaf_ranges = deferred_ranges(sampled_seq, leaf_total) if capped else ()

        # Symbolic count validation uses O(1)-memory passes (never cached
        # target lists). Hook stages stream ordinals directly; other stages
        # stream their lazy iterators with prefix filtering when capped.
        enum_counts: dict[int, int] = {}

        def _level_targets(level: int, parent: WorkingRealization) -> Iterator[GenerationTarget]:
            stage = stages[level]
            prefix = _prefix_of(parent, level, axes)
            if conditional or stage.is_conditional(context):
                # True symbolic C->R(C)->T(C,R): fresh enumeration per parent.
                for target in stage.enumerate_targets(parent, context):
                    if capped and target.ordinal not in allowed.get((level, prefix), set()):
                        continue
                    yield target
                return
            hook = getattr(stage, "target_by_ordinal", None)
            if hook is not None:
                # Full generation streams ordinals; sampled generation fetches
                # ONLY wanted ordinals. No full-target list is ever built.
                if capped:
                    wanted = sorted(allowed.get((level, prefix), set()))
                else:
                    wanted = range(level_counts[level])
                for ordinal in wanted:
                    yield hook(parent, int(ordinal), context)
                return
            if level not in enum_counts:
                enum_counts[level] = sum(1 for _ in stage.enumerate_targets(parent, context))
                if enum_counts[level] != level_counts[level]:
                    raise ValueError(
                        f"stage {axes[level]!r} enumerated {enum_counts[level]} "
                        f"targets but declared {level_counts[level]}; "
                        "sampling math unsound"
                    )
            for target in stage.enumerate_targets(parent, context):
                if capped and target.ordinal not in allowed.get((level, prefix), set()):
                    continue
                yield target

        state = _RunState(
            engine=self,
            context=context,
            stages=stages,
            axes=axes,
            level_counts=level_counts,
            grid=grid,
            conditional=conditional,
            seed=seed,
            should_cancel=should_cancel,
            leaf_paths=leaf_paths,
            path_index=path_index,
            inherited=inherited,
            suppression_disabled=suppression_disabled,
        )
        state.run_level(0, root, (), (root,), (), None, _level_targets)
        return state.finish(
            leaf_total=leaf_total,
            cap=cap,
            sampling_seed=sampling_seed if capped else None,
            capped=capped,
            deferred_leaf_ranges=deferred_leaf_ranges,
            preflight=report_preflight,
            preflight_exact=preflight_exact,
            preflight_basis=preflight_basis,
        )


def _prefix_of(parent: WorkingRealization, level: int, axes: Sequence[str]) -> tuple[int, ...]:
    """Recover the ancestor ordinal path prefix from realization provenance."""
    path = parent.provenance.get("path_ordinals", ())
    return tuple(int(value) for value in path[:level])


class _RunState:
    """Mutable DFS accumulator (one run; never shared)."""

    def __init__(
        self,
        *,
        engine: ConfgenEngine,
        context: MolecularContext,
        stages: list[GenerationStage],
        axes: list[str],
        level_counts: list[int],
        grid: MixedRadixGrid,
        conditional: bool,
        seed: Any,
        should_cancel: Callable[[], bool] | None,
        leaf_paths: Sequence[tuple[int, ...]] = (),
        path_index: Mapping[tuple[int, ...], int] | None = None,
        inherited: Sequence[InheritedTorsionLock] = (),
        suppression_disabled: str | None = None,
    ) -> None:
        self._engine = engine
        self._context = context
        self._stages = stages
        self._axes = axes
        self._level_counts = level_counts
        self._grid = grid
        self._conditional = conditional
        self._seed = seed
        self._should_cancel = should_cancel
        self._leaf_paths = tuple(leaf_paths)
        self._path_index = dict(path_index) if path_index is not None else {}
        self._inherited = tuple(inherited)
        self._suppression_disabled = suppression_disabled
        self._visited_paths: list[tuple[int, ...]] = []
        self.records: list[TargetRecord] = []
        self.leaves: list[WorkingRealization] = []
        self.drift_events: list[dict[str, Any]] = []
        self.proof_contradictions: list[dict[str, Any]] = []
        self.policy_excluded = 0
        self.suppressed_count = 0
        self.realized_ok = 0
        self.failed_counts: dict[str, int] = {}
        self.deferred_parent_leaves = 0

    # -- recursion -------------------------------------------------------

    def run_level(
        self,
        level: int,
        parent: WorkingRealization,
        path: tuple[int, ...],
        ancestors: tuple[WorkingRealization, ...],
        ancestor_specs: tuple[tuple[str, Mapping[str, Any], str | None], ...],
        parent_target_id: str | None,
        level_targets: Callable[[int, WorkingRealization], Iterator[GenerationTarget]],
    ) -> None:
        """Expand one DFS level under *parent*."""
        stage = self._stages[level]
        axis = self._axes[level]
        last = level == len(self._stages) - 1
        exclusions = self._context.resolved_spec.get("exclusions", []) or []
        for target in level_targets(level, parent):
            if self._should_cancel is not None and self._should_cancel():
                raise EngineCancelledError("confgen run cancelled by probe")
            self._expand_target(
                level,
                stage,
                axis,
                last,
                parent,
                path,
                ancestors,
                ancestor_specs,
                parent_target_id,
                target,
                exclusions,
                level_targets,
            )

    def _expand_target(
        self,
        level: int,
        stage: GenerationStage,
        axis: str,
        last: bool,
        parent: WorkingRealization,
        path: tuple[int, ...],
        ancestors: tuple[WorkingRealization, ...],
        ancestor_specs: tuple[tuple[str, Mapping[str, Any], str | None], ...],
        parent_target_id: str | None,
        target: GenerationTarget,
        exclusions: Sequence[Mapping[str, Any]],
        level_targets: Callable[[int, WorkingRealization], Iterator[GenerationTarget]],
    ) -> None:
        """Realize, audit, and account one target."""
        context = self._context
        state_dict = dict(target.state_value)
        child_path = path + (int(target.ordinal),)

        reason = _exclusion_match(axis, state_dict, exclusions)
        if reason is not None:
            record = TargetRecord(
                target_id=target.target_id,
                axis=axis,
                ordinal=int(target.ordinal),
                state_value=FrozenDict(state_dict),
                complete_key=FrozenDict(
                    combine_state_key(parent.state_key, axis, state_dict).to_dict()
                ),
                status=TerminalStatus.REJECTED_BY_POLICY,
                reason=f"rejected_by_policy:{reason}",
                parent_target_id=parent_target_id,
            )
            self.records.append(record)
            self.policy_excluded += 1
            self._defer_subtree(
                level,
                child_path,
                record.target_id,
                "policy_rejection",
                parent=parent,
                axis=axis,
                state_dict=state_dict,
            )
            return

        if self._try_suppression(
            level,
            stage,
            axis,
            parent,
            child_path,
            parent_target_id,
            target,
            state_dict,
        ):
            return

        outcome = None
        try:
            outcome = stage.realize(parent, target, context)
        except Exception as exc:  # stage bug: account explicitly, never silent
            self._record_failure(
                target,
                axis,
                state_dict,
                parent,
                parent_target_id,
                TerminalStatus.FAILED_NUMERICAL,
                f"stage_error:{type(exc).__name__}",
                ({"kind": "stage_error", "detail": str(exc)[:300]},),
            )
            self._defer_subtree(
                level,
                child_path,
                target.target_id,
                "stage_error",
                parent=parent,
                axis=axis,
                state_dict=state_dict,
            )
            return
        assert outcome is not None
        if outcome.status != "realized" or outcome.structure is None:
            if outcome.status == "geometry_failure":
                # Native stage verdicts distinguish physical-state drift
                # (valid geometry perceiving to another state) from solver
                # breakdown. StateKey drift is K_observed != K_target: only
                # an unambiguous observed key DIFFERING from the commanded
                # target routes to FAILED_DRIFT (public DRIFTED with
                # observed-key evidence). An observed key EQUAL to the
                # target with bond/angle/stereo/clash failure is a
                # geometric audit failure (FAILED_GEOMETRY, public
                # UNRESOLVED), as is drift with no perceivable state.
                # Drifted geometry is never stamped REALIZED.
                native_drift, observed = _native_drift_observation(tuple(outcome.evidence))
                if (
                    native_drift
                    and observed is not None
                    and not _keys_equal(dict(target.state_value), observed)
                ):
                    quality = _native_observation_quality(tuple(outcome.evidence))
                    self._record_drift(
                        target,
                        axis,
                        state_dict,
                        parent,
                        parent_target_id,
                        tuple(outcome.evidence),
                        observed,
                        "unknown",
                        exclusions,
                        reason=outcome.reason,
                        geometry_valid=quality,
                        proof_trusted=quality,
                    )
                    self._defer_subtree(
                        level,
                        child_path,
                        target.target_id,
                        outcome.reason,
                        parent=parent,
                        axis=axis,
                        state_dict=state_dict,
                    )
                    return
            status = {
                "geometry_failure": TerminalStatus.FAILED_GEOMETRY,
                "numerical_failure": TerminalStatus.FAILED_NUMERICAL,
                "unsupported": TerminalStatus.UNSUPPORTED,
            }[outcome.status]
            record = TargetRecord(
                target_id=target.target_id,
                axis=axis,
                ordinal=int(target.ordinal),
                state_value=FrozenDict(state_dict),
                complete_key=FrozenDict(
                    combine_state_key(parent.state_key, axis, state_dict).to_dict()
                ),
                status=status,
                reason=outcome.reason,
                evidence=tuple(outcome.evidence),
                parent_target_id=parent_target_id,
            )
            self.records.append(record)
            self.failed_counts[status.value] = self.failed_counts.get(status.value, 0) + 1
            self._defer_subtree(
                level,
                child_path,
                record.target_id,
                outcome.reason,
                parent=parent,
                axis=axis,
                state_dict=state_dict,
            )
            return

        structure = outcome.structure
        if not check_atom_order(structure, context):
            raise AtomOrderViolationError(
                "CONFGEN_ATOM_ORDER_VIOLATION: "
                f"target {target.target_id} axis {axis} reordered atoms; "
                "run certificate invalid, no partial output"
            )

        locks_ok, lock_evidence = audit_parent_locks(
            structure,
            ancestors,
            context,
            parent=parent,
            check_bond_integrity=(axis == "torsions"),
        )
        if not locks_ok:
            kinds = {str(item.get("kind")) for item in lock_evidence}
            if "atom_order_violation" in kinds or "atom_count_violation" in kinds:
                raise AtomOrderViolationError(
                    "CONFGEN_ATOM_ORDER_VIOLATION: "
                    f"target {target.target_id} axis {axis}: "
                    f"{lock_evidence[0].get('kind') if lock_evidence else 'lock'}; "
                    "run certificate invalid, no partial output"
                )
            self._record_failure(
                target,
                axis,
                state_dict,
                parent,
                parent_target_id,
                TerminalStatus.FAILED_NUMERICAL,
                str(lock_evidence[0].get("kind", "lock")) if lock_evidence else "lock",
                lock_evidence,
            )
            self._defer_subtree(
                level,
                child_path,
                target.target_id,
                "parent_lock",
                parent=parent,
                axis=axis,
                state_dict=state_dict,
            )
            return

        # STRICT upstream lock audit: re-perceive every realized ancestor
        # axis on the fresh geometry. Any drift blocks publication.
        verdict, lock_state_evidence, lock_observed, lock_oos = self._audit_locks(
            ancestor_specs, structure, parent.structure.coordinates
        )
        if verdict == "ambiguous":
            self._record_failure(
                target,
                axis,
                state_dict,
                parent,
                parent_target_id,
                TerminalStatus.UNRESOLVED,
                "ambiguous_perception",
                tuple(lock_state_evidence)
                + (
                    {
                        "kind": "anomaly",
                        "anomaly": "AMBIGUOUS_KEY",
                        "detail": "ancestor lock state unmeasurable",
                    },
                ),
            )
            self._defer_subtree(
                level,
                child_path,
                target.target_id,
                "ambiguous",
                parent=parent,
                axis=axis,
                state_dict=state_dict,
            )
            return
        if verdict == "drift":
            self._record_drift(
                target,
                axis,
                state_dict,
                parent,
                parent_target_id,
                lock_state_evidence,
                lock_observed,
                lock_oos,
                exclusions,
            )
            self._defer_subtree(
                level,
                child_path,
                target.target_id,
                "lock_drift",
                parent=parent,
                axis=axis,
                state_dict=state_dict,
            )
            return

        # Carried (inherited) torsion locks: re-measured on every fresh
        # geometry against the prior absolute frames. Any drift blocks
        # publication with the stale parent key (FAILED_DRIFT, never a
        # silent carry).
        if self._inherited:
            tolerance = float(context.tolerances.dihedral_atol_deg)
            inherited_ok, inherited_evidence = check_inherited_torsion_locks(
                structure.coordinates, self._inherited, tolerance
            )
            if not inherited_ok:
                self._record_drift(
                    target,
                    axis,
                    state_dict,
                    parent,
                    parent_target_id,
                    list(lock_evidence) + list(inherited_evidence),
                    {},
                    "unknown",
                    exclusions,
                )
                self._defer_subtree(
                    level,
                    child_path,
                    target.target_id,
                    "inherited_lock_drift",
                    parent=parent,
                    axis=axis,
                    state_dict=state_dict,
                )
                return

        try:
            perception = stage.perceive(structure, context)
        except Exception as exc:  # unmeasurable: anomaly, never a published state
            self._record_failure(
                target,
                axis,
                state_dict,
                parent,
                parent_target_id,
                TerminalStatus.UNRESOLVED,
                "ambiguous_perception",
                tuple(lock_evidence)
                + (
                    {
                        "kind": "anomaly",
                        "anomaly": "AMBIGUOUS_KEY",
                        "detail": f"perception raised {type(exc).__name__}: {str(exc)[:200]}",
                    },
                ),
            )
            self._defer_subtree(
                level,
                child_path,
                target.target_id,
                "ambiguous",
                parent=parent,
                axis=axis,
                state_dict=state_dict,
            )
            return
        axis_ids = stage.axis_ids(context)
        coverage_ok = True
        if axis_ids is not None:
            missing = [key for key in axis_ids if key not in perception.best_key]
            if missing:
                coverage_ok = False
                lock_evidence = list(lock_evidence) + [
                    {
                        "kind": "anomaly",
                        "anomaly": "AMBIGUOUS_KEY",
                        "detail": f"perception missing measured axes {missing}",
                    }
                ]
        if perception.confidence == "ambiguous" or not coverage_ok:
            anomaly = [
                item
                for item in lock_evidence
                if isinstance(item, Mapping) and item.get("kind") == "anomaly"
            ]
            if not anomaly:
                anomaly = [
                    {
                        "kind": "anomaly",
                        "anomaly": "AMBIGUOUS_KEY",
                        "detail": "stage perception ambiguous",
                    }
                ]
            self._record_failure(
                target,
                axis,
                state_dict,
                parent,
                parent_target_id,
                TerminalStatus.UNRESOLVED,
                "ambiguous_perception",
                tuple(lock_evidence) + tuple(anomaly),
            )
            self._defer_subtree(
                level,
                child_path,
                target.target_id,
                "ambiguous",
                parent=parent,
                axis=axis,
                state_dict=state_dict,
            )
            return

        audit = getattr(stage, "audit_target", None)
        measured: dict[str, Any] = dict(perception.best_key)
        evidence: list[dict[str, Any]] = list(lock_evidence)
        if audit is not None:
            try:
                ok, stage_measured, stage_evidence = audit(structure, target, parent, context)
            except Exception as exc:  # audit hook bug: cannot verify -> unresolved
                self._record_failure(
                    target,
                    axis,
                    state_dict,
                    parent,
                    parent_target_id,
                    TerminalStatus.UNRESOLVED,
                    "ambiguous_perception",
                    tuple(lock_evidence)
                    + (
                        {
                            "kind": "anomaly",
                            "anomaly": "AMBIGUOUS_KEY",
                            "detail": f"audit raised {type(exc).__name__}: {str(exc)[:200]}",
                        },
                    ),
                )
                self._defer_subtree(
                    level,
                    child_path,
                    target.target_id,
                    "ambiguous",
                    parent=parent,
                    axis=axis,
                    state_dict=state_dict,
                )
                return
            measured = dict(stage_measured)
            evidence.extend(stage_evidence)
            if not ok:
                observed_map = self._observed_map(stage, state_dict, measured, stage_evidence)
                self._record_drift(
                    target,
                    axis,
                    state_dict,
                    parent,
                    parent_target_id,
                    evidence,
                    observed_map["observed"],
                    observed_map["out_of_scope"],
                    exclusions,
                )
                self._defer_subtree(
                    level,
                    child_path,
                    target.target_id,
                    "drift",
                    parent=parent,
                    axis=axis,
                    state_dict=state_dict,
                )
                return

        for item in evidence:
            if isinstance(item, Mapping) and item.get("kind") == "drift":
                self.drift_events.append(
                    {"target_id": target.target_id, **{k: item[k] for k in item}}
                )
        # Preserved axes: real perception snapped to discrete entries, merged
        # into the key and locked downstream (never scope-annotation-only).
        preserved: dict[str, Any] = {}
        preserved_hook = getattr(stage, "preserved_state", None)
        if preserved_hook is not None:
            try:
                preserved_map, preserved_evidence = preserved_hook(structure, context)
                preserved = dict(preserved_map)
                evidence.extend(preserved_evidence)
            except Exception as exc:
                self._record_failure(
                    target,
                    axis,
                    state_dict,
                    parent,
                    parent_target_id,
                    TerminalStatus.UNRESOLVED,
                    "ambiguous_perception",
                    tuple(evidence)
                    + (
                        {
                            "kind": "anomaly",
                            "anomaly": "AMBIGUOUS_KEY",
                            "detail": f"preserved-state hook raised {type(exc).__name__}",
                        },
                    ),
                )
                self._defer_subtree(
                    level,
                    child_path,
                    target.target_id,
                    "ambiguous",
                    parent=parent,
                    axis=axis,
                    state_dict=state_dict,
                )
                return
        self.realized_ok += 1
        merged_state = dict(state_dict)
        merged_state.update(preserved)
        for lock in self._inherited if axis == "torsions" else ():
            # Carried axes retain their verified prior discrete identity:
            # re-snapping a relative label against the new input reference
            # would let a new-reference 0 masquerade as the old label.
            merged_state[lock.axis_id] = lock.label
        child_key = combine_state_key(parent.state_key, axis, merged_state)
        locked_contribution = merged_state
        locked = tuple(a for a in AXIS_ORDER if a in (set(parent.locked_axes) | {axis}))
        child_provenance = {
            "seed": self._seed,
            "backend": self._engine._backend,
            "path_ordinals": list(child_path),
            "parent_target_id": parent_target_id,
            "measured": dict(measured),
        }
        child = WorkingRealization(
            structure=structure,
            state_key=child_key,
            parent_realization_id=parent.structure.id,
            generation_axis=axis,
            locked_axes=locked,
            provenance=FrozenDict(child_provenance),
        )
        if last:
            if self._conditional:
                leaf_ordinal = self._path_index.get(child_path)
                if leaf_ordinal is None:
                    raise EngineConsistencyError(
                        f"geometry DFS visited unenumerated leaf path {child_path}; "
                        "conditional enumeration is not key-stable"
                    )
            else:
                leaf_ordinal = self._flat_index(child_path)
            self._visited_paths.append(child_path)
            leaf = WorkingRealization(
                structure=child.structure,
                state_key=child.state_key,
                parent_realization_id=child.parent_realization_id,
                generation_axis=child.generation_axis,
                locked_axes=child.locked_axes,
                provenance=FrozenDict(
                    {
                        **child_provenance,
                        "leaf_ordinal": leaf_ordinal,
                        "leaf_target_id": target.target_id,
                        "leaf_path": "-".join(str(v) for v in child_path),
                    }
                ),
            )
            self.leaves.append(leaf)
            self.records.append(
                TargetRecord(
                    target_id=target.target_id,
                    axis=axis,
                    ordinal=int(target.ordinal),
                    state_value=FrozenDict(state_dict),
                    complete_key=FrozenDict(child_key.to_dict()),
                    status=TerminalStatus.PUBLISHED_LEAF,
                    reason="published_leaf",
                    evidence=tuple(evidence),
                    parent_target_id=parent_target_id,
                )
            )
        else:
            self.records.append(
                TargetRecord(
                    target_id=target.target_id,
                    axis=axis,
                    ordinal=int(target.ordinal),
                    state_value=FrozenDict(state_dict),
                    complete_key=FrozenDict(child_key.to_dict()),
                    status=TerminalStatus.EXPANDED,
                    reason="expanded",
                    evidence=tuple(evidence),
                    parent_target_id=parent_target_id,
                )
            )
            self.run_level(
                level + 1,
                child,
                child_path,
                ancestors + (child,),
                ancestor_specs + ((axis, locked_contribution, target.target_id),),
                target.target_id,
                level_targets,
            )

    # -- helpers ----------------------------------------------------------

    def _stage_for(self, axis: str) -> GenerationStage:
        """Return the stage bound to one axis."""
        for name, stage in zip(self._axes, self._stages):
            if name == axis:
                return stage
        raise ValueError(f"no stage bound for axis {axis!r}")

    @staticmethod
    def _aggregate_oos(evidence: Sequence[Mapping[str, Any]]) -> Any:
        """Aggregate out_of_scope flags: True wins, then False, else unknown."""
        values = [
            item.get("out_of_scope")
            for item in evidence
            if isinstance(item, Mapping) and "out_of_scope" in item
        ]
        if any(value is True for value in values):
            return True
        if values and all(value is False for value in values):
            return False
        return "unknown"

    @staticmethod
    def _anomaly(detail: str) -> dict[str, Any]:
        """Build an AMBIGUOUS_KEY anomaly payload."""
        return {"kind": "anomaly", "anomaly": "AMBIGUOUS_KEY", "detail": detail}

    def _audit_locks(
        self,
        ancestor_specs: Sequence[tuple[str, Mapping[str, Any], str | None]],
        structure: Any,
        parent_coords: Any,
    ) -> tuple[str, list[dict[str, Any]], dict[str, Any], Any]:
        """Re-perceive every locked ancestor axis (strict).

        Returns (verdict, evidence, observed, out_of_scope) with verdict in
        ok|drift|ambiguous. Hook (`verify_locked`) first; else perceive plus
        exact discrete compare; else the ring tolerance-aware matcher.
        Parent-relative stages (``lock_reference == "parent"``) receive a
        reference context whose ``input_coords`` are the accepted parent
        realization geometry; all others verify against the run input.
        """
        import dataclasses

        context = self._context
        for axis, locked_state, _target_id in ancestor_specs:
            stage = self._stage_for(axis)
            hook = getattr(stage, "verify_locked", None)
            if hook is not None:
                mode = getattr(stage, "lock_reference", "input")
                if mode not in ("input", "parent"):
                    raise ValueError(
                        f"stage {axis!r} declares unknown lock_reference "
                        f"{mode!r}; expected 'input' or 'parent'"
                    )
                hook_context = context
                if mode == "parent":
                    hook_context = dataclasses.replace(
                        context,
                        input_coords=tuple(tuple(float(v) for v in row) for row in parent_coords),
                    )
                try:
                    ok, snapped, hook_evidence = hook(structure, dict(locked_state), hook_context)
                except Exception as exc:
                    return (
                        "ambiguous",
                        [self._anomaly(f"lock hook raised {type(exc).__name__}: {str(exc)[:200]}")],
                        {},
                        "unknown",
                    )
                if ok:
                    continue
                kinds = {
                    str(item.get("kind")) for item in hook_evidence if isinstance(item, Mapping)
                }
                if "anomaly" in kinds or "lock_error" in kinds:
                    return (
                        "ambiguous",
                        list(hook_evidence) + [self._anomaly("ancestor lock unverifiable")],
                        {},
                        "unknown",
                    )
                return (
                    "drift",
                    list(hook_evidence),
                    dict(snapped),
                    self._aggregate_oos(hook_evidence),
                )
            verdict, evidence, observed, oos = self._fallback_lock(
                stage, axis, dict(locked_state), structure, context
            )
            if verdict != "ok":
                return verdict, evidence, observed, oos
        return "ok", [], {}, False

    def _fallback_lock(
        self,
        stage: GenerationStage,
        axis: str,
        locked_state: Mapping[str, Any],
        structure: Any,
        context: MolecularContext,
    ) -> tuple[str, list[dict[str, Any]], dict[str, Any], Any]:
        """Verify one ancestor lock without a stage hook.

        Rings use the tolerance-aware matcher; other axes use exact discrete
        comparison (sound for discrete vocabularies; torsion owns its hook).
        """
        try:
            perception = stage.perceive(structure, context)
        except Exception as exc:
            return (
                "ambiguous",
                [self._anomaly(f"lock perception raised {type(exc).__name__}")],
                {},
                "unknown",
            )
        best = dict(perception.best_key)
        if perception.confidence == "ambiguous":
            return (
                "ambiguous",
                [self._anomaly("ancestor lock perception ambiguous")],
                {},
                "unknown",
            )
        if axis == "rings":
            try:
                from confflow.science.confgen.ring.perception import ring_states_match
            except ImportError:
                ring_states_match = None  # type: ignore[assignment]
            if ring_states_match is not None:
                tolerance = float(context.tolerances.ring_torsion_atol_deg)
                drifted: list[dict[str, Any]] = []
                for ring_id, commanded in locked_state.items():
                    observed = best.get(ring_id)
                    if not isinstance(observed, Mapping):
                        return (
                            "ambiguous",
                            [self._anomaly(f"ancestor ring {ring_id!r} unmeasured")],
                            {},
                            "unknown",
                        )
                    match, match_evidence = ring_states_match(
                        commanded, observed, torsion_atol_deg=tolerance
                    )
                    if not match:
                        payload = {
                            "kind": "drift",
                            "axis": f"rings.{ring_id}",
                            "detail": "ancestor ring lock drifted",
                            "match_evidence": dict(match_evidence),
                            "observed": dict(observed),
                        }
                        payload["out_of_scope"] = bool(
                            observed.get("template") != commanded.get("template")
                            if isinstance(commanded, Mapping)
                            else True
                        )
                        drifted.append(payload)
                if drifted:
                    return "drift", drifted, {}, self._aggregate_oos(drifted)
                return "ok", [], {}, False
        missing = [key for key in locked_state if key not in best]
        if missing:
            return (
                "ambiguous",
                [self._anomaly(f"ancestor lock axes unmeasured: {missing}")],
                {},
                "unknown",
            )
        drifted = []
        for key, expected in locked_state.items():
            if best.get(key) != expected:
                drifted.append(
                    {
                        "kind": "drift",
                        "axis": f"{axis}.{key}",
                        "detail": "ancestor lock drifted",
                        "expected": expected,
                        "measured": best.get(key),
                        "out_of_scope": "unknown",
                    }
                )
        if drifted:
            return "drift", drifted, {k: best[k] for k in locked_state}, "unknown"
        return "ok", [], {}, False

    @staticmethod
    def _observed_map(
        stage: GenerationStage,
        state_dict: Mapping[str, Any],
        measured: Mapping[str, Any],
        evidence: Sequence[Mapping[str, Any]],
    ) -> dict[str, Any]:
        """Build the observed/out-of-scope routing for a fresh-audit drift."""
        _ = (stage, state_dict)
        return {
            "observed": dict(measured),
            "out_of_scope": _RunState._aggregate_oos(evidence),
        }

    def _proof_contradictions(
        self,
        axis: str,
        observed: Mapping[str, Any],
        exclusions: Sequence[Mapping[str, Any]],
    ) -> list[dict[str, Any]]:
        """Flag drifted states matching proof-backed exclusions."""
        hits: list[dict[str, Any]] = []
        for exclusion in exclusions:
            proof = exclusion.get("proof")
            if exclusion.get("axis") != axis or not isinstance(proof, Mapping):
                continue
            match = exclusion.get("match", {})
            if not isinstance(match, Mapping) or not match:
                continue
            if all(key in observed and observed[key] == value for key, value in match.items()):
                hits.append(
                    {
                        "proof_id": proof.get("proof_id"),
                        "matched": {key: observed[key] for key in match if key in observed},
                    }
                )
        return hits

    def _record_drift(
        self,
        target: GenerationTarget,
        axis: str,
        state_dict: Mapping[str, Any],
        parent: WorkingRealization,
        parent_target_id: str | None,
        evidence: Sequence[Mapping[str, Any]],
        observed: Mapping[str, Any],
        out_of_scope: Any,
        exclusions: Sequence[Mapping[str, Any]],
        reason: str = "drift",
        geometry_valid: bool | None = None,
        proof_trusted: bool = True,
    ) -> None:
        """Record a DRIFTED target with observed/out-of-scope routing."""
        record_evidence: list[dict[str, Any]] = list(evidence)
        observed_payload: dict[str, Any] = {
            "kind": "observed",
            "observed": dict(observed),
            "out_of_scope": out_of_scope,
        }
        if geometry_valid is not None:
            # REALIZED_VIA_DRIFT quality claims ride only on gate-passed
            # observations; otherwise the observed state is diagnostic.
            observed_payload["geometry_valid"] = bool(geometry_valid)
        record_evidence.append(observed_payload)
        for hit in self._proof_contradictions(axis, observed, exclusions):
            if proof_trusted:
                record_evidence.append({"kind": "proof_contradiction", **hit})
                self.proof_contradictions.append({"target_id": target.target_id, **hit})
            else:
                # Diagnostic alert only: an invalid constraint point
                # cannot refute a hard-bound proof, so the certificate
                # stands while the match stays visible.
                record_evidence.append(
                    {
                        "kind": "proof_alert",
                        **hit,
                        "detail": "drifted state matches a proof-backed exclusion "
                        "without gate-passed quality; certificate stands",
                    }
                )
        self._record_failure(
            target,
            axis,
            state_dict,
            parent,
            parent_target_id,
            TerminalStatus.FAILED_DRIFT,
            reason,
            record_evidence,
        )

    #: Verified-suppression record fields the engine requires (agreed
    #: CORE/B hook schema). Bare booleans or naked orbit labels never
    #: suppress; every field must be present and the representative must
    #: differ from the suppressed target.
    _SUPPRESSION_REQUIRED_FIELDS: tuple[str, ...] = (
        "suppressed_target",
        "representative_target",
        "orbit_id",
        "rho",
        "site_action",
        "rotation",
        "pair_residual",
        "witness_provenance",
        "stereo_centers_audited",
        "closure_order",
    )

    def _try_suppression(
        self,
        level: int,
        stage: GenerationStage,
        axis: str,
        parent: WorkingRealization,
        child_path: tuple[int, ...],
        parent_target_id: str | None,
        target: GenerationTarget,
        state_dict: Mapping[str, Any],
    ) -> bool:
        """Consult the verified-symmetry hook; True when handled suppressed.

        Suppression is a single-axis scope only: combined runs realize
        normally and report the scope as unsupported (fail closed, never
        hidden removal). A returned record must carry the full agreed
        witness schema with a representative differing from the
        suppressed target; malformed records fail closed loudly. When
        the named representative already resolved to a non-published
        outcome, the target realizes normally (no orbit without a
        realized representative is ever hidden). The terminal-equation
        linkage (representative published) is enforced at certificate
        time.
        """
        if self._suppression_disabled is not None:
            return False
        hook = getattr(stage, "suppression_for_target", None)
        if hook is None:
            return False
        try:
            record = hook(parent, target, self._context)
        except Exception as exc:  # stage bug: account explicitly, never silent
            self._record_failure(
                target,
                axis,
                state_dict,
                parent,
                parent_target_id,
                TerminalStatus.FAILED_NUMERICAL,
                f"stage_error:{type(exc).__name__}",
                ({"kind": "stage_error", "detail": str(exc)[:300]},),
            )
            self._defer_subtree(
                level,
                child_path,
                target.target_id,
                "stage_error",
                parent=parent,
                axis=axis,
                state_dict=state_dict,
            )
            return True
        if record is None:
            return False
        if not isinstance(record, Mapping):
            raise ValueError(f"suppression record for {target.target_id} must be a mapping")
        missing = [key for key in self._SUPPRESSION_REQUIRED_FIELDS if key not in record]
        if missing:
            raise ValueError(
                f"suppression record for {target.target_id} lacks witness "
                f"fields {missing}; bare suppressions fail closed"
            )
        representative = record["representative_target"]
        if record["suppressed_target"] != target.target_id:
            raise ValueError(
                f"suppression record names {record['suppressed_target']!r}, "
                f"not the proposed target {target.target_id!r}"
            )
        if representative == target.target_id:
            raise ValueError(
                f"suppression record for {target.target_id} names itself "
                "representative; representatives realize normally"
            )
        for prior in self.records:
            if prior.target_id == representative and (
                prior.status is not TerminalStatus.PUBLISHED_LEAF
            ):
                return False  # no realized representative: realize normally
        payload: dict[str, Any] = {"kind": "suppression"}
        for witness_field in self._SUPPRESSION_REQUIRED_FIELDS:
            payload[witness_field] = record[witness_field]
        payload["parent_key"] = parent.state_key.to_dict()
        suppressed_key = combine_state_key(parent.state_key, axis, state_dict)
        self.records.append(
            TargetRecord(
                target_id=target.target_id,
                axis=axis,
                ordinal=int(target.ordinal),
                state_value=FrozenDict(dict(state_dict)),
                complete_key=FrozenDict(suppressed_key.to_dict()),
                status=TerminalStatus.SUPPRESSED_BY_VERIFIED_SYMMETRY,
                reason=f"suppressed_by_verified_symmetry:representative={representative}",
                evidence=(payload,),
                parent_target_id=parent_target_id,
            )
        )
        self.suppressed_count += 1
        return True

    def _record_failure(
        self,
        target: GenerationTarget,
        axis: str,
        state_dict: Mapping[str, Any],
        parent: WorkingRealization,
        parent_target_id: str | None,
        status: TerminalStatus,
        reason: str,
        evidence: Sequence[Mapping[str, Any]],
    ) -> None:
        """Append one failure record with the parent-derived complete key."""
        key = combine_state_key(parent.state_key, axis, state_dict)
        self.records.append(
            TargetRecord(
                target_id=target.target_id,
                axis=axis,
                ordinal=int(target.ordinal),
                state_value=FrozenDict(dict(state_dict)),
                complete_key=FrozenDict(key.to_dict()),
                status=status,
                reason=reason,
                evidence=tuple(evidence),
                parent_target_id=parent_target_id,
            )
        )
        self.failed_counts[status.value] = self.failed_counts.get(status.value, 0) + 1

    def _defer_subtree(
        self,
        level: int,
        child_path: tuple[int, ...],
        failed_id: str,
        reason: str,
        *,
        parent: WorkingRealization,
        axis: str,
        state_dict: Mapping[str, Any],
    ) -> None:
        """Account notional descendants as compressed deferred ranges."""
        remaining = self._level_counts[level + 1 :]
        if not remaining:
            return
        if self._conditional:
            # EXACT descendant leaf indices from the symbolic expansion:
            # every leaf path under the failed prefix is known without
            # expanding geometry. Compressed to ranges (exact weights).
            from confflow.science.confgen.accounting import compress_ranges

            indices = [
                position
                for position, path in enumerate(self._leaf_paths)
                if path[: len(child_path)] == child_path
            ]
            for start, end in compress_ranges(indices):
                self.records.append(
                    deferred_range_record(
                        axis="leaves",
                        start=start,
                        end=end,
                        status=TerminalStatus.DEFERRED_PARENT_FAILED,
                        reason=f"parent_failed:{reason}",
                        parent_target_id=failed_id,
                    )
                )
            self.deferred_parent_leaves += len(indices)
            return
        suffix = 1
        for count in remaining:
            suffix *= count
        prefix_flat = 0
        for depth, ordinal in enumerate(child_path):
            rest = 1
            for later in self._level_counts[depth + 1 :]:
                rest *= later
            prefix_flat += int(ordinal) * rest
        start = prefix_flat
        end = prefix_flat + suffix - 1
        self.records.append(
            deferred_range_record(
                axis="leaves",
                start=start,
                end=end,
                status=TerminalStatus.DEFERRED_PARENT_FAILED,
                reason=f"parent_failed:{reason}",
                parent_target_id=failed_id,
            )
        )
        self.deferred_parent_leaves += suffix

    def _flat_index(self, path: tuple[int, ...]) -> int:
        """Encode an ordinal path as the mixed-radix flat leaf index."""
        flat = 0
        for depth, ordinal in enumerate(path):
            rest = 1
            for later in self._level_counts[depth + 1 :]:
                rest *= later
            flat += int(ordinal) * rest
        return flat

    # -- finalization -------------------------------------------------------

    def finish(
        self,
        *,
        leaf_total: int,
        cap: int | None,
        sampling_seed: int | None,
        capped: bool,
        deferred_leaf_ranges: Sequence[tuple[int, int]],
        preflight: Any,
        preflight_exact: bool,
        preflight_basis: str,
    ) -> EngineRun:
        """Assemble leaves, compressed records, report, and certificates."""
        from dataclasses import replace as _replace

        records = list(self.records)
        deferred_sampled_out = 0
        for start, end in deferred_leaf_ranges:
            records.append(
                deferred_range_record(
                    axis="leaves",
                    start=start,
                    end=end,
                    status=TerminalStatus.DEFERRED_SAMPLED_OUT,
                    reason=(
                        f"unsampled:cap={cap}:seed={sampling_seed}"
                        if sampling_seed is not None
                        else f"unsampled:cap={cap}"
                    ),
                )
            )
            deferred_sampled_out += end - start + 1
        leaves = tuple(sorted(self.leaves, key=lambda leaf: leaf.structure.ordinal or 0))
        sampled = (leaf_total - deferred_sampled_out) if capped else leaf_total
        status_counts: dict[str, int] = {}
        for record in records:
            status_counts[record.status.value] = status_counts.get(record.status.value, 0) + 1
        categories = certificate_category_counts(records)
        counts: dict[str, Any] = {
            "raw": leaf_total,
            "policy_excluded": self.policy_excluded,
            "sampled": sampled,
            "deferred_sampled_out": deferred_sampled_out,
            "deferred_parent_failed_leaves": self.deferred_parent_leaves,
            "realized": self.realized_ok,
            "published": len(leaves),
            "failed": dict(self.failed_counts),
            "status_counts": status_counts,
        }
        counts["target_categories"] = categories["target_categories"]
        counts["unit_basis"] = {
            "target_categories": "mixed diagnostic records; no certificate equation",
            "realized": "successful stage attempts, including internal expansions",
            "published": "final leaves",
            "authoritative_sections": ["leaf_certificate", "attempt_ledger"],
        }
        counts["internal_expanded"] = categories["internal_expanded"]
        counts["anomalies"] = categories["anomalies"]
        counts["estimated_deferred_leaves"] = categories["estimated_deferred_leaves"]
        counts["equation_exact"] = categories["equation_exact"]

        # Path consistency + count equations come BEFORE the enumeration
        # dict (both sections report them).
        terminal_ok, terminal_detail = verify_terminal_equations(records)
        paths_consistent = True
        paths_detail = "unconditional product space"
        if self._conditional:
            # Every geometry-visited leaf path must belong to the symbolic
            # enumeration; completeness comes from the count equations below.
            declared = set(self._leaf_paths)
            stray = [p for p in self._visited_paths if p not in declared]
            paths_consistent = not stray
            if not paths_consistent:
                paths_detail = f"geometry visited unenumerated paths: {stray[:5]}"
            else:
                paths_detail = (
                    f"exact conditional enumeration: {len(self._leaf_paths)} "
                    "raw leaf states; geometry visited a subset"
                )
            count_ok, count_details = verify_count_equations(
                records, raw=len(self._leaf_paths), sampled=len(self._leaf_paths)
            )
            count_details["basis"] = "exact conditional count equations over symbolic leaf paths"
        else:
            count_ok, count_details = verify_count_equations(
                records, raw=leaf_total, sampled=sampled
            )

        # Enumeration certificate: symbolic layers only (no geometry).
        exclusions_declared = [
            {
                "axis": item.get("axis"),
                "match": dict(item.get("match", {})),
                "reason": item.get("reason"),
                "proof_id": (
                    (item.get("proof") or {}).get("proof_id")
                    if isinstance(item.get("proof"), Mapping)
                    else None
                ),
            }
            for item in self._context.resolved_spec.get("exclusions", []) or []
        ]
        enumeration = {
            "per_axis": {
                axis: {
                    "declared_count": estimate.declared_count,
                    "upper_bound": estimate.upper_bound,
                    "exact": estimate.exact,
                    "basis": estimate.details.get("basis"),
                    "scope_coverage": estimate.details.get("scope_coverage"),
                }
                for axis, estimate in preflight.per_axis.items()
            },
            "total_declared": preflight.total_declared,
            "total_upper_bound": preflight.total_upper_bound,
            "exact": preflight_exact,
            "basis": preflight_basis,
            "sampling": {
                "cap": cap,
                "seed": sampling_seed,
                "capped": capped,
                "sorted": True,
            },
            "sampled": sampled,
            "deferred_sampled_out": deferred_sampled_out,
            "policy_excluded": self.policy_excluded,
            "exclusions": exclusions_declared,
            "symbolic": {
                "exact": preflight_exact if not self._conditional else True,
                "raw_leaf_states": (len(self._leaf_paths) if self._conditional else leaf_total),
                "basis": (
                    "full symbolic conditional expansion (no geometry)"
                    if self._conditional
                    else "unconditional product space"
                ),
                "paths_consistent": paths_consistent,
                "paths_detail": paths_detail,
            },
            "digest": enumeration_digest(
                {
                    "per_axis": {
                        axis: (estimate.declared_count, estimate.upper_bound, estimate.exact)
                        for axis, estimate in preflight.per_axis.items()
                    },
                    "total_declared": preflight.total_declared,
                    "total_upper_bound": preflight.total_upper_bound,
                    "exact": preflight_exact,
                    "basis": preflight_basis,
                },
                {
                    "cap": cap,
                    "seed": sampling_seed,
                    "capped": capped,
                    "sampled": sampled,
                    "deferred_sampled_out": deferred_sampled_out,
                },
                self._context.resolved_spec.get("exclusions", []) or [],
            ),
        }

        # Realization certificate: per-target outcomes and equations.
        realization = {
            "attempted": count_details.get("attempted"),
            "realized": self.realized_ok,
            "published": len(leaves),
            "suppressed": self.suppressed_count,
            "realization_attempts": (count_details.get("attempted") or 0) - self.suppressed_count,
            "failed": dict(self.failed_counts),
            "status_counts": status_counts,
            "terminal_equations_ok": terminal_ok,
            "terminal_detail": terminal_detail,
            "count_equations_ok": count_ok,
            "count_details": count_details,
            "paths_consistent": paths_consistent,
            "paths_detail": paths_detail,
            "proof_contradictions": list(self.proof_contradictions),
        }
        # Final-leaf certificate (unit 1): joint leaves only. Failed or
        # rejected ancestors with deferred descendants live in the
        # attempt ledger; their descendants defer into this equation, so
        # no ancestor plus descendant ever shares one leaf equation.
        # Attempt ledger (unit 2): issued per-level stage attempts only;
        # deferred ranges were never issued at any level.
        from confflow.science.confgen.accounting import (
            attempt_ledger_counts,
            leaf_category_counts,
        )

        leaf_cats = leaf_category_counts(records)
        ledger = attempt_ledger_counts(records)
        leaf_certificate = {
            "total": leaf_total,
            "leaf_categories": leaf_cats["leaf_categories"],
            "anomalies": leaf_cats["anomalies"],
            "equations_ok": bool(count_ok) and bool(leaf_cats["equation_exact"]),
            "equation_exact": bool(leaf_cats["equation_exact"]),
            "count_details": count_details,
            "basis": (
                "final joint leaves only: published leaves, terminal leaf "
                "failures, leaf rejections, verified suppressions, and "
                "deferred ranges (counted arithmetically); internal "
                "attempts excluded"
            ),
        }
        attempt_ledger = {
            **ledger,
            "basis": (
                "issued per-level stage attempts along every explored path: "
                "geometry-successful internal expansions plus published "
                "leaves plus terminal attempt failures; policy-screened, "
                "suppressed-skipped, and deferred-unissued units excluded"
            ),
        }
        certificate = build_certificate(records, counts)
        certificate_basis = "terminal equations verify over target records"
        if not terminal_ok:
            certificate_basis = f"INVALID: terminal equations failed: {terminal_detail}"
        elif count_ok is False:
            certificate_basis = f"INVALID: count equations failed: {count_details}"
        if not paths_consistent:
            certificate = _replace(certificate, equations_ok=False)
            certificate_basis = f"INVALID: {paths_detail}"
        if self.proof_contradictions:
            certificate = _replace(certificate, equations_ok=False)
            certificate_basis = (
                f"INVALID: {len(self.proof_contradictions)} proof contradiction(s): "
                "a realized-via-drift state matches a PROVEN_INFEASIBLE exclusion"
            )
        elif not terminal_ok or count_ok is False:
            certificate = _replace(certificate, equations_ok=False)
        breaking = sorted(
            [edge.a, edge.b] for edge in self._context.graph.edges if edge.type.value == "BREAKING"
        )
        scope = {
            "donor_configuration": (
                dict(self._context.resolved_spec.get("coordination") or {})
                if self._context.resolved_spec.get("coordination") is not None
                else None
            ),
            "preserved_axes": self._preserved_axes(),
            "excluded_axes": [
                {
                    "axis": item.get("axis"),
                    "match": dict(item.get("match", {})),
                    "reason": item.get("reason"),
                }
                for item in self._context.resolved_spec.get("exclusions", []) or []
            ],
            "breaking_pairs": breaking,
            "unsupported": [],
        }
        report = {
            "schema_version": 3,
            "axis_order": list(AXIS_ORDER),
            "levels": list(self._axes),
            "counts": counts,
            "enumeration": enumeration,
            "realization": realization,
            "drift_events": list(self.drift_events),
            "scope": scope,
            "leaf_certificate": leaf_certificate,
            "attempt_ledger": attempt_ledger,
            "suppression": {
                "supported": self._suppression_disabled is None,
                "scope": (
                    "single-axis runs without sampling caps or top-level "
                    "exclusions: verified records suppress with a realized "
                    "representative; otherwise targets realize normally "
                    "(joint-tree stabilizer, sampled-representative, and "
                    "policy-excluded-representative scopes pending)"
                ),
                "disabled_reason": self._suppression_disabled,
                "suppressed": self.suppressed_count,
            },
            "inherited": {
                "entries": [
                    {
                        "axis": f"torsions.{lock.axis_id}",
                        "model": lock.model,
                        "frame": list(lock.frame),
                        "expected_absolute": float(lock.expected_absolute),
                    }
                    for lock in self._inherited
                ],
                "audited": bool(self._inherited),
                "basis": (
                    "carried torsion locks re-measured on every fresh geometry "
                    "against prior absolute frames"
                    if self._inherited
                    else "no incoming chained state"
                ),
            },
            "sampling": {
                "cap": cap,
                "seed": sampling_seed,
                "capped": capped,
                "sorted": True,
            },
            "preflight": {
                "total_declared": preflight.total_declared,
                "total_upper_bound": preflight.total_upper_bound,
                "exact": preflight_exact,
                "basis": preflight_basis,
            },
            "certificate": {
                "equations_ok": certificate.equations_ok,
                "digest": certificate.digest,
                "basis": certificate_basis,
            },
            "provenance": {
                "backend": self._engine._backend,
                "input_id": self._context.structure.id,
                "seed": self._seed,
            },
        }
        return EngineRun(
            leaves=leaves,
            target_records=tuple(records),
            report=FrozenDict(report),
            certificate=certificate,
        )

    def _preserved_axes(self) -> list[dict[str, Any]]:
        """List preserve_input axes declared in the resolved spec."""
        resolved = self._context.resolved_spec
        preserved: list[dict[str, Any]] = []
        for entry in resolved.get("torsions", []) or []:
            if isinstance(entry, Mapping) and entry.get("treatment") == "preserve_input":
                preserved.append({"axis": "torsions", "id": entry.get("id")})
        for entry in resolved.get("rings", []) or []:
            if isinstance(entry, Mapping) and entry.get("treatment") == "preserve_input":
                preserved.append({"axis": "rings", "id": entry.get("id")})
        return preserved
