"""Single raw YAML/mapping boundary for canonical configuration migration."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

import yaml

from .issues import ConfigIssue, ConfigValidationError
from .types import WorkflowConfig


def _mapping_or_error(raw: Any, *, path: str = "") -> dict[str, Any]:
    if not isinstance(raw, Mapping):
        raise ConfigValidationError(ConfigIssue(path, "workflow config root must be a mapping"))
    return dict(raw)


def load_raw_mapping(config_file: str | Path) -> dict[str, Any]:
    """Load YAML into an owned root mapping without applying workflow rules."""
    path = Path(config_file)
    if not path.exists():
        raise FileNotFoundError(f"Configuration file not found: {path}")
    if not path.is_file():
        raise ConfigValidationError(ConfigIssue("", f"Configuration path is not a file: {path}"))
    try:
        with path.open(encoding="utf-8") as handle:
            raw = yaml.safe_load(handle)
    except yaml.YAMLError as exc:
        raise ConfigValidationError(ConfigIssue("", f"Invalid YAML configuration: {exc}")) from exc
    return {} if raw is None else _mapping_or_error(raw)


def parse_workflow_mapping(raw: Mapping[str, Any]) -> WorkflowConfig:
    """Apply existing typed workflow rules while presenting canonical errors."""
    owned = _mapping_or_error(raw)
    if "global" in owned and not isinstance(owned["global"], Mapping):
        raise ConfigValidationError(ConfigIssue("global", "global config must be a mapping"))
    steps = owned.get("steps")
    if isinstance(steps, list):
        for index, step in enumerate(steps, start=1):
            if (
                isinstance(step, Mapping)
                and "params" in step
                and not isinstance(step["params"], Mapping)
            ):
                raise ConfigValidationError(
                    ConfigIssue(f"steps[{index}].params", "step params must be a mapping")
                )
    try:
        return WorkflowConfig.from_mapping(owned)
    except ValueError as exc:
        raise ConfigValidationError(ConfigIssue("", str(exc))) from exc
