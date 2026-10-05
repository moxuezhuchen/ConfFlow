#!/usr/bin/env python3

"""Ring component descriptor (moved from the engine dispatch, A1).

The ``factory`` body preserves the ``engine._load_stage`` rings branch
verbatim (snapshot handling, lazy import with fail-closed
``UnsupportedAxisError``, original message); ``is_active`` preserves
the ``engine._levels`` rings predicate.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from confflow.science.confgen.kernel_records import InheritedScopeError, VerificationResult
from confflow.science.confgen.registry import ComponentDescriptor

__all__ = ["descriptor"]


def _fallback_lock(
    stage: Any,
    locked_state: Mapping[str, Any],
    structure: Any,
    context: Any,
) -> Any:
    """Descriptor fallback: ring tolerance-aware matcher (owns behavior)."""
    from confflow.science.confgen.ring.scope import fallback_lock as _impl

    return _impl(stage, locked_state, structure, context)


def _preserved_entries(resolved: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Descriptor fallback: ring ``preserve_input`` entries."""
    from confflow.science.confgen.ring.scope import preserved_entries as _impl

    return _impl(resolved)


def _report_section(resolved: Mapping[str, Any]) -> dict[str, Any] | None:
    """Descriptor fallback: ring contributes no report section."""
    from confflow.science.confgen.ring.scope import report_section as _impl

    return _impl(resolved)


def _describe_scope(resolved: Mapping[str, Any]) -> Any:
    """Descriptor fallback: ring scope slice."""
    from confflow.science.confgen.ring.scope import describe_scope as _impl

    return _impl(resolved)


def _serialize_inherited_state(
    resolved: Mapping[str, Any], state_value: Any, context: Any
) -> Mapping[str, Any]:
    """Build the public rings scope payload (byte-identical to legacy)."""
    ring_entries = {
        str(entry.get("id")): entry
        for entry in (resolved.get("rings", []) or [])
        if isinstance(entry, Mapping)
    }
    out: dict[str, Any] = {}
    for ring_id, label in dict(state_value or {}).items():
        out[str(ring_id)] = {
            "atoms": [int(a) for a in (ring_entries.get(str(ring_id), {}).get("atoms") or [])],
            "label": (dict(label) if isinstance(label, Mapping) else label),
        }
    return out


def _verify_inherited_state(
    structure: Any, state_value: Any, payload: Any, context: Any
) -> VerificationResult:
    """Verify the carried rings slice (scope completeness only)."""
    rings_value = dict(state_value or {}) if isinstance(state_value, Mapping) else {}
    if not rings_value:
        return VerificationResult(ok=True, evidence=())
    if not isinstance(payload, Mapping):
        raise InheritedScopeError(
            "INHERITED_STATE_SCOPE_MISSING: inherited scope has no ring section"
        )
    resolved = context.resolved_spec
    downstream_rings = {
        str(entry.get("id"))
        for entry in (resolved.get("rings", []) or [])
        if isinstance(entry, Mapping)
    }
    for ring_id in rings_value:
        if str(ring_id) in downstream_rings:
            continue
        if not isinstance(payload.get(str(ring_id)), Mapping):
            raise InheritedScopeError(
                f"INHERITED_STATE_SCOPE_MISSING: no scope descriptor for rings.{ring_id}"
            )
        raise InheritedScopeError(
            f"INHERITED_STATE_SCOPE_MISSING: inherited rings.{ring_id} is "
            "lane-owned state with no active downstream ring stage; "
            "re-enumerate it (carried lane audit hooks pending)"
        )
    return VerificationResult(ok=True, evidence=())


def descriptor() -> ComponentDescriptor:
    """Return the rings component descriptor."""
    from confflow.science.confgen.ring.spec import normalize_spec as _normalize_spec
    from confflow.science.confgen.ring.spec import validate_context as _validate_context

    def is_active(resolved: Mapping[str, Any]) -> bool:
        rings = resolved.get("rings", [])
        if isinstance(rings, (list, tuple)) and len(rings) > 0:
            return True
        return False

    def factory(resolved: Mapping[str, Any]) -> Any:
        from confflow.science.confgen.engine import UnsupportedAxisError, thaw_snapshot

        snapshot = thaw_snapshot(resolved)
        try:
            from confflow.science.confgen.ring.stage import RingStage
        except ImportError as exc:
            raise UnsupportedAxisError(
                "rings requested but the ring stage module is unavailable"
            ) from exc
        return RingStage(snapshot)

    return ComponentDescriptor(
        id="rings",
        order=20,
        spec_keys=("rings",),
        state_merge="merge",
        is_active=is_active,
        factory=factory,
        check_bond_integrity=False,
        carries_inherited_locks=False,
        fallback_lock=_fallback_lock,
        preserved_entries=_preserved_entries,
        report_section=_report_section,
        describe_scope=_describe_scope,
        normalize_spec=_normalize_spec,
        validate_context=_validate_context,
        serialize_inherited_state=_serialize_inherited_state,
        verify_inherited_state=_verify_inherited_state,
    )
