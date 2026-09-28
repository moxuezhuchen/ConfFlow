#!/usr/bin/env python3
"""Dependency-free wire-schema identifiers for the configuration contracts.

This module is the single authority for the schema-id strings shared by the
producer contract envelope, the editor manifest, the recipe catalog, and the
configuration-validation response.  It deliberately imports nothing beyond
``__future__`` so that ``confflow.producer`` can read the identifiers without
loading a configuration runtime.

``confflow.configuration-validation.v1`` is **not** a retired V1 document.  It
is the current, frozen content schema of the producer's validation response,
consumed by JobDesk's V4 path, and it is shared by the V4 producer contract
envelope (``workflow_schema`` / ``editor_manifest`` / ``recipe_catalog`` /
``validation_response_schema``).  The released V1/V2 configuration *contract*
documents were retired by the Architecture Diet PR-9; these identifiers were
not.
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
