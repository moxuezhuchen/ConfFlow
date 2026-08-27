"""Pure producer-owned configuration contract documents."""

from __future__ import annotations

from typing import Any

from .schema import WORKFLOW_SCHEMA_VERSION, workflow_json_schema, workflow_schema_sha256

CONFIGURATION_CONTRACT_SCHEMA = "confflow.configuration-contract.v1"
CONFIGURATION_VALIDATION_SCHEMA = "confflow.configuration-validation.v1"


def build_configuration_contract(
    *,
    producer_version: str,
    producer_commit: str | None = None,
    producer_dirty: bool | None = None,
) -> dict[str, Any]:
    """Build a deterministic contract without runtime or workload probing."""
    return {
        "schema": CONFIGURATION_CONTRACT_SCHEMA,
        "workflow_schema_version": WORKFLOW_SCHEMA_VERSION,
        "workflow_schema_sha256": workflow_schema_sha256(),
        "workflow_schema": workflow_json_schema(),
        "producer": {
            "package": "confflow",
            "version": producer_version,
            "commit": producer_commit,
            "dirty": producer_dirty,
        },
        "validation_response_schema": CONFIGURATION_VALIDATION_SCHEMA,
    }


__all__ = [
    "CONFIGURATION_CONTRACT_SCHEMA",
    "CONFIGURATION_VALIDATION_SCHEMA",
    "build_configuration_contract",
]
