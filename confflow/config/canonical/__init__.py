"""Canonical configuration parsing primitives.

This package is an additive boundary. Existing v2 dataclass and Pydantic
entry points remain the compatibility surface while callers migrate.
"""

from .contract import (
    CONFIGURATION_CONTRACT_SCHEMA,
    CONFIGURATION_VALIDATION_SCHEMA,
    build_configuration_contract,
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
from .resolve import resolve_calc_step, resolve_global_options
from .schema import workflow_schema_sha256

__all__ = [
    "CONFIGURATION_CONTRACT_SCHEMA",
    "CONFIGURATION_VALIDATION_SCHEMA",
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
    "load_raw_mapping",
    "parse_workflow_mapping",
    "resolve_calc_step",
    "resolve_global_options",
    "workflow_schema_sha256",
]
