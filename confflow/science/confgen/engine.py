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
from typing import TYPE_CHECKING, Any, cast

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
from confflow.science.confgen.kernel_records import (
    BoundTelemetryEvent,
    ComponentInheritedState,
    ComponentStateKey,
    InheritedScopeError,
    KernelGenerationTarget,
    KernelRun,
    KernelWorkingRealization,
    RetryResult,
    TelemetryError,
    TelemetryRow,
    as_kernel_target,
)
from confflow.science.confgen.model import (
    ConfgenStateKey,
    GenerationStage,
    MolecularContext,
    RealizationResult,
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

if TYPE_CHECKING:  # Annotation only; resolved lazily to keep model import light.
    from confflow.science.confgen.registry import ComponentRegistry

__all__ = [
    "AtomOrderViolationError",
    "EngineCancelledError",
    "EngineRun",
    "ConfgenEngine",
    "InheritedScopeError",
    "InheritedTorsionLock",  # noqa: F822 - provided by the restricted PEP 562 export
    "UnsupportedAxisError",
    "combine_state_key",
    "inherited_torsion_locks",  # noqa: F822 - provided by the restricted PEP 562 export
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


# A4d lazy compat: the three torsion inherited APIs moved to
# ``torsion.inherited``; only these three names are served lazily from
# ``engine.__getattr__`` (plus ``confgen.__init__.__getattr__``). All other
# names raise ``AttributeError`` (never ``ImportError`` masking typos), and
# normal errors (``InheritedScopeError`` etc.) stay eager (same object).
_INHERITED_LAZY_NAMES = frozenset(
    {
        "InheritedTorsionLock",
        "inherited_torsion_locks",
        "check_inherited_torsion_locks",
    }
)


def __getattr__(name: str) -> Any:
    """Serve only the three moved torsion compat names lazily (PEP 562)."""
    if name not in _INHERITED_LAZY_NAMES:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    import importlib as _il

    mod = _il.import_module("confflow.science.confgen.torsion.inherited")
    try:
        value = getattr(mod, name)
    except AttributeError:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}") from None
    globals()[name] = value
    return value


def _key_nonempty(key: ConfgenStateKey | ComponentStateKey) -> bool:
    """Return True when the incoming state key carries any entries."""
    if isinstance(key, ComponentStateKey):
        return bool(dict(key.components))
    from confflow.science.confgen.registry import resolve_registry

    payload = key.to_dict()
    for descriptor in resolve_registry(None)._ordered():
        ident = descriptor.id
        if ident not in payload:
            continue
        value = payload.get(ident)
        if str(descriptor.state_merge) == "replace":
            if value is not None:
                return True
        elif bool(value):
            return True
    return False


def thaw_snapshot(resolved: Mapping[str, Any]) -> dict[str, Any]:
    """Return a plain-data snapshot of the resolved spec for constructors."""
    from confflow.domain._immutable import FrozenDict, thaw_value

    if isinstance(resolved, FrozenDict):
        return resolved.thaw()
    snapshot: dict[str, Any] = thaw_value(dict(resolved))
    return snapshot


def combine_state_key(
    parent_key: ConfgenStateKey | ComponentStateKey,
    axis: str,
    state_value: Mapping[str, Any],
    *,
    registry: Any | None = None,
) -> ConfgenStateKey | ComponentStateKey:
    """Merge a stage-local state value into the complete labeled key.

    Legacy inputs return legacy keys with the frozen replace-vs-merge
    behavior driven by each descriptor's ``state_merge``; generic inputs
    use ``descriptor.state_merge`` (replace vs merge).
    """
    if isinstance(parent_key, ConfgenStateKey):
        from confflow.science.confgen.registry import resolve_registry

        # The legacy API ignored registry; keep its frozen v3 semantics.
        # Only ComponentStateKey below follows a caller-supplied registry.
        resolved = resolve_registry(None)
        target = None
        for descriptor in resolved.descriptors:
            if descriptor.id == axis:
                target = descriptor
                break
        if target is None:
            raise ValueError(f"unknown generation axis {axis!r}")
        payload = parent_key.to_dict()
        if target.id not in payload:
            raise ValueError(f"unknown generation axis {axis!r}")
        kwargs: dict[str, Any] = {}
        for descriptor in resolved._ordered():
            ident = descriptor.id
            if ident not in payload:
                continue
            if ident == axis:
                if str(descriptor.state_merge) == "replace":
                    kwargs[ident] = dict(state_value)
                else:
                    merged = dict(payload.get(ident) or {})
                    merged.update(dict(state_value))
                    kwargs[ident] = merged
            elif str(descriptor.state_merge) == "replace":
                kwargs[ident] = payload.get(ident)
            else:
                kwargs[ident] = dict(payload.get(ident) or {})
        return ConfgenStateKey(**kwargs)
    if not isinstance(parent_key, ComponentStateKey):
        raise ValueError("parent_key must be a ConfgenStateKey or ComponentStateKey")
    from confflow.science.confgen.registry import resolve_registry as _resolve

    resolved_registry = _resolve(registry)
    mode: str | None = None
    for descriptor in resolved_registry.descriptors:
        if descriptor.id == axis:
            mode = str(descriptor.state_merge)
            break
    if mode is None:
        raise ValueError(f"unknown generation axis {axis!r}")
    components = dict(parent_key.components)
    if mode == "replace":
        components[axis] = dict(state_value)
    else:
        existing = dict(components.get(axis, {}) or {})
        existing.update(dict(state_value))
        components[axis] = existing
    return ComponentStateKey(components=components)


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


#: Solve-failure terminal states eligible for a hooked second attempt (D0).
#: Only genuine solver-side failures qualify: FAILED_DRIFT carries lock /
#: perception audit verdicts (not a solve failure) and UNSUPPORTED carries
#: no solvable geometry contract, so neither is ever retried. Policy
#: rejections, suppressions, deferrals and publications are never retried.
_RETRYABLE_STATUSES: frozenset = frozenset(
    {
        TerminalStatus.FAILED_GEOMETRY,
        TerminalStatus.FAILED_NUMERICAL,
        TerminalStatus.UNRESOLVED,
    }
)


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


def _collect_inherited_states(
    initial_key: ComponentStateKey,
    inherited_scope: Mapping[str, Any],
    registry: Any,
) -> list[ComponentInheritedState]:
    """Collect generic inherited states (no v3 attribute reads).

    ``state_value`` slices come from ``ComponentStateKey`` only; never
    reads ``context.input_state_key`` component attributes. Reverse
    registry order reproduces the legacy legacy failure order (see ``_preserved_axes`` precedent).
    """
    comps = dict(initial_key.components) if isinstance(initial_key, ComponentStateKey) else {}
    nonempty = bool(
        comps
        and any(
            v is not None and (not isinstance(v, Mapping) or bool(dict(v))) for v in comps.values()
        )
    )
    if not nonempty:
        return []
    scope = inherited_scope
    if not isinstance(scope, Mapping) or not scope:
        raise InheritedScopeError(
            "INHERITED_STATE_SCOPE_MISSING: non-empty incoming confgen_state "
            "carries no inherited scope descriptors; chained state cannot "
            "be audited"
        )
    try:
        ordered = list(registry._ordered())
    except AttributeError:
        ordered = list(getattr(registry, "descriptors", ()) or ())
    states: list[ComponentInheritedState] = []
    for descriptor in reversed(ordered):
        cid = str(getattr(descriptor, "id", ""))
        if not cid or cid not in comps:
            continue
        sv = comps[cid]
        if sv is None:
            continue
        if isinstance(sv, Mapping) and not dict(sv):
            continue
        payload = scope.get(cid) if isinstance(scope, Mapping) else None
        states.append(ComponentInheritedState(component_id=cid, payload=payload))
    return states


def _wrap_degrees_local(angle: float) -> float:
    """Local angle wrap ((-180,180], small copy of torsion.measure)."""
    import math as _m

    wrapped = _m.fmod(float(angle) + 180.0, 360.0)
    if wrapped <= 0.0:
        wrapped += 360.0
    return wrapped - 180.0


def _inherited_report_entries(
    states: Sequence[ComponentInheritedState],
    initial_key: ComponentStateKey,
    context: Any,
) -> list[dict[str, Any]]:
    """Build legacy report entries (carrier only, byte-identical)."""
    entries: list[dict[str, Any]] = []
    try:
        comps = dict(initial_key.components)
    except (AttributeError, TypeError, ValueError):
        comps = {}
    resolved = context.resolved_spec if hasattr(context, "resolved_spec") else {}
    registry = getattr(context, "registry", None)
    descs: dict[str, Any] = {}
    try:
        for _d in getattr(registry, "descriptors", ()) or ():
            descs[str(getattr(_d, "id", ""))] = _d
    except (AttributeError, TypeError, ValueError):
        descs = {}
    for st in states:
        cid = str(getattr(st, "component_id", ""))
        if not cid:
            continue
        desc_reg = descs.get(cid)
        try:
            carries = bool(getattr(desc_reg, "carries_inherited_locks", False))
        except (AttributeError, TypeError, ValueError):
            carries = False
        if not carries:
            continue
        payload = getattr(st, "payload", {})
        if not isinstance(payload, Mapping):
            continue
        sv = comps.get(cid, {}) or {}
        if not isinstance(sv, Mapping):
            continue
        downstream: dict[str, Any] = {}
        try:
            for _e in resolved.get(cid, []) or []:
                if isinstance(_e, Mapping) and _e.get("id") is not None:
                    downstream[str(_e.get("id"))] = _e
        except (AttributeError, TypeError, KeyError, ValueError):
            downstream = {}
        for axis_id, label in dict(sv).items():
            axis = str(axis_id)
            entry = downstream.get(axis)
            if entry is not None and str(entry.get("treatment", "enumerate")) == "enumerate":
                continue
            desc = payload.get(axis)
            if not isinstance(desc, Mapping):
                continue
            model = str(desc.get("model"))
            frame = desc.get("frame")
            try:
                if model == "chemical":
                    states_map = desc.get("states") or {}
                    if entry is not None and entry.get("atoms") is not None:
                        exp = float(states_map[label])
                    else:
                        ref = desc.get("reference_frame_value")
                        exp = float(_wrap_degrees_local(float(ref) + float(states_map[label])))
                elif model == "absolute_dihedral_grid":
                    exp = float(label)
                elif model == "relative_rotation_grid":
                    ref = desc.get("reference_frame_value")
                    exp = float(_wrap_degrees_local(float(ref) + float(label)))
                else:
                    continue
            except (AttributeError, TypeError, KeyError, ValueError):
                continue
            entries.append(
                {
                    "axis": f"{cid}.{axis}",
                    "model": model,
                    "frame": list(frame) if frame is not None else None,
                    "expected_absolute": float(exp),
                }
            )
    return entries


def _validate_driving_generic(
    context: Any,
    initial_key: ComponentStateKey,
    states: Sequence[ComponentInheritedState],
    registry: Any,
) -> None:
    """Fail the chain closed when driving geometry breaks carried state.

    Scope failures propagate verbatim from component verifies; drift
    raises with the legacy L652 text (no banned axis literals).
    """
    drift_evidence: list[Mapping[str, Any]] = []
    for st in states:
        cid = str(getattr(st, "component_id", ""))
        sv = dict(initial_key.components).get(cid)
        payload = getattr(st, "payload", None)
        descriptor = None
        for d in registry.descriptors:
            if str(getattr(d, "id", "")) == cid:
                descriptor = d
                break
        hook = (
            getattr(descriptor, "verify_inherited_state", None) if descriptor is not None else None
        )
        if not callable(hook):
            continue
        result = hook(context.structure, sv, payload, context)
        ok = bool(getattr(result, "ok", False))
        ev = list(getattr(result, "evidence", ()) or ())
        if not ok:
            drift_evidence.extend([dict(e) if isinstance(e, Mapping) else e for e in ev])
    if drift_evidence:
        broken = "; ".join(
            f"{item.get('axis')} measured={item.get('measured')} expected={item.get('expected')}"
            for item in drift_evidence
            if isinstance(item, Mapping)
        )
        raise InheritedScopeError(
            "INHERITED_STATE_SCOPE_MISSING: driving geometry does not embody "
            f"inherited torsion state ({broken}); refusing to publish stale state"
        )


def _collect_component_diagnostics(
    registry: Any,
    bound: Mapping[str, Any],
    context: Any,
) -> dict[str, Any]:
    """Collect generic per-component input diagnostics (R5).

    Loops registry descriptors in order without naming components or
    importing them. Prefers a bound stage override named
    ``report_diagnostics`` when present, else the descriptor hook. Only
    non-empty mappings are kept; hook errors surface as explicit
    ``{"error": ...}`` entries (never silent omission). Fully empty
    returns ``{}`` so callers omit the report key and preserve bytes.
    """
    try:
        ordered = list(registry._ordered())
    except AttributeError:
        ordered = list(getattr(registry, "descriptors", ()) or ())
    collected: dict[str, Any] = {}
    for descriptor in ordered:
        try:
            ident = str(getattr(descriptor, "id", ""))
        except (AttributeError, TypeError, ValueError):
            continue
        if not ident:
            continue
        hook: Any = None
        try:
            stage = bound.get(ident) if isinstance(bound, Mapping) else None
        except (AttributeError, TypeError, KeyError):
            stage = None
        if stage is not None:
            try:
                cand = getattr(stage, "report_diagnostics", None)
            except AttributeError:
                cand = None
            if callable(cand):
                hook = cand
        if hook is None:
            try:
                cand = getattr(descriptor, "report_diagnostics", None)
            except AttributeError:
                cand = None
            if callable(cand):
                hook = cand
        if hook is None:
            continue
        try:
            result = hook(context)
        except Exception as exc:
            collected[ident] = {"error": f"{type(exc).__name__}: {str(exc)[:300]}"}
            continue
        if result is None:
            continue
        if not isinstance(result, Mapping):
            collected[ident] = {"error": f"bad_diagnostic_type:{type(result).__name__}"}
            continue
        if not dict(result):
            continue
        collected[ident] = dict(result)
    return collected


class ConfgenEngine:
    """Conditional DFS engine over C/R/T stage levels."""

    def __init__(
        self,
        stages: Sequence[GenerationStage] | None = None,
        *,
        allow_preserve_input: bool = False,
        backend: str = "geometric-rodrigues",
        registry: ComponentRegistry | None = None,
    ) -> None:
        from confflow.science.confgen.registry import resolve_registry

        self._explicit_stages = list(stages) if stages is not None else None
        self._allow_preserve_input = bool(allow_preserve_input)
        self._backend = str(backend)
        self._registry = resolve_registry(registry)

    def _require_same_registry(self, context: MolecularContext) -> None:
        """Fail closed unless engine and context hold the same registry object."""
        context_registry = getattr(context, "registry", None)
        if context_registry is None:
            from confflow.science.confgen.registry import default_registry

            context_registry = default_registry()
        if context_registry is not self._registry:
            raise ValueError(
                "ConfgenEngine registry mismatch: engine holds a different "
                "registry object than context.registry (must be identical)"
            )

    # -- stage registry -------------------------------------------------

    def _load_stage(self, axis: str, resolved: Mapping[str, Any]) -> GenerationStage:
        """Load the stage for one axis via the registry (fail closed when unknown).

        Constructors receive a plain-data snapshot (dicts/lists) of the
        resolved spec content, which stages must treat as read-only; the
        authoritative frozen spec stays on the context.
        """
        for descriptor in self._registry.descriptors:
            if descriptor.id == axis:
                return descriptor.factory(resolved)
        raise UnsupportedAxisError(f"unknown generation axis {axis!r}")

    def _levels(self, resolved: Mapping[str, Any]) -> list[tuple[str, GenerationStage]]:
        """Return active (axis, stage) levels in registry order."""
        return self._levels_for(self._explicit_stages, resolved)

    def _levels_for(
        self,
        explicit: Sequence[GenerationStage] | None,
        resolved: Mapping[str, Any],
    ) -> list[tuple[str, GenerationStage]]:
        """Return levels for an explicit list (or the registry when None)."""
        if explicit is not None:
            levels = [(stage.axis, stage) for stage in explicit]
            axes = [axis for axis, _ in levels]
            if sorted(axes) != sorted(set(axes)):
                raise ValueError("duplicate stage axes in explicit stage list")
            order = {descriptor.id: descriptor.order for descriptor in self._registry.descriptors}
            fallback = max(order.values(), default=-1) + 1
            levels.sort(key=lambda item: order.get(item[0], fallback))
            return levels
        return self._registry.resolve(resolved)

    # -- run --------------------------------------------------------------

    def _preserve_input_run(
        self,
        context: MolecularContext,
        initial_key: ComponentStateKey,
        inherited: Sequence[ComponentInheritedState] = (),
    ) -> KernelRun:
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
            _validate_driving_generic(context, initial_key, inherited, self._registry)
        seed = context.resolved_spec.get("seed")
        coords = np.asarray(context.structure.coordinates, dtype=float)
        if not np.all(np.isfinite(coords)):
            raise UnsupportedAxisError("preserve-input refused: input coordinates not finite")
        leaf_structure = StructureRecord(
            id=f"{context.structure.id}:v3:preserve",
            atoms=tuple(context.structure.atoms),
            coordinates=tuple(
                cast("tuple[float, float, float]", tuple(point))
                for point in context.structure.coordinates
            ),
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
        leaf = KernelWorkingRealization(
            structure=leaf_structure,
            state_key=initial_key,
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
                complete_key=FrozenDict(initial_key.to_dict()),
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
            "axis_order": list(self._registry.ids()),
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
                "entries": _inherited_report_entries(inherited, initial_key, context),
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
        _component_diags = _collect_component_diagnostics(self._registry, {}, context)
        if _component_diags:
            report["component_diagnostics"] = _component_diags
        return KernelRun(
            leaves=(leaf,),
            target_records=tuple(records),
            report=FrozenDict(report),
            certificate=certificate,
        )

    def _symbolic_paths(
        self,
        stages: Sequence[GenerationStage],
        axes: Sequence[str],
        root: KernelWorkingRealization,
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

        def rec(level: int, ghost: KernelWorkingRealization, path: tuple[int, ...]) -> None:
            for raw_target in stages[level].enumerate_targets(ghost, context):  # type: ignore[arg-type]
                target = as_kernel_target(raw_target)
                child_path = path + (int(target.ordinal),)
                if level == last:
                    paths.append(child_path)
                else:
                    ghost_child = KernelWorkingRealization(
                        structure=ghost.structure,
                        state_key=combine_state_key(
                            ghost.state_key,
                            axes[level],
                            dict(target.state_value),
                            registry=self._registry,
                        ),  # type: ignore[arg-type]
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
        self._require_same_registry(context)
        from typing import cast

        from confflow.science.confgen.wire_v3 import (
            LegacyStageAdapter,
            from_wire_key,
            is_legacy_stage,
            project_v3,
        )

        initial = from_wire_key(context.input_state_key)
        overrides: Sequence[GenerationStage] | None = None
        if self._explicit_stages is not None:
            # Old explicit v3 stages speak legacy records; adapt at the
            # wire boundary without mutating shared engine state. Generic
            # stages (StageParentProtocol/kernel records, e.g. A6 dummy)
            # pass through unwrapped.
            adapted = [
                LegacyStageAdapter(stage) if is_legacy_stage(stage) else stage
                for stage in self._explicit_stages
            ]
            overrides = tuple(adapted)
        kernel_run = self.run_kernel(
            context, initial_key=initial, should_cancel=should_cancel, stage_overrides=overrides
        )
        return cast(EngineRun, project_v3(kernel_run))

    def run_kernel(
        self,
        context: MolecularContext,
        *,
        initial_key: ComponentStateKey,
        should_cancel: Callable[[], bool] | None = None,
        stage_overrides: Sequence[GenerationStage] | None = None,
    ) -> KernelRun:
        """Run the generic kernel DFS and return generic records.

        ``stage_overrides`` is internal-only (used by :meth:`run` for the
        legacy wire adapter); public ``run_kernel`` explicit stages stay
        generic with no auto-legacy wrapping. Never mutates
        ``self._explicit_stages``.

        A4a registry identity (V24): the engine instance and ``context``
        must hold the *same* registry object (``is``); otherwise fail
        closed. Hand-built legacy contexts without a registry resolve to
        the shared immutable default.
        """
        self._require_same_registry(context)
        resolved = context.resolved_spec
        seed = resolved.get("seed")
        cap, sampling_seed = sampling_of(resolved)
        inherited = _collect_inherited_states(initial_key, context.inherited_scope, self._registry)
        if inherited:
            # The driving geometry must still embody the carried state;
            # otherwise the chain is refused before any geometry is spent.
            _validate_driving_generic(context, initial_key, inherited, self._registry)
        if stage_overrides is not None:
            levels = self._levels_for(tuple(stage_overrides), resolved)
        else:
            levels = self._levels(resolved)
        if not levels:
            return self._preserve_input_run(context, initial_key, inherited=inherited)
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
        root = KernelWorkingRealization(structure=context.structure, state_key=initial_key)
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

        def _level_targets(
            level: int, parent: KernelWorkingRealization
        ) -> Iterator[KernelGenerationTarget]:
            stage = stages[level]
            prefix = _prefix_of(parent, level, axes)
            if conditional or stage.is_conditional(context):
                # True symbolic C->R(C)->T(C,R): fresh enumeration per parent.
                for raw_target in stage.enumerate_targets(parent, context):  # type: ignore[arg-type]
                    target = as_kernel_target(raw_target)
                    if capped and target.ordinal not in allowed.get((level, prefix), set()):
                        continue
                    yield target
                return
            hook = getattr(stage, "target_by_ordinal", None)
            if hook is not None:
                # Full generation streams ordinals; sampled generation fetches
                # ONLY wanted ordinals. No full-target list is ever built.
                if capped:
                    wanted: Sequence[int] = sorted(allowed.get((level, prefix), set()))
                else:
                    wanted = range(level_counts[level])
                for ordinal in wanted:
                    yield as_kernel_target(hook(parent, int(ordinal), context))
                return
            if level not in enum_counts:
                enum_counts[level] = sum(1 for _ in stage.enumerate_targets(parent, context))  # type: ignore[arg-type]
                if enum_counts[level] != level_counts[level]:
                    raise ValueError(
                        f"stage {axes[level]!r} enumerated {enum_counts[level]} "
                        f"targets but declared {level_counts[level]}; "
                        "sampling math unsound"
                    )
            for raw_target in stage.enumerate_targets(parent, context):  # type: ignore[arg-type]
                target = as_kernel_target(raw_target)
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
            input_key=initial_key,
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


def _prefix_of(
    parent: KernelWorkingRealization | WorkingRealization,
    level: int,
    axes: Sequence[str],
) -> tuple[int, ...]:
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
        inherited: Sequence[ComponentInheritedState] = (),
        suppression_disabled: str | None = None,
        input_key: ComponentStateKey | None = None,
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
        self._context_input_key = input_key
        self._visited_paths: list[tuple[int, ...]] = []
        self.records: list[TargetRecord] = []
        self.leaves: list[KernelWorkingRealization] = []
        self.drift_events: list[dict[str, Any]] = []
        self.proof_contradictions: list[dict[str, Any]] = []
        self.policy_excluded = 0
        self.suppressed_count = 0
        self.realized_ok = 0
        self.failed_counts: dict[str, int] = {}
        self.deferred_parent_leaves = 0
        # L-D3 run-local telemetry ledger (never shared, never cached on
        # stage; discarded with the run on cancellation).
        self.telemetry: list[BoundTelemetryEvent] = []
        # L-D3 side channel: the retry solver records its own bound span
        # here synchronously at solve return (before any child recursion
        # appends descendant events), consumed immediately by the calling
        # _expand_target. Tuple (span_start, span_end, selected_idx, phase)
        # or None for legacy plain outcomes. Never crosses targets.
        self._pending_retry_selection: tuple[int, int, int | None, str] | None = None
        # F-ledger first-issued history: one entry per target issuance that
        # reached stage realization, keyed generically without component
        # knowledge. Populated at the real solve gate only (input entry
        # plus retry solver non-decline); policy/suppression gates return
        # before this point and never populate. Retry multi-start rows
        # stay in telemetry only and never expand this per-target set.
        self._issued_history: set[tuple[Any, str, int]] = set()

    # -- recursion -------------------------------------------------------

    def run_level(
        self,
        level: int,
        parent: KernelWorkingRealization,
        path: tuple[int, ...],
        ancestors: tuple[KernelWorkingRealization, ...],
        ancestor_specs: tuple[tuple[str, Mapping[str, Any], str | None], ...],
        parent_target_id: str | None,
        level_targets: Callable[[int, KernelWorkingRealization], Iterator[KernelGenerationTarget]],
    ) -> None:
        """Expand one DFS level under *parent*."""
        stage = self._stages[level]
        axis = self._axes[level]
        last = level == len(self._stages) - 1
        exclusions = self._context.resolved_spec.get("exclusions", []) or []
        if self._supports_retry(stage):
            # Batch path: materialize this parent's targets once (hook path
            # only), run the verbatim first pass, then the retry pass over
            # solve-failure records. Ghost enumeration never runs geometry;
            # gates run per target in both passes via _expand_target. The
            # first-pass table is a per-parent local (never shared cached
            # state); the primary record index per target lets the retry
            # pass replace stale terminals at their exact position.
            from confflow.science.confgen.model import RetryFirstPass

            pending = list(level_targets(level, parent))
            table: list[RetryFirstPass] = []
            primary: dict[int, Any] = {}
            for target in pending:
                if self._should_cancel is not None and self._should_cancel():
                    raise EngineCancelledError("confgen run cancelled by probe")
                ordinal = int(target.ordinal)
                pre = len(self.records)
                published = self._expand_target(
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
                record = self._primary_after(pre, parent_target_id, axis, ordinal, target.target_id)
                primary[ordinal] = record
                table.append(
                    RetryFirstPass(
                        target_id=str(target.target_id),
                        ordinal=ordinal,
                        status=record.status.value,
                        reason=str(record.reason),
                        solver_error=self._is_solver_error(record),
                        structure=published,
                    )
                )
            self._retry_level(
                level,
                stage,
                axis,
                last,
                parent,
                path,
                ancestors,
                ancestor_specs,
                parent_target_id,
                pending,
                tuple(table),
                primary,
                exclusions,
                level_targets,
            )
            return
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
        parent: KernelWorkingRealization,
        path: tuple[int, ...],
        ancestors: tuple[KernelWorkingRealization, ...],
        ancestor_specs: tuple[tuple[str, Mapping[str, Any], str | None], ...],
        parent_target_id: str | None,
        target: KernelGenerationTarget,
        exclusions: Sequence[Mapping[str, Any]],
        level_targets: Callable[[int, KernelWorkingRealization], Iterator[KernelGenerationTarget]],
        solver: Callable[[Any, Any, Any], Any] | None = None,
    ) -> Any:
        """Realize, audit, and account one target.

        Returns the published structure on success (for the retry hook's
        first-pass table) and ``None`` otherwise.
        """
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
                    combine_state_key(
                        parent.state_key, axis, state_dict, registry=self._engine._registry
                    ).to_dict()
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
        tele_mark = len(self.telemetry)
        own_mark_idx: int | None = None
        own_mark_phase: str | None = None
        try:
            if solver is not None:
                outcome = solver(parent, target, context)
                if outcome is not None:
                    self._issued_history.add((parent_target_id, axis, int(target.ordinal)))
                pending = self._pending_retry_selection
                self._pending_retry_selection = None
                if pending is not None:
                    span_start, span_end, selected, span_phase = pending
                    if span_end == span_start:
                        # Empty wrapper payload: fall back to one generic
                        # legacy event on success (declined None keeps none).
                        if outcome is not None:
                            self._append_legacy_telemetry(
                                component_id=axis,
                                parent_target_id=parent_target_id,
                                target_id=str(target.target_id),
                                ordinal=int(target.ordinal),
                                phase="retry",
                                kind="legacy_retry",
                                solve_success=self._geometry_success(outcome),
                                accepted=False,
                            )
                            own_mark_idx = len(self.telemetry) - 1
                            own_mark_phase = "retry"
                    elif outcome is not None and self._geometry_success(outcome):
                        if selected is None:
                            raise TelemetryError(
                                "retry telemetry has no selected successful row "
                                f"for {str(target.target_id)!r}"
                            )
                        own_mark_idx = selected
                        own_mark_phase = span_phase
                elif outcome is not None and len(self.telemetry) == tele_mark:
                    # Legacy plain-outcome retry entry: no component
                    # payload, so the engine synthesizes one generic event.
                    # Declined (None) appends nothing here.
                    self._append_legacy_telemetry(
                        component_id=axis,
                        parent_target_id=parent_target_id,
                        target_id=str(target.target_id),
                        ordinal=int(target.ordinal),
                        phase="retry",
                        kind="legacy_retry",
                        solve_success=self._geometry_success(outcome),
                        accepted=False,
                    )
                    own_mark_idx = len(self.telemetry) - 1
                    own_mark_phase = "retry"
            else:
                # Input gate: count the attempt at actual call entry, so a
                # throwing stage still leaves attempt=1. Policy/suppression
                # gates above return before this point (attempt 0).
                self._issued_history.add((parent_target_id, axis, int(target.ordinal)))
                self._append_legacy_telemetry(
                    component_id=axis,
                    parent_target_id=parent_target_id,
                    target_id=str(target.target_id),
                    ordinal=int(target.ordinal),
                    phase="input",
                    kind="legacy_input",
                    solve_success=False,
                    accepted=False,
                )
                own_mark_idx = len(self.telemetry) - 1
                own_mark_phase = "input"
                outcome = stage.realize(parent, target, context)
                self._update_telemetry_success(own_mark_idx, self._geometry_success(outcome))
        except Exception as exc:  # stage bug: account explicitly, never silent
            if isinstance(exc, TelemetryError):
                raise  # malformed telemetry propagates visibly, never stage_error
            if isinstance(exc, EngineCancelledError) and solver is not None:
                raise  # retry path only: the probe propagates, never a record
            # Legacy path unchanged: even EngineCancelledError from a stage
            # is recorded as stage_error, exactly as before D0. The input
            # placeholder above already retains attempt=1 success=0.
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
        if solver is not None and outcome is None:
            return  # hook declined the retry; the failure record stands
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
                    combine_state_key(
                        parent.state_key, axis, state_dict, registry=self._engine._registry
                    ).to_dict()
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
            check_bond_integrity=self._check_bond_integrity(axis, stage),
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

        # Carried (inherited) states: re-measured on every fresh
        # geometry against the prior absolute frames. Any drift blocks
        # publication with the stale parent key (FAILED_DRIFT, never a
        # silent carry).
        if self._inherited:
            inherited_evidence: list[dict[str, Any]] = []
            inherited_ok = True
            _reg = self._engine._registry
            _descs = {}
            try:
                for _d in getattr(_reg, "descriptors", ()) or ():
                    _descs[str(getattr(_d, "id", ""))] = _d
            except (AttributeError, TypeError, ValueError):
                _descs = {}
            # Reverse registry order preserves legacy torsions-first drift order.
            try:
                _order = list(_reg._ordered())  # type: ignore[union-attr]
            except AttributeError:
                _order = []
            _by_id = {str(getattr(d, "id", "")): d for d in _order}
            for _st in list(self._inherited):
                _cid = str(getattr(_st, "component_id", ""))
                _desc = _descs.get(_cid) or _by_id.get(_cid)
                _hook = (
                    getattr(_desc, "verify_inherited_state", None) if _desc is not None else None
                )
                if not callable(_hook):
                    continue
                try:
                    _key = self._context_input_key
                    _parent_comps = (
                        dict(_key.components)  # type: ignore[union-attr]
                        if _key is not None
                        else {}
                    )
                except (AttributeError, TypeError, ValueError):
                    _parent_comps = {}
                _sv = _parent_comps.get(_cid)
                _pay = getattr(_st, "payload", None)
                _res = _hook(structure, _sv, _pay, context)
                if not bool(getattr(_res, "ok", False)):
                    inherited_ok = False
                    for _e in list(getattr(_res, "evidence", ()) or ()):
                        inherited_evidence.append(dict(_e) if isinstance(_e, Mapping) else _e)
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
        if self._carries_inherited_locks(axis, stage):
            # Carried axes retain their verified prior discrete identity:
            # re-snapping a relative label against the new input reference
            # would let a new-reference 0 masquerade as the old label.
            # Generic overwrite for carried (non-enumerate) axes only;
            # enumerate axes keep their fresh target values.
            try:
                _incoming = dict(parent.state_key.components).get(axis, {}) or {}
            except (AttributeError, TypeError, KeyError, ValueError):
                _incoming = {}
            if isinstance(_incoming, Mapping):
                _resolved = self._context.resolved_spec
                _down = {}
                try:
                    for _e in _resolved.get(axis, []) or []:
                        if isinstance(_e, Mapping) and _e.get("id") is not None:
                            _down[str(_e.get("id"))] = _e
                except (AttributeError, TypeError, KeyError, ValueError):
                    _down = {}
                for _k, _v in dict(_incoming).items():
                    _ent = _down.get(str(_k))
                    if _ent is not None and str(_ent.get("treatment", "enumerate")) == "enumerate":
                        continue
                    merged_state[str(_k)] = _v
        child_key = combine_state_key(
            parent.state_key, axis, merged_state, registry=self._engine._registry
        )
        locked_contribution = merged_state
        locked = tuple(
            a for a in self._engine._registry.ids() if a in (set(parent.locked_axes) | {axis})
        )
        child_provenance = {
            "seed": self._seed,
            "backend": self._engine._backend,
            "path_ordinals": list(child_path),
            "parent_target_id": parent_target_id,
            "measured": dict(measured),
        }
        child = KernelWorkingRealization(
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
            leaf = KernelWorkingRealization(
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
        # L-D3: attribute publication to the own successful start only.
        # The own ledger index was captured at solve return, before child
        # recursion appended descendant events, so marking it can never
        # attach acceptance to another target/component. Geometry success
        # was fixed at the solve gate; accepted is set here only for the
        # selected row (attempts>0 and solve_successes>0 re-validated).
        # Failed candidates keep False. Failure paths above already
        # returned without marking.
        try:
            if own_mark_idx is not None and own_mark_phase is not None:
                self._mark_telemetry_accepted(
                    own_mark_idx,
                    component_id=axis,
                    parent_target_id=parent_target_id,
                    target_id=str(target.target_id),
                    ordinal=int(target.ordinal),
                    phase=own_mark_phase,
                )
        except (TelemetryError, EngineCancelledError):
            raise
        except Exception as exc:
            raise TelemetryError(f"telemetry accept marking failed: {exc}") from exc
        return structure

    # -- helpers ----------------------------------------------------------

    def _stage_for(self, axis: str) -> GenerationStage:
        """Return the stage bound to one axis."""
        for name, stage in zip(self._axes, self._stages):
            if name == axis:
                return stage
        raise ValueError(f"no stage bound for axis {axis!r}")

    def _descriptor_for(self, axis: str) -> Any | None:
        """Return the registry descriptor for *axis*, if any (generic)."""
        for descriptor in self._engine._registry.descriptors:
            if descriptor.id == axis:
                return descriptor
        return None

    def _check_bond_integrity(self, axis: str, stage: GenerationStage) -> bool:
        """Read the bond-integrity hook generically (FIX-1A A3)."""
        from confflow.science.confgen.model import GenerationStage as _Base

        overridden = any(
            "check_bond_integrity" in klass.__dict__
            for klass in type(stage).__mro__
            if klass not in (_Base, object)
        )
        if overridden:
            try:
                return bool(getattr(stage, "check_bond_integrity", False))
            except (AttributeError, TypeError, ValueError):
                return False
        descriptor = self._descriptor_for(axis)
        if descriptor is not None:
            try:
                return bool(descriptor.check_bond_integrity)
            except (AttributeError, TypeError, ValueError):
                pass
        try:
            return bool(getattr(stage, "check_bond_integrity", False))
        except (AttributeError, TypeError, ValueError):
            return False

    def _supports_retry(self, stage: GenerationStage) -> bool:
        """Report whether the stage overrides the optional retry hook (D0/D0.2).

        Generic MRO probe (same shape as the A3 hook readers); the kernel
        never compares axis strings and never imports components. No
        descriptor fallback: a retry must be an explicit stage override.
        Duck stages implementing ``retry_solve`` explicitly without
        inheriting the base remain supported via a callable fallback that
        ignores the base default implementation.
        """
        from confflow.science.confgen.model import GenerationStage as _Base

        try:
            for klass in type(stage).__mro__:
                if klass in (_Base, object):
                    continue
                slots = klass.__dict__
                if (
                    "retry_solve" in slots
                    or "retry_solve_phase" in slots
                    or "retry_phases" in slots
                ):
                    return True
        except Exception:
            pass
        try:
            base_legacy = getattr(_Base, "retry_solve", None)
            base_phase = getattr(_Base, "retry_solve_phase", None)
            for name, base_fn in (("retry_solve", base_legacy), ("retry_solve_phase", base_phase)):
                try:
                    candidate = getattr(stage, name, None)
                except Exception:
                    continue
                if not callable(candidate):
                    continue
                if getattr(candidate, "__func__", None) is base_fn:
                    continue
                if candidate is base_fn:
                    continue
                return True
        except Exception:
            pass
        return False

    def _retry_phase_ids(self, stage: GenerationStage) -> tuple[str, ...]:
        """Normalize the stage-owned phase declaration (D0.2, fail closed).

        ``None`` means the single generic default phase. Otherwise the
        declaration must be a non-empty tuple/list of non-empty unique
        strings in execution order; anything else raises fail-closed.
        No global or stage-instance run cache is read or written here.
        Stages hiding the new API (duck stages without inheritance) fall
        back to the single generic default phase; transparent proxies
        forwarding the declaration are honored.
        """
        from confflow.science.confgen.model import GenerationStage as _Base

        try:
            overridden = any(
                "retry_phases" in klass.__dict__
                for klass in type(stage).__mro__
                if klass not in (_Base, object)
            )
        except Exception:
            overridden = False
        declared: Any = None
        has_declaration = False
        if overridden:
            declared = stage.retry_phases()
            has_declaration = True
        else:
            try:
                candidate = getattr(stage, "retry_phases", None)
            except Exception:
                candidate = None
            if candidate is None:
                from confflow.science.confgen.model import RETRY_DEFAULT_PHASE

                return (RETRY_DEFAULT_PHASE,)
            base_fn = getattr(_Base, "retry_phases", None)
            if candidate is base_fn or getattr(candidate, "__func__", None) is base_fn:
                from confflow.science.confgen.model import RETRY_DEFAULT_PHASE

                return (RETRY_DEFAULT_PHASE,)
            if not callable(candidate):
                from confflow.science.confgen.model import RETRY_DEFAULT_PHASE

                return (RETRY_DEFAULT_PHASE,)
            declared = candidate()
            has_declaration = True
        if not has_declaration or declared is None:
            from confflow.science.confgen.model import RETRY_DEFAULT_PHASE

            return (RETRY_DEFAULT_PHASE,)
        if not isinstance(declared, (tuple, list)):
            raise ValueError("retry phase declaration must be a tuple/list of ids or None")
        ids = [str(item) for item in list(declared)]
        if not ids:
            raise ValueError("retry phase declaration must hold at least one id")
        for item in list(declared):
            if not isinstance(item, str) or not item:
                raise ValueError("retry phase ids must be non-empty strings")
        if len(set(ids)) != len(ids):
            raise ValueError("retry phase ids must be unique")
        return tuple(ids)

    @staticmethod
    def _is_solver_error(record: Any) -> bool:
        """Report whether a failure came from a stage exception.

        Exception-origin failures (``stage_error`` evidence) are code bugs,
        never science-retryable; solver-returned failures carry no such
        marker. Checked generically from record evidence.
        """
        try:
            evidence = tuple(record.evidence or ())
        except Exception:
            return False
        return any(
            isinstance(item, Mapping) and item.get("kind") == "stage_error" for item in evidence
        )

    @staticmethod
    def _geometry_success(outcome: Any) -> bool:
        """Return True for solver geometry success (realized + structure)."""
        try:
            return bool(
                getattr(outcome, "status", None) == "realized"
                and getattr(outcome, "structure", None) is not None
            )
        except Exception:
            return False

    def _telemetry_snapshot(self) -> tuple[BoundTelemetryEvent, ...]:
        """Return the run-local ledger as a read-only frozen snapshot."""
        return tuple(self.telemetry)

    def _append_legacy_telemetry(
        self,
        *,
        component_id: str,
        parent_target_id: str | None,
        target_id: str,
        ordinal: int,
        phase: str,
        kind: str,
        solve_success: bool,
        accepted: bool,
    ) -> None:
        """Append one engine-synthesized legacy event (L-D3).

        Used for real solve entries without component payload: input
        entries and legacy plain-outcome retries. ``solve_success`` is
        geometry success; ``accepted`` is the audited publication flag
        (separate columns, never merged).
        """
        self.telemetry.append(
            BoundTelemetryEvent(
                component_id=str(component_id),
                parent_target_id=parent_target_id,
                target_id=str(target_id),
                ordinal=int(ordinal),
                phase=str(phase),
                kind=str(kind),
                attempts=1,
                solve_successes=1 if solve_success else 0,
                accepted=bool(accepted),
                diagnostic={},
            )
        )

    def _bind_retry_rows(
        self,
        rows: tuple[TelemetryRow, ...],
        *,
        component_id: str,
        parent_target_id: str | None,
        target_id: str,
        ordinal: int,
        phase: str,
    ) -> int:
        """Validate component rows and append bound events (accepted=False).

        Returns the number of appended events. Identity is bound by the
        engine; row ``kind``/``attempts``/``solve_successes``/``diagnostic``
        pass through opaquely (kernel never interprets them). Malformed
        rows raise :class:`TelemetryError`.
        """
        if not isinstance(rows, (tuple, list)):
            raise TelemetryError("retry telemetry must be a tuple of TelemetryRow")
        count = 0
        for entry in list(rows):
            if not isinstance(entry, TelemetryRow):
                raise TelemetryError(
                    "retry telemetry entries must be TelemetryRow, " f"got {type(entry).__name__}"
                )
            self.telemetry.append(
                BoundTelemetryEvent(
                    component_id=str(component_id),
                    parent_target_id=parent_target_id,
                    target_id=str(target_id),
                    ordinal=int(ordinal),
                    phase=str(phase),
                    kind=str(entry.kind),
                    attempts=int(entry.attempts),
                    solve_successes=int(entry.solve_successes),
                    accepted=False,
                    diagnostic=dict(entry.diagnostic),
                )
            )
            count += 1
        return count

    def _update_telemetry_success(self, index: int, solve_success: bool) -> None:
        """Update the geometry-success column of one own ledger event (L-D3).

        Used for input entries counted at actual call entry (before the
        call returns or throws); only the success flag is updated, the
        attempt itself is never removed.
        """
        current = self.telemetry[index]
        from dataclasses import replace as _replace_event

        self.telemetry[index] = _replace_event(current, solve_successes=1 if solve_success else 0)

    def _mark_telemetry_accepted(
        self,
        index: int,
        *,
        component_id: str,
        parent_target_id: str | None,
        target_id: str,
        ordinal: int,
        phase: str,
    ) -> None:
        """Attribute publication to one own selected event (L-D3).

        Marks exactly the ledger event at ``index`` (captured at solve
        return, before child recursion). The event identity is
        re-validated against the engine-bound caller identity, and the
        event must hold ``attempts > 0`` and ``solve_successes > 0``
        (accepted implies a real geometry success; trailing zero-attempt
        diagnostic rows are never selectable). Anything else raises
        :class:`TelemetryError`. Never infers from the global last
        event, which may belong to a descendant target/component.
        """
        current = self.telemetry[index]
        if (
            str(current.component_id) != str(component_id)
            or current.parent_target_id != parent_target_id
            or str(current.target_id) != str(target_id)
            or int(current.ordinal) != int(ordinal)
            or str(current.phase) != str(phase)
        ):
            raise TelemetryError(
                "telemetry accept target mismatch: ledger identity drifted "
                f"for {str(target_id)!r}"
            )
        if int(current.attempts) <= 0 or int(current.solve_successes) <= 0:
            raise TelemetryError(
                "telemetry accept requires attempts > 0 and solve_successes > 0, "
                f"got attempts={int(current.attempts)} "
                f"solve_successes={int(current.solve_successes)}"
            )
        if bool(current.accepted):
            raise TelemetryError("telemetry event already accepted")
        from dataclasses import replace as _replace_event

        self.telemetry[index] = _replace_event(current, accepted=True)

    def _component_statistics_section(self) -> dict[str, Any] | None:
        """Build the additive ``component_statistics`` section (L-D3).

        Groups the run-local ledger by engine-bound component id and
        calls each bound stage's ``report_statistics`` hook with its
        read-only slice. Only non-``None`` non-empty mapping fragments
        are kept; the section is omitted entirely when empty so golden
        bytes are unchanged. Never overwrites existing scope fields and
        never lets the caller choose the component id.
        """
        grouped: dict[str, list[BoundTelemetryEvent]] = {}
        for event in self.telemetry:
            try:
                ident = str(event.component_id)
            except Exception:
                continue
            grouped.setdefault(ident, []).append(event)
        bound = dict(zip(self._axes, self._stages))
        collected: dict[str, Any] = {}
        for axis in self._axes:
            stage = bound.get(axis)
            if stage is None:
                continue
            hook = self._hook_impl(axis, stage, "report_statistics")
            if hook is None:
                continue
            snapshot = tuple(grouped.get(str(axis), ()))
            try:
                result = hook(snapshot)
            except TelemetryError:
                raise
            except EngineCancelledError:
                raise
            except Exception as exc:
                raise TelemetryError(
                    f"report_statistics for {str(axis)!r} failed: "
                    f"{type(exc).__name__}: {str(exc)[:200]}"
                ) from exc
            if result is None:
                continue
            if not isinstance(result, Mapping):
                raise TelemetryError(
                    f"report_statistics for {str(axis)!r} must return a mapping or None"
                )
            plain = dict(result)
            if not plain:
                continue
            collected[str(axis)] = plain
        if not collected:
            return None
        return collected

    def _primary_after(
        self,
        pre: int,
        parent_target_id: str | None,
        axis: str,
        ordinal: int,
        target_id: str,
    ) -> Any:
        """Locate the primary record one expansion call appended.

        Scans only the call's own append window: children carry a deeper
        axis, so the first triple match is this target's terminal. Loud on
        absence (internal inconsistency, never silent skip).
        """
        for record in self.records[pre:]:
            if (
                record.parent_target_id == parent_target_id
                and record.axis == axis
                and int(record.ordinal) == int(ordinal)
            ):
                return record
        raise EngineConsistencyError(
            f"no primary record for target {target_id!r}; expansion accounting broken"
        )

    def _record_index(self, record: Any) -> int:
        """Return the list index of a known record object."""
        for index, candidate in enumerate(self.records):
            if candidate is record:
                return index
        raise EngineConsistencyError("primary record lost before retry pass")

    def _supersede_failure(self, old_idx: int, target_id: str) -> None:
        """Revoke one stale failure and its deferred subtree (D0).

        Removes the failure record at ``old_idx`` plus the maximal run of
        this target's deferred ranges immediately following it (append
        adjacency is guaranteed: nothing interleaves inside one expansion
        call), then reconciles ``failed_counts`` (key dropped at zero, as
        if first-try) and ``deferred_parent_leaves`` (by removed range
        weights). Counters use their declared types directly; any negative
        or missing count raises loudly instead of being masked. Published,
        suppressed and policy records are never touched. Diagnostic lists
        (``drift_events``/``proof_contradictions``) are intentionally NOT
        purged here: they retain real historical attempt diagnostics,
        including observations made before a later retryable failure.
        A target_id-keyed purge could delete another parent's diagnostics.
        """
        # old_idx comes from a live identity scan with append-only traffic
        # since; an IndexError here is a loud internal bug, never masked.
        stale = self.records[old_idx]
        if stale.status not in _RETRYABLE_STATUSES:
            raise EngineConsistencyError(
                f"refusing to supersede non-retryable {stale.status.value} for {target_id!r}"
            )
        kill = [old_idx]
        cursor = old_idx + 1
        while cursor < len(self.records):
            candidate = self.records[cursor]
            if (
                candidate.axis == "leaves"
                and candidate.parent_target_id == target_id
                and candidate.status is TerminalStatus.DEFERRED_PARENT_FAILED
            ):
                kill.append(cursor)
                cursor += 1
            else:
                break
        removed = [self.records[index] for index in kill]
        for index in sorted(kill, reverse=True):
            del self.records[index]
        status = stale.status.value
        have = self.failed_counts.get(status, 0)
        if have <= 0:
            raise EngineConsistencyError(
                f"failed_counts has no {status} to revoke for {target_id!r}"
            )
        if have == 1:
            del self.failed_counts[status]
        else:
            self.failed_counts[status] = have - 1
        drop = 0
        for record in removed:
            state = dict(record.state_value)
            if "range_start" in state and "range_end" in state:
                drop += int(state["range_end"]) - int(state["range_start"]) + 1
        if drop:
            rest = self.deferred_parent_leaves - drop
            if rest < 0:
                raise EngineConsistencyError(
                    f"deferred_parent_leaves would go negative revoking {drop} for {target_id!r}"
                )
            self.deferred_parent_leaves = rest

    def _retry_level(
        self,
        level: int,
        stage: GenerationStage,
        axis: str,
        last: bool,
        parent: KernelWorkingRealization,
        path: tuple[int, ...],
        ancestors: tuple[KernelWorkingRealization, ...],
        ancestor_specs: tuple[tuple[str, Mapping[str, Any], str | None], ...],
        parent_target_id: str | None,
        pending: Sequence[KernelGenerationTarget],
        first_pass: tuple[Any, ...],
        primary: Mapping[int, Any],
        exclusions: Sequence[Mapping[str, Any]],
        level_targets: Callable[[int, KernelWorkingRealization], Iterator[KernelGenerationTarget]],
    ) -> None:
        """Retry pass for batch stages across generic phases (D0/D0.2).

        D0 single-phase behavior is preserved exactly: one generic phase
        delegating to the legacy hook yields identical records/reports.
        D0.2 runs stage-owned phases in declaration order. ``first_pass``
        stays permanently immutable; each phase freezes its own snapshot
        from current terminals at phase start (same snapshot for every
        target inside one phase; the next phase snapshot includes prior
        phase accepted structures). Each phase revisits only currently
        retryable terminals (never stage errors, drift, policy,
        suppression, deferral, or publications) through
        :meth:`_expand_target`, so gates run again and cancellations
        propagate; a retried outcome replaces the stale failure at its
        exact position (deferred subtree revoked, counters reconciled) and
        the live primary reference is updated to the current object, so
        later phases relocate by identity without cross-parent confusion.
        Accepted structures are captured from the audited expansion
        return, never from unaudited solver output. No enumeration happens
        here; no state crosses parents or runs.
        """
        from confflow.science.confgen.model import RETRY_DEFAULT_PHASE, RetryFirstPass

        try:
            phase_candidate = getattr(stage, "retry_solve_phase", None)
        except Exception:
            phase_candidate = None
        phase_hook = phase_candidate if callable(phase_candidate) else None
        try:
            legacy_candidate = getattr(stage, "retry_solve", None)
        except Exception:
            legacy_candidate = None
        legacy_hook = legacy_candidate if callable(legacy_candidate) else None
        probe = self._should_cancel
        retryable_values = {status.value for status in _RETRYABLE_STATUSES}
        phase_ids = self._retry_phase_ids(stage)
        if phase_hook is None and legacy_hook is not None:
            for declared_id in phase_ids:
                if declared_id != RETRY_DEFAULT_PHASE:
                    raise ValueError(
                        f"retry phase {str(declared_id)!r} has no phase-aware hook; "
                        "refusing to reuse the legacy hook"
                    )
        status_of: dict[int, str] = {}
        reason_of: dict[int, str] = {}
        error_of: dict[int, bool] = {}
        struct_of: dict[int, Any] = {}
        for entry in first_pass:
            ordinal_key = int(entry.ordinal)
            status_of[ordinal_key] = str(entry.status)
            reason_of[ordinal_key] = str(entry.reason)
            error_of[ordinal_key] = bool(entry.solver_error)
            struct_of[ordinal_key] = entry.structure
        live: dict[int, Any] = dict(primary)
        ordered = list(pending)
        for phase_id in phase_ids:
            snapshot = tuple(
                RetryFirstPass(
                    target_id=str(item.target_id),
                    ordinal=int(item.ordinal),
                    status=status_of[int(item.ordinal)],
                    reason=reason_of[int(item.ordinal)],
                    solver_error=error_of[int(item.ordinal)],
                    structure=struct_of[int(item.ordinal)],
                )
                for item in ordered
            )
            for target in ordered:
                ordinal = int(target.ordinal)
                if status_of[ordinal] not in retryable_values:
                    continue
                if error_of[ordinal]:
                    continue
                if probe is not None and probe():
                    raise EngineCancelledError("confgen run cancelled by probe")
                stale = live[ordinal]
                mark = len(self.records)

                def _solver(
                    solve_parent: Any,
                    solve_target: Any,
                    solve_context: Any,
                    _phase_hook: Any = phase_hook,
                    _legacy: Any = legacy_hook,
                    _probe: Any = probe,
                    _table: Any = first_pass,
                    _phase: Any = phase_id,
                    _snap: Any = snapshot,
                    _axis: Any = axis,
                    _parent_tid: Any = parent_target_id,
                ) -> Any:
                    self._pending_retry_selection = None
                    if _phase == RETRY_DEFAULT_PHASE:
                        if _phase_hook is not None:
                            raw = _phase_hook(
                                solve_parent,
                                solve_target,
                                solve_context,
                                _probe,
                                _table,
                                _phase,
                                _snap,
                            )
                        elif _legacy is not None:
                            raw = _legacy(solve_parent, solve_target, solve_context, _probe, _table)
                        else:
                            return None
                    else:
                        if _phase_hook is not None:
                            raw = _phase_hook(
                                solve_parent,
                                solve_target,
                                solve_context,
                                _probe,
                                _table,
                                _phase,
                                _snap,
                            )
                        elif _legacy is not None:
                            raise ValueError(
                                f"retry phase {str(_phase)!r} has no phase-aware hook; "
                                "refusing to reuse the legacy hook"
                            )
                        else:
                            return None
                    # L-D3: unwrap only the exact frozen wrapper; legacy
                    # plain outcomes and None keep their semantics. Any
                    # other type (including arbitrary tuples) fails
                    # closed visibly via TelemetryError (never stage_error).
                    # The own bound span and the selected successful row are
                    # recorded synchronously here (before child recursion),
                    # never inferred from the global last event later.
                    if raw is None:
                        return None
                    if isinstance(raw, RetryResult):
                        try:
                            tid = str(solve_target.target_id)
                            ord_val = int(solve_target.ordinal)
                        except Exception as exc:
                            raise TelemetryError(
                                "retry telemetry target identity unreadable: "
                                f"{type(exc).__name__}"
                            ) from exc
                        span_start = len(self.telemetry)
                        self._bind_retry_rows(
                            tuple(raw.telemetry),
                            component_id=str(_axis),
                            parent_target_id=_parent_tid,
                            target_id=tid,
                            ordinal=ord_val,
                            phase=str(_phase),
                        )
                        span_end = len(self.telemetry)
                        out = raw.outcome
                        if out is None:
                            self._pending_retry_selection = (
                                span_start,
                                span_end,
                                None,
                                str(_phase),
                            )
                            return None
                        if not isinstance(out, RealizationResult):
                            raise TelemetryError(
                                "RetryResult outcome must be RealizationResult or None, "
                                f"got {type(out).__name__}"
                            )
                        if not self._geometry_success(out):
                            # Failure outcome: rows kept as failure starts;
                            # no successful start exists to select.
                            if raw.success_index is not None:
                                raise TelemetryError(
                                    "RetryResult success_index requires "
                                    "a geometry-success outcome"
                                )
                            self._pending_retry_selection = (
                                span_start,
                                span_end,
                                None,
                                str(_phase),
                            )
                            return out
                        selected: int | None = None
                        if raw.success_index is not None:
                            selected = span_start + int(raw.success_index)
                        else:
                            candidates = [
                                idx
                                for idx in range(span_start, span_end)
                                if int(self.telemetry[idx].attempts) > 0
                                and int(self.telemetry[idx].solve_successes) > 0
                            ]
                            if len(candidates) != 1:
                                raise TelemetryError(
                                    "retry telemetry holds "
                                    f"{len(candidates)} successful rows for a "
                                    "successful outcome; set success_index explicitly"
                                )
                            selected = candidates[0]
                        self._pending_retry_selection = (
                            span_start,
                            span_end,
                            selected,
                            str(_phase),
                        )
                        return out
                    if isinstance(raw, RealizationResult):
                        return raw
                    raise TelemetryError(
                        "retry hook returned unsupported type "
                        f"{type(raw).__name__}; refusing to guess"
                    )

                accepted = self._expand_target(
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
                    solver=_solver,
                )
                tail = list(self.records[mark:])
                target_id = str(target.target_id)
                prim = [
                    record
                    for record in tail
                    if record.parent_target_id == parent_target_id
                    and record.axis == axis
                    and int(record.ordinal) == ordinal
                ]
                if not prim:
                    continue  # hook declined: the failure record stands, single terminal
                old_idx = self._record_index(stale)
                self._supersede_failure(old_idx, target_id)
                tail_ids = {id(record) for record in tail}
                block_ids = {id(record) for record in prim}
                block_ids.update(
                    id(record)
                    for record in tail
                    if record.axis == "leaves"
                    and record.parent_target_id == target_id
                    and record.status is TerminalStatus.DEFERRED_PARENT_FAILED
                )
                block = [record for record in tail if id(record) in block_ids]
                rest = [record for record in tail if id(record) not in block_ids]
                head = [record for record in self.records if id(record) not in tail_ids]
                self.records[:] = head[:old_idx] + block + head[old_idx:] + rest
                current = prim[0]
                live[ordinal] = current
                status_of[ordinal] = str(current.status.value)
                reason_of[ordinal] = str(current.reason)
                error_of[ordinal] = bool(self._is_solver_error(current))
                struct_of[ordinal] = accepted

    def _carries_inherited_locks(self, axis: str, stage: GenerationStage) -> bool:
        """Read the inherited-lock carrier hook generically (FIX-1A A3)."""
        from confflow.science.confgen.model import GenerationStage as _Base

        overridden = any(
            "carries_inherited_locks" in klass.__dict__
            for klass in type(stage).__mro__
            if klass not in (_Base, object)
        )
        if overridden:
            try:
                return bool(getattr(stage, "carries_inherited_locks", False))
            except (AttributeError, TypeError, ValueError):
                return False
        descriptor = self._descriptor_for(axis)
        if descriptor is not None:
            try:
                return bool(descriptor.carries_inherited_locks)
            except (AttributeError, TypeError, ValueError):
                pass
        try:
            return bool(getattr(stage, "carries_inherited_locks", False))
        except (AttributeError, TypeError, ValueError):
            return False

    def _hook_impl(self, axis: str, stage: GenerationStage, name: str) -> Any | None:
        """Return the bound component hook for *name*, if overridden.

        Prefers the stage implementation when the stage type actually
        overrides the ``GenerationStage`` default (real stages, the
        legacy wire adapter, and test doubles); otherwise falls back to
        the registry descriptor (legacy explicit stages inheriting the
        base defaults). Returns ``None`` when neither provides one. The
        kernel never compares axis strings or imports components.
        """
        from confflow.science.confgen.model import GenerationStage as _Base

        overridden = any(
            name in klass.__dict__ for klass in type(stage).__mro__ if klass not in (_Base, object)
        )
        if overridden:
            try:
                hook = getattr(stage, name, None)
            except AttributeError:
                hook = None
            if callable(hook):
                return hook
        descriptor = self._descriptor_for(axis)
        if descriptor is not None:
            try:
                hook = getattr(descriptor, name, None)
            except AttributeError:
                hook = None
            if callable(hook):
                return hook
        if not overridden:
            try:
                hook = getattr(stage, name, None)
            except AttributeError:
                hook = None
            if callable(hook):
                return hook
        return None

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
                        input_coords=tuple(
                            cast("tuple[float, float, float]", tuple(float(v) for v in row))
                            for row in parent_coords
                        ),
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

        Component-owned fallback first (stage ``fallback_lock`` hook, else
        the registry descriptor fallback for legacy explicit stages);
        otherwise exact discrete comparison (sound for discrete
        vocabularies; torsion owns its hook). The kernel never compares
        axis strings or imports components here.
        """
        # Component-owned fallback (FIX-1A A3). The ring component's
        # tolerance-aware matcher lives in the ring scope module and is
        # reached via the stage hook or the descriptor fallback; it is
        # never replaced by the generic comparison below (which would
        # wrongly publish this UNRESOLVED leaf).
        try:
            stage_hook = getattr(stage, "fallback_lock", None)
        except AttributeError:
            stage_hook = None
        if callable(stage_hook):
            try:
                owned = stage_hook(dict(locked_state), structure, context)
            except Exception as exc:
                return (
                    "ambiguous",
                    [self._anomaly(f"lock fallback raised {type(exc).__name__}")],
                    {},
                    "unknown",
                )
            if owned is not None:
                return cast("tuple[str, list[dict[str, Any]], dict[str, Any], Any]", owned)
        descriptor = self._descriptor_for(axis)
        descriptor_hook = None
        if descriptor is not None:
            try:
                descriptor_hook = getattr(descriptor, "fallback_lock", None)
            except AttributeError:
                descriptor_hook = None
        if callable(descriptor_hook):
            try:
                resolved = descriptor_hook(stage, dict(locked_state), structure, context)
            except Exception as exc:
                return (
                    "ambiguous",
                    [self._anomaly(f"lock fallback raised {type(exc).__name__}")],
                    {},
                    "unknown",
                )
            if resolved is not None:
                return cast("tuple[str, list[dict[str, Any]], dict[str, Any], Any]", resolved)
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
        target: KernelGenerationTarget,
        axis: str,
        state_dict: Mapping[str, Any],
        parent: KernelWorkingRealization,
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
        parent: KernelWorkingRealization,
        child_path: tuple[int, ...],
        parent_target_id: str | None,
        target: KernelGenerationTarget,
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
        suppressed_key = combine_state_key(
            parent.state_key, axis, state_dict, registry=self._engine._registry
        )
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
        target: KernelGenerationTarget,
        axis: str,
        state_dict: Mapping[str, Any],
        parent: KernelWorkingRealization,
        parent_target_id: str | None,
        status: TerminalStatus,
        reason: str,
        evidence: Sequence[Mapping[str, Any]],
    ) -> None:
        """Append one failure record with the parent-derived complete key."""
        key = combine_state_key(parent.state_key, axis, state_dict, registry=self._engine._registry)
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
        parent: KernelWorkingRealization,
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
    ) -> KernelRun:
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
        # F-ledger: target-level ledger consumes the first-issued history
        # so suppressed-after-issue counts as issued, never as skipped;
        # leaf/count equations below stay over final terminals only.
        from confflow.science.confgen.accounting import (
            attempt_ledger_counts,
            leaf_category_counts,
        )

        ledger = attempt_ledger_counts(records, issued_history=self._issued_history)
        realization = {
            "attempted": count_details.get("attempted"),
            "realized": self.realized_ok,
            "published": len(leaves),
            "suppressed": self.suppressed_count,
            "realization_attempts": (count_details.get("attempted") or 0)
            - int(ledger.get("suppressed_skipped", self.suppressed_count)),
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
        leaf_cats = leaf_category_counts(records)
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
            "donor_configuration": self._donor_configuration(),
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
        # L-D3 additive statistics: read-only snapshot grouped per
        # component; the new key appears only when at least one bound
        # component hook returns a non-empty mapping. Default hooks
        # return None, so all existing goldens stay byte-identical.
        # Never overwrites existing scope fields.
        component_stats = self._component_statistics_section()
        if component_stats is not None:
            if "component_statistics" in scope:
                raise EngineConsistencyError(
                    "scope already carries component_statistics; refusing overwrite"
                )
            scope["component_statistics"] = component_stats
        report = {
            "schema_version": 3,
            "axis_order": list(self._engine._registry.ids()),
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
                "entries": _inherited_report_entries(
                    self._inherited, self._context_input_key, self._context
                ),
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
        _component_diags = _collect_component_diagnostics(
            self._engine._registry, dict(zip(self._axes, self._stages)), self._context
        )
        if _component_diags:
            report["component_diagnostics"] = _component_diags
        return KernelRun(
            leaves=leaves,
            target_records=tuple(records),
            report=FrozenDict(report),
            certificate=certificate,
        )

    def _preserved_axes(self) -> list[dict[str, Any]]:
        """List preserve_input axes via component hooks (FIX-1A A3).

        Each component's ``preserved_entries`` hook owns its axis string
        and entry shape; the engine only concatenates. Reverse registry
        order reproduces the legacy torsions-then-rings byte order,
        including axes present in the resolved spec but without a bound
        stage (e.g. explicit stage lists and ``preserve_input`` levels).
        """
        resolved = self._context.resolved_spec
        bound = dict(zip(self._axes, self._stages))
        preserved: list[dict[str, Any]] = []
        for descriptor in reversed(list(self._engine._registry.descriptors)):
            axis = descriptor.id
            if axis in bound:
                hook = self._hook_impl(axis, bound[axis], "preserved_entries")
            else:
                try:
                    hook = getattr(descriptor, "preserved_entries", None)
                except AttributeError:
                    hook = None
                if not callable(hook):
                    continue
            if hook is None:
                continue
            try:
                entries = hook(resolved)
            except Exception:
                continue
            if entries:
                preserved.extend(dict(item) for item in entries)
        return preserved

    def _donor_configuration(self) -> dict[str, Any] | None:
        """Return the coordination report fragment via hooks (FIX-1A A3).

        Bound stages first, then unbound registry descriptors (so explicit
        stage lists and ``preserve_input`` levels report the same
        ``donor_configuration`` as the resolved spec); only coordination
        contributes the key.
        """
        resolved = self._context.resolved_spec
        bound = dict(zip(self._axes, self._stages))
        ordered_axes = list(bound) + [
            descriptor.id
            for descriptor in self._engine._registry.descriptors
            if descriptor.id not in bound
        ]
        for axis in ordered_axes:
            if axis in bound:
                hook = self._hook_impl(axis, bound[axis], "report_section")
            else:
                descriptor = self._descriptor_for(axis)
                try:
                    hook = getattr(descriptor, "report_section", None)
                except AttributeError:
                    hook = None
                if not callable(hook):
                    continue
            if hook is None:
                continue
            try:
                section = hook(resolved)
            except Exception:
                continue
            if isinstance(section, Mapping) and "donor_configuration" in section:
                donor = section["donor_configuration"]
                return dict(donor) if isinstance(donor, Mapping) else donor
        return None
