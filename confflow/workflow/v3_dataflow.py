"""Execution-critical effective dataflow for Workflow V3 (minimal R6).

Disabled steps are pass-through (frozen RFC §12 semantics): a bypassed step
never executes, and its successors see the *effective* upstream sources —
recursively resolved through every disabled hop. This module is the **single
semantic authority** for that resolution:

- :func:`resolve_effective_sources_v3` — the pure resolver both the R6
  preflight and the R4.3 runtime consume (one bypass algorithm, never two);
- :func:`validate_effective_dataflow_v3` — the pure preflight that reports the
  execution-critical cardinality errors the current step handlers cannot
  accept, so a known-invalid workflow is rejected before any runtime directory
  is created, any state is mutated and any handler is invoked.

Identity is exclusively the stable step ID (labels, array indexes, dirnames
and source basenames never participate). This is deliberately *not* the full
R6 typed-port/artifact-algebra system — only what the current executor
actually refuses at runtime, moved earlier.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from ..core.exceptions import ConfFlowError

if TYPE_CHECKING:
    from .plan import WorkflowV3Plan

__all__ = [
    "EffectiveDataflowIssue",
    "EffectiveSource",
    "materialize_effective_inputs",
    "resolve_effective_sources_v3",
    "validate_effective_dataflow_v3",
]

#: One effective input source: ``("external", slot)`` — the 1-based external
#: input slot — or ``("step", step_id)`` — the output of an enabled step.
EffectiveSource = tuple[str, Any]


@dataclass(frozen=True)
class EffectiveDataflowIssue:
    """One structured effective-dataflow diagnostic."""

    code: str
    step_id: str | None
    message: str

    def __str__(self) -> str:  # pragma: no cover - trivial display form
        target = f"step {self.step_id!r}: " if self.step_id else ""
        return f"{self.code}: {target}{self.message}"


def resolve_effective_sources_v3(
    plan: WorkflowV3Plan, *, external_input_count: int
) -> dict[str, tuple[EffectiveSource, ...]]:
    """Resolve every step's effective input sources after disabled bypass.

    The frozen semantics: a disabled step forwards its own effective inputs
    unchanged, so each declared input of a step contributes either the
    enabled predecessor itself or — recursively — the disabled predecessor's
    effective sources. Roots consume the external input slots. Identical
    effective sources arising from branching or bypass convergence are
    deduplicated while preserving the order of first occurrence.

    Pure function over the validated plan; stable IDs only.
    """
    enabled_by_id = {step.id: step.enabled for step in plan.steps}
    sources: dict[str, tuple[EffectiveSource, ...]] = {}
    for step in plan.steps:
        if not step.inputs:
            sources[step.id] = tuple(
                ("external", slot) for slot in range(1, external_input_count + 1)
            )
            continue
        resolved: list[EffectiveSource] = []
        for predecessor in step.inputs:
            if enabled_by_id[predecessor]:
                resolved.append(("step", predecessor))
            else:
                resolved.extend(sources[predecessor])
        # Deduplicate identical effective sources while preserving order of first occurrence
        deduped: list[EffectiveSource] = []
        seen: set[EffectiveSource] = set()
        for src in resolved:
            if src not in seen:
                seen.add(src)
                deduped.append(src)
        sources[step.id] = tuple(deduped)
    return sources


def materialize_effective_inputs(
    step_id: str,
    sources: Mapping[str, tuple[EffectiveSource, ...]],
    *,
    staged_inputs: Sequence[str],
    step_outputs: Mapping[str, str | list[str] | None],
) -> str | list[str]:
    """Map one step's effective sources onto concrete input paths.

    The single mapping layer over :func:`resolve_effective_sources_v3`: an
    external slot resolves to its staged slot path, a step source to that
    step's recorded output (forwarded lists are flattened). Shared by the
    runtime input resolution and finalization so the bypass algorithm and its
    mapping can never drift apart.
    """
    resolved: list[str] = []
    for kind, value in sources[step_id]:
        if kind == "external":
            resolved.append(staged_inputs[int(value) - 1])
            continue
        output = step_outputs.get(value)
        if output is None:
            raise ConfFlowError(f"step {step_id!r} depends on {value!r}, which produced no output")
        if isinstance(output, list):
            resolved.extend(output)
        else:
            resolved.append(output)
    # Deduplicate concrete paths if multiple paths resolved to the same file
    deduped: list[str] = []
    seen: set[str] = set()
    for path in resolved:
        if path not in seen:
            seen.add(path)
            deduped.append(path)
    return deduped[0] if len(deduped) == 1 else deduped


def validate_effective_dataflow_v3(
    plan: WorkflowV3Plan, *, external_input_count: int
) -> tuple[EffectiveDataflowIssue, ...]:
    """Report execution-critical effective-dataflow and artifact capability errors.

    Checks:
    - any step whose effective source set is empty (handler requires input);
    - enabled calc steps consuming more than one effective input (calc consumes
      exactly 1 effective geometry; confgen consumes 1 or more);
    - root step external input cardinality against step type capabilities;
    - checkpoint artifact capabilities statically before execution: ancestor step
      must be capable of producing the required checkpoint type (calc step,
      enabled, compatible program).

    Pure and side-effect free; runs before any runtime directory, state
    mutation or handler invocation.
    """
    sources = resolve_effective_sources_v3(plan, external_input_count=external_input_count)
    by_id = {step.id: step for step in plan.steps}
    issues: list[EffectiveDataflowIssue] = []
    for step in plan.steps:
        effective = sources[step.id]
        if not effective:
            if step.enabled:
                issues.append(
                    EffectiveDataflowIssue(
                        code="zero_effective_input",
                        step_id=step.id,
                        message=(
                            "step has no effective input after disabled-step bypass; "
                            "every handler requires at least one input"
                        ),
                    )
                )
            else:
                issues.append(
                    EffectiveDataflowIssue(
                        code="invalid_bypass_result",
                        step_id=step.id,
                        message=(
                            "disabled step resolves to an empty effective input set; "
                            "its successors would receive no input"
                        ),
                    )
                )
            continue
        if step.enabled and step.type == "calc" and len(effective) > 1:
            if not step.inputs:
                issues.append(
                    EffectiveDataflowIssue(
                        code="effective_cardinality_unsupported",
                        step_id=step.id,
                        message=(
                            f"calc root step receives {len(effective)} external inputs; "
                            "calc steps consume exactly 1 geometry — add a confgen step to merge them"
                        ),
                    )
                )
            else:
                issues.append(
                    EffectiveDataflowIssue(
                        code="effective_cardinality_unsupported",
                        step_id=step.id,
                        message=(
                            f"calc step receives {len(effective)} effective inputs after "
                            "disabled-step bypass; unsupported effective cardinality — "
                            "add a confgen step to merge them"
                        ),
                    )
                )

        if step.enabled and step.checkpoint_from is not None:
            target_id = step.checkpoint_from
            ancestor = by_id.get(target_id)
            if ancestor is None:
                issues.append(
                    EffectiveDataflowIssue(
                        code="checkpoint_capability_unsupported",
                        step_id=step.id,
                        message=f"step {step.id!r} declares a checkpoint from unknown step {target_id!r}",
                    )
                )
            elif not ancestor.enabled:
                issues.append(
                    EffectiveDataflowIssue(
                        code="checkpoint_capability_unsupported",
                        step_id=step.id,
                        message=(
                            f"step {step.id!r} declares a checkpoint from {target_id!r}, "
                            "which is disabled and produces no checkpoint artifact"
                        ),
                    )
                )
            elif ancestor.type != "calc":
                issues.append(
                    EffectiveDataflowIssue(
                        code="checkpoint_capability_unsupported",
                        step_id=step.id,
                        message=(
                            f"step {step.id!r} declares a checkpoint from {target_id!r}, "
                            f"which is a {ancestor.type} step; only calc steps produce "
                            "checkpoint artifacts"
                        ),
                    )
                )
            else:
                step_prog = step.params.get("iprog")
                anc_prog = ancestor.params.get("iprog")
                if step_prog and anc_prog and step_prog != anc_prog:
                    issues.append(
                        EffectiveDataflowIssue(
                            code="checkpoint_capability_unsupported",
                            step_id=step.id,
                            message=(
                                f"step {step.id!r} (program {step_prog!r}) cannot reuse "
                                f"checkpoints produced by {target_id!r} (program {anc_prog!r}); "
                                "checkpoint files are program-specific"
                            ),
                        )
                    )
    return tuple(issues)
