#!/usr/bin/env python3

"""
ConfFlow Config Schema - Configuration parameter normalization module.

Provides unified configuration parameter validation and normalization,
reducing complexity in the YAML -> INI -> dict conversion chain.
"""

from __future__ import annotations

import logging
import re
from typing import Any

from pydantic import ValidationError as PydanticValidationError

from ..core.exceptions import ConfigurationError
from ..core.models import (
    CalcConfigModel,
    GlobalConfigModel,
    _coerce_freeze_indices,
    _coerce_two_atom_indices,
)
from .defaults import (
    DEFAULT_CHARGE,
    DEFAULT_CORES_PER_TASK,
    DEFAULT_ENABLE_DYNAMIC_RESOURCES,
    DEFAULT_FORCE_CONSISTENCY,
    DEFAULT_MAX_PARALLEL_JOBS,
    DEFAULT_MULTIPLICITY,
    DEFAULT_RESUME_FROM_BACKUPS,
    DEFAULT_RMSD_THRESHOLD,
    DEFAULT_SCAN_COARSE_STEP,
    DEFAULT_SCAN_FINE_STEP,
    DEFAULT_SCAN_UPHILL_LIMIT,
    DEFAULT_STOP_CHECK_INTERVAL_SECONDS,
    DEFAULT_TOTAL_MEMORY,
    DEFAULT_TS_BOND_DRIFT_THRESHOLD,
    DEFAULT_TS_RESCUE_SCAN,
    DEFAULT_TS_RMSD_THRESHOLD,
)

__all__ = [
    "ConfigSchema",
    "merge_step_params",
    "validate_yaml_config",
]

logger = logging.getLogger("confflow.config")


class ConfigSchema:
    """Configuration normalizer.

    Responsibilities:
    1. Validate configuration parameter types and values.
    2. Provide default values.
    3. Normalize parameter names and values.
    """

    # Global parameter defaults
    GLOBAL_DEFAULTS = {
        "cores_per_task": DEFAULT_CORES_PER_TASK,
        "total_memory": DEFAULT_TOTAL_MEMORY,
        "max_parallel_jobs": DEFAULT_MAX_PARALLEL_JOBS,
        "charge": DEFAULT_CHARGE,
        "multiplicity": DEFAULT_MULTIPLICITY,
        "rmsd_threshold": DEFAULT_RMSD_THRESHOLD,
        "enable_dynamic_resources": DEFAULT_ENABLE_DYNAMIC_RESOURCES,
        "ts_rescue_scan": DEFAULT_TS_RESCUE_SCAN,
        "scan_coarse_step": DEFAULT_SCAN_COARSE_STEP,
        "scan_fine_step": DEFAULT_SCAN_FINE_STEP,
        "scan_uphill_limit": DEFAULT_SCAN_UPHILL_LIMIT,
        "ts_bond_drift_threshold": DEFAULT_TS_BOND_DRIFT_THRESHOLD,
        "ts_rmsd_threshold": DEFAULT_TS_RMSD_THRESHOLD,
        "resume_from_backups": DEFAULT_RESUME_FROM_BACKUPS,
        "stop_check_interval_seconds": DEFAULT_STOP_CHECK_INTERVAL_SECONDS,
        "force_consistency": DEFAULT_FORCE_CONSISTENCY,
    }

    # Step-level parameters (can override global config)
    STEP_OVERRIDES = {
        "cores_per_task",
        "total_memory",
        "max_parallel_jobs",
        "energy_window",
        "energy_tolerance",
        "keyword",
        "iprog",
        "itask",
        "blocks",
    }

    @classmethod
    def normalize_global_config(cls, raw_config: dict[str, Any]) -> dict[str, Any]:
        """Normalize global configuration.

        Parameters
        ----------
        raw_config : dict
            Raw configuration read from YAML.

        Returns
        -------
        dict
            Normalized configuration dictionary.
        """
        normalized = cls.GLOBAL_DEFAULTS.copy()

        # Update with user-provided values
        for key, value in raw_config.items():
            if key == "freeze":
                normalized[key] = _coerce_freeze_indices(value)
            elif key == "ts_bond_atoms":
                normalized[key] = _coerce_two_atom_indices(value)
            else:
                normalized[key] = value

        return normalized

    @classmethod
    def normalize_step_config(
        cls, step_config: dict[str, Any], global_config: dict[str, Any]
    ) -> dict[str, Any]:
        """Normalize step configuration.

        Parameters
        ----------
        step_config : dict
            Step configuration (the ``params`` field).
        global_config : dict
            Global configuration.

        Returns
        -------
        dict
            Merged configuration dictionary.
        """
        # Copy from global config
        normalized = global_config.copy()

        # Apply step-level overrides; only keys in STEP_OVERRIDES are allowed
        params = step_config.get("params", {})
        for key, value in params.items():
            if key in cls.STEP_OVERRIDES:
                normalized[key] = value

        # Step type
        normalized["step_type"] = step_config.get("type", "calc")
        normalized["step_name"] = step_config.get("name", "unnamed")

        return normalized

    @classmethod
    def validate_global_config(cls, raw_config: dict[str, Any]) -> dict[str, Any]:
        """Normalize and validate global configuration through GlobalConfigModel."""
        normalized = cls.normalize_global_config(raw_config)
        try:
            validated = GlobalConfigModel(**normalized)
        except PydanticValidationError as e:
            errors = []
            for err in e.errors():
                loc = ".".join(str(x) for x in err.get("loc", ())) or "global"
                errors.append(f"global.{loc}: {err.get('msg', 'invalid value')}")
            raise ConfigurationError("Global configuration model validation failed", errors) from e

        validated_dict = validated.model_dump()
        merged = dict(normalized)
        for key in set(normalized) | set(raw_config):
            if key in validated_dict:
                merged[key] = validated_dict[key]
        return merged

    @classmethod
    def validate_calc_config(cls, config: dict[str, Any]) -> None:
        """Validate a calc task configuration.

        Parameters
        ----------
        config : dict
            Configuration dictionary.

        Raises
        ------
        ValueError
            If the configuration is invalid.
        """
        required = ["iprog", "itask", "keyword"]
        for key in required:
            if key not in config:
                raise ValueError(f"calc config missing required parameter: {key}")
        try:
            validated = CalcConfigModel(**config)
        except PydanticValidationError as e:
            field, message = cls._translate_calc_model_error(e)
            raise ValueError(message) from e

        cls._merge_validated_calc_config(config, validated.model_dump())

    @staticmethod
    def _merge_validated_calc_config(config: dict[str, Any], validated_dict: dict[str, Any]) -> None:
        """Write validated/coerced calc fields back to the original config dict."""
        for key in list(config):
            if key in validated_dict:
                config[key] = validated_dict[key]

    @staticmethod
    def _translate_calc_model_error(exc: PydanticValidationError) -> tuple[str, str]:
        """Translate Pydantic calc-model errors to legacy ValueError messages."""
        err = exc.errors()[0]
        field = str(err.get("loc", ("calc",))[0])
        value = err.get("input")
        msg = err.get("msg", "invalid value")

        if field in {"iprog", "itask", "keyword"}:
            return field, msg

        if field in {"cores_per_task", "max_parallel_jobs", "charge", "multiplicity"}:
            return field, ConfigSchema._translate_legacy_integer_error(field, value, msg)

        if field == "total_memory":
            return field, msg

        if field == "ts_bond_atoms":
            return field, ConfigSchema._translate_ts_bond_atoms_error(value)

        return field, msg

    @staticmethod
    def _translate_legacy_integer_error(field: str, value: Any, msg: str) -> str:
        """Translate Pydantic integer parsing errors to legacy schema wording."""
        if "valid integer" in msg.lower():
            return f"{field} must be an integer, current: {value}"
        return msg

    @staticmethod
    def _translate_ts_bond_atoms_error(value: Any) -> str:
        """Translate ``ts_bond_atoms`` model errors to legacy schema wording."""
        if isinstance(value, str):
            parts = value.replace(",", " ").split()
            if len(parts) != 2:
                return f"ts_bond_atoms format error: {value}, expected 'a,b' or [a, b]"
            return f"ts_bond_atoms must be two integers: {value}"
        if isinstance(value, (list, tuple)):
            if len(value) != 2:
                return f"ts_bond_atoms must be two atom indices: {value}"
            return f"ts_bond_atoms must be two integers: {value}"
        return "ts_bond_atoms must be a list or comma-separated string"

    @staticmethod
    def _parse_freeze_string(freeze_str: str) -> list[int]:
        """Parse a freeze index string.

        Parameters
        ----------
        freeze_str : str
            e.g. ``"1,2,3-5"``

        Returns
        -------
        list of int
            Atom indices (1-based).
        """
        return _coerce_freeze_indices(freeze_str)


def merge_step_params(step_config: dict[str, Any], global_config: dict[str, Any]) -> dict[str, Any]:
    """Merge step parameters with global configuration (shortcut function).

    Parameters
    ----------
    step_config : dict
        Step configuration.
    global_config : dict
        Global configuration.

    Returns
    -------
    dict
        Merged configuration dictionary.
    """
    return ConfigSchema.normalize_step_config(step_config, global_config)


# =========================================================================
# YAML configuration validation (migrated from core/utils.py)
# =========================================================================


def validate_yaml_config(
    config: dict[str, Any], required_sections: list[str] | None = None
) -> list[str]:
    """Validate the structure of a YAML configuration file.

    Parameters
    ----------
    config : dict
        Parsed configuration dictionary.
    required_sections : list of str or None
        List of required configuration sections.

    Returns
    -------
    list of str
        List of error messages (empty list means validation passed).
    """
    import os

    errors: list[str] = []

    def _is_positive_int_like(value: Any) -> bool:
        try:
            return int(value) > 0
        except (ValueError, TypeError):
            return False

    if required_sections is None:
        required_sections = ["global", "steps"]

    for section in required_sections:
        if section not in config:
            errors.append(f"missing required section: '{section}'")

    if "global" in config:
        global_config = config["global"]
        if global_config is None:
            global_config = {}
        if not isinstance(global_config, dict):
            errors.append("'global' must be a dict")
        else:
            if "gaussian_path" in global_config:
                path = global_config["gaussian_path"]
                if path and not os.path.exists(path) and "/" in path:
                    errors.append(f"Gaussian path not found: {path}")

            if "orca_path" in global_config:
                path = global_config["orca_path"]
                if path and not os.path.exists(path) and "/" in path:
                    errors.append(f"ORCA path not found: {path}")

            cores = global_config.get("cores_per_task", 1)
            if not _is_positive_int_like(cores):
                errors.append(f"invalid cores_per_task: {cores}")

            max_jobs = global_config.get("max_parallel_jobs", 1)
            if not _is_positive_int_like(max_jobs):
                errors.append(f"invalid max_parallel_jobs: {max_jobs}")

    if "steps" in config:
        steps = config["steps"]

        if not isinstance(steps, list):
            errors.append("'steps' must be a list")
        else:
            for i, step in enumerate(steps):
                if not isinstance(step, dict):
                    errors.append(f"step {i + 1} must be a dict")
                    continue
                step_errors = _validate_step_config(step, i)
                errors.extend(step_errors)

    return errors


def _validate_step_config(step: dict[str, Any], index: int) -> list[str]:
    """Validate a single step's configuration."""
    errors: list[str] = []
    step_id = f"step {index + 1}"

    def _pair_list_ok(val: Any) -> bool:
        if val is None:
            return True
        if isinstance(val, str):
            nums = re.findall(r"\\d+", val)
            return len(nums) >= 2
        if isinstance(val, (list, tuple)):
            if len(val) == 0:
                return True
            if len(val) == 2 and all(isinstance(x, int) for x in val):
                return True
            if all(isinstance(x, (list, tuple)) and len(x) == 2 for x in val):
                return True
            if all(isinstance(x, str) for x in val):
                return all(len(re.findall(r"\\d+", x)) >= 2 for x in val)
        return False

    if "name" not in step:
        errors.append(f"{step_id}: missing 'name' field")
    else:
        step_id = f"step '{step['name']}'"

    if "type" not in step:
        errors.append(f"{step_id}: missing 'type' field")
    else:
        step_type = step["type"]
        valid_types = ["confgen", "calc", "gen", "task"]
        if step_type not in valid_types:
            errors.append(
                f"{step_id}: invalid type '{step_type}', must be 'confgen', 'calc', 'gen' or 'task'"
            )

    if "params" in step:
        params = step["params"]
        if params is None:
            params = {}
        if not isinstance(params, dict):
            errors.append(f"{step_id}: 'params' must be a dict")
            return errors
        step_type = step.get("type", "")

        if step_type in ["calc", "task"]:
            itask = params.get("itask")
            valid_itasks = {"opt", "sp", "freq", "opt_freq", "ts", "0", "1", "2", "3", "4", 0, 1, 2, 3, 4}
            if itask is not None and itask not in valid_itasks:
                errors.append(f"{step_id}: invalid itask value '{itask}'")

            iprog = params.get("iprog")
            valid_iprogs = {"gaussian", "g16", "orca", "1", "2", 1, 2}
            if iprog is not None and iprog not in valid_iprogs:
                errors.append(f"{step_id}: invalid iprog value '{iprog}'")

            if "keyword" not in params and iprog in {"orca", "2", 2}:
                errors.append(f"{step_id}: ORCA task missing 'keyword' parameter")

        elif step_type in ["confgen", "gen"]:
            chains = params.get("chains", None)
            if chains is None:
                chains = params.get("chain", None)
            if not chains:
                errors.append(
                    f"{step_id}: confgen step requires 'chains' (or 'chain'), e.g. chains: ['81-79-78-86-92']"
                )

            for key in ("add_bond", "del_bond", "no_rotate", "force_rotate"):
                if key in params and not _pair_list_ok(params.get(key)):
                    errors.append(
                        f"{step_id}: confgen parameter '{key}' format error; expected [[a,b], ...] / [a,b] / ['a b', ...] / 'a b' (1-based indices)"
                    )

            angle_step = params.get("angle_step")
            if angle_step is not None:
                if not isinstance(angle_step, (int, float)) or angle_step <= 0:
                    errors.append(f"{step_id}: invalid angle_step value '{angle_step}'")

    return errors
