#!/usr/bin/env python3

"""V4 workflow document schema: the single source of shape and defaults.

YAML documents are validated into these pydantic models first; the canonical
:class:`~confflow.workflow.v4.document.WorkflowDefinition` is then built from
them.  Defaults live here exactly once and are surfaced into the generated
JSON Schema, so producers, editors, and runtime read one authority.

Unknown members are rejected everywhere (``extra="forbid"``): there is no
extension escape hatch in V4-1, so a misspelled key such as ``auto_clean``
fails closed instead of being ignored.
"""

from __future__ import annotations

from typing import Any, cast

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    StrictInt,
    ValidationError,
    field_validator,
)

from ...domain.binding import Cardinality, Pairing, PartialConsumption, PortKind
from ...domain.completion import CompletionMode, PartialOutputPolicy
from ...domain.resources import OnFailure, parse_memory_bytes

__all__ = [
    "DEFAULT_CORES_PER_ITEM",
    "DEFAULT_MAX_PARALLEL_ITEMS",
    "DEFAULT_MEMORY_PER_ITEM",
    "TRANSFORM_KINDS",
    "AnalysisModel",
    "BindingModel",
    "CalculationModel",
    "CompletionModel",
    "ConfgenModel",
    "DocumentModel",
    "ExecutionModel",
    "GlobalModel",
    "InputModel",
    "RecoveryModel",
    "ResourcesModel",
    "SchedulerModel",
    "SelectModel",
    "ScientificDefaultsModel",
    "SourceModel",
    "StepModel",
    "TransformModel",
    "build_workflow_json_schema",
    "defaults_summary",
    "pydantic_error_details",
]

#: Conservative single-source resource defaults (V4-1).
DEFAULT_CORES_PER_ITEM = 1
DEFAULT_MEMORY_PER_ITEM = "1GiB"
DEFAULT_MAX_PARALLEL_ITEMS = 1

#: Closed V4-1 structure transform vocabulary.  Refinement and cleanup are
#: explicit transforms; they never happen as a hidden calculation tail.
TRANSFORM_KINDS = ("refine", "deduplicate", "filter")

_STRICT = ConfigDict(extra="forbid", frozen=True, populate_by_name=True)


class SelectModel(BaseModel):
    """Explicit selector on a binding source."""

    model_config = _STRICT

    role: str | None = None
    ids: list[str] | None = None


class SourceModel(BaseModel):
    """Binding source: exactly one of ``run`` or ``step``."""

    model_config = _STRICT

    run: str | None = None
    step: str | None = None
    port: str | None = None
    select: SelectModel | None = None


class BindingModel(BaseModel):
    """One binding into a named target port."""

    model_config = _STRICT

    source: SourceModel
    cardinality: Cardinality | None = None
    pairing: Pairing | None = None
    partial_consumption: PartialConsumption | None = None


class ResourcesModel(BaseModel):
    """Per-item resource request; defaults are the run-level defaults."""

    model_config = _STRICT

    cores_per_item: StrictInt = Field(default=DEFAULT_CORES_PER_ITEM, ge=1)
    memory_per_item: str | StrictInt = DEFAULT_MEMORY_PER_ITEM

    @field_validator("memory_per_item")
    @classmethod
    def _validate_memory(cls, value: str | int) -> str | int:
        parse_memory_bytes(value)
        return value


class SchedulerModel(BaseModel):
    """Scheduler-only policy."""

    model_config = _STRICT

    max_parallel_items: StrictInt = Field(default=DEFAULT_MAX_PARALLEL_ITEMS, ge=1)
    on_failure: OnFailure = OnFailure.CONTINUE


class CompletionModel(BaseModel):
    """Acceptance policy of a step."""

    model_config = _STRICT

    mode: CompletionMode = CompletionMode.REQUIRE_ALL
    minimum_success: StrictInt | None = Field(default=None, ge=1)
    partial_output: PartialOutputPolicy = PartialOutputPolicy.DENY


class RecoveryModel(BaseModel):
    """Declared recovery profile."""

    model_config = _STRICT

    profile: str = "none"
    params: dict[str, Any] = Field(default_factory=dict)


class CalculationModel(BaseModel):
    """Scientific definition of a calculation step."""

    model_config = _STRICT

    program: str = Field(min_length=1)
    role: str | None = None
    execution_adapter: str = "standard"
    result_profile: str = "standard"
    native: dict[str, Any] = Field(default_factory=dict)
    checks: list[str] = Field(default_factory=list)
    check_params: dict[str, dict[str, Any]] = Field(default_factory=dict)
    recovery: RecoveryModel = Field(default_factory=RecoveryModel)
    seed: StrictInt | None = None
    overrides: dict[str, Any] = Field(default_factory=dict)


class ConfgenModel(BaseModel):
    """Scientific definition of a conformer-generation step."""

    model_config = _STRICT

    native: dict[str, Any] = Field(default_factory=dict)
    seed: StrictInt | None = None
    overrides: dict[str, Any] = Field(default_factory=dict)


class TransformModel(BaseModel):
    """Explicit structure-set transformation (refine/deduplicate/filter)."""

    model_config = _STRICT

    kind: str = Field(min_length=1)
    native: dict[str, Any] = Field(default_factory=dict)


class AnalysisModel(BaseModel):
    """Analysis step definition."""

    model_config = _STRICT

    native: dict[str, Any] = Field(default_factory=dict)
    checks: list[str] = Field(default_factory=list)
    check_params: dict[str, dict[str, Any]] = Field(default_factory=dict)


class ExecutionModel(BaseModel):
    """Machine-specific execution binding (never part of scientific digests)."""

    model_config = _STRICT

    binding_id: str = "default"
    executable: str | None = None
    env: dict[str, str] = Field(default_factory=dict)
    sandbox: str | None = None
    allowed_executables: list[str] = Field(default_factory=list)
    walltime_seconds: StrictInt | None = Field(default=None, ge=1)
    target: str | None = None


class StepModel(BaseModel):
    """One workflow step."""

    model_config = _STRICT

    id: str = Field(min_length=1)
    label: str | None = None
    enabled: StrictBool = True
    executor: str = Field(min_length=1)
    bindings: dict[str, BindingModel] = Field(default_factory=dict)
    calculation: CalculationModel | None = None
    confgen: ConfgenModel | None = None
    transform: TransformModel | None = None
    analysis: AnalysisModel | None = None
    resources: ResourcesModel | None = None
    scheduler: SchedulerModel | None = None
    completion: CompletionModel = Field(default_factory=CompletionModel)
    execution: ExecutionModel | None = None
    annotations: dict[str, Any] = Field(default_factory=dict)


class InputModel(BaseModel):
    """Declaration of a named run input."""

    model_config = _STRICT

    kind: PortKind
    cardinality: Cardinality = Cardinality.MANY
    pairing: Pairing | None = None
    role: str | None = None
    description: str | None = None


class ScientificDefaultsModel(BaseModel):
    """Run-level explicit scientific defaults."""

    model_config = _STRICT

    charge: StrictInt | None = None
    multiplicity: StrictInt | None = Field(default=None, ge=1)
    freeze: list[StrictInt] | None = None


class GlobalModel(BaseModel):
    """Run-level defaults: science, resources, scheduler."""

    model_config = _STRICT

    scientific_defaults: ScientificDefaultsModel = Field(default_factory=ScientificDefaultsModel)
    resources: ResourcesModel = Field(default_factory=ResourcesModel)
    scheduler: SchedulerModel = Field(default_factory=SchedulerModel)


class DocumentModel(BaseModel):
    """A complete V4 workflow document."""

    model_config = _STRICT

    schema_id: str = Field(alias="schema", min_length=1)
    inputs: dict[str, InputModel] = Field(default_factory=dict)
    global_: GlobalModel = Field(default_factory=GlobalModel, alias="global")
    steps: list[StepModel] = Field(min_length=1)


def defaults_summary() -> dict[str, Any]:
    """Return the single-source default values for editors and tests."""
    return {
        "resources": {
            "cores_per_item": DEFAULT_CORES_PER_ITEM,
            "memory_per_item": DEFAULT_MEMORY_PER_ITEM,
        },
        "scheduler": {"max_parallel_items": DEFAULT_MAX_PARALLEL_ITEMS},
        "completion": {
            "mode": CompletionMode.REQUIRE_ALL.value,
            "partial_output": PartialOutputPolicy.DENY.value,
        },
        "calculation": {
            "execution_adapter": "standard",
            "result_profile": "standard",
            "recovery": "none",
        },
    }


def _inject_enum(
    schema: dict[str, Any], model_name: str, path: str, values: tuple[str, ...]
) -> None:
    node: Any = schema.get("$defs", {}).get(model_name)
    if node is None:
        return
    parts = path.split(".")
    for part in parts[:-1]:
        if part == "items":
            node = node.get("items")
        else:
            node = node.get("properties", {}).get(part)
        if node is None:
            return
    last = parts[-1]
    if last == "items":
        node["items"]["enum"] = list(values)
    else:
        node.setdefault("properties", {}).setdefault(last, {})["enum"] = list(values)


def build_workflow_json_schema(registry: Any = None) -> dict[str, Any]:
    """Build the V4 JSON Schema with registry vocabularies injected.

    The pydantic models own shape and defaults; capability vocabularies are
    injected from the execution registry so schema and runtime never drift.
    """
    if registry is None:
        from ...execution.registry import default_registry

        registry = default_registry()
    schema = DocumentModel.model_json_schema(by_alias=True)
    _inject_enum(schema, "StepModel", "executor", registry.capability_names)
    _inject_enum(schema, "CalculationModel", "execution_adapter", registry.adapter_names)
    _inject_enum(schema, "CalculationModel", "result_profile", registry.profile_names)
    _inject_enum(schema, "CalculationModel", "checks.items", registry.check_names)
    _inject_enum(schema, "CalculationModel", "recovery.profile", registry.recovery_names)
    _inject_enum(schema, "AnalysisModel", "checks.items", registry.check_names)
    _inject_enum(schema, "TransformModel", "kind", TRANSFORM_KINDS)
    return cast(dict[str, Any], schema)


def pydantic_error_details(exc: ValidationError) -> list[dict[str, Any]]:
    """Flatten a pydantic error into deterministic detail dictionaries."""
    details: list[dict[str, Any]] = []
    for item in exc.errors():
        location = ".".join(str(part) for part in item.get("loc", ()))
        details.append(
            {
                "location": location,
                "type": str(item.get("type", "")),
                "message": str(item.get("msg", "")),
            }
        )
    return details
