#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""统一的单任务执行器。

说明
- 直接封装 `executor` + policy 的执行流程，以便所有调用者统一使用 `TaskRunner().run(...)`。
- 目前仍在内部通过 `executor` 直接 monkeypatch，而不依赖 `confflow.calc` 的顶层兼容符号。
"""

import os
from typing import Any, Dict

from . import executor
from ..analysis import (
    _bond_length_from_xyz_lines,
    _coords_array_from_xyz_lines,
    _keyword_requests_freq,
    _parse_ts_bond_atoms,
)
from ..core import get_itask, parse_iprog
from ..policies.gaussian import GaussianPolicy
from ..policies.orca import OrcaPolicy
from ..rescue import _ts_rescue_scan

from ...blocks import refine


class TaskRunner:
    def _get_policy(self, config: Dict[str, Any]):
        iprog = parse_iprog(config)
        if iprog == 1:
            return GaussianPolicy()
        if iprog == 2:
            return OrcaPolicy()
        raise ValueError(f"Unsupported iprog: {iprog}")

    def run(self, task_info: Dict[str, Any]):
        job, wd, cfg = task_info["job_name"], task_info["work_dir"], task_info["config"]
        os.makedirs(wd, exist_ok=True)
        success = False

        policy = self._get_policy(cfg)

        # Stage cross-step artifacts (e.g., Gaussian .chk) into this job work_dir.
        try:
            executor.prepare_task_inputs(wd, job, cfg)
        except Exception:
            pass

        try:
            res = executor._run_calculation_step(wd, job, policy, task_info["coords"], cfg)

            final_coords = res.get("final_coords")
            itask = get_itask(cfg)
            if not final_coords:
                if itask == 1:
                    final_coords = task_info["coords"]
                else:
                    return {**task_info, "status": "failed", "error": "No coords"}

            num_imag_raw = res.get("num_imag_freqs")
            num_imag = 0 if num_imag_raw is None else int(num_imag_raw)
            lowest_freq = res.get("lowest_freq")

            if itask == 4 and _keyword_requests_freq(cfg):
                if num_imag_raw is None:
                    err_msg = "TS 任务 keyword 包含 freq，但输出中未解析到频率信息"
                    if str(cfg.get("ts_rescue_scan", "true")).lower() == "true":
                        rescued = _ts_rescue_scan(task_info, err_msg)
                        if rescued is not None:
                            return rescued
                    return {**task_info, "status": "failed", "error": err_msg}
                if num_imag != 1:
                    err_msg = f"TS 任务需要且仅需要 1 个虚频，实际为 {num_imag}"
                    if lowest_freq is not None:
                        err_msg += f"（最低频率: {lowest_freq:.1f} cm⁻¹）"
                    if str(cfg.get("ts_rescue_scan", "true")).lower() == "true":
                        rescued = _ts_rescue_scan(task_info, err_msg)
                        if rescued is not None:
                            return rescued
                    return {**task_info, "status": "failed", "error": err_msg}

            if itask == 3 and num_imag > 0:
                err_msg = f"优化+频率任务存在 {num_imag} 个虚频"
                if lowest_freq is not None:
                    err_msg += f"（最低频率: {lowest_freq:.1f} cm⁻¹）"
                return {**task_info, "status": "failed", "error": err_msg}

            ts_bond_atoms = cfg.get("ts_bond_atoms", cfg.get("ts_bond"))
            ts_bond_length = None
            ts_pair = _parse_ts_bond_atoms(ts_bond_atoms)
            if ts_pair and final_coords:
                ts_bond_length = _bond_length_from_xyz_lines(final_coords, ts_pair[0], ts_pair[1])
                ts_bond_atoms = f"{ts_pair[0]},{ts_pair[1]}"

            if itask == 4 and not _keyword_requests_freq(cfg):
                bond_drift_threshold = float(cfg.get("ts_bond_drift_threshold", 0.4))
                if ts_pair is not None:
                    r_initial = _bond_length_from_xyz_lines(
                        task_info["coords"], ts_pair[0], ts_pair[1]
                    )
                    r_final = _bond_length_from_xyz_lines(final_coords, ts_pair[0], ts_pair[1])
                    if r_initial is not None and r_final is not None:
                        d_r = abs(r_final - r_initial)
                        if d_r > bond_drift_threshold:
                            err_msg = (
                                f"TS 几何判据失败：关键键长偏移 |ΔR|={d_r:.3f} Å 超过阈值 {bond_drift_threshold:.3f} Å "
                                f"(R_initial={r_initial:.3f} Å, R_final={r_final:.3f} Å, TSAtoms={ts_pair[0]},{ts_pair[1]})"
                            )
                            if str(cfg.get("ts_rescue_scan", "true")).lower() == "true":
                                rescued = _ts_rescue_scan(task_info, err_msg)
                                if rescued is not None:
                                    return rescued
                            return {**task_info, "status": "failed", "error": err_msg}

            inherited_gc = None
            try:
                meta = task_info.get("metadata") or {}
                # 新约定：一旦某步已经产生 G=...（Gibbs），则不再向下传递 G_corr。
                # 只有 freq/opt_freq 产出的 G_corr 会向下传递，直到与 sp 能量合成出 G。
                if "G" in meta:
                    inherited_gc = None
                elif "G_corr" in meta:
                    inherited_gc = float(meta.get("G_corr"))
                elif "g_corr" in meta:
                    inherited_gc = float(meta.get("g_corr"))
            except Exception:
                inherited_gc = None

            e, g, gc = res.get("e_low"), res.get("g_low"), res.get("g_corr")
            if itask in [2, 3, 4] and gc is None and e is not None and g is not None:
                gc = g - e
            if gc is None and inherited_gc is not None:
                gc = inherited_gc

            final_sp_energy = None
            if itask == 1:
                if e is not None and gc is not None:
                    final_sp_energy = e
                    final_val = e + gc
                    key = "final_gibbs_energy"
                else:
                    final_val = e
                    key = "energy"
            else:
                if g is not None:
                    final_val = g
                    key = "final_gibbs_energy"
                elif e is not None and gc is not None:
                    final_val = e + gc
                    key = "final_gibbs_energy"
                else:
                    final_val = e
                    key = "energy"

            success = True
            result = {
                **task_info,
                "status": "success",
                key: final_val,
                "final_sp_energy": final_sp_energy,
                "final_coords": final_coords,
                "num_imag_freqs": res.get("num_imag_freqs"),
                "lowest_freq": res.get("lowest_freq"),
                "g_corr": gc,
            }
            if ts_bond_atoms is not None:
                result["ts_bond_atoms"] = str(ts_bond_atoms)
            if ts_bond_length is not None:
                result["ts_bond_length"] = ts_bond_length
            return result

        except Exception as e:
            if get_itask(cfg) == 4 and str(cfg.get("ts_rescue_scan", "true")).lower() == "true":
                rescued = _ts_rescue_scan(task_info, str(e))
                if rescued is not None:
                    return rescued
            return {
                **task_info,
                "status": "failed",
                "error": str(e),
                "error_details": executor._get_error_details(wd, job, cfg, e, policy),
            }
        finally:
            executor.handle_backups(wd, cfg, success, cleanup_work_dir=True)
