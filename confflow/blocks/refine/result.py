#!/usr/bin/env python3

"""Structured refine result objects."""

from __future__ import annotations

from dataclasses import dataclass

__all__ = ["RefineResult"]


@dataclass(frozen=True)
class RefineResult:
    produced_output: bool
    output_path: str
    kept_count: int
    reason: str = ""
