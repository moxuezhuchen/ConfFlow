#!/usr/bin/env python3

"""Shared post-processing adapters for calc outputs."""

from __future__ import annotations

import os

from ..blocks import refine
from ..blocks.refine.result import RefineResult

__all__ = ["run_refine_postprocess"]


def run_refine_postprocess(
    *,
    input_file: str,
    output_file: str,
    threshold: float,
    ewin: float | None,
    energy_tolerance: float,
    workers: int,
) -> RefineResult:
    """Run refine post-processing through a shared stable adapter."""
    options = refine.RefineOptions(
        input_file=input_file,
        output=output_file,
        threshold=threshold,
        ewin=ewin,
        energy_tolerance=energy_tolerance,
        workers=workers,
    )
    result = refine.process_xyz(options)
    if isinstance(result, RefineResult):
        return result
    return RefineResult(
        produced_output=os.path.exists(output_file),
        output_path=output_file,
        kept_count=0,
        reason="legacy_refine_return",
    )
