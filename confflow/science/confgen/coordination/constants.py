#!/usr/bin/env python3

"""Coordination lightweight constants (FIX-1A A5, stdlib only).

Single authority for schema/contract vocabularies owned by coordination.
``coordination/stage.py`` re-exports the same objects (``is`` identical)
so historic import paths keep working against one source.
"""

from __future__ import annotations

__all__ = [
    "BACKEND_CHOICES",
    "DEFAULT_SECTION_TOLERANCES",
]

#: Section-level coordination tolerances (lane-owned).
DEFAULT_SECTION_TOLERANCES: dict[str, float] = {
    "realize_tol": 0.45,
    "reaction_tol": 0.25,
    "clash_scale": 0.70,
    "rmsd_tolerance": 0.35,
    "margin_tolerance": 0.05,
    "shape_margin_tolerance": 0.15,
}

#: Realization backend selection (section-level, lane-owned).
BACKEND_CHOICES: tuple[str, ...] = ("rigid", "flexible", "rigid_then_flexible")
