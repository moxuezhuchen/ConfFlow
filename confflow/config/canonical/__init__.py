"""Canonical configuration parsing primitives.

This package is an additive boundary. Existing v2 dataclass and Pydantic
entry points remain the compatibility surface while callers migrate.

It also owns the producer-side *editing* contract: the editor manifest and the
recipe catalog, which describe what a workflow editor may edit and offer.
"""

from .contract import (
    CONFIGURATION_CONTRACT_SCHEMA,
    CONFIGURATION_VALIDATION_SCHEMA,
    build_configuration_contract,
)
from .editor_manifest import (
    EDITOR_MANIFEST_SCHEMA,
    build_editor_manifest,
    editor_manifest_sha256,
)
from .fingerprint import (
    WORKFLOW_BINDING_SCHEMA,
    WorkflowBindingCompatibilityError,
    WorkflowConfigBinding,
    WorkflowFingerprintError,
    build_workflow_binding,
    canonical_workflow_payload,
    parse_workflow_binding,
    workflow_fingerprint,
)
from .issues import ConfigIssue, ConfigValidationError
from .parser import load_raw_mapping, parse_workflow_mapping
from .recipes import (
    RECIPE_CATALOG_SCHEMA,
    build_recipe_catalog,
    recipe_catalog_sha256,
)
from .resolve import resolve_calc_step, resolve_global_options
from .schema import workflow_schema_sha256

__all__ = [
    "CONFIGURATION_CONTRACT_SCHEMA",
    "CONFIGURATION_VALIDATION_SCHEMA",
    "EDITOR_MANIFEST_SCHEMA",
    "RECIPE_CATALOG_SCHEMA",
    "WORKFLOW_BINDING_SCHEMA",
    "WorkflowBindingCompatibilityError",
    "WorkflowConfigBinding",
    "WorkflowFingerprintError",
    "build_workflow_binding",
    "canonical_workflow_payload",
    "parse_workflow_binding",
    "workflow_fingerprint",
    "ConfigIssue",
    "ConfigValidationError",
    "build_configuration_contract",
    "build_editor_manifest",
    "build_recipe_catalog",
    "editor_manifest_sha256",
    "load_raw_mapping",
    "parse_workflow_mapping",
    "recipe_catalog_sha256",
    "resolve_calc_step",
    "resolve_global_options",
    "workflow_schema_sha256",
]
