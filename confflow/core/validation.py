#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""ConfFlow 输入验证模块。

提供统一的参数验证功能，用于在函数入口处检查参数合法性。
"""

from __future__ import annotations

import logging
import os
from functools import wraps
from typing import Any, Callable, List, Optional, TypeVar, Union

import numpy as np

logger = logging.getLogger("confflow.validation")

F = TypeVar("F", bound=Callable[..., Any])


class ValidationError(ValueError):
    """验证错误异常"""

    def __init__(self, param_name: str, message: str, value: Any = None):
        self.param_name = param_name
        self.value = value
        full_msg = f"参数 '{param_name}' 验证失败: {message}"
        if value is not None:
            full_msg += f" (当前值: {value!r})"
        super().__init__(full_msg)


def validate_positive(value: Any, name: str) -> None:
    """验证参数为正数"""
    try:
        num = float(value)
    except (ValueError, TypeError) as e:
        raise ValidationError(name, "必须为数值类型", value) from e

    if num <= 0:
        raise ValidationError(name, "必须为正数", value)


def validate_non_negative(value: Any, name: str) -> None:
    """验证参数为非负数"""
    try:
        num = float(value)
    except (ValueError, TypeError) as e:
        raise ValidationError(name, "必须为数值类型", value) from e

    if num < 0:
        raise ValidationError(name, "必须为非负数", value)


def validate_integer(value: Any, name: str, min_val: Optional[int] = None, max_val: Optional[int] = None) -> int:
    """验证参数为整数，可选范围检查"""
    try:
        num = int(value)
    except (ValueError, TypeError) as e:
        raise ValidationError(name, "必须为整数", value) from e

    if min_val is not None and num < min_val:
        raise ValidationError(name, f"必须 >= {min_val}", value)
    if max_val is not None and num > max_val:
        raise ValidationError(name, f"必须 <= {max_val}", value)

    return num


def validate_float_range(value: Any, name: str, min_val: Optional[float] = None, max_val: Optional[float] = None) -> float:
    """验证参数为浮点数，可选范围检查"""
    try:
        num = float(value)
    except (ValueError, TypeError) as e:
        raise ValidationError(name, "必须为浮点数", value) from e

    if min_val is not None and num < min_val:
        raise ValidationError(name, f"必须 >= {min_val}", value)
    if max_val is not None and num > max_val:
        raise ValidationError(name, f"必须 <= {max_val}", value)

    return num


def validate_not_empty(value: Any, name: str) -> None:
    """验证参数非空"""
    if value is None:
        raise ValidationError(name, "不能为 None")
    if isinstance(value, (str, list, tuple, dict)) and len(value) == 0:
        raise ValidationError(name, "不能为空")


def validate_file_exists(filepath: str, name: str) -> None:
    """验证文件存在"""
    if not filepath:
        raise ValidationError(name, "文件路径不能为空")
    if not os.path.exists(filepath):
        raise ValidationError(name, f"文件不存在: {filepath}")
    if not os.path.isfile(filepath):
        raise ValidationError(name, f"路径不是文件: {filepath}")


def validate_dir_exists(dirpath: str, name: str) -> None:
    """验证目录存在"""
    if not dirpath:
        raise ValidationError(name, "目录路径不能为空")
    if not os.path.exists(dirpath):
        raise ValidationError(name, f"目录不存在: {dirpath}")
    if not os.path.isdir(dirpath):
        raise ValidationError(name, f"路径不是目录: {dirpath}")


def validate_coords_array(coords: Any, name: str, expected_atoms: Optional[int] = None) -> np.ndarray:
    """验证坐标数组

    Args:
        coords: 坐标数据（list 或 numpy array）
        name: 参数名称
        expected_atoms: 预期的原子数（可选）

    Returns:
        验证后的 numpy 数组 (N, 3)
    """
    if coords is None:
        raise ValidationError(name, "坐标不能为 None")

    try:
        arr = np.asarray(coords, dtype=float)
    except (ValueError, TypeError) as e:
        raise ValidationError(name, "无法转换为数值数组", coords) from e

    if arr.ndim != 2:
        raise ValidationError(name, f"坐标必须是二维数组，当前维度: {arr.ndim}")

    if arr.shape[1] != 3:
        raise ValidationError(name, f"坐标必须是 (N, 3) 形状，当前: {arr.shape}")

    if expected_atoms is not None and arr.shape[0] != expected_atoms:
        raise ValidationError(name, f"原子数不匹配，预期 {expected_atoms}，实际 {arr.shape[0]}")

    # 检查 NaN 和 Inf
    if np.any(np.isnan(arr)):
        raise ValidationError(name, "坐标包含 NaN 值")
    if np.any(np.isinf(arr)):
        raise ValidationError(name, "坐标包含无穷大值")

    return arr


def validate_atom_indices(indices: List[int], name: str, max_index: int) -> None:
    """验证原子索引列表

    Args:
        indices: 原子索引列表（1-based）
        name: 参数名称
        max_index: 最大允许索引
    """
    if not indices:
        return

    for i, idx in enumerate(indices):
        if not isinstance(idx, int):
            raise ValidationError(name, f"索引 {i} 不是整数: {idx}")
        if idx < 1:
            raise ValidationError(name, f"原子索引必须 >= 1（1-based），当前: {idx}")
        if idx > max_index:
            raise ValidationError(name, f"原子索引 {idx} 超出范围（最大: {max_index}）")


def validate_bond_pair(pair: Union[List[int], tuple], name: str, max_index: int) -> tuple:
    """验证键对

    Args:
        pair: 键对 [a, b] 或 (a, b)（1-based）
        name: 参数名称
        max_index: 最大允许索引

    Returns:
        验证后的元组 (a, b)
    """
    if not isinstance(pair, (list, tuple)) or len(pair) != 2:
        raise ValidationError(name, "键对必须是长度为 2 的列表或元组", pair)

    a, b = pair
    try:
        a, b = int(a), int(b)
    except (ValueError, TypeError) as e:
        raise ValidationError(name, "键对元素必须是整数", pair) from e

    if a < 1 or b < 1:
        raise ValidationError(name, f"原子索引必须 >= 1（1-based），当前: ({a}, {b})")
    if a > max_index or b > max_index:
        raise ValidationError(name, f"原子索引超出范围（最大: {max_index}），当前: ({a}, {b})")
    if a == b:
        raise ValidationError(name, f"键对不能是同一个原子: ({a}, {b})")

    return (a, b)


def validate_choice(value: Any, name: str, choices: List[Any]) -> None:
    """验证参数在允许的选项中"""
    if value not in choices:
        raise ValidationError(name, f"必须是以下选项之一: {choices}", value)


def validate_string_not_empty(value: Any, name: str) -> str:
    """验证参数为非空字符串"""
    if value is None:
        raise ValidationError(name, "不能为 None")
    if not isinstance(value, str):
        raise ValidationError(name, "必须是字符串类型", value)
    if not value.strip():
        raise ValidationError(name, "不能为空字符串")
    return value.strip()


# ==============================================================================
# 验证装饰器
# ==============================================================================


def validate_params(**validators: Callable[[Any, str], None]) -> Callable[[F], F]:
    """参数验证装饰器

    用法:
        @validate_params(
            threshold=lambda v, n: validate_positive(v, n),
            coords=lambda v, n: validate_not_empty(v, n),
        )
        def my_function(threshold, coords):
            ...

    Args:
        validators: 参数名 -> 验证函数的映射
    """
    def decorator(func: F) -> F:
        @wraps(func)
        def wrapper(*args, **kwargs):
            # 获取函数签名以映射位置参数
            import inspect
            sig = inspect.signature(func)
            bound = sig.bind_partial(*args, **kwargs)
            bound.apply_defaults()

            # 执行验证
            for param_name, validator in validators.items():
                if param_name in bound.arguments:
                    value = bound.arguments[param_name]
                    if value is not None:  # 跳过 None 值（允许可选参数）
                        try:
                            validator(value, param_name)
                        except ValidationError:
                            raise
                        except Exception as e:
                            raise ValidationError(param_name, str(e), value) from e

            return func(*args, **kwargs)
        return wrapper  # type: ignore
    return decorator


__all__ = [
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
