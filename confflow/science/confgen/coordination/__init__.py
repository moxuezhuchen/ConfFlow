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

import importlib
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from . import enumeration as enumeration
    from . import hgeom as hgeom
    from . import perception as perception
    from . import realization as realization
    from . import shapes as shapes
    from . import stage as stage
    from . import symmetry as symmetry

__all__ = [
    "shapes",
    "enumeration",
    "symmetry",
    "perception",
    "realization",
    "hgeom",
    "stage",
]

_LAZY_SUBMODULES: dict[str, str] = {
    "shapes": ".shapes",
    "enumeration": ".enumeration",
    "symmetry": ".symmetry",
    "perception": ".perception",
    "realization": ".realization",
    "hgeom": ".hgeom",
    "stage": ".stage",
}


def __getattr__(name: str) -> Any:
    module_name = _LAZY_SUBMODULES.get(name)
    if module_name is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    module = importlib.import_module(module_name, package=__name__)
    globals()[name] = module
    return module


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(__all__))
