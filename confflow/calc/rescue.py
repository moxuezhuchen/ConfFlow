#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""TS 失败后的 scan 救援逻辑。

除执行救援外，还会输出 scan 诊断信息：
- 终端打印键长-能量关系表（标记最高点 MAX）
- 写入 <work_dir>/scan/scan_table.txt，并随备份目录一起保存
"""

from __future__ import annotations

import os
import sys
import re
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from .analysis import (
    _bond_length_from_xyz_lines,
    _keyword_requests_freq,
    _parse_ts_bond_atoms,
    _coords_array_from_xyz_lines,
)
from .core import get_itask, logger, parse_iprog
from .policies.gaussian import GaussianPolicy
from .components import executor

from ..blocks import refine

try:
    from ..confts import make_scan_keyword_from_ts_keyword  # type: ignore
except Exception:
    make_scan_keyword_from_ts_keyword = None  # type: ignore


def _coords_lines_to_xyz(coords_lines: List[str]):
    try:
        out = []
        for ln in coords_lines:
            p = ln.split()
            if len(p) < 4:
                return None
            sym = p[0]
            xyz = []
            for tok in reversed(p[1:]):
                try:
                    xyz.append(float(tok))
                except Exception:
                    continue
                if len(xyz) == 3:
                    break
            if len(xyz) != 3:
                return None
            z, y, x = xyz
            out.append((sym, float(x), float(y), float(z)))
        return out
    except Exception:
        return None


def _read_gaussian_input_coords(path: str) -> Optional[List[str]]:
    """从 Gaussian 输入文件（.gjf/.com）解析坐标块。

    目标：用于 TS 失败后的 scan 救援时，回到“失败 TS 的输入结构”作为起点。

    解析策略（尽量鲁棒）：
    - 找到第一行匹配 charge/multiplicity（两个整数）的行
    - 其后连续的非空行视为坐标行，直到遇到空行
    - 坐标行要求至少 4 列（元素符号 + x y z），其余列忽略
    """
    try:
        if not path or not os.path.exists(path):
            return None
        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            lines = [ln.rstrip("\n") for ln in f.readlines()]

        qm_idx = None
        for i, ln in enumerate(lines):
            s = ln.strip()
            if not s:
                continue
            if re.match(r"^\s*-?\d+\s+-?\d+\s*$", s):
                qm_idx = i
                break
        if qm_idx is None:
            return None

        coords: List[str] = []
        for ln in lines[qm_idx + 1 :]:
            if not ln.strip():
                break
            p = ln.split()
            if len(p) < 4:
                break
            coords.append(f"{p[0]} {p[1]} {p[2]} {p[3]}")

        return coords or None
    except Exception:
        return None


def _find_failed_ts_input_coords(wd: str, job: str, cfg: Dict[str, Any]) -> Optional[List[str]]:
    """寻找失败 TS 的输入结构坐标。

    TS 失败后，工作目录可能会被清理/移动到备份目录，因此这里同时支持：
    - work_dir 下的 job.gjf/job.com
    - backup_dir 下的 job.gjf/job.com
    """
    try:
        cand_paths: List[str] = [
            os.path.join(wd, f"{job}.gjf"),
            os.path.join(wd, f"{job}.com"),
        ]
        backup_dir = cfg.get("backup_dir")
        if backup_dir:
            cand_paths.extend(
                [
                    os.path.join(str(backup_dir), f"{job}.gjf"),
                    os.path.join(str(backup_dir), f"{job}.com"),
                ]
            )
        for p in cand_paths:
            coords = _read_gaussian_input_coords(p)
            if coords:
                return coords
        return None
    except Exception:
        return None


def _xyz_to_coords_lines(xyz) -> List[str]:
    return [f"{sym:<2s} {x: >12.6f} {y: >12.6f} {z: >12.6f}" for sym, x, y, z in xyz]


def _set_bond_length_on_coords(
    coords_lines: List[str], a1: int, a2: int, target: float
) -> Optional[List[str]]:
    """调整坐标使 a1-a2 键长为 target（仅移动 a2）。"""
    xyz = _coords_lines_to_xyz(coords_lines)
    if xyz is None:
        return None
    if a1 < 1 or a2 < 1 or a1 > len(xyz) or a2 > len(xyz) or a1 == a2:
        return None
    _, x1, y1, z1 = xyz[a1 - 1]
    sym2, x2, y2, z2 = xyz[a2 - 1]
    dx, dy, dz = x2 - x1, y2 - y1, z2 - z1
    r = (dx * dx + dy * dy + dz * dz) ** 0.5
    if r <= 1e-10:
        return None
    ux, uy, uz = dx / r, dy / r, dz / r
    new_x2, new_y2, new_z2 = (
        x1 + ux * float(target),
        y1 + uy * float(target),
        z1 + uz * float(target),
    )
    xyz[a2 - 1] = (sym2, new_x2, new_y2, new_z2)
    return _xyz_to_coords_lines(xyz)


def _write_ts_failure_report(work_dir: str, job_name: str, stage: str, message: str) -> None:
    """记录 TS 任务失败信息到报告文件"""
    try:
        os.makedirs(work_dir, exist_ok=True)
        path = os.path.join(work_dir, "ts_failures.txt")
        ts = datetime.now().isoformat(timespec="seconds")
        with open(path, "a", encoding="utf-8") as f:
            f.write(f"[{ts}] {job_name} | {stage} | {message}\n")
    except (IOError, OSError) as e:
        logger.warning(f"写入 TS failure report 失败（I/O错误）: {e}")
    except Exception as e:
        logger.warning(f"写入 TS failure report 异常: {e}")


def _write_scan_marker(scan_dir: str, job_name: str, message: str) -> None:
    """在 scan 目录写入诊断文件（确保即使主工作目录被清理也能在备份中追溯）。"""
    try:
        if not scan_dir:
            return
        os.makedirs(scan_dir, exist_ok=True)
        path = os.path.join(scan_dir, f"{job_name}.scan_error.txt")
        ts = datetime.now().isoformat(timespec="seconds")
        with open(path, "w", encoding="utf-8") as f:
            f.write(f"[{ts}] {job_name}: {message}\n")
    except Exception:
        return


def _render_scan_table(
    job: str,
    a1: int,
    a2: int,
    rows: List[Tuple[float, float, str]],
) -> str:
    """将 scan 点渲染为纯文本表格。

    rows: [(r_angstrom, energy_hartree, stage)] stage in {"coarse","fine"}
    """
    if not rows:
        return ""

    # 按键长排序，方便目测曲线
    rows_sorted = sorted(rows, key=lambda x: x[0])
    energies = [e for _, e, _ in rows_sorted]
    e_min = min(energies)
    e_max = max(energies)
    # Hartree -> kcal/mol
    hartree_to_kcal = 627.5094740631

    header = [
        f"TS rescue scan table: {job}",
        f"Bond: {a1}-{a2}",
        "Columns: idx  r(Å)     E(Eh)            dE(min)->kcal/mol   stage   note",
    ]

    lines: List[str] = []
    for idx, (r, e, stage) in enumerate(rows_sorted, start=1):
        de = (e - e_min) * hartree_to_kcal
        note = "MAX" if e == e_max else ""
        lines.append(
            f"{idx:>3d}  {r:>7.3f}  {e:>16.8f}  {de:>16.2f}   {stage:<5s}   {note}"
        )

    # 概览
    r_at_max = max(rows_sorted, key=lambda x: x[1])[0]
    r_at_min = min(rows_sorted, key=lambda x: x[1])[0]
    footer = [
        f"Summary: n={len(rows_sorted)}  r@Emax={r_at_max:.3f}Å  r@Emin={r_at_min:.3f}Å  ΔE(max-min)={(e_max-e_min)*hartree_to_kcal:.2f} kcal/mol",
    ]
    return "\n".join(header + lines + footer) + "\n"


def _emit_and_write_scan_table(
    wd: str,
    job: str,
    a1: int,
    a2: int,
    points: List[Tuple[float, float, List[str]]],
    fine_points: Optional[List[Tuple[float, float, List[str]]]] = None,
) -> None:
    """在终端输出并写入 scan 目录 scan_table.txt。"""
    try:
        rows: List[Tuple[float, float, str]] = [(r, e, "coarse") for r, e, _ in points]
        if fine_points:
            rows.extend((r, e, "fine") for r, e, _ in fine_points)

        # 去重：同一 r 可能既在 coarse 又在 fine 出现，优先保留 fine
        merged: Dict[float, Tuple[float, float, str]] = {}
        for r, e, stage in rows:
            key = round(float(r), 6)
            if key not in merged:
                merged[key] = (float(r), float(e), stage)
            else:
                # fine 覆盖 coarse
                if merged[key][2] != "fine" and stage == "fine":
                    merged[key] = (float(r), float(e), stage)

        table = _render_scan_table(job, a1, a2, list(merged.values()))
        if not table.strip():
            return

        # 终端输出：逐行 logger.info，避免多行日志被截断/折叠
        logger.info("📈 scan 键长-能量表格（见 scan_table.txt）")
        for ln in table.rstrip("\n").splitlines():
            logger.info(ln)

        scan_dir = os.path.join(wd, "scan")
        os.makedirs(scan_dir, exist_ok=True)
        out_path = os.path.join(scan_dir, "scan_table.txt")
        with open(out_path, "w", encoding="utf-8") as f:
            f.write(table)
    except Exception:
        return


def _ts_rescue_scan(task_info: Dict[str, Any], fail_reason: str) -> Optional[Dict[str, Any]]:
    """TS 失败后的 scan 救援（Gaussian）。"""
    cfg = task_info.get("config", {})
    job = task_info.get("job_name", "job")
    wd = task_info.get("work_dir", ".")

    prog_id = parse_iprog(cfg)
    if prog_id != 1:
        _write_ts_failure_report(
            wd, job, "rescue", f"未启用：当前仅支持 Gaussian 扫描（iprog={cfg.get('iprog')}）"
        )
        return None

    ts_bond_atoms = cfg.get("ts_bond_atoms", cfg.get("ts_bond"))
    pair = _parse_ts_bond_atoms(ts_bond_atoms)
    if not pair:
        _write_ts_failure_report(
            wd, job, "rescue", "未启用：缺少 ts_bond_atoms/ts_bond（无法定义扫描键）"
        )
        return None

    if make_scan_keyword_from_ts_keyword is None:
        _write_ts_failure_report(
            wd, job, "rescue", "未启用：无法导入 make_scan_keyword_from_ts_keyword"
        )
        return None

    scan_kw = make_scan_keyword_from_ts_keyword(str(cfg.get("keyword", "") or ""))
    if not scan_kw:
        _write_ts_failure_report(wd, job, "rescue", "未启用：scan keyword 为空")
        return None

    # scan 使用 confflow 原生 freeze（Gaussian 坐标第二列 -1）约束，不再需要 modredundant
    scan_kw = re.sub(r"(?i)\bmodredundant\b", " ", scan_kw)
    scan_kw = re.sub(r"\s+", " ", scan_kw).strip()

    # 起点结构：优先使用“失败 TS 的输入结构”（可能已被移动到 backup_dir）。
    base_coords = _find_failed_ts_input_coords(wd, job, cfg)
    if base_coords:
        logger.info("📌 scan 起点使用失败 TS 输入结构（work_dir/backup_dir）")

    if not base_coords:
        base_coords = task_info.get("coords")
    if not base_coords:
        _write_ts_failure_report(
            wd,
            job,
            "rescue",
            "未启用：缺少 TS 输入结构坐标（既未找到 job.gjf/job.com，也未提供 task_info['coords']）",
        )
        return None

    a1, a2 = pair
    r0 = _bond_length_from_xyz_lines(base_coords, a1, a2)
    if r0 is None:
        _write_ts_failure_report(wd, job, "rescue", "未启用：无法从输入结构计算键长")
        return None

    logger.info(
        f"🔄 TS 救援扫描启动: {job} | 键 {a1}-{a2} | 起始键长 {r0:.3f} Å | 原因: {fail_reason}"
    )

    coarse_step = float(cfg.get("scan_coarse_step", 0.1))
    fine_step = float(cfg.get("scan_fine_step", 0.02))
    uphill_limit = int(cfg.get("scan_uphill_limit", 10))
    max_steps = int(cfg.get("scan_max_steps", 60))
    fine_half_window = float(cfg.get("scan_fine_half_window", 0.1))

    # 初扫范围限制：每个方向最多 1 Å（并限制为最多 10 步，符合 0.1 Å/step 的常用配置）
    # 若某方向在该范围内能量严格单调上升，则认为未在合理范围内出现回落/峰值，直接放弃。
    try:
        coarse_span = 1.0
        coarse_k_max = int(round(coarse_span / float(coarse_step)))
        if coarse_k_max < 1:
            coarse_k_max = 1
        if coarse_k_max > 10:
            coarse_k_max = 10
    except Exception:
        coarse_k_max = 10

    def _ensure_has_opt(keyword: str) -> str:
        kw = (keyword or "").strip()
        if not kw:
            return ""
        if re.search(r"(?i)\bopt\b", kw):
            return kw
        return f"opt {kw}".strip()

    def run_constrained_opt(
        point_id: str, start_coords: List[str], target_r: float
    ) -> Tuple[Optional[float], Optional[List[str]], Optional[str]]:
        scan_cfg = dict(cfg)
        scan_cfg["itask"] = "opt"
        # scan keyword：基于 TS keyword 规则改写，但不引入 modredundant。
        base_kw = make_scan_keyword_from_ts_keyword(str(cfg.get("keyword", "") or ""))
        base_kw = re.sub(r"(?i)\bmodredundant\b", " ", base_kw)
        scan_kw_local = _ensure_has_opt(base_kw)

        # 移除 freq
        scan_kw_local = re.sub(
            r"(?i)(^|\s)freq\b(\s*=\s*\([^)]*\)|\s*\([^)]*\)|\s*=\s*[^\s]+)?", " ", scan_kw_local
        )
        scan_kw_local = re.sub(r"\s+", " ", scan_kw_local).strip()

        scan_cfg["keyword"] = scan_kw_local
        # 关键：使用 confflow 原生 freeze 机制（Gaussian 输入第二列 -1）冻结 TS 键两端原子。
        # 这样不需要 modredundant。
        scan_cfg["freeze"] = f"{a1},{a2}"
        # scan 点不做单点备份/清理：让外层 TS 任务的 handle_backups 一次性备份整个 scan 目录。
        scan_cfg["ibkout"] = 0

        adjusted = _set_bond_length_on_coords(start_coords, a1, a2, target_r)
        if adjusted is None:
            return None, None, "无法调整坐标到目标键长"

        scan_dir = os.path.join(wd, "scan")
        os.makedirs(scan_dir, exist_ok=True)
        job_name = f"{job}_scan_{point_id}"
        work_dir = scan_dir

        ok = False
        try:
            res = executor._run_calculation_step(
                work_dir, job_name, GaussianPolicy(), adjusted, scan_cfg, is_sp_task=False
            )
            ok = True
            e = res.get("g_low")
            if e is None:
                e = res.get("e_low")
            return (
                (float(e) if e is not None else None),
                (res.get("final_coords") or adjusted),
                None,
            )
        except Exception as e:
            msg = str(e)
            _write_scan_marker(scan_dir, job_name, msg)
            return None, None, msg
        finally:
            # 不在这里清理 scan_dir；由外层 TS 任务的工作目录备份/清理统一处理。
            pass

    def find_local_max(
        points: List[Tuple[float, float, List[str]]],
    ) -> Optional[Tuple[float, float, List[str]]]:
        if len(points) < 3:
            return None
        pts = sorted(points, key=lambda x: x[0])
        maxima: List[Tuple[float, float, List[str]]] = []
        for i in range(1, len(pts) - 1):
            r_prev, e_prev, _ = pts[i - 1]
            r_mid, e_mid, c_mid = pts[i]
            r_next, e_next, _ = pts[i + 1]
            if e_prev < e_mid and e_mid > e_next:
                maxima.append((r_mid, e_mid, c_mid))
        if not maxima:
            return None
        return max(maxima, key=lambda x: x[1])

    points: List[Tuple[float, float, List[str]]] = []
    initial_coords = base_coords

    e0, c0, err0 = run_constrained_opt("r0", initial_coords, r0)
    # 初始点必须先做约束优化（得到可比的势能面参考结构）
    if e0 is None or c0 is None:
        msg = f"初始点 r0={r0:.3f} Å 约束优化失败，无法继续救援；err={err0 or 'unknown'}；TS失败原因={fail_reason}"
        _write_ts_failure_report(wd, job, "scan", msg)
        logger.warning(f"❌ TS scan 初始点失败: {job} | {msg}")
        return None
    points.append((r0, e0, c0))
    # 后续扫描都以初始点优化后的结构作为起点，保证势能面连续性
    initial_coords = c0

    e_m, c_m, _ = run_constrained_opt("m1", initial_coords, r0 - coarse_step)
    if e_m is not None and c_m is not None:
        points.append((r0 - coarse_step, e_m, c_m))

    e_p, c_p, _ = run_constrained_opt("p1", initial_coords, r0 + coarse_step)
    if e_p is not None and c_p is not None:
        points.append((r0 + coarse_step, e_p, c_p))

    direct_fine = False
    if e0 is not None and e_m is not None and e_p is not None and e_m < e0 and e_p < e0:
        direct_fine = True

    # 方向选择：不再用“第一步是否上坡”来硬切换方向。
    # 改为：该方向若出现“连续两个点能量都下降”，才停止该方向并转扫另一方向。
    # 因此这里仅要求该方向第一步存在即可进入 coarse_extend。
    scan_pos = e_p is not None
    scan_neg = e_m is not None

    def coarse_extend(
        direction: int,
        start_coords_for_dir: List[str],
        first_e: Optional[float],
        base_e: Optional[float],
    ) -> bool:
        best_e = None
        uphill = 0
        last_coords = start_coords_for_dir
        all_increasing = True
        prev_e = first_e
        consecutive_down = 0
        k_max = min(max_steps, coarse_k_max)
        if prev_e is None:
            all_increasing = False
        else:
            # 把 e1 相对 e0 的“下降”也计入 streak，这样一旦 e2 继续下降可立即停止该方向。
            if base_e is not None and prev_e < base_e:
                consecutive_down = 1

        for k in range(2, k_max + 1):
            r = r0 + direction * coarse_step * k
            e, c, _ = run_constrained_opt(f"{'p' if direction > 0 else 'm'}{k}", last_coords, r)
            if e is None or c is None:
                uphill += 1
                all_increasing = False
                if uphill >= uphill_limit:
                    break
                continue
            points.append((r, e, c))
            last_coords = c
            # 更稳妥：连续两个点都下降才停止该方向（避免单点噪声/数值抖动）
            if prev_e is not None and e < prev_e:
                consecutive_down += 1
                all_increasing = False
                if consecutive_down >= 2:
                    break
            else:
                consecutive_down = 0
            prev_e = e
            if best_e is None or e < best_e:
                best_e = e
                uphill = 0
            else:
                uphill += 1
                if uphill >= uphill_limit:
                    break

        # 若在限定范围内点点上升，则返回 True 触发放弃
        return bool(all_increasing and prev_e is not None and k_max >= 2)

    if not direct_fine:
        rising_pos = False
        rising_neg = False

        if scan_pos:
            rising_pos = coarse_extend(+1, c_p or initial_coords, e_p, e0)
        if scan_neg:
            rising_neg = coarse_extend(-1, c_m or initial_coords, e_m, e0)

        # 两个方向都不“上坡”，说明 r0 已经是局部峰值（或邻点失败）；直接走细扫
        if not scan_pos and not scan_neg:
            direct_fine = True

        if rising_pos or rising_neg:
            _emit_and_write_scan_table(wd, job, a1, a2, points, fine_points=None)
            _write_ts_failure_report(
                wd,
                job,
                "scan",
                f"初扫在≤{coarse_k_max}步（≈{coarse_step*coarse_k_max:.2f}Å）范围内能量严格上升，放弃救援；TS失败原因={fail_reason}",
            )
            return None

    coarse_peak = find_local_max(points)
    if coarse_peak is None:
        if direct_fine and e0 is not None and c0 is not None:
            coarse_peak = (r0, e0, c0)
        else:
            _emit_and_write_scan_table(wd, job, a1, a2, points, fine_points=None)
            _write_ts_failure_report(
                wd, job, "scan", f"粗扫未找到局部极大值；TS失败原因={fail_reason}"
            )
            return None

    r_peak, _, coords_peak = coarse_peak

    center = r0 if direct_fine else r_peak
    r_left = center - fine_half_window
    r_right = center + fine_half_window

    fine_points: List[Tuple[float, float, List[str]]] = []
    last_coords = initial_coords
    n_steps = int(round((r_right - r_left) / fine_step))
    if n_steps < 2:
        n_steps = 2

    for i in range(n_steps + 1):
        r = r_left + fine_step * i
        e, c, _ = run_constrained_opt(f"f{i:03d}", last_coords, r)
        if e is None or c is None:
            continue
        fine_points.append((r, e, c))
        last_coords = c

    fine_peak = find_local_max(fine_points)
    if fine_peak is None:
        if fine_points:
            fine_peak = max(fine_points, key=lambda x: x[1])
        else:
            _emit_and_write_scan_table(wd, job, a1, a2, points, fine_points=None)
            _write_ts_failure_report(wd, job, "scan", "细扫无有效点，无法救援")
            return None

    r_best, _, coords_best = fine_peak

    # 输出 scan 结果表格（终端 + scan_dir/scan_table.txt）
    _emit_and_write_scan_table(wd, job, a1, a2, points, fine_points=fine_points)

    ts_dir = os.path.join(wd, "ts_rescue")
    os.makedirs(ts_dir, exist_ok=True)
    ts_job = f"{job}_rescue"
    ts_wd = os.path.join(ts_dir, ts_job)
    os.makedirs(ts_wd, exist_ok=True)

    ok = False
    try:
        ts_cfg = dict(cfg)
        # 关键：TS rescue 使用原始 TS keyword，保持与主流程一致
        ts_cfg["keyword"] = cfg.get("keyword", "")

        res = executor._run_calculation_step(
            ts_wd, ts_job, GaussianPolicy(), coords_best, ts_cfg, is_sp_task=False
        )
        ok = True
        final_coords = res.get("final_coords")
        if not final_coords:
            raise RuntimeError("TS rescue 未产生最终结构")

        # 与主流程保持一致：若 TS keyword 不含 freq，则使用关键键长漂移做 sanity check。
        if not _keyword_requests_freq(cfg):
            bond_drift_threshold = float(cfg.get("ts_bond_drift_threshold", 0.4))
            r_initial = _bond_length_from_xyz_lines(base_coords, a1, a2)
            r_final = _bond_length_from_xyz_lines(final_coords, a1, a2)
            if r_initial is not None and r_final is not None:
                d_r = abs(r_final - r_initial)
                if d_r > bond_drift_threshold:
                    raise RuntimeError(
                        f"TS rescue 几何判据失败：关键键长偏移 |ΔR|={d_r:.3f} Å 超过阈值 {bond_drift_threshold:.3f} Å "
                        f"(R_initial={r_initial:.3f} Å, R_final={r_final:.3f} Å, TSAtoms={a1},{a2})"
                    )

        num_imag_raw = res.get("num_imag_freqs")
        num_imag = 0 if num_imag_raw is None else int(num_imag_raw)
        lowest_freq = res.get("lowest_freq")
        if _keyword_requests_freq(cfg):
            if num_imag_raw is None:
                raise RuntimeError("TS rescue keyword 包含 freq，但未解析到频率信息")
            if num_imag != 1:
                msg = f"TS rescue 需要且仅需要 1 个虚频，实际为 {num_imag}"
                if lowest_freq is not None:
                    msg += f"（最低频率: {lowest_freq:.1f} cm⁻¹）"
                raise RuntimeError(msg)

        e = res.get("e_low")
        g = res.get("g_low")
        gc = res.get("g_corr")
        itask = get_itask(cfg)
        if itask in [2, 3, 4] and gc is None and e is not None and g is not None:
            gc = g - e

        final_val = g if g is not None else e
        key = "final_gibbs_energy" if g is not None else "energy"

        out: Dict[str, Any] = {
            **task_info,
            "status": "success",
            key: final_val,
            "final_coords": final_coords,
            "num_imag_freqs": res.get("num_imag_freqs"),
            "lowest_freq": res.get("lowest_freq"),
            "g_corr": gc,
            "rescued_by_scan": True,
            "scan_peak_bond": float(r_best),
        }

        ts_bond_length = _bond_length_from_xyz_lines(final_coords, a1, a2)
        out["ts_bond_atoms"] = f"{a1},{a2}"
        if ts_bond_length is not None:
            out["ts_bond_length"] = ts_bond_length

        logger.info(
            f"✅ TS 救援成功: {job} | 峰值键长 {r_best:.3f} Å | 最终键长 {ts_bond_length:.3f} Å"
            if ts_bond_length
            else f"✅ TS 救援成功: {job} | 峰值键长 {r_best:.3f} Å"
        )

        return out
    except Exception as e:
        _write_ts_failure_report(wd, job, "ts_rescue", str(e))
        logger.warning(f"❌ TS 救援失败: {job} | {e}")
        return None
    finally:
        try:
            keep = str(cfg.get("ts_rescue_keep_scan_dirs", "false")).lower() == "true"
            executor.handle_backups(ts_wd, cfg, success=ok, cleanup_work_dir=(not keep))
        except Exception:
            pass
