#!/usr/bin/env python3

"""Compatibility forwarding shim.

The element data tables and helpers live in :mod:`confflow.science.data`;
this module re-exports the same objects so historical
``confflow.core.data`` imports keep working against the single authority.
"""

from __future__ import annotations

from ..science.data import (
    GV_COVALENT_RADII,
    GV_RADII_ARRAY,
    PERIODIC_SYMBOLS,
    SYMBOL_TO_ATOMIC_NUMBER,
    get_atomic_number,
    get_covalent_radius,
    get_element_symbol,
)

__all__ = [
    "GV_COVALENT_RADII",
    "GV_RADII_ARRAY",
    "PERIODIC_SYMBOLS",
    "SYMBOL_TO_ATOMIC_NUMBER",
    "get_covalent_radius",
    "get_element_symbol",
    "get_atomic_number",
]
