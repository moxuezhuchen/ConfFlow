#!/usr/bin/env python3

"""Typed workflow finalization boundary.

The execution engine owns step dispatch; this module owns the durable final
state transition and the sidecar/report writes that follow it.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from .state import WorkflowState, WorkflowStateStore
from .stats import WorkflowStatsTracker

__all__ = ["finalize_workflow"]

TraceLowEnergy = Callable[[dict[str, Any]], None]
EmitFinalReport = Callable[[str | list[str], list[str], dict[str, Any], Any], None]
WriteFinalStatistics = Callable[[str, dict[str, Any]], None]


def finalize_workflow(
    *,
    root_dir: str,
    original_inputs: list[str],
    final_output: str | list[str],
    terminal_outputs: dict[str, list[str]],
    execution_count: int,
    state: WorkflowState,
    state_store: WorkflowStateStore,
    stats_tracker: WorkflowStatsTracker,
    logger: Any,
    trace_low_energy: TraceLowEnergy,
    emit_final_report_and_lowest: EmitFinalReport,
    write_final_statistics: WriteFinalStatistics,
) -> dict[str, Any]:
    """Finalize a completed workflow and return its unchanged stats payload.

    The state transition is intentionally persisted before any trace, report,
    or sidecar write.  Callers pass the side-effect functions explicitly so
    the legacy engine-level monkeypatch seams remain effective.
    """
    final_stats = stats_tracker.finalize(final_output)
    final_stats["terminal_outputs"] = terminal_outputs
    state.final_status = "completed"
    state.wavefront_index = execution_count
    state_store.save(state)

    try:
        trace_low_energy(final_stats)
    except (OSError, ValueError, TypeError, KeyError, AttributeError) as e:
        logger.debug(f"Trace failed: {e}")

    emit_final_report_and_lowest(final_output, original_inputs, final_stats, logger)
    write_final_statistics(root_dir, final_stats)
    return final_stats
