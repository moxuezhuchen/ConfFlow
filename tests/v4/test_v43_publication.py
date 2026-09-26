#!/usr/bin/env python3

"""Crash-consistent publication and V4 run-state tests (V4-3).

Covers ``confflow.persistence.publication`` (publish/load round-trip, digest
stability, temp-leftover tolerance, strict corrupt handling, deterministic
rebuild, completion-policy outcomes, publication verification) and
``confflow.persistence.run_state`` (save/load round-trip, missing/corrupt/
version-mismatched inputs, pure transitions, and the publish-then-crash
repair flow that resolves a stale run state from a valid publication).
"""

from __future__ import annotations

import json
import os
import random
from pathlib import Path
from typing import Any

import pytest

from confflow.domain import (
    ArtifactLocator,
    ArtifactRef,
    ArtifactSet,
    CompletionMode,
    CompletionPolicy,
    Diagnostic,
    DiagnosticSeverity,
    PublicationError,
    RecoveryInfo,
    ResultError,
    ResultSet,
    StepProvenance,
    StepResult,
    StepStatus,
    StructureSet,
    Timing,
    WorkItemResult,
    WorkItemStatus,
    typed_digest,
)
from confflow.persistence import (
    RunState,
    RunStepStatus,
    StepLifecycle,
    run_state_path,
    step_dir,
    step_result_path,
)
from confflow.persistence.contracts import CorruptStateError, PersistenceError
from confflow.persistence.publication import (
    STEP_RESULT_DIGEST_KIND,
    load_published_step_result,
    publish_step_result,
    rebuild_step_result,
    verify_for_publication,
)
from confflow.persistence.run_state import (
    detect_published,
    ensure_step,
    load_run_state,
    repair_after_publish,
    save_run_state,
    transition_step,
)
from tests.v4._builders import checkpoint, energy_result, structure

STEP_ID = "s1"
DEFINITION_DIGEST = "sha256:" + "ab" * 32
STEP_SEMANTIC_DIGEST = "sha256:" + "cd" * 32

REQUIRE_ALL = CompletionPolicy(mode=CompletionMode.REQUIRE_ALL)
ALLOW_PARTIAL = CompletionPolicy(mode=CompletionMode.ALLOW_PARTIAL)

STEP_RESULT_FILENAME_TMP = "step_result.json.tmp.999.0"


def completed_item(index: int) -> WorkItemResult:
    """Build a completed work-item result with distinct payloads per index."""
    tag = f"i{index:02d}"
    return WorkItemResult(
        work_item_id=f"wi:{STEP_ID}:{tag}",
        status=WorkItemStatus.COMPLETED,
        structures=StructureSet.of(structure(f"struct_{tag}")),
        results=ResultSet.of(
            energy_result(
                -76.0 - 0.01 * index,
                subject_structure_id=f"struct_{tag}",
                source_step_id=STEP_ID,
            )
        ),
        artifacts=ArtifactSet.of(
            checkpoint(f"struct_{tag}", artifact_id=f"chk_{tag}", producer_step_id=STEP_ID)
        ),
    )


def failed_item(index: int = 99) -> WorkItemResult:
    """Build a failed work-item result carrying structured error info."""
    return WorkItemResult(
        work_item_id=f"wi:{STEP_ID}:f{index:02d}",
        status=WorkItemStatus.FAILED,
        error=ResultError(code="exec_failed", message="boom", retryable=True),
    )


def ten_items() -> tuple[WorkItemResult, ...]:
    """Build nine completed items plus one failed item."""
    return tuple(completed_item(index) for index in range(9)) + (failed_item(),)


def small_result() -> StepResult:
    """Build a three-item completed step result ready to publish."""
    return rebuild_step_result(
        step_id=STEP_ID,
        items=tuple(completed_item(index) for index in range(3)),
        completion=REQUIRE_ALL,
        definition_digest=DEFINITION_DIGEST,
        step_semantic_digest=STEP_SEMANTIC_DIGEST,
    )


class TestPublishLoadRoundTrip:
    """Publish then load preserves the exact canonical payload."""

    def test_round_trip_preserves_to_dict(self, tmp_path: Path) -> None:
        result = small_result()
        run_root = str(tmp_path / "run")
        digest = publish_step_result(run_root=run_root, step_id=STEP_ID, step_result=result)
        assert os.path.isfile(step_result_path(run_root, STEP_ID))
        loaded = load_published_step_result(run_root=run_root, step_id=STEP_ID)
        assert loaded is not None
        assert loaded.to_dict() == result.to_dict()
        assert typed_digest(STEP_RESULT_DIGEST_KIND, loaded.to_dict()) == digest

    def test_publish_twice_gives_stable_digest(self, tmp_path: Path) -> None:
        result = small_result()
        run_root = str(tmp_path / "run")
        first = publish_step_result(run_root=run_root, step_id=STEP_ID, step_result=result)
        second = publish_step_result(run_root=run_root, step_id=STEP_ID, step_result=result)
        assert first == second
        loaded = load_published_step_result(run_root=run_root, step_id=STEP_ID)
        assert loaded is not None
        assert typed_digest(STEP_RESULT_DIGEST_KIND, loaded.to_dict()) == first

    def test_load_missing_returns_none(self, tmp_path: Path) -> None:
        assert load_published_step_result(run_root=str(tmp_path / "run"), step_id=STEP_ID) is None

    def test_temp_leftover_is_ignored(self, tmp_path: Path) -> None:
        run_root = str(tmp_path / "run")
        directory = step_dir(run_root, STEP_ID)
        os.makedirs(directory, exist_ok=True)
        garbage = os.path.join(directory, STEP_RESULT_FILENAME_TMP)
        with open(garbage, "w", encoding="utf-8") as handle:
            handle.write("{not valid json")
        result = small_result()
        publish_step_result(run_root=run_root, step_id=STEP_ID, step_result=result)
        loaded = load_published_step_result(run_root=run_root, step_id=STEP_ID)
        assert loaded is not None
        assert loaded.to_dict() == result.to_dict()

    def test_step_id_mismatch_raises(self, tmp_path: Path) -> None:
        with pytest.raises(PersistenceError, match="not"):
            publish_step_result(
                run_root=str(tmp_path / "run"),
                step_id="other",
                step_result=small_result(),
            )


class TestCorruptPublications:
    """Corrupt publications fail closed; they are never partially loaded."""

    def _write_raw(self, run_root: str, payload: bytes) -> None:
        directory = step_dir(run_root, STEP_ID)
        os.makedirs(directory, exist_ok=True)
        with open(step_result_path(run_root, STEP_ID), "wb") as handle:
            handle.write(payload)

    def _write_json(self, run_root: str, payload: Any) -> None:
        self._write_raw(run_root, json.dumps(payload).encode("utf-8"))

    def test_truncated_file_raises(self, tmp_path: Path) -> None:
        run_root = str(tmp_path / "run")
        self._write_raw(run_root, b'{"step_id": "s1", "stat')
        with pytest.raises(CorruptStateError):
            load_published_step_result(run_root=run_root, step_id=STEP_ID)

    def test_empty_file_raises(self, tmp_path: Path) -> None:
        run_root = str(tmp_path / "run")
        self._write_raw(run_root, b"")
        with pytest.raises(CorruptStateError):
            load_published_step_result(run_root=run_root, step_id=STEP_ID)

    def test_bad_shape_raises(self, tmp_path: Path) -> None:
        run_root = str(tmp_path / "run")
        self._write_json(run_root, {"step_id": STEP_ID, "status": "completed"})
        with pytest.raises(CorruptStateError):
            load_published_step_result(run_root=run_root, step_id=STEP_ID)

    def test_bad_status_raises(self, tmp_path: Path) -> None:
        run_root = str(tmp_path / "run")
        payload = small_result().to_dict()
        payload["status"] = "vaporized"
        self._write_json(run_root, payload)
        with pytest.raises(CorruptStateError):
            load_published_step_result(run_root=run_root, step_id=STEP_ID)

    def test_step_id_mismatch_on_load_raises(self, tmp_path: Path) -> None:
        run_root = str(tmp_path / "run")
        payload = small_result().to_dict()
        payload["step_id"] = "other"
        self._write_json(run_root, payload)
        with pytest.raises(CorruptStateError):
            load_published_step_result(run_root=run_root, step_id=STEP_ID)

    def test_inconsistent_status_raises(self, tmp_path: Path) -> None:
        run_root = str(tmp_path / "run")
        payload = small_result().to_dict()
        payload["item_results"] = [failed_item().to_dict()]
        payload["status"] = StepStatus.COMPLETED.value
        self._write_json(run_root, payload)
        with pytest.raises(CorruptStateError):
            load_published_step_result(run_root=run_root, step_id=STEP_ID)


class TestRebuildDeterminism:
    """Rebuilds are ordered by work-item id, never by input order."""

    def test_shuffled_input_gives_identical_result(self) -> None:
        items = ten_items()
        shuffled = list(items)
        random.Random(2026).shuffle(shuffled)
        assert [item.work_item_id for item in shuffled] != [item.work_item_id for item in items]
        first = rebuild_step_result(
            step_id=STEP_ID,
            items=tuple(shuffled),
            completion=ALLOW_PARTIAL,
            definition_digest=DEFINITION_DIGEST,
            step_semantic_digest=STEP_SEMANTIC_DIGEST,
        )
        second = rebuild_step_result(
            step_id=STEP_ID,
            items=items,
            completion=ALLOW_PARTIAL,
            definition_digest=DEFINITION_DIGEST,
            step_semantic_digest=STEP_SEMANTIC_DIGEST,
        )
        assert first.to_dict() == second.to_dict()
        assert typed_digest(STEP_RESULT_DIGEST_KIND, first.to_dict()) == typed_digest(
            STEP_RESULT_DIGEST_KIND, second.to_dict()
        )
        assert first.work_item_ids == tuple(sorted(first.work_item_ids))

    def test_rebuild_rejects_non_item_members(self) -> None:
        with pytest.raises(PersistenceError, match="WorkItemResult"):
            rebuild_step_result(
                step_id=STEP_ID,
                items=(completed_item(0), "nope"),  # type: ignore[arg-type]
                completion=REQUIRE_ALL,
            )


class TestRebuildCompletion:
    """Completion policies decide status; failures are retained, not dropped."""

    def test_require_all_nine_one_is_failed(self) -> None:
        result = rebuild_step_result(step_id=STEP_ID, items=ten_items(), completion=REQUIRE_ALL)
        assert result.status is StepStatus.FAILED
        assert len(result.item_results) == 10
        retained = [item for item in result.item_results if not item.is_completed]
        assert len(retained) == 1
        assert retained[0].status is WorkItemStatus.FAILED
        assert len(result.structures) == 9
        assert len(result.results) == 9
        assert len(result.artifacts) == 9
        assert result.summary.thaw() == {
            "total": 10,
            "completed": 9,
            "failed": 1,
            "cancelled": 0,
            "completion_mode": CompletionMode.REQUIRE_ALL.value,
            "status": StepStatus.FAILED.value,
        }

    def test_allow_partial_nine_one_accepts_nine(self) -> None:
        result = rebuild_step_result(step_id=STEP_ID, items=ten_items(), completion=ALLOW_PARTIAL)
        assert result.status is StepStatus.PARTIAL
        assert len(result.item_results) == 10
        assert len(result.structures) == 9
        assert len(result.results) == 9
        assert len(result.artifacts) == 9
        assert result.summary.thaw() == {
            "total": 10,
            "completed": 9,
            "failed": 1,
            "cancelled": 0,
            "completion_mode": CompletionMode.ALLOW_PARTIAL.value,
            "status": StepStatus.PARTIAL.value,
        }

    def test_empty_items_complete_vacuously(self) -> None:
        result = rebuild_step_result(step_id=STEP_ID, items=(), completion=REQUIRE_ALL)
        assert result.status is StepStatus.COMPLETED
        assert result.item_results == ()
        assert len(result.structures) == 0
        assert result.summary.thaw() == {
            "total": 0,
            "completed": 0,
            "failed": 0,
            "cancelled": 0,
            "completion_mode": CompletionMode.REQUIRE_ALL.value,
            "status": StepStatus.COMPLETED.value,
        }

    def test_provenance_records_digests(self) -> None:
        result = rebuild_step_result(
            step_id=STEP_ID,
            items=(completed_item(0),),
            completion=REQUIRE_ALL,
            definition_digest=DEFINITION_DIGEST,
            step_semantic_digest=STEP_SEMANTIC_DIGEST,
        )
        assert result.provenance == StepProvenance(
            workflow_definition_digest=DEFINITION_DIGEST,
            step_semantic_digest=STEP_SEMANTIC_DIGEST,
        )


class TestVerifyForPublication:
    """The wiring wrapper enforces the domain durability gate."""

    def test_rejects_artifact_without_checksum(self) -> None:
        artifact = ArtifactRef(
            id="chk_missing",
            role="checkpoint",
            locator=ArtifactLocator.run_relative("steps/s1/missing.chk"),
        )
        result = StepResult(
            step_id=STEP_ID,
            status=StepStatus.COMPLETED,
            item_results=(completed_item(0),),
            artifacts=ArtifactSet.of(artifact),
        )
        with pytest.raises(PublicationError, match="no checksum"):
            verify_for_publication(
                result,
                durable_item_ids=result.work_item_ids,
                verified_artifact_checksums=(),
            )

    def test_rejects_unverified_checksum(self) -> None:
        artifact = checkpoint("subject-x", artifact_id="chk_x", producer_step_id=STEP_ID)
        assert artifact.checksum is not None
        result = StepResult(
            step_id=STEP_ID,
            status=StepStatus.COMPLETED,
            item_results=(completed_item(0),),
            artifacts=ArtifactSet.of(artifact),
        )
        with pytest.raises(PublicationError, match="not verified"):
            verify_for_publication(
                result,
                durable_item_ids=result.work_item_ids,
                verified_artifact_checksums=("sha256:" + "0" * 64,),
            )

    def test_rejects_nondurable_items(self) -> None:
        result = small_result()
        with pytest.raises(PublicationError, match="durable"):
            verify_for_publication(
                result,
                durable_item_ids=(),
                verified_artifact_checksums=[
                    artifact.checksum
                    for artifact in result.artifacts
                    if artifact.checksum is not None
                ],
            )

    def test_accepts_durable_verified_publication(self) -> None:
        result = small_result()
        checksums = [
            artifact.checksum for artifact in result.artifacts if artifact.checksum is not None
        ]
        assert len(checksums) == len(result.artifacts)
        verify_for_publication(
            result,
            durable_item_ids=result.work_item_ids,
            verified_artifact_checksums=checksums,
        )


class TestRunStatePersistence:
    """Run-state save/load is atomic, strict, and fail-closed."""

    def test_save_load_round_trip(self, tmp_path: Path) -> None:
        run_root = str(tmp_path / "run")
        state = RunState(
            run_id="run-1",
            definition_digest=DEFINITION_DIGEST,
            steps=(
                StepLifecycle(
                    step_id=STEP_ID,
                    status=RunStepStatus.COMPLETED,
                    published_step_result_digest="sha256:" + "ee" * 32,
                    updated_wall=1234.5,
                ),
            ),
            created_wall=1234.0,
            updated_wall=1234.5,
        )
        save_run_state(run_root, state)
        assert os.path.isfile(run_state_path(run_root))
        loaded = load_run_state(run_root)
        assert loaded is not None
        assert loaded.to_dict() == state.to_dict()

    def test_missing_file_returns_none(self, tmp_path: Path) -> None:
        assert load_run_state(str(tmp_path / "run")) is None

    def test_corrupt_json_raises(self, tmp_path: Path) -> None:
        run_root = str(tmp_path / "run")
        os.makedirs(run_root, exist_ok=True)
        with open(run_state_path(run_root), "w", encoding="utf-8") as handle:
            handle.write('{"run_id": "r1", "step')
        with pytest.raises(CorruptStateError):
            load_run_state(run_root)

    def test_bad_shape_raises(self, tmp_path: Path) -> None:
        run_root = str(tmp_path / "run")
        os.makedirs(run_root, exist_ok=True)
        with open(run_state_path(run_root), "w", encoding="utf-8") as handle:
            json.dump({"steps": []}, handle)
        with pytest.raises(CorruptStateError):
            load_run_state(run_root)

    def test_unknown_version_raises(self, tmp_path: Path) -> None:
        run_root = str(tmp_path / "run")
        os.makedirs(run_root, exist_ok=True)
        with open(run_state_path(run_root), "w", encoding="utf-8") as handle:
            json.dump({"run_id": "r1", "schema_version": 999, "steps": []}, handle)
        with pytest.raises(CorruptStateError):
            load_run_state(run_root)

    def test_temp_leftover_is_ignored(self, tmp_path: Path) -> None:
        run_root = str(tmp_path / "run")
        os.makedirs(run_root, exist_ok=True)
        with open(
            os.path.join(run_root, "run_state.json.tmp.999.0"), "w", encoding="utf-8"
        ) as handle:
            handle.write("{garbage")
        state = RunState(run_id="r1")
        save_run_state(run_root, state)
        loaded = load_run_state(run_root)
        assert loaded is not None
        assert loaded.to_dict() == state.to_dict()


class TestRunStateTransitions:
    """Transitions are pure; unknown steps fail loudly."""

    def test_transition_is_pure(self) -> None:
        state = RunState(run_id="r1", steps=(StepLifecycle(step_id=STEP_ID),))
        updated = transition_step(state, STEP_ID, RunStepStatus.RUNNING)
        assert state.step(STEP_ID) is not None
        assert state.step(STEP_ID).status is RunStepStatus.PENDING
        assert updated is not state
        assert updated.step(STEP_ID).status is RunStepStatus.RUNNING
        assert updated.step(STEP_ID).updated_wall is not None
        assert updated.updated_wall is not None

    def test_transition_records_digest(self) -> None:
        state = RunState(run_id="r1", steps=(StepLifecycle(step_id=STEP_ID),))
        digest = "sha256:" + "ff" * 32
        updated = transition_step(
            state, STEP_ID, RunStepStatus.COMPLETED, published_step_result_digest=digest
        )
        assert updated.step(STEP_ID).published_step_result_digest == digest
        assert state.step(STEP_ID).published_step_result_digest is None

    def test_transition_unknown_step_raises(self) -> None:
        state = RunState(run_id="r1")
        with pytest.raises(PersistenceError, match="unknown step"):
            transition_step(state, "nope", RunStepStatus.RUNNING)

    def test_ensure_step_is_idempotent(self) -> None:
        state = RunState(run_id="r1")
        first = ensure_step(state, STEP_ID)
        assert first.step(STEP_ID) is not None
        assert first.step(STEP_ID).status is RunStepStatus.PENDING
        assert state.step(STEP_ID) is None
        second = ensure_step(first, STEP_ID)
        assert second is first

    def test_ensure_step_rejects_bad_segment(self) -> None:
        state = RunState(run_id="r1")
        with pytest.raises(PersistenceError):
            ensure_step(state, "a/b")


class TestRepairFlow:
    """A published result with a stale state repairs via detect + transition."""

    def test_publish_crash_repair(self, tmp_path: Path) -> None:
        run_root = str(tmp_path / "run")
        result = small_result()
        digest = publish_step_result(run_root=run_root, step_id=STEP_ID, step_result=result)
        stale = RunState(run_id="run-repair")
        found = detect_published(run_root, STEP_ID)
        assert found == digest
        repaired = repair_after_publish(stale, STEP_ID, RunStepStatus.COMPLETED, found)
        assert stale.step(STEP_ID) is None
        assert repaired.step(STEP_ID) is not None
        assert repaired.step(STEP_ID).status is RunStepStatus.COMPLETED
        assert repaired.step(STEP_ID).published_step_result_digest == digest
        save_run_state(run_root, repaired)
        loaded = load_run_state(run_root)
        assert loaded is not None
        assert loaded.step(STEP_ID) is not None
        assert loaded.step(STEP_ID).status is RunStepStatus.COMPLETED
        assert loaded.step(STEP_ID).published_step_result_digest == digest

    def test_detect_absent_returns_none(self, tmp_path: Path) -> None:
        assert detect_published(str(tmp_path / "run"), STEP_ID) is None

    def test_detect_corrupt_propagates(self, tmp_path: Path) -> None:
        run_root = str(tmp_path / "run")
        directory = step_dir(run_root, STEP_ID)
        os.makedirs(directory, exist_ok=True)
        with open(step_result_path(run_root, STEP_ID), "wb") as handle:
            handle.write(b"truncated{")
        with pytest.raises(CorruptStateError):
            detect_published(run_root, STEP_ID)

    def test_repair_does_not_infer_status(self) -> None:
        state = RunState(run_id="r1")
        digest = "sha256:" + "11" * 32
        partial = repair_after_publish(state, STEP_ID, RunStepStatus.PARTIAL, digest)
        assert partial.step(STEP_ID).status is RunStepStatus.PARTIAL
        assert partial.step(STEP_ID).published_step_result_digest == digest


class TestDomainShapeMirroring:
    """Loaded rows mirror domain to_dict shapes, including nested records."""

    def test_nested_records_survive_round_trip(self, tmp_path: Path) -> None:
        item = WorkItemResult(
            work_item_id=f"wi:{STEP_ID}:rich",
            status=WorkItemStatus.COMPLETED,
            structures=StructureSet.of(structure("rich_struct")),
            results=ResultSet.of(
                energy_result(-76.1, subject_structure_id="rich_struct", source_step_id=STEP_ID)
            ),
            artifacts=ArtifactSet.of(
                checkpoint("rich_struct", artifact_id="chk_rich", producer_step_id=STEP_ID)
            ),
            diagnostics=(
                Diagnostic(
                    code="note",
                    message="all good",
                    severity=DiagnosticSeverity.INFO,
                    step_id=STEP_ID,
                    work_item_id=f"wi:{STEP_ID}:rich",
                    logical_key=f"{STEP_ID}:rich",
                    field_path="energy",
                    details={"extra": 1},
                ),
            ),
            timing=Timing(started_at=1.0, finished_at=2.0, duration_seconds=1.0),
            recovery=RecoveryInfo(profile="none", attempted=False),
            metadata={"origin": "test"},
        )
        result = rebuild_step_result(
            step_id=STEP_ID,
            items=(item,),
            completion=REQUIRE_ALL,
            definition_digest=DEFINITION_DIGEST,
            step_semantic_digest=STEP_SEMANTIC_DIGEST,
        )
        run_root = str(tmp_path / "run")
        publish_step_result(run_root=run_root, step_id=STEP_ID, step_result=result)
        loaded = load_published_step_result(run_root=run_root, step_id=STEP_ID)
        assert loaded is not None
        assert loaded.to_dict() == result.to_dict()
