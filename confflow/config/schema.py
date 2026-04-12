#!/usr/bin/env python3

"""
ConfFlow Config Schema - Configuration parameter normalization module.

Provides unified configuration parameter validation and normalization,
reducing complexity in the YAML -> INI -> dict conversion chain.
"""

from __future__ import annotations

import logging
from typing import Any

from pydantic import ValidationError as PydanticValidationError

from ..core.exceptions import ConfigurationError
from ..core.models import (
    CalcConfigModel,
    GlobalConfigModel,
    _coerce_freeze_indices,
    _coerce_two_atom_indices,
)
from ..shared.config_validation import (
    validate_step_config as shared_validate_step_config,
)
from ..shared.config_validation import (
    validate_yaml_config as shared_validate_yaml_config,
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
    def _merge_validated_calc_config(
        config: dict[str, Any], validated_dict: dict[str, Any]
    ) -> None:
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
    """Validate the structure of a YAML configuration file."""
    return shared_validate_yaml_config(config, required_sections)


def _validate_step_config(step: dict[str, Any], index: int) -> list[str]:
    """Backward-compatible alias for the shared step validator."""
    return shared_validate_step_config(step, index)
