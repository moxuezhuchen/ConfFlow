"""Thread-safe in-memory aggregate repository for service contract tests."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from threading import RLock

from .errors import ErrorCode, ExecutionServiceError, RepositoryConflict, RepositoryMutationError
from .models import ExecutionAggregate, ExecutionEvent, PrepareRequest, RunState


class InMemoryExecutionRepository:
    """One-lock aggregate repository that models cross-service CAS behavior."""

    def __init__(self) -> None:
        self._lock = RLock()
        self._records: dict[str, ExecutionAggregate] = {}
        self._keys: dict[str, str] = {}
        self._fail_next_mutation = False

    def fail_next_mutation(self) -> None:
        """Inject a pre-commit failure to prove aggregate writes never tear."""
        with self._lock:
            self._fail_next_mutation = True

    def create_or_get(self, request: PrepareRequest) -> ExecutionAggregate:
        """Atomically persist a prepared aggregate and its initial event."""
        with self._lock:
            by_key = self._keys.get(request.idempotency_key)
            if by_key is not None:
                record = self._records[by_key]
                if record.request_digest != request.request_digest:
                    raise ExecutionServiceError(
                        ErrorCode.IDEMPOTENCY_CONFLICT,
                        f"Idempotency key conflicts: {request.idempotency_key}",
                    )
                return record
            if request.run_id in self._records:
                raise ExecutionServiceError(
                    ErrorCode.INVALID_REQUEST,
                    f"Run ID is already bound: {request.run_id}",
                )
            event = ExecutionEvent(cursor=_cursor(1), revision=1, type="prepared")
            record = ExecutionAggregate(
                run_id=request.run_id,
                idempotency_key=request.idempotency_key,
                request_digest=request.request_digest,
                workflow_config_digest=request.workflow_config_digest,
                input_manifest_digest=request.input_manifest_digest,
                expected_executable_identity=request.expected_executable_identity,
                revision=1,
                state=RunState.PREPARED,
                events=(event,),
            )
            self._records[request.run_id] = record
            self._keys[request.idempotency_key] = request.run_id
            return record

    def read(self, run_id: str) -> ExecutionAggregate | None:
        """Read an immutable aggregate snapshot."""
        with self._lock:
            return self._records.get(run_id)

    def compare_and_mutate(
        self,
        run_id: str,
        expected_revision: int,
        mutate: Callable[[ExecutionAggregate], tuple[ExecutionAggregate, str]],
    ) -> ExecutionAggregate:
        """Commit record, revision, and event together or leave all unchanged."""
        with self._lock:
            current = self._records.get(run_id)
            if current is None or current.revision != expected_revision:
                raise RepositoryConflict(run_id)
            candidate, event_type = mutate(current)
            if self._fail_next_mutation:
                self._fail_next_mutation = False
                raise RepositoryMutationError("Injected aggregate mutation failure")
            revision = current.revision + 1
            event = ExecutionEvent(cursor=_cursor(revision), revision=revision, type=event_type)
            committed = replace(candidate, revision=revision, events=current.events + (event,))
            self._records[run_id] = committed
            return committed


def _cursor(revision: int) -> str:
    """Derive the only cursor representation accepted by this aggregate."""
    return f"r{revision:020d}"
