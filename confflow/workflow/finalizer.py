"""Workflow completion and producer-side artifact publication boundary."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from .presenter import emit_final_report_and_lowest, write_final_statistics
from .state import WorkflowState, WorkflowStateStore
from .stats import Tracer


def finalize_workflow(
    *,
    root_dir: str,
    final_output: str | list[str],
    original_inputs: list[str],
    terminal_outputs: Mapping[str, list[str]],
    final_stats: dict[str, Any],
    state: WorkflowState,
    state_store: WorkflowStateStore,
    execution_count: int,
    logger: Any,
) -> dict[str, Any]:
    """Publish the final state, reports, and fixed sidecars as one boundary."""
    final_stats["terminal_outputs"] = dict(terminal_outputs)
    state.final_status = "completed"
    state.wavefront_index = execution_count
    state_store.save(state)

    try:
        Tracer.trace_low_energy(final_stats)
    except (OSError, ValueError, TypeError, KeyError, AttributeError) as error:
        logger.debug(f"Trace failed: {error}")

    emit_final_report_and_lowest(final_output, original_inputs, final_stats, logger)
    write_final_statistics(root_dir, final_stats)
    return final_stats


__all__ = ["finalize_workflow"]
