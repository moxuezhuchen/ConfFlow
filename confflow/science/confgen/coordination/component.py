#!/usr/bin/env python3

"""Coordination component descriptor (moved from the engine dispatch, A1).

The ``factory`` body preserves the ``engine._load_stage`` coordination
branch verbatim (snapshot handling, lazy import, the three fail-closed
layers around ``adapt_to_core``, original messages); ``is_active``
preserves the ``engine._levels`` coordination predicate including the
``treatment != "preserve_input"`` gate.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from confflow.science.confgen.kernel_records import InheritedScopeError, VerificationResult
from confflow.science.confgen.registry import ComponentDescriptor

__all__ = ["descriptor"]


def _preserved_entries(resolved: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Descriptor fallback: coordination contributes no preserved entries."""
    from confflow.science.confgen.coordination.scope import preserved_entries as _impl

    return _impl(resolved)


def _report_section(resolved: Mapping[str, Any]) -> dict[str, Any] | None:
    """Descriptor fallback: coordination ``donor_configuration`` fragment."""
    from confflow.science.confgen.coordination.scope import report_section as _impl

    return _impl(resolved)


def _describe_scope(resolved: Mapping[str, Any]) -> Any:
    """Descriptor fallback: coordination scope slice."""
    from confflow.science.confgen.coordination.scope import describe_scope as _impl

    return _impl(resolved)


def _serialize_inherited_state(resolved: Mapping[str, Any], state_value: Any, context: Any) -> Any:
    """Build the public coordination scope payload (byte-identical)."""
    coordination = resolved.get("coordination")
    coord_scope: Any = None
    if isinstance(coordination, Mapping):
        coord_scope = {
            "metal_center": int(coordination.get("metal_center", -1)),
            "donor_atoms": sorted(
                int(a)
                for site in (coordination.get("binding_sites", []) or [])
                if isinstance(site, Mapping)
                for a in (site.get("atoms", []) or [])
            ),
            "shapes": coordination.get("shapes", "auto"),
        }
    if state_value is None:
        return None
    scope: Any = {} if coord_scope is None else dict(coord_scope)
    scope["label"] = dict(state_value) if isinstance(state_value, Mapping) else state_value
    return scope


def _verify_inherited_state(
    structure: Any, state_value: Any, payload: Any, context: Any
) -> VerificationResult:
    """Verify the carried coordination slice (scope completeness only)."""
    if state_value is None:
        return VerificationResult(ok=True, evidence=())
    resolved = context.resolved_spec
    coordination = resolved.get("coordination")
    active = (
        isinstance(coordination, Mapping)
        and coordination.get("treatment", "enumerate") != "preserve_input"
    )
    if active:
        return VerificationResult(ok=True, evidence=())
    if not isinstance(payload, Mapping):
        raise InheritedScopeError(
            "INHERITED_STATE_SCOPE_MISSING: no scope descriptor for coordination"
        )
    raise InheritedScopeError(
        "INHERITED_STATE_SCOPE_MISSING: inherited coordination is "
        "lane-owned state with no active downstream coordination "
        "stage; re-enumerate it (carried lane audit hooks pending)"
    )


def descriptor() -> ComponentDescriptor:
    """Return the coordination component descriptor."""
    from confflow.science.confgen.coordination.spec import (
        contribute_topology as _contribute_topology,
    )
    from confflow.science.confgen.coordination.spec import (
        normalize_spec as _normalize_spec,
    )
    from confflow.science.confgen.coordination.spec import (
        validate_context as _validate_context,
    )

    def is_active(resolved: Mapping[str, Any]) -> bool:
        coordination = resolved.get("coordination")
        if isinstance(coordination, Mapping) and coordination is not None:
            if coordination.get("treatment", "enumerate") != "preserve_input":
                return True
        return False

    def factory(resolved: Mapping[str, Any]) -> Any:
        from confflow.science.confgen.engine import UnsupportedAxisError, thaw_snapshot

        snapshot = thaw_snapshot(resolved)
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

    return ComponentDescriptor(
        id="coordination",
        order=10,
        spec_keys=("coordination",),
        state_merge="replace",
        is_active=is_active,
        factory=factory,
        check_bond_integrity=False,
        carries_inherited_locks=False,
        fallback_lock=None,
        preserved_entries=_preserved_entries,
        report_section=_report_section,
        describe_scope=_describe_scope,
        normalize_spec=_normalize_spec,
        validate_context=_validate_context,
        contribute_topology=_contribute_topology,
        serialize_inherited_state=_serialize_inherited_state,
        verify_inherited_state=_verify_inherited_state,
    )
