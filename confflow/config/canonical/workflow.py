#!/usr/bin/env python3

"""Canonical, fully-resolved workflow definition (IR).

This module is the semantic owner of workflow structure below the raw-mapping
boundary. It provides:

* the version-neutral value objects :class:`CanonicalWorkflowDefinition` and
  :class:`CanonicalStepDefinition`, whose dependency graph is already resolved,
  and
* the pure graph primitives (``build_step_graph`` / ``topo_order``) that resolve
  it.

The historical ``confflow.workflow.dag.explicit`` import path re-exports the two
primitives, so existing callers keep working unchanged. The V2 compatibility
adapter that turns a parsed :class:`~confflow.config.canonical.types.WorkflowConfig`
into this IR lives in :mod:`confflow.config.canonical.v2_adapter`; no module below
this boundary reads raw YAML to learn workflow semantics.

The IR intentionally keeps a minimal ``dependency_mode`` compatibility tag
(``implicit_linear`` vs ``explicit``): V2 treats a workflow whose steps all omit
``inputs`` as a document-order chain, while any declared ``inputs`` switches the
whole workflow to explicit-DAG semantics. That distinction must survive into the
V2 serialization / binding / resume identity even though the resolved graph is
equivalent storage for the engine.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from graphlib import CycleError, TopologicalSorter
from typing import Any, Literal

from ...core.exceptions import ConfFlowError
from .schema import WORKFLOW_SCHEMA_VERSION
from .types import GlobalOptions

__all__ = [
    "CanonicalStepDefinition",
    "CanonicalWorkflowDefinition",
    "DependencyMode",
    "build_step_graph",
    "canonical_step_name",
    "normalize_step_inputs",
    "topo_order",
]

DependencyMode = Literal["implicit_linear", "explicit"]


# ---------------------------------------------------------------------------
# Pure graph primitives (the historical workflow.dag.explicit implementation).
# ---------------------------------------------------------------------------
def _bool_step_token_error(value: bool, context: str) -> ConfFlowError:
    return ConfFlowError(
        f"workflow {context} {value!r} was parsed as a boolean. "
        "YAML interprets unquoted on/off/yes/no/true/false as booleans; "
        f'quote the step name, e.g. "{str(value).lower()}"'
    )


def canonical_step_name(step: dict[str, Any], index: int) -> str:
    raw_name = step.get("name")
    if isinstance(raw_name, bool):
        raise _bool_step_token_error(raw_name, f"step {index} name")
    if raw_name is not None:
        name = str(raw_name).strip()
        if name:
            return name
    return f"step_{index:02d}"


def normalize_step_inputs(raw_inputs: Any) -> list[str]:
    if raw_inputs is None:
        return []
    if isinstance(raw_inputs, bool):
        raise _bool_step_token_error(raw_inputs, "inputs entry")
    if isinstance(raw_inputs, str):
        value = raw_inputs.strip()
        return [value] if value else []
    if isinstance(raw_inputs, (list, tuple)):
        result: list[str] = []
        seen: set[str] = set()
        for item in raw_inputs:
            if item is None:
                continue
            if isinstance(item, bool):
                raise _bool_step_token_error(item, "inputs entry")
            value = str(item).strip()
            if value and value not in seen:
                seen.add(value)
                result.append(value)
        return result
    value = str(raw_inputs).strip()
    return [value] if value else []


def build_step_graph(
    steps: list[dict[str, Any]],
) -> tuple[dict[str, list[str]], dict[str, dict[str, Any]], dict[str, list[str]]]:
    """Build canonical step names and normalized predecessor lists.

    The returned predecessor map reflects only the ``inputs`` fields. The
    engine applies its legacy linear fallback when no step declares that
    field. A step without ``inputs`` is therefore a root whenever explicit
    DAG mode is selected by the engine.

    Returns
    -------
    predecessors : dict[str, list[str]]
        Canonical step name to declared predecessor names.
    by_name : dict[str, dict[str, Any]]
        Original step dictionaries indexed by canonical name.
    declared_inputs : dict[str, list[str]]
        Canonical step name to normalized ``inputs`` values.

    Raises
    ------
    ConfFlowError
        If names are duplicated or a predecessor is unknown.
    """
    predecessors: dict[str, list[str]] = {}
    by_name: dict[str, dict[str, Any]] = {}
    declared_inputs: dict[str, list[str]] = {}

    for index, step in enumerate(steps, start=1):
        name = canonical_step_name(step, index)
        if name in by_name:
            raise ConfFlowError(f"workflow step names must be unique; duplicate name: {name!r}")
        inputs = normalize_step_inputs(step.get("inputs"))
        by_name[name] = step
        predecessors[name] = list(inputs)
        declared_inputs[name] = list(inputs)

    known_names = set(by_name)
    for name, inputs in predecessors.items():
        unknown = [predecessor for predecessor in inputs if predecessor not in known_names]
        if unknown:
            raise ConfFlowError(
                f"workflow step {name!r} has unknown predecessor(s): {', '.join(map(repr, unknown))}"
            )

    return predecessors, by_name, declared_inputs


def topo_order(predecessors: dict[str, list[str]]) -> list[list[str]]:
    """Return deterministic topological waves for a predecessor map."""
    if not predecessors:
        return []

    known_names = set(predecessors)
    for name, inputs in predecessors.items():
        unknown = [predecessor for predecessor in inputs if predecessor not in known_names]
        if unknown:
            raise ConfFlowError(
                f"workflow step {name!r} has unknown predecessor(s): {', '.join(map(repr, unknown))}"
            )

    sorter = TopologicalSorter(predecessors)
    try:
        sorter.prepare()
    except CycleError as exc:
        raise ConfFlowError(f"workflow contains a dependency cycle: {exc}") from exc

    waves: list[list[str]] = []
    while sorter.is_active():
        ready = sorted(sorter.get_ready())
        if not ready:
            raise ConfFlowError("workflow topological ordering produced no ready steps")
        waves.append(ready)
        sorter.done(*ready)
    return waves


# ---------------------------------------------------------------------------
# Canonical value objects
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class CanonicalStepDefinition:
    """One fully-resolved workflow step.

    ``name`` is the canonical graph node identity (stripped, or the generated
    ``step_NN`` fallback). ``v2_name`` is the name the V2 typed model produced
    for the step, which can differ only by surrounding whitespace; it is kept so
    the V2 execution projection stays byte-identical. ``params`` is the step
    parameter mapping, ``predecessors`` the resolved, de-duplicated dependency
    names, and ``extensions`` any unknown step-level fields preserved verbatim.

    The remaining fields exist so the IR can also carry a future Workflow V3
    step; the V2 adapter leaves them at their defaults and never projects them
    into the V2 execution shape:

    * ``id`` — a stable V3 identity (``None`` for a V2-adapted step, whose
      identity remains ``name``; see :attr:`identity`);
    * ``label`` — the human-facing display name (V2 maps its ``name`` here);
    * ``checkpoint_from`` — a structured ``checkpoint: {from_step: <id>}``
      reference (V2 keeps its legacy ``params.chk_from_step`` untouched);
    * ``annotations`` — non-semantic, free-form metadata that never participates
      in execution or fingerprints.
    """

    name: str
    type: str
    enabled: bool
    params: dict[str, Any]
    predecessors: tuple[str, ...]
    inputs_declared: bool
    v2_name: str
    id: str | None = None
    label: str | None = None
    checkpoint_from: str | None = None
    raw_inputs: Any = None
    extensions: dict[str, Any] = field(default_factory=dict)
    annotations: dict[str, Any] = field(default_factory=dict)

    @property
    def identity(self) -> str:
        """Return the canonical identity: the stable id when present, else the name.

        A V2-adapted step has no ``id``, so its identity is the V2 ``name``; a
        future V3 step resolves to its stable ``id``. This is a model-layer
        accessor only — the V2 dirname / fingerprint / state identity stay
        name-based and do not consult it.
        """
        return self.id if self.id is not None else self.name

    def to_v2_step(self) -> dict[str, Any]:
        """Project this step back to the V2 execution mapping."""
        step: dict[str, Any] = {
            "name": self.v2_name,
            "type": self.type,
            "enabled": self.enabled,
            "params": dict(self.params),
        }
        if self.inputs_declared:
            step["inputs"] = self.raw_inputs
        return step


@dataclass(frozen=True)
class CanonicalWorkflowDefinition:
    """The resolved workflow, independent of how its YAML was spelled.

    ``source_version`` records where the definition came from (the workflow
    schema version id); ``annotations`` carries non-semantic, free-form document
    metadata. Both default to the V2 case so the V2 adapter keeps producing the
    exact same IR it always has.
    """

    global_config: dict[str, Any]
    global_options: GlobalOptions
    steps: tuple[CanonicalStepDefinition, ...]
    dependency_mode: DependencyMode
    predecessors: dict[str, tuple[str, ...]]
    execution_order: tuple[str, ...]
    terminal_steps: tuple[str, ...]
    extensions: dict[str, Any] = field(default_factory=dict)
    source_version: str = WORKFLOW_SCHEMA_VERSION
    annotations: dict[str, Any] = field(default_factory=dict)

    def to_v2_execution_shape(self) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        """Return ``(global_config, steps)`` in the exact V2 execution shape."""
        return self.global_config, [step.to_v2_step() for step in self.steps]

    def step_by_name(self) -> dict[str, CanonicalStepDefinition]:
        """Index the canonical steps by their graph identity."""
        return {step.name: step for step in self.steps}
