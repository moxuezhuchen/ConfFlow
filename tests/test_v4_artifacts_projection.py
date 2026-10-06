"""L2 pre-card: typed V4 completed-artifact projection (additive, no old-chain change)."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from confflow.application.execution.errors import ErrorCode, ExecutionServiceError
from confflow.application.execution.v4_artifacts import (
    V4_RUN_RESULT_SCHEMA,
    load_v4_completed_artifacts,
    v4_publication_state,
)
from confflow.persistence.generation import RunGeneration, save_run_generation
from confflow.producer.boundary import RESULT_MANIFEST_SCHEMA
from confflow.producer.contract import build_run_result_manifest

_DEF = "sha256:" + "ab" * 32


def _write_manifest(work: Path, manifest: dict) -> Path:
    path = work / "run_result.json"
    path.write_text(json.dumps(manifest, sort_keys=True, separators=(",", ":")), encoding="utf-8")
    return path


def _completed_manifest(
    run_id: str, locator: str, checksum: str, generation_id: str | None = None
) -> dict:
    return build_run_result_manifest(
        run_id=run_id,
        status="completed",
        definition_digest=_DEF,
        producer_version="0.0.test",
        steps=[],
        analyses=[],
        artifacts=[{"role": "result", "checksum": checksum, "locator": locator}],
        generation_id=generation_id,
    )


def test_result_schema_constant_is_real() -> None:
    assert V4_RUN_RESULT_SCHEMA == RESULT_MANIFEST_SCHEMA == "confflow.run_result_manifest.v1"


def test_completed_projects_real_bytes(tmp_path: Path) -> None:
    work = tmp_path / "work"
    work.mkdir()
    payload = b"hello v4\n"
    (work / "out.txt").write_bytes(payload)
    checksum = "sha256:" + hashlib.sha256(payload).hexdigest()
    manifest = _completed_manifest("run-1", "out.txt", checksum, generation_id="gen-1")
    _write_manifest(work, manifest)
    save_run_generation(
        str(work),
        RunGeneration(
            run_id="run-1", generation_id="gen-1", status="completed", definition_digest=_DEF
        ),
    )
    assert v4_publication_state(str(work)) == "completed"
    artifacts = load_v4_completed_artifacts(str(work))
    assert len(artifacts) == 1
    item = artifacts[0]
    assert item.terminal == "result"
    assert item.path == "out.txt"
    assert item.sha256 == hashlib.sha256(payload).hexdigest()
    assert item.size == len(payload)
    assert item.content_schema == RESULT_MANIFEST_SCHEMA
    # Binding expectations pass through.
    bound = load_v4_completed_artifacts(
        str(work),
        expected_run_id="run-1",
        expected_definition_digest=_DEF,
        expected_generation_id="gen-1",
    )
    assert bound == artifacts


def test_missing_manifest_is_not_published_not_corrupt(tmp_path: Path) -> None:
    work = tmp_path / "work"
    work.mkdir()
    assert v4_publication_state(str(work)) == "not-published"
    with pytest.raises(ExecutionServiceError) as caught:
        load_v4_completed_artifacts(str(work))
    assert caught.value.code is ErrorCode.INVALID_STATE_TRANSITION
    assert "No run_result.json published yet" in str(caught.value)

    save_run_generation(
        str(work),
        RunGeneration(run_id="run-1", generation_id="gen-1", status="running"),
    )
    assert v4_publication_state(str(work)) == "running"
    with pytest.raises(ExecutionServiceError) as caught2:
        load_v4_completed_artifacts(str(work))
    assert caught2.value.code is ErrorCode.INVALID_STATE_TRANSITION


def test_bad_manifest_fail_closed(tmp_path: Path) -> None:
    work = tmp_path / "work"
    work.mkdir()
    (work / "run_result.json").write_text("not-json", encoding="utf-8")
    assert v4_publication_state(str(work)) == "corrupt"
    with pytest.raises(ExecutionServiceError) as caught:
        load_v4_completed_artifacts(str(work))
    assert caught.value.code is ErrorCode.ARTIFACT_INTEGRITY_FAILED

    bad = {"content_schema": "wrong", "run_id": "r", "status": "completed"}
    (work / "run_result.json").write_text(json.dumps(bad), encoding="utf-8")
    with pytest.raises(ExecutionServiceError) as caught2:
        load_v4_completed_artifacts(str(work))
    assert caught2.value.code is ErrorCode.ARTIFACT_INTEGRITY_FAILED


def test_non_completed_statuses_are_distinguished(tmp_path: Path) -> None:
    for status in ("failed", "partial", "cancelled"):
        work = tmp_path / status
        work.mkdir()
        manifest = build_run_result_manifest(
            run_id="run-1",
            status=status,
            definition_digest=_DEF,
            producer_version="0.0.test",
            steps=[],
            analyses=[],
            artifacts=[],
        )
        _write_manifest(work, manifest)
        assert v4_publication_state(str(work)) == status
        with pytest.raises(ExecutionServiceError) as caught:
            load_v4_completed_artifacts(str(work))
        # Never projected as completed; never reported as corrupt.
        assert caught.value.code is ErrorCode.INVALID_STATE_TRANSITION


def test_binding_mismatch_fail_closed(tmp_path: Path) -> None:
    work = tmp_path / "work"
    work.mkdir()
    payload = b"data\n"
    (work / "out.txt").write_bytes(payload)
    checksum = "sha256:" + hashlib.sha256(payload).hexdigest()
    _write_manifest(work, _completed_manifest("run-1", "out.txt", checksum, generation_id="gen-1"))
    save_run_generation(
        str(work),
        RunGeneration(
            run_id="run-1", generation_id="gen-1", status="completed", definition_digest=_DEF
        ),
    )
    with pytest.raises(ExecutionServiceError) as caught:
        load_v4_completed_artifacts(str(work), expected_run_id="other")
    assert caught.value.code is ErrorCode.ARTIFACT_INTEGRITY_FAILED
    with pytest.raises(ExecutionServiceError) as caught2:
        load_v4_completed_artifacts(str(work), expected_generation_id="gen-other")
    assert caught2.value.code is ErrorCode.ARTIFACT_INTEGRITY_FAILED

    # Generation claims a different run.
    save_run_generation(
        str(work),
        RunGeneration(
            run_id="other", generation_id="gen-1", status="completed", definition_digest=_DEF
        ),
    )
    with pytest.raises(ExecutionServiceError) as caught3:
        load_v4_completed_artifacts(str(work))
    assert caught3.value.code is ErrorCode.ARTIFACT_INTEGRITY_FAILED


def test_generation_not_completed_behind_completed_manifest(tmp_path: Path) -> None:
    work = tmp_path / "work"
    work.mkdir()
    payload = b"data\n"
    (work / "out.txt").write_bytes(payload)
    checksum = "sha256:" + hashlib.sha256(payload).hexdigest()
    _write_manifest(work, _completed_manifest("run-1", "out.txt", checksum, generation_id="gen-1"))
    save_run_generation(
        str(work),
        RunGeneration(run_id="run-1", generation_id="gen-1", status="cancelled"),
    )
    with pytest.raises(ExecutionServiceError) as caught:
        load_v4_completed_artifacts(str(work))
    assert caught.value.code is ErrorCode.ARTIFACT_INTEGRITY_FAILED


def test_path_and_checksum_anomalies(tmp_path: Path) -> None:
    work = tmp_path / "work"
    work.mkdir()
    payload = b"data\n"
    (work / "out.txt").write_bytes(payload)
    good = "sha256:" + hashlib.sha256(payload).hexdigest()

    _write_manifest(work, _completed_manifest("run-1", "/abs.txt", good))
    with pytest.raises(ExecutionServiceError) as caught:
        load_v4_completed_artifacts(str(work))
    assert caught.value.code is ErrorCode.ARTIFACT_PATH_INVALID

    _write_manifest(work, _completed_manifest("run-1", "../outside.txt", good))
    with pytest.raises(ExecutionServiceError) as caught2:
        load_v4_completed_artifacts(str(work))
    assert caught2.value.code is ErrorCode.ARTIFACT_PATH_INVALID

    _write_manifest(work, _completed_manifest("run-1", "missing.txt", good))
    with pytest.raises(ExecutionServiceError) as caught3:
        load_v4_completed_artifacts(str(work))
    assert caught3.value.code is ErrorCode.ARTIFACT_INTEGRITY_FAILED

    wrong = "sha256:" + "00" * 32
    _write_manifest(work, _completed_manifest("run-1", "out.txt", wrong))
    with pytest.raises(ExecutionServiceError) as caught4:
        load_v4_completed_artifacts(str(work))
    assert caught4.value.code is ErrorCode.ARTIFACT_INTEGRITY_FAILED

    empty = build_run_result_manifest(
        run_id="run-1",
        status="completed",
        definition_digest=_DEF,
        producer_version="0.0.test",
        steps=[],
        analyses=[],
        artifacts=[],
    )
    _write_manifest(work, empty)
    with pytest.raises(ExecutionServiceError) as caught5:
        load_v4_completed_artifacts(str(work))
    assert caught5.value.code is ErrorCode.ARTIFACT_INTEGRITY_FAILED
