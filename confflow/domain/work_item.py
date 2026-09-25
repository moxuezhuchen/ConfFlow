#!/usr/bin/env python3

"""V4 work item model.

A work item is the smallest executable unit of a V4 run.  Three identities are
deliberately distinct:

- ``WorkItem.id`` is the deterministic instance address
  (``wi:<step id>:<logical key>``); it is the persistence key.
- ``WorkItem.logical_key`` is the logical address of the item
  (``<step id>:<grouping key>``); it stays stable across recompilation when
  logical inputs are unchanged.
- ``WorkItem.semantic_digest`` is the content identity that controls reuse
  validity; a changed scientific input moves the digest but not necessarily
  the logical key.

Results carry structures, results, and artifacts, plus diagnostics, timing,
error, and recovery metadata.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from typing import Any

from ._immutable import FrozenDict
from .artifact import ArtifactSet
from .canonical import typed_digest
from .completion import WorkItemStatus
from .diagnostics import Diagnostic, DiagnosticSeverity
from .errors import InvalidWorkItemError
from .resources import ResourceRequest
from .result import ResultSet
from .structure import StructureSet

__all__ = [
    "ResultError",
    "RecoveryInfo",
    "Timing",
    "WorkItem",
    "WorkItemInputs",
    "WorkItemResult",
    "make_work_item_id",
]

_DIGEST_PATTERN = re.compile(r"^sha256:[0-9a-f]{64}$")

#: Digest domain marker for :attr:`WorkItem.semantic_digest` construction.
WORK_ITEM_DIGEST_KIND = "confflow.work_item.v1"


def _require_identifier(value: Any, field_name: str) -> str:
    if not isinstance(value, str) or not value:
        raise InvalidWorkItemError(f"{field_name} must be a non-empty string")
    if value != value.strip():
        raise InvalidWorkItemError(f"{field_name} must not have surrounding whitespace")
    return value


def _require_digest(value: Any, field_name: str) -> str:
    if not isinstance(value, str) or _DIGEST_PATTERN.match(value) is None:
        raise InvalidWorkItemError(f"{field_name} must be a sha256:<hex> digest")
    return value


def make_work_item_id(logical_key: str) -> str:
    """Return the deterministic instance id for a work-item logical key.

    The id is the persistence address of the item; it is derived from the
    logical key so a recompilation can never mint a random identity.
    """
    _require_identifier(logical_key, "logical_key")
    return f"wi:{logical_key}"


@dataclass(frozen=True, slots=True)
class WorkItemInputs:
    """Named inputs of a work item, grouped by port kind.

    Each mapping is keyed by the binding's target port name.  Values are the
    domain collections resolved for this specific item.
    """

    structures: FrozenDict = field(default_factory=FrozenDict)
    artifacts: FrozenDict = field(default_factory=FrozenDict)
    results: FrozenDict = field(default_factory=FrozenDict)

    def __post_init__(self) -> None:
        for name, expected in (
            ("structures", StructureSet),
            ("artifacts", ArtifactSet),
            ("results", ResultSet),
        ):
            mapping = getattr(self, name)
            if not isinstance(mapping, FrozenDict):
                object.__setattr__(self, name, FrozenDict(mapping))
                mapping = getattr(self, name)
            for port, value in mapping.items():
                if not isinstance(value, expected):
                    raise InvalidWorkItemError(
                        f"input port {port!r} must hold {expected.__name__}, "
                        f"got {type(value).__name__}"
                    )

    @property
    def ports(self) -> tuple[str, ...]:
        """Return all input port names in sorted order."""
        return tuple(sorted(set(self.structures) | set(self.artifacts) | set(self.results)))

    @property
    def is_empty(self) -> bool:
        """Return whether the item has no named inputs."""
        return not (self.structures or self.artifacts or self.results)

    def structure_set(self, port: str) -> StructureSet:
        """Return the structure set bound to *port*.

        Raises
        ------
        KeyError
            Raised when *port* is not a structure input of this item.
        """
        value = self.structures[port]
        if not isinstance(value, StructureSet):
            raise KeyError(port)
        return value

    def artifact_set(self, port: str) -> ArtifactSet:
        """Return the artifact set bound to *port*.

        Raises
        ------
        KeyError
            Raised when *port* is not an artifact input of this item.
        """
        value = self.artifacts[port]
        if not isinstance(value, ArtifactSet):
            raise KeyError(port)
        return value

    def result_set(self, port: str) -> ResultSet:
        """Return the result set bound to *port*.

        Raises
        ------
        KeyError
            Raised when *port* is not a result input of this item.
        """
        value = self.results[port]
        if not isinstance(value, ResultSet):
            raise KeyError(port)
        return value

    def to_dict(self) -> dict[str, Any]:
        """Return a canonical, JSON-compatible representation."""
        return {
            "structures": {
                port: [record.to_dict() for record in value]  # type: ignore[union-attr]
                for port, value in self.structures.items()
            },
            "artifacts": {
                port: [record.to_dict() for record in value]  # type: ignore[union-attr]
                for port, value in self.artifacts.items()
            },
            "results": {
                port: [record.to_dict() for record in value]  # type: ignore[union-attr]
                for port, value in self.results.items()
            },
        }


@dataclass(frozen=True, slots=True)
class WorkItem:
    """An immutable, deterministic unit of execution.

    Parameters
    ----------
    id : str
        Instance address; use :func:`make_work_item_id`.
    logical_key : str
        Stable logical address within the plan.
    step_id : str
        Step this item belongs to.
    named_inputs : WorkItemInputs
        Resolved per-item inputs, keyed by port.
    resources : ResourceRequest
        Fully resolved per-item resources.
    semantic_digest : str
        Content identity controlling reuse validity.
    ordinal : int
        Deterministic ordering index within the step (``>= 0``).
    group_key : str | None
        Grouping key used for pairing.
    lineage_root_id : str | None
        Lineage root of the driving structure, when applicable.
    execution_binding_id : str | None
        Machine execution binding selected for this item.
    metadata : FrozenDict
        Non-semantic annotations.

    Raises
    ------
    InvalidWorkItemError
        Raised when any invariant is violated.
    """

    id: str
    logical_key: str
    step_id: str
    named_inputs: WorkItemInputs
    resources: ResourceRequest
    semantic_digest: str
    ordinal: int = 0
    group_key: str | None = None
    lineage_root_id: str | None = None
    execution_binding_id: str | None = None
    metadata: FrozenDict = field(default_factory=FrozenDict)

    def __post_init__(self) -> None:
        _require_identifier(self.id, "id")
        _require_identifier(self.logical_key, "logical_key")
        _require_identifier(self.step_id, "step_id")
        if self.id != make_work_item_id(self.logical_key):
            raise InvalidWorkItemError(
                "work item id must be the deterministic address wi:<logical_key>"
            )
        if not isinstance(self.named_inputs, WorkItemInputs):
            raise InvalidWorkItemError("named_inputs must be a WorkItemInputs")
        if not isinstance(self.resources, ResourceRequest):
            raise InvalidWorkItemError("resources must be a ResourceRequest")
        if not self.resources.is_resolved:
            raise InvalidWorkItemError("work item resources must be fully resolved before assembly")
        _require_digest(self.semantic_digest, "semantic_digest")
        if isinstance(self.ordinal, bool) or not isinstance(self.ordinal, int):
            raise InvalidWorkItemError("ordinal must be an integer")
        if self.ordinal < 0:
            raise InvalidWorkItemError("ordinal must be >= 0")
        for name in ("group_key", "lineage_root_id", "execution_binding_id"):
            value = getattr(self, name)
            if value is not None:
                _require_identifier(value, name)
        if not isinstance(self.metadata, FrozenDict):
            object.__setattr__(self, "metadata", FrozenDict(self.metadata))

    def to_dict(self) -> dict[str, Any]:
        """Return a canonical, JSON-compatible representation."""
        return {
            "id": self.id,
            "logical_key": self.logical_key,
            "step_id": self.step_id,
            "named_inputs": self.named_inputs.to_dict(),
            "resources": self.resources.to_dict(),
            "semantic_digest": self.semantic_digest,
            "ordinal": self.ordinal,
            "group_key": self.group_key,
            "lineage_root_id": self.lineage_root_id,
            "execution_binding_id": self.execution_binding_id,
            "metadata": self.metadata.thaw(),
        }


@dataclass(frozen=True, slots=True)
class Timing:
    """Wall-clock timing of a work item execution."""

    started_at: float | None = None
    finished_at: float | None = None
    duration_seconds: float | None = None

    def __post_init__(self) -> None:
        for name in ("started_at", "finished_at", "duration_seconds"):
            value = getattr(self, name)
            if value is None:
                continue
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise InvalidWorkItemError(f"timing.{name} must be a finite number or None")
            number = float(value)
            if not math.isfinite(number):
                raise InvalidWorkItemError(f"timing.{name} must be finite")
            object.__setattr__(self, name, number)
        if (
            self.started_at is not None
            and self.finished_at is not None
            and self.finished_at < self.started_at
        ):
            raise InvalidWorkItemError("timing.finished_at must not precede started_at")

    def to_dict(self) -> dict[str, Any]:
        """Return a canonical, JSON-compatible representation."""
        return {
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "duration_seconds": self.duration_seconds,
        }


@dataclass(frozen=True, slots=True)
class ResultError:
    """Structured failure information for a work item."""

    code: str
    message: str
    retryable: bool = False
    details: FrozenDict = field(default_factory=FrozenDict)

    def __post_init__(self) -> None:
        _require_identifier(self.code, "error code")
        _require_identifier(self.message, "error message")
        if not isinstance(self.retryable, bool):
            raise InvalidWorkItemError("error.retryable must be a boolean")
        if not isinstance(self.details, FrozenDict):
            object.__setattr__(self, "details", FrozenDict(self.details))

    def to_dict(self) -> dict[str, Any]:
        """Return a canonical, JSON-compatible representation."""
        return {
            "code": self.code,
            "message": self.message,
            "retryable": self.retryable,
            "details": self.details.thaw(),
        }


@dataclass(frozen=True, slots=True)
class RecoveryInfo:
    """Recovery metadata for a work item."""

    profile: str
    attempted: bool = False
    succeeded: bool | None = None
    details: FrozenDict = field(default_factory=FrozenDict)

    def __post_init__(self) -> None:
        _require_identifier(self.profile, "recovery profile")
        if not isinstance(self.attempted, bool):
            raise InvalidWorkItemError("recovery.attempted must be a boolean")
        if self.succeeded is not None and not isinstance(self.succeeded, bool):
            raise InvalidWorkItemError("recovery.succeeded must be a boolean or None")
        if not isinstance(self.details, FrozenDict):
            object.__setattr__(self, "details", FrozenDict(self.details))

    def to_dict(self) -> dict[str, Any]:
        """Return a canonical, JSON-compatible representation."""
        return {
            "profile": self.profile,
            "attempted": self.attempted,
            "succeeded": self.succeeded,
            "details": self.details.thaw(),
        }


@dataclass(frozen=True, slots=True)
class WorkItemResult:
    """The outcome of one work item.

    Parameters
    ----------
    work_item_id : str
        Id of the producing work item.
    status : WorkItemStatus
        Terminal status of the item.
    structures : StructureSet
        Structures produced by the item.
    results : ResultSet
        Scientific results produced by the item.
    artifacts : ArtifactSet
        Artifacts produced by the item.
    diagnostics : tuple[Diagnostic, ...]
        Structured diagnostics emitted during execution.
    timing : Timing | None
        Timing information.
    error : ResultError | None
        Failure information; required for ``failed`` items.
    recovery : RecoveryInfo | None
        Recovery metadata, when recovery was attempted.
    semantic_digest : str | None
        Semantic digest of the work item this result refers to.
    metadata : FrozenDict
        Non-semantic annotations.

    Raises
    ------
    InvalidWorkItemError
        Raised when the result contradicts its status.
    """

    work_item_id: str
    status: WorkItemStatus
    structures: StructureSet = field(default_factory=StructureSet)
    results: ResultSet = field(default_factory=ResultSet)
    artifacts: ArtifactSet = field(default_factory=ArtifactSet)
    diagnostics: tuple[Diagnostic, ...] = ()
    timing: Timing | None = None
    error: ResultError | None = None
    recovery: RecoveryInfo | None = None
    semantic_digest: str | None = None
    metadata: FrozenDict = field(default_factory=FrozenDict)

    def __post_init__(self) -> None:
        _require_identifier(self.work_item_id, "work_item_id")
        if not isinstance(self.status, WorkItemStatus):
            raise InvalidWorkItemError("status must be a WorkItemStatus")
        for name, expected in (
            ("structures", StructureSet),
            ("results", ResultSet),
            ("artifacts", ArtifactSet),
        ):
            if not isinstance(getattr(self, name), expected):
                raise InvalidWorkItemError(f"{name} must be a {expected.__name__}")
        object.__setattr__(self, "diagnostics", tuple(self.diagnostics))
        if not isinstance(self.metadata, FrozenDict):
            object.__setattr__(self, "metadata", FrozenDict(self.metadata))
        if self.semantic_digest is not None:
            _require_digest(self.semantic_digest, "semantic_digest")
        if self.status is WorkItemStatus.COMPLETED and self.error is not None:
            raise InvalidWorkItemError("a completed work item must not carry an error")
        if self.status is WorkItemStatus.FAILED:
            has_error_diagnostic = any(
                diagnostic.severity is DiagnosticSeverity.ERROR for diagnostic in self.diagnostics
            )
            if self.error is None and not has_error_diagnostic:
                raise InvalidWorkItemError(
                    "a failed work item requires an error or an error diagnostic"
                )

    @property
    def is_completed(self) -> bool:
        """Return whether the item completed successfully."""
        return self.status is WorkItemStatus.COMPLETED

    def to_dict(self) -> dict[str, Any]:
        """Return a canonical, JSON-compatible representation."""
        return {
            "work_item_id": self.work_item_id,
            "status": self.status.value,
            "structures": [record.to_dict() for record in self.structures],
            "results": [record.to_dict() for record in self.results],
            "artifacts": [record.to_dict() for record in self.artifacts],
            "diagnostics": [
                {
                    "code": item.code,
                    "severity": item.severity.value,
                    "message": item.message,
                    "step_id": item.step_id,
                    "work_item_id": item.work_item_id,
                    "logical_key": item.logical_key,
                    "field_path": item.field_path,
                    "details": item.details.thaw(),
                }
                for item in self.diagnostics
            ],
            "timing": self.timing.to_dict() if self.timing is not None else None,
            "error": self.error.to_dict() if self.error is not None else None,
            "recovery": self.recovery.to_dict() if self.recovery is not None else None,
            "semantic_digest": self.semantic_digest,
            "metadata": self.metadata.thaw(),
        }


def work_item_semantic_digest(
    *,
    step_semantic_digest: str,
    input_payload: Any,
    resources: ResourceRequest,
) -> str:
    """Compute the content digest that controls work-item reuse.

    The logical key is deliberately excluded: reuse validity is a function of
    the step science, the resolved input content, and resources.  Two items
    with identical content are interchangeable for reuse even when they carry
    different logical addresses (for example after re-importing the same
    geometry under new entity ids).
    """
    return typed_digest(
        WORK_ITEM_DIGEST_KIND,
        {
            "step_semantic_digest": step_semantic_digest,
            "inputs": input_payload,
            "resources": resources.to_dict(),
        },
    )
