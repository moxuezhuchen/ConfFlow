#!/usr/bin/env python3

"""ConfFlow core package.

Infrastructure utilities (data, I/O, helpers, types, validation).
Historical `from confflow.core import X` surface preserved via lazy
PEP 562 resolution without executing implementation modules; import the
concrete submodule for implementation. `core.types` retired (PR-9);
`core.{bonding,constants,data}` shims retired (DIET-2 R1.5) in favour of
`confflow.science.{bonding,constants,data}` single authority.
"""

from __future__ import annotations

import importlib
from typing import Any

_LAZY_EXPORTS: dict[str, tuple[str, str]] = {
    # Constants (authority: confflow.science.constants)
    "HARTREE_TO_KCALMOL": ("confflow.science.constants", "HARTREE_TO_KCALMOL"),
    # Data (authority: confflow.science.data)
    "GV_COVALENT_RADII": ("confflow.science.data", "GV_COVALENT_RADII"),
    "PERIODIC_SYMBOLS": ("confflow.science.data", "PERIODIC_SYMBOLS"),
    "SYMBOL_TO_ATOMIC_NUMBER": ("confflow.science.data", "SYMBOL_TO_ATOMIC_NUMBER"),
    "get_atomic_number": ("confflow.science.data", "get_atomic_number"),
    "get_covalent_radius": ("confflow.science.data", "get_covalent_radius"),
    "get_element_symbol": ("confflow.science.data", "get_element_symbol"),
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
