#!/usr/bin/env python3

"""Torsion component descriptor (moved from the engine dispatch, A1).

The ``factory`` body preserves the ``engine._load_stage`` torsions
branch verbatim (snapshot handling plus direct stage construction);
``is_active`` preserves the ``engine._levels`` torsions predicate.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from confflow.science.confgen.registry import ComponentDescriptor

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


def descriptor() -> ComponentDescriptor:
    """Return the torsions component descriptor."""

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
    )
