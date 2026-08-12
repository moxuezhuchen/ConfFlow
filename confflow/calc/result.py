#!/usr/bin/env python3
"""Neutral result types shared by calculation and refinement adapters."""

from __future__ import annotations

from dataclasses import dataclass

__all__ = ["RefineResult"]


@dataclass(frozen=True)
class RefineResult:
    """Structured result of a conformer-refinement operation.

    The result belongs to the calculation domain, not to the concrete refine
    block, so callers can consume it without depending on block internals.
    """

    produced_output: bool
    output_path: str
    kept_count: int
    reason: str = ""
