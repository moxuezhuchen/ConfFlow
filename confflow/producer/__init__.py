#!/usr/bin/env python3

"""ConfFlow Workflow V4 producer contract (V4-6).

The producer side of the V4 line: the configuration contract envelope, the
editor manifest, the recipe catalog, and byte-level workflow validation. All
content is generated from the single sources (V4 schema, execution registry,
program registry, domain vocabularies) -- never copied lists.
"""

from __future__ import annotations

from .checkpoints import CHECKPOINT_REUSE_VERSION, REUSE_MODES, wire_checkpoint_reuse
from .contract import (
    ANALYSIS_REACTION_PROFILE_CAPABILITY,
    ANALYSIS_REACTION_PROFILE_CONTRACT,
    CONFIGURATION_CONTRACT_V4_SCHEMA,
    RESULT_MANIFEST_SCHEMA,
    build_boundary_document,
    build_configuration_contract_v4,
    build_run_result_manifest,
    contract_canonical_json,
    contract_digest_of,
    generate_contract_bytes,
    run_result_json_schema,
    run_result_schema_sha256,
)
from .machine import MACHINE_RESOLUTION_VERSION, resolve_machine_resources
from .manifest import build_editor_manifest_v4, editor_manifest_sha256_v4
from .recipes import (
    RECIPE_IDS_V4,
    build_recipe_catalog_v4,
    get_recipe_v4,
    recipe_catalog_sha256_v4,
)
from .run_result import (
    RUN_RESULT_FILENAME as PRODUCER_RUN_RESULT_FILENAME,
)
from .run_result import (
    artifact_entry,
    build_runtime_manifest,
    check_manifest_against_contract,
    manifest_digest,
    project_analysis_groups,
    publish_manifest_atomically,
    reaction_group_dict,
    reaction_group_entry,
    result_ref_entry,
    step_entry,
    verify_artifact_bytes,
    verify_manifest_on_disk,
)
from .validation import ValidationReport, validate_workflow_bytes

__all__ = [
    "ANALYSIS_REACTION_PROFILE_CAPABILITY",
    "ANALYSIS_REACTION_PROFILE_CONTRACT",
    "CHECKPOINT_REUSE_VERSION",
    "CONFIGURATION_CONTRACT_V4_SCHEMA",
    "MACHINE_RESOLUTION_VERSION",
    "RESULT_MANIFEST_SCHEMA",
    "RECIPE_IDS_V4",
    "REUSE_MODES",
    "PRODUCER_RUN_RESULT_FILENAME",
    "ValidationReport",
    "artifact_entry",
    "build_configuration_contract_v4",
    "build_boundary_document",
    "build_editor_manifest_v4",
    "build_recipe_catalog_v4",
    "build_run_result_manifest",
    "build_runtime_manifest",
    "check_manifest_against_contract",
    "contract_canonical_json",
    "contract_digest_of",
    "editor_manifest_sha256_v4",
    "generate_contract_bytes",
    "get_recipe_v4",
    "manifest_digest",
    "project_analysis_groups",
    "publish_manifest_atomically",
    "reaction_group_dict",
    "reaction_group_entry",
    "recipe_catalog_sha256_v4",
    "resolve_machine_resources",
    "result_ref_entry",
    "run_result_json_schema",
    "run_result_schema_sha256",
    "step_entry",
    "validate_workflow_bytes",
    "verify_artifact_bytes",
    "verify_manifest_on_disk",
    "wire_checkpoint_reuse",
]
