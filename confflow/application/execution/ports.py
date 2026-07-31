"""Ports around the aggregate; none expose SQLite, files, CLI, or agent state."""

from __future__ import annotations

from collections.abc import Callable
from typing import Protocol

from .models import (
    CancelReceipt,
    CancelRequest,
    ExecutableIdentity,
    ExecutionAggregate,
    LaunchReceipt,
    LaunchRequest,
    PrepareRequest,
)


class ExecutionRepository(Protocol):
    """Atomic aggregate repository with compare-and-mutate semantics."""

    def create_or_get(self, request: PrepareRequest) -> ExecutionAggregate:
        """Atomically create a prepared aggregate or return its idempotent original."""

    def read(self, run_id: str) -> ExecutionAggregate | None:
        """Read the current aggregate projection."""

    def compare_and_mutate(
        self,
        run_id: str,
        expected_revision: int,
        mutate: Callable[[ExecutionAggregate], tuple[ExecutionAggregate, str]],
    ) -> ExecutionAggregate:
        """Atomically commit replacement aggregate, increment revision, and append event."""


class IdentityVerifier(Protocol):
    """Measure the executable identity at the service launch boundary."""

    def measure(self) -> ExecutableIdentity:
        """Return the current executable identity without launching a calculation."""


class WorkflowExecutor(Protocol):
    """Idempotent non-blocking executor boundary with token-level arbitration.

    An adapter must atomically arbitrate ``ensure_launched`` and
    ``ensure_cancelled`` for one ``(run_id, launch_token)``.  Confirmation of
    cancellation installs a durable tombstone: an already in-flight or later
    launch request for that token must not start work, or must prove it stopped.
    """

    def ensure_launched(self, request: LaunchRequest) -> LaunchReceipt:
        """Ensure exactly this token is launched; return quickly and never join callbacks."""

    def ensure_cancelled(self, request: CancelRequest) -> CancelReceipt:
        """Tombstone the bound launch token and ensure its work is stopped; return quickly."""
