#!/usr/bin/env python3

"""Version capability model: which workflow versions may be parsed / executed.

Parsing a document and *running* it are different capabilities. R3 makes V3
parseable, validatable, plannable and inspectable, but not executable: V3
execution needs ``workflow_state.v2`` / ``workflow_binding.v2`` (R4) because the
current engine keys state, directories and resume off the V2 name identity.

This module is the single capability source for every guard. R3.2 only *defines*
it — the call sites (CLI, service adapter, engine) are wired in R3.5. R4 enables
V3 execution by flipping one flag here; no guard body changes.
"""

from __future__ import annotations

from dataclasses import dataclass

from ...core.exceptions import ConfFlowError
from .schema import WORKFLOW_SCHEMA_VERSION_V2, WORKFLOW_SCHEMA_VERSION_V3

__all__ = [
    "CAPABILITIES",
    "VersionCapability",
    "can_execute",
    "can_parse",
    "require_executable",
]


@dataclass(frozen=True)
class VersionCapability:
    """Whether one workflow schema version may be parsed and/or executed."""

    parse: bool
    execute: bool


#: The one capability table. Extensible to a future version by adding an entry.
CAPABILITIES: dict[str, VersionCapability] = {
    WORKFLOW_SCHEMA_VERSION_V2: VersionCapability(parse=True, execute=True),
    WORKFLOW_SCHEMA_VERSION_V3: VersionCapability(parse=True, execute=False),  # R4 flips `execute`
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
