#!/usr/bin/env python3

"""Structured configuration validation diagnostics.

A :class:`Diagnostic` is the internal, machine-readable form of one finding: a
stable ``code`` (for programs), a human ``message`` (for the CLI), the document
``path`` it attaches to, an optional ``step_ref`` and a ``severity``.

The public ``confflow.configuration-validation.v1`` CLI response does **not**
expose this richer shape: its ``issues`` are exactly ``{path, message}`` because
the JobDesk consumer refuses any other member set. The extra fields stay inside
the process and are projected to the v1 issue shape at the CLI boundary.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

__all__ = ["Diagnostic", "Severity"]

Severity = Literal["error", "warning"]


@dataclass(frozen=True)
class Diagnostic:
    """One configuration finding at a stable logical path."""

    code: str
    severity: Severity
    path: str
    message: str
    step_ref: str | None = None

    @property
    def is_error(self) -> bool:
        return self.severity == "error"

    def to_v1_issue(self) -> dict[str, str]:
        """Project to the frozen ``configuration-validation.v1`` issue shape."""
        return {"path": self.path, "message": self.message}

    def __str__(self) -> str:
        return f"{self.path}: {self.message}" if self.path else self.message
