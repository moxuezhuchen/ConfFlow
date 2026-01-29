#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
ConfFlow Refine - 构象后处理工具 (v1.0)
功能: RMSD 去重、能量筛选、拓扑分析
架构: 模块化设计 (Library & Script)
"""

import argparse
import os
import sys
import re
import logging
import numpy as np
import multiprocessing
import hashlib
from itertools import repeat
import time
from collections import defaultdict
from concurrent.futures import ProcessPoolExecutor

logger = logging.getLogger("confflow.refine")

from ...core.console import create_progress, console



# 尝试导入 numba
try:
    import numba
except ImportError:
    logger.warning("Numba not found. RMSD calculation will be slow. Consider: pip install numba")

    class FakeNumba:
        __name__ = "FakeNumba"

        def njit(self, *args, **kwargs):
            def decorator(func):
                return func

            return decorator

    numba = FakeNumba()

# --- 导入共价半径数据（统一从 core.data 导入）---
try:
    from ...core.data import GV_COVALENT_RADII, PERIODIC_SYMBOLS
except ImportError:
    try:
        from confflow.core.data import GV_COVALENT_RADII, PERIODIC_SYMBOLS
    except ImportError:
        # 最后回退：直接从 core 导入
        from ...core import GV_COVALENT_RADII, PERIODIC_SYMBOLS


def get_element_atomic_number(symbol: str) -> int:
    """根据元素符号获取原子序数"""
    if not symbol:
        return 0
    s = symbol.capitalize()
    try:
        return PERIODIC_SYMBOLS.index(s)
    except ValueError:
        return 0  # 未知元素


# --- 常量定义 ---
HARTREE_TO_KCALMOL = 627.509
BOND_SCALE_FACTOR = 1.2
PMI_TOLERANCE_FACTOR = 0.05

# ==============================================================================
# Numba JIT 加速的核心计算函数
# ==============================================================================


@numba.njit(fastmath=True, cache=True)
def get_pmi(coords):
    if coords.shape[0] == 0:
        return np.array([0.0, 0.0, 0.0])
    center = coords.sum(axis=0) / coords.shape[0]
    coords_centered = coords - center
    I = np.zeros((3, 3))
    for i in range(coords_centered.shape[0]):
        x, y, z = coords_centered[i]
        I[0, 0] += y**2 + z**2
        I[1, 1] += x**2 + z**2
        I[2, 2] += x**2 + y**2
        I[0, 1] -= x * y
        I[0, 2] -= x * z
        I[1, 2] -= y * z
    I[1, 0] = I[0, 1]
    I[2, 0] = I[0, 2]
    I[2, 1] = I[1, 2]
    return np.sort(np.linalg.eigvalsh(I))


@numba.njit(fastmath=True, cache=True)
def fast_rmsd(coords1, coords2):
    if coords1.shape[0] != coords2.shape[0] or coords1.shape[0] == 0:
        return 999.9
    center1 = coords1.sum(axis=0) / coords1.shape[0]
    center2 = coords2.sum(axis=0) / coords2.shape[0]
    coords1_centered = coords1 - center1
    coords2_centered = coords2 - center2
    C = np.dot(coords2_centered.T, coords1_centered)
    U, S, Vt = np.linalg.svd(C)
    d = np.linalg.det(Vt.T) * np.linalg.det(U)
    if d < 0:
        S[-1] = -S[-1]
        Vt[-1, :] = -Vt[-1, :]
    R = np.dot(Vt.T, U.T)
    coords2_aligned = np.dot(coords2_centered, R)
    diff = coords1_centered - coords2_aligned
    return np.sqrt(np.sum(diff * diff) / coords1.shape[0])


# ==============================================================================
# 参数容器（API）
# ==============================================================================


class RefineOptions:
    """
    用于在模块间调用时传递参数的类 (模拟 argparse.Namespace)
    """

    def __init__(
        self,
        input_file,
        output=None,
        threshold=0.25,
        ewin=None,
        imag=None,
        noH=False,
        max_conformers=None,
        dedup_only=False,
        keep_all_topos=False,
        workers=1,
    ):
        self.input_file = input_file
        self.output = output
        self.threshold = threshold
        self.ewin = ewin
        self.imag = imag
        self.noH = noH
        self.max_conformers = max_conformers
        self.dedup_only = dedup_only
        self.keep_all_topos = keep_all_topos
        # ✅ 改进：workers 不超过 CPU 数量，避免过度并行
        cpu_count = multiprocessing.cpu_count()
        self.workers = max(1, min(workers, cpu_count))

        # 验证逻辑
        if self.output is None:
            base, _ = os.path.splitext(self.input_file)
            self.output = f"{base}_cleaned.xyz"


# ==============================================================================
# 工作函数与辅助逻辑
# ==============================================================================


def check_one_against_many(args):
    """工作函数：将一个候选构象与一个独特构象列表（快照）进行比较"""
    cand_data, unique_data_snapshot, rmsd_threshold = args
    cand_coords, cand_pmi = cand_data
    if cand_coords.shape[0] == 0:
        return False, -1
    for unique_coords, unique_pmi, unique_id in unique_data_snapshot:
        pmi_diff = np.abs(cand_pmi - unique_pmi)
        pmi_tol = (unique_pmi + cand_pmi) * 0.5 * PMI_TOLERANCE_FACTOR
        if np.any(pmi_diff > pmi_tol + 1e-4):
            continue
        rmsd = fast_rmsd(cand_coords, unique_coords)
        if rmsd < rmsd_threshold:
            return True, unique_id
    return False, -1


def read_xyz_file(filepath):
    """读取 XYZ 文件（统一走 io_xyz），返回 refine 内部使用的 frame 结构。"""
    if not os.path.exists(filepath):
        return []

    from ...core.io import read_xyz_file as io_read_xyz_file

    frames = io_read_xyz_file(filepath, parse_metadata=True)
    out = []
    for frame_idx, fr in enumerate(frames):
        meta = fr.get("metadata", {}) or {}

        # 能量：优先 G（Gibbs），其次 E/Energy；缺失则 inf
        energy_key = "G" if "G" in meta else ("E" if "E" in meta else ("Energy" if "Energy" in meta else None))
        energy_val = meta.get("G", meta.get("E", meta.get("Energy")))
        try:
            energy = float(energy_val)
        except Exception:
            energy = float("inf")

        # 虚频数：兼容 Imag=1 / num_imag_freqs=1（io_xyz 会把数字解析成 float）
        imag_val = meta.get("num_imag_freqs", meta.get("Imag"))
        try:
            num_imag = int(imag_val) if imag_val is not None else None
        except Exception:
            num_imag = None

        # 额外元数据：过滤掉常见的主字段，剩余按原样保留
        skip = {"e", "g", "energy", "imag", "num_imag_freqs", "rank", "count", "de", "rmsd", "topo"}
        extra_data = {k: v for k, v in meta.items() if str(k).lower() not in skip}

        atoms = fr.get("atoms", []) or []
        coords = np.array(fr.get("coords", []) or [], dtype=np.float64)

        out.append(
            {
                "natoms": fr.get("natoms", len(atoms)),
                "comment": fr.get("comment", ""),
                "energy": energy,
                "energy_key": energy_key,
                "num_imag_freqs": num_imag,
                "extra_data": extra_data,
                "atoms": atoms,
                "original_atoms": atoms,
                "coords": coords,
                "original_index": fr.get("original_index", frame_idx),
            }
        )

    return out


def get_topology_hash_worker(args):
    """计算拓扑哈希值，支持缓存避免重复计算"""
    try:
        atoms, coords = args
        if len(atoms) == 0:
            return "empty"

        # 生成拓扑签名（快速指纹）用于缓存键
        atom_sig = tuple(sorted(atoms))

        # 使用 GaussView 共价半径数据
        radii = np.array([GV_COVALENT_RADII[get_element_atomic_number(a)] for a in atoms])
        delta = coords[:, np.newaxis, :] - coords[np.newaxis, :, :]
        dist_sq = np.sum(delta**2, axis=-1)
        thresh_sq = ((radii[:, np.newaxis] + radii[np.newaxis, :]) * BOND_SCALE_FACTOR) ** 2
        adj = (dist_sq < thresh_sq).astype(np.int8)
        np.fill_diagonal(adj, 0)

        desc = []
        for i in range(len(atoms)):
            neighs = sorted([atoms[k] for k in np.where(adj[i] == 1)[0]])
            desc.append(f"{atoms[i]}-({''.join(neighs)})")
        return hashlib.sha1("".join(sorted(desc)).encode()).hexdigest()[:10]
    except Exception:
        return "error"


def process_topology_group(frames_in_group, rmsd_threshold, heavy_atoms_only, workers):
    frames_in_group.sort(key=lambda x: x["energy"])
    unique_frames, report_data = [], []
    if not frames_in_group:
        return [], []

    # 预先计算 PMI 与重原子坐标
    for f in frames_in_group:
        coords = f["coords"]
        if heavy_atoms_only:
            mask = np.array([a != "H" for a in f["atoms"]])
            coords = coords[mask] if np.any(mask) else np.empty((0, 3))
        f["heavy_coords"] = coords
        f["pmi"] = get_pmi(coords)

    first = frames_in_group.pop(0)
    unique_frames.append(first)
    report_data.append(
        {"Input_Frame_ID": first["original_index"], "Status": "Kept", "Duplicate_Of_Input_ID": "-"}
    )

    candidates = frames_in_group
    if not candidates:
        return unique_frames, report_data

    BATCH_SIZE = min(len(candidates), max(100, workers * 20))
    with ProcessPoolExecutor(max_workers=workers) as executor:
        with create_progress() as progress:
            task_id = progress.add_task("[cyan]RMSD去重", total=len(candidates))
            while candidates:

                curr_batch = candidates[:BATCH_SIZE]
                candidates = candidates[BATCH_SIZE:]
                unique_snap = [
                    (u["heavy_coords"], u["pmi"], u["original_index"]) for u in unique_frames
                ]
                batch_data = [(c["heavy_coords"], c["pmi"]) for c in curr_batch]

                chunk = max(1, len(curr_batch) // (workers * 4) + 1)
                results = list(
                    executor.map(
                        check_one_against_many,
                        zip(batch_data, repeat(unique_snap), repeat(rmsd_threshold)),
                        chunksize=chunk,
                    )
                )

                newly_kept = []
                for i, cand in enumerate(curr_batch):
                    is_dup, mid = results[i]
                    if is_dup:
                        report_data.append(
                            {
                                "Input_Frame_ID": cand["original_index"],
                                "Status": "Removed (Duplicate)",
                                "Duplicate_Of_Input_ID": mid,
                            }
                        )
                    else:
                        # 与本批次新保留的构象做比较
                        is_intra_dup = False
                        if newly_kept:
                            args = (
                                (cand["heavy_coords"], cand["pmi"]),
                                [
                                    (k["heavy_coords"], k["pmi"], k["original_index"])
                                    for k in newly_kept
                                ],
                                rmsd_threshold,
                            )
                            if check_one_against_many(args)[0]:
                                is_intra_dup = True

                        if not is_intra_dup:
                            newly_kept.append(cand)
                            report_data.append(
                                {
                                    "Input_Frame_ID": cand["original_index"],
                                    "Status": "Kept",
                                    "Duplicate_Of_Input_ID": "-",
                                }
                            )

                unique_frames.extend(newly_kept)
                progress.advance(task_id, advance=len(curr_batch))


    return unique_frames, report_data


# ==============================================================================
# 核心入口逻辑
# ==============================================================================


def process_xyz(args):
    """
    执行去重与筛选的主逻辑
    Args:
        args: 可以是 argparse.Namespace 或 RefineOptions 对象
    """
    if not os.path.exists(args.input_file):
        console.print(f"[bold red]错误: 输入文件不存在: {args.input_file}[/]")
        return

    console.print(f"[*] Processing: {os.path.basename(args.input_file)}")
    console.print(f"    Workers: {args.workers} | RMSD Thresh: {args.threshold} | E-window: {args.ewin}")

    all_frames = read_xyz_file(args.input_file)
    if not all_frames:
        return

    # 1. 拓扑分析
    console.print("[*] Analyzing topology...")
    topologies = defaultdict(list)
    atom_coord_pairs = [(f["atoms"], f["coords"]) for f in all_frames]

    with ProcessPoolExecutor(max_workers=args.workers) as executor:
        chunk = max(1, len(all_frames) // (args.workers * 4) + 1)
        
        topo_hashes = []
        with create_progress() as progress:
            task_id = progress.add_task("拓扑哈希", total=len(all_frames))
            for res in executor.map(get_topology_hash_worker, atom_coord_pairs, chunksize=chunk):
                topo_hashes.append(res)
                progress.advance(task_id)

    for i, h in enumerate(topo_hashes):
        all_frames[i]["topology_hash"] = h
        topologies[h].append(all_frames[i])

    if not topologies:
        return

    # 2. 确定主拓扑
    main_topo_hash = max(topologies, key=lambda k: len(topologies[k]))
    frames_to_process = all_frames if args.keep_all_topos else topologies[main_topo_hash]

    console.print(
        f"[*] Topology: Found {len(topologies)}. Mode: {'Keep All' if args.keep_all_topos else 'Main Only'} "
        f"({len(frames_to_process)} confs)"
    )

    # 3. 筛选 (能量/虚频)
    if args.imag is not None:
        frames_to_process = [f for f in frames_to_process if f.get("num_imag_freqs") == args.imag]
        console.print(f"    -> Filter Imag={args.imag}: {len(frames_to_process)} remaining")

    if args.ewin is not None and not args.dedup_only:
        min_e = min(f["energy"] for f in frames_to_process)
        limit = min_e + args.ewin / HARTREE_TO_KCALMOL
        frames_to_process = [f for f in frames_to_process if f["energy"] <= limit]
        console.print(f"    -> Filter E-win={args.ewin} kcal/mol: {len(frames_to_process)} remaining")

    # 4. RMSD 去重
    if frames_to_process:
        final_unique, report_data = process_topology_group(
            frames_to_process, args.threshold, args.noH, args.workers
        )
    else:
        final_unique, report_data = [], []

    if not final_unique:
        print("[!] 筛选后无构象保留。")
        return

    # 5. 统计与输出
    final_unique.sort(key=lambda x: x["energy"])
    global_min = final_unique[0]["energy"]

    # 重新计算 Count (基于处理过的构象)
    report_map = {r["Input_Frame_ID"]: r for r in report_data}
    counts = defaultdict(int)
    for f in frames_to_process:
        curr = f["original_index"]
        path = {curr}
        entry = report_map.get(curr)
        while entry and entry.get("Status") == "Removed (Duplicate)":
            dup_id = entry.get("Duplicate_Of_Input_ID")
            if dup_id in path:
                break
            path.add(dup_id)
            curr = dup_id
            entry = report_map.get(curr)
        if entry and entry.get("Status") == "Kept":
            counts[curr] += 1

    for f in final_unique:
        f["count"] = counts.get(f["original_index"], 1)
        f["rmsd_to_min"] = (
            fast_rmsd(f["heavy_coords"], final_unique[0]["heavy_coords"])
            if len(final_unique) > 0
            else 0
        )

    if args.max_conformers and len(final_unique) > args.max_conformers:
        final_unique = final_unique[: args.max_conformers]

    console.print(f"[*] Post-processing results...", style="dim")
    with open(args.output, "w") as f:
        for i, frame in enumerate(final_unique, 1):
            de = (frame["energy"] - global_min) * HARTREE_TO_KCALMOL
            imag_val = frame.get("num_imag_freqs")
            extra_items = []
            emit_g = str(frame.get("energy_key") or "").upper() == "G"
            for k, v in frame.get("extra_data", {}).items():
                if str(k).lower() == "tsatoms":
                    continue
                # 输出精简：当已经有 G=... 时，不再输出与 Gibbs 合成相关的中间字段。
                if emit_g and str(k) in {"G_corr", "E_sp", "E_includes_gcorr"}:
                    continue
                extra_items.append(f"{k}={v}")
            extra = " | ".join(extra_items)

            # 精简输出：保留 Rank/E/DE；Imag 仅在有值时显示；不再输出 Count/RMSD/Topo/TSAtoms
            label = "G" if emit_g else "E"
            line = f"Rank={i} | {label}={frame['energy']:.8f} | DE={de:.2f} kcal/mol"
            if imag_val is not None:
                line += f" | Imag={imag_val}"
            if extra:
                line += " | " + extra

            f.write(f"{frame['natoms']}\n{line}\n")
            for a, c in zip(frame["original_atoms"], frame["coords"]):
                f.write(f"{a:<4s} {c[0]:12.8f} {c[1]:12.8f} {c[2]:12.8f}\n")
    
    console.print(f"   [green]Refined:[/green] {args.output}")



# ==============================================================================
# 命令行入口
# ==============================================================================


def main():
    """命令行入口函数"""
    try:
        multiprocessing.set_start_method("fork")
    except Exception as e:
        logger.debug(f"设置 multiprocessing start method 失败: {e}")

    parser = argparse.ArgumentParser(description="ConfFlow Refine (v1.0) - 构象后处理工具")
    parser.add_argument("input_file", help="输入XYZ文件")
    parser.add_argument("-o", "--output", help="输出文件")
    parser.add_argument("-t", "--threshold", type=float, default=0.25, help="RMSD阈值 (默认 0.25)")
    parser.add_argument("--ewin", type=float, help="能量窗口 (kcal/mol)")
    parser.add_argument("--imag", type=int, help="保留的虚频数")
    parser.add_argument("--noH", action="store_true", help="RMSD忽略氢")
    parser.add_argument("-n", "--max-conformers", type=int, help="最大输出数量")
    parser.add_argument("--dedup-only", action="store_true", help="仅去重")
    parser.add_argument("--keep-all-topos", action="store_true", help="保留所有拓扑")
    parser.add_argument(
        "-w",
        "--workers",
        type=int,
        default=max(1, multiprocessing.cpu_count() - 2),
        help="并行核心数",
    )

    args = parser.parse_args()

    # 如果未指定输出文件，自动生成
    if args.output is None:
        base, _ = os.path.splitext(args.input_file)
        args.output = f"{base}_cleaned.xyz"

    process_xyz(args)


if __name__ == "__main__":
    main()
