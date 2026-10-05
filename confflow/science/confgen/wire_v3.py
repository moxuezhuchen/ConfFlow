#!/usr/bin/env python3

"""v3 wire adapter (FIX-1A A2).

Boundary conversions between generic kernel records and the frozen v3
types. Only this module (plus ``ConfgenEngine.run`` method body and the
``ConfgenStateKey`` class body) may mention the three v3 axis literals
or read ``.coordination``/``.rings``/``.torsions`` attributes.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from confflow.domain._immutable import FrozenDict
from confflow.science.confgen.kernel_records import (
    ComponentStateKey,
    KernelGenerationTarget,
    KernelRun,
    KernelWorkingRealization,
    as_kernel_target,
)
from confflow.science.confgen.model import (
    ConfgenStateKey,
    GenerationStage,
    GenerationTarget,
    WorkingRealization,
)
from confflow.science.confgen.wire_v3_constants import V3_AXIS_ORDER

__all__ = [
    "LegacyStageAdapter",
    "UnsupportedWireComponent",
    "assemble_inherited_scope",
    "from_wire_key",
    "is_legacy_stage",
    "project_v3",
    "to_legacy_realization",
    "to_legacy_target",
    "to_wire_key",
]


class UnsupportedWireComponent(ValueError):
    """Fail-closed error for components outside the v3 wire format."""


def from_wire_key(key: ConfgenStateKey) -> ComponentStateKey:
    """Convert a v3 key into its generic form (drops empty entries)."""
    if not isinstance(key, ConfgenStateKey):
        raise ValueError("from_wire_key expects a ConfgenStateKey")
    components: dict[str, Any] = {}
    if key.coordination is not None:
        components["coordination"] = key.coordination
    if dict(key.rings):
        components["rings"] = dict(key.rings)
    if dict(key.torsions):
        components["torsions"] = dict(key.torsions)
    return ComponentStateKey(components=components)


def to_wire_key(key: ComponentStateKey) -> ConfgenStateKey:
    """Convert a generic key back to v3 (fail-closed on unknown ids)."""
    if not isinstance(key, ComponentStateKey):
        raise ValueError("to_wire_key expects a ComponentStateKey")
    raw = dict(key.components)
    for component_id in raw:
        if component_id not in V3_AXIS_ORDER:
            raise UnsupportedWireComponent(
                f"unsupported wire component {component_id!r}; v3 wire only carries {V3_AXIS_ORDER}"
            )
    return ConfgenStateKey(
        coordination=raw.get("coordination"),
        rings=dict(raw.get("rings", {}) or {}),
        torsions=dict(raw.get("torsions", {}) or {}),
    )


def assemble_inherited_scope(per_component: Mapping[str, Any]) -> dict[str, Any]:
    """Assemble the legacy v3 inherited-scope mapping (byte-identical).

    ``per_component`` maps component id to its public JSON payload as
    returned by ``serialize_inherited_state``. Only the three v3 sections
    travel on the wire, in legacy order (torsions, rings, coordination);
    unknown component ids are ignored here (the v3 key conversion already
    fails closed for them). Missing sections default to the legacy
    empties (``{}``/``{}``/``None``) so scope bytes never drift.
    """
    data = dict(per_component or {})
    torsions = data.get("torsions", {})
    rings = data.get("rings", {})
    coordination = data.get("coordination", None)
    if torsions is None:
        torsions = {}
    if rings is None:
        rings = {}
    return {
        "torsions": dict(torsions) if isinstance(torsions, Mapping) else torsions,
        "rings": dict(rings) if isinstance(rings, Mapping) else rings,
        "coordination": (dict(coordination) if isinstance(coordination, Mapping) else coordination),
    }


def to_legacy_realization(r: KernelWorkingRealization) -> WorkingRealization:
    """Project one generic leaf back to the frozen v3 realization type."""
    if not isinstance(r, KernelWorkingRealization):
        raise ValueError("to_legacy_realization expects a KernelWorkingRealization")
    return WorkingRealization(
        structure=r.structure,
        state_key=to_wire_key(r.state_key),
        parent_realization_id=r.parent_realization_id,
        generation_axis=r.generation_axis,
        locked_axes=tuple(r.locked_axes),
        provenance=dict(r.provenance),
    )


def to_legacy_target(t: KernelGenerationTarget) -> GenerationTarget:
    """Convert a generic target back to the frozen v3 target type."""
    if isinstance(t, GenerationTarget):
        return t
    if not isinstance(t, KernelGenerationTarget):
        raise ValueError("to_legacy_target expects a KernelGenerationTarget")
    return GenerationTarget(
        axis=str(t.axis),
        target_id=str(t.target_id),
        state_value=dict(t.state_value),
        ordinal=int(t.ordinal),
        provenance=dict(t.provenance),
    )


def is_legacy_stage(stage: Any) -> bool:
    """Return True when *stage* speaks legacy v3 records (boundary check).

    Primary rule (no annotation guessing): an explicit ``run()`` stage
    whose ``axis`` belongs to the frozen ``V3_AXIS_ORDER`` is legacy and
    is adapted by :class:`LegacyStageAdapter`. Python API never required
    annotations, so cleared ``__annotations__`` must not change routing.
    Other axes keep the original annotation compat check (generic dummy
    stays generic; unknown axes fail closed downstream). Public
    ``run_kernel`` never auto-wraps.
    """
    try:
        axis = stage.axis if not isinstance(stage, type) else None
        if callable(axis):
            axis = None
        if isinstance(axis, str) and axis in V3_AXIS_ORDER:
            return True
    except Exception:
        pass
    import inspect

    for method_name in ("estimate", "enumerate_targets", "realize"):
        hook = getattr(stage, method_name, None)
        if hook is None:
            continue
        try:
            hints = inspect.get_annotations(hook)
        except Exception:
            hints = getattr(hook, "__annotations__", {}) or {}
        for hint in hints.values():
            text = hint if isinstance(hint, str) else getattr(hint, "__name__", str(hint))
            if "WorkingRealization" in str(text) and "Kernel" not in str(text):
                return True
            if str(text) == "GenerationTarget":
                return True
        # Fallback: raw annotation strings may be stringified unions.
        try:
            source = str(inspect.signature(hook))
        except Exception:
            continue
        if "WorkingRealization" in source and "Kernel" not in source:
            return True
    return False


def _generic_from_payload(payload: Mapping[str, Any]) -> ComponentStateKey:
    """Rebuild a generic key from a stored ``complete_key`` mapping."""
    if not isinstance(payload, Mapping):
        raise ValueError("complete_key payload must be a mapping")
    if "components" in payload:
        return ComponentStateKey.from_dict(payload)
    raise UnsupportedWireComponent(
        "complete_key payload is not a generic ComponentStateKey mapping"
    )


def _project_evidence(evidence: tuple[Mapping[str, Any], ...]) -> tuple[FrozenDict, ...]:
    """Project evidence ``parent_key`` entries from generic to v3 form."""
    projected: list[FrozenDict] = []
    for item in evidence:
        plain = dict(item)
        if "parent_key" in plain and isinstance(plain["parent_key"], Mapping):
            payload = dict(plain["parent_key"])
            # Only generic payloads (with "components") need conversion;
            # v3 payloads pass through untouched.
            if "components" in payload:
                generic = _generic_from_payload(plain["parent_key"])
                plain["parent_key"] = to_wire_key(generic).to_dict()
        projected.append(FrozenDict(plain))
    return tuple(projected)


def project_v3(kernel_run: KernelRun) -> Any:
    """Project a generic run back to the v3 :class:`EngineRun` (data only)."""
    from confflow.science.confgen.engine import EngineRun

    if not isinstance(kernel_run, KernelRun):
        raise ValueError("project_v3 expects a KernelRun")
    leaves = tuple(to_legacy_realization(leaf) for leaf in kernel_run.leaves)
    records = []
    for record in kernel_run.target_records:
        complete = record.complete_key
        if complete is not None:
            generic = _generic_from_payload(complete)
            complete_v3 = FrozenDict(to_wire_key(generic).to_dict())
        else:
            complete_v3 = complete
        evidence = _project_evidence(tuple(record.evidence))
        records.append(
            type(record)(
                target_id=record.target_id,
                axis=record.axis,
                ordinal=int(record.ordinal),
                state_value=FrozenDict(dict(record.state_value)),
                complete_key=complete_v3,
                status=record.status,
                reason=record.reason,
                evidence=evidence,
                parent_target_id=record.parent_target_id,
            )
        )
    # Report: only key-derived sections are re-projected; everything else
    # passes through byte-identical. Generic runs only store v3-shaped
    # report dicts (engine builds the same report for v3 ids), so the
    # report itself needs no key conversion beyond leaves/records/evidence
    # above. Keep an explicit shallow copy to avoid aliasing.
    report = dict(
        kernel_run.report.thaw()
        if isinstance(kernel_run.report, FrozenDict)
        else dict(kernel_run.report)
    )
    return EngineRun(
        leaves=leaves,
        target_records=tuple(records),
        report=FrozenDict(report),
        certificate=kernel_run.certificate,
    )


def _as_legacy_parent(parent: Any) -> WorkingRealization:
    """Convert a kernel parent to the frozen v3 realization (boundary only)."""
    if isinstance(parent, WorkingRealization):
        return parent
    if isinstance(parent, KernelWorkingRealization):
        return to_legacy_realization(parent)
    raise ValueError("legacy adapter expects a KernelWorkingRealization parent")


def _as_legacy_target(target: Any) -> GenerationTarget:
    """Convert a kernel target to the frozen v3 target (boundary only)."""
    if isinstance(target, GenerationTarget) and not isinstance(target, KernelGenerationTarget):
        return target
    return to_legacy_target(target)


class LegacyStageAdapter(GenerationStage):
    """Wrap one old explicit v3 stage for the generic kernel (wire boundary).

    Only ``ConfgenEngine.run`` builds this adapter for old explicit
    ``self._explicit_stages``. Default registry stages and public
    ``run_kernel`` explicit stages stay generic (no auto-legacy). The
    adapter converts kernel records to legacy records before delegating
    and converts legacy targets back via ``as_kernel_target``. Optional
    hooks (``verify_locked``/``audit_target``/``suppression_for_target``/
    ``target_by_ordinal``/``preserved_state``) exist on the adapter only
    when the wrapped stage defines them, preserving dynamic-hook
    detection; wrappers keep signatures via ``functools.wraps``.
    """

    #: A3 dispatch bools (plain class defaults so hook detection and mypy
    #: see writeable attributes; per-instance values are set in __init__).
    check_bond_integrity: bool = False
    carries_inherited_locks: bool = False

    def __init__(self, legacy: GenerationStage) -> None:
        object.__setattr__(self, "_legacy", legacy)
        try:
            object.__setattr__(self, "lock_reference", legacy.lock_reference)
        except AttributeError:
            pass
        # A3 hooks as plain instance attributes (not properties: the base
        # declares them as writeable class attributes). Wrapped value when
        # the wrapped type overrides it, else the v3 torsions rule.
        object.__setattr__(
            self, "check_bond_integrity", self._v3_bool(legacy, "check_bond_integrity")
        )
        object.__setattr__(
            self, "carries_inherited_locks", self._v3_bool(legacy, "carries_inherited_locks")
        )

    @staticmethod
    def _v3_bool(legacy: Any, name: str) -> bool:
        """Return the wrapped hook value, else whether axis is torsions."""
        from confflow.science.confgen.model import GenerationStage as _Base

        try:
            overridden = any(
                name in klass.__dict__
                for klass in type(legacy).__mro__
                if klass not in (_Base, object)
            )
        except Exception:
            overridden = False
        if overridden:
            try:
                return bool(getattr(legacy, name, False))
            except Exception:
                return False
        try:
            return bool(str(legacy.axis) == "torsions")
        except Exception:
            return False

    @staticmethod
    def _wrapped_overrides(legacy: Any, name: str) -> bool:
        """Return True when the wrapped stage type overrides a hook."""
        from confflow.science.confgen.model import GenerationStage as _Base

        try:
            return any(
                name in klass.__dict__
                for klass in type(legacy).__mro__
                if klass not in (_Base, object)
            )
        except Exception:
            return False

    def preserved_entries(self, resolved: Any) -> Any:
        """A3 hook: wrapped entries when overridden, else component slice."""
        legacy = object.__getattribute__(self, "_legacy")
        if self._wrapped_overrides(legacy, "preserved_entries"):
            return legacy.preserved_entries(resolved)
        try:
            axis = str(legacy.axis)
        except Exception:
            return []
        if axis == "torsions":
            from confflow.science.confgen.torsion.scope import preserved_entries as _impl

            return _impl(resolved)
        if axis == "rings":
            from confflow.science.confgen.ring.scope import preserved_entries as _impl

            return _impl(resolved)
        return []

    def report_section(self, resolved: Any) -> Any:
        """A3 hook: wrapped fragment when overridden, else component slice."""
        legacy = object.__getattribute__(self, "_legacy")
        if self._wrapped_overrides(legacy, "report_section"):
            return legacy.report_section(resolved)
        try:
            axis = str(legacy.axis)
        except Exception:
            return None
        if axis == "coordination":
            from confflow.science.confgen.coordination.scope import report_section as _impl

            return _impl(resolved)
        return None

    def describe_scope(self, resolved: Any) -> Any:
        """A3 hook: wrapped slice when overridden, else component slice."""
        legacy = object.__getattribute__(self, "_legacy")
        if self._wrapped_overrides(legacy, "describe_scope"):
            return legacy.describe_scope(resolved)
        try:
            axis = str(legacy.axis)
        except Exception:
            return None
        if axis == "coordination":
            from confflow.science.confgen.coordination.scope import describe_scope as _impl

            return _impl(resolved)
        if axis == "rings":
            from confflow.science.confgen.ring.scope import describe_scope as _impl

            return _impl(resolved)
        if axis == "torsions":
            from confflow.science.confgen.torsion.scope import describe_scope as _impl

            return _impl(resolved)
        return None

    def fallback_lock(self, locked_state: Any, structure: Any, context: Any) -> Any:
        """A3 hook: wrapped fallback when overridden, else ring matcher."""
        legacy = object.__getattribute__(self, "_legacy")
        if self._wrapped_overrides(legacy, "fallback_lock"):
            return legacy.fallback_lock(locked_state, structure, context)
        try:
            axis = str(legacy.axis)
        except Exception:
            return None
        if axis == "rings":
            from confflow.science.confgen.ring.scope import fallback_lock as _impl

            return _impl(self, locked_state, structure, context)
        return None

    @property
    def axis(self) -> str:
        """Return the wrapped stage axis."""
        return str(self._legacy.axis)

    def is_conditional(self, context: Any) -> bool:
        """Forward conditionality from the wrapped stage."""
        return bool(self._legacy.is_conditional(context))

    def axis_ids(self, context: Any) -> tuple[str, ...] | None:
        """Forward axis ids from the wrapped stage."""
        ids = self._legacy.axis_ids(context)
        return None if ids is None else tuple(str(v) for v in ids)

    def estimate(self, parent: Any, context: Any) -> Any:
        """Estimate via the wrapped stage (parent converted to legacy)."""
        return self._legacy.estimate(_as_legacy_parent(parent), context)

    def enumerate_targets(self, parent: Any, context: Any) -> Any:
        """Enumerate via the wrapped stage (outputs converted to kernel)."""
        for legacy_target in self._legacy.enumerate_targets(_as_legacy_parent(parent), context):
            yield as_kernel_target(legacy_target)

    def realize(self, parent: Any, target: Any, context: Any) -> Any:
        """Realize via the wrapped stage (records converted at boundary)."""
        return self._legacy.realize(_as_legacy_parent(parent), _as_legacy_target(target), context)

    def perceive(self, structure: Any, context: Any) -> Any:
        """Forward perception (structure-only, no conversion)."""
        return self._legacy.perceive(structure, context)

    def __getattr__(self, name: str) -> Any:
        """Forward optional hooks only when the wrapped stage defines them."""
        import functools

        legacy = object.__getattribute__(self, "_legacy")
        if name in (
            "audit_target",
            "suppression_for_target",
            "target_by_ordinal",
            "preserved_state",
            "verify_locked",
        ):
            try:
                hook = getattr(legacy, name)
            except AttributeError:
                raise AttributeError(name) from None
            if name == "target_by_ordinal":

                @functools.wraps(hook)
                def _by_ordinal(parent: Any, ordinal: int, context: Any) -> Any:
                    return as_kernel_target(hook(_as_legacy_parent(parent), int(ordinal), context))

                return _by_ordinal
            if name == "audit_target":

                @functools.wraps(hook)
                def _audit(structure: Any, target: Any, parent: Any, context: Any) -> Any:
                    return hook(
                        structure,
                        _as_legacy_target(target),
                        _as_legacy_parent(parent),
                        context,
                    )

                return _audit
            if name == "suppression_for_target":

                @functools.wraps(hook)
                def _suppression(parent: Any, target: Any, context: Any) -> Any:
                    return hook(_as_legacy_parent(parent), _as_legacy_target(target), context)

                return _suppression
            # verify_locked / preserved_state take no parent/target records.
            return hook
        # Any other dynamic attribute forwards transparently; missing
        # attributes still raise AttributeError (no false hook detection).
        return getattr(legacy, name)
