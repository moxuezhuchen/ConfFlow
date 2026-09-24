"""Canonical configuration parsing primitives.

This package is an additive boundary. Existing v2 dataclass and Pydantic
entry points remain the compatibility surface while callers migrate.

It also owns the producer-side *editing* contract: the workflow schema, the
editor manifest and the recipe catalog, and the contract documents that publish
them (``confflow.configuration-contract.v1`` and ``.v2``).
"""

from .contract import (
    CONFIGURATION_CONTRACT_BUILDERS,
    CONFIGURATION_CONTRACT_SCHEMA,
    CONFIGURATION_CONTRACT_V1_SCHEMA,
    CONFIGURATION_CONTRACT_V2_SCHEMA,
    CONFIGURATION_VALIDATION_SCHEMA,
    build_configuration_contract,
    build_configuration_contract_for_version,
    build_configuration_contract_v1,
    build_configuration_contract_v2,
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
from .v2_adapter import to_canonical_workflow
from .workflow import (
    CanonicalStepDefinition,
    CanonicalWorkflowDefinition,
    DependencyMode,
    build_step_graph,
    topo_order,
)

__all__ = [
    "CONFIGURATION_CONTRACT_BUILDERS",
    "CONFIGURATION_CONTRACT_SCHEMA",
    "CONFIGURATION_CONTRACT_V1_SCHEMA",
    "CONFIGURATION_CONTRACT_V2_SCHEMA",
    "CONFIGURATION_VALIDATION_SCHEMA",
    "EDITOR_MANIFEST_SCHEMA",
    "RECIPE_CATALOG_SCHEMA",
    "WORKFLOW_BINDING_SCHEMA",
    "CanonicalStepDefinition",
    "CanonicalWorkflowDefinition",
    "DependencyMode",
    "WorkflowBindingCompatibilityError",
    "WorkflowConfigBinding",
    "WorkflowFingerprintError",
    "build_step_graph",
    "build_workflow_binding",
    "canonical_workflow_payload",
    "parse_workflow_binding",
    "to_canonical_workflow",
    "topo_order",
    "workflow_fingerprint",
    "ConfigIssue",
    "ConfigValidationError",
    "build_configuration_contract",
    "build_configuration_contract_for_version",
    "build_configuration_contract_v1",
    "build_configuration_contract_v2",
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
