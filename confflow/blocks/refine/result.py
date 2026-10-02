#!/usr/bin/env python3

"""Structured result of a conformer-refinement operation."""

from __future__ import annotations

from dataclasses import dataclass

__all__ = ["RefineResult"]


@dataclass(frozen=True)
class RefineResult:
    """Structured result of a conformer-refinement operation."""

    produced_output: bool
    output_path: str
    kept_count: int
    reason: str = ""
