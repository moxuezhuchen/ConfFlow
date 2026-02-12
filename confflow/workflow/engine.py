#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""工作流执行引擎（从 confflow.main 拆分）。

设计目标
- 纯业务逻辑：不做 sys.exit
- 便于测试：核心入口 `run_workflow()` 接受显式参数
"""

from __future__ import annotations

import os
import shutil
import sys
import time
from datetime import datetime
from typing import Any, Dict, List, Optional, Union

from ..blocks import confgen, viz
from .. import calc
from ..core.types import TaskStatus

from ..core import io as io_xyz
from ..config.schema import ConfigSchema
from ..core.console import console, DOUBLE_LINE, SINGLE_LINE, LINE_WIDTH
from ..core.utils import (
    format_duration_hms,
    format_index_ranges,
    get_logger,
    parse_index_spec,
    parse_itask,
)

# 引入抽离的模块
from .helpers import (
    pushd, 
    as_list, 
    normalize_pair_list, 
    count_conformers_any, 
    is_multi_frame_any
)
from .validation import validate_inputs_compatible
from .config_builder import build_task_config
from .stats import (
    CheckpointManager,
    WorkflowStatsTracker,
    TaskStatsCollector,
    FailureTracker,
    Tracer
)

logger = get_logger()

# 从拆分模块导入，消除重复定义
from .config_builder import (
    _normalize_iprog_label,
    _itask_label,
    load_workflow_config,
    create_runtask_config,
    build_step_dir_name_map,
)
from .helpers import count_conformers_in_xyz
from .stats import count_task_statuses_in_results_db as _count_task_statuses_in_results_db

def _run_confgen_step(
    step_dir: str, current_input: Union[str, List[str]], params: Dict[str, Any], input_files: List[str]
) -> str:
    """执行构象生成步骤"""
    expected_output = os.path.join(step_dir, "search.xyz")
    multi_frame = len(input_files) == 1 and is_multi_frame_any(current_input)

    if multi_frame and isinstance(current_input, str):
        shutil.copy2(current_input, expected_output)
    elif not os.path.exists(expected_output):
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
                chains=as_list(params.get("chains", params.get("chain"))),
                chain_steps=as_list(params.get("chain_steps", params.get("steps"))),
                chain_angles=as_list(params.get("chain_angles", params.get("angles"))),
                rotate_side=params.get("rotate_side", "left"),
            )
        if not os.path.exists(expected_output):
            raise RuntimeError("confgen did not generate search.xyz")
    return expected_output


def _run_calc_step(
    step_dir: str, 
    current_input: Union[str, List[str]], 
    params: Dict[str, Any], 
    global_config: Dict[str, Any], 
    root_dir: str, 
    steps: List[Dict[str, Any]],
    failure_tracker: FailureTracker,
    step_name: str
) -> str:
    """执行计算任务步骤"""
    task_config = build_task_config(params, global_config, root_dir, steps)
    ConfigSchema.validate_calc_config(task_config)

    expected_clean = os.path.join(step_dir, "output.xyz")
    expected_raw = os.path.join(step_dir, "result.xyz")

    if os.path.exists(expected_clean) or os.path.exists(expected_raw):
        final_input = expected_clean if os.path.exists(expected_clean) else expected_raw
        step_failed = os.path.join(step_dir, "failed.xyz")
        if os.path.exists(step_failed):
            failure_tracker.append(step_failed, step_name)
        return final_input

    manager = calc.ChemTaskManager(task_config)
    manager.work_dir = step_dir
    manager.run(input_xyz_file=current_input if isinstance(current_input, str) else current_input[0])

    work_cleaned = os.path.join(step_dir, "output.xyz")
    work_raw = os.path.join(step_dir, "result.xyz")
    work_failed = os.path.join(step_dir, "failed.xyz")
    
    if os.path.exists(work_cleaned):
        final_input = work_cleaned
    elif os.path.exists(work_raw):
        final_input = work_raw
    else:
        raise RuntimeError("计算任务未产生预期输出")

    if os.path.exists(work_failed):
        failure_tracker.append(work_failed, step_name)
    
    return final_input


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
    step_dirnames, _ = build_step_dir_name_map(steps)

    # 预加载 confgen 参数用于多输入柔性链一致性检查
    confgen_params = None
    if len(input_files) > 1:
        for step in steps:
             if step.get("type", "").lower() == "confgen":
                 confgen_params = step.get("params", {})
                 break
        validate_inputs_compatible(input_files, confgen_params, force_consistency=global_config.get("force_consistency", False))

    root_dir = os.path.abspath(work_dir)
    os.makedirs(root_dir, exist_ok=True)
    failed_dir = os.path.join(root_dir, "failed")
    os.makedirs(failed_dir, exist_ok=True)
    
    try:
        shutil.copy2(config_file, os.path.join(failed_dir, os.path.basename(config_file)))
    except Exception:
        pass

    if hasattr(logger, "add_file_handler"):
        logger.add_file_handler(os.path.join(root_dir, "confflow.log"))

    checkpoint = CheckpointManager(root_dir)
    stats_tracker = WorkflowStatsTracker(input_files, original_inputs)
    failure_tracker = FailureTracker(failed_dir)
    
    if not resume:
        failure_tracker.clear_previous()

    resume_from_step = checkpoint.load() if resume else -1
    current_input: Union[str, List[str]] = input_files[0] if len(input_files) == 1 else input_files

    # === 打印工作流开始头部 ===
    from ..core.console import DOUBLE_LINE
    input_basename = os.path.basename(input_files[0]) if len(input_files) == 1 else f"{len(input_files)} files"
    console.print(DOUBLE_LINE)
    console.print(f"{'ConfFlow v1.0':^80}")
    started_str = 'Started: ' + datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    console.print(f"{started_str:^80}")
    initial_count = count_conformers_any(current_input)
    conf_str = f"conformer{'s' if initial_count > 1 else ''}"
    input_str = f"Input: {input_basename} ({initial_count} {conf_str})"
    console.print(f"{input_str:^80}")
    console.print(DOUBLE_LINE)

    for i, step in enumerate(steps):
        if resume_from_step >= i:
            # 如果是恢复且该步已完成，需要更新 current_input 为该步的输出
            step_dir = os.path.join(root_dir, step_dirnames[i])
            expected_output = os.path.join(step_dir, "output.xyz")
            if not os.path.exists(expected_output):
                expected_output = os.path.join(step_dir, "result.xyz")
            if not os.path.exists(expected_output):
                expected_output = os.path.join(step_dir, "search.xyz")
            if os.path.exists(expected_output):
                current_input = expected_output
            continue
            
        if not step.get("enabled", True):
            continue

        step_name = step["name"]
        step_type = step["type"]
        step_dir = os.path.join(root_dir, step_dirnames[i])
        os.makedirs(step_dir, exist_ok=True)

        step_start = time.time()
        in_n = count_conformers_any(current_input)
        
        step_stats = {
            "name": step_name,
            "type": step_type,
            "index": i + 1,
            "input_conformers": in_n,
            "start_time": datetime.now().isoformat(),
        }

        params = step.get("params", {}) or {}

        # === Step header ===
        total_steps = len(steps)
        if step_type in ["calc", "task"]:
            merged = {**global_config, **params}
            iprog = _normalize_iprog_label(merged.get("iprog", "orca"))
            itask = _itask_label(merged.get("itask", "opt"))
            cores = merged.get("cores_per_task", 4)
            mem = merged.get("total_memory", "4GB")
            max_jobs = merged.get("max_parallel_jobs", 4)

            itask_int = parse_itask(merged.get("itask", "opt"))
            freeze_raw = merged.get("freeze", "0") if itask_int in [0, 3] else "0"
            freeze_idx = parse_index_spec(freeze_raw)
            freeze_fmt = format_index_ranges(freeze_idx)
            freeze_show = f"{freeze_fmt} ({len(freeze_idx)})" if freeze_idx else "none"

            console.print()
            header = f"[Step {i + 1}/{total_steps}] {step_name} | {step_type} ({iprog}/{itask})"
            right_info = f"Input: {in_n}"
            padding = LINE_WIDTH - len(header) - len(right_info)
            console.print(f"{header}{' ' * max(1, padding)}{right_info}")
            console.print(SINGLE_LINE)
            
            kw = merged.get("keyword")
            if kw and str(kw).strip():
                console.print(f"  Keyword : {str(kw).strip()}")
            console.print(f"  Resource: {max_jobs} jobs × {cores} cores, {mem:<12} Freeze: {freeze_show}")
        else:
            console.print()
            header = f"[Step {i + 1}/{total_steps}] {step_name} | {step_type}"
            right_info = f"Input: {in_n}"
            padding = LINE_WIDTH - len(header) - len(right_info)
            console.print(f"{header}{' ' * max(1, padding)}{right_info}")
            console.print(SINGLE_LINE)

        try:
            if step_type in ["confgen", "gen"]:
                multi_frame = len(input_files) == 1 and is_multi_frame_any(current_input)
                expected_output = os.path.join(step_dir, "search.xyz")
                
                if multi_frame and isinstance(current_input, str):
                    step_stats["status"] = TaskStatus.SKIPPED_MULTI
                elif os.path.exists(expected_output):
                    step_stats["status"] = TaskStatus.SKIPPED
                
                current_input = _run_confgen_step(step_dir, current_input, params, input_files)
                io_xyz.ensure_xyz_cids(current_input, prefix=f"s{i+1:02d}")
                if step_stats.get("status") not in [TaskStatus.SKIPPED_MULTI, TaskStatus.SKIPPED]:
                    step_stats["status"] = TaskStatus.COMPLETED

            elif step_type in ["calc", "task"]:
                expected_clean = os.path.join(step_dir, "output.xyz")
                expected_raw = os.path.join(step_dir, "result.xyz")

                if os.path.exists(expected_clean) or os.path.exists(expected_raw):
                    step_stats["status"] = TaskStatus.SKIPPED
                
                current_input = _run_calc_step(
                    step_dir, current_input, params, global_config, root_dir, steps, failure_tracker, step_name
                )
                io_xyz.ensure_xyz_cids(current_input, prefix=f"s{i+1:02d}")
                if step_stats.get("status") != TaskStatus.SKIPPED:
                    step_stats["status"] = TaskStatus.COMPLETED

            step_stats["output_xyz"] = os.path.abspath(current_input)

        except Exception as e:
            step_stats["status"] = TaskStatus.FAILED
            step_stats["error"] = str(e)
            checkpoint.save(i - 1, stats_tracker.get_stats())
            raise
        finally:
            step_stats["end_time"] = datetime.now().isoformat()
            step_stats["duration_seconds"] = round(time.time() - step_start, 2)
            step_stats["output_conformers"] = count_conformers_any(current_input)

            failed_count = 0
            if step_type in ["calc", "task"]:
                db_path = os.path.join(step_dir, "results.db")
                failed_count = TaskStatsCollector.count_failed(db_path) or 0
                step_stats["failed_conformers"] = failed_count

            # === Step footer summary ===
            dur = format_duration_hms(step_stats["duration_seconds"])
            status = step_stats["status"]
            mark = "✓" if status in (TaskStatus.COMPLETED, TaskStatus.SKIPPED, TaskStatus.SKIPPED_MULTI) else "✗"
            failed_str = f" ({failed_count} failed)" if failed_count > 0 else ""
            console.print(f"  {mark} {status.capitalize()} | {in_n} → {step_stats['output_conformers']}{failed_str} | {dur}")
            if status == TaskStatus.FAILED:
                console.print(f"  Error: {step_stats.get('error')}")
            console.print()

            stats_tracker.add_step(step_stats)
            if step_stats["status"] in [TaskStatus.COMPLETED, TaskStatus.SKIPPED, TaskStatus.SKIPPED_MULTI]:
                checkpoint.save(i, stats_tracker.get_stats())

    final_stats = stats_tracker.finalize(current_input)
    
    # 溯源
    try:
        Tracer.trace_low_energy(final_stats)
    except Exception as e:
        logger.debug(f"Trace failed: {e}")

    # 报告与最低能量输出
    if isinstance(current_input, str) and os.path.exists(current_input):
        confs = viz.parse_xyz_file(current_input)
        report_text = viz.generate_text_report(confs, stats=final_stats)
        if report_text:
            # CLI redirects stdout to <input>.txt (isatty=False). When used as a library from a TTY,
            # keep silent by default.
            if not sys.stdout.isatty():
                print(report_text)

        best_conf, best_energy, _ = viz.get_lowest_energy_conformer(confs)
        if best_conf:
            input_dir = os.path.dirname(os.path.abspath(original_inputs[0]))
            input_base = os.path.splitext(os.path.basename(original_inputs[0]))[0]
            lowest_path = os.path.join(input_dir, f"{input_base}min.xyz")
            io_xyz.write_xyz_file(lowest_path, [best_conf], atomic=True)
            
            best_meta = best_conf.get("metadata") or {}
            final_stats["lowest_conformer"] = {
                "cid": best_meta.get("CID"),
                "energy": best_energy,
                "xyz_path": lowest_path,
            }
            logger.info(f"已输出最低能量构象: {lowest_path}")

    # 写入最终统计
    stats_file = os.path.join(root_dir, "workflow_stats.json")
    with open(stats_file, "w", encoding="utf-8") as f:
        import json
        json.dump(final_stats, f, indent=2, ensure_ascii=False)

    return final_stats
