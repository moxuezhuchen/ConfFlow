#!/usr/bin/env python3

"""Known calc parameter sets for task configuration."""

from __future__ import annotations

__all__ = [
    "_KNOWN_CALC_PARAMS_BASE",
    "_KNOWN_CALC_PARAMS_STRUCTURED",
    "_EXPLICIT_CALC_CONFIG_FIELDS",
]

# Known calc parameter names (single source of truth)
_KNOWN_CALC_PARAMS_BASE = frozenset(
    {
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
        "clean_opts",
        "auto_clean",
        "delete_work_dir",
        "gaussian_path",
        "orca_path",
        "allowed_executables",
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
        "max_wall_time_seconds",
    }
)

# Structured builder accepts additional execution-related parameters
_KNOWN_CALC_PARAMS_STRUCTURED = _KNOWN_CALC_PARAMS_BASE | {"sandbox_root", "input_chk_dir"}

# Parameters explicitly mapped to CalcTaskConfig fields (for extra field exclusion)
_EXPLICIT_CALC_CONFIG_FIELDS = frozenset(
    {
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
        "energy_tolerance",
        "clean_params",
        "clean_opts",
        "auto_clean",
        "delete_work_dir",
        "allowed_executables",
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
        "max_wall_time_seconds",
        "sandbox_root",
        "input_chk_dir",
    }
)
