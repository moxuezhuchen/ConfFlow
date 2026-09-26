#!/usr/bin/env python3

"""Durable SQLite work-item store tests (V4-3 milestone).

Covers open/reopen round-trips, fail-closed schema handling, registration
idempotency, the full state-transition matrix, attempt history across retry,
result round-trips, claim concurrency, transaction rollback semantics,
execution-protocol conformance, and digest-keyed reuse behavior.
"""

from __future__ import annotations

import sqlite3
import threading
import time
from pathlib import Path
from typing import Any

import pytest

from confflow.domain import (
    ArtifactLocator,
    ArtifactRef,
    ArtifactSet,
    Diagnostic,
    DiagnosticSeverity,
    FrozenDict,
    ResultSet,
    StructureSet,
)
from confflow.domain.canonical import typed_digest
from confflow.domain.completion import WorkItemStatus
from confflow.domain.work_item import (
    RecoveryInfo,
    ResultError,
    Timing,
    WorkItemResult,
)
from confflow.execution.batch import ReuseStore, WorkItemRepository
from confflow.persistence.contracts import (
    PERSISTENCE_SCHEMA_VERSION,
    CorruptStateError,
    OwnerIdentity,
    PersistenceError,
    SchemaVersionError,
    StateTransitionError,
    StoredWorkItemStatus,
)
from confflow.persistence.work_items import SqliteWorkItemStore, StoredAttempt

__all__: list[str] = []

_TEST_DIGEST_KIND = "confflow.test.work_item.v1"


def _digest(seed: str) -> str:
    """Return a deterministic test digest for *seed*."""
    return typed_digest(_TEST_DIGEST_KIND, {"seed": seed})


def _owner(token: str) -> OwnerIdentity:
    """Return a minimal test owner identity."""
    return OwnerIdentity(owner_token=token)


def _store_path(tmp_path: Path, name: str = "work_items.sqlite") -> str:
    """Return a store path nested under a managed run root."""
    return str(tmp_path / "run" / "steps" / "s_opt" / name)


def _open(tmp_path: Path, name: str = "work_items.sqlite") -> SqliteWorkItemStore:
    """Open a fresh store under a managed run root."""
    return SqliteWorkItemStore.open(_store_path(tmp_path, name))


def _register(
    store: SqliteWorkItemStore,
    work_item_id: str = "wi:s_opt:k1",
    logical_key: str = "s_opt:k1",
    digest_seed: str = "item-1",
) -> dict[str, Any]:
    """Register one item and return its registration fields."""
    item_digest = _digest(f"work-{digest_seed}")
    store.register_item(
        work_item_id=work_item_id,
        logical_key=logical_key,
        step_id="s_opt",
        work_item_digest=item_digest,
        step_semantic_digest=_digest("step"),
        environment_digest=_digest("env"),
        producer_provenance={"profile": "standard", "seed": digest_seed},
    )
    return {"work_item_id": work_item_id, "digest": item_digest}


def _completed_result(
    work_item_id: str, digest: str, *, tag: str = "a", energy: float = -40.5
) -> WorkItemResult:
    """Build a rich completed result for round-trip tests."""
    from tests.v4._builders import checkpoint, energy_result, structure

    return WorkItemResult(
        work_item_id=work_item_id,
        status=WorkItemStatus.COMPLETED,
        structures=StructureSet.of(structure(f"struct-{tag}")),
        results=ResultSet.of(energy_result(energy, subject_structure_id=f"struct-{tag}")),
        artifacts=ArtifactSet.of(
            ArtifactRef(
                id=f"chk-{tag}",
                role="checkpoint",
                locator=ArtifactLocator.run_relative(f"steps/s_opt/{tag}.chk"),
                checksum="sha256:" + "ab" * 32,
                subject_structure_id=f"struct-{tag}",
                producer_step_id="s_opt",
                producer_work_item_id=work_item_id,
                metadata=FrozenDict({"note": tag}),
            ),
            checkpoint(f"struct-{tag}", producer_step_id="s_opt"),
        ),
        diagnostics=(
            Diagnostic(
                code="converged",
                message="native program converged",
                severity=DiagnosticSeverity.INFO,
                step_id="s_opt",
                work_item_id=work_item_id,
                details=FrozenDict({"cycles": 12}),
            ),
        ),
        timing=Timing(started_at=100.0, finished_at=112.5, duration_seconds=12.5),
        recovery=RecoveryInfo(profile="none", attempted=False),
        semantic_digest=digest,
        metadata=FrozenDict({"labels": ("x", "y"), "rank": 3}),
    )


def _failed_result(work_item_id: str, digest: str) -> WorkItemResult:
    """Build a failed result carrying a structured error."""
    return WorkItemResult(
        work_item_id=work_item_id,
        status=WorkItemStatus.FAILED,
        diagnostics=(),
        timing=Timing(finished_at=200.0, duration_seconds=4.0),
        error=ResultError(
            code="native_error",
            message="native program failed",
            retryable=True,
            details=FrozenDict({"exit_code": 1}),
        ),
        recovery=RecoveryInfo(profile="none", attempted=False),
        semantic_digest=digest,
    )


def _cancelled_result(work_item_id: str, digest: str) -> WorkItemResult:
    """Build a cancelled result carrying a cancellation error."""
    return WorkItemResult(
        work_item_id=work_item_id,
        status=WorkItemStatus.CANCELLED,
        diagnostics=(
            Diagnostic(
                code="cancellation_error",
                message="cancelled by operator",
                step_id="s_opt",
                work_item_id=work_item_id,
            ),
        ),
        timing=Timing(finished_at=300.0, duration_seconds=1.0),
        error=ResultError(code="cancellation_error", message="cancelled by operator"),
        semantic_digest=digest,
    )


# ----------------------------------------------------------------------
# Open / schema handling
# ----------------------------------------------------------------------


def test_open_create_and_reopen_round_trip(tmp_path: Path) -> None:
    """Registered state survives close and reopen."""
    with _open(tmp_path) as store:
        info = _register(store)
        assert store.get_state(info["work_item_id"]) is StoredWorkItemStatus.PENDING
    with SqliteWorkItemStore.open(_store_path(tmp_path)) as reopened:
        assert reopened.get_state(info["work_item_id"]) is StoredWorkItemStatus.PENDING
        registered = reopened.get_registered(info["work_item_id"])
        assert registered["work_item_digest"] == info["digest"]
        assert registered["producer_provenance"]["profile"] == "standard"


def test_open_missing_with_create_false(tmp_path: Path) -> None:
    """A missing file with ``create=False`` raises instead of creating."""
    with pytest.raises(PersistenceError):
        SqliteWorkItemStore.open(_store_path(tmp_path), create=False)
    assert not Path(_store_path(tmp_path)).exists()


def test_open_refuses_memory() -> None:
    """``:memory:`` is refused because the store is durable only."""
    with pytest.raises(PersistenceError):
        SqliteWorkItemStore.open(":memory:")


def _write_raw_store(path: str, *, version: object, with_tables: bool = True) -> None:
    """Write a hand-rolled store file with a chosen meta version."""
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
    try:
        if with_tables:
            connection.execute("CREATE TABLE meta (kind TEXT PRIMARY KEY, value TEXT)")
            connection.execute("CREATE TABLE items (work_item_id TEXT PRIMARY KEY)")
            connection.execute("CREATE TABLE attempts (attempt_id INTEGER PRIMARY KEY)")
            connection.execute("CREATE TABLE artifact_rows (artifact_id TEXT PRIMARY KEY)")
            connection.execute(
                "INSERT INTO meta (kind, value) VALUES ('schema_version', ?)",
                (str(version),),
            )
        connection.commit()
    finally:
        connection.close()


def test_open_version_mismatch(tmp_path: Path) -> None:
    """A bad schema version fails closed with ``SchemaVersionError``."""
    path = _store_path(tmp_path)
    _write_raw_store(path, version=PERSISTENCE_SCHEMA_VERSION + 1)
    with pytest.raises(SchemaVersionError):
        SqliteWorkItemStore.open(path)


def test_open_missing_tables(tmp_path: Path) -> None:
    """An empty sqlite file fails closed instead of being rebuilt."""
    path = _store_path(tmp_path)
    _write_raw_store(path, version=PERSISTENCE_SCHEMA_VERSION, with_tables=False)
    with pytest.raises(CorruptStateError):
        SqliteWorkItemStore.open(path)


def test_open_corrupt_meta_missing_row(tmp_path: Path) -> None:
    """A store without the version row fails closed."""
    path = _store_path(tmp_path)
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
    try:
        connection.execute("CREATE TABLE meta (kind TEXT PRIMARY KEY, value TEXT)")
        connection.execute("CREATE TABLE items (work_item_id TEXT PRIMARY KEY)")
        connection.execute("CREATE TABLE attempts (attempt_id INTEGER PRIMARY KEY)")
        connection.execute("CREATE TABLE artifact_rows (artifact_id TEXT PRIMARY KEY)")
        connection.commit()
    finally:
        connection.close()
    with pytest.raises(CorruptStateError):
        SqliteWorkItemStore.open(path)


def test_open_corrupt_meta_bad_value(tmp_path: Path) -> None:
    """A non-integer version value fails closed."""
    path = _store_path(tmp_path)
    _write_raw_store(path, version="not-a-version")
    with pytest.raises(CorruptStateError):
        SqliteWorkItemStore.open(path)


def test_open_garbage_file(tmp_path: Path) -> None:
    """A non-database file fails closed."""
    path = _store_path(tmp_path)
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_bytes(b"this is not a sqlite database" * 8)
    with pytest.raises(CorruptStateError):
        SqliteWorkItemStore.open(path)


def test_context_manager_closes(tmp_path: Path) -> None:
    """Exiting the context manager closes; later use raises."""
    with _open(tmp_path) as store:
        _register(store)
    with pytest.raises(PersistenceError):
        store.get_state("wi:s_opt:k1")
    store.close()


# ----------------------------------------------------------------------
# Registration
# ----------------------------------------------------------------------


def test_register_idempotent(tmp_path: Path) -> None:
    """Identical re-registration is a no-op."""
    with _open(tmp_path) as store:
        info = _register(store)
        _register(store)
        _register(store)
        assert store.get_state(info["work_item_id"]) is StoredWorkItemStatus.PENDING
        assert store.get_attempts(info["work_item_id"]) == ()
        assert store.list_items() == (info["work_item_id"],)


def test_register_digest_conflict(tmp_path: Path) -> None:
    """Conflicting digests on an existing id fail closed."""
    with _open(tmp_path) as store:
        _register(store)
        with pytest.raises(CorruptStateError):
            store.register_item(
                work_item_id="wi:s_opt:k1",
                logical_key="s_opt:k1",
                step_id="s_opt",
                work_item_digest=_digest("different-content"),
                step_semantic_digest=_digest("step"),
                environment_digest=_digest("env"),
                producer_provenance={"profile": "standard", "seed": "item-1"},
            )
        with pytest.raises(PersistenceError):
            store.register_item(
                work_item_id="wi:s_opt:k1",
                logical_key="s_opt:other-key",
                step_id="s_opt",
                work_item_digest=_digest("work-item-1"),
                step_semantic_digest=_digest("step"),
            )


def test_get_registered_fields(tmp_path: Path) -> None:
    """Registration readers expose digest and provenance fields."""
    with _open(tmp_path) as store:
        info = _register(store)
        registered = store.get_registered(info["work_item_id"])
        assert registered["work_item_id"] == info["work_item_id"]
        assert registered["logical_key"] == "s_opt:k1"
        assert registered["step_id"] == "s_opt"
        assert registered["work_item_digest"] == info["digest"]
        assert registered["step_semantic_digest"] == _digest("step")
        assert registered["environment_digest"] == _digest("env")
        assert registered["producer_provenance"]["seed"] == "item-1"
        assert registered["status"] == StoredWorkItemStatus.PENDING.value
        assert registered["current_attempt"] == 0
        with pytest.raises(PersistenceError):
            store.get_registered("wi:s_opt:unknown")


# ----------------------------------------------------------------------
# Transitions
# ----------------------------------------------------------------------


def test_terminal_ops_on_pending_raise(tmp_path: Path) -> None:
    """Terminal transitions from ``PENDING`` are illegal."""
    with _open(tmp_path) as store:
        info = _register(store)
        item_id = info["work_item_id"]
        with pytest.raises(StateTransitionError):
            store.complete(item_id, result=_completed_result(item_id, info["digest"]))
        with pytest.raises(StateTransitionError):
            store.fail(item_id, result=_failed_result(item_id, info["digest"]))
        with pytest.raises(StateTransitionError):
            store.cancel_item(item_id)
        with pytest.raises(StateTransitionError):
            store.mark_interrupted(item_id)
        assert store.get_state(item_id) is StoredWorkItemStatus.PENDING


def test_complete_validates_payload_before_transition(tmp_path: Path) -> None:
    """Payload errors raise ``PersistenceError``, never state errors."""
    with _open(tmp_path) as store:
        info = _register(store)
        item_id = info["work_item_id"]
        assert store.claim(item_id, owner=_owner("owner-a"))
        other = _completed_result("wi:s_opt:other", info["digest"])
        with pytest.raises(PersistenceError):
            store.complete(item_id, result=other)
        with pytest.raises(PersistenceError):
            store.complete(item_id, result=_failed_result(item_id, info["digest"]))
        with pytest.raises(PersistenceError):
            store.fail(item_id, result=_completed_result(item_id, info["digest"]))
        assert store.get_state(item_id) is StoredWorkItemStatus.RUNNING


def test_full_lifecycle_with_retry(tmp_path: Path) -> None:
    """Fail, reclaim, and complete preserves both attempts."""
    with _open(tmp_path) as store:
        info = _register(store)
        item_id = info["work_item_id"]
        assert store.get_owner(item_id) is None
        assert store.claim(item_id, owner=_owner("owner-a"))
        assert store.claim(item_id, owner=_owner("owner-b")) is False
        owner = store.get_owner(item_id)
        assert owner is not None and owner.owner_token == "owner-a"
        store.fail(item_id, result=_failed_result(item_id, info["digest"]))
        assert store.get_state(item_id) is StoredWorkItemStatus.FAILED
        assert store.claim(item_id, owner=_owner("owner-b")) is True
        store.complete(item_id, result=_completed_result(item_id, info["digest"]))
        assert store.get_state(item_id) is StoredWorkItemStatus.COMPLETED
        attempts = store.get_attempts(item_id)
        assert [entry.attempt_number for entry in attempts] == [1, 2]
        assert [entry.status for entry in attempts] == [
            StoredWorkItemStatus.FAILED,
            StoredWorkItemStatus.COMPLETED,
        ]
        assert all(isinstance(entry, StoredAttempt) for entry in attempts)
        assert all(entry.has_result for entry in attempts)
        assert store.claim(item_id, owner=_owner("owner-c")) is False
        with pytest.raises(StateTransitionError):
            store.complete(item_id, result=_completed_result(item_id, info["digest"]))


def test_completed_and_cancelled_refuse_reclaim(tmp_path: Path) -> None:
    """Terminal ``COMPLETED``/``CANCELLED`` items never run again."""
    with _open(tmp_path) as store:
        first = _register(store, "wi:s_opt:done", "s_opt:done", digest_seed="done")
        second = _register(store, "wi:s_opt:stop", "s_opt:stop", digest_seed="stop")
        assert store.claim(first["work_item_id"], owner=_owner("owner-a"))
        store.complete(
            first["work_item_id"],
            result=_completed_result(first["work_item_id"], first["digest"]),
        )
        assert store.claim(first["work_item_id"], owner=_owner("owner-b")) is False
        assert store.claim(second["work_item_id"], owner=_owner("owner-a"))
        store.cancel_item(second["work_item_id"], reason="operator request")
        assert store.get_state(second["work_item_id"]) is StoredWorkItemStatus.CANCELLED
        assert store.claim(second["work_item_id"], owner=_owner("owner-b")) is False
        with pytest.raises(StateTransitionError):
            store.mark_interrupted(second["work_item_id"])


def test_interrupted_is_retryable(tmp_path: Path) -> None:
    """``INTERRUPTED`` items can be reclaimed for recovery."""
    with _open(tmp_path) as store:
        info = _register(store)
        item_id = info["work_item_id"]
        assert store.claim(item_id, owner=_owner("owner-a"))
        store.mark_interrupted(item_id, reason="worker lost")
        assert store.get_state(item_id) is StoredWorkItemStatus.INTERRUPTED
        assert store.claim(item_id, owner=_owner("owner-b")) is True
        store.complete(item_id, result=_completed_result(item_id, info["digest"]))
        assert store.get_state(item_id) is StoredWorkItemStatus.COMPLETED


def test_claim_unknown_returns_false(tmp_path: Path) -> None:
    """Claiming an unknown id reports contention-free ``False``."""
    with _open(tmp_path) as store:
        assert store.claim("wi:s_opt:missing", owner=_owner("owner-a")) is False
        assert store.get_state("wi:s_opt:missing") is None
        assert store.get_owner("wi:s_opt:missing") is None
        assert store.get_attempts("wi:s_opt:missing") == ()
        assert store.get_result("wi:s_opt:missing") is None


def test_owner_round_trip(tmp_path: Path) -> None:
    """Full owner identity survives claim and reopen."""
    owner = OwnerIdentity(
        owner_token="owner-full",
        pid=1234,
        process_group_id=1230,
        session_id=1220,
        create_time=1700000000.0,
        claimed_wall=1700000001.5,
    )
    with _open(tmp_path) as store:
        info = _register(store)
        assert store.claim(info["work_item_id"], owner=owner)
    with SqliteWorkItemStore.open(_store_path(tmp_path)) as reopened:
        stored = reopened.get_owner(info["work_item_id"])
        assert stored is not None
        assert stored.to_dict() == owner.to_dict()


def test_list_items_order_and_filter(tmp_path: Path) -> None:
    """Listing is sorted by logical key and filterable by status."""
    with _open(tmp_path) as store:
        ids = ["wi:s_opt:k3", "wi:s_opt:k1", "wi:s_opt:k2"]
        digests: dict[str, str] = {}
        for index, item_id in enumerate(ids):
            info = _register(store, item_id, item_id.replace("wi:", ""), digest_seed=f"k{index}")
            digests[item_id] = info["digest"]
        assert store.list_items() == ("wi:s_opt:k1", "wi:s_opt:k2", "wi:s_opt:k3")
        assert store.list_items(StoredWorkItemStatus.PENDING) == (
            "wi:s_opt:k1",
            "wi:s_opt:k2",
            "wi:s_opt:k3",
        )
        assert store.claim("wi:s_opt:k1", owner=_owner("owner-a"))
        assert store.list_items(StoredWorkItemStatus.RUNNING) == ("wi:s_opt:k1",)
        assert store.list_items(StoredWorkItemStatus.PENDING) == (
            "wi:s_opt:k2",
            "wi:s_opt:k3",
        )


# ----------------------------------------------------------------------
# Results and artifacts
# ----------------------------------------------------------------------


def test_result_round_trip_equality(tmp_path: Path) -> None:
    """Stored results reconstruct exactly via ``to_dict``."""
    with _open(tmp_path) as store:
        info = _register(store)
        expected = _completed_result("wi:s_opt:k1", info["digest"])
        assert store.get_result("wi:s_opt:k1") is None
        assert store.claim("wi:s_opt:k1", owner=_owner("owner-a"))
        assert store.get_result("wi:s_opt:k1") is None
        store.complete("wi:s_opt:k1", result=expected)
        stored = store.get_result("wi:s_opt:k1")
        assert stored is not None
        assert stored.to_dict() == expected.to_dict()


def test_result_round_trip_after_reopen(tmp_path: Path) -> None:
    """Terminal results survive close and reopen."""
    with _open(tmp_path) as store:
        info = _register(store)
        expected = _completed_result("wi:s_opt:k1", info["digest"])
        assert store.claim("wi:s_opt:k1", owner=_owner("owner-a"))
        store.complete("wi:s_opt:k1", result=expected)
    with SqliteWorkItemStore.open(_store_path(tmp_path)) as reopened:
        reopened_result = reopened.get_result("wi:s_opt:k1")
        assert reopened_result is not None
        assert reopened_result.to_dict() == expected.to_dict()


def test_artifact_rows_written_at_complete(tmp_path: Path) -> None:
    """Completion indexes artifact rows with locator and retention data."""
    with _open(tmp_path) as store:
        info = _register(store)
        item_id = info["work_item_id"]
        assert store.list_artifact_rows(item_id) == ()
        assert store.claim(item_id, owner=_owner("owner-a"))
        store.complete(item_id, result=_completed_result(item_id, info["digest"]))
        rows = store.list_artifact_rows(item_id)
        assert [entry["artifact_id"] for entry in rows] == ["chk-a", "chk_struct-a"]
        by_id = {entry["artifact_id"]: entry for entry in rows}
        assert by_id["chk-a"]["locator_path"] == "steps/s_opt/a.chk"
        assert by_id["chk-a"]["checksum"] == "sha256:" + "ab" * 32
        assert by_id["chk-a"]["subject_structure_id"] == "struct-a"
        assert by_id["chk-a"]["role"] == "checkpoint"
        assert by_id["chk-a"]["retention"] == "retained"
        assert by_id["chk-a"]["metadata"] == {"note": "a"}
        assert by_id["chk-a"]["size_bytes"] is None


def test_corrupt_result_json_fails_closed(tmp_path: Path) -> None:
    """Undecodable or misshaped stored results raise ``CorruptStateError``."""
    with _open(tmp_path) as store:
        info = _register(store)
        item_id = info["work_item_id"]
        assert store.claim(item_id, owner=_owner("owner-a"))
        store.complete(item_id, result=_completed_result(item_id, info["digest"]))
        path = store.path
    connection = sqlite3.connect(path)
    try:
        connection.execute(
            "UPDATE attempts SET result_json = 'not json' WHERE work_item_id = ?",
            (item_id,),
        )
        connection.commit()
    finally:
        connection.close()
    with SqliteWorkItemStore.open(path) as reopened:
        with pytest.raises(CorruptStateError):
            reopened.get_result(item_id)
    connection = sqlite3.connect(path)
    try:
        connection.execute(
            "UPDATE attempts SET result_json = '[]' WHERE work_item_id = ?",
            (item_id,),
        )
        connection.commit()
    finally:
        connection.close()
    with SqliteWorkItemStore.open(path) as reopened:
        with pytest.raises(CorruptStateError):
            reopened.get_result(item_id)


def test_failed_complete_leaves_running_without_partial_rows(tmp_path: Path) -> None:
    """Rejected payloads roll back: state and attempts are untouched.

    Validation happens before the transaction opens, so a failed
    ``complete()`` leaves the item ``RUNNING`` with the single running
    attempt and no terminal result.
    """
    with _open(tmp_path) as store:
        info = _register(store)
        item_id = info["work_item_id"]
        assert store.claim(item_id, owner=_owner("owner-a"))
        before = store.get_attempts(item_id)
        with pytest.raises(PersistenceError):
            store.complete(item_id, result=_failed_result(item_id, info["digest"]))
        assert store.get_state(item_id) is StoredWorkItemStatus.RUNNING
        assert store.get_attempts(item_id) == before
        assert len(before) == 1
        assert before[0].status is StoredWorkItemStatus.RUNNING
        assert before[0].has_result is False
        assert store.get_result(item_id) is None
        store.complete(item_id, result=_completed_result(item_id, info["digest"]))
        assert store.get_state(item_id) is StoredWorkItemStatus.COMPLETED


# ----------------------------------------------------------------------
# Concurrency
# ----------------------------------------------------------------------


def test_concurrent_claim_exactly_one_winner(tmp_path: Path) -> None:
    """Eight threads racing one claim produce exactly one winner."""
    with _open(tmp_path) as store:
        info = _register(store)
        item_id = info["work_item_id"]
        count = 8
        barrier = threading.Barrier(count)
        outcomes: list[bool] = [False] * count

        def _race(index: int) -> None:
            barrier.wait(timeout=30)
            outcomes[index] = store.claim(item_id, owner=_owner(f"owner-{index}"))

        threads = [threading.Thread(target=_race, args=(index,)) for index in range(count)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=30)
        assert not any(thread.is_alive() for thread in threads)
        assert sum(outcomes) == 1
        assert store.get_state(item_id) is StoredWorkItemStatus.RUNNING
        assert len(store.get_attempts(item_id)) == 1


def test_parallel_claims_on_distinct_items(tmp_path: Path) -> None:
    """Distinct items are claimable in parallel without serialization stalls."""
    with _open(tmp_path) as store:
        ids = [f"wi:s_opt:p{index}" for index in range(4)]
        for index, item_id in enumerate(ids):
            _register(store, item_id, item_id.replace("wi:", ""), digest_seed=f"p{index}")
        barrier = threading.Barrier(len(ids))
        outcomes: list[bool] = [False] * len(ids)

        def _claim_one(index: int) -> None:
            barrier.wait(timeout=30)
            outcomes[index] = store.claim(ids[index], owner=_owner(f"owner-{index}"))

        started = time.monotonic()
        threads = [threading.Thread(target=_claim_one, args=(index,)) for index in range(len(ids))]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=30)
        elapsed = time.monotonic() - started
        assert not any(thread.is_alive() for thread in threads)
        assert outcomes == [True] * len(ids)
        assert elapsed < 10.0


# ----------------------------------------------------------------------
# Execution protocols and reuse
# ----------------------------------------------------------------------


def test_protocol_conformance(tmp_path: Path) -> None:
    """The store satisfies both batch-execution protocols structurally."""
    with _open(tmp_path) as store:
        assert isinstance(store, WorkItemRepository)
        assert isinstance(store, ReuseStore)


def test_record_started_and_finished_flow(tmp_path: Path) -> None:
    """Repository ports drive claim and completion without explicit owners."""
    with _open(tmp_path) as store:
        info = _register(store)
        item_id = info["work_item_id"]
        with pytest.raises(PersistenceError):
            store.record_started("wi:s_opt:missing", "s_opt", "s_opt:missing")
        store.record_started(item_id, "s_opt", "s_opt:k1")
        assert store.get_state(item_id) is StoredWorkItemStatus.RUNNING
        owner = store.get_owner(item_id)
        assert owner is not None and owner.owner_token == f"ephemeral:{item_id}"
        store.record_started(item_id, "s_opt", "s_opt:k1")
        with pytest.raises(PersistenceError):
            store.record_started(item_id, "s_opt", "s_opt:wrong")
        expected = _completed_result(item_id, info["digest"])
        store.record_finished(expected)
        assert store.get_state(item_id) is StoredWorkItemStatus.COMPLETED
        finished = store.get_result(item_id)
        assert finished is not None
        assert finished.to_dict() == expected.to_dict()
        with pytest.raises(PersistenceError):
            store.record_started(item_id, "s_opt", "s_opt:k1")


def test_record_finished_failed_and_cancelled(tmp_path: Path) -> None:
    """Repository finish ports persist failed and cancelled outcomes."""
    with _open(tmp_path) as store:
        failed = _register(store, "wi:s_opt:f", "s_opt:f", digest_seed="f")
        store.record_started(failed["work_item_id"], "s_opt", "s_opt:f")
        store.record_finished(_failed_result(failed["work_item_id"], failed["digest"]))
        assert store.get_state(failed["work_item_id"]) is StoredWorkItemStatus.FAILED
        cancelled = _register(store, "wi:s_opt:c", "s_opt:c", digest_seed="c")
        store.record_started(cancelled["work_item_id"], "s_opt", "s_opt:c")
        store.record_finished(_cancelled_result(cancelled["work_item_id"], cancelled["digest"]))
        assert store.get_state(cancelled["work_item_id"]) is StoredWorkItemStatus.CANCELLED
        cancelled_result = store.get_result(cancelled["work_item_id"])
        assert cancelled_result is not None
        assert cancelled_result.status is WorkItemStatus.CANCELLED
        pending = _register(store, "wi:s_opt:p", "s_opt:p", digest_seed="p")
        with pytest.raises(StateTransitionError):
            store.record_finished(_completed_result(pending["work_item_id"], pending["digest"]))


def test_reuse_lookup_and_store_semantics(tmp_path: Path) -> None:
    """Reuse lookups hit completed digests; ``store`` never invents history."""
    with _open(tmp_path) as store:
        info = _register(store)
        item_id = info["work_item_id"]
        assert store.lookup(info["digest"]) is None
        assert store.claim(item_id, owner=_owner("owner-a"))
        store.complete(item_id, result=_completed_result(item_id, info["digest"]))
        hit = store.lookup(info["digest"])
        assert hit is not None
        assert hit.to_dict() == _completed_result(item_id, info["digest"]).to_dict()
        assert store.lookup(_digest("no-such-content")) is None
        unknown = _completed_result("wi:s_opt:ghost", _digest("ghost-content"))
        store.store(unknown)
        assert store.lookup(_digest("ghost-content")) is None
        skipped = _failed_result("wi:s_opt:other", _digest("failed-content"))
        store.store(skipped)
        assert store.lookup(_digest("failed-content")) is None
        store.store(hit)
        reloaded = store.lookup(info["digest"])
        assert reloaded is not None
        assert reloaded.to_dict() == hit.to_dict()
        with pytest.raises(PersistenceError):
            store.store("not-a-result")  # type: ignore[arg-type]


def test_reuse_lookup_ignores_logical_key(tmp_path: Path) -> None:
    """Reuse matches on content digest, never on the logical address."""
    with _open(tmp_path) as store:
        info = _register(store)
        item_id = info["work_item_id"]
        assert store.claim(item_id, owner=_owner("owner-a"))
        store.complete(item_id, result=_completed_result(item_id, info["digest"]))
        hit = store.lookup(info["digest"])
        assert hit is not None and hit.work_item_id == item_id


def test_failed_results_are_not_reusable(tmp_path: Path) -> None:
    """Only ``COMPLETED`` attempts back reuse lookups."""
    with _open(tmp_path) as store:
        info = _register(store)
        item_id = info["work_item_id"]
        assert store.claim(item_id, owner=_owner("owner-a"))
        store.fail(item_id, result=_failed_result(item_id, info["digest"]))
        assert store.lookup(info["digest"]) is None
        failed_result = store.get_result(item_id)
        assert failed_result is not None
        assert failed_result.status is WorkItemStatus.FAILED
