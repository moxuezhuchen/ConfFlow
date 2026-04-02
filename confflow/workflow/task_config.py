#!/usr/bin/env python3

"""Workflow task configuration assembly helpers."""

from __future__ import annotations

import configparser
import logging
import os
from typing import Any

from ..calc import format_orca_blocks
from ..config.defaults import (
    DEFAULT_CHARGE,
    DEFAULT_CORES_PER_TASK,
    DEFAULT_ENABLE_DYNAMIC_RESOURCES,
    DEFAULT_MAX_PARALLEL_JOBS,
    DEFAULT_MULTIPLICITY,
    DEFAULT_RESUME_FROM_BACKUPS,
    DEFAULT_TOTAL_MEMORY,
)
from ..core.models import _coerce_freeze_indices, _coerce_two_atom_indices
from ..core.utils import parse_itask
from .step_naming import build_step_dir_name_map

logger = logging.getLogger("confflow.workflow.config_builder")

__all__ = [
    "_itask_label",
    "_normalize_iprog_label",
    "build_task_config",
    "create_runtask_config",
]


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


def _format_freeze_value(value: Any) -> str:
    """Format freeze indices into the flat CSV string expected by calc config."""
    indices = _coerce_freeze_indices(value)
    return ",".join(str(x) for x in indices) if indices else "0"


def _format_ts_bond_pair(value: Any) -> str | None:
    """Format a two-atom index value into ``a,b`` when possible."""
    try:
        pair = _coerce_two_atom_indices(value)
    except (TypeError, ValueError):
        pair = None
    if pair is not None:
        a, b = pair
        if a > 0 and b > 0 and a != b:
            return f"{a},{b}"
        return None

    # Legacy fallback: when freeze carries multiple atoms, keep using the first pair.
    indices = _coerce_freeze_indices(value)
    if len(indices) >= 2:
        a, b = indices[0], indices[1]
        if a > 0 and b > 0 and a != b:
            return f"{a},{b}"
    return None


def _coerce_bool_flag(value: Any) -> bool:
    """Interpret common YAML/INI boolean spellings without Python truthiness surprises."""
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "yes", "on"}:
            return True
        if normalized in {"0", "false", "no", "off", ""}:
            return False
    if isinstance(value, (int, float)):
        return bool(value)
    return bool(value)


def _build_clean_opts(params: dict[str, Any], global_config: dict[str, Any]) -> str:
    clean_params = params.get("clean_params")
    if clean_params:
        return str(clean_params)

    opts: list[str] = []
    if _coerce_bool_flag(params.get("dedup_only")):
        opts.append("--dedup-only")
    if _coerce_bool_flag(params.get("keep_all_topos")):
        opts.append("--keep-all-topos")

    no_h = params.get("noH")
    if no_h is None:
        no_h = global_config.get("noH")
    if _coerce_bool_flag(no_h):
        opts.append("--noH")

    rmsd = params.get("rmsd_threshold", global_config.get("rmsd_threshold"))
    if rmsd is not None:
        opts.append(f"-t {rmsd}")

    ewin = params.get("energy_window")
    if ewin is None:
        ewin = global_config.get("energy_window")
    if ewin is not None:
        opts.append(f"-ewin {ewin}")

    etol = params.get("energy_tolerance")
    if etol is None:
        etol = global_config.get("energy_tolerance")
    if etol is not None:
        opts.append(f"--energy-tolerance {etol}")

    return " ".join(opts)


def _build_base_task_config(params: dict[str, Any], global_config: dict[str, Any]) -> dict[str, str]:
    """Build the base flat config shared by all calc tasks."""
    return {
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
                global_config.get("total_memory", DEFAULT_TOTAL_MEMORY),
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
            _coerce_bool_flag(
                params.get(
                    "enable_dynamic_resources",
                    global_config.get(
                        "enable_dynamic_resources", DEFAULT_ENABLE_DYNAMIC_RESOURCES
                    ),
                )
            )
        ).lower(),
        "resume_from_backups": str(
            _coerce_bool_flag(
                params.get(
                    "resume_from_backups",
                    global_config.get("resume_from_backups", DEFAULT_RESUME_FROM_BACKUPS),
                )
            )
        ).lower(),
        "auto_clean": "true",
        "delete_work_dir": "true",
    }


def build_task_config(
    params: dict[str, Any],
    global_config: dict[str, Any],
    root_dir: str | None = None,
    all_steps: list[dict[str, Any]] | None = None,
) -> dict[str, str]:
    """Normalize workflow YAML parameters into a flat dict consumable by the calc module."""
    final_params = dict(params)
    chk_from = params.get("chk_from_step")
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
    config = _build_base_task_config(params, global_config)

    for key in ["input_chk_dir", "gaussian_write_chk"]:
        val = params.get(key, global_config.get(key))
        if val is not None and str(val).strip() != "":
            config[key] = str(val).strip()

    orca_maxcore = params.get(
        "orca_maxcore", global_config.get("orca_maxcore", global_config.get("maxcore"))
    )
    if orca_maxcore is not None and str(orca_maxcore).strip():
        config["orca_maxcore"] = str(orca_maxcore)

    itask_int = parse_itask(params.get("itask", "opt"))
    itask_str = _itask_label(params.get("itask", "opt"))

    if itask_int in [0, 3]:
        freeze_val = params.get("freeze", global_config.get("freeze", "0"))
    else:
        freeze_val = "0"

    config["itask"] = itask_str
    config["iprog"] = _normalize_iprog_label(params.get("iprog", "orca"))
    config["freeze"] = _format_freeze_value(freeze_val)
    config["clean_opts"] = _build_clean_opts(params, global_config)

    ts_pair = _format_ts_bond_pair(params.get("ts_bond_atoms"))
    if ts_pair is None:
        ts_pair = _format_ts_bond_pair(global_config.get("ts_bond_atoms"))
    if ts_pair is None:
        ts_pair = _format_ts_bond_pair(params.get("freeze", global_config.get("freeze")))

    if ts_pair is not None:
        config["ts_bond_atoms"] = ts_pair

    if itask_int == 4:
        rescue_val = params.get("ts_rescue_scan", global_config.get("ts_rescue_scan", False))
        config["ts_rescue_scan"] = str(_coerce_bool_flag(rescue_val)).lower()

        for key in [
            "ts_bond_drift_threshold",
            "ts_rmsd_threshold",
            "scan_coarse_step",
            "scan_fine_step",
            "scan_uphill_limit",
            "scan_max_steps",
            "scan_fine_half_window",
            "ts_rescue_keep_scan_dirs",
            "ts_rescue_scan_backup",
        ]:
            val = params.get(key, global_config.get(key))
            if val is not None:
                config[key] = str(val)

    kw = params.get("keyword", global_config.get("keyword"))
    if kw:
        config["keyword"] = str(kw)

    blocks = params.get("blocks")
    if blocks:
        if isinstance(blocks, dict):
            config["blocks"] = format_orca_blocks(blocks)
        else:
            config["blocks"] = str(blocks)

    known_calc_params = {
        "iprog",
        "itask",
        "keyword",
        "cores_per_task",
        "total_memory",
        "max_parallel_jobs",
        "charge",
        "multiplicity",
        "freeze",
        "energy_window",
        "rmsd_threshold",
        "noH",
        "dedup_only",
        "keep_all_topos",
        "max_conformers",
        "imag",
        "energy_tolerance",
        "clean_params",
        "gaussian_path",
        "orca_path",
        "orca_maxcore",
        "blocks",
        "gaussian_write_chk",
        "gaussian_modredundant",
        "gaussian_link0",
        "chk_from_step",
        "ts_bond_atoms",
        "ts_rescue_scan",
        "ts_bond_drift_threshold",
        "ts_rmsd_threshold",
        "scan_coarse_step",
        "scan_fine_step",
        "scan_uphill_limit",
        "scan_max_steps",
        "scan_fine_half_window",
        "ts_rescue_keep_scan_dirs",
        "ts_rescue_scan_backup",
        "ibkout",
        "enable_dynamic_resources",
        "resume_from_backups",
    }
    for key, value in params.items():
        if key not in known_calc_params:
            logger.warning("build_task_config: unknown parameter '%s' ignored", key)
            continue
        if key not in config and value is not None:
            config[key] = str(value)

    return {key: value for key, value in config.items() if value is not None and value != ""}


def create_runtask_config(filename: str, params: dict[str, Any], global_config: dict[str, Any]):
    """Legacy compatibility: write build_task_config output to an INI file."""
    config_dict = build_task_config(params, global_config)

    cfg = configparser.ConfigParser(interpolation=None)
    cfg.optionxform = str

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

    cfg["DEFAULT"] = {key: value for key, value in config_dict.items() if key in default_keys}
    cfg["Task"] = {key: value for key, value in config_dict.items() if key not in default_keys}

    os.makedirs(os.path.dirname(filename) or ".", exist_ok=True)
    with open(filename, "w", encoding="utf-8") as f:
        cfg.write(f)
