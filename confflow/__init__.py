#!/usr/bin/env python3

"""ConfFlow - Automated computational chemistry conformer search workflow engine.

Importing the package loads no optional scientific dependency and changes no
global logging state. RDKit, psutil and numba are detected on first access to
their availability flags or handles (PEP 562), by really attempting the import.
"""

from __future__ import annotations

import importlib
from collections.abc import Callable
from importlib.metadata import PackageNotFoundError, version
from typing import TYPE_CHECKING, Any

try:
    __version__ = version("confflow")
except PackageNotFoundError:
    __version__ = "2.1.6"
__author__ = "ConfFlow Team"

if TYPE_CHECKING:
    RDKIT_AVAILABLE: bool
    PSUTIL_AVAILABLE: bool
    NUMBA_AVAILABLE: bool
    Chem: Any
    AllChem: Any
    psutil: Any
    njit: Callable[..., Any]


def _njit_fallback(*args, **kwargs):
    """Return the original function when Numba is unavailable."""

    def decorator(func):
        return func

    return decorator if not args else args[0]


def _detect_rdkit() -> tuple[bool, Any, Any]:
    """Attempt the RDKit import; any failure means unavailable."""
    try:
        from rdkit import Chem
        from rdkit.Chem import AllChem
    except Exception:  # noqa: BLE001 - a broken install must degrade, not crash
        return False, None, None
    return True, Chem, AllChem


def _detect_psutil() -> tuple[bool, Any]:
    """Attempt the psutil import; any failure means unavailable."""
    try:
        import psutil
    except Exception:  # noqa: BLE001 - a broken install must degrade, not crash
        return False, None
    return True, psutil


def _detect_numba() -> tuple[bool, Callable[..., Any]]:
    """Attempt the numba import; fall back to the no-op decorator on failure."""
    try:
        from numba import njit
    except Exception:  # noqa: BLE001 - a broken install must degrade, not crash
        return False, _njit_fallback
    return True, njit


# Each optional group is detected once; the results are bound into the module
# globals, so later accesses never reach ``__getattr__`` again (the cache).
_OPTIONAL_GROUPS: dict[str, tuple[Callable[[], tuple[Any, ...]], tuple[str, ...]]] = {
    "rdkit": (_detect_rdkit, ("RDKIT_AVAILABLE", "Chem", "AllChem")),
    "psutil": (_detect_psutil, ("PSUTIL_AVAILABLE", "psutil")),
    "numba": (_detect_numba, ("NUMBA_AVAILABLE", "njit")),
}
_OPTIONAL_NAMES: dict[str, str] = {
    name: group for group, (_, names) in _OPTIONAL_GROUPS.items() for name in names
}

_LAZY_EXPORTS: dict[str, tuple[str, str]] = {
    "main": (".main", "main"),
    "read_xyz_file": (".core.io", "read_xyz_file"),
    "write_xyz_file": (".core.io", "write_xyz_file"),
    "parse_comment_metadata": (".core.io", "parse_comment_metadata"),
    "ConfFlowLogger": (".core.logging", "ConfFlowLogger"),
    "get_logger": (".core.logging", "get_logger"),
}

__all__ = [
    "__version__",
    "RDKIT_AVAILABLE",
    "PSUTIL_AVAILABLE",
    "NUMBA_AVAILABLE",
    *sorted(_LAZY_EXPORTS),
]


def __getattr__(name: str):
    group = _OPTIONAL_NAMES.get(name)
    if group is not None:
        detect, names = _OPTIONAL_GROUPS[group]
        globals().update(zip(names, detect()))
        return globals()[name]
    export = _LAZY_EXPORTS.get(name)
    if export is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    module_name, attr_name = export
    module = importlib.import_module(module_name, package=__name__)
    value = getattr(module, attr_name)
    globals()[name] = value
    return value
