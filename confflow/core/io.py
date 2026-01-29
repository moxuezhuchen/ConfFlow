#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
ConfFlow XYZ I/O - 统一的 XYZ 文件读写模块

统一了原本分散在 calc.py, refine.py, viz.py, utils.py 中的 XYZ 处理逻辑
"""

import re
from typing import List, Dict, Any, Optional, Tuple


def upsert_comment_kv(comment: str, key: str, value: Any) -> str:
    """在注释行中更新/插入 key=value（不做数值格式化）。

    规则：
    - 若已存在 key=xxx，则替换为 key=value（只替换第一个匹配）
    - 若不存在，则追加 " | key=value"（若 comment 为空则仅写 key=value）
    """
    comment = (comment or "").strip()
    key = str(key)
    val_str = str(value)

    pattern = re.compile(rf"(?P<prefix>^|[\s|,])(?P<k>{re.escape(key)})\s*=\s*(?P<v>[^\s|,]+)")
    m = pattern.search(comment)
    if not m:
        if not comment:
            return f"{key}={val_str}"
        return f"{comment} | {key}={val_str}"

    start, end = m.span("v")
    return comment[:start] + val_str + comment[end:]


def ensure_conformer_cids(
    conformers: List[Dict[str, Any]],
    *,
    prefix: str = "cf",
    start: int = 1,
    width: int = 6,
) -> List[Dict[str, Any]]:
    """确保每个构象都有 CID，并写回到 comment/metadata。

    说明：
    - 若 metadata 中已有 CID，则保持不变，并确保 comment 中包含 CID。
    - 若缺失，则按 frame 顺序分配可复现的 CID（prefix + 序号）。
    """
    next_id = start
    for conf in conformers:
        meta = conf.get("metadata")
        if meta is None or not isinstance(meta, dict):
            meta = {}
            conf["metadata"] = meta

        cid = meta.get("CID")
        if cid is None or str(cid).strip() == "":
            cid = f"{prefix}_{next_id:0{width}d}"
            meta["CID"] = cid
            next_id += 1

        conf["comment"] = upsert_comment_kv(conf.get("comment", ""), "CID", cid)

    return conformers


def parse_comment_metadata(comment: str) -> Dict[str, Any]:
    """解析 XYZ 注释行中的 key=value 元数据

    Args:
        comment: 注释行字符串，如 "Rank=1 | E=-1.0 | G_corr=0.123"

    Returns:
        解析后的字典，值会尝试转换为 float
    """
    meta: Dict[str, Any] = {}
    # 匹配形如 key=value 的模式（兼容空格、逗号、竖线分隔）
    for m in re.finditer(r"([A-Za-z_][A-Za-z0-9_]*)\s*=\s*([^\s|,]+)", comment or ""):
        k, v = m.group(1), m.group(2)
        try:
            meta[k] = float(v)
            # 兼容：Energy 也存为 E
            if k == "Energy":
                meta["E"] = float(v)
        except (ValueError, TypeError):
            meta[k] = v
    return meta


def read_xyz_file(filepath: str, parse_metadata: bool = True) -> List[Dict[str, Any]]:
    """读取 XYZ 文件，返回构象列表

    Args:
        filepath: XYZ 文件路径
        parse_metadata: 是否解析注释行的 key=value 元数据

    Returns:
        构象列表，每个元素为 dict:
            - natoms: 原子数
            - comment: 原始注释行
            - atoms: 原子符号列表（大写）
            - coords: 坐标列表 [[x,y,z], ...]
            - metadata: 元数据字典（如果 parse_metadata=True）
    """
    conformers = []

    try:
        with open(filepath, "r", encoding="utf-8") as f:
            lines = f.readlines()
    except (IOError, OSError) as e:
        raise IOError(f"无法读取 XYZ 文件 {filepath}: {e}")

    i = 0
    frame_idx = 0

    while i < len(lines):
        line = lines[i].strip()
        if not line or not line.isdigit():
            i += 1
            continue

        try:
            num_atoms = int(line)
        except ValueError:
            i += 1
            continue

        if i + 2 + num_atoms > len(lines):
            break

        comment = lines[i + 1].strip()

        atoms = []
        coords = []
        for j in range(num_atoms):
            atom_line = lines[i + 2 + j].strip()
            parts = atom_line.split()
            if len(parts) < 4:
                break

            atoms.append(parts[0].upper())
            try:
                # 取最后三列作为坐标（兼容有额外列的情况）
                x, y, z = float(parts[-3]), float(parts[-2]), float(parts[-1])
                coords.append([x, y, z])
            except (ValueError, IndexError):
                break

        if len(coords) == num_atoms:
            frame = {
                "natoms": num_atoms,
                "comment": comment,
                "atoms": atoms,
                "coords": coords,
                "frame_index": frame_idx,
            }

            if parse_metadata:
                frame["metadata"] = parse_comment_metadata(comment)

            conformers.append(frame)
            frame_idx += 1

        i += 2 + num_atoms

    return conformers


def write_xyz_file(filepath: str, conformers: List[Dict[str, Any]], atomic: bool = True) -> None:
    """写入 XYZ 文件

    Args:
        filepath: 输出文件路径
        conformers: 构象列表，每个元素需包含 natoms, comment, atoms, coords
        atomic: 是否使用原子写入模式（先写临时文件再重命名，防止并发损坏）
    """
    import tempfile
    import shutil

    def _write_to_file(f):
        for conf in conformers:
            natoms = conf.get("natoms", len(conf.get("atoms", [])))
            comment = conf.get("comment", "")
            atoms = conf.get("atoms", [])
            coords = conf.get("coords", [])

            if len(atoms) != len(coords):
                raise ValueError(f"原子数与坐标数不匹配: {len(atoms)} vs {len(coords)}")

            f.write(f"{natoms}\n")
            f.write(f"{comment}\n")

            for atom, (x, y, z) in zip(atoms, coords):
                f.write(f"{atom:<2s} {x:12.8f} {y:12.8f} {z:12.8f}\n")

    if atomic:
        # 原子写入：先写临时文件，再原子重命名
        import os
        import logging
        
        io_logger = logging.getLogger("confflow.io")
        dir_path = os.path.dirname(filepath) or "."
        os.makedirs(dir_path, exist_ok=True)

        fd, tmp_path = tempfile.mkstemp(suffix=".xyz", dir=dir_path)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                _write_to_file(f)
            # 原子重命名
            shutil.move(tmp_path, filepath)
        except (IOError, OSError) as e:
            # 清理临时文件
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            io_logger.error(f"写入 XYZ 文件失败: {filepath}, 原因: {e}")
            raise
        except Exception as e:
            # 清理临时文件
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            io_logger.error(f"写入 XYZ 文件异常: {filepath}, 原因: {e}")
            raise
    else:
        with open(filepath, "w", encoding="utf-8") as f:
            _write_to_file(f)


def coords_lines_to_array(
    coords_lines: List[str],
) -> Optional[List[Tuple[str, float, float, float]]]:
    """将坐标行列表转换为 (symbol, x, y, z) 元组列表

    Args:
        coords_lines: 如 ["H 0.0 0.0 0.0", "C 1.0 0.0 0.0"]

    Returns:
        [(symbol, x, y, z), ...] 或 None（解析失败）
    """
    try:
        result = []
        for line in coords_lines:
            parts = line.split()
            if len(parts) < 4:
                return None

            symbol = parts[0]
            # 取最后三个可转 float 的值
            xyz = []
            for tok in reversed(parts[1:]):
                try:
                    xyz.append(float(tok))
                    if len(xyz) == 3:
                        break
                except (ValueError, TypeError):
                    continue

            if len(xyz) != 3:
                return None

            z, y, x = xyz  # reversed
            result.append((symbol, float(x), float(y), float(z)))

        return result
    except Exception:
        return None


def calculate_bond_length(coords_lines: List[str], atom1: int, atom2: int) -> Optional[float]:
    """计算两原子间距离

    Args:
        coords_lines: 坐标行列表
        atom1, atom2: 1-based 原子索引

    Returns:
        键长（Å）或 None（解析失败）
    """
    coords_array = coords_lines_to_array(coords_lines)
    if coords_array is None:
        return None

    if atom1 < 1 or atom2 < 1 or atom1 > len(coords_array) or atom2 > len(coords_array):
        return None

    _, x1, y1, z1 = coords_array[atom1 - 1]
    _, x2, y2, z2 = coords_array[atom2 - 1]

    dx, dy, dz = x1 - x2, y1 - y2, z1 - z2
    return float((dx * dx + dy * dy + dz * dz) ** 0.5)
