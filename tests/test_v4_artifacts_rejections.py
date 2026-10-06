"""V4 artifact rejection and boundary branches (L2 coverage card, additive)."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

import pytest

from confflow.application.execution.errors import ErrorCode, ExecutionServiceError
from confflow.application.execution.v4_artifacts import (
    load_v4_completed_artifacts,
    v4_publication_state,
)
from confflow.persistence import arbitration as _arbitration
from confflow.persistence.generation import RunGeneration, save_run_generation
from confflow.producer.contract import build_run_result_manifest

_DEF = "sha256:" + "ab" * 32
_OTHER_DEF = "sha256:" + "11" * 32


def _write_raw(work: Path, name: str, text: str) -> Path:
    path = work / name
    path.write_text(text, encoding="utf-8")
    return path


def _write_manifest(work: Path, manifest: dict) -> Path:
    return _write_raw(work, "run_result.json", json.dumps(manifest, sort_keys=True))


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


def _payload_bytes() -> bytes:
    return b"rejection-probe\n"


def _good_checksum(payload: bytes) -> str:
    return "sha256:" + hashlib.sha256(payload).hexdigest()


def test_publication_state_corrupt_variants(tmp_path: Path) -> None:
    work = tmp_path / "pub-corrupt"
    work.mkdir()
    # No manifest + corrupt generation record -> corrupt (never not-published).
    _write_raw(work, "run_generation.json", "not-json")
    assert v4_publication_state(str(work)) == "corrupt"

    work2 = tmp_path / "pub-list"
    work2.mkdir()
    _write_raw(work2, "run_result.json", "[1,2]")
    assert v4_publication_state(str(work2)) == "corrupt"

    work3 = tmp_path / "pub-status-type"
    work3.mkdir()
    _write_manifest(work3, {"status": 123})
    assert v4_publication_state(str(work3)) == "corrupt"

    work4 = tmp_path / "pub-unknown"
    work4.mkdir()
    _write_manifest(work4, {"status": "weird"})
    assert v4_publication_state(str(work4)) == "corrupt"


def test_missing_manifest_rejections(tmp_path: Path) -> None:
    # Corrupt generation record behind an absent manifest is integrity failure.
    missing_corrupt = tmp_path / "missing-corrupt"
    missing_corrupt.mkdir()
    _write_raw(missing_corrupt, "run_generation.json", "not-json")
    with pytest.raises(ExecutionServiceError) as caught:
        load_v4_completed_artifacts(str(missing_corrupt))
    assert caught.value.code is ErrorCode.ARTIFACT_INTEGRITY_FAILED
    assert "run generation record is corrupt" in str(caught.value)

    # Corrupt arbitration ledger degrades to winner-unknown, then not-published.
    degraded = tmp_path / "missing-ledger-corrupt"
    degraded.mkdir()
    save_run_generation(
        str(degraded),
        RunGeneration(run_id="run-1", generation_id="gen-1", status="running"),
    )
    _write_raw(degraded, "generation_ownership.json", "not-json")
    with pytest.raises(ExecutionServiceError) as caught2:
        load_v4_completed_artifacts(str(degraded))
    assert caught2.value.code is ErrorCode.INVALID_STATE_TRANSITION
    assert "No run_result.json published yet" in str(caught2.value)

    # A completed generation without manifest bytes is integrity failure.
    winner_missing = tmp_path / "missing-completed"
    winner_missing.mkdir()
    save_run_generation(
        str(winner_missing),
        RunGeneration(
            run_id="run-1",
            generation_id="gen-1",
            status="completed",
            definition_digest=_DEF,
        ),
    )
    with pytest.raises(ExecutionServiceError) as caught3:
        load_v4_completed_artifacts(str(winner_missing))
    assert caught3.value.code is ErrorCode.ARTIFACT_INTEGRITY_FAILED
    assert "missing its manifest" in str(caught3.value)


def test_unreadable_manifest_is_integrity(tmp_path: Path) -> None:
    work = tmp_path / "unreadable"
    work.mkdir()
    target = work / "run_result.json"
    # Real filesystem OSError: reading /proc/self/mem raises EIO on Linux.
    os.symlink("/proc/self/mem", target)
    assert target.is_file()
    assert v4_publication_state(str(work)) == "corrupt"
    with pytest.raises(ExecutionServiceError) as caught:
        load_v4_completed_artifacts(str(work))
    assert caught.value.code is ErrorCode.ARTIFACT_INTEGRITY_FAILED
    assert "cannot read V4 manifest" in str(caught.value)


def test_manifest_shape_rejections(tmp_path: Path) -> None:
    payload = _payload_bytes()
    good = _good_checksum(payload)

    work = tmp_path / "shape-list"
    work.mkdir()
    _write_raw(work, "run_result.json", "[1,2]")
    with pytest.raises(ExecutionServiceError) as caught:
        load_v4_completed_artifacts(str(work))
    assert caught.value.code is ErrorCode.ARTIFACT_INTEGRITY_FAILED
    assert "must be an object" in str(caught.value)

    base = _completed_manifest("run-1", "out.txt", good)
    (tmp_path / "shape-base").mkdir()
    for name, mutate, fragment in [
        ("missing-field", lambda m: m.pop("provenance"), "missing required field"),
        ("unknown-status", lambda m: m.update(status="weird"), "unknown status"),
        ("empty-run-id", lambda m: m.update(run_id=""), "run_id must be a non-empty"),
        (
            "bad-digest",
            lambda m: m.update(definition_digest="bad"),
            "definition_digest must be a sha256 digest",
        ),
        ("bad-gen-id", lambda m: m.update(generation_id=123), "generation_id must be"),
    ]:
        case = tmp_path / name
        case.mkdir()
        mutated = json.loads(json.dumps(base))
        mutate(mutated)
        _write_manifest(case, mutated)
        with pytest.raises(ExecutionServiceError) as item_caught:
            load_v4_completed_artifacts(str(case))
        assert item_caught.value.code is ErrorCode.ARTIFACT_INTEGRITY_FAILED
        assert fragment in str(item_caught.value)

    # Caller-supplied definition digest must match the manifest claim.
    bound = tmp_path / "shape-expected-digest"
    bound.mkdir()
    _write_manifest(bound, base)
    with pytest.raises(ExecutionServiceError) as caught_exp:
        load_v4_completed_artifacts(str(bound), expected_definition_digest="sha256:" + "00" * 32)
    assert caught_exp.value.code is ErrorCode.ARTIFACT_INTEGRITY_FAILED
    assert "does not match expected" in str(caught_exp.value)


def test_generation_binding_rejections(tmp_path: Path) -> None:
    payload = _payload_bytes()
    good = _good_checksum(payload)

    corrupt_gen = tmp_path / "gen-corrupt"
    corrupt_gen.mkdir()
    (corrupt_gen / "out.txt").write_bytes(payload)
    _write_manifest(corrupt_gen, _completed_manifest("run-1", "out.txt", good))
    _write_raw(corrupt_gen, "run_generation.json", "not-json")
    with pytest.raises(ExecutionServiceError) as caught:
        load_v4_completed_artifacts(str(corrupt_gen))
    assert caught.value.code is ErrorCode.ARTIFACT_INTEGRITY_FAILED
    assert "run generation record is corrupt" in str(caught.value)

    digest_mismatch = tmp_path / "gen-digest"
    digest_mismatch.mkdir()
    (digest_mismatch / "out.txt").write_bytes(payload)
    _write_manifest(digest_mismatch, _completed_manifest("run-1", "out.txt", good))
    save_run_generation(
        str(digest_mismatch),
        RunGeneration(
            run_id="run-1",
            generation_id="gen-1",
            status="completed",
            definition_digest=_OTHER_DEF,
        ),
    )
    with pytest.raises(ExecutionServiceError) as caught2:
        load_v4_completed_artifacts(str(digest_mismatch))
    assert caught2.value.code is ErrorCode.ARTIFACT_INTEGRITY_FAILED
    assert "definition_digest" in str(caught2.value)

    gen_mismatch = tmp_path / "gen-id"
    gen_mismatch.mkdir()
    (gen_mismatch / "out.txt").write_bytes(payload)
    _write_manifest(
        gen_mismatch, _completed_manifest("run-1", "out.txt", good, generation_id="gen-1")
    )
    save_run_generation(
        str(gen_mismatch),
        RunGeneration(
            run_id="run-1",
            generation_id="gen-other",
            status="completed",
            definition_digest=_DEF,
        ),
    )
    with pytest.raises(ExecutionServiceError) as caught3:
        load_v4_completed_artifacts(str(gen_mismatch))
    assert caught3.value.code is ErrorCode.ARTIFACT_INTEGRITY_FAILED
    assert "generation_id" in str(caught3.value)

    # Manifest without a generation id still binds the expected id via generation.
    expected_mismatch = tmp_path / "gen-expected"
    expected_mismatch.mkdir()
    (expected_mismatch / "out.txt").write_bytes(payload)
    _write_manifest(expected_mismatch, _completed_manifest("run-1", "out.txt", good))
    save_run_generation(
        str(expected_mismatch),
        RunGeneration(
            run_id="run-1",
            generation_id="gen-1",
            status="completed",
            definition_digest=_DEF,
        ),
    )
    with pytest.raises(ExecutionServiceError) as caught4:
        load_v4_completed_artifacts(str(expected_mismatch), expected_generation_id="gen-other")
    assert caught4.value.code is ErrorCode.ARTIFACT_INTEGRITY_FAILED
    assert "does not match expected" in str(caught4.value)


def test_arbitration_rejections(tmp_path: Path) -> None:
    payload = _payload_bytes()
    good = _good_checksum(payload)

    # A corrupt ledger fails closed as an arbitration read error.
    corrupt_ledger = tmp_path / "arb-corrupt"
    corrupt_ledger.mkdir()
    (corrupt_ledger / "out.txt").write_bytes(payload)
    _write_manifest(corrupt_ledger, _completed_manifest("run-1", "out.txt", good))
    save_run_generation(
        str(corrupt_ledger),
        RunGeneration(
            run_id="run-1",
            generation_id="gen-1",
            status="completed",
            definition_digest=_DEF,
        ),
    )
    _write_raw(corrupt_ledger, "generation_ownership.json", "not-json")
    with pytest.raises(ExecutionServiceError) as caught:
        load_v4_completed_artifacts(str(corrupt_ledger))
    assert caught.value.code is ErrorCode.ARTIFACT_INTEGRITY_FAILED
    assert "terminal arbitration" in str(caught.value)

    # A durable failed winner behind a completed manifest is refused.
    mismatch = tmp_path / "arb-mismatch"
    mismatch.mkdir()
    (mismatch / "out.txt").write_bytes(payload)
    _arbitration.begin_generation(
        str(mismatch), generation_id="gen-1", run_id="run-1", definition_digest=_DEF
    )
    _arbitration.finalize_generation(
        str(mismatch), generation_id="gen-1", requested_status="failed"
    )
    save_run_generation(
        str(mismatch),
        RunGeneration(
            run_id="run-1",
            generation_id="gen-1",
            status="completed",
            definition_digest=_DEF,
        ),
    )
    _write_manifest(mismatch, _completed_manifest("run-1", "out.txt", good))
    with pytest.raises(ExecutionServiceError) as caught2:
        load_v4_completed_artifacts(str(mismatch))
    assert caught2.value.code is ErrorCode.ARTIFACT_INTEGRITY_FAILED
    assert "terminal arbitration mismatch" in str(caught2.value)


def test_artifact_entry_rejections(tmp_path: Path) -> None:
    payload = _payload_bytes()
    good = _good_checksum(payload)

    cases = [
        ("entry-not-dict", ["x"], "must be an object"),
        (
            "entry-unknown-field",
            [{"role": "result", "checksum": good, "locator": "out.txt", "extra": 1}],
            "unknown fields",
        ),
        (
            "entry-empty-role",
            [{"role": "", "checksum": good, "locator": "out.txt"}],
            "role must be a non-empty",
        ),
        (
            "entry-bad-role",
            [{"role": "bad role!", "checksum": good, "locator": "out.txt"}],
            "not a valid service terminal identifier",
        ),
        (
            "entry-bad-checksum",
            [{"role": "result", "checksum": "not-a-digest", "locator": "out.txt"}],
            "checksum must be a sha256 digest",
        ),
        (
            "locator-padded",
            [{"role": "result", "checksum": good, "locator": " out.txt"}],
            "locator must be a non-empty string",
        ),
        (
            "locator-drive",
            [{"role": "result", "checksum": good, "locator": ":/evil.txt"}],
            "drive prefix",
        ),
        (
            "locator-empty-segment",
            [{"role": "result", "checksum": good, "locator": "a//b"}],
            "empty/dot segment",
        ),
    ]
    for name, artifacts, fragment in cases:
        case = tmp_path / name
        case.mkdir()
        (case / "out.txt").write_bytes(payload)
        manifest = build_run_result_manifest(
            run_id="run-1",
            status="completed",
            definition_digest=_DEF,
            producer_version="0.0.test",
            steps=[],
            analyses=[],
            artifacts=artifacts,
        )
        _write_manifest(case, manifest)
        with pytest.raises(ExecutionServiceError) as caught:
            load_v4_completed_artifacts(str(case))
        if "locator" in fragment or "drive" in fragment or "segment" in fragment:
            assert caught.value.code is ErrorCode.ARTIFACT_PATH_INVALID
        else:
            assert caught.value.code is ErrorCode.ARTIFACT_INTEGRITY_FAILED
        assert fragment in str(caught.value)


def test_artifact_path_escape_and_canonical(tmp_path: Path) -> None:
    payload = _payload_bytes()
    good = _good_checksum(payload)

    # A symlinked directory that resolves outside the work root escapes.
    escape = tmp_path / "escape"
    escape.mkdir()
    outside = tmp_path / "outside-target"
    outside.mkdir()
    (outside / "evil.txt").write_bytes(payload)
    os.symlink(str(outside), escape / "linkdir")
    _write_manifest(escape, _completed_manifest("run-1", "linkdir/evil.txt", good))
    with pytest.raises(ExecutionServiceError) as caught:
        load_v4_completed_artifacts(str(escape))
    assert caught.value.code is ErrorCode.ARTIFACT_PATH_INVALID
    assert "escapes the work root" in str(caught.value)

    # A symlinked file that resolves to a different relative path is non-canonical.
    noncanonical = tmp_path / "noncanonical"
    noncanonical.mkdir()
    (noncanonical / "out.txt").write_bytes(payload)
    os.symlink("out.txt", noncanonical / "link.txt")
    _write_manifest(noncanonical, _completed_manifest("run-1", "link.txt", good))
    with pytest.raises(ExecutionServiceError) as caught2:
        load_v4_completed_artifacts(str(noncanonical))
    assert caught2.value.code is ErrorCode.ARTIFACT_PATH_INVALID
    assert "not canonical" in str(caught2.value)

    # A path segment starting with a non-alphanumeric fails the canonical grammar.
    grammar = tmp_path / "grammar"
    grammar.mkdir()
    (grammar / "out.txt").write_bytes(payload)
    _write_manifest(grammar, _completed_manifest("run-1", "-bad.txt", good))
    with pytest.raises(ExecutionServiceError) as caught3:
        load_v4_completed_artifacts(str(grammar))
    assert caught3.value.code is ErrorCode.ARTIFACT_PATH_INVALID
    assert "not canonical" in str(caught3.value)
