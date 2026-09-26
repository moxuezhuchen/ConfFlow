#!/usr/bin/env python3

"""ConfFlow Workflow V4 producer contract (V4-6).

The producer side of the V4 line: the configuration contract envelope, the
editor manifest, the recipe catalog, and byte-level workflow validation. All
content is generated from the single sources (V4 schema, execution registry,
program registry, domain vocabularies) -- never copied lists.
"""

from __future__ import annotations

from .contract import (
    ANALYSIS_REACTION_PROFILE_CAPABILITY,
    ANALYSIS_REACTION_PROFILE_CONTRACT,
    CONFIGURATION_CONTRACT_V4_SCHEMA,
    RESULT_MANIFEST_SCHEMA,
    build_configuration_contract_v4,
    build_run_result_manifest,
    contract_canonical_json,
    contract_digest_of,
    generate_contract_bytes,
    run_result_json_schema,
    run_result_schema_sha256,
)
from .manifest import build_editor_manifest_v4, editor_manifest_sha256_v4
from .recipes import (
    RECIPE_IDS_V4,
    build_recipe_catalog_v4,
    get_recipe_v4,
    recipe_catalog_sha256_v4,
)
from .validation import ValidationReport, validate_workflow_bytes

__all__ = [
    "ANALYSIS_REACTION_PROFILE_CAPABILITY",
    "ANALYSIS_REACTION_PROFILE_CONTRACT",
    "CONFIGURATION_CONTRACT_V4_SCHEMA",
    "RESULT_MANIFEST_SCHEMA",
    "RECIPE_IDS_V4",
    "ValidationReport",
    "build_configuration_contract_v4",
    "build_editor_manifest_v4",
    "build_recipe_catalog_v4",
    "build_run_result_manifest",
    "contract_canonical_json",
    "contract_digest_of",
    "editor_manifest_sha256_v4",
    "generate_contract_bytes",
    "get_recipe_v4",
    "recipe_catalog_sha256_v4",
    "run_result_json_schema",
    "run_result_schema_sha256",
    "validate_workflow_bytes",
]
