"""Synthetic producer lifecycle fixture: contract, safety and attach tests.

The fixture is an explicit opt-in ``WorkflowExecutor`` whose durable facts are
produced only through ``ExecutionService`` and ``ExecutionLifecycle``.  These
tests prove default-off behavior, fixed-payload restrictions, single-worker
idempotency, response-lost attach/reconnect and that the fixture never writes
producer SQLite/state or bypasses the control adapter.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path

import pytest
import rfc8785

from confflow.application.execution import (
    Artifact,
    CancelReceipt,
    CancelRequest,
    ErrorCode,
    ExecutableIdentity,
    ExecutionLifecycle,
    ExecutionService,
    ExecutionServiceError,
    LaunchReceipt,
    LaunchRequest,
    PrepareRequest,
    RunState,
    SQLiteExecutionRepository,
    StateRoot,
)
from confflow.application.execution.synthetic_producer import (
    SYNTHETIC_ARTIFACT,
    SYNTHETIC_ARTIFACT_CONTENT,
    SYNTHETIC_ARTIFACT_PATH,
    SYNTHETIC_ARTIFACT_SCHEMA,
    SYNTHETIC_ARTIFACT_TERMINAL,
    SYNTHETIC_CHECKPOINT_ID,
    SyntheticProducerExecutor,
    open_synthetic_service,
)
from confflow.application.execution.workflow_adapter import (
    FileIdentityVerifier,
    executor_identity,
    measure_executable,
    open_control_service,
)
from confflow.control import main as control_main

TERMINAL = frozenset({RunState.COMPLETED, RunState.FAILED, RunState.CANCELLED})


@dataclass
class SpyExecutor:
    """Port spy proving the fixture consumes the formal executor boundary."""

    inner: SyntheticProducerExecutor
    launch_calls: list[LaunchRequest] = field(default_factory=list)
    cancel_calls: list[CancelRequest] = field(default_factory=list)

    def ensure_launched(self, request: LaunchRequest) -> LaunchReceipt:
        self.launch_calls.append(request)
        return self.inner.ensure_launched(request)

    def ensure_cancelled(self, request: CancelRequest) -> CancelReceipt:
        self.cancel_calls.append(request)
        return self.inner.ensure_cancelled(request)


def _build(root: Path) -> tuple[ExecutionService, SyntheticProducerExecutor]:
    """Build the opt-in synthetic service over one durable state root."""
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    state = StateRoot.resolve(root)
    executor = SyntheticProducerExecutor(state)
    service = ExecutionService(
        repository=SQLiteExecutionRepository(state),
        executor=executor,
        identity_verifier=FileIdentityVerifier(sys.executable),
    )
    executor.bind(service)
    return service, executor


def _request(
    run_id: str, identity: ExecutableIdentity, *, key: str | None = None, digest: str = "a"
) -> PrepareRequest:
    return PrepareRequest(
        run_id=run_id,
        idempotency_key=key or run_id,
        request_digest=digest * 64,
        workflow_config_digest="b" * 64,
        input_manifest_digest="c" * 64,
        expected_executable_identity=identity,
    )


def _aggregate(root: Path, run_id: str):
    repository = SQLiteExecutionRepository(StateRoot.resolve(root))
    return repository.read(run_id)


def _wait_terminal(service: ExecutionService, run_id: str, *, timeout: float = 15.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        snapshot = service.status(run_id)
        if snapshot.state in TERMINAL:
            return snapshot
        time.sleep(0.02)
    raise AssertionError(f"Run {run_id} did not reach a terminal state in {timeout}s")


def _wait_state(service: ExecutionService, run_id: str, state: RunState, *, timeout: float = 15.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        snapshot = service.status(run_id)
        if snapshot.state is state:
            return snapshot
        time.sleep(0.02)
    raise AssertionError(f"Run {run_id} did not reach {state.value} in {timeout}s")


def _event_types(aggregate) -> list[str]:
    return [event.type for event in aggregate.events]


def _run_dir(root: Path, run_id: str) -> Path:
    return root.parent / f"run_{run_id}"


def _claim_file(root: Path, run_id: str, token: str) -> Path:
    return _run_dir(root, run_id) / f"synthetic.claim.{token}"


# -------------------------------------------------------------------------------------
# Default-off and explicit opt-in
# -------------------------------------------------------------------------------------


def test_open_control_service_never_invokes_the_fixture(tmp_path: Path):
    """The production control adapter is the default; no synthetic worker starts."""
    root = tmp_path / "state"
    service = open_control_service(root)
    from confflow.application.execution.workflow_adapter import _AgentControlExecutor

    assert isinstance(service._executor, _AgentControlExecutor)  # noqa: SLF001
    identity = executor_identity(service)
    run_id = "run-default-off"
    service.prepare(_request(run_id, identity))
    snapshot = service.execute(run_id)
    assert snapshot.state is RunState.QUEUED
    time.sleep(0.4)
    aggregate = _aggregate(root, run_id)
    assert aggregate is not None
    assert aggregate.state is RunState.QUEUED
    assert _event_types(aggregate) == ["prepared", "queued"]
    assert not (root.parent / f"run_{run_id}").exists()


def test_control_cli_execute_leaves_run_queued_without_fixture(tmp_path: Path, capsys):
    """A regular ``confflow control execute`` never drives the fixture."""
    root = tmp_path / "state"
    run_id = "run-cli-default"
    identity = measure_executable(sys.executable)
    payload = {
        "protocol_schema": "confflow.control.v1",
        "operation": "prepare",
        "run_id": run_id,
        "idempotency_key": run_id,
        "workflow_config": {"path": "workflow.yaml", "sha256": "b" * 64},
        "input_manifest": {"path": "inputs/manifest.json", "sha256": "c" * 64},
        "expected_executable_identity": {"sha256": identity.sha256},
    }
    semantic = dict(payload)
    payload["request_digest"] = hashlib.sha256(rfc8785.dumps(semantic)).hexdigest()
    request_path = tmp_path / "prepare.json"
    request_path.write_text(json.dumps(payload), encoding="utf-8")

    assert (
        control_main(
            ["prepare", "--state-root", str(root), "--request", str(request_path), "--json"]
        )
        == 0
    )
    capsys.readouterr()
    code = control_main(["execute", "--state-root", str(root), "--run-id", run_id, "--json"])
    response = json.loads(capsys.readouterr().out)
    assert code == 0
    assert response["state"] == "queued"
    time.sleep(0.4)
    assert control_main(["status", "--state-root", str(root), "--run-id", run_id, "--json"]) == 0
    status = json.loads(capsys.readouterr().out)
    assert status["state"] == "queued"


def test_control_rejects_unknown_fixture_flags(tmp_path: Path, capsys):
    """No hidden CLI surface exists for the fixture; unknown flags are rejected."""
    code = control_main(
        [
            "execute",
            "--state-root",
            str(tmp_path / "state"),
            "--run-id",
            "run-flag",
            "--synthetic",
            "--json",
        ]
    )
    assert code == 1
    response = json.loads(capsys.readouterr().out)
    assert response["ok"] is False
    assert response["error"]["code"] == "invalid_request"


def test_control_prepare_rejects_unknown_synthetic_fields(tmp_path: Path, capsys):
    """Prepare payloads carrying launcher/payload fields fail protocol validation."""
    payload = {
        "protocol_schema": "confflow.control.v1",
        "operation": "prepare",
        "run_id": "run-field",
        "idempotency_key": "run-field",
        "workflow_config": {"path": "workflow.yaml", "sha256": "b" * 64},
        "input_manifest": {"path": "inputs/manifest.json", "sha256": "c" * 64},
        "expected_executable_identity": {"sha256": "d" * 64},
        "synthetic": {"command": "touch /tmp/x", "artifact_path": "/tmp/owned"},
    }
    semantic = dict(payload)
    payload["request_digest"] = hashlib.sha256(rfc8785.dumps(semantic)).hexdigest()
    request_path = tmp_path / "prepare.json"
    request_path.write_text(json.dumps(payload), encoding="utf-8")
    code = control_main(
        [
            "prepare",
            "--state-root",
            str(tmp_path / "state"),
            "--request",
            str(request_path),
            "--json",
        ]
    )
    assert code == 1
    response = json.loads(capsys.readouterr().out)
    assert response["ok"] is False
    assert response["error"]["code"] == "invalid_request"


def test_open_synthetic_service_is_the_explicit_opt_in(tmp_path: Path):
    """The fixture is only reachable through its dedicated factory."""
    root = tmp_path / "state"
    service = open_synthetic_service(root)
    assert isinstance(service, ExecutionService)
    assert isinstance(service._executor, SyntheticProducerExecutor)  # noqa: SLF001


# -------------------------------------------------------------------------------------
# Fixed payload / artifact whitelist
# -------------------------------------------------------------------------------------


def test_fixture_artifact_is_a_fixed_builtin_constant():
    """The fixture payload is one fixed, small, built-in text artifact."""
    expected_digest = hashlib.sha256(SYNTHETIC_ARTIFACT_CONTENT.encode("utf-8")).hexdigest()
    assert SYNTHETIC_ARTIFACT == Artifact(
        terminal=SYNTHETIC_ARTIFACT_TERMINAL,
        path=SYNTHETIC_ARTIFACT_PATH,
        sha256=expected_digest,
        size=len(SYNTHETIC_ARTIFACT_CONTENT),
        content_schema=SYNTHETIC_ARTIFACT_SCHEMA,
    )
    assert isinstance(SYNTHETIC_ARTIFACT_CONTENT, str)
    assert 1 <= len(SYNTHETIC_ARTIFACT_CONTENT) <= 256
    assert "\x00" not in SYNTHETIC_ARTIFACT_CONTENT


def test_prepare_payloads_do_not_reach_the_fixture(tmp_path: Path):
    """Workflow/input digests are bindings only; garbage-but-valid digests run fine."""
    root = tmp_path / "state"
    service, executor = _build(root)
    identity = executor_identity(service)
    run_id = "run-fixed-payload"
    request = _request(run_id, identity, digest="f")
    service.prepare(request)
    service.execute(run_id)
    _wait_terminal(service, run_id)
    aggregate = _aggregate(root, run_id)
    assert aggregate is not None
    assert aggregate.state is RunState.COMPLETED
    assert aggregate.artifacts == (SYNTHETIC_ARTIFACT,)
    assert not executor.worker_errors


@pytest.mark.parametrize(
    "identity",
    [
        ExecutableIdentity(sha256="0" * 64),
        measure_executable("/bin/sh"),
    ],
)
def test_external_executable_identity_is_rejected_before_launch(
    tmp_path: Path, identity: ExecutableIdentity
):
    """Preparing an external executable identity never launches it."""
    root = tmp_path / "state"
    service, executor = _build(root)
    run_id = "run-external-exe"
    service.prepare(_request(run_id, identity))
    with pytest.raises(ExecutionServiceError) as caught:
        service.execute(run_id)
    assert caught.value.code is ErrorCode.EXECUTABLE_IDENTITY_MISMATCH
    snapshot = service.status(run_id)
    assert snapshot.state is RunState.PREPARED
    assert _event_types(_aggregate(root, run_id)) == ["prepared"]
    assert executor.worker_count == 0


def _manually_driven_service(root: Path) -> tuple[ExecutionService, str, str]:
    """Build the default (non-driving) control service for deterministic tests.

    The manifest and cancel legality tests must hold the run in RUNNING state
    while injecting invalid inputs; a real fixture worker would finish first.
    The lifecycle surface used here is the same official one the fixture uses.
    """
    service = open_control_service(root)
    identity = executor_identity(service)
    run_id = "run-manual-drive"
    service.prepare(_request(run_id, identity))
    service.execute(run_id)
    aggregate = _aggregate(root, run_id)
    assert aggregate is not None and aggregate.launch_token is not None
    snapshot = ExecutionLifecycle(service, run_id, aggregate.launch_token).started()
    assert snapshot.state is RunState.RUNNING
    return service, run_id, aggregate.launch_token


# -------------------------------------------------------------------------------------
# Manifest validation rejections through the official lifecycle surface
# -------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "artifact",
    [
        Artifact("synthetic", "/abs/path.txt", "f" * 64, 4, SYNTHETIC_ARTIFACT_SCHEMA),
        Artifact("synthetic", "a/../b.txt", "f" * 64, 4, SYNTHETIC_ARTIFACT_SCHEMA),
        Artifact("synthetic", "a/./b.txt", "f" * 64, 4, SYNTHETIC_ARTIFACT_SCHEMA),
        Artifact("synthetic", "a//b.txt", "f" * 64, 4, SYNTHETIC_ARTIFACT_SCHEMA),
        Artifact("synthetic", "a/b/", "f" * 64, 4, SYNTHETIC_ARTIFACT_SCHEMA),
        Artifact("synthetic", "", "f" * 64, 4, SYNTHETIC_ARTIFACT_SCHEMA),
        Artifact("synthetic", "ok.txt", "not-a-digest", 4, SYNTHETIC_ARTIFACT_SCHEMA),
        Artifact("synthetic", "ok.txt", "f" * 63, 4, SYNTHETIC_ARTIFACT_SCHEMA),
        Artifact("synthetic", "ok.txt", "F" * 64, 4, SYNTHETIC_ARTIFACT_SCHEMA),
        Artifact("synthetic", "ok.txt", "f" * 64, -1, SYNTHETIC_ARTIFACT_SCHEMA),
        Artifact("synthetic", "ok.txt", "f" * 64, 4, ""),
        Artifact("-bad-terminal", "ok.txt", "f" * 64, 4, SYNTHETIC_ARTIFACT_SCHEMA),
    ],
)
def test_terminal_manifest_rejects_invalid_artifacts(tmp_path: Path, artifact: Artifact):
    """Path, size and digest validation rejects every illegal manifest entry."""
    root = tmp_path / "state"
    service, run_id, token = _manually_driven_service(root)
    lifecycle = ExecutionLifecycle(service, run_id, token)
    with pytest.raises(ExecutionServiceError) as caught:
        lifecycle.completed([artifact])
    assert caught.value.code is ErrorCode.ARTIFACT_PATH_INVALID
    assert service.status(run_id).state is RunState.RUNNING
    assert _aggregate(root, run_id).artifacts == ()
    lifecycle.completed([SYNTHETIC_ARTIFACT])
    assert _aggregate(root, run_id).artifacts == (SYNTHETIC_ARTIFACT,)


def test_terminal_manifest_rejects_duplicate_terminal_path_pairs(tmp_path: Path):
    """A repeated (terminal, path) entry aborts the whole manifest mutation."""
    root = tmp_path / "state"
    service, run_id, token = _manually_driven_service(root)
    duplicate = Artifact(
        SYNTHETIC_ARTIFACT_TERMINAL,
        SYNTHETIC_ARTIFACT_PATH,
        SYNTHETIC_ARTIFACT.sha256,
        SYNTHETIC_ARTIFACT.size,
        SYNTHETIC_ARTIFACT_SCHEMA,
    )
    with pytest.raises(ExecutionServiceError) as caught:
        ExecutionLifecycle(service, run_id, token).completed([SYNTHETIC_ARTIFACT, duplicate])
    assert caught.value.code is ErrorCode.ARTIFACT_PATH_INVALID


# -------------------------------------------------------------------------------------
# Full lifecycle through prepare + execute
# -------------------------------------------------------------------------------------


def test_service_execute_produces_full_terminal_lifecycle(tmp_path: Path):
    """Prepared -> queued -> running -> terminal with events, cursors and manifest."""
    root = tmp_path / "state"
    service, executor = _build(root)
    identity = executor_identity(service)
    run_id = "synthetic-run-001"
    service.prepare(_request(run_id, identity))
    assert service.status(run_id).state is RunState.PREPARED
    queued = service.execute(run_id)
    assert queued.state is RunState.QUEUED
    terminal = _wait_terminal(service, run_id)
    assert terminal.state is RunState.COMPLETED
    assert terminal.revision == 5
    assert executor.worker_count == 1
    assert not executor.worker_errors

    aggregate = _aggregate(root, run_id)
    assert aggregate is not None
    assert aggregate.attempt == 1
    token = f"{run_id}.launch.1"
    assert aggregate.launch_token == token
    assert aggregate.checkpoint is not None
    assert aggregate.checkpoint.checkpoint_id == SYNTHETIC_CHECKPOINT_ID
    assert aggregate.artifacts == (SYNTHETIC_ARTIFACT,)
    assert _event_types(aggregate) == ["prepared", "queued", "running", "checkpointed", "completed"]
    assert [event.revision for event in aggregate.events] == [1, 2, 3, 4, 5]
    assert [event.cursor for event in aggregate.events] == [
        f"r{revision:020d}" for revision in (1, 2, 3, 4, 5)
    ]

    run_dir = _run_dir(root, run_id)
    artifact_file = run_dir / "synthetic" / "artifact.txt"
    assert artifact_file.is_file()
    assert artifact_file.read_bytes() == SYNTHETIC_ARTIFACT_CONTENT.encode("utf-8")
    assert artifact_file.stat().st_size == SYNTHETIC_ARTIFACT.size
    assert hashlib.sha256(artifact_file.read_bytes()).hexdigest() == SYNTHETIC_ARTIFACT.sha256
    claim = _claim_file(root, run_id, token)
    assert claim.is_file()
    assert json.loads(claim.read_text(encoding="utf-8")) == {
        "v": 1,
        "run_id": run_id,
        "token": token,
    }


def test_control_prepare_execute_and_artifacts_end_to_end(tmp_path: Path, capsys):
    """Control prepare, then the opt-in service execute, then control artifacts."""
    root = tmp_path / "state"
    run_id = "synthetic-run-002"
    identity = measure_executable(sys.executable)
    payload = {
        "protocol_schema": "confflow.control.v1",
        "operation": "prepare",
        "run_id": run_id,
        "idempotency_key": run_id,
        "workflow_config": {"path": "workflow.yaml", "sha256": "b" * 64},
        "input_manifest": {"path": "inputs/manifest.json", "sha256": "c" * 64},
        "expected_executable_identity": {"sha256": identity.sha256},
    }
    semantic = dict(payload)
    payload["request_digest"] = hashlib.sha256(rfc8785.dumps(semantic)).hexdigest()
    request_path = tmp_path / "prepare.json"
    request_path.write_text(json.dumps(payload), encoding="utf-8")
    assert (
        control_main(
            ["prepare", "--state-root", str(root), "--request", str(request_path), "--json"]
        )
        == 0
    )
    assert json.loads(capsys.readouterr().out)["state"] == "prepared"

    service = open_synthetic_service(root)
    service.execute(run_id)
    terminal = _wait_terminal(service, run_id)
    assert terminal.state is RunState.COMPLETED

    assert control_main(["artifacts", "--state-root", str(root), "--run-id", run_id, "--json"]) == 0
    response = json.loads(capsys.readouterr().out)
    assert response["ok"] is True
    assert response["state"] == "completed"
    assert response["artifacts"] == [
        {
            "terminal": SYNTHETIC_ARTIFACT_TERMINAL,
            "path": SYNTHETIC_ARTIFACT_PATH,
            "sha256": SYNTHETIC_ARTIFACT.sha256,
            "size": SYNTHETIC_ARTIFACT.size,
            "content_schema": SYNTHETIC_ARTIFACT_SCHEMA,
        }
    ]


# -------------------------------------------------------------------------------------
# Checkpoint and cancel/terminal legality
# -------------------------------------------------------------------------------------


def test_cancel_before_launch_is_terminal_without_executor_work(tmp_path: Path):
    """Cancelling a prepared run reaches CANCELLED without touching the executor."""
    root = tmp_path / "state"
    service, executor = _build(root)
    spy = SpyExecutor(executor)
    service = ExecutionService(
        repository=SQLiteExecutionRepository(StateRoot.resolve(root)),
        executor=spy,
        identity_verifier=FileIdentityVerifier(sys.executable),
    )
    executor.bind(service)
    identity = executor_identity(service)
    run_id = "run-cancel-early"
    service.prepare(_request(run_id, identity))
    cancelled = service.cancel(run_id)
    assert cancelled.state is RunState.CANCELLED
    assert spy.launch_calls == []
    assert spy.cancel_calls == []
    assert service.execute(run_id).state is RunState.CANCELLED
    assert _event_types(_aggregate(root, run_id)) == ["prepared", "cancel_requested", "cancelled"]
    with pytest.raises(ExecutionServiceError) as caught:
        service.cancel(run_id)
    assert caught.value.code is ErrorCode.TERMINAL_RUN


def test_cancel_after_started_is_a_legal_terminal(tmp_path: Path):
    """Cancel during running ends in exactly one legal terminal with no replay."""
    root = tmp_path / "state"
    service, run_id, token = _manually_driven_service(root)
    cancelled = service.cancel(run_id)
    assert cancelled.state is RunState.CANCELLED
    aggregate = _aggregate(root, run_id)
    assert aggregate is not None
    assert _event_types(aggregate) == [
        "prepared",
        "queued",
        "running",
        "cancel_requested",
        "cancelled",
    ]
    assert service.artifacts(run_id).artifacts == ()
    time.sleep(0.2)
    assert _event_types(_aggregate(root, run_id)) == [
        "prepared",
        "queued",
        "running",
        "cancel_requested",
        "cancelled",
    ]
    with pytest.raises(ExecutionServiceError) as caught:
        service.cancel(run_id)
    assert caught.value.code is ErrorCode.TERMINAL_RUN
    with pytest.raises(ExecutionServiceError) as caught:
        ExecutionLifecycle(service, run_id, token).completed([SYNTHETIC_ARTIFACT])
    assert caught.value.code is ErrorCode.INVALID_STATE_TRANSITION


def test_lifecycle_callbacks_are_token_bound_and_terminal_stable(tmp_path: Path):
    """Stale tokens and terminal replays are rejected without overwriting anything."""
    root = tmp_path / "state"
    service, run_id, token = _manually_driven_service(root)
    with pytest.raises(ExecutionServiceError) as caught:
        ExecutionLifecycle(service, run_id, "forged-token").checkpoint("fixture.ready")
    assert caught.value.code is ErrorCode.INVALID_STATE_TRANSITION
    with pytest.raises(ExecutionServiceError) as caught:
        ExecutionLifecycle(service, run_id, token).checkpoint("")
    assert caught.value.code is ErrorCode.INVALID_REQUEST

    ExecutionLifecycle(service, run_id, token).completed([SYNTHETIC_ARTIFACT])
    before = _aggregate(root, run_id)
    with pytest.raises(ExecutionServiceError) as caught:
        ExecutionLifecycle(service, run_id, token).completed([SYNTHETIC_ARTIFACT])
    assert caught.value.code is ErrorCode.INVALID_STATE_TRANSITION
    after = _aggregate(root, run_id)
    assert after.events == before.events
    assert after.artifacts == before.artifacts
    assert after.revision == before.revision


# -------------------------------------------------------------------------------------
# Idempotent token consumption and single-worker guarantees
# -------------------------------------------------------------------------------------


def test_repeated_execute_is_idempotent_and_never_relaunches(tmp_path: Path):
    """Repeated execute after terminal returns the same snapshot without relaunching."""
    root = tmp_path / "state"
    service, executor = _build(root)
    spy = SpyExecutor(executor)
    service = ExecutionService(
        repository=SQLiteExecutionRepository(StateRoot.resolve(root)),
        executor=spy,
        identity_verifier=FileIdentityVerifier(sys.executable),
    )
    executor.bind(service)
    identity = executor_identity(service)
    run_id = "run-idempotent"
    service.prepare(_request(run_id, identity))
    service.execute(run_id)
    _wait_terminal(service, run_id)
    assert len(spy.launch_calls) == 1
    before = _aggregate(root, run_id)

    for _ in range(3):
        snapshot = service.execute(run_id)
        assert snapshot.state is RunState.COMPLETED
        assert snapshot.revision == before.revision
    assert len(spy.launch_calls) == 1
    assert executor.worker_count == 1
    after = _aggregate(root, run_id)
    assert after.events == before.events
    assert after.artifacts == before.artifacts
    assert after.revision == before.revision


def test_concurrent_execute_spawns_one_worker_and_one_event_stream(tmp_path: Path):
    """Concurrent execute calls produce a single worker, stream and manifest."""
    root = tmp_path / "state"
    service, executor = _build(root)
    spy = SpyExecutor(executor)
    service = ExecutionService(
        repository=SQLiteExecutionRepository(StateRoot.resolve(root)),
        executor=spy,
        identity_verifier=FileIdentityVerifier(sys.executable),
    )
    executor.bind(service)
    identity = executor_identity(service)
    run_id = "run-concurrent"
    service.prepare(_request(run_id, identity))

    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = [pool.submit(service.execute, run_id) for _ in range(8)]
        [future.result(timeout=15) for future in futures]
    assert len(spy.launch_calls) == 8
    assert executor.worker_count == 1
    terminal = _wait_terminal(service, run_id)
    assert terminal.state is RunState.COMPLETED
    aggregate = _aggregate(root, run_id)
    assert aggregate is not None
    assert _event_types(aggregate) == ["prepared", "queued", "running", "checkpointed", "completed"]
    assert aggregate.artifacts == (SYNTHETIC_ARTIFACT,)
    assert [event.revision for event in aggregate.events] == [1, 2, 3, 4, 5]
    assert not executor.worker_errors


def test_repeated_direct_launch_intent_is_consumed_without_second_worker(tmp_path: Path):
    """The same launch token is consumed once; later intents attach only."""
    root = tmp_path / "state"
    service, executor = _build(root)
    identity = executor_identity(service)
    run_id = "run-direct-intent"
    service.prepare(_request(run_id, identity))
    service.execute(run_id)
    _wait_terminal(service, run_id)
    token = _aggregate(root, run_id).launch_token
    assert token is not None
    request = LaunchRequest(
        run_id=run_id,
        token=token,
        checkpoint_id=None,
        expected_identity=identity,
    )
    for _ in range(3):
        receipt = executor.ensure_launched(request)
        assert receipt.accepted is True
    assert executor.worker_count == 1

    second_executor = SyntheticProducerExecutor(StateRoot.resolve(root))
    second_executor.bind(service)
    receipt = second_executor.ensure_launched(request)
    assert receipt.accepted is True
    assert second_executor.worker_count == 0


# -------------------------------------------------------------------------------------
# Response-lost attach / reconnect
# -------------------------------------------------------------------------------------


def test_attach_reconnect_reads_same_terminal_snapshot_events_and_manifest(tmp_path: Path):
    """Reopening the service after a lost response sees identical durable facts."""
    root = tmp_path / "state"
    first_service, _first_executor = _build(root)
    identity = executor_identity(first_service)
    run_id = "run-attach"
    first_service.prepare(_request(run_id, identity))
    first_service.execute(run_id)
    terminal = _wait_terminal(first_service, run_id)
    token = _aggregate(root, run_id).launch_token
    assert token is not None

    second_service, second_executor = _build(root)
    assert second_service.status(run_id) == terminal
    assert second_service.events(run_id).events == first_service.events(run_id).events
    assert second_service.artifacts(run_id) == first_service.artifacts(run_id)

    request = LaunchRequest(
        run_id=run_id,
        token=token,
        checkpoint_id=None,
        expected_identity=identity,
    )
    receipt = second_executor.ensure_launched(request)
    assert receipt.accepted is True
    assert second_executor.worker_count == 0
    time.sleep(0.3)
    after = _aggregate(root, run_id)
    assert after.state is RunState.COMPLETED
    assert after.revision == terminal.revision
    assert after.artifacts == (SYNTHETIC_ARTIFACT,)
    assert _event_types(after) == ["prepared", "queued", "running", "checkpointed", "completed"]


def test_response_lost_while_queued_reconnects_to_single_terminal(tmp_path: Path):
    """Re-executing after a lost response attaches and still ends in one terminal."""
    root = tmp_path / "state"
    first_service, _first_executor = _build(root)
    identity = executor_identity(first_service)
    run_id = "run-lost-response"
    first_service.prepare(_request(run_id, identity))
    first_service.execute(run_id)

    second_service, _second_executor = _build(root)
    second_service.execute(run_id)
    terminal = _wait_terminal(second_service, run_id)
    assert terminal.state is RunState.COMPLETED
    aggregate = _aggregate(root, run_id)
    assert aggregate is not None
    assert _event_types(aggregate) == ["prepared", "queued", "running", "checkpointed", "completed"]
    assert aggregate.artifacts == (SYNTHETIC_ARTIFACT,)
    assert not aggregate.launch_token.endswith(".2")


# -------------------------------------------------------------------------------------
# The fixture never writes producer SQLite/state and never bypasses boundaries
# -------------------------------------------------------------------------------------


def test_fixture_never_writes_producer_sqlite_and_never_calls_the_engine(
    tmp_path: Path, monkeypatch
):
    """Only the service tables/rows exist; the legacy engine is never invoked."""

    def engine_bomb(*args, **kwargs):
        raise AssertionError("workflow engine must not run for the synthetic fixture")

    monkeypatch.setattr("confflow.workflow.engine.run_workflow", engine_bomb)
    monkeypatch.setattr(
        "confflow.application.execution.workflow_adapter.run_workflow_through_service",
        engine_bomb,
    )
    root = tmp_path / "state"
    service, _executor = _build(root)
    identity = executor_identity(service)
    run_id = "run-no-sqlite-writes"
    service.prepare(_request(run_id, identity))
    service.execute(run_id)
    terminal = _wait_terminal(service, run_id)
    assert terminal.state is RunState.COMPLETED

    database = StateRoot.resolve(root).database_path
    connection = sqlite3.connect(str(database))
    try:
        tables = {
            row[0]
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        assert tables == {"aggregates", "events", "artifacts", "repository_meta"}
        assert connection.execute("SELECT COUNT(*) FROM aggregates").fetchone() == (1,)
        assert connection.execute("SELECT state FROM aggregates").fetchone() == ("completed",)
        assert connection.execute("SELECT type FROM events ORDER BY revision").fetchall() == [
            ("prepared",),
            ("queued",),
            ("running",),
            ("checkpointed",),
            ("completed",),
        ]
        assert connection.execute(
            "SELECT terminal,path,sha256,size,content_schema FROM artifacts"
        ).fetchall() == [
            (
                SYNTHETIC_ARTIFACT_TERMINAL,
                SYNTHETIC_ARTIFACT_PATH,
                SYNTHETIC_ARTIFACT.sha256,
                SYNTHETIC_ARTIFACT.size,
                SYNTHETIC_ARTIFACT_SCHEMA,
            )
        ]
    finally:
        connection.close()
    assert _claim_file(root, run_id, f"{run_id}.launch.1").is_file()


def test_fixture_consumes_formal_launch_and_cancel_requests(tmp_path: Path):
    """The fixture only ever sees the formal token-bound executor requests."""
    root = tmp_path / "state"
    service, executor = _build(root)
    spy = SpyExecutor(executor)
    service = ExecutionService(
        repository=SQLiteExecutionRepository(StateRoot.resolve(root)),
        executor=spy,
        identity_verifier=FileIdentityVerifier(sys.executable),
    )
    executor.bind(service)
    identity = executor_identity(service)
    run_id = "run-formal-requests"
    service.prepare(_request(run_id, identity))
    service.execute(run_id)
    _wait_terminal(service, run_id)
    assert len(spy.launch_calls) == 1
    request = spy.launch_calls[0]
    assert request.run_id == run_id
    assert request.token == f"{run_id}.launch.1"
    assert request.checkpoint_id is None
    assert request.expected_identity == identity

    cancel_run_id = "run-formal-cancel"
    service.prepare(_request(cancel_run_id, identity))
    service.execute(cancel_run_id)
    try:
        service.cancel(cancel_run_id)
        assert service.status(cancel_run_id).state is RunState.CANCELLED
        assert len(spy.cancel_calls) == 1
        cancel_request = spy.cancel_calls[0]
        assert cancel_request.run_id == cancel_run_id
        assert cancel_request.launch_token == f"{cancel_run_id}.launch.1"
        assert cancel_request.attempt == 1
        assert service.execute(cancel_run_id).state is RunState.CANCELLED
        late_intent = LaunchRequest(
            run_id=cancel_run_id,
            token=f"{cancel_run_id}.launch.1",
            checkpoint_id=None,
            expected_identity=identity,
        )
        assert executor.ensure_launched(late_intent) == LaunchReceipt(
            accepted=False, cancelled=True
        )
    except ExecutionServiceError as error:
        assert error.code is ErrorCode.TERMINAL_RUN
        assert service.status(cancel_run_id).state is RunState.COMPLETED
        assert _aggregate(root, cancel_run_id).artifacts == (SYNTHETIC_ARTIFACT,)
    assert executor.worker_count == 2


def test_checkpoint_uses_the_official_lifecycle_surface_only(tmp_path: Path):
    """Every durable fact (including the checkpoint) is a service event."""
    root = tmp_path / "state"
    service, _executor = _build(root)
    identity = executor_identity(service)
    run_id = "run-checkpoint-surface"
    service.prepare(_request(run_id, identity))
    service.execute(run_id)
    _wait_terminal(service, run_id)
    aggregate = _aggregate(root, run_id)
    assert aggregate is not None
    assert aggregate.checkpoint is not None
    assert aggregate.checkpoint.checkpoint_id == SYNTHETIC_CHECKPOINT_ID
    checkpointed = [event for event in aggregate.events if event.type == "checkpointed"]
    assert len(checkpointed) == 1
    assert checkpointed[0].revision == 4


def test_concurrent_reads_never_observe_torn_state(tmp_path: Path):
    """read()/status() stay snapshot-consistent while the worker mutates."""
    root = tmp_path / "state"
    service, _executor = _build(root)
    identity = executor_identity(service)
    run_id = "run-atomic-reads"
    service.prepare(_request(run_id, identity))

    stop = threading.Event()
    violations: list[str] = []

    def reader_loop() -> None:
        repo = SQLiteExecutionRepository(StateRoot.resolve(root))
        while not stop.is_set():
            try:
                aggregate = repo.read(run_id)
                if aggregate is not None:
                    if len(aggregate.events) != aggregate.revision:
                        violations.append("torn: events != revision")
                    if tuple(e.revision for e in aggregate.events) != tuple(
                        range(1, aggregate.revision + 1)
                    ):
                        violations.append("torn: non-contiguous events")
                    expected = (
                        (SYNTHETIC_ARTIFACT,)
                        if aggregate.state is RunState.COMPLETED
                        else ()
                    )
                    if aggregate.artifacts != expected:
                        violations.append("torn: artifacts mismatch")
                service.status(run_id)
            except ExecutionServiceError as error:
                violations.append(f"read error: {error}")

    readers = [threading.Thread(target=reader_loop) for _ in range(8)]
    for reader in readers:
        reader.start()
    try:
        service.execute(run_id)
        _wait_terminal(service, run_id)
    finally:
        stop.set()
        for reader in readers:
            reader.join(timeout=30)
    assert violations == []
