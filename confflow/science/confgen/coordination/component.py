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

from confflow.science.confgen.registry import ComponentDescriptor

__all__ = ["descriptor"]


def descriptor() -> ComponentDescriptor:
    """Return the coordination component descriptor."""

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
    )
