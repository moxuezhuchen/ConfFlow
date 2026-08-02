"""Immutable execution-domain types owned by the Phase C aggregate."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class RunState(str, Enum):
    """The v1 control-protocol state machine."""

    PREPARED = "prepared"
    QUEUED = "queued"
    RUNNING = "running"
    PAUSED = "paused"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


TERMINAL_STATES = frozenset({RunState.COMPLETED, RunState.FAILED, RunState.CANCELLED})


@dataclass(frozen=True)
class ExecutableIdentity:
    """Identity bound by prepare and verified before each launch attempt."""

    sha256: str
    realpath: str | None = None
    device_inode: str | None = None


@dataclass(frozen=True)
class PrepareRequest:
    """All values accepted by the durable non-executing prepare operation."""

    run_id: str
    idempotency_key: str
    request_digest: str
    workflow_config_digest: str
    input_manifest_digest: str
    expected_executable_identity: ExecutableIdentity


@dataclass(frozen=True)
class Checkpoint:
    """The one current checkpoint that can authorize a resume attempt."""

    checkpoint_id: str


@dataclass(frozen=True)
class Artifact:
    """Producer-validated terminal artifact metadata."""

    terminal: str
    path: str
    sha256: str
    size: int
    content_schema: str


@dataclass(frozen=True)
class ExecutionEvent:
    """Stable replay event; its cursor is derived solely from its revision."""

    cursor: str
    revision: int
    type: str


@dataclass(frozen=True)
class ExecutionAggregate:
    """Complete durable state for one run, mutated atomically by its repository."""

    run_id: str
    idempotency_key: str
    request_digest: str
    workflow_config_digest: str
    input_manifest_digest: str
    expected_executable_identity: ExecutableIdentity
    revision: int
    state: RunState
    attempt: int = 0
    launch_token: str | None = None
    launch_checkpoint: str | None = None
    cancel_token: str | None = None
    cancel_pending: bool = False
    checkpoint: Checkpoint | None = None
    artifacts: tuple[Artifact, ...] = ()
    events: tuple[ExecutionEvent, ...] = ()

    def snapshot(self) -> RunSnapshot:
        """Return the public monotonic projection."""
        return RunSnapshot(run_id=self.run_id, revision=self.revision, state=self.state)


@dataclass(frozen=True)
class RunSnapshot:
    """Public state projection returned by service operations."""

    run_id: str
    revision: int
    state: RunState


@dataclass(frozen=True)
class EventPage:
    """Ordered event replay result."""

    snapshot: RunSnapshot
    events: tuple[ExecutionEvent, ...]
    next_cursor: str | None


@dataclass(frozen=True)
class ArtifactManifest:
    """Terminal-only artifact projection."""

    snapshot: RunSnapshot
    artifacts: tuple[Artifact, ...]


@dataclass(frozen=True)
class LaunchRequest:
    """Idempotent hand-off from the service to a workflow-executor adapter."""

    run_id: str
    token: str
    attempt: int
    checkpoint_id: str | None
    expected_identity: ExecutableIdentity


@dataclass(frozen=True)
class LaunchReceipt:
    """Fast acknowledgement that an executor accepted a token."""

    accepted: bool
    identity_mismatch: bool = False
    cancelled: bool = False


@dataclass(frozen=True)
class CancelRequest:
    """Idempotent cancellation hand-off from the service to an executor adapter."""

    run_id: str
    token: str
    launch_token: str | None
    attempt: int


@dataclass(frozen=True)
class CancelReceipt:
    """Fast acknowledgement that an executor confirmed cancellation."""

    confirmed: bool
