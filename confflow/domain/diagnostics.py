#!/usr/bin/env python3

"""Typed diagnostics for the V4 domain and compiler.

Diagnostics are structured records: callers (compiler, scheduler, future
JobDesk integration) must never parse exception strings.  Concrete diagnostic
code values are owned by the emitting layer (for example the V4 compiler's
``DiagnosticCode`` enum); the domain layer only enforces that codes are
non-empty strings.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Final

from ._immutable import FrozenDict
from .errors import DomainError

__all__ = [
    "Diagnostic",
    "DiagnosticSeverity",
    "diagnostic_sort_key",
]


class DiagnosticSeverity(str, Enum):
    """Severity levels for V4 diagnostics."""

    ERROR = "error"
    WARNING = "warning"
    INFO = "info"


_SEVERITY_RANK: Final[dict[DiagnosticSeverity, int]] = {
    DiagnosticSeverity.ERROR: 0,
    DiagnosticSeverity.WARNING: 1,
    DiagnosticSeverity.INFO: 2,
}


@dataclass(frozen=True, slots=True)
class Diagnostic:
    """A single structured diagnostic message.

    Parameters
    ----------
    code : str
        Stable machine-readable code such as ``"binding_error"``.
    message : str
        Human-readable message.
    severity : DiagnosticSeverity
        Severity level; defaults to ``ERROR``.
    step_id : str | None
        Related workflow step, when applicable.
    work_item_id : str | None
        Related work item id, when applicable.
    logical_key : str | None
        Related work item logical key, when applicable.
    field_path : str | None
        Field or binding path such as ``"steps.s_opt.bindings.structure"``.
    details : FrozenDict
        Additional machine-readable detail.

    Raises
    ------
    DomainError
        Raised when *code* or *message* is empty.
    """

    code: str
    message: str
    severity: DiagnosticSeverity = DiagnosticSeverity.ERROR
    step_id: str | None = None
    work_item_id: str | None = None
    logical_key: str | None = None
    field_path: str | None = None
    details: FrozenDict = field(default_factory=FrozenDict)

    def __post_init__(self) -> None:
        if not isinstance(self.code, str) or not self.code.strip():
            raise DomainError("diagnostic code must be a non-empty string")
        if not isinstance(self.message, str) or not self.message.strip():
            raise DomainError("diagnostic message must be a non-empty string")
        if not isinstance(self.details, FrozenDict):
            object.__setattr__(self, "details", FrozenDict(self.details))

    @property
    def is_error(self) -> bool:
        """Return whether this diagnostic is an error."""
        return self.severity is DiagnosticSeverity.ERROR


def diagnostic_sort_key(diagnostic: Diagnostic) -> tuple[int, str, str, str, str, str]:
    """Return a deterministic ordering key for diagnostics.

    Errors sort before warnings, then by code and location, so identical
    compilations always report diagnostics in the same order.
    """
    return (
        _SEVERITY_RANK[diagnostic.severity],
        diagnostic.code,
        diagnostic.step_id or "",
        diagnostic.logical_key or "",
        diagnostic.field_path or "",
        diagnostic.message,
    )
