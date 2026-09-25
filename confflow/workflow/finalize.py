#!/usr/bin/env python3

"""Typed workflow finalization boundary.

The execution engine owns step dispatch; this module owns the durable final
state transition and the sidecar/report writes that follow it. Workflow-Config
V2 runs finalize through :func:`finalize_workflow` (untouched V1 behavior);
Workflow-Config V3 runs finalize through :func:`finalize_workflow_v3`, which
publishes ``output_manifest.v2`` and ``workflow_stats.v2`` — both identified
by the stable step ID, with labels as display snapshots only.
"""

from __future__ import annotations

import os
import time
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from ..artifact_json import write_atomic_json
from ..config.canonical.v3_graph import is_persisted_id
from ..contract import (
    OUTPUT_MANIFEST_FILE,
    OUTPUT_MANIFEST_SCHEMA_V2,
    WORKFLOW_STATS_FILE,
    WORKFLOW_STATS_SCHEMA_V2,
)
from .helpers import count_conformers_any
from .presenter import (
    _relative_manifest_artifact,
    emit_final_report_and_lowest,
    emit_v3_final_report,
)
from .state import WorkflowState, WorkflowStateStore
from .stats import TaskStatsCollector, WorkflowStatsTracker

if TYPE_CHECKING:
    from .binding_v2 import WorkflowBindingV2
    from .plan import WorkflowV3Plan
    from .state import WorkflowStateV2

__all__ = ["finalize_workflow", "finalize_workflow_v3"]

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


# ---------------------------------------------------------------------------
# Workflow V3 finalization (output_manifest.v2 / workflow_stats.v2)
# ---------------------------------------------------------------------------
def _v3_artifact_list(
    output: str | list[str] | None,
    work_dir: str,
    staged_inputs: tuple[str, ...],
) -> list[str]:
    """Return output paths that are workflow-produced files inside the root.

    Mirrors the V1 rule: a disabled terminal may forward an execution input,
    and an input — including its V3 staged copy under ``external_inputs/`` —
    is never a produced artifact and cannot be advertised by the manifest.
    """
    root = os.path.realpath(work_dir)
    staged = {os.path.realpath(path) for path in staged_inputs}
    artifacts: list[str] = []
    paths = [output] if isinstance(output, str) else list(output or [])
    for path in paths:
        if not isinstance(path, str):
            continue
        resolved = os.path.realpath(path)
        try:
            contained = os.path.commonpath((root, resolved)) == root
        except ValueError:
            contained = False
        if contained and resolved != root and resolved not in staged and os.path.isfile(path):
            abs_path = os.path.abspath(path)
            if abs_path not in artifacts:
                artifacts.append(abs_path)
    return artifacts


def _v3_conformer_count(paths: Any) -> int:
    try:
        return count_conformers_any(paths)
    except (OSError, ValueError, TypeError):
        return 0


def build_workflow_stats_v3(
    *,
    work_dir: str,
    plan: WorkflowV3Plan,
    state: WorkflowStateV2,
    outputs: dict[str, str | list[str]],
    staged_inputs: tuple[str, ...],
    binding: WorkflowBindingV2,
) -> dict[str, Any]:
    """Build the ``workflow_stats.v2`` payload (stable-ID identity, label display)."""
    from .v3_dataflow import materialize_effective_inputs, resolve_effective_sources_v3

    sources = resolve_effective_sources_v3(plan, external_input_count=len(staged_inputs))
    outputting_terminals = [
        terminal for terminal in plan.terminals if outputs.get(terminal) is not None
    ]
    final_output: str | list[str]
    if len(outputting_terminals) == 1:
        final_output = _v3_artifact_list(outputs[outputting_terminals[0]], work_dir, staged_inputs)
        if len(final_output) == 1:
            final_output = final_output[0]
    elif outputting_terminals:
        collected: list[str] = []
        for terminal in outputting_terminals:
            for artifact in _v3_artifact_list(outputs[terminal], work_dir, staged_inputs):
                if artifact not in collected:
                    collected.append(artifact)
        final_output = collected
    else:
        final_output = staged_inputs[0] if len(staged_inputs) == 1 else list(staged_inputs)

    steps: list[dict[str, Any]] = []
    for index, step_id in enumerate(state.execution_order):
        record = state.step(step_id)
        step_dir = os.path.join(work_dir, "steps", step_id)
        effective_input = materialize_effective_inputs(
            step_id,
            sources,
            staged_inputs=staged_inputs,
            step_outputs=outputs,
        )
        duration = 0.0
        if record.submitted_at is not None and record.completed_at is not None:
            duration = round(record.completed_at - record.submitted_at, 2)
        failed_conformers = 0
        if record.type == "calc" and os.path.isfile(os.path.join(step_dir, "results.db")):
            failed_conformers = (
                TaskStatsCollector.count_failed(os.path.join(step_dir, "results.db")) or 0
            )
        steps.append(
            {
                "index": index + 1,
                "id": step_id,
                "label": record.label,
                "type": record.type,
                "status": record.status,
                "input_conformers": _v3_conformer_count(effective_input),
                "output_conformers": _v3_conformer_count(record.output_xyz or effective_input),
                "failed_conformers": failed_conformers,
                "duration_seconds": duration,
                "output_xyz": record.output_xyz,
                "error": record.error,
            }
        )

    final_outputs = (
        [os.path.abspath(p) for p in final_output]
        if isinstance(final_output, list)
        else ([os.path.abspath(final_output)] if isinstance(final_output, str) else [])
    )
    return {
        "content_schema": WORKFLOW_STATS_SCHEMA_V2,
        "start_time": state.started_at,
        "end_time": time.time(),
        "total_duration_seconds": round(time.time() - state.started_at, 2),
        "input_files": list(state.input_files),
        "original_input_files": list(state.original_inputs),
        "initial_conformers": _v3_conformer_count(
            staged_inputs[0] if len(staged_inputs) == 1 else list(staged_inputs)
        ),
        "final_output": final_output if isinstance(final_output, str) else None,
        "final_outputs": final_outputs,
        "final_conformers": _v3_conformer_count(final_output),
        "steps": steps,
        "terminal_outputs": {
            terminal: [
                artifact
                for artifact in _v3_artifact_list(outputs[terminal], work_dir, staged_inputs)
            ]
            for terminal in plan.terminals
            if _v3_artifact_list(outputs.get(terminal), work_dir, staged_inputs)
        },
        "definition_fingerprint": binding.definition_fingerprint,
        "execution_fingerprint": binding.execution_fingerprint,
    }


def build_output_manifest_v3(
    root_dir: str, terminal_outputs: dict[str, list[str]], labels: dict[str, str | None]
) -> dict[str, Any]:
    """Build the ``output_manifest.v2`` payload.

    RFC §17 shape: per terminal ``{id, label, artifacts}`` — machine identity
    is the stable step ID, the label is a display snapshot, and the artifact
    paths are relative, canonical, work-root-safe POSIX paths. The terminals
    list natively expresses 0/1/N terminal outputs.
    """
    terminals = []
    for terminal, artifacts in terminal_outputs.items():
        if not is_persisted_id(terminal):
            raise ValueError(f"output manifest terminal is not a legal V3 step id: {terminal!r}")
        terminals.append(
            {
                "id": terminal,
                "label": labels.get(terminal),
                "artifacts": [
                    _relative_manifest_artifact(root_dir, artifact) for artifact in artifacts
                ],
            }
        )
    return {"content_schema": OUTPUT_MANIFEST_SCHEMA_V2, "terminals": terminals}


def finalize_workflow_v3(
    *,
    work_dir: str,
    plan: WorkflowV3Plan,
    state: WorkflowStateV2,
    outputs: dict[str, str | list[str]],
    staged_inputs: tuple[str, ...],
    binding: WorkflowBindingV2,
    logger: Any,
) -> dict[str, Any]:
    """Finalize a completed Workflow-V3 run and publish its v2 sidecars.

    Called only after the runtime reached a coherent completed state (every
    step completed or skipped, ``final_status`` persisted first — the state is
    the execution-progress authority, the manifest is the completed-output
    publication). Stats are written before the manifest so a crash mid-write
    can never publish a manifest without its stats; both writes are atomic.
    The public report sidecars (text report + lowest-energy XYZ beside the
    original input) reuse the V1 presenter verbatim — presentation only.
    """
    final_stats = build_workflow_stats_v3(
        work_dir=work_dir,
        plan=plan,
        state=state,
        outputs=outputs,
        staged_inputs=staged_inputs,
        binding=binding,
    )

    final_output = final_stats["final_output"] or (
        staged_inputs[0] if len(staged_inputs) == 1 else list(staged_inputs)
    )
    emit_final_report_and_lowest(final_output, list(state.original_inputs), final_stats, logger)

    write_atomic_json(os.path.join(work_dir, WORKFLOW_STATS_FILE), final_stats)

    terminal_outputs: dict[str, list[str]] = {
        terminal: artifacts
        for terminal, artifacts in final_stats["terminal_outputs"].items()
        if artifacts
    }
    manifest = build_output_manifest_v3(
        work_dir,
        terminal_outputs,
        labels={step_id: state.step(step_id).label for step_id in terminal_outputs},
    )
    write_atomic_json(os.path.join(work_dir, OUTPUT_MANIFEST_FILE), manifest)

    try:
        emit_v3_final_report(final_stats)
    except Exception as error:  # noqa: BLE001 - presentation must never fail a run
        logger.debug(f"V3 final report failed: {error}")

    return {
        "final_output": final_stats["final_output"],
        "final_outputs": final_stats["final_outputs"],
        "terminal_outputs": terminal_outputs,
        "step_outputs": dict(outputs),
        "stats": final_stats,
    }
