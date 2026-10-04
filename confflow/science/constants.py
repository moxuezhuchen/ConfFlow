#!/usr/bin/env python3

"""ConfFlow science physical constants.

Centralises physical constants referenced across layers to avoid
cross-layer dependencies.
"""

from __future__ import annotations

__all__ = [
    "HARTREE_TO_KCALMOL",
]

# =============================================================================
# Physical constants
# =============================================================================

HARTREE_TO_KCALMOL: float = 627.5094740631  # Hartree to kcal/mol
