#!/usr/bin/env python3

"""Workflow presentation and reporting utilities."""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime
from typing import Any

from ..blocks import viz
from ..config.defaults import (
    DEFAULT_CORES_PER_TASK,
    DEFAULT_MAX_PARALLEL_JOBS,
    DEFAULT_TOTAL_MEMORY,
)
from ..core import io as io_xyz
from ..core.console import (
    console,
    print_kv,
    print_step_header,
    print_step_result,
    print_workflow_header,
)
from ..core.types import TaskStatus
from ..core.utils import format_duration_hms, format_index_ranges, parse_index_spec, parse_itask
from .config_builder import _itask_label, _normalize_iprog_label
from .helpers import count_conformers_any

__all__ = [
    "print_workflow_start",
    "print_step_header_block",
    "print_step_footer_block",
    "emit_final_report_and_lowest",
    "build_run_summary",
    "write_final_statistics",
]


def print_workflow_start(input_files: list[str], current_input: str | list[str]) -> None:
    input_basename = (
        os.path.basename(input_files[0]) if len(input_files) == 1 else f"{len(input_files)} files"
    )
    initial_count = count_conformers_any(current_input)
    print_workflow_header(input_basename, initial_count)


def print_step_header_block(
    step_index: int,
    total_steps: int,
    step_name: str,
    step_type: str,
    global_config: dict[str, Any],
    params: dict[str, Any],
    in_count: int,
) -> None:
    if step_type in ["calc", "task"]:
        merged = {**global_config, **params}
        iprog = _normalize_iprog_label(merged.get("iprog", "orca"))
        itask = _itask_label(merged.get("itask", "opt"))
        cores = merged.get("cores_per_task", DEFAULT_CORES_PER_TASK)
        mem = merged.get("total_memory", DEFAULT_TOTAL_MEMORY)
        max_jobs = merged.get("max_parallel_jobs", DEFAULT_MAX_PARALLEL_JOBS)

        itask_int = parse_itask(merged.get("itask", "opt"))
        freeze_raw = merged.get("freeze", "0") if itask_int in [0, 3] else "0"
        freeze_idx = parse_index_spec(freeze_raw)
        freeze_fmt = format_index_ranges(freeze_idx)
        freeze_show = f"{freeze_fmt} ({len(freeze_idx)})" if freeze_idx else "none"

        print_step_header(
            step_index, total_steps, step_name, f"{step_type} ({iprog}/{itask})", in_count
        )

        kw = merged.get("keyword")
        if kw and str(kw).strip():
            print_kv("Keyword", str(kw).strip())
        print_kv("Resource", f"jobs={max_jobs} | cores/job={cores} | mem={mem}")
        print_kv("Freeze", freeze_show)

        rmsd = merged.get("rmsd_threshold", 1.0)
        ewin = merged.get("energy_window", "none")
        ewin_str = f"{ewin} kcal/mol" if str(ewin).lower() != "none" else "none"
        print_kv("Refine", f"RMSD={rmsd} | E-window={ewin_str}")
        return

    print_step_header(step_index, total_steps, step_name, step_type, in_count)


def print_step_footer_block(
    step_stats: dict[str, Any],
    in_count: int,
    failed_count: int,
) -> None:
    dur = format_duration_hms(step_stats["duration_seconds"])
    status = step_stats["status"]
    print_step_result(status, in_count, step_stats["output_conformers"], failed_count, dur)
    if status == TaskStatus.FAILED:
        print_kv("Error", str(step_stats.get("error", "unknown")))
    console.print()


def emit_final_report_and_lowest(
    current_input: str | list[str],
    original_inputs: list[str],
    final_stats: dict[str, Any],
    logger: Any,
) -> None:
    if not (isinstance(current_input, str) and os.path.exists(current_input)):
        return

    confs = viz.parse_xyz_file(current_input)
    report_text = viz.generate_text_report(confs, stats=final_stats)
    if report_text and not sys.stdout.isatty():
        print(report_text)

    best_conf, best_energy, _ = viz.get_lowest_energy_conformer(confs)
    if not best_conf:
        return

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
    logger.info(f"Wrote the lowest-energy conformer to: {lowest_path}")


def build_run_summary(final_stats: dict[str, Any]) -> dict[str, Any]:
    """Build a concise machine-readable run summary."""

    def _status_value(raw: Any) -> str:
        if hasattr(raw, "value"):
            return str(raw.value)
        return str(raw)

    steps = list(final_stats.get("steps", []) or [])
    step_status_counts: dict[str, int] = {}
    compact_steps = []

    for step in steps:
        status = _status_value(step.get("status", "unknown"))
        step_status_counts[status] = step_status_counts.get(status, 0) + 1
        compact_steps.append(
            {
                "index": step.get("index"),
                "name": step.get("name"),
                "type": step.get("type"),
                "status": status,
                "input_conformers": step.get("input_conformers"),
                "output_conformers": step.get("output_conformers"),
                "failed_conformers": step.get("failed_conformers"),
                "duration_seconds": step.get("duration_seconds"),
                "output_xyz": step.get("output_xyz"),
            }
        )

    summary: dict[str, Any] = {
        "generated_at": datetime.now().isoformat(),
        "input_files": final_stats.get("input_files", []),
        "original_input_files": final_stats.get("original_input_files", []),
        "initial_conformers": final_stats.get("initial_conformers", 0),
        "final_conformers": final_stats.get("final_conformers", 0),
        "final_output": final_stats.get("final_output"),
        "final_outputs": final_stats.get("final_outputs", []),
        "total_duration_seconds": final_stats.get("total_duration_seconds", 0),
        "step_status_counts": step_status_counts,
        "steps": compact_steps,
        "lowest_conformer": final_stats.get("lowest_conformer"),
    }

    low_energy_trace = final_stats.get("low_energy_trace")
    if isinstance(low_energy_trace, dict):
        trace_rows = []
        for row in low_energy_trace.get("conformers", []) or []:
            trace_rows.append(
                {
                    "cid": row.get("cid"),
                    "final_energy": row.get("final_energy"),
                }
            )
        summary["low_energy_trace"] = {
            "top_k": low_energy_trace.get("top_k", len(trace_rows)),
            "conformers": trace_rows,
        }

    return summary


def write_final_statistics(root_dir: str, final_stats: dict[str, Any]) -> None:
    """Persist detailed workflow stats and a compact run summary."""
    stats_file = os.path.join(root_dir, "workflow_stats.json")
    with open(stats_file, "w", encoding="utf-8") as f:
        json.dump(final_stats, f, indent=2, ensure_ascii=False)

    run_summary_file = os.path.join(root_dir, "run_summary.json")
    with open(run_summary_file, "w", encoding="utf-8") as f:
        json.dump(build_run_summary(final_stats), f, indent=2, ensure_ascii=False)
