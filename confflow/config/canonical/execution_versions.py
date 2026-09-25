#!/usr/bin/env python3

"""Version capability model: which workflow versions may be parsed / executed.

Parsing a document and *running* it are different capabilities. V3 is fully
enabled (parse + execute) since R4 flipped the flag below; V3 execution runs
through ``workflow_state.v2`` / ``workflow_binding.v2`` because the engine
keys state, directories and resume off the stable step identity.

This module is the single capability source for every guard.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from ...core.exceptions import ConfFlowError
from .issues import ConfigValidationError
from .parser import detect_workflow_file_version
from .schema import WORKFLOW_SCHEMA_VERSION_V2, WORKFLOW_SCHEMA_VERSION_V3

__all__ = [
    "CAPABILITIES",
    "VersionCapability",
    "can_execute",
    "can_parse",
    "require_executable",
    "require_executable_workflow_file",
]


@dataclass(frozen=True)
class VersionCapability:
    """Whether one workflow schema version may be parsed and/or executed."""

    parse: bool
    execute: bool


#: The one capability table. Extensible to a future version by adding an entry.
CAPABILITIES: dict[str, VersionCapability] = {
    WORKFLOW_SCHEMA_VERSION_V2: VersionCapability(parse=True, execute=True),
    WORKFLOW_SCHEMA_VERSION_V3: VersionCapability(
        parse=True, execute=True
    ),  # R4 enables V3 execution
}


def can_parse(schema_version: str) -> bool:
    """Return whether ``schema_version`` may be parsed."""
    capability = CAPABILITIES.get(schema_version)
    return capability is not None and capability.parse


def can_execute(schema_version: str) -> bool:
    """Return whether ``schema_version`` may be executed."""
    capability = CAPABILITIES.get(schema_version)
    return capability is not None and capability.execute


def require_executable(schema_version: str) -> None:
    """Raise unless ``schema_version`` may be executed."""
    if not can_execute(schema_version):
        raise ConfFlowError(
            f"Workflow {schema_version!r} execution requires state/binding v2 (R4); "
            "it can be parsed, validated, planned and inspected, but not run."
        )


def require_executable_workflow_file(config_file: str | Path) -> str | None:
    """Preflight one configuration file for execution, side-effect free.

    Reads the file and recognises its schema version through the single
    ``detect_schema_version`` truth, then applies :func:`require_executable`.
    Returns the detected version. A file that cannot be read or recognised has
    no version to gate on, so ``None`` is returned and the caller's existing
    handling of such files applies unchanged — a preflight must never turn a
    historically-load-erroring V2 invocation into a different failure, nor
    create anything.
    """
    try:
        version = detect_workflow_file_version(config_file)
    except (OSError, ConfigValidationError, ConfFlowError):
        return None
    require_executable(version)
    return version
