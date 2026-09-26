#!/usr/bin/env python3

"""Producer-owned V4 editor manifest: which workflow fields a GUI may edit.

The manifest follows the structural pattern of
:mod:`confflow.config.canonical.editor_manifest` (field entries with
``field_id``/``context``/``json_pointer``/``label``/``value_type``/``editor``,
step fields addressed at ``/steps/{id}`` with an explicit
``step_selector: "id"`` plus ``relative_pointer``), but every pointer targets
the V4 workflow schema and every choice list is read from the live V4
registries.

Only user-editable scientific, scheduler, resource, completion, and execution
settings are published. Digests, owner tokens, sqlite paths, and work-item
internals never appear as fields.

Every field pointer is verified against the workflow JSON schema at build
time; :func:`build_editor_manifest_v4` raises :class:`ValueError` when a
pointer does not resolve, so a schema change without a manifest update fails
closed instead of publishing a dangling editor.
"""

from __future__ import annotations

import copy
from typing import Any

from ..config.canonical.editor_manifest import EDITOR_MANIFEST_SCHEMA
from ..domain.binding import Cardinality, Pairing
from ..domain.canonical import canonical_sha256
from ..domain.completion import CompletionMode, PartialOutputPolicy
from ..domain.resources import OnFailure
from ..execution.native import ProgramName
from ..execution.registry import ExecutionRegistry, default_registry
from ..workflow.v4.document import SCHEMA_ID
from ..workflow.v4.schema import (
    TRANSFORM_KINDS,
    build_workflow_json_schema,
    defaults_summary,
)

#: Placeholder spellings accepted in step-scoped template pointers.
_PLACEHOLDERS = frozenset({"{id}", "{index}"})


def _resolve_ref(node: Any, defs: dict[str, Any]) -> Any:
    """Follow local ``$ref`` links until a concrete schema node remains."""
    seen: set[str] = set()
    while isinstance(node, dict) and isinstance(node.get("$ref"), str):
        reference = str(node["$ref"])
        if reference in seen:
            return node
        seen.add(reference)
        prefix = "#/$defs/"
        if reference.startswith(prefix):
            node = defs.get(reference[len(prefix) :], node)
        else:  # pragma: no cover - only local refs are generated
            return node
    return node


def _descend(node: Any, part: str, defs: dict[str, Any]) -> Any | None:
    """Descend one pointer part; return the child node or ``None``.

    Free-form mappings (``native``, ``overrides``, ``env``, ``annotations``)
    accept any remaining part without further descent.
    """
    node = _resolve_ref(node, defs)
    if not isinstance(node, dict):
        return None
    if part in _PLACEHOLDERS:
        items = node.get("items")
        if items is None:
            return None
        return _resolve_ref(items, defs)
    properties = node.get("properties")
    if isinstance(properties, dict) and part in properties:
        return _resolve_ref(properties[part], defs)
    for branch_key in ("anyOf", "oneOf"):
        branches = node.get(branch_key)
        if isinstance(branches, list):
            for branch in branches:
                hit = _descend(branch, part, defs)
                if hit is not None:
                    return hit
    node_type = node.get("type")
    if node_type == "object" and not isinstance(properties, dict):
        return node
    if node_type == "array" and part.isdigit():
        return _resolve_ref(node.get("items", {}), defs)
    return None


def pointer_resolves(schema: dict[str, Any], pointer: str) -> bool:
    """Return whether *pointer* resolves against the workflow JSON *schema*.

    Parameters
    ----------
    schema :
        Workflow JSON Schema mapping as produced by
        :func:`confflow.workflow.v4.schema.build_workflow_json_schema`.
    pointer :
        JSON pointer such as ``/global/resources/cores_per_item`` or the
        step template ``/steps/{id}/calculation/program``.
    """
    if not pointer.startswith("/"):
        return False
    node: Any = schema
    defs = schema.get("$defs", {}) if isinstance(schema, dict) else {}
    for part in pointer.split("/")[1:]:
        node = _descend(node, part, defs if isinstance(defs, dict) else {})
        if node is None:
            return False
    return True


def _step_definition_node(schema: dict[str, Any]) -> dict[str, Any]:
    """Return the ``StepModel`` schema node used for relative pointers."""
    defs = schema.get("$defs", {})
    node = defs.get("StepModel", {}) if isinstance(defs, dict) else {}
    return node if isinstance(node, dict) else {}


def verify_manifest_fields(fields: list[dict[str, Any]], schema: dict[str, Any]) -> None:
    """Verify every manifest field pointer against the workflow *schema*.

    Raises
    ------
    ValueError
        Raised when a field pointer (or a step field's ``relative_pointer``)
        does not resolve, or when a step-scoped field lacks its selector pair.
    """
    step_node = _step_definition_node(schema)
    problems: list[str] = []
    for field in fields:
        field_id = str(field.get("field_id", "?"))
        pointer = str(field.get("json_pointer", ""))
        if any(part in _PLACEHOLDERS for part in pointer.split("/")):
            if field.get("step_selector") != "id":
                problems.append(f"{field_id}: template pointer without step_selector id")
                continue
            relative = str(field.get("relative_pointer", ""))
            if not pointer_resolves(schema, pointer):
                problems.append(f"{field_id}: template pointer does not resolve: {pointer}")
            probe = {"$defs": schema.get("$defs", {}), **step_node}
            if not pointer_resolves(probe, relative):
                problems.append(
                    f"{field_id}: relative pointer does not resolve in StepModel: {relative}"
                )
        elif not pointer_resolves(schema, pointer):
            problems.append(f"{field_id}: pointer does not resolve: {pointer}")
    if problems:
        raise ValueError(
            "editor manifest fields do not resolve against the workflow schema: "
            + "; ".join(problems)
        )


def _program_choices() -> list[dict[str, str]]:
    """Return the program choice list derived from :class:`ProgramName`."""
    labels = {"gaussian": "Gaussian", "orca": "ORCA"}
    return [{"value": program.value, "label": labels[program.value]} for program in ProgramName]


def _enum_choices(values: tuple[str, ...]) -> list[dict[str, str]]:
    """Return labelled choices for a closed string vocabulary."""
    return [{"value": value, "label": value.replace("_", " ")} for value in values]


def _v4_fields(registry: ExecutionRegistry) -> list[dict[str, Any]]:
    """Build the V4 editor field list from live registry vocabularies."""
    defaults = defaults_summary()
    program_field_choices = _program_choices()
    adapter_choices = _enum_choices(registry.adapter_names)
    profile_choices = _enum_choices(registry.profile_names)
    check_choices = _enum_choices(registry.check_names)
    recovery_choices = _enum_choices(registry.recovery_names)
    transform_choices = _enum_choices(TRANSFORM_KINDS)
    on_failure_choices = _enum_choices(tuple(item.value for item in OnFailure))
    completion_choices = _enum_choices(tuple(item.value for item in CompletionMode))
    partial_choices = _enum_choices(tuple(item.value for item in PartialOutputPolicy))
    cardinality_values = [item.value for item in Cardinality]
    pairing_values = [item.value for item in Pairing]

    def _step(
        field_id: str,
        relative: str,
        *,
        label: str,
        description: str,
        value_type: str,
        editor: str,
        group: str,
        level: str,
        order: int,
        **extra: Any,
    ) -> dict[str, Any]:
        """Build one step-scoped field entry with id addressing."""
        field: dict[str, Any] = {
            "field_id": field_id,
            "context": field_id.split(".")[0],
            "json_pointer": f"/steps/{{id}}{relative}",
            "step_selector": "id",
            "relative_pointer": relative,
            "label": label,
            "description": description,
            "value_type": value_type,
            "editor": editor,
            "group": group,
            "level": level,
            "order": order,
        }
        field.update(extra)
        return field

    def _global(
        field_id: str,
        pointer: str,
        *,
        label: str,
        description: str,
        value_type: str,
        editor: str,
        group: str,
        level: str,
        order: int,
        **extra: Any,
    ) -> dict[str, Any]:
        """Build one run-global field entry."""
        field: dict[str, Any] = {
            "field_id": field_id,
            "context": "global",
            "json_pointer": pointer,
            "label": label,
            "description": description,
            "value_type": value_type,
            "editor": editor,
            "group": group,
            "level": level,
            "order": order,
        }
        field.update(extra)
        return field

    fields: list[dict[str, Any]] = [
        _global(
            "global.charge",
            "/global/scientific_defaults/charge",
            label="Charge",
            description="Total molecular charge applied unless a step overrides it.",
            value_type="integer",
            editor="integer",
            group="system",
            level="basic",
            order=10,
        ),
        _global(
            "global.multiplicity",
            "/global/scientific_defaults/multiplicity",
            label="Multiplicity",
            description="Spin multiplicity; must be at least 1.",
            value_type="integer",
            editor="integer",
            group="system",
            level="basic",
            order=20,
        ),
        _global(
            "global.freeze",
            "/global/scientific_defaults/freeze",
            label="Frozen atoms",
            description="1-based atom indices held fixed where the program supports it.",
            value_type="array",
            editor="integer_list",
            item_type="integer",
            group="system",
            level="advanced",
            order=30,
        ),
        _global(
            "global.cores_per_item",
            "/global/resources/cores_per_item",
            label="CPU cores per item",
            description="Default core count. A step may override it.",
            value_type="integer",
            editor="integer",
            group="resources",
            level="basic",
            order=40,
            default=defaults["resources"]["cores_per_item"],
        ),
        _global(
            "global.memory_per_item",
            "/global/resources/memory_per_item",
            label="Memory per item",
            description="Default memory per item, written as e.g. 4GiB or 8000MB.",
            value_type="string",
            editor="memory",
            group="resources",
            level="basic",
            order=50,
            default=defaults["resources"]["memory_per_item"],
        ),
        _global(
            "global.max_parallel_items",
            "/global/scheduler/max_parallel_items",
            label="Parallel items",
            description="Maximum number of work items running concurrently.",
            value_type="integer",
            editor="integer",
            group="scheduling",
            level="basic",
            order=60,
            default=defaults["scheduler"]["max_parallel_items"],
        ),
        _global(
            "global.on_failure",
            "/global/scheduler/on_failure",
            label="On failure",
            description="Whether remaining items keep running after a failure.",
            value_type="string",
            editor="select",
            group="scheduling",
            level="advanced",
            order=70,
            choices=on_failure_choices,
            default=OnFailure.CONTINUE.value,
        ),
        _step(
            "calc.program",
            "/calculation/program",
            label="Program",
            description="Quantum chemistry program; selects the file-format adapter.",
            value_type="string",
            editor="select",
            group="calculation",
            level="basic",
            order=10,
            choices=program_field_choices,
        ),
        _step(
            "calc.role",
            "/calculation/role",
            label="Role",
            description=(
                "Scientific annotation of the step's intent (opt, sp, freq, ts, "
                "irc, qst2, neb, ...). Never selects runtime behavior."
            ),
            value_type="string",
            editor="text",
            group="calculation",
            level="basic",
            order=20,
        ),
        _step(
            "calc.execution_adapter",
            "/calculation/execution_adapter",
            label="Execution adapter",
            description="How step inputs are supplied to the native program.",
            value_type="string",
            editor="select",
            group="calculation",
            level="advanced",
            order=30,
            choices=adapter_choices,
            default=defaults["calculation"]["execution_adapter"],
        ),
        _step(
            "calc.result_profile",
            "/calculation/result_profile",
            label="Result profile",
            description="How native output is interpreted into results.",
            value_type="string",
            editor="select",
            group="calculation",
            level="advanced",
            order=40,
            choices=profile_choices,
            default=defaults["calculation"]["result_profile"],
        ),
        _step(
            "calc.checks",
            "/calculation/checks",
            label="Scientific checks",
            description="Acceptance checks; each must be supported by the result profile.",
            value_type="array",
            editor="multi_select",
            item_type="string",
            group="checks",
            level="basic",
            order=50,
            choices=check_choices,
        ),
        _step(
            "calc.check_params",
            "/calculation/check_params",
            label="Check parameters",
            description="Per-check parameters keyed by check name.",
            value_type="object",
            editor="json",
            group="checks",
            level="advanced",
            order=60,
        ),
        _step(
            "calc.recovery",
            "/calculation/recovery/profile",
            label="Recovery profile",
            description="Declared recovery; only ever triggered when declared here.",
            value_type="string",
            editor="select",
            group="recovery",
            level="advanced",
            order=70,
            choices=recovery_choices,
            default=defaults["calculation"]["recovery"],
        ),
        _step(
            "calc.recovery_params",
            "/calculation/recovery/params",
            label="Recovery parameters",
            description="Parameters of the declared recovery profile.",
            value_type="object",
            editor="json",
            group="recovery",
            level="advanced",
            order=80,
        ),
        _step(
            "calc.seed",
            "/calculation/seed",
            label="Seed",
            description="Explicit seed; required for stochastic executors.",
            value_type="integer",
            editor="integer",
            group="calculation",
            level="advanced",
            order=90,
        ),
        _step(
            "calc.native",
            "/calculation/native",
            label="Native input",
            description=(
                "Program-native definition (keyword, irc/goat/neb sections, "
                "atom mapping). The verbatim escape hatch into file format."
            ),
            value_type="object",
            editor="json",
            group="calculation",
            level="basic",
            order=100,
        ),
        _step(
            "calc.overrides.charge",
            "/calculation/overrides/charge",
            label="Charge override",
            description="Step override. Clear it to inherit the global value.",
            value_type="integer",
            editor="integer",
            group="system",
            level="advanced",
            order=110,
            inherit_from="global.charge",
        ),
        _step(
            "calc.overrides.multiplicity",
            "/calculation/overrides/multiplicity",
            label="Multiplicity override",
            description="Step override. Clear it to inherit the global value.",
            value_type="integer",
            editor="integer",
            group="system",
            level="advanced",
            order=120,
            inherit_from="global.multiplicity",
        ),
        _step(
            "calc.overrides.freeze",
            "/calculation/overrides/freeze",
            label="Frozen atoms override",
            description="Step override. Clear it to inherit the global value.",
            value_type="array",
            editor="integer_list",
            item_type="integer",
            group="system",
            level="advanced",
            order=130,
            inherit_from="global.freeze",
        ),
        _step(
            "calc.resources.cores_per_item",
            "/resources/cores_per_item",
            label="CPU cores per item",
            description="Step override. Clear it to inherit the global value.",
            value_type="integer",
            editor="integer",
            group="resources",
            level="advanced",
            order=140,
            inherit_from="global.cores_per_item",
        ),
        _step(
            "calc.resources.memory_per_item",
            "/resources/memory_per_item",
            label="Memory per item",
            description="Step override. Clear it to inherit the global value.",
            value_type="string",
            editor="memory",
            group="resources",
            level="advanced",
            order=150,
            inherit_from="global.memory_per_item",
        ),
        _step(
            "calc.scheduler.max_parallel_items",
            "/scheduler/max_parallel_items",
            label="Parallel items",
            description="Step override. Clear it to inherit the global value.",
            value_type="integer",
            editor="integer",
            group="scheduling",
            level="advanced",
            order=160,
            inherit_from="global.max_parallel_items",
        ),
        _step(
            "calc.scheduler.on_failure",
            "/scheduler/on_failure",
            label="On failure",
            description="Step override of the scheduler failure behavior.",
            value_type="string",
            editor="select",
            group="scheduling",
            level="advanced",
            order=170,
            choices=on_failure_choices,
            inherit_from="global.on_failure",
        ),
        _step(
            "calc.completion.mode",
            "/completion/mode",
            label="Completion mode",
            description="Whether a step may finish with a partial subset accepted.",
            value_type="string",
            editor="select",
            group="completion",
            level="advanced",
            order=180,
            choices=completion_choices,
            default=defaults["completion"]["mode"],
        ),
        _step(
            "calc.completion.minimum_success",
            "/completion/minimum_success",
            label="Minimum success",
            description="Minimum completed items still accepted under allow_partial.",
            value_type="integer",
            editor="integer",
            group="completion",
            level="advanced",
            order=190,
            visible_when={"field": "calc.completion.mode", "equals": "allow_partial"},
        ),
        _step(
            "calc.completion.partial_output",
            "/completion/partial_output",
            label="Partial output",
            description="Whether downstream steps may consume a partial producer.",
            value_type="string",
            editor="select",
            group="completion",
            level="advanced",
            order=200,
            choices=partial_choices,
            default=defaults["completion"]["partial_output"],
        ),
        _step(
            "calc.execution.binding_id",
            "/execution/binding_id",
            label="Execution binding",
            description="Machine binding selected at run time; never affects reuse identity.",
            value_type="string",
            editor="text",
            group="execution",
            level="advanced",
            order=210,
        ),
        _step(
            "calc.execution.executable",
            "/execution/executable",
            label="Executable",
            description="Resolved executable override for this step.",
            value_type="string",
            editor="text",
            group="execution",
            level="advanced",
            order=220,
        ),
        _step(
            "calc.execution.walltime_seconds",
            "/execution/walltime_seconds",
            label="Walltime",
            description="Walltime limit in seconds for this step.",
            value_type="integer",
            editor="integer",
            group="execution",
            level="advanced",
            order=230,
        ),
        _step(
            "calc.execution.target",
            "/execution/target",
            label="Target",
            description="Scheduler target or queue for this step.",
            value_type="string",
            editor="text",
            group="execution",
            level="advanced",
            order=240,
        ),
        _step(
            "confgen.seed",
            "/confgen/seed",
            label="Seed",
            description="Explicit seed; conformer generation is stochastic.",
            value_type="integer",
            editor="integer",
            group="conformer generation",
            level="basic",
            order=10,
        ),
        _step(
            "confgen.native",
            "/confgen/native",
            label="Native input",
            description="Conformer-generation native definition (chains, ensemble options).",
            value_type="object",
            editor="json",
            group="conformer generation",
            level="basic",
            order=20,
        ),
        _step(
            "transform.kind",
            "/transform/kind",
            label="Transform kind",
            description="Explicit structure-set transformation.",
            value_type="string",
            editor="select",
            group="transform",
            level="basic",
            order=10,
            choices=transform_choices,
        ),
        _step(
            "analysis.checks",
            "/analysis/checks",
            label="Analysis checks",
            description="Acceptance checks applied to the analysis inputs.",
            value_type="array",
            editor="multi_select",
            item_type="string",
            group="analysis",
            level="basic",
            order=10,
            choices=check_choices,
        ),
        _step(
            "analysis.native",
            "/analysis/native",
            label="Native input",
            description="Analysis native definition (method, options).",
            value_type="object",
            editor="json",
            group="analysis",
            level="basic",
            order=20,
        ),
    ]
    fields.append(
        {
            "field_id": "binding.cardinality",
            "context": "binding",
            "json_pointer": "/steps/{id}/bindings",
            "step_selector": "id",
            "relative_pointer": "/bindings",
            "label": "Binding cardinality",
            "description": (
                "Per-binding cardinality and pairing. Allowed cardinalities: "
                + ", ".join(cardinality_values)
                + ". Allowed pairings: "
                + ", ".join(pairing_values)
                + ". See the contract ports section for the per-port rules."
            ),
            "value_type": "object",
            "editor": "json",
            "group": "bindings",
            "level": "advanced",
            "order": 10,
        }
    )
    return fields


def build_editor_manifest_v4(
    *,
    registry: ExecutionRegistry | None = None,
    schema: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build the V4 editor manifest, verifying pointers against the schema.

    Parameters
    ----------
    registry :
        Execution registry the choice lists are read from. Defaults to the
        shared default registry.
    schema :
        Workflow JSON Schema the pointers are verified against. Defaults to
        the live V4 schema; tests pass a tampered copy to prove that a
        schema change without a manifest update fails.

    Raises
    ------
    ValueError
        Raised when a field pointer does not resolve against *schema*.
    """
    active = registry if registry is not None else default_registry()
    live = schema if schema is not None else build_workflow_json_schema(active)
    fields = _v4_fields(active)
    verify_manifest_fields(fields, live)
    return {
        "schema": EDITOR_MANIFEST_SCHEMA,
        "workflow_schema_version": SCHEMA_ID,
        "step_contexts": {
            "calc": "calc",
            "confgen": "confgen",
            "transform": "transform",
            "analysis": "analysis",
            "binding": "binding",
        },
        "fields": copy.deepcopy(fields),
    }


def editor_manifest_sha256_v4() -> str:
    """Return the canonical SHA-256 of the V4 editor manifest document."""
    return canonical_sha256(build_editor_manifest_v4())


__all__ = [
    "build_editor_manifest_v4",
    "editor_manifest_sha256_v4",
    "pointer_resolves",
    "verify_manifest_fields",
]
