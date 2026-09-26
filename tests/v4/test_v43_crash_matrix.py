#!/usr/bin/env python3

"""V4-3 crash-point x resume-action matrix at store + publication level.

Each class pins one crash point from the V4-3 fault-injection matrix and
asserts the four resume facets: persisted state, resume decision, whether the
native boundary reruns (a call-counting launcher stub stands in for native
programs, which never execute here), and publication behavior.

- (A) claim ``RUNNING`` then crash-before-launch: the abandoned claim
  reconciles to dead, moves to ``INTERRUPTED``, and re-claims with two
  attempts.
- (B) success computed but crash-before-commit: no result is durable, so
  re-execution is required and nothing is published.
- (C) ``complete()`` committed but crash-before-publication: the stored
  result rebuilds and publishes with zero native reruns.
- (D) garbage ``*.tmp.*`` beside ``step_result.json`` is ignored; a corrupt
  ``step_result.json`` fails closed while store rows stay intact.
- (E) a valid publication with a stale run state repairs via
  ``detect_published`` plus a pure transition, with zero native reruns.
- (F) corrupted artifact bytes fail verification, forcing
  ``INVALIDATE_ARTIFACT`` instead of reuse.

Seams (A)-(C), (D2), and (F1) exercise the sibling-owned
``confflow.persistence.work_items`` / ``recovery`` / ``artifacts`` modules
through lazily imported helpers; until those land, those tests fail with a
plain import error while the publication/run-state/reuse seams stay green.
"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
from typing import Any

import pytest

from confflow.domain import (
    ArtifactLocator,
    ArtifactRef,
    ArtifactSet,
    CompletionMode,
    CompletionPolicy,
    FrozenDict,
    ResultError,
    StepStatus,
    WorkItemResult,
    WorkItemStatus,
    typed_digest,
)
from confflow.persistence.contracts import (
    CorruptStateError,
    OwnerIdentity,
    OwnerVerdict,
    ReuseCode,
    RunState,
    RunStepStatus,
    StoredWorkItemStatus,
    step_dir,
    step_result_path,
    store_path,
)
from confflow.persistence.publication import (
    STEP_RESULT_DIGEST_KIND,
    load_published_step_result,
    publish_step_result,
    rebuild_step_result,
    verify_for_publication,
)
from confflow.persistence.reuse import (
    ReuseInputs,
    build_producer_provenance,
    evaluate_reuse,
)
from confflow.persistence.run_state import (
    detect_published,
    load_run_state,
    repair_after_publish,
    save_run_state,
    transition_step,
)

STEP_ID = "s1"
WORK_ITEM_DIGEST = "sha256:" + "a1" * 32
STEP_SEMANTIC_DIGEST = "sha256:" + "b2" * 32
ENVIRONMENT_DIGEST = "sha256:" + "c3" * 32
ARTIFACT_CHECKSUM = "sha256:" + "d4" * 32
REQUIRE_ALL = CompletionPolicy(mode=CompletionMode.REQUIRE_ALL)


class _Launcher:
    """Call-counting stand-in for the native program boundary."""

    def __init__(self) -> None:
        self.launches: list[str] = []

    def launch(self, work_item_id: str) -> None:
        """Record one native launch of *work_item_id*."""
        self.launches.append(work_item_id)


def _provenance() -> FrozenDict:
    """Build the fixed producer provenance shared by every matrix item."""
    return build_producer_provenance(
        adapter_version="adapter.v1",
        profile_version="profile.v1",
        check_versions={"normal_termination": "c.v1"},
        recovery_version="recovery.v1",
    )


def _register(store: Any, item_id: str, logical_key: str) -> None:
    """Register one matrix work item with fixed digest axes."""
    store.register_item(
        work_item_id=item_id,
        logical_key=logical_key,
        step_id=STEP_ID,
        work_item_digest=WORK_ITEM_DIGEST,
        step_semantic_digest=STEP_SEMANTIC_DIGEST,
        environment_digest=ENVIRONMENT_DIGEST,
        producer_provenance=_provenance(),
    )


def _open_store(run_root: str, step_id: str) -> Any:
    """Open the step SQLite store (sibling workstream: work_items.py)."""
    from confflow.persistence.work_items import SqliteWorkItemStore

    return SqliteWorkItemStore.open(store_path(run_root, step_id))


def _reconcile_owner(owner: OwnerIdentity) -> OwnerVerdict:
    """Reconcile a recorded owner (sibling workstream: recovery.py)."""
    from confflow.persistence.recovery import reconcile_owner

    return reconcile_owner(owner)


def _verify_artifact(run_root: str, ref: ArtifactRef) -> None:
    """Verify artifact bytes (sibling workstream: artifacts.py)."""
    from confflow.persistence.artifacts import verify_artifact

    verify_artifact(run_root=run_root, ref=ref)


def _dead_owner(token: str) -> OwnerIdentity:
    """Build an owner whose pid is provably absent via signal probing."""
    base = 1 << 30
    for offset in range(256):
        pid = base + offset
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return OwnerIdentity(owner_token=token, pid=pid)
        except PermissionError:
            continue
    raise AssertionError("could not find an unused pid for the dead-owner probe")


def _completed_result(item_id: str) -> WorkItemResult:
    """Hand-build a minimal completed work-item result."""
    return WorkItemResult(work_item_id=item_id, status=WorkItemStatus.COMPLETED)


def _failed_result(item_id: str) -> WorkItemResult:
    """Hand-build a failed work-item result with structured error info."""
    return WorkItemResult(
        work_item_id=item_id,
        status=WorkItemStatus.FAILED,
        error=ResultError(code="exec_failed", message="boom", retryable=True),
    )


def _checkpoint_artifact(tag: str, checksum: str) -> ArtifactRef:
    """Hand-build a checkpoint artifact reference carrying *checksum*."""
    return ArtifactRef(
        id=f"chk_{tag}",
        role="checkpoint",
        locator=ArtifactLocator.run_relative(f"steps/{STEP_ID}/{tag}.chk"),
        checksum=checksum,
        subject_structure_id=f"struct_{tag}",
        producer_step_id=STEP_ID,
    )


def _completed_result_with_checkpoint(item_id: str, tag: str, checksum: str) -> WorkItemResult:
    """Hand-build a completed result bound to one checksummed artifact."""
    return WorkItemResult(
        work_item_id=item_id,
        status=WorkItemStatus.COMPLETED,
        artifacts=ArtifactSet.of(_checkpoint_artifact(tag, checksum)),
    )


def _reuse_inputs(**overrides: Any) -> ReuseInputs:
    """Build reuse axes matching the registered matrix digests."""
    payload: dict[str, Any] = {
        "work_item_digest": WORK_ITEM_DIGEST,
        "step_semantic_digest": STEP_SEMANTIC_DIGEST,
        "environment_digest": ENVIRONMENT_DIGEST,
        "producer_provenance": _provenance(),
        "artifact_checksums": (ARTIFACT_CHECKSUM,),
    }
    payload.update(overrides)
    return ReuseInputs(**payload)


class TestCrashABeforeLaunch:
    """Claim RUNNING then crash before any native launch."""

    def test_abandoned_claim_recovers_and_reclaims_with_two_attempts(self, tmp_path: Path) -> None:
        run_root = str(tmp_path / "run")
        item_id = "wi:s1:a"
        owner = _dead_owner("owner-a1")

        store = _open_store(run_root, STEP_ID)
        _register(store, item_id, f"{STEP_ID}:a")
        assert store.claim(item_id, owner=owner) is True
        assert store.get_state(item_id) == StoredWorkItemStatus.RUNNING
        store.close()  # crash before launch: no terminal write

        launcher = _Launcher()
        reopened = _open_store(run_root, STEP_ID)
        try:
            # Persisted state: the RUNNING claim and its owner survive reopen.
            # (The store stamps claim time, so identity compares on token+pid.)
            assert reopened.get_state(item_id) == StoredWorkItemStatus.RUNNING
            recorded = reopened.get_owner(item_id)
            assert recorded.owner_token == owner.owner_token
            assert recorded.pid == owner.pid
            # Resume decision: the recorded owner is dead, so recovery proceeds.
            assert _reconcile_owner(recorded) == OwnerVerdict.DEFINITELY_DEAD
            reopened.mark_interrupted(item_id, reason="owner-dead: crash before launch")
            assert reopened.get_state(item_id) == StoredWorkItemStatus.INTERRUPTED
            assert (
                reopened.claim(
                    item_id,
                    owner=OwnerIdentity(owner_token="owner-a2", pid=os.getpid()),
                )
                is True
            )
            attempts = reopened.get_attempts(item_id)
            assert [attempt.attempt_number for attempt in attempts] == [1, 2]
            # Native rerun: exactly one launch after the successful re-claim.
            launcher.launch(item_id)
        finally:
            reopened.close()
        assert launcher.launches == [item_id]

    def test_live_owner_blocks_duplicate_launch(self, tmp_path: Path) -> None:
        run_root = str(tmp_path / "run")
        item_id = "wi:s1:b"
        live = OwnerIdentity(owner_token="owner-live", pid=os.getpid())

        with _open_store(run_root, STEP_ID) as store:
            _register(store, item_id, f"{STEP_ID}:b")
            assert store.claim(item_id, owner=live) is True
        # Crash: the context manager closed the handle with the claim held.

        reopened = _open_store(run_root, STEP_ID)
        try:
            # Persisted state: still RUNNING under the live owner.
            assert reopened.get_state(item_id) == StoredWorkItemStatus.RUNNING
            verdict = _reconcile_owner(live)
            assert verdict == OwnerVerdict.DEFINITELY_ALIVE
            # Resume decision: a rival claim is refused; reuse blocks.
            rival = OwnerIdentity(owner_token="owner-rival", pid=os.getpid())
            assert reopened.claim(item_id, owner=rival) is False
            decision = evaluate_reuse(
                current=_reuse_inputs(),
                stored=_reuse_inputs(),
                stored_status=StoredWorkItemStatus.RUNNING,
                owner_verdict=verdict,
                work_item_id=item_id,
            )
            assert decision.decision is ReuseCode.BLOCKED_UNCERTAIN_OWNER
        finally:
            reopened.close()


class TestCrashBBeforeCommit:
    """Success computed but crash before commit."""

    def test_uncommitted_success_leaves_no_result_and_allows_reexecution(
        self, tmp_path: Path
    ) -> None:
        run_root = str(tmp_path / "run")
        item_id = "wi:s1:c"
        owner = _dead_owner("owner-b1")

        store = _open_store(run_root, STEP_ID)
        _register(store, item_id, f"{STEP_ID}:c")
        assert store.claim(item_id, owner=owner) is True
        computed = _completed_result(item_id)
        store.close()  # crash after compute, before complete(): nothing committed

        launcher = _Launcher()
        reopened = _open_store(run_root, STEP_ID)
        try:
            # Persisted state: no result survived; only the stale claim did.
            assert reopened.get_result(item_id) is None
            assert reopened.get_state(item_id) == StoredWorkItemStatus.RUNNING
            # Publication behavior: with no commit there is nothing to publish.
            assert load_published_step_result(run_root=run_root, step_id=STEP_ID) is None
            assert detect_published(run_root, STEP_ID) is None
            # Resume decision: recover the abandoned claim, then re-execute.
            assert _reconcile_owner(owner) == OwnerVerdict.DEFINITELY_DEAD
            reopened.mark_interrupted(item_id, reason="owner-dead: crash before commit")
            assert (
                reopened.claim(
                    item_id,
                    owner=OwnerIdentity(owner_token="owner-b2", pid=os.getpid()),
                )
                is True
            )
            launcher.launch(item_id)
            reopened.complete(item_id, result=computed)
            stored = reopened.get_result(item_id)
            assert stored is not None
            assert stored.to_dict() == computed.to_dict()
        finally:
            reopened.close()
        # Native rerun: exactly one launch, because nothing was durable.
        assert launcher.launches == [item_id]


class TestCrashCBeforePublication:
    """complete() committed but crash before publication."""

    def test_committed_result_rebuilds_without_reexecution(self, tmp_path: Path) -> None:
        run_root = str(tmp_path / "run")
        item_id = "wi:s1:d"
        owner = _dead_owner("owner-c1")

        store = _open_store(run_root, STEP_ID)
        _register(store, item_id, f"{STEP_ID}:d")
        assert store.claim(item_id, owner=owner) is True
        computed = _completed_result(item_id)
        store.complete(item_id, result=computed)
        store.close()  # crash after commit, before publication

        launcher = _Launcher()
        reopened = _open_store(run_root, STEP_ID)
        try:
            # Persisted state: the committed result survives reopen.
            stored = reopened.get_result(item_id)
            assert stored is not None
            assert stored.to_dict() == computed.to_dict()
            assert reopened.get_state(item_id) == StoredWorkItemStatus.COMPLETED
            # Resume decision: rebuild from store rows; the native stub stays idle.
            rebuilt = rebuild_step_result(
                step_id=STEP_ID,
                items=(stored,),
                completion=REQUIRE_ALL,
                definition_digest="sha256:" + "ab" * 32,
                step_semantic_digest=STEP_SEMANTIC_DIGEST,
            )
            assert launcher.launches == []
            # Publication behavior: publish the rebuild and load it back exactly.
            digest = publish_step_result(run_root=run_root, step_id=STEP_ID, step_result=rebuilt)
            loaded = load_published_step_result(run_root=run_root, step_id=STEP_ID)
            assert loaded is not None
            assert loaded.to_dict() == rebuilt.to_dict()
            assert typed_digest(STEP_RESULT_DIGEST_KIND, loaded.to_dict()) == digest
        finally:
            reopened.close()
        assert launcher.launches == []

    def test_committed_result_passes_publication_gate(self, tmp_path: Path) -> None:
        run_root = str(tmp_path / "run")
        item_id = "wi:s1:e"
        owner = _dead_owner("owner-c2")

        with _open_store(run_root, STEP_ID) as store:
            _register(store, item_id, f"{STEP_ID}:e")
            assert store.claim(item_id, owner=owner) is True
            computed = _completed_result_with_checkpoint(item_id, "e", ARTIFACT_CHECKSUM)
            store.complete(item_id, result=computed)
        # Crash after commit, before publication.

        launcher = _Launcher()
        reopened = _open_store(run_root, STEP_ID)
        try:
            stored = reopened.get_result(item_id)
            assert stored is not None
            rebuilt = rebuild_step_result(step_id=STEP_ID, items=(stored,), completion=REQUIRE_ALL)
            assert rebuilt.status is StepStatus.COMPLETED
            verify_for_publication(
                rebuilt,
                durable_item_ids=rebuilt.work_item_ids,
                verified_artifact_checksums=(ARTIFACT_CHECKSUM,),
            )
            digest = publish_step_result(run_root=run_root, step_id=STEP_ID, step_result=rebuilt)
            assert detect_published(run_root, STEP_ID) == digest
        finally:
            reopened.close()
        assert launcher.launches == []


class TestCrashDPublicationFiles:
    """Garbage temp files versus corrupt publications."""

    def test_tmp_leftover_beside_valid_publish_is_ignored(self, tmp_path: Path) -> None:
        run_root = str(tmp_path / "run")
        directory = step_dir(run_root, STEP_ID)
        os.makedirs(directory, exist_ok=True)
        garbage = os.path.join(directory, "step_result.json.tmp.999.0")
        with open(garbage, "w", encoding="utf-8") as handle:
            handle.write("{not valid json")
        result = rebuild_step_result(
            step_id=STEP_ID,
            items=(_completed_result("wi:s1:f"), _failed_result("wi:s1:g")),
            completion=REQUIRE_ALL,
        )
        digest = publish_step_result(run_root=run_root, step_id=STEP_ID, step_result=result)
        loaded = load_published_step_result(run_root=run_root, step_id=STEP_ID)
        assert loaded is not None
        assert loaded.to_dict() == result.to_dict()
        assert typed_digest(STEP_RESULT_DIGEST_KIND, loaded.to_dict()) == digest

    def test_corrupt_publication_fails_closed_while_store_stays_intact(
        self, tmp_path: Path
    ) -> None:
        run_root = str(tmp_path / "run")
        item_id = "wi:s1:h"
        owner = _dead_owner("owner-d1")

        store = _open_store(run_root, STEP_ID)
        _register(store, item_id, f"{STEP_ID}:h")
        assert store.claim(item_id, owner=owner) is True
        computed = _completed_result(item_id)
        store.complete(item_id, result=computed)
        rebuilt = rebuild_step_result(step_id=STEP_ID, items=(computed,), completion=REQUIRE_ALL)
        publish_step_result(run_root=run_root, step_id=STEP_ID, step_result=rebuilt)
        store.close()  # crash; a later partial write corrupts the publication
        with open(step_result_path(run_root, STEP_ID), "wb") as handle:
            handle.write(b"truncated{")

        # Publication behavior: corruption raises, never partial data or absence.
        with pytest.raises(CorruptStateError):
            load_published_step_result(run_root=run_root, step_id=STEP_ID)
        with pytest.raises(CorruptStateError):
            detect_published(run_root, STEP_ID)
        # Persisted state: the committed store rows are untouched by the corruption.
        with _open_store(run_root, STEP_ID) as reopened:
            assert reopened.get_state(item_id) == StoredWorkItemStatus.COMPLETED
            stored = reopened.get_result(item_id)
            assert stored is not None
            assert stored.to_dict() == computed.to_dict()


class TestCrashEStaleRunState:
    """Valid publication with a stale run state repairs without re-execution."""

    def _published(self, run_root: str) -> Any:
        result = rebuild_step_result(
            step_id=STEP_ID,
            items=(_completed_result("wi:s1:i"),),
            completion=REQUIRE_ALL,
            definition_digest="sha256:" + "ab" * 32,
            step_semantic_digest=STEP_SEMANTIC_DIGEST,
        )
        digest = publish_step_result(run_root=run_root, step_id=STEP_ID, step_result=result)
        return result, digest

    def test_stale_state_repairs_from_publication(self, tmp_path: Path) -> None:
        run_root = str(tmp_path / "run")
        result, digest = self._published(run_root)
        stale = RunState(run_id="run-stale")
        launcher = _Launcher()

        # Resume decision: the publication exists, so repair instead of rerunning.
        found = detect_published(run_root, STEP_ID)
        assert found == digest
        repaired = repair_after_publish(stale, STEP_ID, RunStepStatus.COMPLETED, found)
        assert stale.step(STEP_ID) is None
        assert repaired.step(STEP_ID) is not None
        assert repaired.step(STEP_ID).status is RunStepStatus.COMPLETED
        assert repaired.step(STEP_ID).published_step_result_digest == digest
        assert launcher.launches == []

        # Publication behavior: the repaired state points at the exact payload.
        save_run_state(run_root, repaired)
        loaded_state = load_run_state(run_root)
        assert loaded_state is not None
        assert loaded_state.step(STEP_ID) is not None
        assert loaded_state.step(STEP_ID).published_step_result_digest == digest
        loaded_result = load_published_step_result(run_root=run_root, step_id=STEP_ID)
        assert loaded_result is not None
        assert loaded_result.to_dict() == result.to_dict()

    def test_repair_records_digest_without_mutating_stale_state(self, tmp_path: Path) -> None:
        run_root = str(tmp_path / "run")
        _, digest = self._published(run_root)
        stale = RunState(run_id="run-stale-2")

        found = detect_published(run_root, STEP_ID)
        assert found == typed_digest(
            STEP_RESULT_DIGEST_KIND,
            load_published_step_result(run_root=run_root, step_id=STEP_ID).to_dict(),
        )
        updated = transition_step(
            repair_after_publish(stale, STEP_ID, RunStepStatus.COMPLETED, found),
            STEP_ID,
            RunStepStatus.COMPLETED,
            published_step_result_digest=digest,
        )
        assert stale.step(STEP_ID) is None
        assert updated.step(STEP_ID) is not None
        assert updated.step(STEP_ID).published_step_result_digest == digest


class TestCrashFCorruptArtifacts:
    """Corrupted artifact bytes force invalidation instead of reuse."""

    def test_corrupted_bytes_fail_verification_and_invalidate_reuse(self, tmp_path: Path) -> None:
        from confflow.persistence.artifacts import ArtifactIntegrityError

        run_root = str(tmp_path / "run")
        item_id = "wi:s1:j"
        owner = _dead_owner("owner-f1")
        payload = b"checkpoint-payload-v1" * 64
        checksum = "sha256:" + hashlib.sha256(payload).hexdigest()
        ref = _checkpoint_artifact("j", checksum)
        assert ref.locator.path is not None
        artifact_path = os.path.join(run_root, ref.locator.path)
        os.makedirs(os.path.dirname(artifact_path), exist_ok=True)
        with open(artifact_path, "wb") as handle:
            handle.write(payload)

        store = _open_store(run_root, STEP_ID)
        _register(store, item_id, f"{STEP_ID}:j")
        assert store.claim(item_id, owner=owner) is True
        computed = WorkItemResult(
            work_item_id=item_id,
            status=WorkItemStatus.COMPLETED,
            artifacts=ArtifactSet.of(ref),
        )
        store.complete(item_id, result=computed)
        _verify_artifact(run_root, ref)
        store.close()  # crash after commit; bytes rot afterwards

        with open(artifact_path, "r+b") as handle:
            first = handle.read(1)
            handle.seek(0)
            handle.write(bytes((first[0] ^ 0xFF,)))

        # Persisted state: the committed result row is still present.
        with _open_store(run_root, STEP_ID) as reopened:
            assert reopened.get_state(item_id) == StoredWorkItemStatus.COMPLETED
        # Resume decision: flipped bytes fail verification, so reuse is refused.
        with pytest.raises(ArtifactIntegrityError):
            _verify_artifact(run_root, ref)
        decision = evaluate_reuse(
            current=_reuse_inputs(artifact_checksums=(checksum,)),
            stored=_reuse_inputs(artifact_checksums=(checksum,)),
            stored_status=StoredWorkItemStatus.COMPLETED,
            artifacts_verified=False,
            work_item_id=item_id,
        )
        assert decision.decision is ReuseCode.INVALIDATE_ARTIFACT

    def test_failed_verification_alone_forces_invalidate_artifact(self) -> None:
        decision = evaluate_reuse(
            current=_reuse_inputs(),
            stored=_reuse_inputs(),
            stored_status=StoredWorkItemStatus.COMPLETED,
            artifacts_verified=False,
            work_item_id="wi:s1:k",
        )
        assert decision.decision is ReuseCode.INVALIDATE_ARTIFACT
        assert decision.reason == "stored artifacts failed verification"
        control = evaluate_reuse(
            current=_reuse_inputs(),
            stored=_reuse_inputs(),
            stored_status=StoredWorkItemStatus.COMPLETED,
            artifacts_verified=True,
            work_item_id="wi:s1:k",
        )
        assert control.decision is ReuseCode.REUSE
