#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
ConfFlow XYZ I/O - 统一的 XYZ 文件读写模块

统一了原本分散在 calc.py, refine.py, viz.py, utils.py 中的 XYZ 处理逻辑
"""

import re
import os
import tempfile
import shutil
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
    """确保每个构象都有 CID，并写回到 comment/metadata。"""
    next_id = start
    for conf in conformers:
        meta = conf.get("metadata")
        if not meta:
            meta = {}
            conf["metadata"] = meta

        existing_cid = meta.get("CID")
        if existing_cid:
            if "CID=" not in conf.get("comment", ""):
                conf["comment"] = upsert_comment_kv(conf.get("comment", ""), "CID", str(existing_cid))
            continue

        new_cid = f"{prefix}{next_id:0{width}d}"
        next_id += 1
        meta["CID"] = new_cid
        conf["comment"] = upsert_comment_kv(conf.get("comment", ""), "CID", new_cid)

    return conformers


def ensure_xyz_cids(xyz_path: str, prefix: str = "cf") -> None:
    """读取 XYZ 文件，确保所有构象都有 CID，如果不完整则写回。"""
    if not os.path.exists(xyz_path):
        return
    try:
        confs = read_xyz_file(xyz_path, parse_metadata=True)
        if confs and (not confs.get("metadata") if isinstance(confs, dict) else (not confs[0].get("metadata") or "CID" not in confs[0]["metadata"])):
            ensure_conformer_cids(confs, prefix=prefix)
            write_xyz_file(xyz_path, confs, atomic=True)
    except Exception:
        pass


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


def parse_gaussian_input(filepath: str) -> Dict[str, Any]:
    """解析 Gaussian 输入文件 (.gjf/.com)。"""
    try:
        with open(filepath, "r", encoding="utf-8", errors="ignore") as f:
            text = f.read()
        return parse_gaussian_input_text(text, filepath)
    except Exception as e:
        if isinstance(e, (IOError, ValueError)):
            raise
        raise IOError(f"Failed to read Gaussian input {filepath}: {e}")


def parse_gaussian_input_text(text: str, source_label: str = "text") -> Dict[str, Any]:
    """解析 Gaussian 输入文本。

    返回:
        Dict 包含:
        - charge: 电荷
        - multiplicity: 多重度
        - atoms: 原子符号列表
        - coords: 坐标列表 [[x,y,z], ...]
        - coords_lines: 格式化后的坐标行 ["Sym x y z", ...]
    """
    import re
    from ..calc.constants import get_element_symbol

    lines = text.splitlines()
    qm_idx = None
    charge = 0
    mult = 1
    for i, ln in enumerate(lines):
        s = ln.strip()
        if not s:
            continue
        if re.match(r"^\s*-?\d+\s+-?\d+\s*$", s):
            qm_idx = i
            parts = s.split()
            charge = int(parts[0])
            mult = int(parts[1])
            break

    if qm_idx is None:
        raise ValueError(f"Cannot find charge/multiplicity line in {source_label}")

    atoms: List[str] = []
    coords_list: List[List[float]] = []
    coords_formatted: List[str] = []
    raw_coords_lines: List[str] = []

    for ln in lines[qm_idx + 1 :]:
        raw_ln = ln.strip()
        if not raw_ln:
            break
        p = raw_ln.split()
        if len(p) < 4:
            break

        raw_coords_lines.append(raw_ln)
        sym = p[0]
        if sym.isdigit():
            sym = get_element_symbol(int(sym))

        # 处理可能存在的冻结原子列 (取最后三个数值)
        xyz: List[float] = []
        for tok in reversed(p[1:]):
            try:
                xyz.append(float(tok))
            except (ValueError, TypeError):
                continue
            if len(xyz) == 3:
                break

        if len(xyz) != 3:
            break

        z, y, x = xyz
        atoms.append(sym)
        coords_list.append([x, y, z])
        coords_formatted.append(f"{sym} {x:.8f} {y:.8f} {z:.8f}")

    return {
        "charge": charge,
        "multiplicity": mult,
        "atoms": atoms,
        "coords": coords_list,
        "coords_lines": coords_formatted,
        "raw_coords_lines": raw_coords_lines,
    }


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
