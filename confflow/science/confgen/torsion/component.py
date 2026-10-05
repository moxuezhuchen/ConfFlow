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
    )
