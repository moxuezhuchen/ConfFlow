#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""与任务后处理相关的解析/计算（TSBond、RMSD 解析辅助等）。"""

from __future__ import annotations

import re
from typing import Any, List, Optional, Tuple

import numpy as np

from ..core import io as io_xyz


def _keyword_requests_freq(config: dict) -> bool:
    """判断 keyword 是否显式请求频率计算。"""
    kw = str(config.get("keyword", "") or "")
    if not kw.strip():
        return False
    return re.search(r"(?i)\bfreq\b", kw) is not None


def _parse_ts_bond_atoms(val: Any) -> Optional[Tuple[int, int]]:
    """解析 TS 成键/断键原子对（1-based）。"""
    if val is None:
        return None
    if isinstance(val, (list, tuple)):
        nums: List[int] = []
        for x in val:
            try:
                nums.append(int(x))
            except (ValueError, TypeError):
                continue
    else:
        nums = []
        for m in re.findall(r"\d+", str(val)):
            try:
                nums.append(int(m))
            except (ValueError, TypeError):
                continue
    if len(nums) < 2:
        return None
    a, b = nums[0], nums[1]
    if a <= 0 or b <= 0 or a == b:
        return None
    return a, b


def _bond_length_from_xyz_lines(coords_lines: List[str], a1: int, a2: int) -> Optional[float]:
    """从 XYZ 坐标行计算两原子距离（Å）。"""
    return io_xyz.calculate_bond_length(coords_lines, a1, a2)


def _coords_array_from_xyz_lines(coords_lines: List[str]) -> Optional[np.ndarray]:
    """将 XYZ 坐标行解析为 (N, 3) 的 numpy 数组。"""
    if not coords_lines:
        return None
    try:
        coords: List[List[float]] = []
        for line in coords_lines:
            # 跳过空行或 None
            if line is None or not isinstance(line, str):
                return None
            parts = line.split()
            xyz: List[float] = []
            for tok in reversed(parts):
                try:
                    xyz.append(float(tok))
                except (ValueError, TypeError):
                    continue
                if len(xyz) == 3:
                    break
            if len(xyz) != 3:
                return None
            z, y, x = xyz  # reversed
            coords.append([x, y, z])
        return np.array(coords, dtype=float)
    except (ValueError, TypeError, AttributeError):
        # 数值转换失败或类型错误
        return None
