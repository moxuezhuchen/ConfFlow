#!/usr/bin/env python3

"""Torsion component descriptor (moved from the engine dispatch, A1).

The ``factory`` body preserves the ``engine._load_stage`` torsions
branch verbatim (snapshot handling plus direct stage construction);
``is_active`` preserves the ``engine._levels`` torsions predicate.

A5 lightweight: top level imports only this component's stdlib-only
``constants.py`` plus the registry type. All runtime imports happen
inside hook bodies when called, never at ``descriptor()`` build time.
``schema_constants``/``contract_options`` read only ``constants.py``.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from confflow.science.confgen.registry import ComponentDescriptor

from .constants import TORSION_MODELS, TREATMENTS, wrap_degrees

__all__ = ["descriptor"]


def _preserved_entries(resolved: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Descriptor fallback: torsion ``preserve_input`` entries."""
    from confflow.science.confgen.torsion.scope import preserved_entries as _impl

    return _impl(resolved)


def _report_section(resolved: Mapping[str, Any]) -> dict[str, Any] | None:
    """Descriptor fallback: torsion contributes no report section."""
    from confflow.science.confgen.torsion.scope import report_section as _impl

    return _impl(resolved)


def _describe_scope(resolved: Mapping[str, Any]) -> Any:
    """Descriptor fallback: torsion scope slice."""
    from confflow.science.confgen.torsion.scope import describe_scope as _impl

    return _impl(resolved)


def _normalize_spec(raw: Mapping[str, Any], *, index_base: int) -> Mapping[str, Any]:
    """Lazy proxy: normalize owned torsion keys only when called."""
    from confflow.science.confgen.torsion.spec import normalize_spec as _impl

    return _impl(raw, index_base=index_base)


def _validate_context(resolved: Mapping[str, Any], context: Any) -> None:
    """Lazy proxy: torsion context check."""
    from confflow.science.confgen.torsion.spec import validate_context as _impl

    return _impl(resolved, context)


def _has_indices(raw: Mapping[str, Any]) -> bool:
    """Lazy proxy: torsions/paths index probe only when called."""
    from confflow.science.confgen.torsion.spec import has_indices as _impl

    return _impl(raw)


def _expand_context_spec(resolved: Any, structure: Any, adjacency: Any) -> None:
    """Expand declared paths, preserving the legacy empty-path call guard."""
    if not resolved.get("paths"):
        return
    from confflow.science.confgen.torsion.spec import expand_context_spec as _impl

    return _impl(resolved, structure, adjacency)


def _serialize_inherited_state(resolved: Mapping[str, Any], state_value: Any, context: Any) -> Any:
    """Descriptor hook: torsion public scope payload (lazy, no eager solver)."""
    from confflow.science.confgen.torsion.inherited import (
        serialize_inherited_state as _impl,
    )

    return _impl(resolved, state_value, context)


def _verify_inherited_state(structure: Any, state_value: Any, payload: Any, context: Any) -> Any:
    """Descriptor hook: torsion scope+geometry verify (lazy)."""
    from confflow.science.confgen.torsion.inherited import (
        verify_inherited_state as _impl,
    )

    return _impl(structure, state_value, payload, context)


def _schema_constants() -> Mapping[str, Any]:
    """Return lightweight torsion vocabulary (no solver import)."""
    return {
        "models": tuple(TORSION_MODELS),
        "treatments": tuple(TREATMENTS),
        "wrap_degrees": wrap_degrees,
    }


def _contract_options() -> Mapping[str, Any]:
    """Return the torsion-owned slice of the producer contract."""
    return {
        "torsion_models": list(TORSION_MODELS),
        "treatments": list(TREATMENTS),
    }


def descriptor() -> ComponentDescriptor:
    """Return the torsions component descriptor (lightweight build)."""

    def is_active(resolved: Mapping[str, Any]) -> bool:
        torsions = resolved.get("torsions", [])
        if isinstance(torsions, (list, tuple)) and len(torsions) > 0:
            return True
        return False

    def factory(resolved: Mapping[str, Any]) -> Any:
        from confflow.science.confgen.engine import thaw_snapshot
        from confflow.science.confgen.torsion.stage import TorsionStage

        snapshot = thaw_snapshot(resolved)
        return TorsionStage(snapshot)

    return ComponentDescriptor(
        id="torsions",
        order=30,
        spec_keys=("torsions", "paths", "strict_path_bond_check"),
        state_merge="merge",
        is_active=is_active,
        factory=factory,
        check_bond_integrity=True,
        carries_inherited_locks=True,
        fallback_lock=None,
        preserved_entries=_preserved_entries,
        report_section=_report_section,
        describe_scope=_describe_scope,
        normalize_spec=_normalize_spec,
        validate_context=_validate_context,
        spec_defaults=(("torsions", []), ("paths", []), ("strict_path_bond_check", False)),
        has_indices=_has_indices,
        index_detection_order=10,
        expand_context_spec=_expand_context_spec,
        serialize_inherited_state=_serialize_inherited_state,
        verify_inherited_state=_verify_inherited_state,
        schema_constants=_schema_constants,
        contract_options=_contract_options,
    )
