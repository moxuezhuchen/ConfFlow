"""Single raw YAML/mapping boundary for canonical configuration migration."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import TYPE_CHECKING, Any

import yaml

from .issues import ConfigIssue, ConfigValidationError
from .schema import (
    WORKFLOW_SCHEMA_VERSION_V2,
    WORKFLOW_SCHEMA_VERSION_V3,
    SchemaProfile,
)
from .types import WorkflowConfig

if TYPE_CHECKING:
    from .workflow import CanonicalWorkflowDefinition


def _mapping_or_error(raw: Any, *, path: str = "") -> dict[str, Any]:
    if not isinstance(raw, Mapping):
        raise ConfigValidationError(ConfigIssue(path, "workflow config root must be a mapping"))
    return dict(raw)


def detect_schema_version(raw: Mapping[str, Any]) -> str:
    """Return the workflow schema version of ``raw``.

    The single version-recognition truth: absent ``schema`` is the historical V2
    document, an explicit V2/V3 value is honoured, and anything else fails closed
    (an unknown version is never silently treated as V2).
    """
    if not isinstance(raw, Mapping):
        raise ConfigValidationError(ConfigIssue("", "workflow config root must be a mapping"))
    if "schema" not in raw:
        return WORKFLOW_SCHEMA_VERSION_V2
    value = raw["schema"]
    if not isinstance(value, str) or not value.strip():
        raise ConfigValidationError(
            ConfigIssue("schema", "workflow 'schema' must be a non-empty string")
        )
    normalized = value.strip()
    if normalized in {WORKFLOW_SCHEMA_VERSION_V2, WORKFLOW_SCHEMA_VERSION_V3}:
        return normalized
    raise ConfigValidationError(ConfigIssue("schema", f"unsupported workflow schema: {value!r}"))


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


def parse_canonical_workflow(raw: Mapping[str, Any]) -> CanonicalWorkflowDefinition:
    """Dispatch ``raw`` by schema version onto the canonical workflow IR.

    V2 documents go through the unchanged V2 parser + adapter; V3 documents go
    through the V3 structural parser. This is parse-only: nothing here builds an
    execution plan (that is R3.5), and an unknown version fails closed.
    """
    version = detect_schema_version(raw)
    if version == WORKFLOW_SCHEMA_VERSION_V3:
        from .v3_parser import parse_v3_document

        return parse_v3_document(raw, profile=SchemaProfile.DOCUMENT)
    from .v2_adapter import to_canonical_workflow

    return to_canonical_workflow(parse_workflow_mapping(raw))


def detect_workflow_file_version(config_file: str | Path) -> str:
    """Return the schema version of a configuration file, side-effect free.

    Reads and recognises only — no validation, no mutation. This is the helper
    every pre-execution guard (CLI, service adapter, rerun) uses, so version
    recognition stays the single :func:`detect_schema_version` truth. Load and
    recognition errors propagate; guards decide whether a config they cannot
    read is theirs to reject.
    """
    return detect_schema_version(load_raw_mapping(config_file))


def load_workflow_definition(config_file: str | Path) -> CanonicalWorkflowDefinition:
    """Load a configuration file and parse it into the canonical workflow IR."""
    return parse_canonical_workflow(load_raw_mapping(config_file))
