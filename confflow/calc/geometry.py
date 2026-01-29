#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""几何与通用解析工具。

- 解析日志尾部结构
- 终止检查
"""

from __future__ import annotations

import os
import re
from typing import List, Optional

from .constants import get_element_symbol
from .core import logger


def parse_last_geometry(log_file: str, prog_id: int) -> Optional[List[str]]:
    """从 Gaussian/ORCA 输出文件中提取最后一个结构坐标块。"""
    if not os.path.exists(log_file):
        return None

    coords: List[str] = []

    # ORCA: 优先尝试同名 .xyz 文件
    if prog_id == 2:
        xyz_file_path = os.path.splitext(log_file)[0] + ".xyz"
        if os.path.exists(xyz_file_path):
            try:
                with open(xyz_file_path, "r") as f:
                    lines = f.readlines()
                    num = int(lines[0].strip())
                    for line in lines[2 : 2 + num]:
                        if line.strip():
                            p = line.split()
                            coords.append(
                                f"{p[0]:<2s} {float(p[1]): >12.6f} {float(p[2]): >12.6f} {float(p[3]): >12.6f}"
                            )
                    return coords
            except Exception as e:
                logger.debug(f"ORCA XYZ 读取失败 {xyz_file_path}: {e}")

    try:
        with open(log_file, "r", errors="ignore") as f:
            lines = f.read().splitlines()
    except IOError:
        return None

    if prog_id == 1:  # Gaussian
        start_idx = -1
        for i in range(len(lines) - 1, -1, -1):
            if "Standard orientation:" in lines[i] or "Input orientation:" in lines[i]:
                start_idx = i
                break
        if start_idx != -1:
            idx = start_idx + 5
            while idx < len(lines) and "---" not in lines[idx]:
                p = lines[idx].split()
                if len(p) == 6:
                    try:
                        an = int(p[1])
                        sym = get_element_symbol(an)
                        coords.append(
                            f"{sym:<2s} {float(p[3]): >12.6f} {float(p[4]): >12.6f} {float(p[5]): >12.6f}"
                        )
                    except Exception as e:
                        logger.debug(f"Gaussian 坐标解析失败 line {idx}: {e}")
                idx += 1
    elif prog_id == 2:  # ORCA Log Fallback
        content = "\n".join(lines)
        blocks = list(
            re.finditer(
                r"CARTESIAN COORDINATES \(ANGSTROEM\)\n-+\n(.*?)\n\s*\n",
                content,
                re.DOTALL,
            )
        )
        if blocks:
            for line in blocks[-1].group(1).strip().split("\n"):
                p = line.split()
                if len(p) == 4:
                    coords.append(
                        f"{p[0]:<2s} {float(p[1]): >12.6f} {float(p[2]): >12.6f} {float(p[3]): >12.6f}"
                    )

    return coords if coords else None


def check_termination(log_file: str, prog_name: str) -> bool:
    """检查 Gaussian/ORCA 是否正常终止（尾部关键字）。"""
    if not os.path.exists(log_file):
        return False
    try:
        with open(log_file, "rb") as f:
            f.seek(0, 2)
            size = f.tell()
            f.seek(max(0, size - 10000))
            content = f.read().decode("utf-8", errors="ignore")
            if prog_name == "gaussian" and "Normal termination" in content:
                return True
            if prog_name == "orca" and "****ORCA TERMINATED NORMALLY****" in content:
                return True
    except Exception as e:
        logger.debug(f"终止检查失败 {log_file}: {e}")
    return False
