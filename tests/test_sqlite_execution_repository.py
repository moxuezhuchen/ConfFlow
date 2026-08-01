"""TDD contracts for the production execution aggregate SQLite repository."""

from __future__ import annotations

import json
import multiprocessing
import os
import sqlite3
import stat
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path
from threading import Event, Lock

import pytest

from confflow.application.execution import (
    ApprovalVerifier,
    Artifact,
    CancelReceipt,
    Checkpoint,
    ErrorCode,
    ExecutableIdentity,
    ExecutionService,
    ExecutionServiceError,
    LaunchReceipt,
    PrepareRequest,
    RunState,
    SharedFilesystemApproval,
)
from confflow.application.execution.sqlite import SQLiteExecutionRepository
from confflow.application.execution.state_root import StateRoot

IDENTITY = ExecutableIdentity(sha256="e" * 64, realpath="/opt/g16/g16")


class StaticIdentityVerifier:
    """Identity port that does not touch a real executable."""

    def measure(self) -> ExecutableIdentity:
        """Return the already-prepared identity."""
        return IDENTITY


class TokenExecutor:
    """Thread-safe token-idempotent executor used without workflow execution."""

    def __init__(self) -> None:
        self._lock = Lock()
        self.accepted: set[str] = set()

    def ensure_launched(self, request) -> LaunchReceipt:
        """Accept a launch token without running workflow code."""
        with self._lock:
            self.accepted.add(request.token)
        return LaunchReceipt(accepted=True)

    def ensure_cancelled(self, request):
        """Reject cancellation because this executor is launch-only."""
        raise AssertionError(f"Unexpected cancel request: {request}")


class TombstoneExecutor(TokenExecutor):
    """Executor that records token-level cancellation without starting workflow work."""

    def ensure_cancelled(self, request):
        """Confirm a token-bound cancellation."""
        self.cancelled = request.launch_token
        return CancelReceipt(confirmed=True)


class BlockingTombstoneExecutor(TombstoneExecutor):
    """Block launch until cancellation installs its token tombstone."""

    def __init__(self) -> None:
        super().__init__()
        self.entered = Event()
        self.release = Event()
        self.started: set[str] = set()

    def ensure_launched(self, request) -> LaunchReceipt:
        """Return cancelled when cancellation won the executor-side arbitration."""
        self.entered.set()
        assert self.release.wait(timeout=2)
        if getattr(self, "cancelled", None) == request.token:
            return LaunchReceipt(accepted=False, cancelled=True)
        self.started.add(request.token)
        return LaunchReceipt(accepted=True)


def _request() -> PrepareRequest:
    """Create a protocol-valid prepare request."""
    return PrepareRequest(
        run_id="run-001",
        idempotency_key="key-001",
        request_digest="a" * 64,
        workflow_config_digest="b" * 64,
        input_manifest_digest="c" * 64,
        expected_executable_identity=IDENTITY,
    )


def _service(repository: SQLiteExecutionRepository, executor: TokenExecutor) -> ExecutionService:
    """Create an execution service with SQLite state and no computation."""
    return ExecutionService(
        repository=repository,
        executor=executor,
        identity_verifier=StaticIdentityVerifier(),
    )


def _process_execute(root_text: str, result_queue) -> None:
    """Run one service instance in a separate process against a shared SQLite database."""
    root = StateRoot.resolve(root_text)
    repository = SQLiteExecutionRepository(root)
    service = _service(repository, TokenExecutor())
    result_queue.put(service.execute("run-001"))
    repository.close()


def test_state_root_rejects_relative_home_root_wrong_owner_and_weak_permissions(tmp_path: Path):
    """Repository state must not escape an explicit private root."""
    with pytest.raises(ExecutionServiceError) as error:
        StateRoot.resolve("relative-state")
    assert error.value.code is ErrorCode.REPOSITORY_UNAVAILABLE


def test_state_root_rejects_symlink(tmp_path: Path):
    """Reject a root locator that traverses a final symbolic link."""
    target = tmp_path / "target"
    target.mkdir(mode=0o700)
    link = tmp_path / "link"
    link.symlink_to(target, target_is_directory=True)
    with pytest.raises(ExecutionServiceError) as error:
        StateRoot.resolve(link)
    assert error.value.code is ErrorCode.REPOSITORY_UNAVAILABLE
    with pytest.raises(ExecutionServiceError):
        StateRoot.resolve(Path.home())
    with pytest.raises(ExecutionServiceError):
        StateRoot.resolve(Path("/"))
    with pytest.raises(ExecutionServiceError):
        StateRoot.resolve(tmp_path, expected_uid=os.getuid() + 1)

    weak = tmp_path / "weak"
    weak.mkdir(mode=0o700)
    weak.chmod(0o777)
    with pytest.raises(ExecutionServiceError) as error:
        StateRoot.resolve(weak)
    assert error.value.code is ErrorCode.REPOSITORY_UNAVAILABLE


def test_state_root_uses_one_database_and_private_run_layout(tmp_path: Path):
    """StateRoot exposes only the versioned database and private run directories."""
    root_dir = tmp_path / "state"
    root_dir.mkdir(mode=0o700)
    state_root = StateRoot.resolve(root_dir)

    assert state_root.database_path == root_dir / "v1" / "repository.sqlite3"
    paths = state_root.ensure_run_paths("run-001")
    assert paths.staging == root_dir / "v1" / "runs" / "run-001" / "staging"
    assert paths.work == root_dir / "v1" / "runs" / "run-001" / "work"
    assert stat.S_IMODE(paths.work.stat().st_mode) & (stat.S_IWGRP | stat.S_IWOTH) == 0


@pytest.mark.parametrize("component", ["v1", "runs", "run", "staging", "work"])
def test_managed_layout_rejects_symlink_at_every_component(tmp_path: Path, component: str):
    """Every service-created component is opened relative to a no-follow directory fd."""
    root_dir = tmp_path / "state"
    root_dir.mkdir(mode=0o700)
    root = StateRoot.resolve(root_dir)
    target = tmp_path / f"outside-{component}"
    target.mkdir(mode=0o700)

    if component == "v1":
        link = root_dir / "v1"
    else:
        version = root_dir / "v1"
        version.mkdir(mode=0o700)
        if component == "runs":
            link = version / "runs"
        else:
            runs = version / "runs"
            runs.mkdir(mode=0o700)
            if component == "run":
                link = runs / "run-001"
            else:
                run_dir = runs / "run-001"
                run_dir.mkdir(mode=0o700)
                link = run_dir / component
    link.symlink_to(target, target_is_directory=True)

    with pytest.raises(ExecutionServiceError) as error:
        root.ensure_run_paths("run-001")
    assert error.value.code is ErrorCode.REPOSITORY_UNAVAILABLE


def test_existing_modes_must_be_exactly_private_and_database_is_0600(tmp_path: Path):
    """Reject permissive managed directories and force database files to owner-only mode."""
    root_dir = tmp_path / "state"
    root_dir.mkdir(mode=0o700)
    root_dir.chmod(0o755)
    with pytest.raises(ExecutionServiceError) as error:
        StateRoot.resolve(root_dir)
    assert error.value.code is ErrorCode.REPOSITORY_UNAVAILABLE
    root_dir.chmod(0o700)
    root = StateRoot.resolve(root_dir)
    repository = SQLiteExecutionRepository(root)
    assert stat.S_IMODE(root.database_path.stat().st_mode) == 0o600
    repository.close()


def test_sqlite_migrates_reopens_and_preserves_aggregate_event_intent(tmp_path: Path):
    """Reopening recovers queued intent, revision, and event stream."""
    root_dir = tmp_path / "state"
    root_dir.mkdir(mode=0o700)
    state_root = StateRoot.resolve(root_dir)
    first = SQLiteExecutionRepository(state_root, busy_timeout_ms=1500)
    executor = TokenExecutor()
    service = _service(first, executor)
    assert service.prepare(_request()).revision == 1
    assert service.execute("run-001").state is RunState.QUEUED
    first.close()

    reopened = SQLiteExecutionRepository(StateRoot.resolve(root_dir), busy_timeout_ms=1500)
    recovered = reopened.read("run-001")
    assert recovered is not None
    assert recovered.state is RunState.QUEUED
    assert recovered.revision == 2
    assert [event.type for event in recovered.events] == ["prepared", "queued"]
    assert recovered.launch_token == "run-001.launch.1"
    assert reopened.journal_mode == "wal"
    assert reopened.schema_version == 1
    reopened.close()


def test_shared_fs_mode_uses_delete_journal(tmp_path: Path, monkeypatch):
    """Shared filesystem operation is explicit and never silently enables WAL."""
    root_dir = tmp_path / "state"
    root_dir.mkdir(mode=0o700)
    def verify(self, path, *, root, filesystem_type):
        del self
        assert path == "/admin/approval.json"
        assert root == root_dir
        assert filesystem_type
        root_stat = root.stat()
        return SharedFilesystemApproval(
            "approval-1", str(root.resolve()), root_stat.st_dev, root_stat.st_ino, filesystem_type
        )

    monkeypatch.setattr(ApprovalVerifier, "verify", verify)

    repository = SQLiteExecutionRepository(
        StateRoot.resolve(root_dir),
        shared_fs=True,
        shared_fs_approval_path="/admin/approval.json",
        filesystem_detector=lambda path: "nfs4",
    )
    assert repository.journal_mode == "delete"
    repository.close()


def test_shared_fs_approval_file_and_repository_binding(tmp_path: Path, monkeypatch):
    """Approval is root-owned evidence and a repository cannot reopen under changed evidence."""
    root_dir = tmp_path / "state"
    root_dir.mkdir(mode=0o700)
    root = StateRoot.resolve(root_dir)
    root_stat = root_dir.stat()
    approval_path = tmp_path / "approval.json"
    approval_path.write_text(
        json.dumps(
            {
                "approval_id": "approval-1",
                "root_realpath": str(root_dir.resolve()),
                "device": root_stat.st_dev,
                "inode": root_stat.st_ino,
                "filesystem_type": "nfs4",
                "guarantees": {"locking": True, "atomic_rename": True, "fsync": True},
            }
        ),
        encoding="utf-8",
    )
    approval_path.chmod(0o600)
    real_fstat = os.fstat

    def root_owned_fstat(fd: int):
        metadata = real_fstat(fd)
        values = list(metadata)
        values[4] = 0
        return os.stat_result(values)

    monkeypatch.setattr(
        "confflow.application.execution.shared_fs_approval.os.fstat", root_owned_fstat
    )
    verified = ApprovalVerifier().verify(approval_path, root=root_dir, filesystem_type="nfs4")
    assert verified.approval_id == "approval-1"
    invalid = json.loads(approval_path.read_text(encoding="utf-8"))
    invalid["guarantees"]["locking"] = 1
    approval_path.write_text(json.dumps(invalid), encoding="utf-8")
    with pytest.raises(ExecutionServiceError) as error:
        ApprovalVerifier().verify(approval_path, root=root_dir, filesystem_type="nfs4")
    assert error.value.code is ErrorCode.REPOSITORY_UNAVAILABLE

    active_approval = "approval-1"

    def fixed_verify(self, path, *, root, filesystem_type):
        del self, path
        current = root.stat()
        return SharedFilesystemApproval(
            active_approval,
            str(root.resolve()),
            current.st_dev,
            current.st_ino,
            filesystem_type,
        )

    monkeypatch.setattr(ApprovalVerifier, "verify", fixed_verify)

    repository = SQLiteExecutionRepository(
        root,
        shared_fs=True,
        shared_fs_approval_path=approval_path,
        filesystem_detector=lambda path: "nfs4",
    )
    repository.close()
    active_approval = "approval-2"
    with pytest.raises(ExecutionServiceError) as error:
        SQLiteExecutionRepository(
            root,
            shared_fs=True,
            shared_fs_approval_path=approval_path,
            filesystem_detector=lambda path: "nfs4",
        )
    assert error.value.code is ErrorCode.REPOSITORY_UNAVAILABLE


@pytest.mark.parametrize("suffix", ["-wal", "-shm"])
def test_sqlite_rejects_precreated_sidecar_symlink(tmp_path: Path, suffix: str):
    """SQLite must never follow a pre-created WAL or shared-memory sidecar link."""
    root_dir = tmp_path / "state"
    root_dir.mkdir(mode=0o700)
    root = StateRoot.resolve(root_dir)
    repository = SQLiteExecutionRepository(root)
    repository.close()
    target = tmp_path / f"outside{suffix}"
    target.write_bytes(b"")
    sidecar = Path(f"{root.database_path}{suffix}")
    sidecar.unlink(missing_ok=True)
    sidecar.symlink_to(target)
    with pytest.raises(ExecutionServiceError) as error:
        SQLiteExecutionRepository(root)
    assert error.value.code is ErrorCode.REPOSITORY_UNAVAILABLE


@pytest.mark.parametrize(
    "corruption",
    [
        "INSERT INTO artifacts VALUES('run-001','terminal','../escape.xyz','dddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddd',1,'schema.v1')",
        "UPDATE aggregates SET request_digest='not-a-digest' WHERE run_id='run-001'",
        "DELETE FROM events WHERE run_id='run-001' AND revision=1",
    ],
)
def test_sqlite_loader_rejects_noncanonical_or_discontinuous_data(
    tmp_path: Path, corruption: str
):
    """Direct database corruption cannot cross the repository boundary."""
    root_dir = tmp_path / "state"
    root_dir.mkdir(mode=0o700)
    root = StateRoot.resolve(root_dir)
    repository = SQLiteExecutionRepository(root)
    repository.create_or_get(_request())
    repository.close()
    connection = sqlite3.connect(root.database_path)
    connection.execute(corruption)
    connection.commit()
    connection.close()
    reopened = SQLiteExecutionRepository(root)
    with pytest.raises(ExecutionServiceError) as error:
        reopened.read("run-001")
    assert error.value.code is ErrorCode.REPOSITORY_UNAVAILABLE
    reopened.close()


def test_newer_schema_and_corrupt_json_fail_closed(tmp_path: Path):
    """Reject forward-incompatible migration state and unversioned corrupt payloads."""
    root_dir = tmp_path / "state"
    root_dir.mkdir(mode=0o700)
    root = StateRoot.resolve(root_dir)
    repository = SQLiteExecutionRepository(root)
    repository.close()
    connection = sqlite3.connect(root.database_path)
    connection.execute("PRAGMA user_version=2")
    connection.commit()
    connection.close()
    with pytest.raises(ExecutionServiceError) as error:
        SQLiteExecutionRepository(root)
    assert error.value.code is ErrorCode.REPOSITORY_UNAVAILABLE

    connection = sqlite3.connect(root.database_path)
    connection.execute("PRAGMA user_version=1")
    connection.execute(
        "INSERT INTO aggregates VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        ("run-001", "key-001", "a" * 64, "b" * 64, "c" * 64, "{bad", 1, "prepared", 0, None, None, None, 0, None),
    )
    connection.execute("INSERT INTO events VALUES(?,?,?,?)", ("run-001", 1, "r00000000000000000001", "prepared"))
    connection.commit()
    connection.close()
    repository = SQLiteExecutionRepository(root)
    with pytest.raises(ExecutionServiceError) as error:
        repository.read("run-001")
    assert error.value.code is ErrorCode.REPOSITORY_UNAVAILABLE
    repository.close()


def test_commit_failure_rolls_back_record_event_and_launch_intent(tmp_path: Path, monkeypatch):
    """Commit failure cannot tear state from its event or launch intent."""
    root_dir = tmp_path / "state"
    root_dir.mkdir(mode=0o700)
    repository = SQLiteExecutionRepository(StateRoot.resolve(root_dir))
    original = repository.create_or_get(_request())

    def fail_commit() -> None:
        raise OSError("injected commit failure")

    monkeypatch.setattr(repository, "_commit", fail_commit)
    with pytest.raises(ExecutionServiceError) as error:
        repository.compare_and_mutate(
            original.run_id,
            original.revision,
            lambda current: (replace(current, state=RunState.QUEUED), "queued"),
        )
    assert error.value.code is ErrorCode.REPOSITORY_UNAVAILABLE
    recovered = repository.read("run-001")
    assert recovered is not None
    assert recovered.revision == 1 and recovered.state is RunState.PREPARED
    assert [event.type for event in recovered.events] == ["prepared"]
    repository.close()


def test_two_connections_and_two_processes_share_one_cas_launch_attempt(tmp_path: Path):
    """Workers converge on one queued attempt across connections and processes."""
    root_dir = tmp_path / "state"
    root_dir.mkdir(mode=0o700)
    first_repo = SQLiteExecutionRepository(StateRoot.resolve(root_dir), busy_timeout_ms=3000)
    second_repo = SQLiteExecutionRepository(StateRoot.resolve(root_dir), busy_timeout_ms=3000)
    executor = TokenExecutor()
    first = _service(first_repo, executor)
    second = _service(second_repo, executor)
    first.prepare(_request())

    with ThreadPoolExecutor(max_workers=2) as pool:
        snapshots = list(pool.map(lambda service: service.execute("run-001"), (first, second)))
    assert {snapshot.state for snapshot in snapshots} == {RunState.QUEUED}
    aggregate = first_repo.read("run-001")
    assert aggregate is not None and aggregate.attempt == 1
    assert executor.accepted == {aggregate.launch_token}

    context = multiprocessing.get_context("spawn")
    result_queue = context.Queue()
    processes = [context.Process(target=_process_execute, args=(str(root_dir), result_queue)) for _ in range(2)]
    for process in processes:
        process.start()
    for process in processes:
        process.join(timeout=15)
        assert process.exitcode == 0
    snapshots = [result_queue.get(timeout=2) for _ in processes]
    assert {snapshot.state for snapshot in snapshots} == {RunState.QUEUED}
    final = first_repo.read("run-001")
    assert final is not None and final.attempt == 1
    first_repo.close()
    second_repo.close()


def test_sqlite_cursor_reconnect_and_token_bound_cancel(tmp_path: Path):
    """Persisted cursor replay and cancellation token binding survive a real reopen."""
    root_dir = tmp_path / "state"
    root_dir.mkdir(mode=0o700)
    root = StateRoot.resolve(root_dir)
    executor = TombstoneExecutor()
    repository = SQLiteExecutionRepository(root)
    service = _service(repository, executor)
    service.prepare(_request())
    queued = service.execute("run-001")
    first = service.events("run-001")
    assert first.next_cursor == "r00000000000000000002"
    cancelled = service.cancel("run-001")
    assert cancelled.state is RunState.CANCELLED
    assert executor.cancelled == "run-001.launch.1"
    repository.close()

    reopened = SQLiteExecutionRepository(StateRoot.resolve(root_dir))
    second = _service(reopened, TombstoneExecutor()).events("run-001", after=first.next_cursor)
    assert [event.type for event in second.events] == ["cancel_requested", "cancelled"]
    assert second.next_cursor == "r00000000000000000004"
    assert queued.revision == 2
    reopened.close()


def test_sqlite_inflight_launch_cancel_tombstone_persists_cursor(tmp_path: Path):
    """Cancel wins a blocked real SQLite launch without allowing the old token to start."""
    root_dir = tmp_path / "state"
    root_dir.mkdir(mode=0o700)
    executor = BlockingTombstoneExecutor()
    first = _service(SQLiteExecutionRepository(StateRoot.resolve(root_dir)), executor)
    second = _service(SQLiteExecutionRepository(StateRoot.resolve(root_dir)), executor)
    first.prepare(_request())
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(first.execute, "run-001")
        assert executor.entered.wait(timeout=2)
        assert second.cancel("run-001").state is RunState.CANCELLED
        executor.release.set()
        assert future.result().state is RunState.CANCELLED
    record = second._repository.read("run-001")  # noqa: SLF001
    assert record is not None and record.state is RunState.CANCELLED
    assert record.launch_token not in executor.started
    assert [event.type for event in record.events][-2:] == ["cancel_requested", "cancelled"]
    assert second.events("run-001", after="r00000000000000000002").next_cursor == "r00000000000000000004"


def test_parent_symlink_and_migration_failure_fail_closed(tmp_path: Path, monkeypatch):
    """Reject a symlinked root and roll back an interrupted exclusive migration."""
    target = tmp_path / "target"
    target.mkdir(mode=0o700)
    link = tmp_path / "link"
    link.symlink_to(target, target_is_directory=True)
    with pytest.raises(ExecutionServiceError):
        StateRoot.resolve(link)

    root_dir = tmp_path / "state"
    root_dir.mkdir(mode=0o700)
    root = StateRoot.resolve(root_dir)
    monkeypatch.setattr(
        SQLiteExecutionRepository,
        "_commit",
        lambda self, connection: (_ for _ in ()).throw(OSError("migration fail")),
    )
    with pytest.raises(ExecutionServiceError):
        SQLiteExecutionRepository(root)
    assert not root.database_path.exists() or sqlite3.connect(root.database_path).execute("PRAGMA user_version").fetchone()[0] == 0


def test_wal_policy_and_operation_lock_release(tmp_path: Path):
    """Reject shared/unknown mounts unless verified DELETE mode and release every operation lock."""
    root_dir = tmp_path / "state"
    root_dir.mkdir(mode=0o700)
    root = StateRoot.resolve(root_dir)
    with pytest.raises(ExecutionServiceError) as error:
        SQLiteExecutionRepository(root, filesystem_detector=lambda path: "9p")
    assert error.value.code is ErrorCode.REPOSITORY_UNAVAILABLE
    with pytest.raises(ExecutionServiceError):
        SQLiteExecutionRepository(root, shared_fs=True)
    first = SQLiteExecutionRepository(root)
    second = SQLiteExecutionRepository(StateRoot.resolve(root_dir))
    original = first.create_or_get(_request())
    second.compare_and_mutate(
        original.run_id,
        original.revision,
        lambda current: (replace(current, state=RunState.QUEUED), "queued"),
    )
    first.close()
    second.close()


def test_duplicate_run_id_and_missing_mounts_fail_closed(tmp_path: Path, monkeypatch):
    """Reject conflicting run handles and unknown WAL filesystem evidence."""
    root_dir = tmp_path / "state"
    root_dir.mkdir(mode=0o700)
    root = StateRoot.resolve(root_dir)
    repository = SQLiteExecutionRepository(root)
    repository.create_or_get(_request())
    with pytest.raises(ExecutionServiceError) as error:
        repository.create_or_get(
            PrepareRequest(
                "run-001", "key-other", "d" * 64, "b" * 64, "c" * 64, IDENTITY
            )
        )
    assert error.value.code is ErrorCode.INVALID_REQUEST
    monkeypatch.setattr("confflow.application.execution.sqlite.Path.read_text", lambda self, **kwargs: (_ for _ in ()).throw(FileNotFoundError()))
    with pytest.raises(ExecutionServiceError) as error:
        SQLiteExecutionRepository(StateRoot.resolve(root_dir), filesystem_detector=None)
    assert error.value.code is ErrorCode.REPOSITORY_UNAVAILABLE


def test_optional_identity_checkpoint_and_artifact_roundtrip_and_rollback(tmp_path: Path, monkeypatch):
    """Round-trip optional identity fields/checkpoint/artifacts and roll back failed replacement."""
    root_dir = tmp_path / "state"
    root_dir.mkdir(mode=0o700)
    repository = SQLiteExecutionRepository(StateRoot.resolve(root_dir))
    original = repository.create_or_get(_request())
    artifact = Artifact("terminal", "terminal/out.xyz", "d" * 64, 3, "schema.v1")
    updated = repository.compare_and_mutate(
        original.run_id,
        original.revision,
        lambda current: (
            replace(
                current,
                state=RunState.RUNNING,
                expected_executable_identity=ExecutableIdentity("e" * 64),
                checkpoint=Checkpoint("cp-1"),
                artifacts=(artifact,),
            ),
            "checkpointed",
        ),
    )
    assert updated.checkpoint == Checkpoint("cp-1")
    assert updated.expected_executable_identity.realpath is None
    assert updated.artifacts == (artifact,)
    monkeypatch.setattr(repository, "_insert_artifacts", lambda *args: (_ for _ in ()).throw(OSError("artifact fail")))
    with pytest.raises(ExecutionServiceError) as error:
        repository.compare_and_mutate(
            updated.run_id,
            updated.revision,
            lambda current: (replace(current, artifacts=()), "replaced"),
        )
    assert error.value.code is ErrorCode.REPOSITORY_UNAVAILABLE
    recovered = repository.read("run-001")
    assert recovered is not None and recovered.revision == updated.revision
    assert recovered.artifacts == (artifact,)
    assert recovered.events[-1].type == "checkpointed"
