#!/usr/bin/env python3

"""v3 axis order constant (dependency-free).

Single authority for the three v3 component ids. ``model.py`` re-exports
it as ``AXIS_ORDER`` for compatibility; kernel code uses
``registry.ids()`` instead.
"""

from __future__ import annotations

__all__ = ["V3_AXIS_ORDER"]

#: Fixed v3 generation order: Coordination -> Rings -> Torsions.
V3_AXIS_ORDER: tuple[str, ...] = ("coordination", "rings", "torsions")
