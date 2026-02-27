#!/usr/bin/env python3

"""Workflow configuration builder module."""

from __future__ import annotations

import os
import re
from typing import Any

from ..calc import format_orca_blocks
from ..config.defaults import (
    DEFAULT_CHARGE,
    DEFAULT_CORES_PER_TASK,
    DEFAULT_ENABLE_DYNAMIC_RESOURCES,
    DEFAULT_MAX_PARALLEL_JOBS,
    DEFAULT_MULTIPLICITY,
    DEFAULT_TOTAL_MEMORY,
)
from ..config.loader import load_workflow_config_file
from ..core.utils import parse_itask

__all__ = [
    "sanitize_step_dir_name",
    "build_step_dir_name_map",
    "load_workflow_config",
    "build_task_config",
    "create_runtask_config",
]


def sanitize_step_dir_name(name: Any, fallback: str) -> str:
    """Sanitize a step name into a safe directory name."""
    raw = str(name).strip() if name is not None else ""
    if not raw:
        raw = fallback

    raw = raw.replace(os.sep, "_")
    if os.altsep:
        raw = raw.replace(os.altsep, "_")

    safe = re.sub(r"[^A-Za-z0-9_.-]+", "_", raw)
    safe = re.sub(r"_+", "_", safe).strip("._-")
    return safe or fallback


def build_step_dir_name_map(steps: list[dict[str, Any]]) -> tuple[list[str], dict[str, str]]:
    """Build deterministic, unique directory names for workflow steps.

    Returns
    -------
        (dirnames_by_index, first_match_by_step_name)
    """
    used: dict[str, int] = {}
    dirnames: list[str] = []
    by_name: dict[str, str] = {}

    for idx, step in enumerate(steps, start=1):
        step_name = str(step.get("name", "")).strip()
        base = sanitize_step_dir_name(step_name, fallback=f"step_{idx:02d}")

        n = used.get(base, 0)
        dirname = base if n == 0 else f"{base}_{n + 1}"
        used[base] = n + 1

        dirnames.append(dirname)
        if step_name and step_name not in by_name:
            by_name[step_name] = dirname

    return dirnames, by_name


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


def load_workflow_config(config_file: str) -> dict[str, Any]:
    """Load a workflow configuration file."""
    return load_workflow_config_file(config_file)


def build_task_config(
    params: dict[str, Any],
    global_config: dict[str, Any],
    root_dir: str | None = None,
    all_steps: list[dict[str, Any]] | None = None,
) -> dict[str, str]:
    """Normalize workflow YAML parameters into a flat dict consumable by the calc module.

    Replaces the legacy ``create_runtask_config()`` by directly producing
    a ``Dict[str, str]``.

    Parameters
    ----------
    params : dict
        Step-level parameters from the YAML config.
    global_config : dict
        Global configuration section.
    root_dir : str or None
        Workflow root directory (used for cross-step chk resolution).
    all_steps : list of dict or None
        All workflow steps (used for cross-step chk resolution).

    Returns
    -------
    dict of str
        Flat configuration dictionary.
    """
    # Handle cross-step chk input
    final_params = dict(params)
    chk_from = (
        params.get("chk_from_step")
        or params.get("input_chk_from_step")
        or params.get("read_chk_from_step")
    )
    if chk_from and root_dir and all_steps:
        step_dirs, by_name = build_step_dir_name_map(all_steps)
        from_dir = None
        s = str(chk_from).strip()
        if s.isdigit():
            idx = int(s)
            if 1 <= idx <= len(all_steps):
                from_dir = step_dirs[idx - 1]
        else:
            from_dir = by_name.get(s)

        if from_dir:
            final_params["input_chk_dir"] = os.path.join(root_dir, from_dir, "backups")

    params = final_params

    def build_clean_opts(p: dict[str, Any], gc: dict[str, Any]) -> str:
        clean_params = p.get("clean_params")
        if clean_params:
            return str(clean_params)

        opts: list[str] = []
        if p.get("dedup_only"):
            opts.append("--dedup-only")
        if p.get("keep_all_topos"):
            opts.append("--keep-all-topos")

        no_h = p.get("noH")
        if no_h is None:
            no_h = gc.get("noH")
        if bool(no_h):
            opts.append("--noH")

        rmsd = p.get("rmsd_threshold", gc.get("rmsd_threshold"))
        if rmsd is not None:
            opts.append(f"-t {rmsd}")

        if "energy_window" in p and p.get("energy_window") is not None:
            opts.append(f"-ewin {p['energy_window']}")

        etol = p.get("energy_tolerance")
        if etol is None:
            etol = gc.get("energy_tolerance")
        if etol is not None:
            opts.append(f"--energy-tolerance {etol}")

        return " ".join(opts)

    def _parse_two_atom_indices(val):
        if val is None:
            return None
        if isinstance(val, (list, tuple)):
            nums = []
            for x in val:
                try:
                    nums.append(int(x))
                except (ValueError, TypeError):
                    continue
        else:
            nums = []
            for m in re.findall(r"\d+", str(val)):
                try:
                    nums.append(int(m))
                except (ValueError, TypeError):
                    continue
        if len(nums) >= 2:
            a, b = nums[0], nums[1]
            if a > 0 and b > 0 and a != b:
                return f"{a},{b}"
        return None

    # Initialize configuration dict (corresponds to INI DEFAULT + Task sections)
    config: dict[str, str] = {
        "gaussian_path": str(global_config.get("gaussian_path", "g16")),
        "orca_path": str(global_config.get("orca_path", "orca")),
        "cores_per_task": str(
            params.get(
                "cores_per_task", global_config.get("cores_per_task", DEFAULT_CORES_PER_TASK)
            )
        ),
        "total_memory": str(
            params.get(
                "total_memory",
                global_config.get(
                    "total_memory", global_config.get("mem_per_task", DEFAULT_TOTAL_MEMORY)
                ),
            )
        ),
        "max_parallel_jobs": str(
            params.get(
                "max_parallel_jobs",
                global_config.get("max_parallel_jobs", DEFAULT_MAX_PARALLEL_JOBS),
            )
        ),
        "charge": str(params.get("charge", global_config.get("charge", DEFAULT_CHARGE))),
        "multiplicity": str(
            params.get("multiplicity", global_config.get("multiplicity", DEFAULT_MULTIPLICITY))
        ),
        "enable_dynamic_resources": str(
            params.get(
                "enable_dynamic_resources",
                global_config.get("enable_dynamic_resources", DEFAULT_ENABLE_DYNAMIC_RESOURCES),
            )
        ).lower(),
        "auto_clean": "true",
        "delete_work_dir": "true",
    }

    # Handle input_chk_dir / gaussian_write_chk
    for key in ["input_chk_dir", "gaussian_write_chk"]:
        val = params.get(key, global_config.get(key))
        if val is not None and str(val).strip() != "":
            config[key] = str(val).strip()

    # orca_maxcore
    orca_maxcore = params.get(
        "orca_maxcore", global_config.get("orca_maxcore", global_config.get("maxcore"))
    )
    if orca_maxcore is not None and str(orca_maxcore).strip():
        config["orca_maxcore"] = str(orca_maxcore)

    itask_int = parse_itask(params.get("itask", "opt"))
    itask_str = _itask_label(params.get("itask", "opt"))

    # freeze only applies to opt/opt_freq
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

    config["itask"] = itask_str
    config["iprog"] = _normalize_iprog_label(params.get("iprog", "orca"))
    config["freeze"] = str(freeze_val)
    config["clean_opts"] = str(build_clean_opts(params, global_config))

    # TS: rescue + scan params
    ts_pair = _parse_two_atom_indices(params.get("ts_bond_atoms"))
    if ts_pair is None:
        ts_pair = _parse_two_atom_indices(global_config.get("ts_bond_atoms"))
    if ts_pair is None:
        ts_pair = _parse_two_atom_indices(params.get("freeze", global_config.get("freeze")))

    if ts_pair is not None:
        config["ts_bond_atoms"] = ts_pair

    if itask_int == 4:
        rescue_val = params.get("ts_rescue_scan", global_config.get("ts_rescue_scan", False))
        config["ts_rescue_scan"] = str(bool(rescue_val)).lower()

        for k in [
            "scan_coarse_step",
            "scan_fine_step",
            "scan_uphill_limit",
            "scan_max_steps",
            "scan_fine_half_window",
            "ts_rescue_keep_scan_dirs",
            "ts_rescue_scan_backup",
        ]:
            val = params.get(k, global_config.get(k))
            if val is not None:
                config[k] = str(val)

    # Task keyword and block
    kw = params.get("keyword", global_config.get("keyword"))
    if kw:
        config["keyword"] = str(kw)

    blocks = params.get("blocks")
    if blocks:
        if isinstance(blocks, dict):
            config["blocks"] = format_orca_blocks(blocks)
        else:
            config["blocks"] = str(blocks)

    for k in ["solvent_block", "custom_block"]:
        if k in params:
            config[k] = str(params[k])

    # Capture any other parameters that may be missing
    excluded = {
        "itask",
        "iprog",
        "freeze",
        "clean_params",
        "rmsd_threshold",
        "energy_window",
        "blocks",
        "keyword",
        "solvent_block",
        "custom_block",
    }
    for k, v in params.items():
        if k not in config and k not in excluded and v is not None:
            config[k] = str(v)

    return {k: v for k, v in config.items() if v is not None and v != ""}


def create_runtask_config(filename: str, params: dict[str, Any], global_config: dict[str, Any]):
    """Legacy compatibility: write build_task_config output to an INI file."""
    import configparser

    config_dict = build_task_config(params, global_config)

    cfg = configparser.ConfigParser(interpolation=None)
    cfg.optionxform = str

    # Split entries into DEFAULT and Task sections
    default_keys = {
        "gaussian_path",
        "orca_path",
        "cores_per_task",
        "total_memory",
        "max_parallel_jobs",
        "charge",
        "multiplicity",
        "enable_dynamic_resources",
        "auto_clean",
        "delete_work_dir",
        "input_chk_dir",
        "gaussian_write_chk",
        "ts_bond_atoms",
        "orca_maxcore",
        "ts_rescue_scan",
        "scan_coarse_step",
        "scan_fine_step",
        "scan_uphill_limit",
        "scan_max_steps",
        "scan_fine_half_window",
        "ts_rescue_keep_scan_dirs",
        "ts_rescue_scan_backup",
    }

    cfg["DEFAULT"] = {k: v for k, v in config_dict.items() if k in default_keys}
    cfg["Task"] = {k: v for k, v in config_dict.items() if k not in default_keys}

    os.makedirs(os.path.dirname(filename) or ".", exist_ok=True)
    with open(filename, "w", encoding="utf-8") as f:
        cfg.write(f)
