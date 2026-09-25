#!/usr/bin/env python3

"""Typed diagnostics for the V4 compiler.

The eight diagnostic *codes* are the frozen top-level families required by the
V4 specification.  The machine-readable discriminator is
``details["reason"]``, taken from :class:`DiagnosticReason`, so callers never
parse human messages and codes stay stable while reasons grow.
"""

from __future__ import annotations

from collections.abc import Mapping
from enum import Enum
from typing import Any

from ...domain.diagnostics import Diagnostic, DiagnosticSeverity

__all__ = [
    "DiagnosticCode",
    "DiagnosticReason",
    "error",
    "reason_of",
    "warning",
]


class DiagnosticCode(str, Enum):
    """Frozen top-level diagnostic families."""

    SCHEMA_ERROR = "schema_error"
    COMPILE_ERROR = "compile_error"
    BINDING_ERROR = "binding_error"
    CARDINALITY_ERROR = "cardinality_error"
    CAPABILITY_ERROR = "capability_error"
    IDENTITY_ERROR = "identity_error"
    RESOURCE_ERROR = "resource_error"
    SCIENTIFIC_PARAMETER_CONFLICT = "scientific_parameter_conflict"


class DiagnosticReason(str, Enum):
    """Stable machine-readable reasons carried in ``details["reason"]``."""

    # schema
    VERSION_UNSUPPORTED = "version_unsupported"
    DUPLICATE_KEY = "duplicate_key"
    UNKNOWN_MEMBER = "unknown_member"
    MISSING_REQUIRED_MEMBER = "missing_required_member"
    WRONG_TYPE = "wrong_type"
    INVALID_STEP_ID = "invalid_step_id"
    INVALID_INPUT_NAME = "invalid_input_name"
    INVALID_VALUE = "invalid_value"
    MINIMUM_SUCCESS_NOT_ALLOWED = "minimum_success_not_allowed"
    MINIMUM_SUCCESS_INVALID = "minimum_success_invalid"
    EMPTY_STEPS = "empty_steps"
    YAML_SYNTAX = "yaml_syntax"

    # compile
    DEPENDENCY_CYCLE = "dependency_cycle"
    SELF_REFERENCE = "self_reference"
    DISABLED_PASSTHROUGH_AMBIGUOUS = "disabled_passthrough_ambiguous"
    DISABLED_ROOT_NO_SOURCE = "disabled_root_no_source"
    DISABLED_STEP_UNUSED = "disabled_step_unused"
    PARTIAL_CONSUMPTION_UNDEFINED = "partial_consumption_undefined"
    PARTIAL_OUTPUT_DENIED = "partial_output_denied"
    SELECTOR_COMPOSITION_UNSUPPORTED = "selector_composition_unsupported"
    NOT_ASSEMBLABLE = "not_assemblable"

    # binding
    SOURCE_KIND_INVALID = "source_kind_invalid"
    UNKNOWN_SOURCE_STEP = "unknown_source_step"
    UNKNOWN_PORT = "unknown_port"
    UNKNOWN_RUN_INPUT = "unknown_run_input"
    KIND_MISMATCH = "kind_mismatch"
    SELECTOR_NOT_APPLICABLE = "selector_not_applicable"
    UNKNOWN_SELECTOR_ROLE = "unknown_selector_role"
    ROLE_REQUIRED = "role_required"
    PAIRING_UNDEFINED = "pairing_undefined"
    PAIRING_NOT_ALLOWED = "pairing_not_allowed"
    PARTIAL_CONSUMPTION_NOT_ALLOWED = "partial_consumption_not_allowed"

    # cardinality
    REQUIRED_INPUT_MISSING = "required_input_missing"
    RUN_INPUT_MISSING = "run_input_missing"
    RUN_INPUT_UNEXPECTED = "run_input_unexpected"
    CARDINALITY_MISMATCH = "cardinality_mismatch"
    ARTIFACT_SUBJECT_MISSING = "artifact_subject_missing"
    SUBJECT_IDENTITY_MISSING = "subject_identity_missing"
    MINIMUM_SUCCESS_UNREACHABLE = "minimum_success_unreachable"

    # capability
    UNKNOWN_EXECUTOR_CAPABILITY = "unknown_executor_capability"
    UNKNOWN_EXECUTION_ADAPTER = "unknown_execution_adapter"
    UNKNOWN_RESULT_PROFILE = "unknown_result_profile"
    UNKNOWN_SCIENTIFIC_CHECK = "unknown_scientific_check"
    UNKNOWN_RECOVERY = "unknown_recovery"
    DISABLED_CAPABILITY_LOST = "disabled_capability_lost"
    ADAPTER_REQUIRED_PORT_MISSING = "adapter_required_port_missing"
    ADAPTER_CAPABILITY_MISMATCH = "adapter_capability_mismatch"
    CHECK_NOT_SUPPORTED = "check_not_supported"
    RECOVERY_UNSUPPORTED = "recovery_unsupported"
    MISSING_EXECUTOR_BLOCK = "missing_executor_block"
    MISPLACED_EXECUTOR_BLOCK = "misplaced_executor_block"
    UNKNOWN_TRANSFORM_KIND = "unknown_transform_kind"
    EXECUTOR_BLOCK_CONFLICT = "executor_block_conflict"
    SEED_REQUIRED = "seed_required"

    # identity
    DUPLICATE_STEP_ID = "duplicate_step_id"
    DUPLICATE_INPUT_NAME = "duplicate_input_name"
    DUPLICATE_LOGICAL_KEY = "duplicate_logical_key"
    DUPLICATE_SUBJECT_KEY = "duplicate_subject_key"
    DUPLICATE_STRUCTURE_IDENTITY = "duplicate_structure_identity"

    # resource
    CORES_PER_ITEM_INVALID = "cores_per_item_invalid"
    MEMORY_PER_ITEM_INVALID = "memory_per_item_invalid"
    SCHEDULER_WIDTH_INVALID = "scheduler_width_invalid"
    UNRESOLVED_RESOURCES = "unresolved_resources"

    # scientific parameters
    STRUCTURE_VALUE_OVERRIDDEN = "structure_value_overridden"
    ELECTRON_PARITY_MISMATCH = "electron_parity_mismatch"
    METADATA_UNAVAILABLE = "metadata_unavailable"
    FREEZE_INDEX_OUT_OF_RANGE = "freeze_index_out_of_range"
    ATOM_COUNT_MISMATCH = "atom_count_mismatch"
    ELEMENT_MISMATCH = "element_mismatch"
    SCIENTIFIC_VALUE_MISMATCH = "scientific_value_mismatch"


def _build(
    code: DiagnosticCode,
    reason: DiagnosticReason,
    message: str,
    severity: DiagnosticSeverity,
    *,
    step_id: str | None = None,
    work_item_id: str | None = None,
    logical_key: str | None = None,
    field_path: str | None = None,
    details: Mapping[str, Any] | None = None,
) -> Diagnostic:
    merged: dict[str, Any] = {"reason": reason.value}
    if details:
        merged.update(details)
    return Diagnostic(
        code=code.value,
        message=message,
        severity=severity,
        step_id=step_id,
        work_item_id=work_item_id,
        logical_key=logical_key,
        field_path=field_path,
        details=merged,
    )


def error(
    code: DiagnosticCode,
    reason: DiagnosticReason,
    message: str,
    *,
    step_id: str | None = None,
    work_item_id: str | None = None,
    logical_key: str | None = None,
    field_path: str | None = None,
    details: Mapping[str, Any] | None = None,
) -> Diagnostic:
    """Build an error diagnostic with a stable machine-readable reason."""
    return _build(
        code,
        reason,
        message,
        DiagnosticSeverity.ERROR,
        step_id=step_id,
        work_item_id=work_item_id,
        logical_key=logical_key,
        field_path=field_path,
        details=details,
    )


def warning(
    code: DiagnosticCode,
    reason: DiagnosticReason,
    message: str,
    *,
    step_id: str | None = None,
    work_item_id: str | None = None,
    logical_key: str | None = None,
    field_path: str | None = None,
    details: Mapping[str, Any] | None = None,
) -> Diagnostic:
    """Build a warning diagnostic with a stable machine-readable reason."""
    return _build(
        code,
        reason,
        message,
        DiagnosticSeverity.WARNING,
        step_id=step_id,
        work_item_id=work_item_id,
        logical_key=logical_key,
        field_path=field_path,
        details=details,
    )


def reason_of(diagnostic: Diagnostic) -> str | None:
    """Return the machine-readable reason carried by *diagnostic*."""
    return diagnostic.details.get("reason")
