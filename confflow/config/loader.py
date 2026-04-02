#!/usr/bin/env python3

"""Unified entry point for configuration loading and validation.

Goals: allow CLI, engine, and tests to share the same logic:
- Read YAML
- ``validate_yaml_config`` for structural validation
- ``ConfigSchema.normalize_global_config`` for normalization

The returned structure is a simple dict to avoid introducing complex types.
"""

from __future__ import annotations

import logging
import os
from typing import Any

import yaml

from ..core.exceptions import ConfigurationError  # noqa: F401 — re-export
from .schema import ConfigSchema, validate_yaml_config

__all__ = [
    "load_workflow_config_file",
]

logger = logging.getLogger("confflow.config")


def load_workflow_config_file(config_file: str) -> dict[str, Any]:
    """Read and validate a workflow configuration file.

    Parameters
    ----------
    config_file : str
        Path to the YAML configuration file.

    Returns
    -------
    dict
        Configuration dictionary containing ``global``, ``steps``, and ``raw`` keys.

    Raises
    ------
    FileNotFoundError
        If the configuration file does not exist.
    ConfigurationError
        If configuration validation fails.
    yaml.YAMLError
        If YAML parsing fails.
    """
    if not config_file:
        raise ConfigurationError("Configuration file path must not be empty")

    if not os.path.exists(config_file):
        raise FileNotFoundError(f"Configuration file not found: {config_file}")

    if not os.path.isfile(config_file):
        raise ConfigurationError(f"Configuration path is not a file: {config_file}")

    logger.info(f"Loading configuration file: {config_file}")

    try:
        with open(config_file, encoding="utf-8") as f:
            full_config = yaml.safe_load(f) or {}
    except yaml.YAMLError as e:
        logger.error(f"YAML parsing failed: {e}")
        raise ConfigurationError(f"YAML parsing failed: {e}") from e
    except OSError as e:
        logger.error(f"Failed to read configuration file: {e}")
        raise ConfigurationError(f"Failed to read configuration file: {e}") from e

    # Basic type check
    if not isinstance(full_config, dict):
        raise ConfigurationError(
            f"Configuration file root must be a dict, got: {type(full_config).__name__}"
        )

    # Structural validation
    errors = validate_yaml_config(full_config)
    if errors:
        logger.error(f"Configuration validation failed with {len(errors)} error(s)")
        raise ConfigurationError("Configuration file validation failed", errors)

    global_raw = full_config.get("global", {})
    if global_raw is None:
        global_raw = {}
    if not isinstance(global_raw, dict):
        raise ConfigurationError(
            f"'global' config must be a dict, got: {type(global_raw).__name__}"
        )

    if "ts_bond" in global_raw:
        raise ConfigurationError("Legacy key 'ts_bond' is not supported. Use 'ts_bond_atoms'.")

    # Normalize and validate global configuration through the schema entry point.
    global_config = ConfigSchema.validate_global_config(global_raw)

    # Validate step configurations
    steps = full_config.get("steps", [])
    if steps is None:
        steps = []
    if not isinstance(steps, list):
        raise ConfigurationError(f"'steps' config must be a list, got: {type(steps).__name__}")

    # Legacy key checks that are not covered by generic schema validation.
    for i, step in enumerate(steps):
        if not isinstance(step, dict):
            continue
        params = step.get("params") or {}
        if isinstance(params, dict) and "ts_bond" in params:
            step_name = step.get("name", f"step_{i+1}")
            raise ConfigurationError(
                f"Legacy key 'ts_bond' is not supported in step '{step_name}'. Use 'ts_bond_atoms'."
            )

    logger.info(f"Configuration loaded successfully: {len(steps)} step(s)")

    return {
        "global": global_config,
        "steps": steps,
        "raw": full_config,
    }
