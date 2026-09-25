#!/usr/bin/env python3

"""V4 workflow document parser.

Parsing has two stages: YAML text is loaded into the strict shape models from
:mod:`confflow.workflow.v4.schema`, then converted into the canonical
:class:`~confflow.workflow.v4.document.WorkflowDefinition`.  Duplicate YAML
keys are rejected rather than silently overwritten.  The parser performs no
registry semantics and never executes anything.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError

from ...domain._immutable import FrozenDict
from ...domain.binding import Binding, BindingSet, BindingSource, PortSelector
from ...domain.completion import CompletionPolicy
from ...domain.diagnostics import Diagnostic, diagnostic_sort_key
from ...domain.errors import DomainError
from ...domain.resources import ResourceRequest, SchedulerPolicy
from ...execution.contracts import ExecutionBinding
from .diagnostics import DiagnosticCode, DiagnosticReason, error
from .document import (
    SCHEMA_ID,
    RunInputDeclaration,
    ScientificDefaults,
    ScientificDefinition,
    StepDefinition,
    WorkflowDefinition,
    require_identifier,
)
from .schema import DocumentModel, InputModel, StepModel, pydantic_error_details

__all__ = [
    "DocumentParseResult",
    "WorkflowYamlError",
    "convert_document",
    "load_definition_file",
    "parse_workflow_document",
    "parse_workflow_text",
    "parse_workflow_text_document",
]


class WorkflowYamlError(DomainError):
    """Raised when YAML text cannot be loaded as a mapping."""


@dataclass(frozen=True, slots=True)
class DocumentParseResult:
    """Outcome of parsing a workflow document."""

    definition: WorkflowDefinition | None
    diagnostics: tuple[Diagnostic, ...] = ()

    @property
    def ok(self) -> bool:
        """Return whether parsing produced a definition."""
        return self.definition is not None

    @property
    def errors(self) -> tuple[Diagnostic, ...]:
        """Return the error diagnostics in deterministic order."""
        return tuple(
            sorted(
                (item for item in self.diagnostics if item.is_error),
                key=diagnostic_sort_key,
            )
        )


class _StrictSafeLoader(yaml.SafeLoader):
    """YAML loader that rejects duplicate mapping keys."""


def _construct_mapping(loader: _StrictSafeLoader, node: yaml.Node, deep: bool = False) -> dict:
    mapping: dict[Any, Any] = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if key in mapping:
            raise WorkflowYamlError(f"duplicate key {key!r} at line {key_node.start_mark.line + 1}")
        mapping[key] = loader.construct_object(value_node, deep=deep)
    return mapping


_StrictSafeLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG,
    lambda loader, node: _construct_mapping(loader, node),
)


def parse_workflow_text(text: str) -> Mapping[str, Any]:
    """Parse YAML *text* into a mapping, rejecting duplicate keys.

    Raises
    ------
    WorkflowYamlError
        Raised when the text is not valid YAML or not a mapping.
    """
    try:
        data = yaml.load(text, Loader=_StrictSafeLoader)  # noqa: S506 - SafeLoader subclass
    except WorkflowYamlError:
        raise
    except yaml.YAMLError as exc:
        raise WorkflowYamlError(f"invalid YAML: {exc}") from exc
    if data is None:
        return {}
    if not isinstance(data, Mapping):
        raise WorkflowYamlError("the workflow document must be a mapping")
    return data


def _pydantic_diagnostics(exc: ValidationError) -> tuple[Diagnostic, ...]:
    diagnostics: list[Diagnostic] = []
    for item in pydantic_error_details(exc):
        location = item["location"]
        error_type = item["type"]
        field_path = location.replace("global_", "global")
        if error_type == "extra_forbidden":
            diagnostics.append(
                error(
                    DiagnosticCode.SCHEMA_ERROR,
                    DiagnosticReason.UNKNOWN_MEMBER,
                    f"unknown member at {field_path}",
                    field_path=field_path,
                    details={"member": location.rsplit(".", 1)[-1]},
                )
            )
        elif error_type == "missing":
            diagnostics.append(
                error(
                    DiagnosticCode.SCHEMA_ERROR,
                    DiagnosticReason.MISSING_REQUIRED_MEMBER,
                    f"missing required member at {field_path}",
                    field_path=field_path,
                    details={"member": location.rsplit(".", 1)[-1]},
                )
            )
        elif error_type in ("int_type", "bool_type", "string_type", "int_parsing"):
            diagnostics.append(
                error(
                    DiagnosticCode.SCHEMA_ERROR,
                    DiagnosticReason.WRONG_TYPE,
                    f"wrong type at {field_path}: {item['message']}",
                    field_path=field_path,
                    details={"error_type": error_type},
                )
            )
        else:
            diagnostics.append(
                error(
                    DiagnosticCode.SCHEMA_ERROR,
                    DiagnosticReason.INVALID_VALUE,
                    f"invalid value at {field_path}: {item['message']}",
                    field_path=field_path,
                    details={"error_type": error_type},
                )
            )
    return tuple(diagnostics)


def _build_selector(
    select: Any, step_id: str, port_name: str
) -> tuple[PortSelector | None, list[Diagnostic]]:
    field_path = f"steps.{step_id}.bindings.{port_name}.source.select"
    if select is None:
        return PortSelector.all(), []
    role = select.role
    ids = select.ids
    if role is not None and ids:
        return None, [
            error(
                DiagnosticCode.BINDING_ERROR,
                DiagnosticReason.INVALID_VALUE,
                "selector must not declare both role and ids",
                step_id=step_id,
                field_path=field_path,
            )
        ]
    if role is not None:
        if not isinstance(role, str) or not role:
            return None, [
                error(
                    DiagnosticCode.BINDING_ERROR,
                    DiagnosticReason.INVALID_VALUE,
                    "selector role must be a non-empty string",
                    step_id=step_id,
                    field_path=field_path,
                )
            ]
        return PortSelector.by_role(role), []
    if ids:
        return PortSelector.by_ids(*ids), []
    return PortSelector.all(), []


def _build_binding(
    step_id: str, port_name: str, model: Any
) -> tuple[Binding | None, list[Diagnostic]]:
    field_path = f"steps.{step_id}.bindings.{port_name}"
    source_model = model.source
    run_name = source_model.run
    source_step = source_model.step
    source_port = source_model.port
    if (run_name is None) == (source_step is None):
        return None, [
            error(
                DiagnosticCode.BINDING_ERROR,
                DiagnosticReason.SOURCE_KIND_INVALID,
                "binding source must declare exactly one of 'run' or 'step'",
                step_id=step_id,
                field_path=field_path,
            )
        ]
    selector, diagnostics = _build_selector(source_model.select, step_id, port_name)
    if selector is None:
        return None, diagnostics
    if run_name is not None:
        if source_port is not None:
            diagnostics.append(
                error(
                    DiagnosticCode.BINDING_ERROR,
                    DiagnosticReason.INVALID_VALUE,
                    "run-input sources must not declare a port",
                    step_id=step_id,
                    field_path=field_path,
                )
            )
            return None, diagnostics
        try:
            source = BindingSource.run_input(str(run_name), selector)
        except DomainError as exc:
            diagnostics.append(
                error(
                    DiagnosticCode.BINDING_ERROR,
                    DiagnosticReason.INVALID_VALUE,
                    str(exc),
                    step_id=step_id,
                    field_path=field_path,
                )
            )
            return None, diagnostics
    else:
        if not source_port:
            diagnostics.append(
                error(
                    DiagnosticCode.BINDING_ERROR,
                    DiagnosticReason.MISSING_REQUIRED_MEMBER,
                    "step-output sources must declare a port",
                    step_id=step_id,
                    field_path=field_path,
                )
            )
            return None, diagnostics
        try:
            source = BindingSource.step_output(str(source_step), str(source_port), selector)
        except DomainError as exc:
            diagnostics.append(
                error(
                    DiagnosticCode.BINDING_ERROR,
                    DiagnosticReason.INVALID_VALUE,
                    str(exc),
                    step_id=step_id,
                    field_path=field_path,
                )
            )
            return None, diagnostics
    try:
        binding = Binding(
            target_step_id=step_id,
            target_port=str(port_name),
            source=source,
            cardinality=model.cardinality,
            pairing=model.pairing,
            partial_consumption=model.partial_consumption,
        )
    except DomainError as exc:
        diagnostics.append(
            error(
                DiagnosticCode.BINDING_ERROR,
                DiagnosticReason.INVALID_VALUE,
                str(exc),
                step_id=step_id,
                field_path=field_path,
            )
        )
        return None, diagnostics
    return binding, diagnostics


def _build_execution(step_id: str, model: Any) -> tuple[ExecutionBinding | None, list[Diagnostic]]:
    if model is None:
        return None, []
    try:
        return (
            ExecutionBinding(
                binding_id=model.binding_id,
                executable=model.executable,
                env=model.env,
                sandbox=model.sandbox,
                allowed_executables=tuple(model.allowed_executables),
                walltime_seconds=model.walltime_seconds,
                target=model.target,
            ),
            [],
        )
    except DomainError as exc:
        return None, [
            error(
                DiagnosticCode.SCHEMA_ERROR,
                DiagnosticReason.INVALID_VALUE,
                str(exc),
                step_id=step_id,
                field_path=f"steps.{step_id}.execution",
            )
        ]


_EXECUTOR_BLOCK_BY_CAPABILITY = {
    "calculation": "calculation",
    "confgen": "confgen",
    "structure_transform": "transform",
    "analysis": "analysis",
}


def _build_scientific(
    step: StepModel,
) -> tuple[ScientificDefinition | None, bool, list[Diagnostic]]:
    """Return (scientific, is_known_executor, diagnostics).

    The executor-capability vocabulary itself is validated semantically; this
    function only maps executor-specific YAML blocks onto the canonical
    scientific definition.
    """
    diagnostics: list[Diagnostic] = []
    field_path = f"steps.{step.id}"
    blocks = {
        "calculation": step.calculation,
        "confgen": step.confgen,
        "transform": step.transform,
        "analysis": step.analysis,
    }
    present = [name for name, block in blocks.items() if block is not None]
    expected_block = _EXECUTOR_BLOCK_BY_CAPABILITY.get(step.executor)
    if expected_block is None:
        return None, False, diagnostics
    block_name = expected_block
    unexpected = [name for name in present if name != block_name]
    if unexpected or len(present) > 1:
        diagnostics.append(
            error(
                DiagnosticCode.CAPABILITY_ERROR,
                DiagnosticReason.MISPLACED_EXECUTOR_BLOCK,
                f"executor {step.executor!r} must not declare blocks: "
                + ", ".join(sorted(set(unexpected) or set(present))),
                step_id=step.id,
                field_path=field_path,
            )
        )
        return None, True, diagnostics
    if block_name == "calculation":
        if step.calculation is None:
            diagnostics.append(
                error(
                    DiagnosticCode.CAPABILITY_ERROR,
                    DiagnosticReason.MISSING_EXECUTOR_BLOCK,
                    "calculation steps require a calculation block",
                    step_id=step.id,
                    field_path=field_path,
                )
            )
            return None, True, diagnostics
        calculation = step.calculation
        try:
            scientific = ScientificDefinition(
                program=calculation.program,
                role=calculation.role,
                execution_adapter=calculation.execution_adapter,
                result_profile=calculation.result_profile,
                native=FrozenDict(calculation.native),
                checks=tuple(calculation.checks),
                recovery=calculation.recovery.profile,
                seed=calculation.seed,
                overrides=FrozenDict(calculation.overrides),
            )
        except DomainError as exc:
            diagnostics.append(
                error(
                    DiagnosticCode.SCHEMA_ERROR,
                    DiagnosticReason.INVALID_VALUE,
                    str(exc),
                    step_id=step.id,
                    field_path=field_path,
                )
            )
            return None, True, diagnostics
        return scientific, True, diagnostics
    if block_name == "confgen":
        if step.confgen is None:
            diagnostics.append(
                error(
                    DiagnosticCode.CAPABILITY_ERROR,
                    DiagnosticReason.MISSING_EXECUTOR_BLOCK,
                    "confgen steps require a confgen block",
                    step_id=step.id,
                    field_path=field_path,
                )
            )
            return None, True, diagnostics
        try:
            scientific = ScientificDefinition(
                result_profile="ensemble",
                native=FrozenDict(step.confgen.native),
                seed=step.confgen.seed,
                overrides=FrozenDict(step.confgen.overrides),
            )
        except DomainError as exc:
            diagnostics.append(
                error(
                    DiagnosticCode.SCHEMA_ERROR,
                    DiagnosticReason.INVALID_VALUE,
                    str(exc),
                    step_id=step.id,
                    field_path=field_path,
                )
            )
            return None, True, diagnostics
        return scientific, True, diagnostics
    if block_name == "transform":
        if step.transform is None:
            diagnostics.append(
                error(
                    DiagnosticCode.CAPABILITY_ERROR,
                    DiagnosticReason.MISSING_EXECUTOR_BLOCK,
                    "structure_transform steps require a transform block",
                    step_id=step.id,
                    field_path=field_path,
                )
            )
            return None, True, diagnostics
        try:
            scientific = ScientificDefinition(
                result_profile="standard",
                native=FrozenDict(step.transform.native),
                transform=step.transform.kind,
            )
        except DomainError as exc:
            diagnostics.append(
                error(
                    DiagnosticCode.SCHEMA_ERROR,
                    DiagnosticReason.INVALID_VALUE,
                    str(exc),
                    step_id=step.id,
                    field_path=field_path,
                )
            )
            return None, True, diagnostics
        return scientific, True, diagnostics
    # analysis: the block is optional (an empty analysis is legal).
    analysis = step.analysis
    try:
        scientific = ScientificDefinition(
            result_profile="standard",
            native=FrozenDict(analysis.native if analysis is not None else {}),
            checks=tuple(analysis.checks if analysis is not None else ()),
        )
    except DomainError as exc:
        diagnostics.append(
            error(
                DiagnosticCode.SCHEMA_ERROR,
                DiagnosticReason.INVALID_VALUE,
                str(exc),
                step_id=step.id,
                field_path=field_path,
            )
        )
        return None, True, diagnostics
    return scientific, True, diagnostics


def _build_step(step: StepModel) -> tuple[StepDefinition | None, list[Diagnostic]]:
    diagnostics: list[Diagnostic] = []
    try:
        require_identifier(step.id, "step id")
    except DomainError as exc:
        diagnostics.append(
            error(
                DiagnosticCode.SCHEMA_ERROR,
                DiagnosticReason.INVALID_STEP_ID,
                str(exc),
                step_id=step.id,
                field_path=f"steps.{step.id}.id",
            )
        )
        return None, diagnostics
    bindings: list[Binding] = []
    for port_name, binding_model in step.bindings.items():
        binding, binding_diagnostics = _build_binding(step.id, port_name, binding_model)
        diagnostics.extend(binding_diagnostics)
        if binding is not None:
            bindings.append(binding)
    scientific, _known, scientific_diagnostics = _build_scientific(step)
    diagnostics.extend(scientific_diagnostics)
    execution, execution_diagnostics = _build_execution(step.id, step.execution)
    diagnostics.extend(execution_diagnostics)
    try:
        binding_set = BindingSet(tuple(bindings))
    except DomainError as exc:
        diagnostics.append(
            error(
                DiagnosticCode.BINDING_ERROR,
                DiagnosticReason.INVALID_VALUE,
                str(exc),
                step_id=step.id,
                field_path=f"steps.{step.id}.bindings",
            )
        )
        binding_set = BindingSet()
    try:
        resources = (
            ResourceRequest.from_values(
                cores_per_item=step.resources.cores_per_item,
                memory_per_item=step.resources.memory_per_item,
            )
            if step.resources is not None
            else ResourceRequest()
        )
        scheduler = (
            SchedulerPolicy(
                max_parallel_items=step.scheduler.max_parallel_items,
                on_failure=step.scheduler.on_failure,
            )
            if step.scheduler is not None
            else SchedulerPolicy()
        )
        completion = CompletionPolicy(
            mode=step.completion.mode,
            minimum_success=step.completion.minimum_success,
            partial_output=step.completion.partial_output,
        )
    except DomainError as exc:
        diagnostics.append(
            error(
                DiagnosticCode.SCHEMA_ERROR,
                DiagnosticReason.INVALID_VALUE,
                str(exc),
                step_id=step.id,
                field_path=f"steps.{step.id}",
            )
        )
        return None, diagnostics
    try:
        definition = StepDefinition(
            id=step.id,
            label=step.label,
            enabled=bool(step.enabled),
            executor=step.executor,
            scientific=scientific,
            bindings=binding_set,
            resources=resources,
            scheduler=scheduler,
            completion=completion,
            execution=execution,
            annotations=FrozenDict(step.annotations),
        )
    except DomainError as exc:
        diagnostics.append(
            error(
                DiagnosticCode.SCHEMA_ERROR,
                DiagnosticReason.INVALID_VALUE,
                str(exc),
                step_id=step.id,
                field_path=f"steps.{step.id}",
            )
        )
        return None, diagnostics
    return definition, diagnostics


def _build_inputs(
    inputs: Mapping[str, InputModel],
) -> tuple[tuple[RunInputDeclaration, ...], list[Diagnostic]]:
    declarations: list[RunInputDeclaration] = []
    diagnostics: list[Diagnostic] = []
    for name, model in inputs.items():
        try:
            require_identifier(name, "run input name")
            declarations.append(
                RunInputDeclaration(
                    name=name,
                    kind=model.kind,
                    cardinality=model.cardinality,
                    pairing=model.pairing,
                    role=model.role,
                    description=model.description,
                )
            )
        except DomainError as exc:
            diagnostics.append(
                error(
                    DiagnosticCode.SCHEMA_ERROR,
                    DiagnosticReason.INVALID_INPUT_NAME,
                    str(exc),
                    field_path=f"inputs.{name}",
                )
            )
    return tuple(declarations), diagnostics


def convert_document(model: DocumentModel) -> DocumentParseResult:
    """Convert a shape-validated document into the canonical definition."""
    diagnostics: list[Diagnostic] = []
    if model.schema_id != SCHEMA_ID:
        diagnostics.append(
            error(
                DiagnosticCode.SCHEMA_ERROR,
                DiagnosticReason.VERSION_UNSUPPORTED,
                f"unsupported workflow schema {model.schema_id!r}; expected {SCHEMA_ID!r}",
                field_path="schema",
                details={"found": model.schema_id, "supported": SCHEMA_ID},
            )
        )
        return DocumentParseResult(None, tuple(diagnostics))
    inputs, input_diagnostics = _build_inputs(model.inputs)
    diagnostics.extend(input_diagnostics)
    steps: list[StepDefinition] = []
    for step_model in model.steps:
        step, step_diagnostics = _build_step(step_model)
        diagnostics.extend(step_diagnostics)
        if step is not None:
            steps.append(step)
    step_ids = [step.id for step in steps]
    seen: set[str] = set()
    for step_id in step_ids:
        if step_id in seen:
            diagnostics.append(
                error(
                    DiagnosticCode.IDENTITY_ERROR,
                    DiagnosticReason.DUPLICATE_STEP_ID,
                    f"duplicate step id {step_id!r}",
                    step_id=step_id,
                    field_path="steps",
                )
            )
        seen.add(step_id)
    input_names = [declaration.name for declaration in inputs]
    seen_inputs: set[str] = set()
    for name in input_names:
        if name in seen_inputs:
            diagnostics.append(
                error(
                    DiagnosticCode.IDENTITY_ERROR,
                    DiagnosticReason.DUPLICATE_INPUT_NAME,
                    f"duplicate run input name {name!r}",
                    field_path=f"inputs.{name}",
                )
            )
        seen_inputs.add(name)
    if not steps:
        diagnostics.append(
            error(
                DiagnosticCode.SCHEMA_ERROR,
                DiagnosticReason.EMPTY_STEPS,
                "the workflow must declare at least one valid step",
                field_path="steps",
            )
        )
    definition = WorkflowDefinition(
        steps=tuple(steps),
        inputs=inputs,
        scientific_defaults=ScientificDefaults(
            charge=model.global_.scientific_defaults.charge,
            multiplicity=model.global_.scientific_defaults.multiplicity,
            freeze=(
                tuple(model.global_.scientific_defaults.freeze)
                if model.global_.scientific_defaults.freeze is not None
                else None
            ),
        ),
        resources=ResourceRequest.from_values(
            cores_per_item=model.global_.resources.cores_per_item,
            memory_per_item=model.global_.resources.memory_per_item,
        ),
        scheduler=SchedulerPolicy(
            max_parallel_items=model.global_.scheduler.max_parallel_items,
            on_failure=model.global_.scheduler.on_failure,
        ),
    )
    if any(item.is_error for item in diagnostics):
        return DocumentParseResult(None, tuple(diagnostics))
    return DocumentParseResult(definition, tuple(diagnostics))


def parse_workflow_document(raw: Mapping[str, Any]) -> DocumentParseResult:
    """Validate *raw* mapping shape and convert it into a definition."""
    try:
        model = DocumentModel.model_validate(dict(raw))
    except ValidationError as exc:
        return DocumentParseResult(None, _pydantic_diagnostics(exc))
    return convert_document(model)


def parse_workflow_text_document(text: str) -> DocumentParseResult:
    """Parse YAML *text* and convert it into a definition."""
    try:
        raw = parse_workflow_text(text)
    except WorkflowYamlError as exc:
        return DocumentParseResult(
            None,
            (
                error(
                    DiagnosticCode.SCHEMA_ERROR,
                    DiagnosticReason.YAML_SYNTAX,
                    str(exc),
                    field_path="<document>",
                ),
            ),
        )
    return parse_workflow_document(raw)


def load_definition_file(path: str | Path) -> DocumentParseResult:
    """Load and parse a workflow YAML file."""
    text = Path(path).read_text(encoding="utf-8")
    return parse_workflow_text_document(text)
