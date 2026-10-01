"""ConfGen v3 coordination science (coordination lane).

Pure symbolic/geometric coordination stage: ideal shape templates and their
proper rotation groups (:mod:`shapes`), labeled enumeration with policy/proof
filters and symmetry accounting (:mod:`enumeration`), budgeted automorphism
search (:mod:`symmetry`), SCINE frame perception audit (:mod:`perception`),
geometric realization (:mod:`realization`), geometric symmetry verification
(:mod:`hgeom`), the isolated pure mapper re-export, and the core-API adapter
(:mod:`stage`, lazy so science never requires the core lane module).
"""

from __future__ import annotations

__all__ = [
    "shapes",
    "enumeration",
    "symmetry",
    "perception",
    "realization",
    "hgeom",
    "stage",
]
