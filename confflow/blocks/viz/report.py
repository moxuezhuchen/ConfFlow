#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
ConfFlow Viz - 可视化报告生成器 (v1.0)
功能: 生成构象分析 HTML 报告 (能量分布, Boltzmann 权重, 工作流统计)
架构: 模块化设计 (Library & Script)
"""

import logging
import os
import math
from datetime import datetime
from typing import List, Dict, Optional, Tuple

from ...core.io import read_xyz_file
from ...calc.constants import HARTREE_TO_KCALMOL

# --- 常量定义 ---
KB_KCALMOL = 0.001987204  # Boltzmann constant in kcal/(mol·K)

# ==============================================================================
# 核心逻辑
# ==============================================================================

logger = logging.getLogger("confflow.viz")


def parse_xyz_file(filepath: str) -> List[Dict]:
    """解析 XYZ 文件并提取构象元数据（统一走 io_xyz）。"""
    if not os.path.exists(filepath):
        logger.debug(f"XYZ 文件不存在: {filepath}")
        return []
    try:
        return read_xyz_file(filepath, parse_metadata=True)
    except (IOError, OSError) as e:
        logger.warning(f"读取 XYZ 文件失败: {filepath}, 原因: {e}")
        return []
    except ValueError as e:
        logger.warning(f"解析 XYZ 文件格式错误: {filepath}, 原因: {e}")
        return []


def calculate_boltzmann_weights(energies: List[float], temperature: float = 298.15) -> List[float]:
    """计算 Boltzmann 权重"""
    if not energies:
        return []

    # 过滤无效能量
    valid_energies = [e for e in energies if e is not None and e != float("inf")]
    if not valid_energies:
        return [0] * len(energies)

    min_energy = min(valid_energies)
    rel_energies = []

    for e in energies:
        if e is None or e == float("inf"):
            rel_energies.append(9999.9)
        else:
            rel_energies.append((e - min_energy) * HARTREE_TO_KCALMOL)

    # 计算 Boltzmann 因子
    beta = 1.0 / (KB_KCALMOL * temperature)
    boltzmann_factors = []
    for de in rel_energies:
        if de < 50:  # 能量过高贡献为0
            boltzmann_factors.append(math.exp(-beta * de))
        else:
            boltzmann_factors.append(0.0)

    # 归一化为百分比
    total = sum(boltzmann_factors)
    if total > 0:
        return [bf / total * 100 for bf in boltzmann_factors]
    return [0] * len(energies)


def format_duration(seconds: float) -> str:
    """格式化时间显示"""
    if seconds < 60:
        return f"{seconds:.1f}s"
    elif seconds < 3600:
        return f"{seconds/60:.1f}min"
    else:
        return f"{seconds/3600:.1f}h"


def _extract_energies(conformers: List[Dict]) -> List[Optional[float]]:
    """从构象元数据中提取能量（优先 Gibbs）。"""
    energies: List[Optional[float]] = []

    for c in conformers:
        meta = c.get("metadata") or {}
        g = meta.get("G")
        e = meta.get("E", meta.get("Energy"))
        e_sp = meta.get("E_sp")
        g_corr = meta.get("G_corr")
        includes = meta.get("E_includes_gcorr")

        val: Optional[float] = None
        try:
            # 新约定：若存在 G，则直接使用（不再叠加/传递 G_corr）。
            if g is not None:
                val = float(g)
                energies.append(val)
                continue

            e_f = float(e) if e is not None else None
            e_sp_f = float(e_sp) if e_sp is not None else None
            g_corr_f = float(g_corr) if g_corr is not None else None
            includes_flag = bool(includes) if includes is not None else False

            # 兼容旧文件：很多 calc/refine 产物会同时携带 E/Energy 与 G_corr，
            # 且其中 E/Energy 已经是 Gibbs（E_sp + G_corr）。这些文件可能没有 E_includes_gcorr 标记。
            # 经验规则：
            # - calc 输出通常使用 Energy=...（metadata 中会保留 Energy 键）
            # - refine 输出通常包含 DE=... / Rank=...
            # 这两类情况下若没有显式 E_sp，则默认视为“已包含 G_corr”。
            if not includes_flag and e_sp_f is None and g_corr_f is not None:
                if ("Energy" in meta) or ("DE" in meta) or ("Rank" in meta):
                    includes_flag = True

            if e_sp_f is not None and g_corr_f is not None:
                val = e_sp_f + g_corr_f
            elif e_f is not None:
                val = e_f
                if g_corr_f is not None and not includes_flag:
                    # 兼容旧行为：默认 E 视作未矫正能量
                    val += g_corr_f
        except Exception:
            val = None
        energies.append(val)

    return energies


def get_lowest_energy_conformer(
    conformers: List[Dict],
) -> Tuple[Optional[Dict], Optional[float], Optional[int]]:
    """获取最低能量构象及其能量与索引。"""
    if not conformers:
        return None, None, None

    energies = _extract_energies(conformers)
    valid = [(i, e) for i, e in enumerate(energies) if e is not None and e != float("inf")]
    if not valid:
        return None, None, None

    idx, e_min = min(valid, key=lambda x: x[1])
    return conformers[idx], float(e_min), idx


def generate_text_report(
    conformers: List[Dict],
    temperature: float = 298.15,
    stats: Optional[Dict] = None,
) -> str:
    """生成纯文本报告（新格式：美化输出）。"""
    from ...core.console import LINE_WIDTH
    DOUBLE_LINE = "=" * LINE_WIDTH
    SINGLE_LINE = "─" * LINE_WIDTH
    
    lines: List[str] = []
    
    # === 最终报告头部 ===
    lines.append("")
    lines.append(DOUBLE_LINE)
    lines.append(f"{'FINAL REPORT':^{LINE_WIDTH}}")
    finished_str = 'Finished: ' + datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    lines.append(f"{finished_str:^{LINE_WIDTH}}")
    
    if stats:
        total_duration = stats.get("total_duration_seconds", 0)
        time_str = 'Total Time: ' + format_duration(total_duration)
        lines.append(f"{time_str:^{LINE_WIDTH}}")
    lines.append(DOUBLE_LINE)

    if not conformers:
        lines.append("No conformers found.")
        return "\n".join(lines)

    energies = _extract_energies(conformers)
    valid_energies = [e for e in energies if e is not None and e != float("inf")]
    min_energy = min(valid_energies) if valid_energies else 0.0

    rel_energies = []
    for e in energies:
        if e is None or e == float("inf"):
            rel_energies.append(999.9)
        else:
            rel_energies.append((e - min_energy) * HARTREE_TO_KCALMOL)

    boltzmann_weights = calculate_boltzmann_weights(energies, temperature)

    def _sort_key(i: int) -> float:
        e = energies[i] if i < len(energies) else None
        if e is None or e == float("inf"):
            return float("inf")
        try:
            return float(e)
        except Exception:
            return float("inf")

    order = sorted(range(len(conformers)), key=_sort_key)

    # === WORKFLOW SUMMARY ===
    if stats:
        steps = stats.get("steps", [])
        total_duration = stats.get("total_duration_seconds", 0)
        initial_confs = stats.get("initial_conformers", 0)
        final_confs = stats.get("final_conformers", 0)
        if final_confs == 0 and steps:
            final_confs = steps[-1].get("output_conformers", 0)

        lines.append("")
        lines.append("WORKFLOW SUMMARY")
        lines.append(SINGLE_LINE)
        
        # 步骤表格
        header = f"  {'Step':>4}   {'Name':<10}  {'Type':<8}  {'Status':<10}  {'In':>5}  {'Out':>5}  {'Failed':>6}  {'Time':>10}"
        lines.append(header)
        
        for step in steps:
            idx = step.get('index', 0)
            name = str(step.get("name", "Unknown"))[:10]
            stype = str(step.get("type", ""))[:8]
            status = str(step.get("status", "unknown"))[:10]
            inp = step.get("input_conformers", 0)
            out = step.get("output_conformers", 0)
            failed = step.get("failed_conformers", None)
            dur = step.get("duration_seconds", 0)
            
            failed_str = "-" if failed is None else str(int(failed))
            dur_str = format_duration(dur)
            
            line = f"  {idx:>4}   {name:<10}  {stype:<8}  {status:<10}  {inp:>5}  {out:>5}  {failed_str:>6}  {dur_str:>10}"
            lines.append(line)
        
        lines.append(SINGLE_LINE)
        lines.append(f"  Total: {initial_confs} → {final_confs} conformers")

    # === CONFORMER ANALYSIS ===
    lines.append("")
    lines.append("CONFORMER ANALYSIS")
    lines.append(SINGLE_LINE)
    lines.append(
        f"  Conformers: {len(conformers)}    Range: {max(rel_energies) if rel_energies else 0:.2f} kcal/mol    T: {temperature} K"
    )
    lines.append(f"  Lowest Energy: {min_energy:.6f} Ha")
    lines.append("")
    
    # 构象表格
    header = f"  {'Rank':>4}  {'Energy (Ha)':>14}  {'ΔG (kcal)':>11}  {'Pop (%)':>9}  {'Imag':>6}  {'TSBond':>10}  {'CID':>10}"
    lines.append(header)

    for display_rank, idx in enumerate(order, start=1):
        conf = conformers[idx]
        meta = conf.get("metadata", {})
        energy = energies[idx] if idx < len(energies) else None
        de = rel_energies[idx] if idx < len(rel_energies) else 999.9
        imag = meta.get("Imag", meta.get("num_imag_freqs", "-"))
        tsbond = meta.get("TSBond", meta.get("ts_bond_length", "-"))
        cid = meta.get("CID", "-")
        boltz = boltzmann_weights[idx] if idx < len(boltzmann_weights) else 0.0

        e_str = f"{float(energy):.7f}" if energy is not None and energy != float("inf") else "N/A"
        if tsbond == "-" or tsbond is None:
            tsbond_str = "-"
        else:
            try:
                tsbond_str = f"{float(tsbond):.4f}"
            except (ValueError, TypeError):
                tsbond_str = str(tsbond)

        line = f"  {display_rank:>4}  {e_str:>14}  {de:>11.2f}  {boltz:>9.1f}  {str(imag):>6}  {tsbond_str:>10}  {str(cid):>10}"
        lines.append(line)

    lines.append(DOUBLE_LINE)
    return "\n".join(lines)

