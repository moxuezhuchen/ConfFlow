#!/usr/bin/env python3
"""Dependency-free wire-schema identifiers for configuration contracts.

Single authority for schema-id strings shared by producer contract envelope, editor manifest,
recipe catalog, and validation response; imports nothing beyond `__future__` so `confflow.producer`
can read ids without loading a configuration runtime.
`confflow.configuration-validation.v1` is not a retired V1 document: it is the current frozen
content schema of the producer validation response (consumed by JobDesk V4 path; envelope fields
`workflow_schema`/`editor_manifest`/`recipe_catalog`/`validation_response_schema`); V1/V2 contract
documents retired (PR-9), these identifiers were not.
"""

from __future__ import annotations

__all__ = [
    "CONFIGURATION_VALIDATION_SCHEMA",
    "EDITOR_MANIFEST_SCHEMA",
    "RECIPE_CATALOG_SCHEMA",
]

#: Schema id of the frozen configuration-validation response consumed by
#: JobDesk (``confflow.configuration-validation.v1``).
CONFIGURATION_VALIDATION_SCHEMA: str = "confflow.configuration-validation.v1"

#: Schema id of the producer-owned editor manifest
#: (``confflow.editor-manifest.v1``).
EDITOR_MANIFEST_SCHEMA: str = "confflow.editor-manifest.v1"

#: Schema id of the producer-owned recipe catalog
#: (``confflow.recipe-catalog.v1``).
RECIPE_CATALOG_SCHEMA: str = "confflow.recipe-catalog.v1"
