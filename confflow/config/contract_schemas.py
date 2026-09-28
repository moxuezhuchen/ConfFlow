#!/usr/bin/env python3
"""Dependency-free wire-schema identifiers for the configuration contracts.

This module is the single authority for the schema-id strings shared by the
producer contract envelope, the editor manifest, the recipe catalog, and the
configuration-validation response.  It deliberately imports nothing beyond
``__future__`` so that ``confflow.producer`` can read the identifiers without
loading the V1/V2 configuration runtime
(:mod:`confflow.config.canonical`).

The historical import paths stay valid and re-export these same objects
(never a second copy):

- :data:`CONFIGURATION_VALIDATION_SCHEMA` —
  ``confflow.config.canonical.contract``
- :data:`EDITOR_MANIFEST_SCHEMA` — ``confflow.config.canonical.editor_manifest``
- :data:`RECIPE_CATALOG_SCHEMA` — ``confflow.config.canonical.recipes``
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
