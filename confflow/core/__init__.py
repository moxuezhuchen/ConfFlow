#!/usr/bin/env python3

"""ConfFlow core package.

Provides infrastructure-layer utilities: shared data, I/O, helper functions,
type definitions, and validation.
"""

from __future__ import annotations

from .constants import HARTREE_TO_KCALMOL
from .data import (
    GV_COVALENT_RADII,
    PERIODIC_SYMBOLS,
    SYMBOL_TO_ATOMIC_NUMBER,
    get_atomic_number,
    get_covalent_radius,
    get_element_symbol,
)
from .models import TaskContext
from .types import (
    AtomList,
    ConformerData,
    CoordLine,
    CoordLines,
    Coords3D,
    GlobalConfig,
    ParsedOutput,
    StepParams,
    StepStats,
    TaskResult,
    ValidationResult,
    WorkflowStats,
)
from .validation import (
    ValidationError,
    validate_atom_indices,
    validate_bond_pair,
    validate_choice,
    validate_coords_array,
    validate_dir_exists,
    validate_file_exists,
    validate_float_range,
    validate_integer,
    validate_non_negative,
    validate_not_empty,
    validate_params,
    validate_positive,
    validate_string_not_empty,
)

__all__ = [
    # Constants
    "HARTREE_TO_KCALMOL",
    # Data
    "GV_COVALENT_RADII",
    "PERIODIC_SYMBOLS",
    "SYMBOL_TO_ATOMIC_NUMBER",
    "get_covalent_radius",
    "get_element_symbol",
    "get_atomic_number",
    # Types (TypedDict — for type annotations)
    "CoordLine",
    "CoordLines",
    "Coords3D",
    "AtomList",
    "GlobalConfig",
    "StepParams",
    "ConformerData",
    "TaskResult",
    "WorkflowStats",
    "StepStats",
    "ParsedOutput",
    "ValidationResult",
    # Models (Pydantic — runtime validation)
    "TaskContext",
    # Validation
    "ValidationError",
    "validate_positive",
    "validate_non_negative",
    "validate_integer",
    "validate_float_range",
    "validate_not_empty",
    "validate_file_exists",
    "validate_dir_exists",
    "validate_coords_array",
    "validate_atom_indices",
    "validate_bond_pair",
    "validate_choice",
    "validate_string_not_empty",
    "validate_params",
]
