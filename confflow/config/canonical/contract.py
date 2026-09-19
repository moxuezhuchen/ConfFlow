"""Pure producer-owned configuration contract documents.

Two contract versions exist and both stay valid:

``v1``
    the workflow schema, its digest, and the producer provenance block. This is
    what ``confflow config contract --json`` has always emitted and it keeps
    emitting unless a caller explicitly asks otherwise.
``v2``
    ``v1`` plus the editor manifest and the recipe catalog, each with its own
    digest. Adding members is backward compatible for every existing consumer,
    because an unknown top-level member is simply not read.

The two builders share one implementation of the common members, so a v2 document
can never disagree with the v1 document about the workflow schema or the producer
block -- the difference between them is exactly the four added members.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from .editor_manifest import build_editor_manifest, editor_manifest_sha256
from .recipes import build_recipe_catalog, recipe_catalog_sha256
from .schema import WORKFLOW_SCHEMA_VERSION, workflow_json_schema, workflow_schema_sha256

CONFIGURATION_CONTRACT_V1_SCHEMA = "confflow.configuration-contract.v1"
CONFIGURATION_CONTRACT_V2_SCHEMA = "confflow.configuration-contract.v2"

#: Historical alias, kept so existing imports and callers do not have to move.
#: It deliberately keeps meaning **v1**: that is what the name has always
#: described, and the default CLI output is still v1.
CONFIGURATION_CONTRACT_SCHEMA = CONFIGURATION_CONTRACT_V1_SCHEMA

CONFIGURATION_VALIDATION_SCHEMA = "confflow.configuration-validation.v1"

ContractBuilder = Callable[..., dict[str, Any]]


def build_configuration_contract_v1(
    *,
    producer_version: str,
    producer_commit: str | None = None,
    producer_dirty: bool | None = None,
) -> dict[str, Any]:
    """Build a deterministic v1 contract without runtime or workload probing."""
    return {
        "schema": CONFIGURATION_CONTRACT_V1_SCHEMA,
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


def build_configuration_contract_v2(
    *,
    producer_version: str,
    producer_commit: str | None = None,
    producer_dirty: bool | None = None,
) -> dict[str, Any]:
    """Build a v2 contract: v1 plus the editor manifest and the recipe catalog.

    Each artifact's digest is taken over the artifact alone, so an artifact can be
    published, cached or verified on its own without reproducing the envelope.
    """
    document = build_configuration_contract_v1(
        producer_version=producer_version,
        producer_commit=producer_commit,
        producer_dirty=producer_dirty,
    )
    document["schema"] = CONFIGURATION_CONTRACT_V2_SCHEMA
    document["editor_manifest"] = build_editor_manifest()
    document["editor_manifest_sha256"] = editor_manifest_sha256()
    document["recipe_catalog"] = build_recipe_catalog()
    document["recipe_catalog_sha256"] = recipe_catalog_sha256()
    return document


def build_configuration_contract(
    *,
    producer_version: str,
    producer_commit: str | None = None,
    producer_dirty: bool | None = None,
) -> dict[str, Any]:
    """Build the contract emitted by default, which is v1."""
    return build_configuration_contract_v1(
        producer_version=producer_version,
        producer_commit=producer_commit,
        producer_dirty=producer_dirty,
    )


#: The builder for each contract version a caller may ask for. Single definition
#: of "which versions exist", so the CLI's accepted choices cannot drift from the
#: dispatch below.
CONFIGURATION_CONTRACT_BUILDERS: dict[int, ContractBuilder] = {
    1: build_configuration_contract_v1,
    2: build_configuration_contract_v2,
}


def build_configuration_contract_for_version(
    version: int,
    *,
    producer_version: str,
    producer_commit: str | None = None,
    producer_dirty: bool | None = None,
) -> dict[str, Any]:
    """Build the contract for an explicit version, or raise for an unknown one."""
    builder = CONFIGURATION_CONTRACT_BUILDERS.get(version)
    if builder is None:
        supported = ", ".join(str(item) for item in sorted(CONFIGURATION_CONTRACT_BUILDERS))
        raise ValueError(
            f"unsupported configuration contract version {version!r}; supported: {supported}"
        )
    return builder(
        producer_version=producer_version,
        producer_commit=producer_commit,
        producer_dirty=producer_dirty,
    )


__all__ = [
    "CONFIGURATION_CONTRACT_BUILDERS",
    "CONFIGURATION_CONTRACT_SCHEMA",
    "CONFIGURATION_CONTRACT_V1_SCHEMA",
    "CONFIGURATION_CONTRACT_V2_SCHEMA",
    "CONFIGURATION_VALIDATION_SCHEMA",
    "ContractBuilder",
    "build_configuration_contract",
    "build_configuration_contract_for_version",
    "build_configuration_contract_v1",
    "build_configuration_contract_v2",
]
