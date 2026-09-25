#!/usr/bin/env python3

"""Canonical V4 workflow definition.

The canonical definition is a frozen, YAML-free, registry-free representation
of a workflow document: it carries stable step ids, typed bindings, scientific
definitions, and resolved run-level defaults.  Labels and annotations are kept
because humans need them, but they are presentation-only and excluded from
every digest.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from ...domain._immutable import FrozenDict
from ...domain.binding import BindingSet, Cardinality, Pairing, PortKind
from ...domain.completion import CompletionPolicy
from ...domain.errors import DomainError
from ...domain.resources import ResourceRequest, SchedulerPolicy
from ...domain.stochastic import validate_seed

__all__ = [
    "SCHEMA_ID",
    "RunInputDeclaration",
    "ScientificDefaults",
    "ScientificDefinition",
    "StepDefinition",
    "WorkflowDefinition",
    "require_identifier",
]

#: V4 workflow document schema identity.
SCHEMA_ID = "confflow.workflow.v4"

_IDENTIFIER_PATTERN = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,63}$")


def require_identifier(value: Any, field_name: str) -> str:
    """Validate a stable V4 identifier (step id or run input name).

    Raises
    ------
    DomainError
        Raised when *value* is not a stable identifier.
    """
    if not isinstance(value, str) or _IDENTIFIER_PATTERN.match(value) is None:
        raise DomainError(f"{field_name} must match [A-Za-z][A-Za-z0-9_]{{0,63}}, got {value!r}")
    return value


def _require_optional_text(value: Any, field_name: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value or value != value.strip():
        raise DomainError(f"{field_name} must be a non-empty, trimmed string or None")
    return value


@dataclass(frozen=True, slots=True)
class RunInputDeclaration:
    """A named run input a binding may reference as ``{run: <name>}``.

    Run inputs are named, never positional: the invocation boundary maps
    files or collections onto these names, so no filename can become identity.
    """

    name: str
    kind: PortKind
    cardinality: Cardinality
    pairing: Pairing | None = None
    role: str | None = None
    description: str | None = None

    def __post_init__(self) -> None:
        require_identifier(self.name, "run input name")
        if not isinstance(self.kind, PortKind):
            raise DomainError("run input kind must be a PortKind")
        if not isinstance(self.cardinality, Cardinality):
            raise DomainError("run input cardinality must be a Cardinality")
        if self.pairing is not None and not isinstance(self.pairing, Pairing):
            raise DomainError("run input pairing must be a Pairing or None")
        if self.role is not None:
            _require_optional_text(self.role, "run input role")
        _require_optional_text(self.description, "run input description")

    @property
    def effective_pairing(self) -> Pairing:
        """Return the declared pairing, or the kind's default pairing."""
        if self.pairing is not None:
            return self.pairing
        if self.kind is PortKind.STRUCTURE:
            return Pairing.PER_STRUCTURE
        return Pairing.BY_SUBJECT

    def to_dict(self) -> dict[str, Any]:
        """Return a canonical, JSON-compatible representation."""
        return {
            "name": self.name,
            "kind": self.kind.value,
            "cardinality": self.cardinality.value,
            "pairing": self.pairing.value if self.pairing is not None else None,
            "role": self.role,
            "description": self.description,
        }


def _validate_freeze(value: Any) -> tuple[int, ...] | None:
    if value is None:
        return None
    if isinstance(value, (str, bytes)) or not isinstance(value, (list, tuple)):
        raise DomainError("freeze must be a list of 1-based atom indices or None")
    indices: set[int] = set()
    for index in value:
        if isinstance(index, bool) or not isinstance(index, int):
            raise DomainError("freeze indices must be integers")
        if index < 1:
            raise DomainError("freeze indices are 1-based and must be >= 1")
        indices.add(index)
    return tuple(sorted(indices))


@dataclass(frozen=True, slots=True)
class ScientificDefaults:
    """Run-level explicit scientific defaults."""

    charge: int | None = None
    multiplicity: int | None = None
    freeze: tuple[int, ...] | None = None

    def __post_init__(self) -> None:
        if self.charge is not None:
            if isinstance(self.charge, bool) or not isinstance(self.charge, int):
                raise DomainError("scientific defaults charge must be an integer or None")
        if self.multiplicity is not None:
            if isinstance(self.multiplicity, bool) or not isinstance(self.multiplicity, int):
                raise DomainError("scientific defaults multiplicity must be an integer or None")
            if self.multiplicity < 1:
                raise DomainError("scientific defaults multiplicity must be >= 1")
        freeze = _validate_freeze(self.freeze)
        if freeze != self.freeze:
            object.__setattr__(self, "freeze", freeze)

    @property
    def has_entries(self) -> bool:
        """Return whether any explicit default is declared."""
        return self.charge is not None or self.multiplicity is not None or self.freeze is not None

    def to_payload(self) -> dict[str, Any]:
        """Return the digest contribution of these defaults."""
        return {
            "charge": self.charge,
            "multiplicity": self.multiplicity,
            "freeze": list(self.freeze) if self.freeze is not None else None,
        }

    def to_dict(self) -> dict[str, Any]:
        """Return a canonical, JSON-compatible representation."""
        return self.to_payload()


@dataclass(frozen=True, slots=True)
class ScientificDefinition:
    """Resolved scientific definition of a step.

    The fields carry only what changes what is computed.  ``label``,
    annotations, GUI placement, machine execution bindings, and scheduler
    width live elsewhere by construction.
    """

    program: str | None = None
    role: str | None = None
    execution_adapter: str | None = None
    result_profile: str | None = None
    native: FrozenDict = field(default_factory=FrozenDict)
    checks: tuple[str, ...] = ()
    recovery: str = "none"
    seed: int | None = None
    overrides: FrozenDict = field(default_factory=FrozenDict)
    transform: str | None = None

    def __post_init__(self) -> None:
        for name in ("program", "role", "execution_adapter", "result_profile", "transform"):
            _require_optional_text(getattr(self, name), name)
        if not isinstance(self.recovery, str) or not self.recovery:
            raise DomainError("recovery must be a non-empty string")
        checks = tuple(self.checks)
        for index, check in enumerate(checks):
            if not isinstance(check, str) or not check:
                raise DomainError(f"checks[{index}] must be a non-empty string")
        if len(set(checks)) != len(checks):
            raise DomainError("checks must not contain duplicates")
        object.__setattr__(self, "checks", checks)
        validate_seed(self.seed)
        if not isinstance(self.native, FrozenDict):
            object.__setattr__(self, "native", FrozenDict(self.native))
        if not isinstance(self.overrides, FrozenDict):
            object.__setattr__(self, "overrides", FrozenDict(self.overrides))
        # Charge, multiplicity, and freeze overrides are the only scientific
        # overrides with defined precedence semantics in V4-1; reject unknown
        # override keys early so a typo cannot silently do nothing.
        allowed = {"charge", "multiplicity", "freeze"}
        unknown = sorted(set(self.overrides) - allowed)
        if unknown:
            raise DomainError("unsupported scientific overrides: " + ", ".join(unknown))
        self._validate_override_types()

    def _validate_override_types(self) -> None:
        charge = self.overrides.get("charge")
        if charge is not None and (isinstance(charge, bool) or not isinstance(charge, int)):
            raise DomainError("override charge must be an integer")
        multiplicity = self.overrides.get("multiplicity")
        if multiplicity is not None:
            if isinstance(multiplicity, bool) or not isinstance(multiplicity, int):
                raise DomainError("override multiplicity must be an integer")
            if multiplicity < 1:
                raise DomainError("override multiplicity must be >= 1")
        if "freeze" in self.overrides:
            normalized = _validate_freeze(self.overrides.get("freeze"))
            if normalized != self.overrides.get("freeze"):
                merged = dict(self.overrides)
                merged["freeze"] = normalized
                object.__setattr__(self, "overrides", FrozenDict(merged))

    @property
    def effective_recovery(self) -> str:
        """Return the declared recovery profile name."""
        return self.recovery

    def to_payload(self) -> dict[str, Any]:
        """Return the science payload used by step semantic digests."""
        return {
            "program": self.program,
            "role": self.role,
            "execution_adapter": self.execution_adapter,
            "result_profile": self.result_profile,
            "native": dict(self.native),
            "checks": list(self.checks),
            "recovery": self.recovery,
            "seed": self.seed,
            "overrides": dict(self.overrides),
            "transform": self.transform,
        }

    def to_dict(self) -> dict[str, Any]:
        """Return a canonical, JSON-compatible representation."""
        return self.to_payload()


@dataclass(frozen=True, slots=True)
class StepDefinition:
    """A canonical workflow step definition."""

    id: str
    label: str | None = None
    enabled: bool = True
    executor: str = ""
    scientific: ScientificDefinition | None = None
    bindings: BindingSet = field(default_factory=BindingSet)
    resources: ResourceRequest = field(default_factory=ResourceRequest)
    scheduler: SchedulerPolicy = field(default_factory=SchedulerPolicy)
    completion: CompletionPolicy = field(default_factory=CompletionPolicy)
    execution: Any = None
    annotations: FrozenDict = field(default_factory=FrozenDict)

    def __post_init__(self) -> None:
        require_identifier(self.id, "step id")
        _require_optional_text(self.label, "step label")
        if not isinstance(self.enabled, bool):
            raise DomainError("step enabled must be a boolean")
        if not isinstance(self.executor, str) or not self.executor:
            raise DomainError("step executor must be a non-empty string")
        if self.scientific is not None and not isinstance(self.scientific, ScientificDefinition):
            raise DomainError("step scientific must be a ScientificDefinition or None")
        if not isinstance(self.bindings, BindingSet):
            raise DomainError("step bindings must be a BindingSet")
        if not isinstance(self.resources, ResourceRequest):
            raise DomainError("step resources must be a ResourceRequest")
        if not isinstance(self.scheduler, SchedulerPolicy):
            raise DomainError("step scheduler must be a SchedulerPolicy")
        if not isinstance(self.completion, CompletionPolicy):
            raise DomainError("step completion must be a CompletionPolicy")
        if not isinstance(self.annotations, FrozenDict):
            object.__setattr__(self, "annotations", FrozenDict(self.annotations))

    def to_dict(self) -> dict[str, Any]:
        """Return a canonical, JSON-compatible representation."""
        return {
            "id": self.id,
            "label": self.label,
            "enabled": self.enabled,
            "executor": self.executor,
            "scientific": self.scientific.to_dict() if self.scientific else None,
            "bindings": [binding.to_dict() for binding in self.bindings],
            "resources": self.resources.to_dict(),
            "scheduler": self.scheduler.to_dict(),
            "completion": self.completion.to_dict(),
            "annotations": self.annotations.thaw(),
        }


@dataclass(frozen=True, slots=True)
class WorkflowDefinition:
    """A canonical, validated-shape V4 workflow definition."""

    steps: tuple[StepDefinition, ...] = ()
    inputs: tuple[RunInputDeclaration, ...] = ()
    scientific_defaults: ScientificDefaults = field(default_factory=ScientificDefaults)
    resources: ResourceRequest = field(default_factory=ResourceRequest)
    scheduler: SchedulerPolicy = field(default_factory=SchedulerPolicy)
    schema: str = SCHEMA_ID

    def __post_init__(self) -> None:
        if self.schema != SCHEMA_ID:
            raise DomainError(f"unsupported workflow schema {self.schema!r}")
        steps = tuple(self.steps)
        for step in steps:
            if not isinstance(step, StepDefinition):
                raise DomainError("steps members must be StepDefinition")
        object.__setattr__(self, "steps", steps)
        inputs = tuple(self.inputs)
        for declaration in inputs:
            if not isinstance(declaration, RunInputDeclaration):
                raise DomainError("inputs members must be RunInputDeclaration")
        object.__setattr__(self, "inputs", inputs)
        if not isinstance(self.scientific_defaults, ScientificDefaults):
            raise DomainError("scientific_defaults must be a ScientificDefaults")
        if not isinstance(self.resources, ResourceRequest):
            raise DomainError("resources must be a ResourceRequest")
        if not isinstance(self.scheduler, SchedulerPolicy):
            raise DomainError("scheduler must be a SchedulerPolicy")

    @property
    def step_ids(self) -> tuple[str, ...]:
        """Return step ids in document order."""
        return tuple(step.id for step in self.steps)

    @property
    def input_names(self) -> tuple[str, ...]:
        """Return run input names in document order."""
        return tuple(declaration.name for declaration in self.inputs)

    def step(self, step_id: str) -> StepDefinition | None:
        """Return the step definition with *step_id*, or ``None``."""
        for step in self.steps:
            if step.id == step_id:
                return step
        return None

    def input_declaration(self, name: str) -> RunInputDeclaration | None:
        """Return the run input declaration named *name*, or ``None``."""
        for declaration in self.inputs:
            if declaration.name == name:
                return declaration
        return None

    def to_dict(self) -> dict[str, Any]:
        """Return a canonical, JSON-compatible representation."""
        return {
            "schema": self.schema,
            "inputs": [declaration.to_dict() for declaration in self.inputs],
            "scientific_defaults": self.scientific_defaults.to_dict(),
            "resources": self.resources.to_dict(),
            "scheduler": self.scheduler.to_dict(),
            "steps": [step.to_dict() for step in self.steps],
        }
