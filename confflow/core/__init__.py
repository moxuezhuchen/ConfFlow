#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""ConfFlow 核心模块。

提供基础设施层功能：共享数据、I/O、工具函数、类型定义、验证。
"""

from .data import (
    GV_COVALENT_RADII,
    PERIODIC_SYMBOLS,
    SYMBOL_TO_ATOMIC_NUMBER,
    get_covalent_radius,
    get_element_symbol,
    get_atomic_number,
)

from .types import (
    CoordLine,
    CoordLines,
    Coords3D,
    AtomList,
    GlobalConfig,
    StepParams,
    ConformerData,
    TaskResult,
    WorkflowStats,
    StepStats,
    ParsedOutput,
    ValidationResult,
)

from .validation import (
    ValidationError,
    validate_positive,
    validate_non_negative,
    validate_integer,
    validate_float_range,
    validate_not_empty,
    validate_file_exists,
    validate_dir_exists,
    validate_coords_array,
    validate_atom_indices,
    validate_bond_pair,
    validate_choice,
    validate_string_not_empty,
    validate_params,
)

__all__ = [
    # 数据
    "GV_COVALENT_RADII",
    "PERIODIC_SYMBOLS",
    "SYMBOL_TO_ATOMIC_NUMBER",
    "get_covalent_radius",
    "get_element_symbol",
    "get_atomic_number",
    # 类型
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
    # 验证
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
