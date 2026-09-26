#!/usr/bin/env python3

"""Byte-level V4 workflow validation for producers and editors.

:func:`validate_workflow_bytes` parses V4 text, compiles it through the V4
parser and compiler (shape, capability, binding, and cardinality checks), and
returns a structured :class:`ValidationReport`. Diagnostics carry a stable
``code``/``severity``/``step_id``/``field-path``/``message`` shape; unexpected
failures become one structured diagnostic -- never a bare traceback. Output
is deterministic canonical JSON.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ..config.canonical.contract import CONFIGURATION_VALIDATION_SCHEMA
from ..domain.canonical import canonical_json_bytes
from ..domain.diagnostics import Diagnostic
from ..execution.registry import ExecutionRegistry
from ..workflow.v4.compiler import compile_workflow
from ..workflow.v4.diagnostics import (
    DiagnosticCode,
    DiagnosticReason,
    error,
)
from ..workflow.v4.parser import WorkflowYamlError, parse_workflow_document, parse_workflow_text

#: Schema id of the validation response envelope (kept at v1, shared single source).
VALIDATION_RESPONSE_SCHEMA = CONFIGURATION_VALIDATION_SCHEMA


def _diagnostic_dict(diagnostic: Diagnostic) -> dict[str, Any]:
    """Return the wire form of one structured diagnostic."""
    return {
        "code": diagnostic.code,
        "severity": diagnostic.severity.value,
        "step_id": diagnostic.step_id,
        "field_path": diagnostic.field_path,
        "message": diagnostic.message,
        "reason": diagnostic.details.get("reason"),
    }


@dataclass(frozen=True, slots=True)
class ValidationReport:
    """Outcome of validating one V4 workflow byte payload."""

    ok: bool
    diagnostics: tuple[dict[str, Any], ...] = ()
    definition_digest: str | None = None
    step_ids: tuple[str, ...] = ()
    schema_id: str | None = None

    def errors(self) -> tuple[dict[str, Any], ...]:
        """Return the error diagnostics in deterministic order."""
        return tuple(item for item in self.diagnostics if item["severity"] == "error")

    def to_dict(self) -> dict[str, Any]:
        """Return a canonical, JSON-compatible representation."""
        return {
            "schema": VALIDATION_RESPONSE_SCHEMA,
            "ok": self.ok,
            "diagnostics": [dict(item) for item in self.diagnostics],
            "definition_digest": self.definition_digest,
            "step_ids": list(self.step_ids),
            "workflow_schema_id": self.schema_id,
        }

    def to_canonical_json(self) -> str:
        """Return the deterministic canonical JSON encoding of the report."""
        return canonical_json_bytes(self.to_dict()).decode("utf-8")


def _decode(data: bytes) -> tuple[str | None, Diagnostic | None]:
    """Decode UTF-8 bytes, returning a diagnostic on failure."""
    try:
        return data.decode("utf-8"), None
    except UnicodeDecodeError as exc:
        return None, error(
            DiagnosticCode.SCHEMA_ERROR,
            DiagnosticReason.INVALID_VALUE,
            f"input is not valid UTF-8: {exc}",
            field_path="<document>",
        )


def _load_mapping(text: str) -> tuple[Any | None, Diagnostic | None]:
    """Parse YAML text into a mapping, returning a diagnostic on failure."""
    try:
        return parse_workflow_text(text), None
    except WorkflowYamlError as exc:
        return None, error(
            DiagnosticCode.SCHEMA_ERROR,
            DiagnosticReason.YAML_SYNTAX,
            str(exc),
            field_path="<document>",
        )


def validate_workflow_bytes(
    data: bytes, *, registry: ExecutionRegistry | None = None
) -> ValidationReport:
    """Validate V4 workflow *data* and return a structured report.

    The payload moves through parse (shape) and compile (capability, bind,
    and cardinality checks). Every failure mode -- undecodable bytes,
    invalid YAML, shape errors, semantic errors, and unexpected internal
    errors -- is reported as structured diagnostics; tracebacks never escape.

    Parameters
    ----------
    data :
        Raw workflow document bytes (YAML text).
    registry :
        Execution registry compilation resolves against. Defaults to the
        shared default registry.
    """
    text, decode_diagnostic = _decode(data)
    if text is None:
        assert decode_diagnostic is not None
        return ValidationReport(ok=False, diagnostics=(_diagnostic_dict(decode_diagnostic),))
    raw, yaml_diagnostic = _load_mapping(text)
    if raw is None:
        assert yaml_diagnostic is not None
        return ValidationReport(ok=False, diagnostics=(_diagnostic_dict(yaml_diagnostic),))
    try:
        parsed = parse_workflow_document(raw)
        if parsed.definition is None:
            diagnostics = tuple(_diagnostic_dict(item) for item in parsed.diagnostics)
            return ValidationReport(ok=False, diagnostics=diagnostics)
        compiled = compile_workflow(parsed, registry=registry)
        diagnostics = tuple(_diagnostic_dict(item) for item in compiled.diagnostics)
        if not compiled.ok or compiled.plan is None:
            return ValidationReport(ok=False, diagnostics=diagnostics)
        plan = compiled.plan
        return ValidationReport(
            ok=True,
            diagnostics=diagnostics,
            definition_digest=plan.definition_digest,
            step_ids=tuple(step.step_id for step in plan.steps),
            schema_id=plan.schema,
        )
    except Exception as exc:  # noqa: BLE001 - every failure becomes a diagnostic
        diagnostic = error(
            DiagnosticCode.SCHEMA_ERROR,
            DiagnosticReason.INVALID_VALUE,
            f"validation failed: {type(exc).__name__}: {exc}",
            field_path="<document>",
        )
        return ValidationReport(ok=False, diagnostics=(_diagnostic_dict(diagnostic),))


__all__ = ["VALIDATION_RESPONSE_SCHEMA", "ValidationReport", "validate_workflow_bytes"]
