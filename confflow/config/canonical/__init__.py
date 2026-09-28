"""Canonical configuration parsing primitives.

This package is the V1/V2 compatibility boundary. Existing V2 dataclass and
Pydantic entry points remain the released configuration surface while callers
migrate.

It also owns the producer-side *editing* contract: the workflow schema, the
editor manifest and the recipe catalog, and the contract documents that publish
them (``confflow.configuration-contract.v1`` and ``.v2``).

The never-released Workflow V3 line (V3 parser/graph, the V3 semantic
validation profile, the ``configuration-contract.v3`` document, the V2->V3
upgrade emitter and the V3 editor/recipe catalogs) was retired by the
Architecture Diet PR-7: V3 was never a published public wire, so nothing here
imports, exports or advertises it.
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
    v2_calc_keys,
)
from .parser import (
    detect_schema_version,
    detect_workflow_file_version,
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
    WORKFLOW_SCHEMA_VERSION,
    WORKFLOW_SCHEMA_VERSION_V2,
    workflow_json_schema,
    workflow_schema_sha256,
)
from .serialization import CANONICALIZATION_VERSION
from .v2_adapter import to_canonical_workflow
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
    "CANONICALIZATION_VERSION",
    "CONFIGURATION_CONTRACT_BUILDERS",
    "CONFIGURATION_CONTRACT_SCHEMA",
    "CONFIGURATION_CONTRACT_V1_SCHEMA",
    "CONFIGURATION_CONTRACT_V2_SCHEMA",
    "CONFIGURATION_VALIDATION_SCHEMA",
    "EDITOR_MANIFEST_SCHEMA",
    "RECIPE_CATALOG_SCHEMA",
    "WORKFLOW_BINDING_SCHEMA",
    "WORKFLOW_SCHEMA_VERSION",
    "WORKFLOW_SCHEMA_VERSION_V2",
    "CanonicalStepDefinition",
    "CanonicalWorkflowDefinition",
    "ConfigIssue",
    "ConfigValidationError",
    "DependencyMode",
    "Diagnostic",
    "ParamFieldDescriptor",
    "Severity",
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
    "canonical_step_name",
    "canonical_workflow_payload",
    "confgen_keys",
    "confgen_param_fields",
    "detect_schema_version",
    "detect_workflow_file_version",
    "editor_manifest_sha256",
    "load_raw_mapping",
    "load_workflow_definition",
    "normalize_step_inputs",
    "parse_canonical_workflow",
    "parse_workflow_binding",
    "parse_workflow_mapping",
    "recipe_catalog_sha256",
    "resolve_calc_step",
    "resolve_global_options",
    "to_canonical_workflow",
    "topo_order",
    "v2_calc_keys",
    "validate_workflow_definition",
    "validate_workflow_run_context",
    "workflow_fingerprint",
    "workflow_json_schema",
    "workflow_schema_sha256",
]
