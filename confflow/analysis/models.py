#!/usr/bin/env python3

"""Analysis runtime models for ConfFlow Workflow V4 (V4-6).

This module owns the frozen V4-6 analysis data contracts:

- :class:`ReactionGroup`: one reaction triple (transition state plus native
  forward/reverse path endpoints) discovered by grouping.  It is a plain
  pairing record, never a domain graph node: grouping keys on
  ``group_key`` plus subject ids plus endpoint roles, never on list
  position, file names, or parse order.
- :class:`AnalysisDefinition`: the explicit per-step analysis request
  (capability kind, params, energy-model seam object, endpoint
  assignment, partial policy).  Forward/reverse directions never become
  reactant/product without an explicit typed ``endpoint_assignment``;
  the default assignment is ``"unassigned"`` and conflicting
  assignments fail closed with a typed error.
- :class:`AnalysisInputs`: typed executor inputs (structures and results
  by port, plus the definition).
- :class:`ComputedGroup`: per-group energy-math outcome returned across
  the compute seam (structural twin of agent B's ``ReactionAnalysis``).
- :class:`AnalysisStepResult`: the executor outcome (usually no new
  structures: analysis projects results, it rarely mints structures).
- :class:`AnalysisError`: the single typed failure for this runtime,
  carrying a machine-readable ``code``.

Failure codes
-------------
Grouping/assignment failures (fail-closed, surfaced as diagnostics or
raised errors):

- ``analysis_ts_missing``: no transition-state candidate parents both
  endpoints.
- ``analysis_endpoint_missing``: a forward/reverse endpoint is absent.
- ``analysis_endpoint_ambiguous``: several structures claim one
  endpoint slot; never resolved by lowest-energy-wins.
- ``analysis_group_ambiguous``: several transition-state candidates, or
  a structure that cannot be placed in any group.
- ``analysis_subject_mismatch``: a result's subject matches no known
  structure.
- ``analysis_assignment_conflict``: both endpoints claim the same
  chemistry role.

Wiring failures (raised, never returned as results):

- ``analysis_unknown_kind``: the definition kind names no registered
  analysis capability.
- ``analysis_invalid_definition``: a definition/model invariant is
  violated (empty kind, bad assignment value, bad policy, unusable
  energy-model seam object).
- ``analysis_compute_failed``: the energy-model seam raised or returned
  garbage; the group fails closed instead of propagating the crash.

Only ``confflow.domain`` plus stdlib are imported here.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Final

from ..domain._immutable import FrozenDict
from ..domain.artifact import ArtifactSet
from ..domain.binding import PartialConsumption
from ..domain.canonical import typed_digest
from ..domain.diagnostics import Diagnostic, DiagnosticSeverity
from ..domain.errors import DomainError
from ..domain.result import ResultSet, ScientificResult
from ..domain.structure import StructureSet

__all__ = [
    "ANALYSIS_DEFINITION_CODES",
    "ANALYSIS_DEFINITION_DIGEST_KIND",
    "ANALYSIS_FAILURE_CODES",
    "ASSIGNMENT_EXPLICIT",
    "ASSIGNMENT_UNASSIGNED",
    "CHEMISTRY_ROLES",
    "ENDPOINT_FORWARD",
    "ENDPOINT_REVERSE",
    "ENDPOINT_SLOTS",
    "GROUP_ASSIGNMENTS",
    "AnalysisDefinition",
    "AnalysisError",
    "AnalysisInputs",
    "AnalysisStepResult",
    "ComputedGroup",
    "ReactionGroup",
]

#: Digest domain marker for :meth:`AnalysisDefinition.semantic_digest`.
ANALYSIS_DEFINITION_DIGEST_KIND: Final[str] = "confflow.analysis.definition.v1"

#: Default group assignment: no chemistry claim was provided.
ASSIGNMENT_UNASSIGNED: Final[str] = "unassigned"

#: Group assignment marker: the definition carried an explicit mapping.
ASSIGNMENT_EXPLICIT: Final[str] = "explicit"

#: Allowed values of :attr:`ReactionGroup.assignment`.
GROUP_ASSIGNMENTS: Final[tuple[str, ...]] = (ASSIGNMENT_UNASSIGNED, ASSIGNMENT_EXPLICIT)

#: Native path direction slot: the forward IRC/NEB endpoint.
ENDPOINT_FORWARD: Final[str] = "forward"

#: Native path direction slot: the reverse IRC/NEB endpoint.
ENDPOINT_REVERSE: Final[str] = "reverse"

#: Endpoint slots keyed by :attr:`AnalysisDefinition.endpoint_assignment`.
ENDPOINT_SLOTS: Final[tuple[str, ...]] = (ENDPOINT_FORWARD, ENDPOINT_REVERSE)

#: Chemistry roles an endpoint slot may be assigned to.
CHEMISTRY_ROLES: Final[tuple[str, ...]] = ("reactant", "product", ASSIGNMENT_UNASSIGNED)

#: Fail-closed grouping/assignment failure codes.
ANALYSIS_FAILURE_CODES: Final[tuple[str, ...]] = (
    "analysis_ts_missing",
    "analysis_endpoint_missing",
    "analysis_endpoint_ambiguous",
    "analysis_group_ambiguous",
    "analysis_subject_mismatch",
    "analysis_assignment_conflict",
)

#: Wiring failure codes (raised, never computed through).
ANALYSIS_DEFINITION_CODES: Final[tuple[str, ...]] = (
    "analysis_unknown_kind",
    "analysis_invalid_definition",
    "analysis_compute_failed",
)


class AnalysisError(DomainError):
    """Typed failure for the V4-6 analysis runtime.

    Parameters
    ----------
    code : str
        Machine-readable failure code (see the module docstring).
    message : str
        Human-readable message; never parsed by callers.
    subject_structure_id : str or None
        Structure the failure belongs to, when known.
    group_key : str or None
        Reaction group the failure belongs to, when known.
    details : Mapping or None
        Additional machine-readable detail (always carries ``reason``).
    """

    def __init__(
        self,
        code: str,
        message: str,
        *,
        subject_structure_id: str | None = None,
        group_key: str | None = None,
        details: Mapping[str, Any] | None = None,
    ) -> None:
        """Build the error, validating the code eagerly."""
        if not isinstance(code, str) or not code.strip():
            raise DomainError("analysis error code must be a non-empty string")
        if not isinstance(message, str) or not message.strip():
            raise DomainError("analysis error message must be a non-empty string")
        super().__init__(message)
        self.code = code
        self.message = message
        self.subject_structure_id = subject_structure_id
        self.group_key = group_key
        self.details = dict(details or {})

    def to_diagnostic(
        self,
        *,
        severity: DiagnosticSeverity = DiagnosticSeverity.ERROR,
        step_id: str | None = None,
    ) -> Diagnostic:
        """Return this failure as a structured diagnostic.

        Parameters
        ----------
        severity : DiagnosticSeverity
            Diagnostic severity; defaults to ``ERROR``.
        step_id : str or None
            Analysis step id carried for routing, when known.

        Returns
        -------
        Diagnostic
            The failure with ``code``, group, subject, and details set.
        """
        payload: dict[str, Any] = dict(self.details)
        payload.setdefault("reason", self.code)
        if self.group_key is not None:
            payload.setdefault("group_key", self.group_key)
        if self.subject_structure_id is not None:
            payload.setdefault("subject_structure_id", self.subject_structure_id)
        return Diagnostic(
            code=self.code,
            message=self.message,
            severity=severity,
            step_id=step_id,
            details=FrozenDict(payload),
        )


def _require_identifier(value: Any, field_name: str) -> str:
    """Return *value* when it is a usable identifier, else raise typed."""
    if not isinstance(value, str) or not value:
        raise AnalysisError(
            "analysis_invalid_definition",
            f"{field_name} must be a non-empty string",
            details={"reason": "empty_identifier", "field": field_name},
        )
    if value != value.strip():
        raise AnalysisError(
            "analysis_invalid_definition",
            f"{field_name} must not have surrounding whitespace",
            details={"reason": "padded_identifier", "field": field_name},
        )
    return value


@dataclass(frozen=True, slots=True)
class ReactionGroup:
    """One reaction triple discovered by grouping.

    A group pairs a transition-state structure with its two native path
    endpoints by shared ``group_key``, subject ids, and endpoint roles.
    It is never a domain graph node and never orders members: ``forward``
    and ``reverse`` are native path directions only, and chemistry roles
    (reactant/product) appear nowhere on this record.

    Parameters
    ----------
    group_key : str
        Reaction pairing key shared by the triple.
    ts_structure_id : str or None
        Transition-state subject id; ``None`` when missing/ambiguous.
    forward_endpoint_id : str or None
        Forward endpoint subject id; ``None`` when missing/ambiguous.
    reverse_endpoint_id : str or None
        Reverse endpoint subject id; ``None`` when missing/ambiguous.
    source_result_ids : tuple[str, ...]
        Sorted result ``value_digest`` strings attached by subject; empty
        when no result names a triple subject.
    assignment : str
        ``"unassigned"`` (grouping default: no chemistry claim) or
        ``"explicit"`` (the executor applied an explicit definition
        mapping).  Never a chemistry role itself.
    diagnostics : tuple[Diagnostic, ...]
        Per-group typed diagnostics; any error marks the group failed.

    Raises
    ------
    AnalysisError
        With code ``analysis_invalid_definition`` when an invariant is
        violated.
    """

    group_key: str
    ts_structure_id: str | None
    forward_endpoint_id: str | None
    reverse_endpoint_id: str | None
    source_result_ids: tuple[str, ...] = ()
    assignment: str = ASSIGNMENT_UNASSIGNED
    diagnostics: tuple[Diagnostic, ...] = ()

    def __post_init__(self) -> None:
        """Validate every invariant explicitly."""
        _require_identifier(self.group_key, "group_key")
        for name in ("ts_structure_id", "forward_endpoint_id", "reverse_endpoint_id"):
            value = getattr(self, name)
            if value is not None:
                _require_identifier(value, name)
        sources = tuple(self.source_result_ids)
        for index, digest in enumerate(sources):
            _require_identifier(digest, f"source_result_ids[{index}]")
        object.__setattr__(self, "source_result_ids", sources)
        if self.assignment not in GROUP_ASSIGNMENTS:
            raise AnalysisError(
                "analysis_invalid_definition",
                f"assignment must be one of {list(GROUP_ASSIGNMENTS)}, got {self.assignment!r}",
                group_key=self.group_key,
                details={"reason": "invalid_assignment", "assignment": self.assignment},
            )
        diagnostics = tuple(self.diagnostics)
        for item in diagnostics:
            if not isinstance(item, Diagnostic):
                raise AnalysisError(
                    "analysis_invalid_definition",
                    "group diagnostics must be Diagnostic records",
                    group_key=self.group_key,
                    details={"reason": "invalid_diagnostic"},
                )
        object.__setattr__(self, "diagnostics", diagnostics)

    @property
    def is_complete(self) -> bool:
        """Return whether all three subject ids are present."""
        return (
            self.ts_structure_id is not None
            and self.forward_endpoint_id is not None
            and self.reverse_endpoint_id is not None
        )

    @property
    def ok(self) -> bool:
        """Return whether the group carries no error diagnostics."""
        return not [item for item in self.diagnostics if item.is_error]

    def subject_ids(self) -> tuple[str, ...]:
        """Return the present triple subject ids in sorted order."""
        return tuple(
            sorted(
                subject
                for subject in (
                    self.ts_structure_id,
                    self.forward_endpoint_id,
                    self.reverse_endpoint_id,
                )
                if subject is not None
            )
        )

    def to_dict(self) -> dict[str, Any]:
        """Return a canonical, JSON-compatible representation."""
        return {
            "group_key": self.group_key,
            "ts_structure_id": self.ts_structure_id,
            "forward_endpoint_id": self.forward_endpoint_id,
            "reverse_endpoint_id": self.reverse_endpoint_id,
            "source_result_ids": list(self.source_result_ids),
            "assignment": self.assignment,
            "diagnostics": [
                {
                    "code": item.code,
                    "severity": item.severity.value,
                    "message": item.message,
                    "details": item.details.thaw(),
                }
                for item in self.diagnostics
            ],
        }


@dataclass(frozen=True, slots=True)
class AnalysisDefinition:
    """An explicit per-step analysis request.

    Parameters
    ----------
    kind : str
        Analysis capability name (for example ``"reaction_profile"``);
        must name a registered capability or execution fails closed.
    energy_model : object
        Opaque energy-policy object owned by the energy workstream.  The
        analysis runtime never interprets it; it only requires the
        compute seam (``compute(group, lookup, *, analysis_step_id,
        endpoint_assignment)`` plus ``to_dict()``) documented on
        ``confflow.analysis.executor.EnergyModel``.  Main-agent wiring
        passes the concrete model here.
    params : FrozenDict
        Capability params (theory selectors, thresholds); never silently
        defaulted by the executor.
    endpoint_assignment : FrozenDict
        Explicit chemistry assignment keyed by endpoint slot:
        ``{"forward": "reactant"|"product"|"unassigned", "reverse": ...}``.
        Missing slots default to ``"unassigned"``; both slots claiming
        the same chemistry role raises ``analysis_assignment_conflict``.
    partial_policy : PartialConsumption
        ``REQUIRE_COMPLETE`` (default: any failed group fails the step
        with no computed results) or ``ACCEPT_SUBSET`` (failed groups
        are omitted with diagnostics; complete groups still emit).

    Raises
    ------
    AnalysisError
        With code ``analysis_assignment_conflict`` on conflicting
        assignments, or ``analysis_invalid_definition`` on any other
        shape violation.
    """

    kind: str
    energy_model: Any
    params: FrozenDict = field(default_factory=FrozenDict)
    endpoint_assignment: FrozenDict = field(
        default_factory=lambda: FrozenDict(
            {ENDPOINT_FORWARD: ASSIGNMENT_UNASSIGNED, ENDPOINT_REVERSE: ASSIGNMENT_UNASSIGNED}
        )
    )
    partial_policy: PartialConsumption = PartialConsumption.REQUIRE_COMPLETE

    def __post_init__(self) -> None:
        """Validate kind, seam object, assignment, and policy explicitly."""
        _require_identifier(self.kind, "kind")
        model = self.energy_model
        if (
            model is None
            or not callable(getattr(model, "compute", None))
            or not callable(getattr(model, "to_dict", None))
        ):
            raise AnalysisError(
                "analysis_invalid_definition",
                "energy_model must expose compute(group, lookup, *, ...) and to_dict()",
                details={"reason": "invalid_energy_model"},
            )
        if not isinstance(self.params, FrozenDict):
            object.__setattr__(self, "params", FrozenDict(dict(self.params)))
        raw = self.endpoint_assignment
        if raw is None:
            mapping: dict[str, Any] = {}
        elif isinstance(raw, Mapping):
            mapping = dict(raw)
        else:
            raise AnalysisError(
                "analysis_invalid_definition",
                "endpoint_assignment must be a mapping of endpoint slot to role",
                details={"reason": "invalid_assignment_shape"},
            )
        for slot in mapping:
            if slot not in ENDPOINT_SLOTS:
                raise AnalysisError(
                    "analysis_invalid_definition",
                    f"endpoint_assignment slot must be one of {list(ENDPOINT_SLOTS)}, got {slot!r}",
                    details={"reason": "unknown_endpoint", "slot": slot},
                )
        resolved = {slot: mapping.get(slot, ASSIGNMENT_UNASSIGNED) for slot in ENDPOINT_SLOTS}
        for slot, role in resolved.items():
            if role not in CHEMISTRY_ROLES:
                raise AnalysisError(
                    "analysis_invalid_definition",
                    f"endpoint_assignment[{slot!r}] must be one of "
                    f"{list(CHEMISTRY_ROLES)}, got {role!r}",
                    details={"reason": "invalid_role", "slot": slot, "role": role},
                )
        forward_role = resolved[ENDPOINT_FORWARD]
        reverse_role = resolved[ENDPOINT_REVERSE]
        if forward_role != ASSIGNMENT_UNASSIGNED and forward_role == reverse_role:
            raise AnalysisError(
                "analysis_assignment_conflict",
                f"forward and reverse endpoints both claim {forward_role!r}: "
                "chemistry assignment is ambiguous",
                details={
                    "reason": "conflicting_assignment",
                    "endpoint_assignment": dict(resolved),
                },
            )
        object.__setattr__(self, "endpoint_assignment", FrozenDict(resolved))
        policy = self.partial_policy
        if isinstance(policy, str) and not isinstance(policy, PartialConsumption):
            try:
                policy = PartialConsumption(policy)
            except ValueError:
                raise AnalysisError(
                    "analysis_invalid_definition",
                    f"partial_policy must be one of "
                    f"{[item.value for item in PartialConsumption]}, got {policy!r}",
                    details={"reason": "invalid_partial_policy", "policy": policy},
                ) from None
        if not isinstance(policy, PartialConsumption):
            raise AnalysisError(
                "analysis_invalid_definition",
                "partial_policy must be a PartialConsumption",
                details={"reason": "invalid_partial_policy"},
            )
        object.__setattr__(self, "partial_policy", policy)

    @property
    def assignment(self) -> str:
        """Return ``"explicit"`` when a chemistry claim was made, else ``"unassigned"``."""
        if any(role != ASSIGNMENT_UNASSIGNED for role in self.endpoint_assignment.values()):
            return ASSIGNMENT_EXPLICIT
        return ASSIGNMENT_UNASSIGNED

    def to_dict(self) -> dict[str, Any]:
        """Return a canonical, JSON-compatible representation."""
        model = self.energy_model
        describe = getattr(model, "to_dict", None)
        energy_payload = describe() if callable(describe) else repr(model)
        return {
            "kind": self.kind,
            "params": self.params.thaw(),
            "energy_model": energy_payload,
            "endpoint_assignment": dict(self.endpoint_assignment),
            "partial_policy": self.partial_policy.value,
        }

    def semantic_digest(self) -> str:
        """Return the content digest controlling analysis reuse identity."""
        return typed_digest(ANALYSIS_DEFINITION_DIGEST_KIND, self.to_dict())


@dataclass(frozen=True, slots=True)
class AnalysisInputs:
    """Typed inputs of one analysis step execution, grouped by port.

    Parameters
    ----------
    structures : FrozenDict
        Port name to :class:`StructureSet` (subject structures).
    results : FrozenDict
        Port name to :class:`ResultSet` (source values by subject).
    definition : AnalysisDefinition
        The explicit analysis request applied to these inputs.

    Raises
    ------
    AnalysisError
        With code ``analysis_invalid_definition`` when a port carries
        the wrong collection type or the definition is missing.
    """

    structures: FrozenDict = field(default_factory=FrozenDict)
    results: FrozenDict = field(default_factory=FrozenDict)
    definition: AnalysisDefinition | None = None

    def __post_init__(self) -> None:
        """Validate port collections and the definition explicitly."""
        structures = self.structures
        if not isinstance(structures, FrozenDict):
            object.__setattr__(self, "structures", FrozenDict(dict(structures)))
            structures = self.structures
        for port, value in structures.items():
            if not isinstance(value, StructureSet):
                raise AnalysisError(
                    "analysis_invalid_definition",
                    f"structure port {port!r} must hold a StructureSet",
                    details={"reason": "invalid_input_port", "port": port},
                )
        results = self.results
        if not isinstance(results, FrozenDict):
            object.__setattr__(self, "results", FrozenDict(dict(results)))
            results = self.results
        for port, value in results.items():
            if not isinstance(value, ResultSet):
                raise AnalysisError(
                    "analysis_invalid_definition",
                    f"result port {port!r} must hold a ResultSet",
                    details={"reason": "invalid_input_port", "port": port},
                )
        if not isinstance(self.definition, AnalysisDefinition):
            raise AnalysisError(
                "analysis_invalid_definition",
                "analysis inputs require an AnalysisDefinition",
                details={"reason": "missing_definition"},
            )


@dataclass(frozen=True, slots=True)
class ComputedGroup:
    """Per-group energy-math outcome returned across the compute seam.

    This is the structural twin of agent B's ``ReactionAnalysis``: the
    concrete energy model maps that record field-for-field into this
    one so the executor never imports the energy workstream.

    Parameters
    ----------
    results : tuple[ScientificResult, ...]
        Computed results for the group (empty when it failed closed).
    diagnostics : tuple[Diagnostic, ...]
        Typed per-group diagnostics; any error marks the group failed.
    """

    results: tuple[ScientificResult, ...] = ()
    diagnostics: tuple[Diagnostic, ...] = ()

    def __post_init__(self) -> None:
        """Validate member types explicitly."""
        resolved = tuple(self.results)
        for item in resolved:
            if not isinstance(item, ScientificResult):
                raise AnalysisError(
                    "analysis_invalid_definition",
                    "computed group results must be ScientificResult records",
                    details={"reason": "invalid_computed_result"},
                )
        object.__setattr__(self, "results", resolved)
        diagnostics = tuple(self.diagnostics)
        for item in diagnostics:
            if not isinstance(item, Diagnostic):
                raise AnalysisError(
                    "analysis_invalid_definition",
                    "computed group diagnostics must be Diagnostic records",
                    details={"reason": "invalid_computed_diagnostic"},
                )
        object.__setattr__(self, "diagnostics", diagnostics)

    @property
    def ok(self) -> bool:
        """Return whether the group computed without error diagnostics."""
        return not [item for item in self.diagnostics if item.is_error]


@dataclass(frozen=True, slots=True)
class AnalysisStepResult:
    """The outcome of one analysis step execution.

    Analysis projects results: ``structures`` is usually empty because
    analysis consumes subject structures without minting new ones.

    Parameters
    ----------
    structures : StructureSet
        Structures minted by the analysis (usually empty).
    results : ResultSet
        Computed scientific results bound to group subjects.
    artifacts : ArtifactSet
        Report artifacts (empty unless the capability emits them).
    diagnostics : tuple[Diagnostic, ...]
        Structured diagnostics in deterministic order.

    Raises
    ------
    AnalysisError
        With code ``analysis_invalid_definition`` when a collection has
        the wrong type.
    """

    structures: StructureSet = field(default_factory=StructureSet)
    results: ResultSet = field(default_factory=ResultSet)
    artifacts: ArtifactSet = field(default_factory=ArtifactSet)
    diagnostics: tuple[Diagnostic, ...] = ()

    def __post_init__(self) -> None:
        """Validate collection types explicitly."""
        for name, expected in (
            ("structures", StructureSet),
            ("results", ResultSet),
            ("artifacts", ArtifactSet),
        ):
            if not isinstance(getattr(self, name), expected):
                raise AnalysisError(
                    "analysis_invalid_definition",
                    f"{name} must be a {expected.__name__}",
                    details={"reason": "invalid_step_result", "field": name},
                )
        diagnostics = tuple(self.diagnostics)
        for item in diagnostics:
            if not isinstance(item, Diagnostic):
                raise AnalysisError(
                    "analysis_invalid_definition",
                    "step diagnostics must be Diagnostic records",
                    details={"reason": "invalid_step_diagnostic"},
                )
        object.__setattr__(self, "diagnostics", diagnostics)

    @property
    def ok(self) -> bool:
        """Return whether the step carries no error diagnostics."""
        return not [item for item in self.diagnostics if item.is_error]

    def to_dict(self) -> dict[str, Any]:
        """Return a canonical, JSON-compatible representation."""
        return {
            "structures": [record.to_dict() for record in self.structures],
            "results": [record.to_dict() for record in self.results],
            "artifacts": [record.to_dict() for record in self.artifacts],
            "diagnostics": [
                {
                    "code": item.code,
                    "severity": item.severity.value,
                    "message": item.message,
                    "details": item.details.thaw(),
                }
                for item in self.diagnostics
            ],
        }
