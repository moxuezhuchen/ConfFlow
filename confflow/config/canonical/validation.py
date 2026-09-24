#!/usr/bin/env python3

"""Canonical workflow validation.

Two scopes are kept deliberately distinct, because one function cannot honestly
answer both:

``validate_workflow_definition``
    Validation that depends on the workflow **document alone**: syntax/schema,
    step types, step parameter semantics, duplicate names, unknown
    predecessors, dependency cycles, calc fan-in, calc program/task/keyword,
    confgen runnable parameters (non-empty ``chains``, positive ``angle_step``)
    and ``chk_from_step`` references. It never touches the filesystem or the
    run context.

    This is *runnable-definition* validation. A parsed document is not
    necessarily runnable: a producer recipe is a legal partial fragment whose
    user-filled required fields are still missing, so
    :func:`confflow.config.canonical.parser.parse_workflow_mapping` accepts it
    while this function rejects it until those fields are supplied. Parseability
    and runnability are deliberately distinct.

``validate_workflow_run_context``
    Validation that additionally needs the **run context**: how many input
    files the caller supplied. A root calc step is fine with exactly one input
    and rejected with several; that is a fact about the invocation, not the
    document, so ``config validate --stdin`` cannot and must not claim to check
    it.

The canonical resolvers (``resolve_calc_step`` / ``resolve_confgen_params``) own
the semantic rules; this module only collects their verdicts into
:class:`~confflow.config.canonical.diagnostics.Diagnostic` values with stable
codes.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping
from typing import Any

from ...core.exceptions import ConfFlowError, ConfigurationError
from ...shared.confgen_params import resolve_confgen_params
from .diagnostics import Diagnostic
from .issues import ConfigValidationError
from .resolve import resolve_calc_step, resolve_global_options
from .types import WorkflowConfig
from .workflow import (
    CanonicalWorkflowDefinition,
    canonical_step_name,
    normalize_step_inputs,
    topo_order,
)

__all__ = [
    "calc_input_diagnostics",
    "validate_workflow_definition",
    "validate_workflow_run_context",
]

_STEP_TYPE_ALIASES = {"gen": "confgen", "task": "calc"}
_CANONICAL_STEP_TYPES = {"confgen", "calc"}

# The canonical calc resolver raises one ValueError whose message we map to a
# stable code. Tests pin the mapping so the two cannot drift apart silently.
_CALC_CODE_BY_PREFIX = (
    ("Unsupported calc program", "calc.invalid_program"),
    ("Unsupported calc task", "calc.invalid_task"),
)
_CALC_MISSING_KEYWORD = "calc.missing_keyword"


def _bool_step_name_message(value: bool, index: int) -> str:
    return (
        f"step {index}: name {value!r} was parsed as a boolean. "
        "YAML interprets unquoted on/off/yes/no/true/false as booleans; "
        'quote the step name, e.g. name: "off"'
    )


def _structural_diagnostics(raw: Mapping[str, Any]) -> list[Diagnostic]:
    """Collect every document-only structural problem, tolerant of the rest."""
    diagnostics: list[Diagnostic] = []

    if "global" in raw and not isinstance(raw["global"], Mapping):
        diagnostics.append(
            Diagnostic(
                "workflow.global_not_mapping", "error", "global", "global config must be a mapping"
            )
        )
    else:
        try:
            resolve_global_options(raw.get("global"))
        except ConfigValidationError as exc:
            diagnostics.append(
                Diagnostic(
                    "global.invalid",
                    "error",
                    exc.issue.path or "global",
                    exc.issue.message,
                )
            )

    steps = raw.get("steps")
    if steps is not None and not isinstance(steps, list):
        diagnostics.append(
            Diagnostic(
                "workflow.steps_not_list",
                "error",
                "steps",
                "workflow config 'steps' must be a list",
            )
        )
        return diagnostics

    if not isinstance(steps, list):
        return diagnostics

    canonical_names: list[str] = []
    declared_inputs: list[list[str]] = []

    for index, step in enumerate(steps, start=1):
        path = f"steps[{index}]"
        if not isinstance(step, Mapping):
            diagnostics.append(
                Diagnostic("workflow.step_not_mapping", "error", path, "step must be a mapping")
            )
            continue

        raw_type = step.get("type")
        raw_type_text = "" if raw_type is None else str(raw_type).strip().lower()
        step_type = _STEP_TYPE_ALIASES.get(raw_type_text, raw_type_text)
        if step_type not in _CANONICAL_STEP_TYPES:
            diagnostics.append(
                Diagnostic(
                    "workflow.step_type_invalid",
                    "error",
                    f"{path}.type",
                    f"step {index} has unsupported type: {raw_type_text!r}",
                )
            )

        raw_name = step.get("name")
        if isinstance(raw_name, bool):
            diagnostics.append(
                Diagnostic(
                    "workflow.step_name_boolean",
                    "error",
                    f"{path}.name",
                    _bool_step_name_message(raw_name, index),
                )
            )
            exec_name = f"{step_type or 'step'}_{index}"
        else:
            exec_name = str(raw_name) if raw_name else f"{step_type or 'step'}_{index}"

        if (
            "params" in step
            and step["params"] is not None
            and not isinstance(step["params"], Mapping)
        ):
            diagnostics.append(
                Diagnostic(
                    "workflow.step_params_not_mapping",
                    "error",
                    f"{path}.params",
                    "step params must be a mapping",
                )
            )

        try:
            declared = normalize_step_inputs(step.get("inputs"))
        except ConfFlowError as exc:
            diagnostics.append(
                Diagnostic(
                    "workflow.step_inputs_boolean",
                    "error",
                    f"{path}.inputs",
                    str(exc),
                    exec_name,
                )
            )
            declared = []

        canonical_names.append(canonical_step_name({"name": exec_name}, index))
        declared_inputs.append(declared)

    counts = Counter(canonical_names)
    for name, count in counts.items():
        if count > 1:
            diagnostics.append(
                Diagnostic(
                    "workflow.duplicate_step_name",
                    "error",
                    "",
                    f"workflow step names must be unique; duplicate name: {name!r}",
                    name,
                )
            )

    known = set(canonical_names)
    predecessors: dict[str, list[str]] = {}
    for name, declared in zip(canonical_names, declared_inputs, strict=True):
        unknown = [predecessor for predecessor in declared if predecessor not in known]
        if unknown:
            diagnostics.append(
                Diagnostic(
                    "workflow.unknown_predecessor",
                    "error",
                    "",
                    f"workflow step {name!r} has unknown predecessor(s): "
                    f"{', '.join(map(repr, unknown))}",
                    name,
                )
            )
        predecessors.setdefault(name, list(declared))

    if len(counts) == len(canonical_names) and not any(
        diagnostic.code == "workflow.unknown_predecessor" for diagnostic in diagnostics
    ):
        try:
            topo_order(predecessors)
        except ConfFlowError as exc:
            diagnostics.append(Diagnostic("workflow.dependency_cycle", "error", "", str(exc)))

    return diagnostics


def _calc_semantic_diagnostics(
    definition: CanonicalWorkflowDefinition,
) -> list[Diagnostic]:
    diagnostics: list[Diagnostic] = []
    for index, step in enumerate(definition.steps, start=1):
        if step.type != "calc":
            continue
        try:
            resolve_calc_step(step.params, definition.global_options)
        except ConfigValidationError as exc:
            message = exc.issue.message
            code = "calc.invalid"
            for prefix, candidate in _CALC_CODE_BY_PREFIX:
                if message.startswith(prefix):
                    code = candidate
                    break
            else:
                if "non-empty keyword" in message:
                    code = _CALC_MISSING_KEYWORD
            diagnostics.append(
                Diagnostic(code, "error", f"steps[{index}].params", message, step.name)
            )
    return diagnostics


def _confgen_semantic_diagnostics(
    definition: CanonicalWorkflowDefinition,
) -> list[Diagnostic]:
    diagnostics: list[Diagnostic] = []
    for index, step in enumerate(definition.steps, start=1):
        if step.type != "confgen":
            continue
        try:
            resolved = resolve_confgen_params(
                step.params, default_workers=definition.global_options.max_parallel_jobs
            )
        except (ConfigurationError, ValueError) as exc:
            diagnostics.append(
                Diagnostic(
                    "confgen.invalid", "error", f"steps[{index}].params", str(exc), step.name
                )
            )
            continue

        # Runnable-definition invariants, checked on the *resolved* params (so
        # the `chain` alias counts) rather than on a second copy of the rules.
        # They mirror the generator's execution preconditions: it refuses to run
        # without chains and builds ``range(0, 360, int(angle_step))``.
        chains = resolved.get("chains")
        if not chains or not any(str(chain).strip() for chain in chains):
            diagnostics.append(
                Diagnostic(
                    "confgen.chains.required",
                    "error",
                    f"steps[{index}].params.chains",
                    "confgen step requires non-empty 'chains' (or its alias 'chain') naming "
                    "the bonds to rotate",
                    step.name,
                )
            )
        angle_step = resolved.get("angle_step")
        if not isinstance(angle_step, int) or angle_step <= 0:
            diagnostics.append(
                Diagnostic(
                    "confgen.angle_step.invalid",
                    "error",
                    f"steps[{index}].params.angle_step",
                    f"confgen angle_step must be a positive integer, got {angle_step!r}",
                    step.name,
                )
            )
    return diagnostics


def _chk_from_step_diagnostics(
    definition: CanonicalWorkflowDefinition,
) -> list[Diagnostic]:
    names = {step.v2_name.strip() for step in definition.steps if step.v2_name.strip()}
    step_count = len(definition.steps)
    diagnostics: list[Diagnostic] = []
    for index, step in enumerate(definition.steps, start=1):
        raw = step.params.get("chk_from_step")
        if not raw:
            continue
        text = str(raw).strip()
        resolved = (text.isdigit() and 1 <= int(text) <= step_count) or (
            not text.isdigit() and text in names
        )
        if not resolved:
            diagnostics.append(
                Diagnostic(
                    "workflow.chk_from_step_unknown",
                    "error",
                    f"steps[{index}].params.chk_from_step",
                    f"step {step.name!r} references an unknown chk_from_step: {text!r}",
                    step.name,
                )
            )
    return diagnostics


def calc_input_diagnostics(
    definition: CanonicalWorkflowDefinition,
    *,
    input_file_count: int | None = None,
) -> list[Diagnostic]:
    """Report calc single-input violations.

    ``input_file_count`` selects the scope: ``None`` checks only the graph
    (a calc step with several predecessors is a definition error); an integer
    additionally checks a root calc step against the number of external input
    files, which is a run-context fact. Document order is preserved and, per
    step, the fan-in rule is reported before the external-input rule, matching
    the planner's historical precedence.
    """
    diagnostics: list[Diagnostic] = []
    for index, step in enumerate(definition.steps, start=1):
        if step.type != "calc" or not step.enabled:
            continue
        predecessors = step.predecessors
        if len(predecessors) > 1:
            diagnostics.append(
                Diagnostic(
                    "workflow.calc_fan_in",
                    "error",
                    f"steps[{index}]",
                    f"calc step {step.name!r} has {len(predecessors)} inputs; "
                    "a calc step accepts exactly one input. Add a confgen step to "
                    "merge them first.",
                    step.name,
                )
            )
            continue
        if not predecessors and input_file_count is not None and input_file_count > 1:
            diagnostics.append(
                Diagnostic(
                    "workflow.input_cardinality",
                    "error",
                    f"steps[{index}]",
                    f"calc step {step.name!r} has no inputs but the workflow provides "
                    f"{input_file_count} initial inputs; a calc step accepts exactly "
                    "one input. Add a confgen step to merge them first.",
                    step.name,
                )
            )
    return diagnostics


def _semantic_diagnostics(definition: CanonicalWorkflowDefinition) -> list[Diagnostic]:
    return [
        *calc_input_diagnostics(definition),
        *_calc_semantic_diagnostics(definition),
        *_confgen_semantic_diagnostics(definition),
        *_chk_from_step_diagnostics(definition),
    ]


def validate_workflow_definition(raw: Mapping[str, Any]) -> list[Diagnostic]:
    """Validate everything derivable from the workflow document alone."""
    if not isinstance(raw, Mapping):
        return [
            Diagnostic(
                "workflow.root_not_mapping", "error", "", "workflow config root must be a mapping"
            )
        ]

    diagnostics = _structural_diagnostics(raw)
    if diagnostics:
        return diagnostics

    # A document with no steps has no workflow semantics to resolve (and the
    # planner never builds a graph for it), so it is valid by definition.
    if not raw.get("steps"):
        return []

    try:
        workflow = WorkflowConfig.from_mapping(dict(raw))
    except ValueError as exc:
        return [Diagnostic("workflow.invalid", "error", "", str(exc))]

    from .v2_adapter import to_canonical_workflow  # local: avoid an import cycle

    definition = to_canonical_workflow(workflow)
    return _semantic_diagnostics(definition)


def validate_workflow_run_context(
    definition: CanonicalWorkflowDefinition,
    *,
    input_file_count: int,
) -> list[Diagnostic]:
    """Validate facts that depend on the actual run context."""
    return [
        diagnostic
        for diagnostic in calc_input_diagnostics(definition, input_file_count=input_file_count)
        if diagnostic.code == "workflow.input_cardinality"
    ]
