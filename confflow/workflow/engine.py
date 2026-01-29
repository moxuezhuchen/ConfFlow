#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""工作流执行引擎（从 confflow.main 拆分）。

设计目标
- 纯业务逻辑：不做 sys.exit
- 便于测试：核心入口 `run_workflow()` 接受显式参数
"""

from __future__ import annotations

import json
import os
import re
import shutil
import time
from contextlib import contextmanager
import sqlite3
from datetime import datetime
from typing import Any, Dict, List, Optional, Union

from ..blocks import confgen, refine, viz
from .. import calc

from ..core import io as io_xyz
from ..blocks.confgen.generator import load_mol_from_xyz, _parse_chain
from ..blocks.confgen.validator import ChainValidator
from ..config.loader import load_workflow_config_file
from ..config.schema import ConfigSchema
from ..core.utils import (
    format_duration_hms,
    format_index_ranges,
    get_logger,
    parse_index_spec,
    validate_xyz_file,
)

logger = get_logger()


@contextmanager
def pushd(path: str):
    prev = os.getcwd()
    os.chdir(path)
    try:
        yield
    finally:
        os.chdir(prev)


def as_list(value):
    if value is None:
        return None
    if isinstance(value, list):
        return value
    return [value]


def normalize_pair_list(value):
    """将 add_bond/del_bond/no_rotate/force_rotate 规范为 [[a,b], ...] (1-based)。"""
    if value is None:
        return None

    if isinstance(value, list):
        if len(value) == 0:
            return []
        if len(value) == 2 and all(isinstance(x, int) for x in value):
            return [value]
        if all(isinstance(x, (list, tuple)) and len(x) == 2 for x in value):
            return [[int(a), int(b)] for a, b in value]
        if all(isinstance(x, str) for x in value):
            out = []
            for item in value:
                parts = item.replace(",", " ").split()
                if len(parts) != 2:
                    raise ValueError(f"键对格式错误: {item}，应为 'a b' 或 'a,b'")
                out.append([int(parts[0]), int(parts[1])])
            return out

    if isinstance(value, str):
        parts = value.replace(",", " ").split()
        if len(parts) != 2:
            raise ValueError(f"键对格式错误: {value}，应为 'a b' 或 'a,b'")
        return [[int(parts[0]), int(parts[1])]]

    raise ValueError(f"不支持的键对格式: {type(value)}")


def count_conformers_in_xyz(filepath: str) -> int:
    if not os.path.exists(filepath):
        return 0
    ok, geoms = validate_xyz_file(filepath)
    if not ok:
        return 0
    return len(geoms)


def count_conformers_any(src: Union[str, List[str]]) -> int:
    if isinstance(src, (list, tuple)):
        return sum(count_conformers_in_xyz(str(p)) for p in src)
    return count_conformers_in_xyz(str(src))


def is_multi_frame_xyz(filepath: str) -> bool:
    return count_conformers_in_xyz(filepath) >= 2


def is_multi_frame_any(src: Union[str, List[str]]) -> bool:
    if isinstance(src, (list, tuple)):
        return count_conformers_any(src) >= 2
    return is_multi_frame_xyz(str(src))


def validate_inputs_compatible(
    input_files: List[str],
    confgen_params: Optional[Dict[str, Any]] = None,
    force_consistency: bool = False,
) -> None:
    """确保多输入可被 confgen 合并：单帧、原子数与元素序列一致。
    
    Args:
        input_files: 输入文件列表
        confgen_params: (Optional) confgen 步骤参数，用于柔性链对齐检查
        force_consistency: 即使不一致也不抛出异常（用于 --yes / --force 模式绕过检查）
    """
    if not input_files:
        raise ValueError("未提供输入文件")

    allow_chain_mapping = bool(confgen_params and confgen_params.get("chains"))

    ref_atoms = None
    ref_natoms = None
    for fp in input_files:
        ok, geoms = validate_xyz_file(fp)
        if not ok or not geoms:
            raise ValueError(f"输入 XYZ 无法解析: {fp}")
        if len(geoms) != 1:
            raise ValueError(
                f"多文件输入模式要求每个输入为单帧 XYZ（当前 {fp} 含 {len(geoms)} 帧）。"
            )
        atoms = list(geoms[0].get("atoms") or [])
        natoms = len(atoms)
        if ref_atoms is None:
            ref_atoms = atoms
            ref_natoms = natoms
            continue
        if natoms != ref_natoms:
            raise ValueError(
                f"输入文件原子数不一致: {fp} ({natoms}) vs Ref ({ref_natoms})"
            )

        if allow_chain_mapping:
            # 允许原子顺序不同，但要求元素计数一致
            if sorted(atoms) != sorted(ref_atoms):
                raise ValueError(
                    "输入文件元素组成不一致（chains 模式要求元素计数一致）：\n"
                    f"File: {fp}"
                )
        else:
            # 默认严格要求原子顺序一致
            if atoms != ref_atoms:
                diffs = []
                for i, (a1, a2) in enumerate(zip(atoms, ref_atoms)):
                    if a1 != a2:
                        diffs.append(f"#{i+1} {a1} vs {a2}")
                        if len(diffs) >= 3:
                            break
                raise ValueError(
                    "要求所有输入具有相同的原子数与元素顺序。\n"
                    "输入文件元素顺序不一致（多输入模式要求完全一致）：\n"
                    f"File: {fp}\nDifference: {', '.join(diffs)}..."
                )

    # -------------------------------------------------------------------------
    # 柔性链一致性检查（如果有 confgen 参数）
    # -------------------------------------------------------------------------
    if confgen_params and "chains" in confgen_params:
        chains = as_list(confgen_params.get("chains"))
        if chains:
            try:
                if not bool(confgen_params.get("validate_chain_bonds", False)):
                    return
                validator = ChainValidator(chains)
                bond_threshold = float(confgen_params.get("bond_threshold", 1.15))

                # 仅检查第一个输入文件中的链合法性与成键性
                ref_fp = input_files[0]
                mol = load_mol_from_xyz(ref_fp, bond_threshold)
                ref_data = validator.validate_mol(mol, ref_fp)
                invalid = [d for d in ref_data if not d.get("valid")]
                if invalid:
                    messages = [f"{d.get('raw_chain')}: {d.get('error')}" for d in invalid]
                    raise ValueError(
                        "柔性链在参考输入文件中无效：\n" + "\n".join(messages)
                    )
            except Exception as e:
                if isinstance(e, ValueError):
                    raise
                logger.warning(f"无法执行柔性链一致性检查: {e}")


def _count_failed_tasks_in_results_db(db_path: str) -> Optional[int]:
    """从 calc 结果库统计失败任务数量。

    约定：calc 模块写入 SQLite 表 task_results，其中 status='failed' 表示该构象计算失败。
    返回 None 表示无法统计（文件不存在或解析失败）。
    """
    counts = _count_task_statuses_in_results_db(db_path)
    if counts is None:
        return None
    return int(counts.get("failed", 0))


def _count_task_statuses_in_results_db(db_path: str) -> Optional[Dict[str, int]]:
    """从 calc 结果库统计各 status 数量。

    返回 dict: {total, success, failed, skipped}。
    返回 None 表示无法统计（文件不存在或解析失败）。
    """
    try:
        if not db_path or (not os.path.exists(db_path)):
            return None
        con = sqlite3.connect(db_path)
        try:
            cur = con.cursor()
            cur.execute("select status, count(*) from task_results group by status")
            rows = cur.fetchall() or []
            counts: Dict[str, int] = {}
            for st, n in rows:
                if st is None:
                    continue
                counts[str(st)] = int(n)
            out = {
                "success": counts.get("success", 0),
                "failed": counts.get("failed", 0),
                "skipped": counts.get("skipped", 0),
            }
            out["total"] = int(sum(out.values()))
            return out
        finally:
            con.close()
    except FileNotFoundError:
        return None
    except Exception as e:
        logger.debug(f"统计任务状态失败 {db_path}: {e}")
        return None


def _normalize_iprog_label(iprog: Any) -> str:
    s = str(iprog).strip().lower()
    if s in {"1", "g16", "gaussian", "gau", "g09", "g03"}:
        return "g16"
    if s in {"2", "orca"}:
        return "orca"
    return str(iprog).strip()


def _itask_label(itask: Any) -> str:
    s = str(itask).strip().lower()
    mapping = {
        "0": "opt",
        "1": "sp",
        "2": "freq",
        "3": "opt_freq",
        "4": "ts",
        "opt": "opt",
        "sp": "sp",
        "freq": "freq",
        "opt_freq": "opt_freq",
        "optfreq": "opt_freq",
        "ts": "ts",
    }
    return mapping.get(s, str(itask).strip())


def load_workflow_config(config_file: str) -> Dict[str, Any]:
    """兼容：保留旧名字，但实现收敛到统一入口。"""
    return load_workflow_config_file(config_file)


def create_runtask_config(
    filename: str, params: Dict[str, Any], global_config: Dict[str, Any]
) -> None:
    """为 calc 模块生成临时的 ini 配置文件（保留现有行为，供 engine 调用）。"""
    import configparser

    config = configparser.ConfigParser(interpolation=None)

    def _identity_option(optionstr: str) -> str:
        return optionstr

    config.optionxform = _identity_option

    def build_clean_opts(params: Dict[str, Any], global_config: Dict[str, Any]) -> str:
        clean_params = params.get("clean_params")
        if clean_params:
            return str(clean_params)

        opts: List[str] = []
        if params.get("dedup_only"):
            opts.append("--dedup-only")
        if params.get("keep_all_topos"):
            opts.append("--keep-all-topos")

        # RMSD ignore H (refine: --noH)
        no_h = params.get("noH")
        if no_h is None:
            no_h = global_config.get("noH")
        if bool(no_h):
            opts.append("--noH")

        rmsd = params.get("rmsd_threshold", global_config.get("rmsd_threshold"))
        if rmsd is not None:
            opts.append(f"-t {rmsd}")

        if "energy_window" in params and params.get("energy_window") is not None:
            opts.append(f"-ewin {params['energy_window']}")

        return " ".join(opts)

    def _parse_two_atom_indices(val):
        if val is None:
            return None
        if isinstance(val, (list, tuple)):
            nums = []
            for x in val:
                try:
                    nums.append(int(x))
                except Exception:
                    continue
        else:
            nums = []
            for m in re.findall(r"\d+", str(val)):
                try:
                    nums.append(int(m))
                except Exception:
                    continue
        if len(nums) >= 2:
            a, b = nums[0], nums[1]
            if a > 0 and b > 0 and a != b:
                return f"{a},{b}"
        return None

    # 资源配置
    cores = params.get("cores_per_task", global_config.get("cores_per_task", 4))
    memory = params.get(
        "total_memory", global_config.get("total_memory", global_config.get("mem_per_task", "4GB"))
    )
    max_jobs = params.get("max_parallel_jobs", global_config.get("max_parallel_jobs", 4))

    config["DEFAULT"] = {
        "gaussian_path": global_config.get("gaussian_path", "g16"),
        "orca_path": global_config.get("orca_path", "orca"),
        "cores_per_task": str(cores),
        "total_memory": str(memory),
        "max_parallel_jobs": str(max_jobs),
        "charge": str(params.get("charge", global_config.get("charge", 0))),
        "multiplicity": str(params.get("multiplicity", global_config.get("multiplicity", 1))),
        "enable_dynamic_resources": str(
            params.get(
                "enable_dynamic_resources", global_config.get("enable_dynamic_resources", False)
            )
        ).lower(),
        "auto_clean": "true",
        "delete_work_dir": "true",
    }

    # Cross-step artifact inputs
    if params.get("input_chk_dir") is not None and str(params.get("input_chk_dir")).strip() != "":
        config["DEFAULT"]["input_chk_dir"] = str(params.get("input_chk_dir")).strip()
    elif global_config.get("input_chk_dir") is not None and str(global_config.get("input_chk_dir")).strip() != "":
        config["DEFAULT"]["input_chk_dir"] = str(global_config.get("input_chk_dir")).strip()

    # Gaussian checkpoint output toggle
    if params.get("gaussian_write_chk") is not None:
        config["DEFAULT"]["gaussian_write_chk"] = str(params.get("gaussian_write_chk")).strip()
    elif global_config.get("gaussian_write_chk") is not None:
        config["DEFAULT"]["gaussian_write_chk"] = str(global_config.get("gaussian_write_chk")).strip()

    # TSAtoms 默认：显式 ts_bond_atoms/ts_bond > global > freeze 前两位
    ts_pair_any = _parse_two_atom_indices(params.get("ts_bond_atoms", params.get("ts_bond")))
    if ts_pair_any is None:
        ts_pair_any = _parse_two_atom_indices(
            global_config.get("ts_bond_atoms", global_config.get("ts_bond"))
        )
    if ts_pair_any is None:
        ts_pair_any = _parse_two_atom_indices(params.get("freeze", global_config.get("freeze")))
    if ts_pair_any is not None:
        config["DEFAULT"]["ts_bond_atoms"] = ts_pair_any

    orca_maxcore = params.get(
        "orca_maxcore", global_config.get("orca_maxcore", global_config.get("maxcore"))
    )
    if orca_maxcore is not None and str(orca_maxcore).strip():
        config["DEFAULT"]["orca_maxcore"] = str(orca_maxcore)

    from ..core.utils import parse_itask

    itask_int = parse_itask(params.get("itask", "opt"))

    # freeze 只对 opt/opt_freq 生效
    if itask_int in [0, 3]:
        freeze_val = params.get("freeze", global_config.get("freeze", "0"))
    else:
        freeze_val = "0"
    if isinstance(freeze_val, list):
        freeze_val = ",".join(str(x) for x in freeze_val)
    elif freeze_val is None:
        freeze_val = "0"
    else:
        freeze_val = str(freeze_val)

    # TS: rescue + scan params
    if itask_int == 4:
        ts_pair = _parse_two_atom_indices(params.get("ts_bond_atoms", params.get("ts_bond")))
        if ts_pair is None:
            ts_pair = _parse_two_atom_indices(
                global_config.get("ts_bond_atoms", global_config.get("ts_bond"))
            )
        if ts_pair is None:
            ts_pair = _parse_two_atom_indices(params.get("freeze", global_config.get("freeze")))
        if ts_pair is not None:
            config["DEFAULT"]["ts_bond_atoms"] = ts_pair

        rescue_val = params.get("ts_rescue_scan", global_config.get("ts_rescue_scan", True))
        config["DEFAULT"]["ts_rescue_scan"] = str(bool(rescue_val)).lower()

        for k in [
            "scan_coarse_step",
            "scan_fine_step",
            "scan_uphill_limit",
            "scan_max_steps",
            "scan_fine_half_window",
            "ts_rescue_keep_scan_dirs",
            "ts_rescue_scan_backup",
        ]:
            if k in params:
                config["DEFAULT"][k] = str(params[k])
            elif k in global_config:
                config["DEFAULT"][k] = str(global_config[k])

    # 任务设置
    task_settings = {
        "itask": str(params.get("itask", 1)),
        "iprog": str(params.get("iprog", 2)),
        "freeze": freeze_val,
        "clean_opts": build_clean_opts(params, global_config),
    }

    if "keyword" in params:
        task_settings["keyword"] = params["keyword"]
    if "solvent_block" in params:
        task_settings["solvent_block"] = params["solvent_block"]
    if "custom_block" in params:
        task_settings["custom_block"] = params["custom_block"]

    task_settings = {k: v for k, v in task_settings.items() if v is not None and v != ""}
    config["Task"] = task_settings

    os.makedirs(os.path.dirname(filename) or ".", exist_ok=True)
    with open(filename, "w", encoding="utf-8") as f:
        config.write(f)


def run_workflow(
    input_xyz: List[str],
    config_file: str,
    work_dir: str,
    original_input_files: Optional[List[str]] = None,
    resume: bool = False,
    verbose: bool = False,
) -> Dict[str, Any]:
    if verbose and hasattr(logger, "set_level"):
        logger.set_level(10)

    input_files = [os.path.abspath(x) for x in input_xyz]
    original_inputs = (
        [os.path.abspath(x) for x in original_input_files]
        if original_input_files
        else input_files
    )
    for fp in input_files:
        if not os.path.exists(fp):
            raise FileNotFoundError(f"输入文件不存在: {fp}")

    cfg = load_workflow_config(config_file)
    global_config = cfg["global"]
    steps = cfg["steps"]

    # 预加载 confgen 参数用于多输入柔性链一致性检查
    confgen_params = None
    if len(input_files) > 1:
        # 寻找第一个 confgen 步骤
        for step in steps:
             if step.get("type", "").lower() == "confgen":
                 confgen_params = step.get("params", {})
                 break
        validate_inputs_compatible(input_files, confgen_params, force_consistency=global_config.get("force_consistency", False))

    root_dir = os.path.abspath(work_dir)
    os.makedirs(root_dir, exist_ok=True)
    if hasattr(logger, "add_file_handler"):
        logger.add_file_handler(os.path.join(root_dir, "confflow.log"))

    checkpoint_file = os.path.join(root_dir, ".checkpoint")

    def _ensure_xyz_has_cids(xyz_path: str, *, prefix: str) -> None:
        try:
            confs = io_xyz.read_xyz_file(xyz_path, parse_metadata=True)
            io_xyz.ensure_conformer_cids(confs, prefix=prefix)
            io_xyz.write_xyz_file(xyz_path, confs, atomic=True)
        except FileNotFoundError as e:
            logger.warning(f"无法为 XYZ 文件分配 CID（文件不存在）: {xyz_path}")
        except Exception as e:
            # 溯源功能增强：失败不应中断主工作流
            logger.warning(f"为 XYZ 文件分配 CID 失败: {xyz_path}, 原因: {e}")

    def _extract_energy(meta: Dict[str, Any]) -> Optional[float]:
        val = meta.get("G", meta.get("E", meta.get("Energy")))
        if val is None:
            return None
        try:
            return float(val)
        except (ValueError, TypeError):
            logger.debug(f"无法解析能量值: {val}")
            return None

    def _build_cid_index(xyz_path: str) -> Dict[str, Any]:
        confs = io_xyz.read_xyz_file(xyz_path, parse_metadata=True)
        # 确保 CID 存在（兼容旧文件）
        io_xyz.ensure_conformer_cids(confs, prefix="trace")

        cid_to_frame = {}
        energy_rows = []
        for idx, c in enumerate(confs):
            meta = c.get("metadata") or {}
            cid = None
            if isinstance(meta, dict):
                cid = meta.get("CID")
            if cid is None:
                continue
            cid = str(cid)
            e = _extract_energy(meta) if isinstance(meta, dict) else None
            cid_to_frame[cid] = {"frame_index": idx, "energy": e}
            if e is not None:
                energy_rows.append((e, cid))

        # rank_by_energy: 1 = lowest
        rank_by_energy = {}
        energy_rows.sort(key=lambda x: x[0])
        for r, (_, cid) in enumerate(energy_rows, start=1):
            # 同一 CID 只记录最优（理论上不会重复）
            if cid not in rank_by_energy:
                rank_by_energy[cid] = r

        return {
            "cid_to_frame": cid_to_frame,
            "rank_by_energy": rank_by_energy,
        }

    def _load_checkpoint() -> Optional[int]:
        if not os.path.exists(checkpoint_file):
            return None
        try:
            with open(checkpoint_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            return int(data.get("last_completed_step", -1))
        except json.JSONDecodeError as e:
            logger.warning(f"检查点文件格式错误: {e}")
            return None
        except (IOError, OSError) as e:
            logger.warning(f"无法读取检查点文件: {e}")
            return None
        except Exception as e:
            logger.debug(f"加载检查点失败: {e}")
            return None

    def _save_checkpoint(step_index: int, workflow_stats: Dict[str, Any]) -> None:
        data = {
            "last_completed_step": step_index,
            "timestamp": datetime.now().isoformat(),
            "stats": workflow_stats,
        }
        with open(checkpoint_file, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)

    resume_from_step = -1
    if resume:
        val = _load_checkpoint()
        if val is not None:
            resume_from_step = val

    current_input: Union[str, List[str]] = input_files[0] if len(input_files) == 1 else input_files

    initial_conformer_count = count_conformers_any(current_input)
    multi_frame = len(input_files) == 1 and is_multi_frame_any(current_input)

    workflow_stats: Dict[str, Any] = {
        "start_time": datetime.now().isoformat(),
        "input_files": input_files,
        "original_input_files": original_inputs,
        "initial_conformers": initial_conformer_count,
        "is_multi_frame_input": multi_frame,
        "steps": [],
        "_start_ts": time.time(),
    }

    for i, step in enumerate(steps):
        if resume_from_step >= i:
            continue
        if not step.get("enabled", True):
            continue

        step_name = step["name"]
        step_type = step["type"]
        step_dir = os.path.join(root_dir, step_name)
        os.makedirs(step_dir, exist_ok=True)

        step_start = time.time()
        step_stats: Dict[str, Any] = {
            "name": step_name,
            "type": step_type,
            "index": i + 1,
            "input_conformers": count_conformers_any(current_input),
        }

        params = step.get("params", {}) or {}

        # === Step header（使用 Rich 美化） ===
        total_steps = len(steps)
        in_n = step_stats["input_conformers"]
        
        from ..core.console import print_step_header, console
        from rich.table import Table

        if step_type in ["calc", "task"]:
            merged = {**global_config, **params}
            iprog = _normalize_iprog_label(merged.get("iprog", "orca"))
            itask = _itask_label(merged.get("itask", "opt"))
            cores = merged.get("cores_per_task", global_config.get("cores_per_task", 4))
            mem = merged.get(
                "total_memory",
                global_config.get("total_memory", global_config.get("mem_per_task", "4GB")),
            )
            max_jobs = merged.get("max_parallel_jobs", global_config.get("max_parallel_jobs", 4))

            # freeze：只对 opt/opt_freq 生效（与 create_runtask_config 保持一致）
            from ..core.utils import parse_itask

            itask_int = parse_itask(merged.get("itask", "opt"))
            freeze_raw = (
                merged.get("freeze", global_config.get("freeze", "0"))
                if itask_int in [0, 3]
                else "0"
            )
            freeze_idx = parse_index_spec(freeze_raw)
            freeze_fmt = format_index_ranges(freeze_idx)
            freeze_show = f"{freeze_fmt} ({len(freeze_idx)})" if freeze_idx else "none"

            print_step_header(i + 1, total_steps, step_name, step_type, in_n)
            
            grid = Table.grid(padding=(0, 2))
            grid.add_column(style="dim cyan", justify="right")
            grid.add_column(style="white")
            
            grid.add_row("Prog:", iprog)
            grid.add_row("Task:", itask)
            grid.add_row("Config:", f"Jobs={max_jobs}, Cores={cores}, Mem={mem}")
            grid.add_row("Freeze:", freeze_show)

            kw = merged.get("keyword")
            if kw is not None and str(kw).strip() != "":
                grid.add_row("Keyword:", str(kw).strip())
            
            console.print(grid)
            console.print() # 空行分隔
        else:
            print_step_header(i + 1, total_steps, step_name, step_type, in_n)

        try:
            if step_type in ["confgen", "gen"]:
                expected_output = os.path.join(step_dir, "traj.xyz")

                if multi_frame and isinstance(current_input, str):
                    shutil.copy2(current_input, expected_output)
                    step_stats["status"] = "skipped_multi_frame"
                    _ensure_xyz_has_cids(expected_output, prefix=f"s{i+1:02d}")
                    current_input = expected_output
                elif os.path.exists(expected_output):
                    step_stats["status"] = "skipped"
                    _ensure_xyz_has_cids(expected_output, prefix=f"s{i+1:02d}")
                    current_input = expected_output
                else:
                    chains = params.get("chains")
                    if chains is None:
                        chains = params.get("chain")
                    chains_list = as_list(chains)
                    if chains_list is not None:
                        chains_list = [str(x) for x in chains_list]

                    chain_steps = params.get("chain_steps")
                    if chain_steps is None:
                        chain_steps = params.get("steps")
                    chain_steps_list = as_list(chain_steps)
                    if chain_steps_list is not None:
                        chain_steps_list = [str(x) for x in chain_steps_list]

                    chain_angles = params.get("chain_angles")
                    if chain_angles is None:
                        chain_angles = params.get("angles")
                    chain_angles_list = as_list(chain_angles)
                    if chain_angles_list is not None:
                        chain_angles_list = [str(x) for x in chain_angles_list]

                    with pushd(step_dir):
                        confgen.run_generation(
                            input_files=current_input,
                            angle_step=params.get("angle_step", 120),
                            bond_threshold=params.get("bond_multiplier", 1.15),
                            clash_threshold=0.65,
                            add_bond=normalize_pair_list(params.get("add_bond")),
                            del_bond=normalize_pair_list(params.get("del_bond")),
                            no_rotate=normalize_pair_list(params.get("no_rotate")),
                            force_rotate=normalize_pair_list(params.get("force_rotate")),
                            optimize=params.get("optimize", False),
                            confirm=False,
                            chains=chains_list,
                            chain_steps=chain_steps_list,
                            chain_angles=chain_angles_list,
                            rotate_side=params.get("rotate_side", "left"),
                        )

                    # confgen 默认输出 traj.xyz 在 step_dir
                    gen_out = os.path.join(step_dir, "traj.xyz")
                    if not os.path.exists(gen_out):
                        raise RuntimeError("confgen 未生成 traj.xyz")
                    if gen_out != expected_output:
                        shutil.move(gen_out, expected_output)
                    _ensure_xyz_has_cids(expected_output, prefix=f"s{i+1:02d}")
                    step_stats["status"] = "completed"
                    current_input = expected_output

                step_stats["output_xyz"] = os.path.abspath(current_input)

            elif step_type in ["calc", "task"]:
                # Optional: stage chk artifacts from an earlier step.
                # The mapping key is job_name (CID-stable after ensure_conformer_cids), so later steps can
                # consume {job_name}.chk from the chosen step's backups directory.
                params = dict(params)
                chk_from = (
                    params.get("chk_from_step")
                    or params.get("input_chk_from_step")
                    or params.get("read_chk_from_step")
                )
                if chk_from is not None and str(chk_from).strip() != "":
                    from_name: Optional[str] = None
                    s = str(chk_from).strip()
                    if s.isdigit():
                        idx = int(s)
                        if 1 <= idx <= len(steps):
                            from_name = steps[idx - 1].get("name")
                    else:
                        from_name = s
                    if from_name:
                        params["input_chk_dir"] = os.path.join(root_dir, from_name, "backups")

                config_path = os.path.join(step_dir, "config.ini")
                create_runtask_config(config_path, params, global_config)
                ConfigSchema.validate_calc_config({**global_config, **params})

                expected_clean = os.path.join(step_dir, "isomers_cleaned.xyz")
                expected_raw = os.path.join(step_dir, "isomers.xyz")

                if os.path.exists(expected_clean):
                    step_stats["status"] = "skipped"
                    current_input = expected_clean
                    _ensure_xyz_has_cids(current_input, prefix=f"s{i+1:02d}")
                elif os.path.exists(expected_raw):
                    step_stats["status"] = "skipped"
                    current_input = expected_raw
                    _ensure_xyz_has_cids(current_input, prefix=f"s{i+1:02d}")
                else:
                    manager = calc.ChemTaskManager(settings_file=config_path)
                    manager.config["auto_clean"] = "true"
                    manager.work_dir = os.path.join(step_dir, "work")
                    manager.run(
                        input_xyz_file=(
                            current_input if isinstance(current_input, str) else current_input[0]
                        )
                    )

                    work_cleaned = os.path.join(manager.work_dir, "isomers_cleaned.xyz")
                    work_raw = os.path.join(manager.work_dir, "isomers.xyz")
                    work_failed = os.path.join(manager.work_dir, "isomers_failed.xyz")
                    if os.path.exists(work_cleaned):
                        shutil.copy2(work_cleaned, expected_clean)
                        current_input = expected_clean
                    elif os.path.exists(work_raw):
                        shutil.copy2(work_raw, expected_raw)
                        current_input = expected_raw
                    else:
                        raise RuntimeError("计算任务未产生预期输出")

                    # 若存在失败构象输出，一并复制到 step_dir
                    try:
                        if os.path.exists(work_failed):
                            shutil.copy2(work_failed, os.path.join(step_dir, "isomers_failed.xyz"))
                    except Exception:
                        pass

                    _ensure_xyz_has_cids(current_input, prefix=f"s{i+1:02d}")
                    step_stats["status"] = "completed"

                step_stats["output_xyz"] = os.path.abspath(current_input)

            else:
                raise ValueError(f"未知 step type: {step_type}")

        except Exception as e:
            step_stats["status"] = "failed"
            step_stats["error"] = str(e)
            _save_checkpoint(i - 1, workflow_stats)
            raise
        finally:
            step_stats["end_time"] = datetime.now().isoformat()
            step_stats["duration_seconds"] = round(time.time() - step_start, 2)
            step_stats["output_conformers"] = (
                count_conformers_any(current_input)
                if isinstance(current_input, str)
                else count_conformers_any(current_input)
            )

            # 填充 Failed 列：仅对 calc/task step 尝试从 work/results.db 统计失败任务数。
            if step_type in ["calc", "task"]:
                db_path = os.path.join(step_dir, "work", "results.db")
                failed_n = _count_failed_tasks_in_results_db(db_path)
                if failed_n is not None:
                    step_stats["failed_conformers"] = failed_n

            # === Step footer summary ===
            dur = format_duration_hms(step_stats.get("duration_seconds", 0.0))
            status = step_stats.get("status", "unknown")
            out_xyz = step_stats.get("output_xyz")
            
            # 使用 Rich Panel 展示结果，更清晰
            from ..core.console import console
            from rich.panel import Panel
            from rich import box
            from rich.table import Table
            
            summary_grid = Table.grid(padding=(0, 2))
            summary_grid.add_column(style="bold white", justify="left")
            summary_grid.add_column(style="cyan", justify="right")
            
            summary_grid.add_row("Status:", f"[{'green' if status == 'completed' else 'yellow'}]{status}[/]")
            summary_grid.add_row("Duration:", dur)
            
            if step_type in ["calc", "task"]:
                db_path = os.path.join(step_dir, "work", "results.db")
                counts = _count_task_statuses_in_results_db(db_path)
                if counts is not None:
                    summary_grid.add_row("Stats:", f"Success={counts['success']}, Failed={counts['failed']}, Total={counts['total']}")

            if out_xyz:
                summary_grid.add_row("Output:", f"[link=file://{out_xyz}]{os.path.basename(out_xyz)}[/link]")

            if status == "failed":
                err = str(step_stats.get("error") or "").strip()
                if len(err) > 100:
                    err = err[:100] + "..."
                if err:
                    summary_grid.add_row("Error:", f"[red]{err}[/]")

            console.print(Panel(summary_grid, title=f"Step {i + 1} Summary", border_style="dim", expand=True, box=box.ASCII))
            console.print() # 空行


            workflow_stats["steps"].append(step_stats)
            if step_stats["status"] in ["completed", "skipped", "skipped_multi_frame"]:
                _save_checkpoint(i, workflow_stats)

    workflow_stats["end_time"] = datetime.now().isoformat()
    workflow_stats["final_output"] = current_input if isinstance(current_input, str) else None
    workflow_stats["final_conformers"] = count_conformers_any(current_input)
    workflow_stats["total_duration_seconds"] = round(
        time.time() - workflow_stats.pop("_start_ts", time.time()), 2
    )

    # === 低能构象溯源（按每步输出文件追踪，不做 RMSD） ===
    try:
        final_xyz = workflow_stats.get("final_output")
        if isinstance(final_xyz, str) and os.path.exists(final_xyz):
            final_confs = io_xyz.read_xyz_file(final_xyz, parse_metadata=True)
            io_xyz.ensure_conformer_cids(final_confs, prefix="final")

            rows = []
            for c in final_confs:
                meta = c.get("metadata") or {}
                if not isinstance(meta, dict):
                    continue
                cid = meta.get("CID")
                e = _extract_energy(meta)
                if cid is None or e is None:
                    continue
                rows.append((float(e), str(cid)))

            rows.sort(key=lambda x: x[0])
            top_k = 6
            top = []
            seen = set()
            for e, cid in rows:
                if cid in seen:
                    continue
                top.append({"cid": cid, "final_energy": e})
                seen.add(cid)
                if len(top) >= top_k:
                    break

            step_outputs = [
                {
                    "index": s.get("index"),
                    "name": s.get("name"),
                    "type": s.get("type"),
                    "output_xyz": s.get("output_xyz"),
                }
                for s in workflow_stats.get("steps", [])
                if s.get("output_xyz")
            ]

            # 为每个 step 输出构建索引
            step_indexes = []
            for s in step_outputs:
                xyz_path = s.get("output_xyz")
                if not isinstance(xyz_path, str) or not os.path.exists(xyz_path):
                    continue
                idx = _build_cid_index(xyz_path)
                step_indexes.append(
                    {
                        "step": s,
                        "cid_to_frame": idx["cid_to_frame"],
                        "rank_by_energy": idx["rank_by_energy"],
                    }
                )

            for item in top:
                cid = item["cid"]
                trace = []
                for entry in step_indexes:
                    s = entry["step"]
                    info = entry["cid_to_frame"].get(cid)
                    if info is None:
                        trace.append(
                            {
                                "step_index": s.get("index"),
                                "step_name": s.get("name"),
                                "step_type": s.get("type"),
                                "xyz": s.get("output_xyz"),
                                "status": "missing",
                            }
                        )
                        continue
                    trace.append(
                        {
                            "step_index": s.get("index"),
                            "step_name": s.get("name"),
                            "step_type": s.get("type"),
                            "xyz": s.get("output_xyz"),
                            "status": "found",
                            "frame_index": int(info["frame_index"]),
                            "rank_by_energy": entry["rank_by_energy"].get(cid),
                            "energy": info.get("energy"),
                        }
                    )
                item["trace"] = trace

            workflow_stats["low_energy_trace"] = {
                "top_k": len(top),
                "source_xyz": final_xyz,
                "conformers": top,
            }
    except Exception:
        pass

    if isinstance(current_input, str) and os.path.exists(current_input):
        confs = viz.parse_xyz_file(current_input)
        report_text = viz.generate_text_report(confs, stats=workflow_stats)
        if report_text:
            print(report_text)

        best_conf, best_energy, _ = viz.get_lowest_energy_conformer(confs)
        if best_conf is not None:
            best_meta = best_conf.get("metadata") or {}
            best_cid = best_meta.get("CID")
            input_dir = os.path.dirname(os.path.abspath(original_inputs[0]))
            input_base = os.path.splitext(os.path.basename(original_inputs[0]))[0]
            lowest_path = os.path.join(input_dir, f"{input_base}_lowest.xyz")
            io_xyz.write_xyz_file(lowest_path, [best_conf], atomic=True)
            workflow_stats["lowest_conformer"] = {
                "cid": best_cid,
                "energy": best_energy,
                "xyz_path": lowest_path,
            }
            # 关联 trace（若存在）
            try:
                trace_block = workflow_stats.get("low_energy_trace") or {}
                conformers = trace_block.get("conformers") or []
                if best_cid is not None:
                    for item in conformers:
                        if str(item.get("cid")) == str(best_cid):
                            workflow_stats["lowest_conformer"]["trace"] = item.get("trace")
                            break
            except Exception:
                pass
            if best_energy is not None:
                logger.info(f"已输出最低能量构象: {lowest_path} (E={best_energy:.6f} Ha)")
            else:
                logger.info(f"已输出最低能量构象: {lowest_path}")
        else:
            logger.warning("未找到带能量的构象，未输出最低能量构象 XYZ。")

    # 写入 workflow_stats.json（包含 low_energy_trace / lowest_conformer）
    stats_file = os.path.join(root_dir, "workflow_stats.json")
    with open(stats_file, "w", encoding="utf-8") as f:
        json.dump(workflow_stats, f, indent=2, ensure_ascii=False)

    return workflow_stats
