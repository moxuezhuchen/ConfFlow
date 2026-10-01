#!/usr/bin/env python3

"""Producer authoring seam (``confflow.authoring.v4``).

Four operations turn the frozen authoring wire into thin projections over the
existing V4 authority:

- :func:`describe_step` -- step projection: contracts and per-port facts from
  the execution registry, current bindings, declared-vs-absent resources and
  scheduler fields, capability identity, diagnostics;
- :func:`binding_candidates` -- legal binding sources for one explicit target
  port (named run inputs and enabled step outputs whose contract port kind
  matches), plus the conservative ``auto_wire`` decision;
- :func:`instantiate_card` -- build one new step from a scientific snapshot and
  explicit binding choices, allocate a fresh legal step id when needed, then
  validate the resulting document through the producer validator;
- :func:`validate_document` -- thin wrapper over
  :func:`confflow.producer.validation.validate_workflow_bytes`.

No rule table lives here.  Port contracts, pairing/cardinality legality,
capability vocabulary, adapter selection, cycle detection, and every validation
outcome come from the V4 registry, parser, validation authority, and compiler;
this module only queries them and projects the answers into the frozen
response envelope (``authoring_protocol_schema()``).

Request channel note: the published request schema keeps ``parameters`` open,
so the two envelope-level payloads the operations need -- an explicit editor
``action`` for the auto-wire decision and the ``snapshot`` of a card to
instantiate -- ride inside ``parameters``.  No published request or response
member is added, removed, or changed, and no scientific interpretation happens
in this layer.
"""

from __future__ import annotations

import copy
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from functools import lru_cache
from typing import Any

from jsonschema import Draft202012Validator

from ..domain._immutable import FrozenDict
from ..domain.binding import (
    Binding,
    BindingSource,
    Pairing,
    SelectorKind,
    SourceKind,
)
from ..domain.canonical import canonical_json_bytes, canonical_sha256
from ..domain.diagnostics import (
    Diagnostic,
    DiagnosticSeverity,
)
from ..domain.errors import DomainError
from ..domain.resources import ResourceRequest, SchedulerPolicy
from ..execution.contracts import (
    ExecutionAdapterSpec,
    ExecutorCapability,
    ExecutorContract,
    PortSpec,
)
from ..execution.registry import ExecutionRegistry, default_registry
from ..workflow.v4.compiler import compile_workflow
from ..workflow.v4.diagnostics import DiagnosticCode, DiagnosticReason
from ..workflow.v4.document import (
    SCHEMA_ID,
    StepDefinition,
    WorkflowDefinition,
    require_identifier,
)
from ..workflow.v4.parser import (
    WorkflowYamlError,
    parse_workflow_document,
    parse_workflow_text,
)
from ..workflow.v4.schema import ResourcesModel, SchedulerModel
from ..workflow.v4.validation import (
    _resolve_run_policy,
    _validate_step,
    resolve_executor_contract,
    resolve_step_input_ports,
    run_input_port_spec,
)
from .boundary import AUTHORING_PROTOCOL_SCHEMA, authoring_protocol_schema
from .contract import _port_dict
from .validation import _diagnostic_dict, validate_workflow_bytes

__all__ = [
    "AUTHORING_OPERATIONS",
    "binding_candidates",
    "describe_step",
    "dispatch_request",
    "instantiate_card",
    "validate_document",
]

#: The operation enum published by the frozen request schema (single source).
AUTHORING_OPERATIONS: tuple[str, ...] = tuple(
    str(item) for item in authoring_protocol_schema()["request"]["properties"]["operation"]["enum"]
)

#: Editor actions that make the auto-wire decision explicit.  Auto-wire is
#: never inferred from a passive query.
_AUTO_WIRE_ACTIONS = frozenset({"add", "continue", "insert"})

#: Edge diagnostics whose reason an explicit binding declaration can resolve
#: (a selector role, a partial-consumption choice); everything else that the
#: compiler rejects at the binding path refuses the candidate.
_DECLARATION_FIXABLE_REASONS = frozenset(
    {
        DiagnosticReason.PARTIAL_CONSUMPTION_UNDEFINED.value,
        DiagnosticReason.ROLE_REQUIRED.value,
    }
)

#: Authoring-level reason codes carried in ``details["reason"]``.  They are
#: additive producer vocabulary for this seam; the boundary only requires
#: stable machine codes, never a closed list.
_REASON_INVALID_REQUEST = "invalid_request"
_REASON_DOCUMENT_UNPARSEABLE = "document_unparseable"
_REASON_UNKNOWN_STEP = "unknown_step"
_REASON_UNKNOWN_TARGET_PORT = "unknown_target_port"
_REASON_UNSUPPORTED_OPERATION = "unsupported_operation"
_REASON_STALE_DOCUMENT = "stale_document"
_REASON_SNAPSHOT_INVALID = "snapshot_invalid"
_REASON_BINDING_CHOICE_INVALID = "binding_choice_invalid"

#: Binding-wire members a normalized choice may carry.
_BINDING_CHOICE_KEYS = ("source", "cardinality", "pairing", "partial_consumption")
#: Source-wire members accepted in a bare source shorthand.
_SOURCE_KEYS = ("run", "step", "port", "select")


# ----------------------------------------------------------------------
# Response envelope
# ----------------------------------------------------------------------


@lru_cache(maxsize=1)
def _capability_identity_cached() -> dict[str, Any]:
    """Return the registry-derived capability identity (built once)."""
    import confflow

    from .contract import build_configuration_contract_v4

    envelope = build_configuration_contract_v4(producer_version=confflow.__version__)
    return dict(envelope["boundary"]["capability_identity"])


def _capability_identity() -> dict[str, Any]:
    """Return a fresh copy of the producer's capability identity."""
    return copy.deepcopy(_capability_identity_cached())


def _diagnostic(
    severity: DiagnosticSeverity,
    code: str,
    reason: str,
    message: str,
    *,
    step_id: str | None = None,
    field_path: str | None = None,
    details: Mapping[str, Any] | None = None,
) -> Diagnostic:
    """Build one authoring diagnostic with a stable reason code."""
    merged: dict[str, Any] = {"reason": reason}
    if details:
        merged.update(details)
    return Diagnostic(
        code=code,
        message=message,
        severity=severity,
        step_id=step_id,
        field_path=field_path,
        details=FrozenDict(merged),
    )


def _schema_problem(
    message: str,
    *,
    step_id: str | None = None,
    field_path: str | None = None,
    reason: str = _REASON_INVALID_REQUEST,
) -> Diagnostic:
    """Return a schema_error diagnostic for a bad authoring request."""
    return _diagnostic(
        DiagnosticSeverity.ERROR,
        DiagnosticCode.SCHEMA_ERROR.value,
        reason,
        message,
        step_id=step_id,
        field_path=field_path,
    )


def _stale_warning(expected: str | None, actual: str | None) -> Diagnostic:
    """Return the warning raised when a request's document digest is stale."""
    return _diagnostic(
        DiagnosticSeverity.WARNING,
        DiagnosticCode.SCHEMA_ERROR.value,
        _REASON_STALE_DOCUMENT,
        (
            "the request document digest "
            f"{expected!r} does not match the document now ({actual!r}); no auto-wire"
        ),
        field_path="<document>",
    )


def _diagnostic_key(item: Diagnostic | Mapping[str, Any]) -> tuple[str, str, str, str, str]:
    """Return a deterministic ordering key for envelope diagnostics."""
    if isinstance(item, Diagnostic):
        return (
            item.severity.value,
            item.code,
            item.step_id or "",
            item.field_path or "",
            item.message,
        )
    return (
        str(item.get("severity") or ""),
        str(item.get("code") or ""),
        str(item.get("step_id") or ""),
        str(item.get("field_path") or ""),
        str(item.get("message") or ""),
    )


def _diagnostic_wire(item: Diagnostic | Mapping[str, Any]) -> dict[str, Any]:
    """Return the wire form of a Diagnostic or an already-wire diagnostic."""
    if isinstance(item, Diagnostic):
        return _diagnostic_dict(item)
    return dict(item)


def _envelope(
    operation: str | None,
    ok: bool,
    request_document_digest: str | None,
    result: Any,
    diagnostics: Sequence[Diagnostic | Mapping[str, Any]],
) -> dict[str, Any]:
    """Build the frozen authoring response envelope."""
    return {
        "content_schema": AUTHORING_PROTOCOL_SCHEMA,
        "operation": operation,
        "ok": ok,
        "capability_identity": _capability_identity(),
        "request_document_digest": request_document_digest,
        "result": result,
        "diagnostics": [
            _diagnostic_wire(item) for item in sorted(diagnostics, key=_diagnostic_key)
        ],
    }


# ----------------------------------------------------------------------
# Request document helpers
# ----------------------------------------------------------------------


def _coerce_document(document: Any) -> tuple[Mapping[str, Any] | None, list[Diagnostic]]:
    """Coerce a request document into a mapping, or explain why not."""
    if isinstance(document, Mapping):
        return document, []
    if isinstance(document, (bytes, bytearray)):
        try:
            text = bytes(document).decode("utf-8")
        except UnicodeDecodeError as exc:
            return None, [
                _schema_problem(
                    f"the document bytes are not valid UTF-8: {exc}",
                    reason=_REASON_DOCUMENT_UNPARSEABLE,
                )
            ]
        return _coerce_document(text)
    if isinstance(document, str):
        try:
            return parse_workflow_text(document), []
        except WorkflowYamlError as exc:
            return None, [_schema_problem(str(exc), reason=_REASON_DOCUMENT_UNPARSEABLE)]
    return None, [_schema_problem("a document mapping (or YAML/JSON text) is required")]


def _document_digest(document: Mapping[str, Any] | None) -> str | None:
    """Return the JCS digest of a request document, or ``None``."""
    if document is None:
        return None
    try:
        return "sha256:" + canonical_sha256(document)
    except DomainError:
        return None


def _document_bytes(document: Mapping[str, Any]) -> tuple[bytes | None, list[Diagnostic]]:
    """Return the canonical JSON bytes of a request document."""
    try:
        return canonical_json_bytes(document), []
    except DomainError as exc:
        return None, [_schema_problem(f"the document cannot be canonicalized: {exc}")]


def _raw_step(document: Mapping[str, Any], step_id: str) -> Mapping[str, Any]:
    """Return the raw declared mapping of one step, or an empty mapping."""
    steps = document.get("steps")
    if isinstance(steps, (list, tuple)):
        for step in steps:
            if isinstance(step, Mapping) and step.get("id") == step_id:
                return step
    return {}


# ----------------------------------------------------------------------
# Authority-resolved step facts
# ----------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class _StepFacts:
    """Registry/validation facts of one step, without re-deriving any rule."""

    definition: StepDefinition
    capability: ExecutorCapability | None
    contract: ExecutorContract | None
    adapter: ExecutionAdapterSpec | None
    input_ports: tuple[PortSpec, ...]
    output_ports: tuple[PortSpec, ...]
    profile_name: str | None
    diagnostics: tuple[Diagnostic, ...] = ()


def _unique_diagnostics(items: Sequence[Diagnostic]) -> list[Diagnostic]:
    """Deduplicate diagnostics by their stable machine fields."""
    seen: set[tuple[str, str, str, str, str]] = set()
    unique: list[Diagnostic] = []
    for item in items:
        key = (
            item.code,
            item.step_id or "",
            item.field_path or "",
            item.message,
            str(item.details.get("reason")),
        )
        if key in seen:
            continue
        seen.add(key)
        unique.append(item)
    return unique


def _resolve_step_facts(
    definition: WorkflowDefinition,
    step: StepDefinition,
    *,
    registry: ExecutionRegistry,
) -> _StepFacts:
    """Resolve one step's contracts, ports, and profile from the authority.

    The full semantic validation of the step is authoritative whenever it can
    resolve; otherwise the shared contract/port resolution helpers supply the
    port facts so a partially authored step can still be projected.  No port,
    pairing, or adapter rule is re-implemented here.
    """
    run_resources, run_scheduler = _resolve_run_policy(definition)
    validated, diagnostics = _validate_step(
        step,
        run_resources,
        run_scheduler,
        registry,
        definition.scientific_defaults,
        input_declarations={item.name: item for item in definition.inputs},
        all_steps=tuple(definition.steps),
    )
    if validated is not None:
        return _StepFacts(
            definition=step,
            capability=validated.executor.capability,
            contract=validated.executor,
            adapter=validated.adapter,
            input_ports=validated.input_ports,
            output_ports=validated.output_ports,
            profile_name=validated.profile.name,
            diagnostics=tuple(diagnostics),
        )
    capability, contract, contract_diagnostics = resolve_executor_contract(step, registry)
    diagnostics.extend(contract_diagnostics)
    if capability is None or contract is None:
        return _StepFacts(
            definition=step,
            capability=capability,
            contract=contract,
            adapter=None,
            input_ports=(),
            output_ports=(),
            profile_name=None,
            diagnostics=tuple(_unique_diagnostics(diagnostics)),
        )
    if step.scientific is None:
        return _StepFacts(
            definition=step,
            capability=capability,
            contract=contract,
            adapter=None,
            input_ports=(),
            output_ports=contract.output_ports,
            profile_name=None,
            diagnostics=tuple(_unique_diagnostics(diagnostics)),
        )
    adapter, ports, port_diagnostics = resolve_step_input_ports(
        step, capability, contract, registry
    )
    diagnostics.extend(port_diagnostics)
    profile = registry.find_profile(step.scientific.result_profile or "standard")
    return _StepFacts(
        definition=step,
        capability=capability,
        contract=contract,
        adapter=adapter,
        input_ports=tuple(ports) if ports is not None else (),
        output_ports=contract.output_ports,
        profile_name=profile.name if profile is not None else None,
        diagnostics=tuple(_unique_diagnostics(diagnostics)),
    )


def _all_step_facts(
    definition: WorkflowDefinition, *, registry: ExecutionRegistry
) -> dict[str, _StepFacts]:
    """Resolve every step's facts once, keyed by step id."""
    return {
        step.id: _resolve_step_facts(definition, step, registry=registry)
        for step in definition.steps
    }


def _is_step_diagnostic(item: Diagnostic, step_id: str) -> bool:
    """Return whether a diagnostic belongs to one step's projection."""
    if item.step_id == step_id:
        return True
    return item.step_id is None and (item.field_path or "").startswith(f"steps.{step_id}")


# ----------------------------------------------------------------------
# Wire projections (frozen binding wire only; no new vocabulary)
# ----------------------------------------------------------------------


def _source_wire(source: BindingSource) -> dict[str, Any]:
    """Project a binding source into the frozen ``source`` wire mapping."""
    if source.kind is SourceKind.RUN_INPUT:
        payload: dict[str, Any] = {"run": source.port}
    else:
        payload = {"step": source.step_id, "port": source.port}
    selector = source.selector
    if selector.kind is SelectorKind.ROLE:
        payload["select"] = {"role": selector.role}
    elif selector.kind is SelectorKind.IDS:
        payload["select"] = {"ids": list(selector.ids)}
    return payload


def _binding_wire(binding: Binding) -> dict[str, Any]:
    """Project a canonical binding into the frozen binding wire mapping."""
    payload: dict[str, Any] = {"source": _source_wire(binding.source)}
    if binding.cardinality is not None:
        payload["cardinality"] = binding.cardinality.value
    if binding.pairing is not None:
        payload["pairing"] = binding.pairing.value
    if binding.partial_consumption is not None:
        payload["partial_consumption"] = binding.partial_consumption.value
    return payload


def _presence(
    raw_block: Any,
    known_fields: Sequence[str],
    effective: Mapping[str, Any],
) -> dict[str, Any]:
    """Report declared/absent/effective fields for one presence block."""
    declared = dict(raw_block) if isinstance(raw_block, Mapping) else {}
    return {
        "declared": {name: declared[name] for name in known_fields if name in declared},
        "absent": [name for name in known_fields if name not in declared],
        "effective": dict(effective),
    }


# ----------------------------------------------------------------------
# describe_step
# ----------------------------------------------------------------------


def _project_step(
    raw_document: Mapping[str, Any],
    definition: WorkflowDefinition,
    step: StepDefinition,
    facts: _StepFacts,
    run_resources: ResourceRequest,
    run_scheduler: SchedulerPolicy,
) -> dict[str, Any]:
    """Project one step from its authority-resolved facts."""
    raw_step = _raw_step(raw_document, step.id)
    return {
        "step_id": step.id,
        "label": step.label,
        "enabled": step.enabled,
        "executor": step.executor,
        "capability": facts.capability.value if facts.capability is not None else None,
        "contract_version": (
            facts.contract.contract_version if facts.contract is not None else None
        ),
        "adapter": facts.adapter.name if facts.adapter is not None else None,
        "result_profile": facts.profile_name,
        "scientific": step.scientific.to_dict() if step.scientific is not None else None,
        "input_ports": [_port_dict(port) for port in facts.input_ports],
        "output_ports": [_port_dict(port) for port in facts.output_ports],
        "bindings": {binding.target_port: _binding_wire(binding) for binding in step.bindings},
        "resources": _presence(
            raw_step.get("resources"),
            tuple(ResourcesModel.model_fields),
            step.resources.with_defaults(run_resources).to_dict(),
        ),
        "scheduler": _presence(
            raw_step.get("scheduler"),
            tuple(SchedulerModel.model_fields),
            step.scheduler.with_defaults(run_scheduler).to_dict(),
        ),
        "completion": step.completion.to_dict(),
        "capability_identity": _capability_identity(),
        "diagnostics": [
            _diagnostic_wire(item)
            for item in facts.diagnostics
            if _is_step_diagnostic(item, step.id)
        ],
    }


def describe_step(
    document: Mapping[str, Any] | bytes | bytearray | str,
    step_id: Any,
    *,
    registry: ExecutionRegistry | None = None,
) -> dict[str, Any]:
    """Return the projection of one step over the V4 authority.

    The step must exist in a parse-clean document.  Contracts, port facts,
    bindings, and the resolved resource/scheduler policy are projected from
    the parser, execution registry, and validation authority; declared fields
    are reported as declared, and absent fields stay absent (never defaulted).
    """
    mapping, problems = _coerce_document(document)
    if mapping is None:
        return _envelope("describe_step", False, None, None, problems)
    digest = _document_digest(mapping)
    parsed = parse_workflow_document(mapping)
    if not parsed.ok or parsed.definition is None:
        return _envelope("describe_step", False, digest, None, list(parsed.diagnostics))
    definition = parsed.definition
    step = definition.step(step_id) if isinstance(step_id, str) else None
    if step is None:
        return _envelope(
            "describe_step",
            False,
            digest,
            None,
            [
                _schema_problem(
                    f"unknown step {step_id!r}",
                    step_id=step_id if isinstance(step_id, str) else None,
                    field_path="steps",
                    reason=_REASON_UNKNOWN_STEP,
                )
            ],
        )
    active = registry if registry is not None else default_registry()
    facts = _resolve_step_facts(definition, step, registry=active)
    run_resources, run_scheduler = _resolve_run_policy(definition)
    result = _project_step(mapping, definition, step, facts, run_resources, run_scheduler)
    return _envelope("describe_step", True, digest, result, list(facts.diagnostics))


# ----------------------------------------------------------------------
# binding_candidates
# ----------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class _CandidateSource:
    """One prospective source for the target port."""

    source: BindingSource
    source_kind: str
    run_input_grouping: str | None = None


@dataclass(frozen=True, slots=True)
class _CandidateEvaluation:
    """One evaluated candidate: wire payload plus auto-wire inputs."""

    payload: dict[str, Any]
    trial_ok: bool


def _declared_dependencies(definition: WorkflowDefinition) -> dict[str, tuple[str, ...]]:
    """Return the declared-bindings dependency adjacency.

    Mirrors the adjacency ``build_binding_graph`` derives from bindings (the
    compiler re-derives it for real when a trial document compiles).
    """
    dependencies: dict[str, list[str]] = {step.id: [] for step in definition.steps}
    for step in definition.steps:
        for binding in step.bindings:
            if binding.source.kind is SourceKind.STEP_OUTPUT and binding.source.step_id is not None:
                dependencies[step.id].append(binding.source.step_id)
    return {step_id: tuple(items) for step_id, items in dependencies.items()}


def _reaches(dependencies: Mapping[str, tuple[str, ...]], start: str, goal: str) -> bool:
    """Return whether *start* transitively depends on *goal*."""
    stack = [start]
    seen: set[str] = set()
    while stack:
        node = stack.pop()
        if node == goal:
            return True
        if node in seen:
            continue
        seen.add(node)
        stack.extend(dependencies.get(node, ()))
    return False


def _candidate_sources(
    definition: WorkflowDefinition,
    target_step_id: str,
    target_port: PortSpec,
    facts_by_id: Mapping[str, _StepFacts],
) -> list[_CandidateSource]:
    """Enumerate contract-compatible candidate sources for a target port."""
    sources: list[_CandidateSource] = []
    dependencies = _declared_dependencies(definition)
    for declaration in definition.inputs:
        port = run_input_port_spec(declaration)
        if port.kind is not target_port.kind:
            continue
        sources.append(
            _CandidateSource(
                source=BindingSource.run_input(declaration.name),
                source_kind="run_input",
                run_input_grouping=declaration.grouping,
            )
        )
    for step in definition.steps:
        if step.id == target_step_id or not step.enabled:
            continue
        facts = facts_by_id[step.id]
        if facts.contract is None:
            continue
        if _reaches(dependencies, step.id, target_step_id):
            continue
        for port in facts.output_ports:
            if port.kind is not target_port.kind:
                continue
            sources.append(
                _CandidateSource(
                    source=BindingSource.step_output(step.id, port.name),
                    source_kind="step_output",
                )
            )
    return sources


def _apply_binding(
    document: Mapping[str, Any],
    target_step_id: str,
    target_port: str,
    binding_wire: Mapping[str, Any],
) -> dict[str, Any]:
    """Return a copy of *document* with the candidate binding applied."""
    trial = copy.deepcopy(dict(document))
    steps = trial.get("steps")
    if not isinstance(steps, (list, tuple)):
        return trial
    materialized = [dict(item) if isinstance(item, Mapping) else item for item in steps]
    trial["steps"] = materialized
    for step in materialized:
        if isinstance(step, dict) and step.get("id") == target_step_id:
            bindings = step.get("bindings")
            if not isinstance(bindings, dict):
                bindings = {}
            bindings[target_port] = copy.deepcopy(dict(binding_wire))
            step["bindings"] = bindings
    return trial


def _is_edge_diagnostic(item: Diagnostic, target_step_id: str, target_port: str) -> bool:
    """Return whether a compiler diagnostic is the candidate edge's own."""
    return item.field_path == f"steps.{target_step_id}.bindings.{target_port}"


def _compatibility(ok: bool, edge_errors: Sequence[Diagnostic]) -> str:
    """Classify a candidate with the compatibility vocabulary."""
    if ok or not edge_errors:
        return "compatible"
    reasons = {str(item.details.get("reason")) for item in edge_errors}
    if reasons & _DECLARATION_FIXABLE_REASONS:
        return "needs_revalidation"
    return "unsupported"


def _effective_edge_facts(
    plan_graph: Any,
    binding: Binding,
    target_port: PortSpec,
) -> tuple[str, str]:
    """Return (effective cardinality, pairing) from the resolved graph.

    When the trial document compiles, the value comes from the real
    ``build_binding_graph`` edge; otherwise the edge default rule applies
    (binding override, else the target port contract).
    """
    if plan_graph is not None:
        for edge in plan_graph.edges:
            if (
                edge.target_step_id == binding.target_step_id
                and edge.target_port.name == binding.target_port
            ):
                return edge.cardinality.value, edge.pairing.value
    cardinality = binding.cardinality or target_port.cardinality
    pairing = binding.pairing or target_port.pairing
    return cardinality.value, pairing.value


def binding_candidates(
    document: Mapping[str, Any] | bytes | bytearray | str,
    target_step_id: Any,
    target_port: Any,
    *,
    expected_document_digest: str | None = None,
    action: str | None = None,
    registry: ExecutionRegistry | None = None,
) -> dict[str, Any]:
    """Return legal candidate sources for one explicit target port.

    Candidates are named run inputs and outputs of other enabled steps whose
    registry port contract kind matches the target port; disabled, unknown,
    self, and cycle-creating sources are excluded.  ``auto_wire`` is true only
    for the single candidate that satisfies every conservative condition: an
    explicit editor action, a fresh document digest, an explicit target port,
    no selector/pairing ambiguity or grouping guess, all required ports
    satisfiable, and a trial application that compiles.
    """
    mapping, problems = _coerce_document(document)
    if mapping is None:
        return _envelope("binding_candidates", False, None, None, problems)
    digest = _document_digest(mapping)
    response_diagnostics: list[Diagnostic | Mapping[str, Any]] = []
    if (
        expected_document_digest is not None
        and digest is not None
        and expected_document_digest != digest
    ):
        response_diagnostics.append(_stale_warning(expected_document_digest, digest))
    parsed = parse_workflow_document(mapping)
    if not parsed.ok or parsed.definition is None:
        return _envelope(
            "binding_candidates",
            False,
            digest,
            None,
            list(parsed.diagnostics) + response_diagnostics,
        )
    definition = parsed.definition
    target_step = definition.step(target_step_id) if isinstance(target_step_id, str) else None
    if target_step is None:
        response_diagnostics.append(
            _schema_problem(
                f"unknown step {target_step_id!r}",
                step_id=target_step_id if isinstance(target_step_id, str) else None,
                field_path="steps",
                reason=_REASON_UNKNOWN_STEP,
            )
        )
        return _envelope("binding_candidates", False, digest, None, response_diagnostics)
    active = registry if registry is not None else default_registry()
    facts_by_id = _all_step_facts(definition, registry=active)
    target_facts = facts_by_id[target_step.id]
    response_diagnostics.extend(target_facts.diagnostics)
    target_spec = (
        next(
            (port for port in target_facts.input_ports if port.name == target_port),
            None,
        )
        if isinstance(target_port, str)
        else None
    )
    if target_spec is None:
        response_diagnostics.append(
            _schema_problem(
                f"step {target_step.id!r} has no input port {target_port!r}",
                step_id=target_step.id,
                field_path=f"steps.{target_step.id}.bindings.{target_port}",
                reason=_REASON_UNKNOWN_TARGET_PORT,
            )
        )
        return _envelope("binding_candidates", False, digest, None, response_diagnostics)

    candidate_sources = _candidate_sources(definition, target_step.id, target_spec, facts_by_id)
    action_token = str(action).strip().lower() if action is not None else ""
    action_explicit = action_token in _AUTO_WIRE_ACTIONS
    fresh = (
        expected_document_digest is not None
        and digest is not None
        and expected_document_digest == digest
    )
    bound_ports = {binding.target_port for binding in target_step.bindings}
    bound_ports.add(target_spec.name)
    required_ports = {port.name for port in target_facts.input_ports if port.is_required}
    required_satisfied = required_ports <= bound_ports

    identity = _capability_identity()
    evaluations: list[tuple[_CandidateSource, Binding, Any, list[Diagnostic], str, str, str]] = []
    for candidate in candidate_sources:
        binding = Binding(
            target_step_id=target_step.id,
            target_port=target_spec.name,
            source=candidate.source,
        )
        binding_wire = {"source": _source_wire(candidate.source)}
        trial = _apply_binding(mapping, target_step.id, target_spec.name, binding_wire)
        compiled = compile_workflow(trial, registry=active)
        edge_errors = [
            item
            for item in compiled.diagnostics
            if _is_edge_diagnostic(item, target_step.id, target_spec.name)
        ]
        compatibility = _compatibility(compiled.ok, edge_errors)
        plan_graph = compiled.plan.graph if compiled.plan is not None else None
        effective_cardinality, pairing = _effective_edge_facts(plan_graph, binding, target_spec)
        evaluations.append(
            (
                candidate,
                binding,
                compiled,
                edge_errors,
                compatibility,
                effective_cardinality,
                pairing,
            )
        )

    complete_legal_count = sum(1 for item in evaluations if item[2].ok)
    results: list[dict[str, Any]] = []
    for (
        candidate,
        binding,
        compiled,
        edge_errors,
        compatibility,
        effective_cardinality,
        pairing,
    ) in evaluations:
        reasons: list[str] = []
        if not action_explicit:
            reasons.append("user_action_not_explicit")
        if not fresh:
            reasons.append("stale_document")
        if not compiled.ok:
            reasons.append("candidate_does_not_validate")
        if complete_legal_count != 1:
            reasons.append("source_not_unique")
        if compatibility != "compatible":
            reasons.append("selector_or_pairing_ambiguity")
        if (
            target_spec.pairing is Pairing.BY_GROUP_KEY
            and candidate.source_kind == "run_input"
            and candidate.run_input_grouping is None
        ):
            reasons.append("external_grouping_guess_required")
        if not required_satisfied:
            reasons.append("required_ports_unsatisfied")
        results.append(
            {
                "target_step_id": target_step.id,
                "target_port": target_spec.name,
                "source": _source_wire(candidate.source),
                "source_kind": candidate.source_kind,
                "source_step_id": binding.source.step_id,
                "source_port": binding.source.port,
                "effective_cardinality": effective_cardinality,
                "pairing": pairing,
                "compatibility": compatibility,
                "diagnostics": [_diagnostic_wire(item) for item in edge_errors],
                "request_document_digest": digest,
                "capability_identity": copy.deepcopy(identity),
                "auto_wire": not reasons,
                "auto_wire_reasons": reasons,
            }
        )
    return _envelope("binding_candidates", True, digest, results, response_diagnostics)


# ----------------------------------------------------------------------
# instantiate_card
# ----------------------------------------------------------------------


def _snapshot_step(
    snapshot: Any,
) -> tuple[dict[str, Any] | None, list[Diagnostic]]:
    """Extract one step mapping from a card snapshot, or explain why not."""
    data, problems = _coerce_document(snapshot)
    if data is None:
        return None, problems
    if "executor" in data:
        return copy.deepcopy(dict(data)), []
    card_step = data.get("step")
    if isinstance(card_step, Mapping) and "executor" in card_step:
        return copy.deepcopy(dict(card_step)), []
    document = data.get("document")
    if isinstance(document, Mapping):
        steps = document.get("steps")
        if (
            isinstance(steps, list)
            and len(steps) == 1
            and isinstance(steps[0], Mapping)
            and "executor" in steps[0]
        ):
            return copy.deepcopy(dict(steps[0])), []
    return None, [
        _schema_problem(
            "the snapshot must be a step mapping (or a card carrying one step)",
            reason=_REASON_SNAPSHOT_INVALID,
        )
    ]


def _allocate_step_id(preferred: Any, secondary: Any, existing: set[str]) -> tuple[str, bool]:
    """Return a fresh legal V4 step id and whether one had to be allocated.

    A legal requested id that is free is used as-is (``allocated=False``).
    Otherwise a legal id is derived from the requested id, then from the
    snapshot's own id, then from the fixed ``step`` stem, with a deterministic
    numeric suffix on collision.
    """
    base = "step"
    for candidate in (preferred, secondary):
        if candidate is None:
            continue
        try:
            require_identifier(candidate, "step id")
        except DomainError:
            continue
        base = candidate
        break
    if base not in existing:
        return base, base != preferred
    suffix = 2
    while True:
        suffix_text = f"_{suffix}"
        stem = base[: 64 - len(suffix_text)]
        allocated = f"{stem}{suffix_text}"
        if allocated not in existing:
            return allocated, True
        suffix += 1


def _binding_choice_wire(choice: Any) -> dict[str, Any] | None:
    """Normalize one binding choice into the frozen binding wire mapping."""
    if not isinstance(choice, Mapping):
        return None
    if "source" in choice:
        return {key: copy.deepcopy(choice[key]) for key in _BINDING_CHOICE_KEYS if key in choice}
    if any(key in choice for key in _SOURCE_KEYS):
        return {
            "source": {key: copy.deepcopy(choice[key]) for key in _SOURCE_KEYS if key in choice}
        }
    return None


def _build_instantiated_step(
    snapshot_step: Mapping[str, Any],
    step_id: str,
    binding_choices: Mapping[str, Any] | None,
) -> tuple[dict[str, Any] | None, list[Diagnostic]]:
    """Build the new step mapping from the snapshot payload and choices."""
    problems: list[Diagnostic] = []
    step = {
        key: copy.deepcopy(value)
        for key, value in snapshot_step.items()
        if key not in ("id", "bindings")
    }
    step["id"] = step_id
    bindings: dict[str, Any] = {}
    for port, choice in (binding_choices or {}).items():
        wire = _binding_choice_wire(choice)
        if wire is None:
            problems.append(
                _schema_problem(
                    f"binding choice for port {port!r} is not a binding source mapping",
                    field_path=f"steps.{step_id}.bindings.{port}",
                    reason=_REASON_BINDING_CHOICE_INVALID,
                )
            )
            continue
        bindings[str(port)] = wire
    step["bindings"] = bindings
    if problems:
        return None, problems
    return step, []


def instantiate_card(
    snapshot: Mapping[str, Any] | bytes | bytearray | str,
    requested_step_id: Any = None,
    document_context: Mapping[str, Any] | bytes | bytearray | str | None = None,
    binding_choices: Mapping[str, Any] | None = None,
    *,
    registry: ExecutionRegistry | None = None,
) -> dict[str, Any]:
    """Build one step from a card snapshot and validate the resulting document.

    The requested step id is used when it is legal and fresh; otherwise a
    unique legal V4 id is allocated deterministically.  The snapshot's
    scientific payload is copied verbatim (no scientific interpretation), the
    binding choices are normalized into the frozen binding wire, and the
    resulting document is validated through the producer validator.  ``ok``
    mirrors that validation.
    """
    context, context_problems = (
        _coerce_document(document_context) if document_context is not None else (None, [])
    )
    if document_context is not None and context is None:
        return _envelope("instantiate_card", False, None, None, context_problems)
    request_digest = _document_digest(context)
    if snapshot is None or (isinstance(snapshot, (str, bytes, bytearray)) and not snapshot):
        return _envelope(
            "instantiate_card",
            False,
            request_digest,
            None,
            [
                _schema_problem(
                    "a card snapshot is required",
                    reason=_REASON_SNAPSHOT_INVALID,
                )
            ],
        )
    snapshot_step, snapshot_problems = _snapshot_step(snapshot)
    if snapshot_step is None:
        return _envelope("instantiate_card", False, request_digest, None, snapshot_problems)
    if context is None:
        context = {"schema": SCHEMA_ID, "steps": []}
    context_steps = context.get("steps")
    if not isinstance(context_steps, (list, tuple)):
        context_steps = []
    existing = {
        str(step.get("id"))
        for step in context_steps
        if isinstance(step, Mapping) and step.get("id") is not None
    }
    preferred = requested_step_id
    if not isinstance(preferred, str) or not preferred:
        preferred = None
    step_id, allocated = _allocate_step_id(preferred, snapshot_step.get("id"), existing)
    step, choice_problems = _build_instantiated_step(snapshot_step, step_id, binding_choices)
    if step is None:
        return _envelope("instantiate_card", False, request_digest, None, choice_problems)
    document = copy.deepcopy(dict(context))
    if "schema" not in document:
        document["schema"] = SCHEMA_ID
    document_steps = document.get("steps")
    if not isinstance(document_steps, (list, tuple)):
        document_steps = []
    steps = [
        item
        for item in document_steps
        if not (isinstance(item, Mapping) and item.get("id") == step_id)
    ]
    steps.append(step)
    document["steps"] = steps
    document_bytes, byte_problems = _document_bytes(document)
    if document_bytes is None:
        return _envelope("instantiate_card", False, request_digest, None, byte_problems)
    report = validate_workflow_bytes(
        document_bytes, registry=registry if registry is not None else None
    )
    result = {
        "step": step,
        "step_id": step_id,
        "requested_step_id": requested_step_id,
        "allocated": allocated,
        "document": document,
        "document_digest": _document_digest(document),
        "definition_digest": report.definition_digest,
        "validation": report.to_dict(),
    }
    return _envelope(
        "instantiate_card", report.ok, request_digest, result, list(report.diagnostics)
    )


# ----------------------------------------------------------------------
# validate_document
# ----------------------------------------------------------------------


def _bytes_document_digest(data: bytes) -> str | None:
    """Return the JCS digest of parseable document bytes, or ``None``."""
    try:
        raw = parse_workflow_text(data.decode("utf-8"))
    except (UnicodeDecodeError, WorkflowYamlError):
        return None
    return _document_digest(raw)


def validate_document(
    document: Mapping[str, Any] | bytes | bytearray | str,
    *,
    registry: ExecutionRegistry | None = None,
) -> dict[str, Any]:
    """Validate a document through the producer validator (thin wrapper).

    Accepts the request's document mapping (serialized to canonical JSON) or
    exact text/bytes; the answer is the producer
    :class:`~confflow.producer.validation.ValidationReport` plus the frozen
    response envelope.
    """
    if isinstance(document, Mapping):
        data, problems = _document_bytes(document)
        if data is None:
            return _envelope("validate_document", False, None, None, problems)
    elif isinstance(document, (bytes, bytearray)):
        data = bytes(document)
    elif isinstance(document, str):
        data = document.encode("utf-8")
    else:
        return _envelope(
            "validate_document",
            False,
            None,
            None,
            [_schema_problem("a document mapping, JSON text, or bytes is required")],
        )
    report = validate_workflow_bytes(data, registry=registry)
    return _envelope(
        "validate_document",
        report.ok,
        _bytes_document_digest(data),
        report.to_dict(),
        list(report.diagnostics),
    )


# ----------------------------------------------------------------------
# Request dispatch (CLI seam)
# ----------------------------------------------------------------------


def _request_path(parts: Sequence[Any]) -> str:
    """Render a jsonschema error path as a dotted field path."""
    return ".".join(str(part) for part in parts) or "<request>"


def dispatch_request(data: bytes | bytearray | str) -> dict[str, Any]:
    """Parse and dispatch one ``confflow.authoring.v4`` request.

    The request is validated against the published request schema; malformed
    requests answer with a structured envelope and ``ok=False``.  Unknown or
    unimplemented operations (``check_compatibility`` is PR-1 out of scope)
    answer structured as well, never with a traceback.
    """
    try:
        text = bytes(data).decode("utf-8") if isinstance(data, (bytes, bytearray)) else str(data)
        request = json.loads(text)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        return _envelope(
            None,
            False,
            None,
            None,
            [_schema_problem(f"the authoring request is not valid JSON: {exc}")],
        )
    if not isinstance(request, Mapping):
        return _envelope(
            None,
            False,
            None,
            None,
            [_schema_problem("the authoring request must be a JSON object")],
        )
    operation = request.get("operation")
    if not isinstance(operation, str) or operation not in AUTHORING_OPERATIONS:
        return _envelope(
            None,
            False,
            None,
            None,
            [
                _schema_problem(
                    f"unknown authoring operation {operation!r}",
                    field_path="operation",
                    reason=_REASON_UNSUPPORTED_OPERATION,
                )
            ],
        )
    validator = Draft202012Validator(authoring_protocol_schema()["request"])
    schema_errors = sorted(
        validator.iter_errors(request), key=lambda item: list(item.absolute_path)
    )
    if schema_errors:
        return _envelope(
            operation,
            False,
            _document_digest(request),
            None,
            [
                _schema_problem(
                    f"invalid authoring request at {_request_path(item.absolute_path)}: "
                    f"{item.message}",
                    field_path=_request_path(item.absolute_path),
                )
                for item in schema_errors
            ],
        )
    parameters = request.get("parameters")
    params = dict(parameters) if isinstance(parameters, Mapping) else {}
    document = request.get("document")
    if operation == "describe_step":
        return describe_step(document, params.get("step_id"))
    if operation == "binding_candidates":
        return binding_candidates(
            document,
            params.get("target_step_id"),
            params.get("target_port"),
            expected_document_digest=request.get("document_digest"),
            action=params.get("action"),
        )
    if operation == "instantiate_card":
        return instantiate_card(
            params.get("snapshot"),
            params.get("requested_step_id"),
            document,
            params.get("binding_choices"),
        )
    if operation == "validate_document":
        return validate_document(document if document is not None else {})
    if operation == "compile_intent":
        intent = params.get("intent", document)
        profile = params.get("machine_profile")
        if not isinstance(intent, Mapping) or (
            profile is not None and not isinstance(profile, Mapping)
        ):
            return _envelope(
                operation,
                False,
                None,
                None,
                [_schema_problem("intent and machine_profile must be objects")],
            )
        from .intent import compile_intent

        try:
            resolved = compile_intent(intent, machine_profile=profile)
        except (ValueError, DomainError) as exc:
            return _envelope(
                operation,
                False,
                _document_digest(intent),
                None,
                [_schema_problem(str(exc), field_path="parameters.intent")],
            )
        return _envelope(operation, True, _document_digest(intent), {"document": resolved}, [])
    if operation == "preview_paths":
        from .path_preview import preview_paths_request

        try:
            preview = preview_paths_request(params)
        except (ValueError, DomainError) as exc:
            return _envelope(
                operation,
                False,
                None,
                None,
                [_schema_problem(str(exc), field_path="parameters.native")],
            )
        return _envelope(operation, True, None, preview, [])
    return _envelope(
        operation,
        False,
        _document_digest(request),
        None,
        [
            _schema_problem(
                f"operation {operation!r} is not implemented by the PR-1 authoring seam",
                field_path="operation",
                reason=_REASON_UNSUPPORTED_OPERATION,
            )
        ],
    )
