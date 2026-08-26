#!/usr/bin/env python3

"""Application composition bridges for workflow execution."""

from __future__ import annotations

from ..calc.postprocess import RefineRequest, set_default_refine_callable


def run_refine_block(request: RefineRequest) -> object:
    """Adapt the concrete refine block to the neutral calc request."""
    from ..blocks.refine import RefineOptions, process_xyz

    return process_xyz(
        RefineOptions(
            input_file=request.input_file,
            output=request.output_file,
            threshold=request.threshold,
            ewin=request.ewin,
            imag=request.imag,
            noH=request.noH,
            max_conformers=request.max_conformers,
            dedup_only=request.dedup_only,
            keep_all_topos=request.keep_all_topos,
            energy_tolerance=request.energy_tolerance,
            workers=request.workers,
        )
    )


def configure_default_refine() -> None:
    """Compose the concrete refine block into calc's neutral port."""
    set_default_refine_callable(run_refine_block)
