#!/usr/bin/env python3

"""ConfFlow core package.

Provides infrastructure-layer utilities: shared data, I/O, helper functions,
type definitions, and validation.

The historical ``from confflow.core import X`` surface is preserved, but it is
resolved lazily (PEP 562): importing :mod:`confflow.core` no longer executes
the implementation modules (``core.io``, ``core.data``, ...).  Import the concrete submodule when you need the
implementation.

The ``core.types`` TypedDict module was retired by the Architecture Diet PR-9:
it only described the released V1/V2 configuration wire, which is gone.
"""

from __future__ import annotations

import importlib
from typing import Any

_LAZY_EXPORTS: dict[str, tuple[str, str]] = {
    # Constants
    "HARTREE_TO_KCALMOL": (".constants", "HARTREE_TO_KCALMOL"),
    # Data
    "GV_COVALENT_RADII": (".data", "GV_COVALENT_RADII"),
    "PERIODIC_SYMBOLS": (".data", "PERIODIC_SYMBOLS"),
    "SYMBOL_TO_ATOMIC_NUMBER": (".data", "SYMBOL_TO_ATOMIC_NUMBER"),
    "get_atomic_number": (".data", "get_atomic_number"),
    "get_covalent_radius": (".data", "get_covalent_radius"),
    "get_element_symbol": (".data", "get_element_symbol"),
}

__all__ = [*sorted(_LAZY_EXPORTS)]


def __getattr__(name: str) -> Any:
    export = _LAZY_EXPORTS.get(name)
    if export is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    module = importlib.import_module(export[0], package=__name__)
    value = getattr(module, export[1])
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(__all__))
