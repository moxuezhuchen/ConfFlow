#!/usr/bin/env python3

"""Shared light intent errors (L1-C1 mechanical boundary).

Minimal ``common`` boundary: only :class:`IntentCompilationError` and
:func:`_fail` live here. They are needed by ``compiler``, ``bindings`` and
``resources`` alike; keeping them in ``compiler`` and letting the helpers
import ``compiler`` back would create a ``compiler <-> helpers`` cycle.
This module imports only stdlib ``typing`` so the edge
``common <- {compiler,bindings,resources}`` stays acyclic. No solver,
handler, registry, card, program, workflow, or science knowledge lives here
(root review: real dependency is ``_fail``/``IntentCompilationError`` only;
Gaussian checkpoint details stay in ``resources`` orchestration / G1,
registry/solver knowledge stays in ``compiler``/``bindings`` callers).
"""

from __future__ import annotations

from typing import Any


class IntentCompilationError(ValueError):
    """Raised when a simplified intent cannot become strict V4."""

    def __init__(
        self,
        message: str,
        *,
        step_id: str | None = None,
        field_path: str | None = None,
        diagnostics: Any = None,
    ) -> None:
        super().__init__(message)
        self.step_id = step_id
        self.field_path = field_path
        self.diagnostics = diagnostics


def _fail(
    message: str,
    *,
    step_id: str | None = None,
    field_path: str | None = None,
    diagnostics: Any = None,
) -> IntentCompilationError:
    return IntentCompilationError(
        message, step_id=step_id, field_path=field_path, diagnostics=diagnostics
    )
