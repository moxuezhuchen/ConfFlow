"""Phase C aggregate contracts, including multi-service and failure interleavings."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from threading import Event

import pytest

from confflow.application.execution import (
    Artifact,
    CancelReceipt,
    ErrorCode,
    ExecutableIdentity,
    ExecutionAggregate,
    ExecutionLifecycle,
    ExecutionService,
    ExecutionServiceError,
    InMemoryExecutionRepository,
    LaunchReceipt,
    PrepareRequest,
    RunState,
)

IDENTITY = ExecutableIdentity(sha256="e" * 64, realpath="/opt/g16/g16")


def _request(*, run_id: str = "run-001", key: str = "key-001", digest: str = "a") -> PrepareRequest:
    """Build a complete protocol-valid prepare request."""
    return PrepareRequest(
        run_id=run_id,
        idempotency_key=key,
        request_digest=digest * 64,
        workflow_config_digest="b" * 64,
        input_manifest_digest="c" * 64,
        expected_executable_identity=IDENTITY,
    )


@dataclass
class FakeIdentityVerifier:
    """Static identity measurement port for service contracts."""

    measured: ExecutableIdentity = IDENTITY
    calls: int = 0

    def measure(self) -> ExecutableIdentity:
        """Return the configured identity without external execution."""
        self.calls += 1
        return self.measured


@dataclass
class FakeExecutor:
    """Token-idempotent non-computing executor with controllable acknowledgement faults."""

    raise_after_accept_once: bool = False
    reject_identity: bool = False
    launch_calls: list[str] = field(default_factory=list)
    accepted_launches: set[str] = field(default_factory=set)
    cancel_calls: list[str] = field(default_factory=list)
    cancel_requests: list = field(default_factory=list)
    accepted_cancels: set[str] = field(default_factory=set)
    fail_cancel_once: bool = False

    def ensure_launched(self, request) -> LaunchReceipt:
        """Accept the token exactly once even if transport acknowledgement fails."""
        self.launch_calls.append(request.token)
        if self.reject_identity:
            return LaunchReceipt(accepted=False, identity_mismatch=True)
        self.accepted_launches.add(request.token)
        if self.raise_after_accept_once:
            self.raise_after_accept_once = False
            raise RuntimeError("transport dropped after accept")
        return LaunchReceipt(accepted=True)

    def ensure_cancelled(self, request) -> CancelReceipt:
        """Confirm cancellation idempotently, with an optional lost acknowledgement."""
        self.cancel_calls.append(request.token)
        self.cancel_requests.append(request)
        if self.fail_cancel_once:
            self.fail_cancel_once = False
            raise RuntimeError("cancel acknowledgement lost")
        self.accepted_cancels.add(request.token)
        return CancelReceipt(confirmed=True)


class BlockingCancelExecutor(FakeExecutor):
    """Executor that permits a deterministic terminal-versus-cancel race."""

    def __init__(self) -> None:
        super().__init__()
        self.entered = Event()
        self.release = Event()

    def ensure_cancelled(self, request) -> CancelReceipt:
        """Wait after intent dispatch, outside the aggregate transaction."""
        self.cancel_calls.append(request.token)
        self.cancel_requests.append(request)
        self.entered.set()
        assert self.release.wait(timeout=2)
        self.accepted_cancels.add(request.token)
        return CancelReceipt(confirmed=True)


class BlockingLaunchExecutor(FakeExecutor):
    """Executor that proves cancel tombstones an already in-flight launch token."""

    def __init__(self) -> None:
        super().__init__()
        self.launch_entered = Event()
        self.release_launch = Event()
        self.tombstoned_launches: set[str] = set()
        self.started_launches: set[str] = set()

    def ensure_launched(self, request) -> LaunchReceipt:
        """Wait until cancellation can win the executor-side token arbitration."""
        self.launch_calls.append(request.token)
        self.launch_entered.set()
        assert self.release_launch.wait(timeout=2)
        if request.token in self.tombstoned_launches:
            return LaunchReceipt(accepted=False, cancelled=True)
        self.accepted_launches.add(request.token)
        self.started_launches.add(request.token)
        return LaunchReceipt(accepted=True)

    def ensure_cancelled(self, request) -> CancelReceipt:
        """Install the launch-token tombstone before confirming cancellation."""
        self.cancel_calls.append(request.token)
        self.cancel_requests.append(request)
        if request.launch_token is not None:
            self.tombstoned_launches.add(request.launch_token)
        self.accepted_cancels.add(request.token)
        return CancelReceipt(confirmed=True)


def _service(
    repository: InMemoryExecutionRepository,
    executor: FakeExecutor,
    verifier: FakeIdentityVerifier | None = None,
) -> ExecutionService:
    """Build one service instance; tests intentionally share repositories across instances."""
    return ExecutionService(
        repository=repository,
        executor=executor,
        identity_verifier=verifier or FakeIdentityVerifier(),
        event_page_size=2,
    )


def _running(service: ExecutionService, executor: FakeExecutor) -> ExecutionLifecycle:
    """Prepare and start one fake token-bound workflow."""
    service.prepare(_request())
    assert service.execute("run-001").state is RunState.QUEUED
    token = service.status("run-001").revision
    del token
    aggregate = service._repository.read("run-001")  # noqa: SLF001 - contract callback setup
    assert aggregate is not None and aggregate.launch_token is not None
    lifecycle = ExecutionLifecycle(service, "run-001", aggregate.launch_token)
    assert lifecycle.started().state is RunState.RUNNING
    return lifecycle


def test_two_services_concurrent_execute_share_one_launch_token():
    """Aggregate CAS gives concurrent service instances one durable launch attempt."""
    repository = InMemoryExecutionRepository()
    executor = FakeExecutor()
    first = _service(repository, executor)
    second = _service(repository, executor)
    first.prepare(_request())

    with ThreadPoolExecutor(max_workers=2) as pool:
        snapshots = list(pool.map(lambda service: service.execute("run-001"), (first, second)))

    aggregate = repository.read("run-001")
    assert aggregate is not None
    assert {snapshot.state for snapshot in snapshots} == {RunState.QUEUED}
    assert aggregate.attempt == 1
    assert len(executor.accepted_launches) == 1
    assert all(token == aggregate.launch_token for token in executor.launch_calls)


def test_first_execute_is_queued_and_duplicate_returns_current_snapshot():
    """Accepted launch receipts never silently turn queued into running."""
    repository = InMemoryExecutionRepository()
    executor = FakeExecutor()
    service = _service(repository, executor)
    service.prepare(_request())

    first = service.execute("run-001")
    duplicate = service.execute("run-001")

    assert first == duplicate
    assert first.state is RunState.QUEUED
    assert first.revision == 2
    assert len(executor.accepted_launches) == 1


def test_prepare_same_key_returns_original_and_different_digest_conflicts():
    """Prepare idempotency is durable and cannot bind a changed request digest."""
    repository = InMemoryExecutionRepository()
    service = _service(repository, FakeExecutor())
    original = service.prepare(_request())

    assert service.prepare(_request()) == original
    with pytest.raises(ExecutionServiceError) as error:
        service.prepare(_request(digest="d"))
    assert error.value.code is ErrorCode.IDEMPOTENCY_CONFLICT


def test_duplicate_execute_returns_running_then_terminal_current_snapshot():
    """Duplicate execute never starts another process after lifecycle progress or terminal state."""
    repository = InMemoryExecutionRepository()
    executor = FakeExecutor()
    service = _service(repository, executor)
    lifecycle = _running(service, executor)

    assert service.execute("run-001").state is RunState.RUNNING
    assert lifecycle.completed().state is RunState.COMPLETED
    assert service.execute("run-001").state is RunState.COMPLETED
    assert len(executor.accepted_launches) == 1


def test_accepted_launch_with_lost_acknowledgement_retries_same_token():
    """Unknown acknowledgement preserves queued intent and cannot create another launch."""
    repository = InMemoryExecutionRepository()
    executor = FakeExecutor(raise_after_accept_once=True)
    service = _service(repository, executor)
    service.prepare(_request())

    with pytest.raises(ExecutionServiceError) as error:
        service.execute("run-001")
    assert error.value.code is ErrorCode.INTERNAL
    queued = service.status("run-001")
    aggregate = repository.read("run-001")
    assert aggregate is not None and aggregate.launch_token is not None
    assert queued.state is RunState.QUEUED
    assert service.execute("run-001") == queued
    assert len(executor.accepted_launches) == 1
    assert executor.launch_calls == [aggregate.launch_token, aggregate.launch_token]


def test_identity_mismatch_before_claim_writes_no_launch_intent():
    """Service-side verification fails closed before any queue/intent mutation."""
    repository = InMemoryExecutionRepository()
    executor = FakeExecutor()
    service = _service(repository, executor, FakeIdentityVerifier(ExecutableIdentity(sha256="f" * 64)))
    service.prepare(_request())

    with pytest.raises(ExecutionServiceError) as error:
        service.execute("run-001")

    aggregate = repository.read("run-001")
    assert error.value.code is ErrorCode.EXECUTABLE_IDENTITY_MISMATCH
    assert aggregate is not None and aggregate.state is RunState.PREPARED
    assert aggregate.launch_token is None
    assert executor.launch_calls == []


def test_executor_identity_rejection_fails_the_claimed_queued_attempt():
    """The execution boundary guard turns explicit identity rejection into failed."""
    repository = InMemoryExecutionRepository()
    service = _service(repository, FakeExecutor(reject_identity=True))
    service.prepare(_request())

    with pytest.raises(ExecutionServiceError) as error:
        service.execute("run-001")

    assert error.value.code is ErrorCode.EXECUTABLE_IDENTITY_MISMATCH
    assert service.status("run-001").state is RunState.FAILED
    assert [event.type for event in service.events("run-001").events] == ["prepared", "queued"]


def test_cancel_side_effect_failure_retries_the_same_durable_intent():
    """Cancellation is not claimed complete until the executor confirms its token."""
    repository = InMemoryExecutionRepository()
    executor = FakeExecutor(fail_cancel_once=True)
    service = _service(repository, executor)
    lifecycle = _running(service, executor)
    del lifecycle

    with pytest.raises(ExecutionServiceError) as error:
        service.cancel("run-001")
    pending = repository.read("run-001")
    assert error.value.code is ErrorCode.INTERNAL
    assert pending is not None and pending.state is RunState.RUNNING and pending.cancel_pending
    assert pending.cancel_token is not None

    cancelled = service.cancel("run-001")
    assert cancelled.state is RunState.CANCELLED
    assert executor.cancel_calls == [pending.cancel_token, pending.cancel_token]
    assert executor.cancel_requests[-1].launch_token == pending.launch_token
    assert executor.cancel_requests[-1].attempt == pending.attempt


def test_cancel_tombstone_wins_against_an_in_flight_launch():
    """Confirmed cancel prevents a blocked older launch token from starting work."""
    repository = InMemoryExecutionRepository()
    executor = BlockingLaunchExecutor()
    first = _service(repository, executor)
    second = _service(repository, executor)
    first.prepare(_request())

    with ThreadPoolExecutor(max_workers=1) as pool:
        in_flight_execute = pool.submit(first.execute, "run-001")
        assert executor.launch_entered.wait(timeout=2)
        assert second.cancel("run-001").state is RunState.CANCELLED
        executor.release_launch.set()
        assert in_flight_execute.result().state is RunState.CANCELLED

    aggregate = repository.read("run-001")
    assert aggregate is not None and aggregate.launch_token is not None
    assert aggregate.state is RunState.CANCELLED
    assert aggregate.launch_token in executor.tombstoned_launches
    assert aggregate.launch_token not in executor.started_launches


def test_terminal_callback_wins_a_cancel_confirmation_race():
    """A confirmed late cancellation can never overwrite an earlier terminal callback."""
    repository = InMemoryExecutionRepository()
    executor = BlockingCancelExecutor()
    service = _service(repository, executor)
    lifecycle = _running(service, executor)

    with ThreadPoolExecutor(max_workers=1) as pool:
        pending_cancel = pool.submit(service.cancel, "run-001")
        assert executor.entered.wait(timeout=2)
        assert lifecycle.completed().state is RunState.COMPLETED
        executor.release.set()
        with pytest.raises(ExecutionServiceError) as error:
            pending_cancel.result()

    assert error.value.code is ErrorCode.TERMINAL_RUN
    assert service.status("run-001").state is RunState.COMPLETED


def test_resume_generates_a_new_token_with_the_current_checkpoint():
    """Resume pre-verifies, queues a new attempt, and forwards its checkpoint."""
    repository = InMemoryExecutionRepository()
    executor = FakeExecutor()
    service = _service(repository, executor)
    lifecycle = _running(service, executor)
    lifecycle.checkpoint("checkpoint-1")
    lifecycle.paused()
    first_token = repository.read("run-001").launch_token  # type: ignore[union-attr]

    resumed = service.resume("run-001")
    aggregate = repository.read("run-001")
    assert aggregate is not None
    assert resumed.state is RunState.QUEUED
    assert aggregate.attempt == 2
    assert aggregate.launch_token != first_token
    assert aggregate.launch_checkpoint == "checkpoint-1"
    assert executor.launch_calls[-1] == aggregate.launch_token


def test_stale_lifecycle_token_cannot_modify_the_resumed_attempt():
    """Callbacks for the old attempt are rejected by aggregate token CAS."""
    repository = InMemoryExecutionRepository()
    executor = FakeExecutor()
    service = _service(repository, executor)
    old_lifecycle = _running(service, executor)
    old_lifecycle.checkpoint("checkpoint-1")
    old_lifecycle.paused()
    service.resume("run-001")

    assert old_lifecycle.started().state is RunState.QUEUED
    aggregate = repository.read("run-001")
    assert aggregate is not None and aggregate.launch_token is not None
    new_lifecycle = ExecutionLifecycle(service, "run-001", aggregate.launch_token)
    assert new_lifecycle.started().state is RunState.RUNNING


def test_injected_repository_failure_does_not_tear_record_event_or_intent():
    """A pre-commit repository failure leaves the original aggregate completely intact."""
    repository = InMemoryExecutionRepository()
    executor = FakeExecutor()
    service = _service(repository, executor)
    service.prepare(_request())
    repository.fail_next_mutation()

    with pytest.raises(ExecutionServiceError) as error:
        service.execute("run-001")

    aggregate = repository.read("run-001")
    assert error.value.code is ErrorCode.INTERNAL
    assert aggregate is not None
    assert aggregate.state is RunState.PREPARED and aggregate.revision == 1
    assert aggregate.launch_token is None
    assert [event.type for event in aggregate.events] == ["prepared"]
    assert executor.launch_calls == []


def test_terminal_artifacts_validate_inside_the_atomic_terminal_mutation():
    """Bad paths leave running state and prior event stream untouched."""
    repository = InMemoryExecutionRepository()
    executor = FakeExecutor()
    service = _service(repository, executor)
    lifecycle = _running(service, executor)
    before = service.status("run-001")
    invalid = Artifact(
        terminal="g16_opt",
        path="../escape.xyz",
        sha256="d" * 64,
        size=1,
        content_schema="confflow.output_manifest.v1",
    )

    with pytest.raises(ExecutionServiceError) as error:
        lifecycle.completed((invalid,))
    assert error.value.code is ErrorCode.ARTIFACT_PATH_INVALID
    assert service.status("run-001") == before

    valid = Artifact(
        terminal="g16_opt",
        path="g16_opt/output.xyz",
        sha256="d" * 64,
        size=1,
        content_schema="confflow.output_manifest.v1",
    )
    assert lifecycle.completed((valid,)).state is RunState.COMPLETED
    assert service.artifacts("run-001").artifacts == (valid,)


def test_events_cursor_order_pagination_empty_reconnect_and_unknown():
    """Revision-derived cursors replay strictly, page deterministically, and fail closed."""
    repository = InMemoryExecutionRepository()
    executor = FakeExecutor()
    service = _service(repository, executor)
    lifecycle = _running(service, executor)
    lifecycle.checkpoint("checkpoint-1")
    lifecycle.paused()

    first = service.events("run-001")
    second = service.events("run-001", after=first.next_cursor)
    third = service.events("run-001", after=second.next_cursor)
    empty = service.events("run-001", after=third.next_cursor)

    replay = first.events + second.events + third.events
    assert [event.revision for event in replay] == [1, 2, 3, 4, 5]
    assert [event.cursor for event in replay] == [f"r{number:020d}" for number in range(1, 6)]
    assert empty.events == ()
    assert empty.next_cursor == third.next_cursor
    with pytest.raises(ExecutionServiceError) as error:
        service.events("run-001", after="unknown")
    assert error.value.code is ErrorCode.INVALID_REQUEST


def test_initial_empty_event_page_returns_null_cursor():
    """The RFC initial-empty rule holds for a recovered aggregate with no events."""
    aggregate = ExecutionAggregate(
        run_id="recovered-001",
        idempotency_key="key-recovered",
        request_digest="a" * 64,
        workflow_config_digest="b" * 64,
        input_manifest_digest="c" * 64,
        expected_executable_identity=IDENTITY,
        revision=0,
        state=RunState.PREPARED,
    )

    class ReadOnlyRepository:
        def read(self, run_id: str):
            return aggregate if run_id == aggregate.run_id else None

    service = ExecutionService(
        repository=ReadOnlyRepository(), executor=FakeExecutor(), identity_verifier=FakeIdentityVerifier()
    )
    page = service.events("recovered-001")
    assert page.events == ()
    assert page.next_cursor is None


def test_resume_rejects_missing_stale_running_and_terminal_checkpoints():
    """The resume sequence matrix distinguishes every invalid state/checkpoint case."""
    missing_repo = InMemoryExecutionRepository()
    missing_executor = FakeExecutor()
    missing = _service(missing_repo, missing_executor)
    missing_lifecycle = _running(missing, missing_executor)
    missing_lifecycle.paused()
    with pytest.raises(ExecutionServiceError) as error:
        missing.resume("run-001")
    assert error.value.code is ErrorCode.INVALID_CHECKPOINT

    stale_repo = InMemoryExecutionRepository()
    stale_executor = FakeExecutor()
    stale = _service(stale_repo, stale_executor)
    stale_lifecycle = _running(stale, stale_executor)
    stale_lifecycle.checkpoint("checkpoint-old")
    stale_lifecycle.checkpoint("checkpoint-current")
    stale_lifecycle.paused()
    with pytest.raises(ExecutionServiceError) as error:
        stale.resume("run-001", checkpoint_id="checkpoint-old")
    assert error.value.code is ErrorCode.INVALID_CHECKPOINT
    assert stale.resume("run-001", checkpoint_id="checkpoint-current").state is RunState.QUEUED

    running_repo = InMemoryExecutionRepository()
    running_executor = FakeExecutor()
    running = _service(running_repo, running_executor)
    running_lifecycle = _running(running, running_executor)
    with pytest.raises(ExecutionServiceError) as error:
        running.resume("run-001")
    assert error.value.code is ErrorCode.INVALID_STATE_TRANSITION
    running_lifecycle.completed()
    with pytest.raises(ExecutionServiceError) as error:
        running.resume("run-001")
    assert error.value.code is ErrorCode.TERMINAL_RUN


def test_duplicate_artifact_target_rejects_the_terminal_mutation_atomically():
    """Two entries resolving to one terminal target leave the active aggregate unchanged."""
    repository = InMemoryExecutionRepository()
    executor = FakeExecutor()
    service = _service(repository, executor)
    lifecycle = _running(service, executor)
    artifact = Artifact(
        terminal="g16_opt",
        path="g16_opt/output.xyz",
        sha256="d" * 64,
        size=1,
        content_schema="confflow.output_manifest.v1",
    )

    with pytest.raises(ExecutionServiceError) as error:
        lifecycle.completed((artifact, artifact))
    assert error.value.code is ErrorCode.ARTIFACT_PATH_INVALID
    assert service.status("run-001").state is RunState.RUNNING
