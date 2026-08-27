#!/usr/bin/env python3

"""Neutral post-processing port for calc outputs."""

from __future__ import annotations

import os
from collections.abc import Callable
from dataclasses import dataclass

from .result import RefineResult

__all__ = [
    "RefineCallable",
    "RefineRequest",
    "get_default_refine_callable",
    "run_refine_postprocess",
    "set_default_refine_callable",
]


@dataclass(frozen=True)
class RefineRequest:
    """Neutral request passed from calc to a concrete refine implementation."""

    input_file: str
    output_file: str
    threshold: float
    ewin: float | None
    energy_tolerance: float
    workers: int
    noH: bool = False
    dedup_only: bool = False
    keep_all_topos: bool = False
    imag: int | None = None
    max_conformers: int | None = None


RefineCallable = Callable[[RefineRequest], object]
_default_refine_callable: RefineCallable | None = None


def set_default_refine_callable(refine_callable: RefineCallable) -> None:
    """Install the application-composed refine implementation for calc callers."""
    global _default_refine_callable
    _default_refine_callable = refine_callable


def get_default_refine_callable() -> RefineCallable | None:
    """Return the implementation installed by an application composition root."""
    return _default_refine_callable


def run_refine_postprocess(
    *,
    input_file: str,
    output_file: str,
    threshold: float,
    ewin: float | None,
    energy_tolerance: float,
    workers: int,
    noH: bool = False,
    dedup_only: bool = False,
    keep_all_topos: bool = False,
    imag: int | None = None,
    max_conformers: int | None = None,
    refine_callable: RefineCallable | None = None,
) -> RefineResult:
    """Run post-processing through an injected, neutral refine port."""
    if refine_callable is None:
        refine_callable = get_default_refine_callable()
    if refine_callable is None:
        raise ValueError("refine_callable must be provided by the composition root")

    request = RefineRequest(
        input_file=input_file,
        output_file=output_file,
        threshold=threshold,
        ewin=ewin,
        energy_tolerance=energy_tolerance,
        workers=workers,
        noH=noH,
        dedup_only=dedup_only,
        keep_all_topos=keep_all_topos,
        imag=imag,
        max_conformers=max_conformers,
    )
    result = refine_callable(request)
    if isinstance(result, RefineResult):
        return result
    return RefineResult(
        produced_output=os.path.exists(output_file),
        output_path=output_file,
        kept_count=0,
        reason="legacy_refine_return",
    )
