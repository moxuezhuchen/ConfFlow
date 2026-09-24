#!/usr/bin/env python3

"""Legacy YAML configuration validation compatibility facade.

The semantic rules for a workflow document are owned by
:mod:`confflow.config.canonical.validation`. This module keeps the historical
``validate_yaml_config`` / ``validate_step_config`` entry points and their
``list[str]`` return shape, but it no longer owns a second copy of any rule: each
call delegates to the canonical definition validator and renders its
diagnostics.

The one thing this facade adds beyond definition validation is the historical
*executable-path existence* check, which is an environment probe (it stats the
filesystem) rather than a property of the document, so it is deliberately not
part of the canonical definition validator.
"""

from __future__ import annotations

import ntpath
import os
from collections.abc import Mapping
from typing import Any

__all__ = [
    "validate_yaml_config",
    "validate_step_config",
]


def _should_validate_executable_path(path: Any) -> bool:
    if not isinstance(path, str):
        return False
    return os.path.isabs(path) or ntpath.isabs(path) or "/" in path or "\\" in path


def _executable_path_errors(config: Mapping[str, Any]) -> list[str]:
    """Environment-only checks the definition validator intentionally omits."""
    errors: list[str] = []
    global_config = config.get("global")
    if global_config is None:
        global_config = {}
    if not isinstance(global_config, Mapping):
        return errors
    for key, label in (("gaussian_path", "Gaussian"), ("orca_path", "ORCA")):
        if key not in global_config:
            continue
        path = global_config[key]
        if path and not os.path.exists(path) and _should_validate_executable_path(path):
            errors.append(f"{label} path not found: {path}")
    return errors


def _canonical_errors(config: Mapping[str, Any]) -> list[str]:
    # Imported lazily so this low-level module does not take a module-level
    # dependency on the configuration layer.
    from ..config.canonical.validation import validate_workflow_definition

    return [str(diagnostic) for diagnostic in validate_workflow_definition(config)]


def validate_yaml_config(
    config: dict[str, Any], required_sections: list[str] | None = None
) -> list[str]:
    """Validate a workflow YAML mapping, reporting legacy ``list[str]`` errors."""
    errors: list[str] = []

    if required_sections is None:
        required_sections = ["global", "steps"]

    for section in required_sections:
        if section not in config:
            errors.append(f"missing required section: '{section}'")

    errors.extend(_executable_path_errors(config))
    errors.extend(_canonical_errors(config))
    return errors


def validate_step_config(step: dict[str, Any], index: int) -> list[str]:
    """Validate one step mapping via the canonical definition validator.

    ``index`` is retained for signature compatibility; the canonical diagnostics
    carry their own ``steps[N]`` path.
    """
    del index
    return _canonical_errors({"global": {}, "steps": [step]})
