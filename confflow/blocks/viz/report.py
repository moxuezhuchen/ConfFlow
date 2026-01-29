#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
ConfFlow Viz - 可视化报告生成器 (v1.0)
功能: 生成构象分析 HTML 报告 (能量分布, Boltzmann 权重, 工作流统计)
架构: 模块化设计 (Library & Script)
"""

import logging
import os
import argparse
import json
import math
import sqlite3
from datetime import datetime
from typing import List, Dict, Optional, Tuple

from ...core.io import read_xyz_file

# --- 常量定义 ---
HARTREE_TO_KCALMOL = 627.509
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


def generate_workflow_section(stats: Dict) -> str:
    """生成工作流统计 HTML 片段"""
    if not stats:
        return ""

    steps = stats.get("steps", [])
    total_duration = stats.get("total_duration_seconds", 0)
    initial_confs = stats.get("initial_conformers", 0)
    final_confs = stats.get("final_conformers", 0)

    # 如果 final_conformers 缺失，尝试从最后一步推导
    if final_confs == 0 and steps:
        final_confs = steps[-1].get("output_conformers", 0)

    # 步骤表格行
    step_rows = ""

    def _count_failed_from_step_output(output_xyz: str) -> Optional[int]:
        try:
            if not output_xyz:
                return None
            step_dir = os.path.dirname(output_xyz)
            db_path = os.path.join(step_dir, "work", "results.db")
            if not os.path.exists(db_path):
                return None
            con = sqlite3.connect(db_path)
            try:
                cur = con.cursor()
                cur.execute("select count(*) from task_results where status='failed'")
                row = cur.fetchone()
                if not row:
                    return 0
                return int(row[0])
            finally:
                con.close()
        except Exception:
            return None

    for step in steps:
        name = step.get("name", "Unknown")
        stype = step.get("type", "")
        status = step.get("status", "unknown")
        inp = step.get("input_conformers", 0)
        out = step.get("output_conformers", 0)
        failed = step.get("failed_conformers", None)
        if failed is None and stype in {"calc", "task"}:
            failed = _count_failed_from_step_output(step.get("output_xyz") or "")
        dur = step.get("duration_seconds", 0)

        status_icon = "✅" if status == "completed" else ("⏭️" if status == "skipped" else "❌")

        change = out - inp
        change_cls = "positive" if change > 0 else ("negative" if change < 0 else "neutral")
        change_str = f"+{change}" if change > 0 else str(change)
        if inp == 0 and out == 0:
            change_str = "-"

        failed_str = "-" if failed is None else str(int(failed))

        step_rows += f"""
        <tr>
            <td><span class="step-badge">{step.get('index', 0)}</span></td>
            <td><strong>{name}</strong><br><small class="text-muted">{stype}</small></td>
            <td>{status_icon} {status}</td>
            <td>{inp}</td>
            <td>{out}</td>
            <td>{failed_str}</td>
            <td><span class="change-{change_cls}">{change_str}</span></td>
            <td>{format_duration(dur)}</td>
        </tr>"""

    return f"""
    <div class="workflow-section">
        <h2>📈 工作流统计</h2>
        <div class="workflow-summary">
            <div class="summary-card"><div>🔄</div><div><div class="val">{len(steps)}</div><div class="lbl">Steps</div></div></div>
            <div class="summary-card"><div>⏱️</div><div><div class="val">{format_duration(total_duration)}</div><div class="lbl">Time</div></div></div>
            <div class="summary-card"><div>📥</div><div><div class="val">{initial_confs or stats.get('initial_conformers', 0)}</div><div class="lbl">Input</div></div></div>
            <div class="summary-card"><div>📤</div><div><div class="val">{final_confs}</div><div class="lbl">Final</div></div></div>
        </div>
        <table class="step-table">
            <thead><tr><th>#</th><th>Step</th><th>Status</th><th>In</th><th>Out</th><th>Failed</th><th>Δ</th><th>Time</th></tr></thead>
            <tbody>{step_rows}</tbody>
        </table>
    </div>
    """


def generate_html_report(
    conformers: List[Dict],
    output_file: str,
    temperature: float = 298.15,
    stats: Optional[Dict] = None,
):
    """生成完整 HTML 报告"""
    energies = _extract_energies(conformers)

    # 计算相对能量和 Boltzmann 权重
    valid_energies = [e for e in energies if e is not None and e != float("inf")]
    min_energy = min(valid_energies) if valid_energies else 0.0

    rel_energies = []
    for e in energies:
        if e is None or e == float("inf"):
            rel_energies.append(999.9)
        else:
            rel_energies.append((e - min_energy) * HARTREE_TO_KCALMOL)

    boltzmann_weights = calculate_boltzmann_weights(energies, temperature)
    workflow_html = generate_workflow_section(stats) if stats else ""

    # 表格显示按 Gibbs 能量排序（避免输入文件顺序与展示能量不一致导致“看起来没排序”）
    def _sort_key(i: int) -> float:
        e = energies[i] if i < len(energies) else None
        if e is None or e == float("inf"):
            return float("inf")
        try:
            return float(e)
        except Exception:
            return float("inf")

    order = sorted(range(len(conformers)), key=_sort_key)

    # HTML 模板
    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <title>ConfFlow Report</title>
    <style>
        :root {{ --primary: #667eea; --secondary: #764ba2; --bg: #f5f7fa; --text: #2d3748; }}
        body {{ font-family: system-ui, -apple-system, sans-serif; background: var(--bg); color: var(--text); margin: 0; padding: 20px; line-height: 1.5; }}
        .container {{ max-width: 1200px; margin: 0 auto; background: white; border-radius: 12px; box-shadow: 0 4px 6px rgba(0,0,0,0.05); overflow: hidden; }}
        header {{ background: linear-gradient(135deg, var(--primary), var(--secondary)); color: white; padding: 40px; }}
        h1 {{ margin: 0; font-size: 2.2rem; }}
        .meta {{ opacity: 0.9; margin-top: 10px; }}
        
        .section {{ padding: 30px; border-bottom: 1px solid #e2e8f0; }}
        .summary-grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 20px; }}
        .card {{ background: #f8fafc; padding: 20px; border-radius: 8px; border: 1px solid #e2e8f0; }}
        .card-label {{ color: #718096; font-size: 0.875rem; text-transform: uppercase; letter-spacing: 0.05em; }}
        .card-val {{ font-size: 1.5rem; font-weight: 700; color: #2d3748; margin-top: 5px; }}
        
        table {{ width: 100%; border-collapse: collapse; margin-top: 20px; font-size: 0.95rem; }}
        th {{ background: #f1f5f9; text-align: left; padding: 12px 16px; font-weight: 600; color: #4a5568; }}
        td {{ padding: 12px 16px; border-bottom: 1px solid #e2e8f0; }}
        tr:hover {{ background: #f8fafc; }}
        
        .rank-badge {{ background: var(--primary); color: white; padding: 4px 10px; border-radius: 999px; font-weight: 700; font-size: 0.85rem; }}
        .bar-container {{ width: 100px; height: 6px; background: #e2e8f0; border-radius: 3px; overflow: hidden; display: inline-block; vertical-align: middle; margin-right: 8px; }}
        .bar-fill {{ height: 100%; background: var(--secondary); }}
        
        /* Workflow Styles */
        .workflow-summary {{ display: flex; gap: 20px; margin-bottom: 30px; flex-wrap: wrap; }}
        .summary-card {{ display: flex; align-items: center; gap: 15px; background: white; padding: 15px 25px; border-radius: 8px; box-shadow: 0 2px 4px rgba(0,0,0,0.05); flex: 1; border: 1px solid #edf2f7; }}
        .summary-card .val {{ font-size: 1.25rem; font-weight: bold; }}
        .summary-card .lbl {{ font-size: 0.85rem; color: #718096; }}
        .step-badge {{ background: #edf2f7; color: #4a5568; width: 24px; height: 24px; display: flex; align-items: center; justify-content: center; border-radius: 50%; font-weight: bold; font-size: 0.8rem; }}
        .change-positive {{ color: #48bb78; font-weight: bold; }}
        .change-negative {{ color: #f56565; font-weight: bold; }}
        .change-neutral {{ color: #cbd5e0; }}
        .resource-bar {{ width: 60px; height: 4px; background: #edf2f7; border-radius: 2px; margin-bottom: 4px; overflow: hidden; }}
        .cpu-bar {{ height: 100%; background: #4299e1; }}
        .mem-bar {{ height: 100%; background: #ed8936; }}
        .text-muted {{ color: #a0aec0; font-size: 0.85rem; }}
    </style>
</head>
<body>
    <div class="container">
        <header>
            <h1>🧪 ConfFlow Analysis Report</h1>
            <div class="meta">Generated: {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}</div>
        </header>
        
        {workflow_html}
        
        <div class="section">
            <h2>📊 Conformer Analysis</h2>
            <div class="summary-grid">
                <div class="card">
                    <div class="card-label">Conformers</div>
                    <div class="card-val">{len(conformers)}</div>
                </div>
                <div class="card">
                    <div class="card-label">Energy Range</div>
                    <div class="card-val">{max(rel_energies) if rel_energies else 0:.2f} <small>kcal/mol</small></div>
                </div>
                <div class="card">
                    <div class="card-label">Temperature</div>
                    <div class="card-val">{temperature} K</div>
                </div>
                <div class="card">
                    <div class="card-label">Lowest Energy</div>
                    <div class="card-val">{min_energy:.6f} <small>Ha</small></div>
                </div>
            </div>
            
            <table>
                <thead>
                    <tr>
                        <th>Rank</th>
                        <th>Gibbs Energy (Ha)</th>
                        <th>ΔG (kcal/mol)</th>
                        <th>Boltzmann Weight</th>
                        <th>Imag</th>
                        <th>TSBond (Å)</th>
                    </tr>
                </thead>
                <tbody>
"""

    for display_rank, idx in enumerate(order, start=1):
        conf = conformers[idx]
        meta = conf["metadata"]
        energy = energies[idx] if idx < len(energies) else None
        de = rel_energies[idx] if idx < len(rel_energies) else 999.9
        imag = meta.get("Imag", meta.get("num_imag_freqs", "-"))
        tsbond = meta.get("TSBond", meta.get("ts_bond_length", "-"))
        boltz = boltzmann_weights[idx] if idx < len(boltzmann_weights) else 0.0

        # 格式化能量
        e_str = f"{float(energy):.6f}" if energy is not None and energy != float("inf") else "N/A"

        # 格式化 TSBond（健壮处理各种类型）
        if tsbond == "-" or tsbond is None:
            tsbond_str = "-"
        else:
            try:
                tsbond_str = f"{float(tsbond):.4f}"
            except (ValueError, TypeError):
                tsbond_str = str(tsbond)

        html += f"""
                    <tr>
                        <td><span class="rank-badge">#{int(display_rank)}</span></td>
                        <td>{e_str}</td>
                        <td>{de:.2f}</td>
                        <td>
                            <div class="bar-container"><div class="bar-fill" style="width: {boltz}%"></div></div>
                            {boltz:.1f}%
                        </td>
                        <td>{imag}</td>
                        <td>{tsbond_str}</td>
                    </tr>"""

    html += """
                </tbody>
            </table>
        </div>
    </div>
</body>
</html>
"""

    with open(output_file, "w", encoding="utf-8") as f:
        f.write(html)


def generate_text_report(
    conformers: List[Dict],
    temperature: float = 298.15,
    stats: Optional[Dict] = None,
) -> str:
    """生成纯文本报告（用于合并到 txt 输出）。"""
    lines: List[str] = []
    lines.append("=== ConfFlow Analysis Report ===")
    lines.append(f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

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

    if stats:
        steps = stats.get("steps", [])
        total_duration = stats.get("total_duration_seconds", 0)
        initial_confs = stats.get("initial_conformers", 0)
        final_confs = stats.get("final_conformers", 0)
        if final_confs == 0 and steps:
            final_confs = steps[-1].get("output_conformers", 0)

        lines.append("")
        lines.append("== Workflow Summary ==")
        lines.append(
            f"Steps: {len(steps)}  Time: {format_duration(total_duration)}  Input: {initial_confs}  Final: {final_confs}"
        )
        lines.append(
            f"{'Step':>4} {'Name':<12} {'Type':<7} {'Status':<10} {'In':>5} {'Out':>5} {'Failed':>6} {'Δ':>4} {'Time':>8}"
        )
        for step in steps:
            name = str(step.get("name", "Unknown"))[:12]
            stype = str(step.get("type", ""))[:7]
            status = str(step.get("status", "unknown"))[:10]
            inp = step.get("input_conformers", 0)
            out = step.get("output_conformers", 0)
            failed = step.get("failed_conformers", None)
            dur = step.get("duration_seconds", 0)
            change = out - inp
            change_str = f"+{change}" if change > 0 else str(change)
            if inp == 0 and out == 0:
                change_str = "-"
            failed_str = "-" if failed is None else str(int(failed))
            lines.append(
                f"{step.get('index', 0):>4} {name:<12} {stype:<7} {status:<10} {inp:>5} {out:>5} {failed_str:>6} {change_str:>4} {format_duration(dur):>8}"
            )

    lines.append("")
    lines.append("== Conformer Analysis ==")
    lines.append(
        f"Conformers: {len(conformers)}  Energy Range: {max(rel_energies) if rel_energies else 0:.2f} kcal/mol  Temperature: {temperature} K  Lowest Energy: {min_energy:.6f} Ha"
    )
    if stats:
        lowest = stats.get("lowest_conformer") or {}
        cid = lowest.get("cid")
        energy = lowest.get("energy")
        xyz_path = lowest.get("xyz_path")
        if cid or energy is not None or xyz_path:
            energy_str = f"{energy:.6f} Ha" if energy is not None else "N/A"
            lines.append(
                f"Lowest Conformer: CID={cid or '-'}  Energy={energy_str}  XYZ={xyz_path or '-'}"
            )
    lines.append(
        f"{'Rank':>4} {'Gibbs(Ha)':>12} {'ΔG(kcal/mol)':>12} {'Boltz(%)':>8} {'Imag':>5} {'TSBond(Å)':>10}"
    )

    for display_rank, idx in enumerate(order, start=1):
        conf = conformers[idx]
        meta = conf.get("metadata", {})
        energy = energies[idx] if idx < len(energies) else None
        de = rel_energies[idx] if idx < len(rel_energies) else 999.9
        imag = meta.get("Imag", meta.get("num_imag_freqs", "-"))
        tsbond = meta.get("TSBond", meta.get("ts_bond_length", "-"))
        boltz = boltzmann_weights[idx] if idx < len(boltzmann_weights) else 0.0

        e_str = f"{float(energy):.6f}" if energy is not None and energy != float("inf") else "N/A"
        if tsbond == "-" or tsbond is None:
            tsbond_str = "-"
        else:
            try:
                tsbond_str = f"{float(tsbond):.4f}"
            except (ValueError, TypeError):
                tsbond_str = str(tsbond)

        lines.append(
            f"{display_rank:>4} {e_str:>12} {de:>12.2f} {boltz:>8.1f} {str(imag):>5} {tsbond_str:>10}"
        )

    return "\n".join(lines)


# ==============================================================================
# CLI Entry Point
# ==============================================================================


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="ConfFlow Viz (v1.0)")
    parser.add_argument("input_xyz", help="Input XYZ file")
    parser.add_argument("-o", "--output", default="report.html", help="Output HTML file")
    parser.add_argument("-t", "--temperature", type=float, default=298.15, help="Temperature (K)")
    parser.add_argument("--stats", help="Path to workflow_stats.json")

    args = parser.parse_args(argv)

    if not os.path.exists(args.input_xyz):
        print(f"Error: File not found: {args.input_xyz}")
        return 1

    confs = parse_xyz_file(args.input_xyz)
    if not confs:
        print("No conformers found.")
        return 1

    stats_data = None
    if args.stats and os.path.exists(args.stats):
        try:
            with open(args.stats, "r", encoding="utf-8") as f:
                stats_data = json.load(f)
        except Exception as e:
            import logging

            logger = logging.getLogger("confflow.viz")
            logger.warning(f"无法读取统计文件 {args.stats}: {e}")

    generate_html_report(confs, args.output, args.temperature, stats=stats_data)
    print(f"✅ Report generated: {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
