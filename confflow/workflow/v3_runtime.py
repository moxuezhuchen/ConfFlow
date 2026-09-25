"""Workflow V3 runtime projection and the internal execution seam (R4.3/R4.4).

This module turns a planning-only :class:`~confflow.workflow.plan.WorkflowV3Plan`
plus its resolved execution context and immutable binding into a
:class:`WorkflowV3RuntimePlan` — the stable-ID runtime representation — and
executes it through the **existing** calc/confgen step handlers. No execution
algorithm is duplicated here: the runtime only projects identity, paths,
inputs and checkpoint references, then delegates.

Hard boundaries:

- runtime identity is the persisted stable step ID exclusively (labels,
  array indexes, V2 names and legacy dirnames never reach a path);
- execution consumes **staged** slot-normalized external inputs, never the
  source paths (frozen PD-2 / TOCTOU rule);
- the binding is built (fresh run) or validated (resume) BEFORE any runtime
  directory is created or any state mutation happens;
- this seam is internal: ``engine.run_workflow`` keeps refusing V3 while
  ``CAPABILITIES[V3].execute`` is False, and nothing here flips the table.
"""

from __future__ import annotations

import hashlib
import os
import tempfile
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..config.canonical import (
    ValidationProfile,
    resolve_step_semantic_params,
)
from ..config.canonical.v3_graph import is_persisted_id
from ..core.exceptions import ConfFlowError, StopRequestedError
from ..core.utils import get_logger
from .binding_v2 import BindingCompatibility, WorkflowBindingV2, compare_workflow_binding_v2
from .execution_context import (
    ResolvedExecutionContextV3,
    resolve_execution_context_v3,
)
from .finalize import finalize_workflow_v3
from .plan import WorkflowV3Plan, build_workflow_plan
from .state import (
    StepRecordV2,
    WorkflowStateCompatibilityError,
    WorkflowStateV2Store,
    build_initial_state_v2,
)
from .stats import CheckpointManager, FailureTracker
from .v3_dataflow import (
    materialize_effective_inputs,
    resolve_effective_sources_v3,
    validate_effective_dataflow_v3,
)

__all__ = [
    "RuntimeStepV3",
    "WorkflowV3RuntimePlan",
    "compare_bindings_for_resume",
    "project_v3_runtime_plan",
    "run_v3_workflow",
    "stage_v3_external_inputs",
]

logger = get_logger()

_SHA256_PREFIX = "sha256:"


# ---------------------------------------------------------------------------
# Module-level handler indirections — the same pattern the V1 engine uses so
# tests can substitute fake calc/confgen handlers without touching capability
# tables or duplicating execution algorithms.
# ---------------------------------------------------------------------------
def _run_calc_step(**kwargs: Any) -> Any:
    from .step_handlers import run_calc_step

    return run_calc_step(**kwargs)


def _run_confgen_step(**kwargs: Any) -> Any:
    from .step_handlers import run_confgen_step

    return run_confgen_step(**kwargs)


# ---------------------------------------------------------------------------
# Runtime projection
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class RuntimeStepV3:
    """Version-neutral execution projection of one V3 step.

    Handlers receive only what they need (work directory, params, inputs,
    job identity); they never see the V3 schema.
    """

    id: str
    label: str | None
    type: str
    enabled: bool
    inputs: tuple[str, ...]
    params: dict[str, Any]
    step_dir: str
    checkpoint_from: str | None
    checkpoint_dir: str | None
    program: str | None = None  # resolved program for calc steps (chk compatibility)


@dataclass(frozen=True)
class WorkflowV3RuntimePlan:
    """The stable-ID runtime representation of one V3 run.

    Carries no mutable step status (that belongs to
    :class:`~confflow.workflow.state.WorkflowStateV2`) — only identity, paths
    and the immutable binding.
    """

    plan: WorkflowV3Plan
    binding: WorkflowBindingV2
    work_dir: str
    staged_inputs: tuple[str, ...]
    steps: tuple[RuntimeStepV3, ...]  # topological wave order, stable-ID sorted


def _step_dir(work_dir: str, step_id: str) -> str:
    """Runtime directory for one step — filesystem trust boundary.

    Re-validates the persisted-ID grammar even though the plan, config and
    state have all validated it: nothing reaches ``mkdir`` without passing
    the authoritative validator (no traversal, no separators, no ``..``).
    """
    if not is_persisted_id(step_id):
        raise ConfFlowError(
            f"refusing to create a runtime directory: {step_id!r} is not a legal "
            "persisted V3 step id"
        )
    return os.path.join(work_dir, "steps", step_id)


def stage_v3_external_inputs(
    work_dir: str,
    input_files: list[str],
    expected_digests: tuple[str, ...],
) -> tuple[str, ...]:
    """Stage external inputs under slot-based, deterministic names.

    Slot order is the Execution-Fingerprint-C input order — inputs ``A, B``
    and ``B, A`` receive different slot assignments, and the source
    basename/path never appears in a staged name (frozen PD-2). Each source
    file is read **once**: the same bytes buffer is hashed and written, so a
    source modified after resolution cannot contaminate the staged copy —
    a mismatch fails closed instead.
    """
    if len(input_files) != len(expected_digests):
        raise ConfFlowError(
            "external input set changed during resolution: "
            f"{len(input_files)} files vs {len(expected_digests)} resolved digests"
        )
    staging_dir = os.path.join(work_dir, "external_inputs")
    os.makedirs(staging_dir, exist_ok=True)
    staged: list[str] = []
    for index, raw in enumerate(input_files, start=1):
        data = Path(raw).read_bytes()
        digest = _SHA256_PREFIX + hashlib.sha256(data).hexdigest()
        if digest != expected_digests[index - 1]:
            raise ConfFlowError(
                f"external input {raw!r} changed after the execution context was "
                "resolved; refusing to stage inconsistent bytes"
            )
        destination = os.path.join(staging_dir, f"input_{index:04d}.xyz")
        fd, temporary = tempfile.mkstemp(
            prefix=f".{os.path.basename(destination)}.", suffix=".tmp", dir=staging_dir
        )
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, destination)
        except BaseException:
            try:
                os.unlink(temporary)
            except FileNotFoundError:
                pass
            raise
        staged.append(destination)
    return tuple(staged)


def project_v3_runtime_plan(
    plan: WorkflowV3Plan,
    context: ResolvedExecutionContextV3,
    binding: WorkflowBindingV2,
    *,
    work_dir: str,
    staged_inputs: tuple[str, ...],
) -> WorkflowV3RuntimePlan:
    """Project the planning triple into the stable-ID runtime representation."""
    if len(staged_inputs) != context.input_count:
        raise ConfFlowError("staged input set does not match the resolved execution context")
    raw_params = {step.id: step.params for step in plan.definition.steps if step.id}
    checkpoint_targets = {
        step.id: step.checkpoint_from for step in plan.definition.steps if step.checkpoint_from
    }
    resolved_programs = {
        step.id: resolve_step_semantic_params(
            step, plan.definition, profile=ValidationProfile.RUNNABLE
        ).get("iprog")
        for step in plan.definition.steps
        if step.id and step.type == "calc"
    }
    steps: list[RuntimeStepV3] = []
    for planned in plan.steps:
        step_id = planned.id
        checkpoint_from = checkpoint_targets.get(step_id)
        checkpoint_dir = None
        if checkpoint_from is not None and planned.enabled:
            if checkpoint_from not in {s.id for s in plan.steps}:
                raise ConfFlowError(
                    f"step {step_id!r} declares a checkpoint from unknown step "
                    f"{checkpoint_from!r}"
                )
            checkpoint_dir = os.path.join(work_dir, "steps", checkpoint_from, "backups")
        steps.append(
            RuntimeStepV3(
                id=step_id,
                label=planned.label,
                type=planned.type,
                enabled=planned.enabled,
                inputs=planned.inputs,
                params=dict(raw_params.get(step_id, {})),
                step_dir=_step_dir(work_dir, step_id),
                checkpoint_from=checkpoint_from,
                checkpoint_dir=checkpoint_dir,
                program=resolved_programs.get(step_id),
            )
        )
    return WorkflowV3RuntimePlan(
        plan=plan,
        binding=binding,
        work_dir=work_dir,
        staged_inputs=tuple(staged_inputs),
        steps=tuple(steps),
    )


def compare_bindings_for_resume(
    stored: WorkflowBindingV2,
    current: WorkflowBindingV2,
    *,
    stored_input_digests: tuple[str, ...],
    current_input_digests: tuple[str, ...],
) -> BindingCompatibility:
    """Compare a stored run binding against the current one for resume."""
    return compare_workflow_binding_v2(
        stored,
        current,
        stored_input_digests=stored_input_digests,
        current_input_digests=current_input_digests,
    )


# ---------------------------------------------------------------------------
# Internal execution seam
# ---------------------------------------------------------------------------
def _raise_incompatible(compatibility: BindingCompatibility) -> None:
    details = "; ".join(str(error) for error in compatibility.errors)
    raise ConfFlowError(f"workflow binding mismatch; resume refused before any mutation: {details}")


def _validate_runtime_checkpoint(step: RuntimeStepV3, by_id: dict[str, RuntimeStepV3]) -> str:
    """Resolve and validate the checkpoint directory for one enabled calc step.

    The reference is the validated stable-ID ancestor; this boundary checks
    that the ancestor actually produced a checkpoint directory and that the
    program that will consume the checkpoint matches the program that
    produced it (chk files are program-specific — RFC §11 runtime layer).
    """
    assert step.checkpoint_dir is not None and step.checkpoint_from is not None
    ancestor = by_id.get(step.checkpoint_from)
    if ancestor is None:
        raise ConfFlowError(
            f"step {step.id!r} declares a checkpoint from unknown step {step.checkpoint_from!r}"
        )
    if ancestor.type != "calc":
        raise ConfFlowError(
            f"step {step.id!r} declares a checkpoint from {step.checkpoint_from!r}, "
            "which is not a calc step"
        )
    if not os.path.isdir(step.checkpoint_dir):
        raise ConfFlowError(
            f"checkpoint artifacts for step {step.id!r} are missing: "
            f"{step.checkpoint_dir} does not exist (ancestor {step.checkpoint_from!r} "
            "produced no checkpoint directory)"
        )
    if ancestor.program and step.program and ancestor.program != step.program:
        raise ConfFlowError(
            f"step {step.id!r} (program {step.program!r}) cannot reuse checkpoints "
            f"produced by {step.checkpoint_from!r} (program {ancestor.program!r}); "
            "checkpoint files are program-specific"
        )
    return step.checkpoint_dir


def run_v3_workflow(
    *,
    input_xyz: list[str],
    config_file: str,
    work_dir: str,
    resume: bool = False,
    verbose: bool = False,
    pause_beacon_file: str | None = None,
    cancel_beacon_file: str | None = None,
    step_started_callback: Callable[[str, str, str], None] | None = None,
    on_step_status_change: Callable[[StepRecordV2], None] | None = None,
    calc_executor: Any = None,
    provenance: Any = None,
) -> dict[str, Any]:
    """Execute one Workflow-V3 run — the internal, capability-gated seam.

    This is the R4.3/R4.4 execution entry used by tests and, from R4.5, by the
    engine's V3 branch after the capability flip. It is NOT reachable through
    the public CLI/service/worker paths while ``CAPABILITIES[V3].execute`` is
    False, and this function never flips the table.

    Sequence (frozen): plan → resolve execution context → A/B/C binding →
    only then create directories, stage inputs and mutate state. Resume adds:
    load state v2 → validate → build the current binding → compare → only
    after compatibility may anything be staged or mutated.
    """
    del verbose  # the internal seam keeps output quiet; presenters are R4.5
    plan = build_workflow_plan(input_xyz, config_file)
    if not isinstance(plan, WorkflowV3Plan):
        raise ConfFlowError(
            "the V3 runtime seam executes Workflow V3 documents only; use the "
            "V2 engine for legacy configurations"
        )

    # Execution-critical effective-dataflow preflight (minimal R6): a
    # known-invalid workflow is rejected here — before any context
    # resolution, directory creation, state mutation or handler invocation.
    # Pure function; zero side effects.
    dataflow_issues = validate_effective_dataflow_v3(
        plan, external_input_count=len(plan.input_files)
    )
    if dataflow_issues:
        details = "; ".join(str(issue) for issue in dataflow_issues)
        raise ConfFlowError(f"effective dataflow validation failed: {details}")

    context = resolve_execution_context_v3(plan, input_files=plan.input_files)
    from .binding_v2 import build_workflow_binding_v2

    binding = build_workflow_binding_v2(plan, context, provenance=provenance)

    store = WorkflowStateV2Store(work_dir)
    if resume:
        try:
            stored = store.load()
        except WorkflowStateCompatibilityError as exc:
            # A cross-version or corrupt state file is refused before anything
            # else happens — never migrated, never overwritten.
            raise ConfFlowError(f"cannot resume: {exc}") from exc
        if stored is None:
            raise ConfFlowError(
                f"no workflow state found in {work_dir}; a V3 resume requires " "workflow_state.v2"
            )
        compatibility = compare_bindings_for_resume(
            stored.binding,
            binding,
            stored_input_digests=tuple(stored.input_digests),
            current_input_digests=context.input_digests,
        )
        if not compatibility.compatible:
            _raise_incompatible(compatibility)
        for warning in compatibility.warnings:
            logger.warning("Workflow V3 resume: %s", warning)
        state = stored
        if stored.execution_order != list(plan.graph.topological_order):
            raise ConfFlowError("workflow state execution order does not match the validated graph")
        # Refresh display label snapshots from the current document: labels
        # are presentation metadata and never identity, so a rename drifts
        # harmlessly into the state.
        for planned_step in plan.steps:
            if stored.steps[planned_step.id].label != planned_step.label:
                stored.update_step(planned_step.id, label=planned_step.label)
    else:
        state = build_initial_state_v2(
            plan,
            run_id=str(uuid.uuid4()),
            work_dir=work_dir,
            config_file=os.path.abspath(config_file),
            binding=binding,
            input_files=list(plan.input_files),
            original_inputs=list(plan.original_inputs),
            input_digests=list(context.input_digests),
        )

    # Binding established/validated — only now may runtime directories exist.
    os.makedirs(work_dir, exist_ok=True)
    staged = stage_v3_external_inputs(
        work_dir, list(plan.input_files), expected_digests=context.input_digests
    )
    runtime = project_v3_runtime_plan(
        plan, context, binding, work_dir=work_dir, staged_inputs=staged
    )

    if not resume:
        store.save(state)
    else:
        state.execution_order = list(plan.graph.topological_order)

    by_id = {step.id: step for step in runtime.steps}
    failure_tracker = FailureTracker(os.path.join(work_dir, "failed"))
    if not resume:
        failure_tracker.clear_previous()
    checkpoint = CheckpointManager(work_dir)

    # Single bypass authority (minimal R6): runtime input resolution consumes
    # the same effective-source resolver the preflight validated — never a
    # second bypass algorithm.
    effective_sources = resolve_effective_sources_v3(
        plan, external_input_count=len(runtime.staged_inputs)
    )
    outputs: dict[str, str | list[str]] = {}

    def _resolve_inputs(step: RuntimeStepV3) -> str | list[str]:
        return materialize_effective_inputs(
            step.id,
            effective_sources,
            staged_inputs=runtime.staged_inputs,
            step_outputs=outputs,
        )

    def _notify(record: StepRecordV2) -> None:
        if on_step_status_change is not None:
            on_step_status_change(record)

    for execution_index, step in enumerate(runtime.steps):
        # Cancellation wins over pausing, at step boundaries (V1 semantics).
        if cancel_beacon_file and os.path.exists(cancel_beacon_file):
            raise StopRequestedError(f"Cancel beacon found at {cancel_beacon_file}")
        if pause_beacon_file and os.path.exists(pause_beacon_file):
            raise StopRequestedError(f"Pause beacon found at {pause_beacon_file}")

        record = state.step(step.id)
        if resume and record.status == "completed":
            # A completed step is reused only while its recorded output still
            # exists; a missing artifact fails closed instead of silently
            # recomputing or fabricating a result.
            output = record.output_xyz
            if not output or not os.path.isfile(output):
                raise ConfFlowError(
                    f"workflow state marks step {step.id!r} completed but its output "
                    f"artifact is missing: {output!r}; remove the state or restore "
                    "the artifact"
                )
            outputs[step.id] = output
            continue
        if resume and record.status == "skipped":
            outputs[step.id] = _resolve_inputs(step)
            continue
        # Any other persisted status (pending / submitted / failed) is
        # re-executed: an interrupted "submitted" step is retried, never
        # trusted as completed.

        inputs_for_step = _resolve_inputs(step)
        if not step.enabled:
            # Pass-through bypass: the step produces nothing and forwards its
            # effective input. Unsupported effective cardinality is R6's
            # static analysis, but the runtime still refuses to guess here.
            outputs[step.id] = inputs_for_step
            state.update_step(step.id, status="skipped", error=None)
            store.save(state)
            _notify(state.step(step.id))
            continue

        if step.checkpoint_dir is not None:
            _validate_runtime_checkpoint(step, by_id)

        os.makedirs(step.step_dir, exist_ok=True)
        state.update_step(
            step.id,
            status="submitted",
            submitted_at=time.time(),
            error=None,
        )
        state.wavefront_index = execution_index
        store.save(state)
        _notify(state.step(step.id))
        if step_started_callback is not None:
            # Internal event identity is the stable step ID; the label never
            # substitutes for it (service wire adaptation is R4.5).
            step_started_callback(step.id, step.type, step.step_dir)

        try:
            if step.type == "calc" and not isinstance(inputs_for_step, str):
                if len(inputs_for_step) != 1:
                    raise ConfFlowError(
                        f"step {step.id!r} is a calc step receiving "
                        f"{len(inputs_for_step)} effective inputs after disabled-step "
                        "bypass; unsupported effective cardinality — add a confgen "
                        "step to merge them (static analysis: R6)"
                    )
            if step.type == "confgen":
                result = _run_confgen_step(
                    step_dir=step.step_dir,
                    current_input=inputs_for_step,
                    params=step.params,
                    input_files=list(runtime.staged_inputs),
                    global_config=plan.definition.global_config,
                )
            elif step.type == "calc":
                result = _run_calc_step(
                    step_dir=step.step_dir,
                    current_input=inputs_for_step,
                    params=step.params,
                    global_config=plan.definition.global_config,
                    root_dir=work_dir,
                    steps=[],
                    failure_tracker=failure_tracker,
                    step_name=step.id,
                    typed_global=plan.definition.global_options,
                    calc_executor=calc_executor,
                    cancel_beacon_file=cancel_beacon_file,
                    input_chk_dir=step.checkpoint_dir,
                )
            else:
                raise ConfFlowError(f"step {step.id!r} has unsupported type {step.type!r}")
            output = result.output_path
        except Exception as exc:
            state.update_step(
                step.id,
                status="failed",
                error=str(exc),
                completed_at=time.time(),
                fail_count=state.step(step.id).fail_count + 1,
            )
            state.final_status = "failed"
            store.save(state)
            _notify(state.step(step.id))
            checkpoint.save(execution_index - 1, {"steps": []})
            raise

        outputs[step.id] = output
        state.update_step(
            step.id,
            status="completed",
            completed_at=time.time(),
            output_xyz=os.path.abspath(output) if isinstance(output, str) else None,
            error=None,
        )
        store.save(state)
        _notify(state.step(step.id))
        checkpoint.save(execution_index, {"steps": []})

    state.final_status = "completed"
    store.save(state)
    result = finalize_workflow_v3(
        work_dir=work_dir,
        plan=plan,
        state=state,
        outputs=dict(outputs),
        staged_inputs=runtime.staged_inputs,
        binding=binding,
        logger=logger,
    )
    return {
        "run_id": state.run_id,
        **result,
        "definition_fingerprint": binding.definition_fingerprint,
        "execution_fingerprint": binding.execution_fingerprint,
    }
