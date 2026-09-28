#!/usr/bin/env python3

"""ConfFlow core package.

Provides infrastructure-layer utilities: shared data, I/O, helper functions,
type definitions, and validation.

The historical ``from confflow.core import X`` surface is preserved, but it is
resolved lazily (PEP 562): importing :mod:`confflow.core` no longer executes
the legacy implementation modules (``core.models`` → canonical V2 pydantic
models, ``core.types``, ``core.validation``, ``core.io``, ...).  Import the
concrete submodule when you need the implementation.
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
    # Types (TypedDict — for type annotations)
    "CoordLine": (".types", "CoordLine"),
    "CoordLines": (".types", "CoordLines"),
    "Coords3D": (".types", "Coords3D"),
    "AtomList": (".types", "AtomList"),
    "GlobalConfig": (".types", "GlobalConfig"),
    "StepParams": (".types", "StepParams"),
    "ConformerData": (".types", "ConformerData"),
    "TaskResult": (".types", "TaskResult"),
    "WorkflowStats": (".types", "WorkflowStats"),
    "StepStats": (".types", "StepStats"),
    "ParsedOutput": (".types", "ParsedOutput"),
    "ValidationResult": (".types", "ValidationResult"),
    # Models (Pydantic — runtime validation)
    "TaskContext": (".models", "TaskContext"),
    # Validation
    "ValidationError": (".validation", "ValidationError"),
    "validate_positive": (".validation", "validate_positive"),
    "validate_non_negative": (".validation", "validate_non_negative"),
    "validate_integer": (".validation", "validate_integer"),
    "validate_float_range": (".validation", "validate_float_range"),
    "validate_not_empty": (".validation", "validate_not_empty"),
    "validate_file_exists": (".validation", "validate_file_exists"),
    "validate_dir_exists": (".validation", "validate_dir_exists"),
    "validate_coords_array": (".validation", "validate_coords_array"),
    "validate_atom_indices": (".validation", "validate_atom_indices"),
    "validate_bond_pair": (".validation", "validate_bond_pair"),
    "validate_choice": (".validation", "validate_choice"),
    "validate_string_not_empty": (".validation", "validate_string_not_empty"),
    "validate_params": (".validation", "validate_params"),
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
