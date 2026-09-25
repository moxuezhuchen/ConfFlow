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

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .plan import WorkflowV3Plan

__all__ = [
    "EffectiveDataflowIssue",
    "EffectiveSource",
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
    effective sources. Roots consume the external input slots. Multiplicity
    is preserved exactly (a source referenced through two bypass paths is
    consumed twice, byte-identical to the runtime's forwarding behavior).

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
        sources[step.id] = tuple(resolved)
    return sources


def validate_effective_dataflow_v3(
    plan: WorkflowV3Plan, *, external_input_count: int
) -> tuple[EffectiveDataflowIssue, ...]:
    """Report the execution-critical effective-dataflow errors.

    Checks exactly the limitations the current step handlers enforce at
    runtime — nothing more:

    - an enabled ``calc`` step consuming more than one effective input
      (single-input by design);
    - any step whose effective source set is empty (a handler always requires
      input; an empty bypass result would poison every successor).

    Pure and side-effect free; runs before any runtime directory, state
    mutation or handler invocation.
    """
    sources = resolve_effective_sources_v3(plan, external_input_count=external_input_count)
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
    return tuple(issues)
