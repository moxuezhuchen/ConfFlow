#!/usr/bin/env python3

"""v3 axis order constant (dependency-free).

Single authority for the three v3 component ids. ``model.py`` re-exports
it as ``AXIS_ORDER`` for compatibility; kernel code uses
``registry.ids()`` instead.
"""

from __future__ import annotations

__all__ = ["V3_AXIS_ORDER", "WIRE_TOP_LEVEL_KEYS"]

#: Fixed v3 generation order: Coordination -> Rings -> Torsions.
V3_AXIS_ORDER: tuple[str, ...] = ("coordination", "rings", "torsions")

#: Historical v3 spec top-level key whitelist (pure constant alias).
#: Re-exported by ``planner._TOP_LEVEL_KEYS`` for compatibility only; the
#: real unknown-key gate is ``_GENERIC_TOP_LEVEL_KEYS | registry.spec_keys``.
#: No algorithm lives here (dependency-free, literals only).
WIRE_TOP_LEVEL_KEYS: frozenset[str] = frozenset(
    {
        "schema_version",
        "index_base",
        "index_convention",
        "coordination",
        "rings",
        "torsions",
        "paths",
        "strict_path_bond_check",
        "topology",
        "stereochemistry",
        "exclusions",
        "tolerances",
        "limits",
        "sampling",
        "seed",
    }
)
