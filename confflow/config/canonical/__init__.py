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
from .diagnostics import Diagnostic, Severity
from .editor_manifest import (
    EDITOR_MANIFEST_SCHEMA,
    build_editor_manifest,
    editor_manifest_sha256,
)
from .execution_versions import (
    CAPABILITIES,
    VersionCapability,
    can_execute,
    can_parse,
    require_executable,
)
from .extensions import (
    DEFAULT_EXTENSION_REGISTRY,
    EXTENSION_NAMESPACE_PATTERN,
    ExtensionRegistry,
    is_valid_namespace,
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
from .param_fields import (
    ParamFieldDescriptor,
    calc_param_fields,
    confgen_keys,
    confgen_param_fields,
    param_properties,
    v2_calc_keys,
    v3_calc_keys,
)
from .parser import (
    detect_schema_version,
    load_raw_mapping,
    load_workflow_definition,
    parse_canonical_workflow,
    parse_workflow_mapping,
)
from .recipes import (
    RECIPE_CATALOG_SCHEMA,
    build_recipe_catalog,
    recipe_catalog_sha256,
)
from .resolve import resolve_calc_step, resolve_global_options
from .schema import (
    WORKFLOW_SCHEMA_VERSION_V2,
    WORKFLOW_SCHEMA_VERSION_V3,
    WORKFLOW_V3_ID_PATTERN,
    SchemaProfile,
    workflow_fragment_schema_sha256_v3,
    workflow_json_schema_v3,
    workflow_schema_sha256,
    workflow_schema_sha256_v3,
)
from .v2_adapter import to_canonical_workflow
from .v3_parser import parse_v3_document
from .validation import (
    calc_input_diagnostics,
    validate_workflow_definition,
    validate_workflow_run_context,
)
from .workflow import (
    CanonicalStepDefinition,
    CanonicalWorkflowDefinition,
    DependencyMode,
    build_step_graph,
    canonical_step_name,
    normalize_step_inputs,
    topo_order,
)

__all__ = [
    "CAPABILITIES",
    "CONFIGURATION_CONTRACT_BUILDERS",
    "CONFIGURATION_CONTRACT_SCHEMA",
    "CONFIGURATION_CONTRACT_V1_SCHEMA",
    "CONFIGURATION_CONTRACT_V2_SCHEMA",
    "CONFIGURATION_VALIDATION_SCHEMA",
    "DEFAULT_EXTENSION_REGISTRY",
    "EDITOR_MANIFEST_SCHEMA",
    "EXTENSION_NAMESPACE_PATTERN",
    "RECIPE_CATALOG_SCHEMA",
    "WORKFLOW_BINDING_SCHEMA",
    "WORKFLOW_SCHEMA_VERSION_V2",
    "WORKFLOW_SCHEMA_VERSION_V3",
    "WORKFLOW_V3_ID_PATTERN",
    "CanonicalStepDefinition",
    "CanonicalWorkflowDefinition",
    "DependencyMode",
    "Diagnostic",
    "ExtensionRegistry",
    "ParamFieldDescriptor",
    "SchemaProfile",
    "Severity",
    "VersionCapability",
    "WorkflowBindingCompatibilityError",
    "WorkflowConfigBinding",
    "WorkflowFingerprintError",
    "build_configuration_contract",
    "build_configuration_contract_for_version",
    "build_configuration_contract_v1",
    "build_configuration_contract_v2",
    "build_editor_manifest",
    "build_recipe_catalog",
    "build_step_graph",
    "build_workflow_binding",
    "calc_input_diagnostics",
    "calc_param_fields",
    "can_execute",
    "can_parse",
    "canonical_step_name",
    "canonical_workflow_payload",
    "confgen_keys",
    "confgen_param_fields",
    "detect_schema_version",
    "editor_manifest_sha256",
    "is_valid_namespace",
    "load_raw_mapping",
    "load_workflow_definition",
    "normalize_step_inputs",
    "param_properties",
    "parse_canonical_workflow",
    "parse_v3_document",
    "parse_workflow_binding",
    "parse_workflow_mapping",
    "recipe_catalog_sha256",
    "require_executable",
    "resolve_calc_step",
    "resolve_global_options",
    "to_canonical_workflow",
    "topo_order",
    "v2_calc_keys",
    "v3_calc_keys",
    "validate_workflow_definition",
    "validate_workflow_run_context",
    "workflow_fingerprint",
    "workflow_fragment_schema_sha256_v3",
    "workflow_json_schema_v3",
    "workflow_schema_sha256",
    "workflow_schema_sha256_v3",
    "ConfigIssue",
    "ConfigValidationError",
]
