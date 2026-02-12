#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""Workflow 辅助工具函数"""

import os
import contextlib
from typing import Any, List, Optional, Union


@contextlib.contextmanager
def pushd(path: str):
    """临时切换工作目录的上下文管理器"""
    prev = os.getcwd()
    os.chdir(path)
    try:
        yield
    finally:
        os.chdir(prev)


def as_list(value: Any) -> Any:
    """确保返回列表或 None"""
    if value is None:
        return None
    if isinstance(value, list):
        return value
    return [value]


def normalize_pair_list(value: Any) -> Optional[List[List[int]]]:
    """将 add_bond/del_bond/no_rotate/force_rotate 规范为 [[a,b], ...] (1-based)。"""
    if value is None:
        return None

    if isinstance(value, list):
        if len(value) == 0:
            return []
        if len(value) == 2 and all(isinstance(x, (int, float)) for x in value):
            return [[int(value[0]), int(value[1])]]
        if all(isinstance(x, (list, tuple)) and len(x) == 2 for x in value):
            return [[int(a), int(b)] for a, b in value]
        if all(isinstance(x, str) for x in value):
            out = []
            for item in value:
                parts = item.replace(",", " ").split()
                if len(parts) != 2:
                    raise ValueError(f"pair format error: {item}, expected 'a b' or 'a,b'")
                out.append([int(parts[0]), int(parts[1])])
            return out

    if isinstance(value, str):
        parts = value.replace(",", " ").split()
        if len(parts) != 2:
            raise ValueError(f"pair format error: {value}, expected 'a b' or 'a,b'")
        return [[int(parts[0]), int(parts[1])]]

    raise ValueError(f"unsupported pair format: {type(value)}")


def count_conformers_in_xyz(filepath: str) -> int:
    """计算单个 XYZ 文件中包含的构象数"""
    if not os.path.exists(filepath):
        return 0
    from ..core.utils import validate_xyz_file
    ok, geoms = validate_xyz_file(filepath)
    if not ok:
        return 0
    return len(geoms)


def count_conformers_any(src: Union[str, List[str]]) -> int:
    """计算单个或多个 XYZ 文件中的构象总数"""
    if isinstance(src, (list, tuple)):
        return sum(count_conformers_in_xyz(str(p)) for p in src)
    return count_conformers_in_xyz(str(src))


def is_multi_frame_xyz(filepath: str) -> bool:
    """判断是否为多帧 XYZ 文件"""
    return count_conformers_in_xyz(filepath) >= 2


def is_multi_frame_any(src: Union[str, List[str]]) -> bool:
    """判断给定的单个或多个输入是否包含多帧"""
    if isinstance(src, list):
        return any(is_multi_frame_xyz(str(p)) for p in src)
    return is_multi_frame_xyz(str(src))
