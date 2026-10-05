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


def descriptor() -> ComponentDescriptor:
    """Return the rings component descriptor."""

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
    )
