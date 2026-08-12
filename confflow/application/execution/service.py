"""Single aggregate service for all Phase C execution-domain transitions."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import replace

from .errors import ErrorCode, ExecutionServiceError, RepositoryConflict, RepositoryMutationError
from .models import (
    TERMINAL_STATES,
    Artifact,
    ArtifactManifest,
    CancelRequest,
    Checkpoint,
    EventPage,
    ExecutableIdentity,
    ExecutionAggregate,
    LaunchRequest,
    PrepareRequest,
    RunSnapshot,
    RunState,
)
from .policy import (
    _ID_CHARS,  # noqa: F401 - private compatibility alias
    _canonical_path,  # noqa: F401 - private compatibility alias
    _identities_match,
    _is_digest,  # noqa: F401 - private compatibility alias
    _is_identifier,  # noqa: F401 - private compatibility alias
    _parse_cursor,
    _terminal_error,
    _validate_prepare,
    _validated_artifacts,
)
from .ports import ExecutionRepository, IdentityVerifier, WorkflowExecutor


class ExecutionLifecycle:
    """Token-bound callback surface that rejects stale lifecycle reports."""

    def __init__(self, service: ExecutionService, run_id: str, token: str) -> None:
        self._service = service
        self._run_id = run_id
        self._token = token

    def started(self) -> RunSnapshot:
        """Advance the matching queued attempt to running."""
        return self._service.lifecycle_started(self._run_id, self._token)

    def checkpoint(self, checkpoint_id: str) -> RunSnapshot:
        """Atomically set the current checkpoint for the matching running attempt."""
        return self._service.lifecycle_checkpoint(self._run_id, self._token, checkpoint_id)

    def paused(self) -> RunSnapshot:
        """Advance the matching running attempt to paused."""
        return self._service.lifecycle_paused(self._run_id, self._token)

    def completed(self, artifacts: Sequence[Artifact] = ()) -> RunSnapshot:
        """Commit a matching terminal completion and its validated manifest."""
        return self._service.lifecycle_terminal(
            self._run_id, self._token, RunState.COMPLETED, artifacts
        )

    def failed(self, artifacts: Sequence[Artifact] = ()) -> RunSnapshot:
        """Commit a matching terminal failure and its validated manifest."""
        return self._service.lifecycle_terminal(
            self._run_id, self._token, RunState.FAILED, artifacts
        )


class ExecutionService:
    """Application service whose repository owns every durable run-domain fact."""

    def __init__(
        self,
        *,
        repository: ExecutionRepository,
        executor: WorkflowExecutor,
        identity_verifier: IdentityVerifier,
        event_page_size: int = 100,
    ) -> None:
        if event_page_size < 1:
            raise ValueError("event_page_size must be positive")
        self._repository = repository
        self._executor = executor
        self._identity_verifier = identity_verifier
        self._event_page_size = event_page_size

    def prepare(self, request: PrepareRequest) -> RunSnapshot:
        """Durably create a non-executing prepared aggregate idempotently."""
        _validate_prepare(request)
        return self._repository.create_or_get(request).snapshot()

    def verify_executable_identity(self, expected: ExecutableIdentity) -> None:
        """Verify a complete identity before an alternate adapter persists a prepare."""
        if expected.realpath is None or expected.device_inode is None:
            raise ExecutionServiceError(
                ErrorCode.EXECUTABLE_IDENTITY_MISMATCH,
                "Fixture executable identity must include realpath and device_inode",
            )
        self._verify_identity(expected)

    def execute(self, run_id: str) -> RunSnapshot:
        """Claim a launch intent, then ensure its token outside repository mutation."""
        record = self._require(run_id)
        if record.state is RunState.PREPARED:
            self._verify_identity(record.expected_executable_identity)
            record = self._claim_launch(record, checkpoint_id=None)
        elif record.state is not RunState.QUEUED:
            return record.snapshot()
        return self._ensure_launch(record)

    def consume_queued_launch(self, run_id: str) -> RunSnapshot:
        """Consume one existing queued launch intent without creating an attempt.

        This is the explicit agent/launcher hand-off for callers that must not
        claim ``PREPARED`` or resume ``PAUSED`` state.  Terminal calls attach
        to the durable result; only a queued aggregate with its existing,
        non-empty token reaches the executor port.
        """
        record = self._require(run_id)
        if record.state in TERMINAL_STATES:
            return record.snapshot()
        if record.state is not RunState.QUEUED:
            raise ExecutionServiceError(
                ErrorCode.INVALID_STATE_TRANSITION,
                f"Cannot consume launch intent in {record.state.value} state",
            )
        if record.attempt < 1 or not record.launch_token:
            raise ExecutionServiceError(
                ErrorCode.INVALID_STATE_TRANSITION,
                f"Queued run has no launch token: {run_id}",
            )
        return self._ensure_launch(record)

    def recover_abandoned_launch(self, run_id: str, *, token: str) -> RunSnapshot:
        """Requeue a running token after its external worker lease disappeared.

        The control worker owns the kernel lease that proves the prior process
        is gone before calling this method.  The service still performs the
        durable compare-and-swap and creates a fresh attempt token, so an old
        lifecycle callback cannot finish the recovered attempt.
        """
        record = self._require(run_id)
        if record.state is not RunState.RUNNING or record.launch_token != token:
            return record.snapshot()
        if record.cancel_pending:
            raise ExecutionServiceError(
                ErrorCode.INVALID_STATE_TRANSITION,
                f"Cannot recover a cancellation-pending run: {run_id}",
            )
        attempt = record.attempt + 1
        next_token = f"{run_id}.launch.{attempt}"
        checkpoint_id = record.checkpoint.checkpoint_id if record.checkpoint is not None else None
        try:
            return self._repository.compare_and_mutate(
                run_id,
                record.revision,
                lambda current: (
                    replace(
                        current,
                        state=RunState.QUEUED,
                        attempt=attempt,
                        launch_token=next_token,
                        launch_checkpoint=checkpoint_id,
                        cancel_token=None,
                        cancel_pending=False,
                    ),
                    "requeued",
                ),
            ).snapshot()
        except RepositoryConflict:
            latest = self._require(run_id)
            return latest.snapshot()
        except RepositoryMutationError as error:
            raise ExecutionServiceError(ErrorCode.INTERNAL, str(error), retryable=True) from error

    def validate_launch_request(self, request: LaunchRequest) -> RunSnapshot:
        """Validate a formal hand-off against the current durable run facts.

        Executors use this service-level check instead of reaching into the
        repository.  A matching non-queued state is attach-only; a matching
        queued state is launchable.  Every other token, run, attempt,
        checkpoint, identity, or cancellation-pending combination is rejected.
        """
        record = self._require(request.run_id)
        if (
            record.launch_token != request.token
            or record.attempt != request.attempt
            or record.launch_checkpoint != request.checkpoint_id
            or record.expected_executable_identity != request.expected_identity
        ):
            raise ExecutionServiceError(
                ErrorCode.INVALID_STATE_TRANSITION,
                "Launch request does not match the active durable attempt",
            )
        if record.state is RunState.QUEUED and not record.cancel_pending:
            return record.snapshot()
        if record.state in {RunState.RUNNING, RunState.PAUSED} or record.state in TERMINAL_STATES:
            return record.snapshot()
        raise ExecutionServiceError(
            ErrorCode.INVALID_STATE_TRANSITION,
            f"Launch request is invalid while run is {record.state.value}",
        )

    def status(self, run_id: str) -> RunSnapshot:
        """Read the latest public projection."""
        return self._require(run_id).snapshot()

    def events(self, run_id: str, *, after: str | None = None) -> EventPage:
        """Return ordered replay strictly after a known revision-derived cursor."""
        record = self._require(run_id)
        after_revision = _parse_cursor(after) if after is not None else 0
        if after is not None and not any(
            event.revision == after_revision for event in record.events
        ):
            raise ExecutionServiceError(
                ErrorCode.INVALID_REQUEST, f"Unknown or expired cursor: {after}"
            )
        page = tuple(event for event in record.events if event.revision > after_revision)[
            : self._event_page_size
        ]
        next_cursor = page[-1].cursor if page else after
        return EventPage(snapshot=record.snapshot(), events=page, next_cursor=next_cursor)

    def cancel(self, run_id: str) -> RunSnapshot:
        """Persist cancellation intent before asking an executor to confirm it."""
        record = self._require(run_id)
        if record.state in TERMINAL_STATES:
            raise _terminal_error(run_id)
        if not record.cancel_pending:
            record = self._claim_cancel(record)
        return self._ensure_cancel(record)

    def resume(self, run_id: str, *, checkpoint_id: str | None = None) -> RunSnapshot:
        """Create a new queued launch attempt from the current paused checkpoint."""
        record = self._require(run_id)
        if record.state in TERMINAL_STATES:
            raise _terminal_error(run_id)
        if record.state is not RunState.PAUSED:
            raise ExecutionServiceError(
                ErrorCode.INVALID_STATE_TRANSITION,
                f"Cannot resume a run in {record.state.value} state",
            )
        checkpoint = record.checkpoint
        if checkpoint is None or (
            checkpoint_id is not None and checkpoint_id != checkpoint.checkpoint_id
        ):
            raise ExecutionServiceError(
                ErrorCode.INVALID_CHECKPOINT, "Checkpoint is missing or stale"
            )
        self._verify_identity(record.expected_executable_identity)
        claimed = self._claim_launch(record, checkpoint_id=checkpoint.checkpoint_id)
        return self._ensure_launch(claimed)

    def artifacts(self, run_id: str) -> ArtifactManifest:
        """Expose only the manifest atomically committed with a terminal transition."""
        record = self._require(run_id)
        if record.state not in TERMINAL_STATES:
            raise ExecutionServiceError(
                ErrorCode.INVALID_STATE_TRANSITION,
                f"Artifacts unavailable in {record.state.value} state",
            )
        return ArtifactManifest(snapshot=record.snapshot(), artifacts=record.artifacts)

    def lifecycle_started(self, run_id: str, token: str) -> RunSnapshot:
        """Apply a token-bound queued-to-running callback."""
        return self._lifecycle_mutate(
            run_id,
            token,
            allowed_state=RunState.QUEUED,
            event_type="running",
            mutate=lambda record: replace(record, state=RunState.RUNNING),
        )

    def lifecycle_checkpoint(self, run_id: str, token: str, checkpoint_id: str) -> RunSnapshot:
        """Apply a token-bound checkpoint callback."""
        if not checkpoint_id:
            raise ExecutionServiceError(
                ErrorCode.INVALID_REQUEST, "Checkpoint ID must not be empty"
            )
        return self._lifecycle_mutate(
            run_id,
            token,
            allowed_state=RunState.RUNNING,
            event_type="checkpointed",
            mutate=lambda record: replace(record, checkpoint=Checkpoint(checkpoint_id)),
        )

    def lifecycle_paused(self, run_id: str, token: str) -> RunSnapshot:
        """Apply a token-bound running-to-paused callback."""
        return self._lifecycle_mutate(
            run_id,
            token,
            allowed_state=RunState.RUNNING,
            event_type="paused",
            mutate=lambda record: replace(record, state=RunState.PAUSED),
        )

    def lifecycle_terminal(
        self, run_id: str, token: str, state: RunState, artifacts: Sequence[Artifact]
    ) -> RunSnapshot:
        """Commit terminal state and path-validated manifest in one CAS mutation."""
        if state not in {RunState.COMPLETED, RunState.FAILED}:
            raise ValueError("Executor callbacks may only complete or fail a run")

        def terminal(record: ExecutionAggregate) -> ExecutionAggregate:
            return replace(
                record,
                state=state,
                cancel_pending=False,
                artifacts=_validated_artifacts(artifacts),
            )

        return self._lifecycle_mutate(
            run_id,
            token,
            allowed_state=RunState.RUNNING,
            event_type=state.value,
            mutate=terminal,
            allow_cancel_pending=True,
        )

    def _claim_launch(
        self, record: ExecutionAggregate, *, checkpoint_id: str | None
    ) -> ExecutionAggregate:
        """Atomically create or reuse one queued launch intent."""
        while True:
            if record.state is RunState.QUEUED and record.launch_token is not None:
                return record
            if record.state not in {RunState.PREPARED, RunState.PAUSED}:
                return record
            attempt = record.attempt + 1
            token = f"{record.run_id}.launch.{attempt}"
            try:
                return self._repository.compare_and_mutate(
                    record.run_id,
                    record.revision,
                    lambda current, attempt=attempt, token=token: (
                        replace(
                            current,
                            state=RunState.QUEUED,
                            attempt=attempt,
                            launch_token=token,
                            launch_checkpoint=checkpoint_id,
                        ),
                        "queued" if current.state is RunState.PREPARED else "resumed",
                    ),
                )
            except RepositoryConflict:
                record = self._require(record.run_id)
            except RepositoryMutationError as error:
                raise ExecutionServiceError(
                    ErrorCode.INTERNAL, str(error), retryable=True
                ) from error

    def _ensure_launch(self, record: ExecutionAggregate) -> RunSnapshot:
        """Verify the accepted identity before every idempotent executor hand-off."""
        if record.launch_token is None:
            return record.snapshot()
        self._verify_identity(record.expected_executable_identity)
        request = LaunchRequest(
            run_id=record.run_id,
            token=record.launch_token,
            attempt=record.attempt,
            checkpoint_id=record.launch_checkpoint,
            expected_identity=record.expected_executable_identity,
        )
        try:
            receipt = self._executor.ensure_launched(request)
        except Exception as error:
            raise ExecutionServiceError(
                ErrorCode.INTERNAL, f"Launch acknowledgement unknown: {error}", retryable=True
            ) from error
        if not receipt.accepted:
            if receipt.cancelled:
                # An in-flight launch lost the executor's token-arbitration
                # race to a confirmed cancellation.  It must not undo that
                # terminal aggregate state or report a launch failure.
                return self._require(record.run_id).snapshot()
            if receipt.identity_mismatch:
                self._mark_identity_mismatch(record)
                raise ExecutionServiceError(
                    ErrorCode.EXECUTABLE_IDENTITY_MISMATCH,
                    "Executor rejected the prepared executable identity",
                )
            raise ExecutionServiceError(
                ErrorCode.INTERNAL, "Executor did not accept launch", retryable=True
            )
        return self._require(record.run_id).snapshot()

    def _mark_identity_mismatch(self, record: ExecutionAggregate) -> None:
        """Atomically fail only the still-current queued intent rejected by the executor."""
        latest = self._require(record.run_id)
        if latest.state is not RunState.QUEUED or latest.launch_token != record.launch_token:
            return
        try:
            self._repository.compare_and_mutate(
                latest.run_id,
                latest.revision,
                lambda current: (replace(current, state=RunState.FAILED), "failed"),
            )
        except RepositoryConflict:
            return
        except RepositoryMutationError as error:
            raise ExecutionServiceError(ErrorCode.INTERNAL, str(error), retryable=True) from error

    def _claim_cancel(self, record: ExecutionAggregate) -> ExecutionAggregate:
        """Atomically persist a stable cancellation token while retaining current state."""
        while True:
            if record.cancel_pending:
                return record
            if record.state in TERMINAL_STATES:
                raise _terminal_error(record.run_id)
            token = f"{record.run_id}.cancel.{record.revision + 1}"
            try:
                return self._repository.compare_and_mutate(
                    record.run_id,
                    record.revision,
                    lambda current, token=token: (
                        replace(current, cancel_token=token, cancel_pending=True),
                        "cancel_requested",
                    ),
                )
            except RepositoryConflict:
                record = self._require(record.run_id)
            except RepositoryMutationError as error:
                raise ExecutionServiceError(
                    ErrorCode.INTERNAL, str(error), retryable=True
                ) from error

    def _ensure_cancel(self, record: ExecutionAggregate) -> RunSnapshot:
        """Confirm cancellation outside CAS, then atomically enter the terminal state."""
        if record.cancel_token is None:
            return record.snapshot()
        if record.launch_token is None:
            return self._commit_cancelled(record)
        try:
            receipt = self._executor.ensure_cancelled(
                CancelRequest(
                    run_id=record.run_id,
                    token=record.cancel_token,
                    launch_token=record.launch_token,
                    attempt=record.attempt,
                )
            )
        except Exception as error:
            raise ExecutionServiceError(
                ErrorCode.INTERNAL, f"Cancel acknowledgement unknown: {error}", retryable=True
            ) from error
        if not receipt.confirmed:
            raise ExecutionServiceError(
                ErrorCode.INTERNAL, "Executor did not confirm cancellation", retryable=True
            )
        latest = self._require(record.run_id)
        if latest.state in TERMINAL_STATES:
            raise _terminal_error(record.run_id)
        if latest.cancel_token != record.cancel_token or not latest.cancel_pending:
            return latest.snapshot()
        return self._commit_cancelled(latest)

    def _commit_cancelled(self, record: ExecutionAggregate) -> RunSnapshot:
        """Atomically finish a cancellation that has no executor work to stop."""
        try:
            return self._repository.compare_and_mutate(
                record.run_id,
                record.revision,
                lambda current: (
                    replace(current, state=RunState.CANCELLED, cancel_pending=False),
                    "cancelled",
                ),
            ).snapshot()
        except RepositoryConflict:
            latest = self._require(record.run_id)
            if latest.state in TERMINAL_STATES:
                raise _terminal_error(record.run_id) from None
            return latest.snapshot()
        except RepositoryMutationError as error:
            raise ExecutionServiceError(ErrorCode.INTERNAL, str(error), retryable=True) from error

    def _lifecycle_mutate(
        self,
        run_id: str,
        token: str,
        *,
        allowed_state: RunState,
        event_type: str,
        mutate: Callable[[ExecutionAggregate], ExecutionAggregate],
        allow_cancel_pending: bool = False,
    ) -> RunSnapshot:
        """CAS a lifecycle callback only while its token remains current and legal."""
        while True:
            record = self._require(run_id)
            if record.launch_token != token:
                raise ExecutionServiceError(
                    ErrorCode.INVALID_STATE_TRANSITION,
                    "Lifecycle token does not match the active launch attempt",
                )
            if record.state is not allowed_state or (
                record.cancel_pending and not allow_cancel_pending
            ):
                raise ExecutionServiceError(
                    ErrorCode.INVALID_STATE_TRANSITION,
                    f"Lifecycle callback is invalid while run is {record.state.value}",
                )
            try:
                return self._repository.compare_and_mutate(
                    run_id,
                    record.revision,
                    lambda current: (mutate(current), event_type),
                ).snapshot()
            except RepositoryConflict:
                continue
            except RepositoryMutationError as error:
                raise ExecutionServiceError(
                    ErrorCode.INTERNAL, str(error), retryable=True
                ) from error

    def _verify_identity(self, expected: ExecutableIdentity) -> None:
        """Measure and compare before durable launch claiming or external execution."""
        measured = self._identity_verifier.measure()
        if not _identities_match(expected, measured):
            raise ExecutionServiceError(
                ErrorCode.EXECUTABLE_IDENTITY_MISMATCH,
                "Measured executable identity does not match the prepared identity",
            )

    def _require(self, run_id: str) -> ExecutionAggregate:
        record = self._repository.read(run_id)
        if record is None:
            raise ExecutionServiceError(ErrorCode.UNKNOWN_RUN, f"Unknown run: {run_id}")
        return record
