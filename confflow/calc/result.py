#!/usr/bin/env python3
"""Neutral result types shared by calculation and refinement adapters."""

from __future__ import annotations

from dataclasses import dataclass

__all__ = ["RefineResult"]


@dataclass(frozen=True)
class RefineResult:
    """Structured result of a conformer-refinement operation.

    The contract belongs to the calc integration boundary rather than the
    concrete refine block.  The legacy ``blocks.refine.result`` module
    re-exports this exact class to preserve import and pickle identity.
    """

    produced_output: bool
    output_path: str
    kept_count: int
    reason: str = ""
