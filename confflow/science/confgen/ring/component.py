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
    )
