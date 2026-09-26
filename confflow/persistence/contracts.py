#!/usr/bin/env python3

"""V4 durable persistence contracts (V4-3, frozen shared authority).

This module is owned by the main agent and is **frozen**: subagents must read
it but never edit it.  It defines every cross-module type of the durable
execution layer so the store, reuse, publication, recovery, artifact, and test
workstreams can proceed in parallel without interface drift:

- run layout constants and path joiners (the only authority hierarchy is
  WorkItemStore > StepResult > RunState > artifacts; filenames are never
  semantic truth);
- the work-item state machine and its legal transitions;
- :class:`ReuseDecision` with machine-readable reason codes;
- owner identity and liveness verdicts for abandoned ``RUNNING`` recovery;
- the run-state record shape (load/save lives in ``run_state.py``);
- the GC plan shape (planning/execution live in ``artifacts.py``).

Dependency rule: this package imports only ``confflow.domain`` plus the
standard library (``sqlite3`` in implementation modules only).  It never
imports ``confflow.execution``, ``confflow.workflow``, ``confflow.calc``,
``confflow.core``, or any legacy runtime.  The executor side depends on this
package, never the reverse.
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Final

from ..domain._immutable import FrozenDict
from ..domain.errors import DomainError

__all__ = [
    "ALLOWED_TRANSITIONS",
    "GCEntry",
    "GCPlan",
    "OwnerIdentity",
    "OwnerVerdict",
    "PERSISTENCE_SCHEMA_VERSION",
    "PersistenceError",
    "CorruptStateError",
    "ReuseCode",
    "ReuseDecision",
    "RunState",
    "RunStepStatus",
    "RUN_STATE_FILENAME",
    "SCHEMA_KIND",
    "STEP_RESULT_FILENAME",
    "STEP_STORE_FILENAME",
    "StepLifecycle",
    "StoredWorkItemStatus",
    "is_legal_transition",
    "is_retryable_status",
    "is_terminal_status",
    "item_work_dir",
    "run_state_path",
    "step_artifacts_dir",
    "step_dir",
    "step_result_path",
    "store_path",
    "validate_run_root",
    "wall_now",
]

#: Current SQLite schema version of the durable work-item store.  Any store
#: file carrying another version fails closed; there is no silent migration.
PERSISTENCE_SCHEMA_VERSION: Final[int] = 1

#: Digest-kind marker for schema-identity payloads.
SCHEMA_KIND: Final[str] = "confflow.persistence.schema.v1"

#: File names inside the run layout.  They are conventions, never identity:
#: authority always comes from store rows and digest-validated payloads.
RUN_STATE_FILENAME: Final[str] = "run_state.json"
STEP_STORE_FILENAME: Final[str] = "work_items.sqlite"
STEP_RESULT_FILENAME: Final[str] = "step_result.json"


class PersistenceError(DomainError, RuntimeError):
    """Base class for every durable-persistence failure."""


class CorruptStateError(PersistenceError):
    """Durable state exists but is unreadable, incomplete, or fails validation.

    Raising this error must always fail closed: never drop, rebuild over, or
    silently migrate the corrupt data.
    """


class SchemaVersionError(PersistenceError):
    """A store file carries an unsupported schema version."""


class StateTransitionError(PersistenceError):
    """An illegal work-item state transition was requested."""


def wall_now() -> float:
    """Return the current UTC wall-clock time in seconds.

    Wall timestamps are provenance only.  Durations and elapsed intervals
    always come from a monotonic source; never subtract wall timestamps.
    """
    return time.time()


def validate_run_root(run_root: str) -> str:
    """Validate *run_root* as a managed persistence root.

    Returns the absolute path.  The store, artifacts, and publication files
    must all live under this root; absolute machine paths are never durable
    identity.
    """
    if not isinstance(run_root, str) or not run_root or not run_root.strip():
        raise PersistenceError("run root must be a non-empty string")
    if "\x00" in run_root:
        raise PersistenceError("run root must not contain NUL")
    absolute = os.path.abspath(run_root)
    return absolute


def _require_path_segment(value: str, field_name: str) -> str:
    """Validate *value* as a single portable path segment."""
    if not isinstance(value, str) or not value or value != value.strip():
        raise PersistenceError(f"{field_name} must be a non-empty string")
    if value in (".", "..") or "/" in value or "\\" in value or "\x00" in value:
        raise PersistenceError(f"{field_name} must be a single path segment: {value!r}")
    return value


def step_dir(run_root: str, step_id: str) -> str:
    """Return the directory owning one step's durable state."""
    return os.path.join(
        validate_run_root(run_root), "steps", _require_path_segment(step_id, "step_id")
    )


def store_path(run_root: str, step_id: str) -> str:
    """Return the SQLite store path for one step."""
    return os.path.join(step_dir(run_root, step_id), STEP_STORE_FILENAME)


def step_result_path(run_root: str, step_id: str) -> str:
    """Return the published step-result path for one step."""
    return os.path.join(step_dir(run_root, step_id), STEP_RESULT_FILENAME)


def run_state_path(run_root: str) -> str:
    """Return the run-state path for a run."""
    return os.path.join(validate_run_root(run_root), RUN_STATE_FILENAME)


def item_work_dir(run_root: str, step_id: str, work_item_id: str) -> str:
    """Return the native working directory of one work item."""
    return os.path.join(
        step_dir(run_root, step_id),
        "work",
        _require_path_segment(work_item_id, "work_item_id"),
    )


def step_artifacts_dir(run_root: str, step_id: str) -> str:
    """Return the directory holding one step's durable artifact bytes."""
    return os.path.join(step_dir(run_root, step_id), "artifacts")


class StoredWorkItemStatus(str, Enum):
    """Durable status of one work item.

    ``RUNNING`` means a live claim holds the item; ``INTERRUPTED`` means a
    claim died without a terminal outcome and reconciliation must decide.
    ``CANCELLED`` is terminal and never auto-retried.
    """

    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    INTERRUPTED = "interrupted"


#: The only legal durable transitions.  Anything else raises
#: :class:`StateTransitionError`.  In particular ``COMPLETED`` and
#: ``CANCELLED`` have no outgoing edges: history is never overwritten.
ALLOWED_TRANSITIONS: dict[StoredWorkItemStatus, frozenset[StoredWorkItemStatus]] = {
    StoredWorkItemStatus.PENDING: frozenset({StoredWorkItemStatus.RUNNING}),
    StoredWorkItemStatus.RUNNING: frozenset(
        {
            StoredWorkItemStatus.COMPLETED,
            StoredWorkItemStatus.FAILED,
            StoredWorkItemStatus.CANCELLED,
            StoredWorkItemStatus.INTERRUPTED,
        }
    ),
    StoredWorkItemStatus.FAILED: frozenset({StoredWorkItemStatus.RUNNING}),
    StoredWorkItemStatus.INTERRUPTED: frozenset({StoredWorkItemStatus.RUNNING}),
    StoredWorkItemStatus.COMPLETED: frozenset(),
    StoredWorkItemStatus.CANCELLED: frozenset(),
}


def is_legal_transition(current: StoredWorkItemStatus, nxt: StoredWorkItemStatus) -> bool:
    """Return whether *current* may move to *nxt* durably."""
    return nxt in ALLOWED_TRANSITIONS[current]


def is_terminal_status(status: StoredWorkItemStatus) -> bool:
    """Return whether *status* ends an attempt chain without retry."""
    return status in (
        StoredWorkItemStatus.COMPLETED,
        StoredWorkItemStatus.CANCELLED,
    )


def is_retryable_status(status: StoredWorkItemStatus) -> bool:
    """Return whether *status* may start a new attempt via re-claim."""
    return status in (
        StoredWorkItemStatus.FAILED,
        StoredWorkItemStatus.INTERRUPTED,
    )


class ReuseCode(str, Enum):
    """Machine-readable outcome of a reuse evaluation."""

    REUSE = "reuse"
    EXECUTE_NEW = "execute_new"
    RETRY_FAILED = "retry_failed"
    RECOVER_ABANDONED = "recover_abandoned"
    INVALIDATE_DEFINITION = "invalidate_definition"
    INVALIDATE_INPUT = "invalidate_input"
    INVALIDATE_ENVIRONMENT = "invalidate_environment"
    INVALIDATE_PROVENANCE = "invalidate_provenance"
    INVALIDATE_ARTIFACT = "invalidate_artifact"
    BLOCKED_UNCERTAIN_OWNER = "blocked_uncertain_owner"


@dataclass(frozen=True, slots=True)
class ReuseDecision:
    """One explicit reuse verdict for a work item.

    Parameters
    ----------
    decision : ReuseCode
        Machine-readable outcome; never a bare boolean.
    reason : str
        Human-readable explanation naming the matching/mismatching axis.
    work_item_id : str
        Item this decision applies to.
    details : FrozenDict
        Mismatching digests, artifact ids, attempt numbers, or owner proof.
    """

    decision: ReuseCode
    reason: str
    work_item_id: str
    details: FrozenDict = field(default_factory=FrozenDict)

    def __post_init__(self) -> None:
        if not isinstance(self.decision, ReuseCode):
            raise PersistenceError("decision must be a ReuseCode")
        if not isinstance(self.reason, str) or not self.reason.strip():
            raise PersistenceError("reason must be a non-empty string")
        if not isinstance(self.work_item_id, str) or not self.work_item_id.strip():
            raise PersistenceError("work_item_id must be a non-empty string")
        if not isinstance(self.details, FrozenDict):
            object.__setattr__(self, "details", FrozenDict(self.details))

    def to_dict(self) -> dict[str, Any]:
        """Return a canonical, JSON-compatible representation."""
        return {
            "decision": self.decision.value,
            "reason": self.reason,
            "work_item_id": self.work_item_id,
            "details": self.details.thaw(),
        }


class OwnerVerdict(str, Enum):
    """Liveness verdict for an abandoned ``RUNNING`` claim."""

    DEFINITELY_ALIVE = "definitely_alive"
    DEFINITELY_DEAD = "definitely_dead"
    UNCERTAIN = "uncertain"


@dataclass(frozen=True, slots=True)
class OwnerIdentity:
    """Process-boundary identity recorded at claim time.

    The token binds a claim to one controller; the pid/session/process-group
    triple plus creation time lets reconciliation prove whether the original
    native boundary is still alive.  ``UNCERTAIN`` whenever proof is
    impossible (for example pid reuse windows) blocks duplicate launch.
    """

    owner_token: str
    pid: int | None = None
    process_group_id: int | None = None
    session_id: int | None = None
    create_time: float | None = None
    claimed_wall: float | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.owner_token, str) or not self.owner_token.strip():
            raise PersistenceError("owner_token must be a non-empty string")
        for name in ("pid", "process_group_id", "session_id"):
            value = getattr(self, name)
            if value is not None and (
                isinstance(value, bool) or not isinstance(value, int) or value <= 0
            ):
                raise PersistenceError(f"{name} must be a positive int or None")
        for name in ("create_time", "claimed_wall"):
            value = getattr(self, name)
            if value is not None and (
                isinstance(value, bool) or not isinstance(value, (int, float))
            ):
                raise PersistenceError(f"{name} must be a number or None")

    def to_dict(self) -> dict[str, Any]:
        """Return a canonical, JSON-compatible representation."""
        return {
            "owner_token": self.owner_token,
            "pid": self.pid,
            "process_group_id": self.process_group_id,
            "session_id": self.session_id,
            "create_time": self.create_time,
            "claimed_wall": self.claimed_wall,
        }


class RunStepStatus(str, Enum):
    """Lifecycle status of one step inside a run."""

    PENDING = "pending"
    RUNNING = "running"
    PARTIAL = "partial"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass(frozen=True, slots=True)
class StepLifecycle:
    """Lifecycle record of one step.

    The work-item store owns item truth; this record only tracks the step's
    lifecycle plus the identity of its published step result.  It never embeds
    work-item payloads.
    """

    step_id: str
    status: RunStepStatus = RunStepStatus.PENDING
    published_step_result_digest: str | None = None
    updated_wall: float | None = None

    def __post_init__(self) -> None:
        _require_path_segment(self.step_id, "step_id")
        if not isinstance(self.status, RunStepStatus):
            raise PersistenceError("status must be a RunStepStatus")

    def to_dict(self) -> dict[str, Any]:
        """Return a canonical, JSON-compatible representation."""
        return {
            "step_id": self.step_id,
            "status": self.status.value,
            "published_step_result_digest": self.published_step_result_digest,
            "updated_wall": self.updated_wall,
        }


@dataclass(frozen=True, slots=True)
class RunState:
    """Durable lifecycle truth of one run.

    Parameters
    ----------
    run_id : str
        Stable run identifier.
    schema_version : int
        Persistence schema version; must equal
        :data:`PERSISTENCE_SCHEMA_VERSION` or loading fails closed.
    definition_digest : str | None
        Workflow definition digest this run executes, when known.
    steps : tuple[StepLifecycle, ...]
        Per-step lifecycle records, unique by step id.
    """

    run_id: str
    schema_version: int = PERSISTENCE_SCHEMA_VERSION
    definition_digest: str | None = None
    steps: tuple[StepLifecycle, ...] = ()
    created_wall: float | None = None
    updated_wall: float | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.run_id, str) or not self.run_id.strip():
            raise PersistenceError("run_id must be a non-empty string")
        if self.schema_version != PERSISTENCE_SCHEMA_VERSION:
            raise PersistenceError(
                f"run state schema version {self.schema_version!r} is not supported"
            )
        steps = tuple(self.steps)
        seen: set[str] = set()
        for step in steps:
            if not isinstance(step, StepLifecycle):
                raise PersistenceError("steps members must be StepLifecycle")
            if step.step_id in seen:
                raise PersistenceError(f"duplicate step lifecycle: {step.step_id!r}")
            seen.add(step.step_id)
        object.__setattr__(self, "steps", steps)

    def step(self, step_id: str) -> StepLifecycle | None:
        """Return the lifecycle record for *step_id*, or ``None``."""
        for record in self.steps:
            if record.step_id == step_id:
                return record
        return None

    def to_dict(self) -> dict[str, Any]:
        """Return a canonical, JSON-compatible representation."""
        return {
            "run_id": self.run_id,
            "schema_version": self.schema_version,
            "definition_digest": self.definition_digest,
            "steps": [record.to_dict() for record in self.steps],
            "created_wall": self.created_wall,
            "updated_wall": self.updated_wall,
        }

    @classmethod
    def from_dict(cls, payload: Any) -> RunState:
        """Rebuild a run state from a decoded JSON payload, failing closed."""
        if not isinstance(payload, dict):
            raise CorruptStateError("run state payload must be a mapping")
        try:
            steps = tuple(
                StepLifecycle(
                    step_id=entry["step_id"],
                    status=RunStepStatus(entry.get("status", "pending")),
                    published_step_result_digest=entry.get("published_step_result_digest"),
                    updated_wall=entry.get("updated_wall"),
                )
                for entry in payload.get("steps", ())
            )
            return cls(
                run_id=payload["run_id"],
                schema_version=payload.get("schema_version", PERSISTENCE_SCHEMA_VERSION),
                definition_digest=payload.get("definition_digest"),
                steps=steps,
                created_wall=payload.get("created_wall"),
                updated_wall=payload.get("updated_wall"),
            )
        except (KeyError, TypeError, ValueError, PersistenceError) as exc:
            raise CorruptStateError(f"run state payload is invalid: {exc}") from exc


@dataclass(frozen=True, slots=True)
class GCEntry:
    """One garbage-collection candidate with its justification."""

    artifact_id: str
    reason: str
    locator_path: str | None = None
    size_bytes: int | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.artifact_id, str) or not self.artifact_id.strip():
            raise PersistenceError("artifact_id must be a non-empty string")
        if not isinstance(self.reason, str) or not self.reason.strip():
            raise PersistenceError("reason must be a non-empty string")

    def to_dict(self) -> dict[str, Any]:
        """Return a canonical, JSON-compatible representation."""
        return {
            "artifact_id": self.artifact_id,
            "reason": self.reason,
            "locator_path": self.locator_path,
            "size_bytes": self.size_bytes,
        }


@dataclass(frozen=True, slots=True)
class GCPlan:
    """A dry-runnable collection plan; applying it is a separate step."""

    entries: tuple[GCEntry, ...] = ()

    def __post_init__(self) -> None:
        entries = tuple(self.entries)
        for entry in entries:
            if not isinstance(entry, GCEntry):
                raise PersistenceError("entries members must be GCEntry")
        object.__setattr__(self, "entries", entries)

    @property
    def artifact_ids(self) -> tuple[str, ...]:
        """Return candidate artifact ids in plan order."""
        return tuple(entry.artifact_id for entry in self.entries)

    def to_dict(self) -> dict[str, Any]:
        """Return a canonical, JSON-compatible representation."""
        return {"entries": [entry.to_dict() for entry in self.entries]}
