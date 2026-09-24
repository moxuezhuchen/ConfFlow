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
    WORKFLOW_SEMANTICS_VERSION,
    WorkflowBindingCompatibilityError,
    WorkflowConfigBinding,
    WorkflowFingerprintError,
    build_workflow_binding,
    build_workflow_definition_payload_v3,
    canonical_workflow_payload,
    parse_workflow_binding,
    workflow_definition_fingerprint_v3,
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
from .upgrade import (
    MIGRATION_NAMESPACE,
    MigrationRecord,
    UnknownParamsPolicy,
    UpgradeError,
    UpgradeResult,
    parse_unknown_params_policy,
    upgrade_v2_to_v3,
)
from .v2_adapter import to_canonical_workflow
from .v3_graph import (
    ValidatedWorkflowGraph,
    build_validated_graph,
    v3_id_order_key,
)
from .v3_parser import parse_v3_document
from .validation import (
    ValidationProfile,
    calc_input_diagnostics,
    resolve_step_semantic_params,
    validate_v3_definition,
    validate_workflow_definition,
    validate_workflow_run_context,
    validate_workflow_v3,
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
from .yaml_io import (
    dump_workflow_yaml,
    order_workflow_document,
    write_workflow_yaml_atomic,
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
    "MIGRATION_NAMESPACE",
    "MigrationRecord",
    "RECIPE_CATALOG_SCHEMA",
    "UnknownParamsPolicy",
    "UpgradeError",
    "UpgradeResult",
    "ValidatedWorkflowGraph",
    "ValidationProfile",
    "WORKFLOW_BINDING_SCHEMA",
    "WORKFLOW_SEMANTICS_VERSION",
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
    "build_validated_graph",
    "build_workflow_binding",
    "build_workflow_definition_payload_v3",
    "calc_input_diagnostics",
    "calc_param_fields",
    "can_execute",
    "can_parse",
    "canonical_step_name",
    "canonical_workflow_payload",
    "confgen_keys",
    "confgen_param_fields",
    "detect_schema_version",
    "dump_workflow_yaml",
    "editor_manifest_sha256",
    "is_valid_namespace",
    "load_raw_mapping",
    "load_workflow_definition",
    "normalize_step_inputs",
    "order_workflow_document",
    "param_properties",
    "parse_canonical_workflow",
    "parse_unknown_params_policy",
    "parse_v3_document",
    "parse_workflow_binding",
    "parse_workflow_mapping",
    "recipe_catalog_sha256",
    "require_executable",
    "resolve_calc_step",
    "resolve_global_options",
    "resolve_step_semantic_params",
    "to_canonical_workflow",
    "topo_order",
    "upgrade_v2_to_v3",
    "v2_calc_keys",
    "v3_calc_keys",
    "v3_id_order_key",
    "validate_v3_definition",
    "validate_workflow_definition",
    "validate_workflow_run_context",
    "validate_workflow_v3",
    "workflow_definition_fingerprint_v3",
    "workflow_fingerprint",
    "workflow_fragment_schema_sha256_v3",
    "workflow_json_schema_v3",
    "workflow_schema_sha256",
    "workflow_schema_sha256_v3",
    "write_workflow_yaml_atomic",
    "ConfigIssue",
    "ConfigValidationError",
]
