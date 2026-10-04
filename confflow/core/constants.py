#!/usr/bin/env python3

"""Compatibility forwarding shim.

The physical constants live in :mod:`confflow.science.constants`; this
module re-exports the same objects so historical ``confflow.core.constants``
imports keep working against the single authority.
"""

from __future__ import annotations

from ..science.constants import HARTREE_TO_KCALMOL

__all__ = [
    "HARTREE_TO_KCALMOL",
]
