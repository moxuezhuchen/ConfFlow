#!/usr/bin/env python3

"""V4 step result model: the semantic truth of a completed step.

A :class:`StepResult` is assembled from accepted work-item results and
published atomically.  It carries typed structures, results, and artifacts —
never a result file path.  Exports (XYZ, CSV, JSON reports) are projections
computed from this model, not the model itself.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ._immutable import FrozenDict
from .artifact import ArtifactSet
from .completion import StepStatus
from .diagnostics import Diagnostic
from .errors import DomainError
from .result import ResultSet
from .structure import StructureSet
from .work_item import WorkItemResult

__all__ = [
    "StepProvenance",
    "StepResult",
]


def _require_identifier(value: Any, field_name: str) -> str:
    if not isinstance(value, str) or not value:
        raise DomainError(f"{field_name} must be a non-empty string")
    if value != value.strip():
        raise DomainError(f"{field_name} must not have surrounding whitespace")
    return value


@dataclass(frozen=True, slots=True)
class StepProvenance:
    """Compiler provenance recorded on a published step result."""

    workflow_definition_digest: str | None = None
    step_semantic_digest: str | None = None
    compiler_version: str | None = None
    metadata: FrozenDict = field(default_factory=FrozenDict)

    def __post_init__(self) -> None:
        for name in (
            "workflow_definition_digest",
            "step_semantic_digest",
            "compiler_version",
        ):
            value = getattr(self, name)
            if value is not None:
                _require_identifier(value, name)
        if not isinstance(self.metadata, FrozenDict):
            object.__setattr__(self, "metadata", FrozenDict(self.metadata))

    def to_dict(self) -> dict[str, Any]:
        """Return a canonical, JSON-compatible representation."""
        return {
            "workflow_definition_digest": self.workflow_definition_digest,
            "step_semantic_digest": self.step_semantic_digest,
            "compiler_version": self.compiler_version,
            "metadata": self.metadata.thaw(),
        }


@dataclass(frozen=True, slots=True)
class StepResult:
    """An immutable, published result of one workflow step.

    Parameters
    ----------
    step_id : str
        Step id this result belongs to.
    status : StepStatus
        Terminal step status evaluated by the completion policy.
    structures : StructureSet
        Structures produced and accepted by the step.
    results : ResultSet
        Scientific results produced and accepted by the step.
    artifacts : ArtifactSet
        Artifacts produced by the step.
    item_results : tuple[WorkItemResult, ...]
        Accepted work-item results backing this step result.
    diagnostics : tuple[Diagnostic, ...]
        Step-level diagnostics.
    summary : FrozenDict
        Machine-readable completion summary (counts, policy outcome).
    provenance : StepProvenance | None
        Compiler provenance.

    Raises
    ------
    DomainError
        Raised when the result contradicts its status or repeats items.
    """

    step_id: str
    status: StepStatus
    structures: StructureSet = field(default_factory=StructureSet)
    results: ResultSet = field(default_factory=ResultSet)
    artifacts: ArtifactSet = field(default_factory=ArtifactSet)
    item_results: tuple[WorkItemResult, ...] = ()
    diagnostics: tuple[Diagnostic, ...] = ()
    summary: FrozenDict = field(default_factory=FrozenDict)
    provenance: StepProvenance | None = None

    def __post_init__(self) -> None:
        _require_identifier(self.step_id, "step_id")
        if not isinstance(self.status, StepStatus):
            raise DomainError("status must be a StepStatus")
        for name, expected in (
            ("structures", StructureSet),
            ("results", ResultSet),
            ("artifacts", ArtifactSet),
        ):
            if not isinstance(getattr(self, name), expected):
                raise DomainError(f"{name} must be a {expected.__name__}")
        items = tuple(self.item_results)
        seen: set[str] = set()
        for item in items:
            if not isinstance(item, WorkItemResult):
                raise DomainError(
                    f"item_results members must be WorkItemResult, got {type(item).__name__}"
                )
            if item.work_item_id in seen:
                raise DomainError(f"duplicate work item result: {item.work_item_id!r}")
            seen.add(item.work_item_id)
        object.__setattr__(self, "item_results", items)
        object.__setattr__(self, "diagnostics", tuple(self.diagnostics))
        if not isinstance(self.summary, FrozenDict):
            object.__setattr__(self, "summary", FrozenDict(self.summary))
        if self.provenance is not None and not isinstance(self.provenance, StepProvenance):
            raise DomainError("provenance must be a StepProvenance")
        self._validate_status_consistency()

    def _validate_status_consistency(self) -> None:
        completed = [item for item in self.item_results if item.is_completed]
        if self.status is StepStatus.COMPLETED:
            if len(completed) != len(self.item_results):
                raise DomainError(
                    "a completed step result requires every work item to be completed"
                )
        elif self.status is StepStatus.PARTIAL:
            if not completed:
                raise DomainError("a partial step result requires at least one completed item")
            if len(completed) == len(self.item_results):
                raise DomainError("a partial step result must not have only completed items")
        elif self.status is StepStatus.FAILED:
            if self.item_results and len(completed) == len(self.item_results):
                raise DomainError("a failed step result must not have only completed items")
        elif self.status is StepStatus.CANCELLED:
            has_cancelled = any(not item.is_completed for item in self.item_results)
            if self.item_results and not has_cancelled:
                raise DomainError(
                    "a cancelled step result must have at least one non-completed item"
                )

    @property
    def work_item_ids(self) -> tuple[str, ...]:
        """Return accepted work-item ids in publication order."""
        return tuple(item.work_item_id for item in self.item_results)

    def to_dict(self) -> dict[str, Any]:
        """Return a canonical, JSON-compatible representation."""
        return {
            "step_id": self.step_id,
            "status": self.status.value,
            "structures": [record.to_dict() for record in self.structures],
            "results": [record.to_dict() for record in self.results],
            "artifacts": [record.to_dict() for record in self.artifacts],
            "item_results": [item.to_dict() for item in self.item_results],
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
            "summary": self.summary.thaw(),
            "provenance": self.provenance.to_dict() if self.provenance is not None else None,
        }
