#!/usr/bin/env python3

"""Typed, strict resume decisions for workflow execution."""

from __future__ import annotations

import os
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Literal

from ..calc.artifacts import CalcArtifactManager
from ..config.models import CalcStepParams, GlobalOptions
from ..contract import WORKFLOW_STATE_SCHEMA
from .helpers import resolve_step_output
from .state import StepRecord, WorkflowState, WorkflowStateStore

__all__ = [
    "ResumeDecision",
    "ResumeDiagnostic",
    "ResumePolicy",
    "WORKFLOW_STATE_VERSION",
    "create_initial_workflow_state",
    "format_resume_failure",
    "validate_state_steps",
]


WORKFLOW_STATE_VERSION = 1
ResumeAction = Literal["execute", "reuse", "skip"]
StepOutput = str | list[str]


@dataclass(frozen=True)
class ResumeDiagnostic:
    """Evidence attached to a strict resume decision or rejection."""

    step_index: int
    step_name: str
    step_dir: str
    reason: str
    checkpoint_index: int = -1
    state_schema: str = WORKFLOW_STATE_SCHEMA
    state_version: int = WORKFLOW_STATE_VERSION

    @property
    def checkpoint(self) -> int:
        """Return the legacy checkpoint index under a short compatibility name."""
        return self.checkpoint_index

    def failure_message(self) -> str:
        """Render the established user-facing strict-resume error."""
        return format_resume_failure(
            step_index=self.step_index,
            step_name=self.step_name,
            step_dir=self.step_dir,
            reason=self.reason,
        )


@dataclass(frozen=True)
class ResumeDecision:
    """Decision made before a step is dispatched."""

    action: ResumeAction
    output: StepOutput | None = None
    diagnostics: ResumeDiagnostic | None = None

    @property
    def reused(self) -> bool:
        """Whether the decision reuses a completed output."""
        return self.action == "reuse"


def format_resume_failure(*, step_index: int, step_name: str, step_dir: str, reason: str) -> str:
    """Return the established strict-resume failure text."""
    return (
        f"Resume failed: step {step_index} ('{step_name}') cannot be reused: {reason}. "
        "Strict resume does not automatically re-run stale or incomplete steps. "
        f"Next action: back up or remove {step_dir}, then run again without --resume "
        "if recomputing this step is intended."
    )


def expected_output_reason(step_type: str | None) -> str:
    """Describe the output required for reuse of a legacy step directory."""
    normalized = (step_type or "").lower()
    if normalized in {"calc", "task"}:
        return "missing expected output file output.xyz or result.xyz"
    if normalized in {"confgen", "gen"}:
        return "missing expected output file search.xyz"
    return "missing expected step output"


def create_initial_workflow_state(
    *,
    root_dir: str,
    input_files: list[str],
    original_inputs: list[str],
    config_file: str,
    steps: list[dict[str, Any]],
    step_dirnames: list[str],
) -> WorkflowState:
    """Create state records keyed by deterministic step directory names."""
    records = {
        dirname: StepRecord(
            name=str(step.get("name", dirname)),
            type=str(step.get("type", "")),
            status="skipped" if not step.get("enabled", True) else "pending",
        )
        for dirname, step in zip(step_dirnames, steps, strict=True)
    }
    return WorkflowState(
        run_id=str(uuid.uuid4()),
        work_dir=root_dir,
        input_files=input_files,
        original_inputs=original_inputs,
        config_file=os.path.abspath(config_file),
        steps=records,
    )


def validate_state_steps(
    state: WorkflowState,
    steps: list[dict[str, Any]],
    step_dirnames: list[str],
) -> None:
    """Reject resume when saved step names/types differ from the plan."""
    expected = set(step_dirnames)
    if set(state.steps) != expected:
        raise RuntimeError("Workflow state does not match the configured workflow steps")
    for dirname, step in zip(step_dirnames, steps, strict=True):
        record = state.steps[dirname]
        if record.name != str(step.get("name", dirname)) or record.type != str(
            step.get("type", "")
        ):
            raise RuntimeError("Workflow state does not match the configured workflow steps")


class ResumePolicy:
    """Make strict, side-effect-free decisions about reuse before dispatch."""

    def __init__(
        self,
        *,
        resume: bool,
        resume_from_step: int,
        root_dir: str,
        global_config: dict[str, Any],
        state: WorkflowState | None,
        steps: list[dict[str, Any]],
        step_dirnames: list[str],
    ) -> None:
        self.resume = resume
        self.resume_from_step = resume_from_step
        self.root_dir = root_dir
        self.global_config = global_config
        self.state = state
        self.state_schema = WORKFLOW_STATE_SCHEMA
        self.state_version = WORKFLOW_STATE_VERSION
        if resume and state is not None:
            validate_state_steps(state, steps, step_dirnames)

    def decide(
        self,
        *,
        step_index: int,
        step_name: str,
        step: dict[str, Any],
        step_dir: str,
        state_record: StepRecord,
        resolve_inputs: Callable[[str], StepOutput],
        current_input: StepOutput,
    ) -> ResumeDecision:
        """Return execute/reuse/skip, rejecting stale or incomplete resume data."""
        if self.resume and state_record.status in {"completed", "skipped"}:
            if state_record.output_xyz is None:
                if state_record.status == "skipped" and not step.get("enabled", True):
                    return ResumeDecision(
                        action="skip",
                        output=self._safe_resolve(resolve_inputs, step_name, current_input),
                    )
                return self._reject(
                    step_index=step_index,
                    step_name=state_record.name,
                    step_dir=step_dir,
                    reason="workflow state has no output path",
                )
            if not os.path.exists(state_record.output_xyz):
                return self._reject(
                    step_index=step_index,
                    step_name=state_record.name,
                    step_dir=step_dir,
                    reason=f"saved output is missing: {state_record.output_xyz}",
                )
            return ResumeDecision(action="reuse", output=state_record.output_xyz)

        if self.resume_from_step < step_index:
            return ResumeDecision(action="execute")

        if not step.get("enabled", True):
            return ResumeDecision(
                action="skip",
                output=self._safe_resolve(resolve_inputs, step_name, current_input),
            )

        inputs_for_step = resolve_inputs(step_name)
        if step.get("type") in ["calc", "task"]:
            params = step.get("params", {}) or {}
            typed_global = GlobalOptions.from_mapping(self.global_config)
            calc_config = CalcStepParams.from_params(params, typed_global)
            input_for_digest = (
                inputs_for_step if isinstance(inputs_for_step, str) else inputs_for_step[0]
            )
            artifact_prepared = CalcArtifactManager(
                step_dir,
                step_name=step_name,
                config=calc_config,
                input_path=input_for_digest,
            ).prepare(resume=True)
            if artifact_prepared.reusable_output is not None:
                return ResumeDecision(
                    action="reuse",
                    output=str(artifact_prepared.reusable_output),
                )
            if artifact_prepared.cleaned_stale_artifacts:
                return self._reject(
                    step_index=step_index,
                    step_name=step_name,
                    step_dir=step_dir,
                    reason="manifest digest did not match current config/input",
                )

        expected_output = resolve_step_output(step_dir, step.get("type"))
        if expected_output is not None and os.path.exists(expected_output):
            return ResumeDecision(action="reuse", output=expected_output)
        return self._reject(
            step_index=step_index,
            step_name=step_name,
            step_dir=step_dir,
            reason=expected_output_reason(step.get("type")),
        )

    @staticmethod
    def _safe_resolve(
        resolve_inputs: Callable[[str], StepOutput],
        step_name: str,
        current_input: StepOutput,
    ) -> StepOutput:
        try:
            return resolve_inputs(step_name)
        except RuntimeError:
            return current_input

    def _reject(
        self,
        *,
        step_index: int,
        step_name: str,
        step_dir: str,
        reason: str,
    ) -> ResumeDecision:
        diagnostic = ResumeDiagnostic(
            step_index=step_index + 1,
            step_name=step_name,
            step_dir=step_dir,
            reason=reason,
            checkpoint_index=self.resume_from_step,
            state_schema=self.state_schema,
            state_version=self.state_version,
        )
        raise RuntimeError(diagnostic.failure_message())


def load_resume_state(store: WorkflowStateStore, *, resume: bool) -> WorkflowState | None:
    """Load state only for a requested resume, preserving fresh-run behavior."""
    return store.load() if resume else None
