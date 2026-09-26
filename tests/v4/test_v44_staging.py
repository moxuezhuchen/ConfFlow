#!/usr/bin/env python3

"""Secure typed staging tests for the V4 remote boundary (V4-4).

Covers :mod:`confflow.remote.staging`: happy-path input staging across all
three entry kinds, checksum/traversal/symlink/permission fail-closes with no
leftover files, TOCTOU stability rechecks, and producer-side result import
(identity, attempt, checksum, and payload reconstruction).
"""

from __future__ import annotations

import hashlib
import os
import stat
from collections.abc import Iterable
from pathlib import Path
from typing import Any

import pytest

from confflow.domain import (
    ArtifactSet,
    FrozenDict,
    ResultSet,
    StructureSet,
    WorkItemResult,
)
from confflow.domain.completion import WorkItemStatus
from confflow.domain.retention import RetentionClass
from confflow.domain.work_item import RecoveryInfo, Timing
from confflow.remote.envelope import (
    ArtifactBundleEntry,
    ExecutionDefinition,
    InputBundleManifest,
    ResultBundle,
    ResultEntry,
    ResultProducedArtifact,
    StructureBundleEntry,
    WorkerHandoffV2,
    bundle_entry_digest,
)
from confflow.remote.staging import (
    StagedBundle,
    StagingError,
    _verify_temp_stability,
    import_result_artifacts,
    stage_input_bundle,
)
from tests.v4._builders import energy_result, structure

pytestmark = pytest.mark.skipif(os.name != "posix", reason="staging contract requires POSIX")

__all__: list[str] = []

_DATA = b"confflow-v44-staging-bytes-0123456789"
_ARTIFACT_ID = "art-in-1"
_STRUCTURE_ID = "struct-1"
_RESULT_ID = "energy:struct-1"
_LOCATOR = "files/0001-input.bin"
_STEP_ID = "s_opt"
_WORK_ITEM_ID = "wi:s_opt:k1"
_DIGEST_A = "sha256:" + "aa" * 32
_DIGEST_B = "sha256:" + "bb" * 32


def _sha256(data: bytes) -> str:
    """Return the ``sha256:<hex>`` checksum of *data*."""
    return "sha256:" + hashlib.sha256(data).hexdigest()


def _manifest(
    data: bytes = _DATA,
    *,
    checksum: str | None = None,
    locator: str = _LOCATOR,
    artifact_id: str = _ARTIFACT_ID,
) -> InputBundleManifest:
    """Build a manifest carrying one entry of each kind."""
    structure_payload = {"id": _STRUCTURE_ID, "atoms": ["H"]}
    result_payload = {"kind": "energy", "value": -1.0}
    return InputBundleManifest(
        entries=(
            StructureBundleEntry(
                structure_id=_STRUCTURE_ID,
                payload=structure_payload,
                digest=bundle_entry_digest("structure", structure_payload),
            ),
            ArtifactBundleEntry(
                artifact_id=artifact_id,
                role="checkpoint",
                checksum=checksum or _sha256(data),
                bundle_locator=locator,
                size_bytes=len(data),
            ),
            ResultEntry(
                result_id=_RESULT_ID,
                payload=result_payload,
                digest=bundle_entry_digest("result", result_payload),
            ),
        )
    )


def _execution() -> ExecutionDefinition:
    """Build a minimal execution definition for handoff tests."""
    return ExecutionDefinition(
        program="g16",
        native={},
        execution_adapter="standard",
        result_profile="standard",
        checks=(),
        check_params={},
        recovery="none",
        recovery_params={},
        resources={},
        step_semantic_digest=_DIGEST_A,
        contract_versions={},
    )


def _handoff(
    manifest: InputBundleManifest,
    *,
    token: str = "tok-1",
    digest: str = _DIGEST_A,
    attempt: int = 1,
) -> WorkerHandoffV2:
    """Build a handoff envelope over *manifest*."""
    # NOTE: envelope ``.new()`` digests its payload, and the canonicalizer
    # only accepts plain JSON data, so models cross as dumped mappings
    # (with strict-model tuples restored).
    execution = _execution().model_dump(mode="json")
    execution["checks"] = tuple(execution["checks"])
    inputs = manifest.model_dump(mode="json")
    inputs["entries"] = tuple(inputs["entries"])
    return WorkerHandoffV2.new(
        schema="confflow.control.worker-handoff.v2",
        protocol_version="v2",
        run_id="run-1",
        step_id=_STEP_ID,
        work_item_id=_WORK_ITEM_ID,
        logical_key=f"{_STEP_ID}:k1",
        attempt_number=attempt,
        launch_token=token,
        work_item_digest=digest,
        step_semantic_digest=digest,
        producer_provenance={},
        environment_request={"program": "g16"},
        execution=execution,
        inputs=inputs,
    )


def _roots(tmp_path: Path) -> tuple[str, str, str]:
    """Create producer, run, and worker roots; return them as strings."""
    producer = tmp_path / "producer"
    run_root = tmp_path / "run"
    worker_root = tmp_path / "worker"
    for directory in (producer, run_root, worker_root):
        directory.mkdir(parents=True, exist_ok=True)
    return str(producer), str(run_root), str(worker_root)


def _write_producer(producer: str, name: str, data: bytes) -> str:
    """Write *data* under *producer*; return the absolute source path."""
    path = os.path.join(producer, name)
    with open(path, "wb") as handle:
        handle.write(data)
    return path


def _files_under(root: str) -> list[str]:
    """Return all regular files under *root* without following symlinks."""
    found: list[str] = []
    for dirpath, _dirnames, filenames in os.walk(root, followlinks=False):
        for name in filenames:
            candidate = os.path.join(dirpath, name)
            if os.path.islink(candidate):
                continue
            found.append(candidate)
    return found


class _Attempt:
    """Minimal attempt record exposing only ``attempt_number``."""

    def __init__(self, number: int) -> None:
        self.attempt_number = number


class _FakeStore:
    """Opaque store double exposing only ``get_attempts``."""

    def __init__(self, numbers: Iterable[int]) -> None:
        self._numbers = tuple(numbers)

    def get_attempts(self, work_item_id: str) -> tuple[_Attempt, ...]:
        """Return attempt records for *work_item_id* in order."""
        assert work_item_id == _WORK_ITEM_ID
        return tuple(_Attempt(number) for number in self._numbers)


def _completed_payload() -> dict[str, Any]:
    """Build a completed result payload whose artifacts will be replaced."""
    result = WorkItemResult(
        work_item_id=_WORK_ITEM_ID,
        status=WorkItemStatus.COMPLETED,
        structures=StructureSet.of(structure(_STRUCTURE_ID)),
        results=ResultSet.of(energy_result(-40.5, subject_structure_id=_STRUCTURE_ID)),
        artifacts=ArtifactSet(),
        diagnostics=(),
        timing=Timing(finished_at=10.0, duration_seconds=1.0),
        recovery=RecoveryInfo(profile="none", attempted=False),
        semantic_digest=_DIGEST_A,
        metadata=FrozenDict({}),
    )
    return result.to_dict()


def _write_result_tree(
    worker_root: str,
    token: str,
    bundle: ResultBundle,
    files: dict[str, bytes],
) -> str:
    """Write the bundle file plus worker files; return the result path."""
    result_dir = os.path.join(worker_root, "results", token)
    os.makedirs(os.path.join(result_dir, "files"), mode=0o700, exist_ok=True)
    result_path = os.path.join(result_dir, "result.json")
    with open(result_path, "wb") as handle:
        handle.write(bundle.model_dump_json().encode("utf-8"))
    for name, data in files.items():
        with open(os.path.join(result_dir, "files", name), "wb") as handle:
            handle.write(data)
    return result_path


def _result_bundle(
    payload: dict[str, Any],
    data: bytes,
    *,
    token: str = "tok-h1",
    digest: str = _DIGEST_A,
    attempt: int = 1,
    locator: str = "files/0001-out.chk",
    size: int | None = None,
) -> ResultBundle:
    """Build a result bundle announcing one produced artifact."""
    announced = ResultProducedArtifact(
        artifact_id="art-out-1",
        role="checkpoint",
        checksum=_sha256(data),
        size_bytes=len(data) if size is None else size,
        subject_structure_id=_STRUCTURE_ID,
        media_type="application/octet-stream",
        bundle_locator=locator,
    )
    return ResultBundle.new(
        schema="confflow.control.worker-result.v2",
        protocol_version="v2",
        run_id="run-1",
        step_id=_STEP_ID,
        work_item_id=_WORK_ITEM_ID,
        attempt_number=attempt,
        launch_token=token,
        work_item_digest=digest,
        environment={"program": "g16"},
        result=payload,
        produced_artifacts=(announced.model_dump(mode="json"),),
    )


# ----------------------------------------------------------------------
# stage_input_bundle
# ----------------------------------------------------------------------


def test_stage_happy_path_lists_all_entry_kinds(tmp_path: Path) -> None:
    """Staged bytes match and every entry kind is listed."""
    producer, run_root, worker_root = _roots(tmp_path)
    source = _write_producer(producer, "input.bin", _DATA)
    staged = stage_input_bundle(
        manifest=_manifest(),
        run_root=run_root,
        worker_root=worker_root,
        launch_token="tok-happy",
        source_files={_ARTIFACT_ID: source},
    )
    assert isinstance(staged, StagedBundle)
    assert staged.work_dir == os.path.join(worker_root, "work", "tok-happy")
    assert stat.S_IMODE(os.lstat(staged.work_dir).st_mode) == 0o700
    by_id = {entry.entry_id: entry for entry in staged.entries}
    assert set(by_id) == {_ARTIFACT_ID, _STRUCTURE_ID, _RESULT_ID}
    artifact = by_id[_ARTIFACT_ID]
    assert artifact.kind == "artifact"
    assert artifact.staged_path is not None
    assert Path(artifact.staged_path).read_bytes() == _DATA
    assert artifact.checksum_verified is True
    assert stat.S_IMODE(os.lstat(artifact.staged_path).st_mode) == 0o600
    assert by_id[_STRUCTURE_ID].kind == "structure"
    assert by_id[_STRUCTURE_ID].staged_path is None
    assert by_id[_RESULT_ID].kind == "result"
    assert by_id[_RESULT_ID].staged_path is None


def test_stage_checksum_mismatch_leaves_no_files(tmp_path: Path) -> None:
    """A wrong digest fails closed and stages nothing."""
    producer, run_root, worker_root = _roots(tmp_path)
    source = _write_producer(producer, "input.bin", _DATA)
    with pytest.raises(StagingError):
        stage_input_bundle(
            manifest=_manifest(checksum=_sha256(b"something else entirely")),
            run_root=run_root,
            worker_root=worker_root,
            launch_token="tok-bad-digest",
            source_files={_ARTIFACT_ID: source},
        )
    assert _files_under(worker_root) == []


def test_stage_size_mismatch_leaves_no_files(tmp_path: Path) -> None:
    """A wrong announced size fails closed and stages nothing."""
    producer, run_root, worker_root = _roots(tmp_path)
    source = _write_producer(producer, "input.bin", _DATA)
    manifest = _manifest()
    entries = tuple(manifest.entries)
    altered = ArtifactBundleEntry(
        artifact_id=_ARTIFACT_ID,
        role="checkpoint",
        checksum=_sha256(_DATA),
        bundle_locator=_LOCATOR,
        size_bytes=len(_DATA) + 1,
    )
    manifest = InputBundleManifest(
        entries=(entries[0], altered, entries[2]),
    )
    with pytest.raises(StagingError):
        stage_input_bundle(
            manifest=manifest,
            run_root=run_root,
            worker_root=worker_root,
            launch_token="tok-bad-size",
            source_files={_ARTIFACT_ID: source},
        )
    assert _files_under(worker_root) == []


def test_stage_traversal_locator_is_rejected(tmp_path: Path) -> None:
    """A bundle locator escaping the work directory fails closed."""
    producer, run_root, worker_root = _roots(tmp_path)
    source = _write_producer(producer, "input.bin", _DATA)
    with pytest.raises(StagingError):
        stage_input_bundle(
            manifest=_manifest(locator="../evil.bin"),
            run_root=run_root,
            worker_root=worker_root,
            launch_token="tok-traversal",
            source_files={_ARTIFACT_ID: source},
        )
    assert not (tmp_path / "evil.bin").exists()
    assert _files_under(worker_root) == []


def test_stage_symlink_source_is_rejected(tmp_path: Path) -> None:
    """A symlinked producer source fails closed."""
    producer, run_root, worker_root = _roots(tmp_path)
    real = _write_producer(producer, "real.bin", _DATA)
    link = os.path.join(producer, "link.bin")
    os.symlink(real, link)
    with pytest.raises(StagingError):
        stage_input_bundle(
            manifest=_manifest(),
            run_root=run_root,
            worker_root=worker_root,
            launch_token="tok-symlink-src",
            source_files={_ARTIFACT_ID: link},
        )
    assert _files_under(worker_root) == []


def test_stage_symlink_dest_parent_is_rejected(tmp_path: Path) -> None:
    """A symlinked staging parent fails closed before any bytes move."""
    producer, run_root, worker_root = _roots(tmp_path)
    source = _write_producer(producer, "input.bin", _DATA)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    work = os.path.join(worker_root, "work")
    os.makedirs(work, exist_ok=True)
    os.symlink(str(elsewhere), os.path.join(work, "tok-link"))
    with pytest.raises(StagingError):
        stage_input_bundle(
            manifest=_manifest(),
            run_root=run_root,
            worker_root=worker_root,
            launch_token="tok-link",
            source_files={_ARTIFACT_ID: source},
        )
    assert _files_under(str(elsewhere)) == []


@pytest.mark.skipif(
    not hasattr(os, "getuid") or os.getuid() == 0,
    reason="permission-bit enforcement is not observable as root",
)
def test_stage_world_writable_source_dir_is_rejected(tmp_path: Path) -> None:
    """A group/world-writable source directory fails closed."""
    producer, run_root, worker_root = _roots(tmp_path)
    shared = os.path.join(producer, "shared")
    os.makedirs(shared, mode=0o777, exist_ok=True)
    os.chmod(shared, 0o777)
    source = _write_producer(shared, "input.bin", _DATA)
    with pytest.raises(StagingError):
        stage_input_bundle(
            manifest=_manifest(),
            run_root=run_root,
            worker_root=worker_root,
            launch_token="tok-shared-src",
            source_files={_ARTIFACT_ID: source},
        )
    assert _files_under(worker_root) == []


def test_stage_missing_source_mapping_is_rejected(tmp_path: Path) -> None:
    """An artifact entry without a producer source fails closed."""
    _, run_root, worker_root = _roots(tmp_path)
    with pytest.raises(StagingError):
        stage_input_bundle(
            manifest=_manifest(),
            run_root=run_root,
            worker_root=worker_root,
            launch_token="tok-no-source",
            source_files={},
        )
    assert _files_under(worker_root) == []


def test_stage_second_call_fails_after_source_changes(tmp_path: Path) -> None:
    """Bytes changing between two stages fail the second identical stage."""
    producer, run_root, worker_root = _roots(tmp_path)
    first = b"version-one-byteAA"
    second = b"version-two-byteBB"
    assert len(first) == len(second)
    source = _write_producer(producer, "input.bin", first)
    manifest = _manifest(first)
    stage_input_bundle(
        manifest=manifest,
        run_root=run_root,
        worker_root=worker_root,
        launch_token="tok-stable",
        source_files={_ARTIFACT_ID: source},
    )
    Path(source).write_bytes(second)
    with pytest.raises(StagingError):
        stage_input_bundle(
            manifest=manifest,
            run_root=run_root,
            worker_root=worker_root,
            launch_token="tok-stable-again",
            source_files={_ARTIFACT_ID: source},
        )


def test_temp_stability_recheck_detects_replacement(tmp_path: Path) -> None:
    """The dev/ino helper passes stable files and rejects swapped ones."""
    directory = tmp_path / "anchor"
    directory.mkdir(mode=0o700)
    owner = os.getuid()
    nofollow = getattr(os, "O_NOFOLLOW", 0)
    parent_fd = os.open(str(directory), os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        name = ".confflow-stage-probe"
        probe_fd = os.open(
            name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | nofollow, 0o600, dir_fd=parent_fd
        )
        try:
            os.write(probe_fd, b"probe")
            _verify_temp_stability(probe_fd, name, parent_fd, owner)
            os.unlink(name, dir_fd=parent_fd)
            replacement_fd = os.open(
                name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | nofollow, 0o600, dir_fd=parent_fd
            )
            try:
                os.write(replacement_fd, b"other")
            finally:
                os.close(replacement_fd)
            with pytest.raises(StagingError):
                _verify_temp_stability(probe_fd, name, parent_fd, owner)
        finally:
            os.close(probe_fd)
    finally:
        os.close(parent_fd)


# ----------------------------------------------------------------------
# import_result_artifacts
# ----------------------------------------------------------------------


def test_import_happy_path_replaces_artifacts(tmp_path: Path) -> None:
    """Worker bytes become run-relative refs; payload content is preserved."""
    _, run_root, worker_root = _roots(tmp_path)
    data = b"worker-produced-checkpoint-bytes"
    payload = _completed_payload()
    bundle = _result_bundle(payload, data)
    result_path = _write_result_tree(worker_root, "tok-h1", bundle, {"0001-out.chk": data})
    handoff = _handoff(_manifest(), token="tok-h1")

    imported = import_result_artifacts(
        result_path=result_path,
        handoff=handoff,
        run_root=run_root,
        store=_FakeStore((1,)),
    )

    assert isinstance(imported, WorkItemResult)
    assert imported.work_item_id == _WORK_ITEM_ID
    assert imported.status is WorkItemStatus.COMPLETED
    assert imported.structures.ids == (_STRUCTURE_ID,)
    assert [item.kind for item in imported.results] == ["energy"]
    assert len(imported.artifacts) == 1
    ref = imported.artifacts[0]
    assert ref.id == "art-out-1"
    assert ref.role == "checkpoint"
    assert ref.locator.path == "steps/s_opt/remote/wi_s_opt_k1/0001-out.chk"
    assert ref.checksum == _sha256(data)
    assert ref.subject_structure_id == _STRUCTURE_ID
    assert ref.media_type == "application/octet-stream"
    assert ref.program == "g16"
    assert ref.producer_step_id == _STEP_ID
    assert ref.producer_work_item_id == _WORK_ITEM_ID
    assert ref.retention is RetentionClass.RETAINED
    stored = Path(run_root) / str(ref.locator.path)
    assert stored.read_bytes() == data
    assert stat.S_IMODE(stored.lstat().st_mode) == 0o600


@pytest.mark.parametrize("numbers", [(2,), ()])
def test_import_wrong_attempt_is_rejected(tmp_path: Path, numbers: tuple[int, ...]) -> None:
    """Stale and unknown attempts never import."""
    _, run_root, worker_root = _roots(tmp_path)
    data = b"worker-produced-checkpoint-bytes"
    bundle = _result_bundle(_completed_payload(), data)
    result_path = _write_result_tree(worker_root, "tok-h1", bundle, {"0001-out.chk": data})
    handoff = _handoff(_manifest(), token="tok-h1")
    with pytest.raises(StagingError):
        import_result_artifacts(
            result_path=result_path,
            handoff=handoff,
            run_root=run_root,
            store=_FakeStore(numbers),
        )


def test_import_tampered_bytes_are_rejected(tmp_path: Path) -> None:
    """Worker bytes that drift from the announced checksum fail closed."""
    _, run_root, worker_root = _roots(tmp_path)
    data = b"worker-produced-checkpoint-bytes"
    bundle = _result_bundle(_completed_payload(), data)
    result_path = _write_result_tree(worker_root, "tok-h1", bundle, {"0001-out.chk": b"tampered!!"})
    handoff = _handoff(_manifest(), token="tok-h1")
    with pytest.raises(StagingError):
        import_result_artifacts(
            result_path=result_path,
            handoff=handoff,
            run_root=run_root,
            store=_FakeStore((1,)),
        )


def test_import_wrong_size_is_rejected(tmp_path: Path) -> None:
    """Matching bytes with a wrong announced size fail closed."""
    _, run_root, worker_root = _roots(tmp_path)
    data = b"worker-produced-checkpoint-bytes"
    bundle = _result_bundle(_completed_payload(), data, size=len(data) + 1)
    result_path = _write_result_tree(worker_root, "tok-h1", bundle, {"0001-out.chk": data})
    handoff = _handoff(_manifest(), token="tok-h1")
    with pytest.raises(StagingError):
        import_result_artifacts(
            result_path=result_path,
            handoff=handoff,
            run_root=run_root,
            store=_FakeStore((1,)),
        )


def test_import_wrong_launch_token_is_rejected(tmp_path: Path) -> None:
    """A bundle answering a different launch token fails closed."""
    _, run_root, worker_root = _roots(tmp_path)
    data = b"worker-produced-checkpoint-bytes"
    bundle = _result_bundle(_completed_payload(), data, token="tok-h1")
    result_path = _write_result_tree(worker_root, "tok-h1", bundle, {"0001-out.chk": data})
    handoff = _handoff(_manifest(), token="tok-other")
    with pytest.raises(StagingError):
        import_result_artifacts(
            result_path=result_path,
            handoff=handoff,
            run_root=run_root,
            store=_FakeStore((1,)),
        )


def test_import_wrong_digest_is_rejected(tmp_path: Path) -> None:
    """A bundle answering a different work-item digest fails closed."""
    _, run_root, worker_root = _roots(tmp_path)
    data = b"worker-produced-checkpoint-bytes"
    bundle = _result_bundle(_completed_payload(), data, digest=_DIGEST_A)
    result_path = _write_result_tree(worker_root, "tok-h1", bundle, {"0001-out.chk": data})
    handoff = _handoff(_manifest(), token="tok-h1", digest=_DIGEST_B)
    with pytest.raises(StagingError):
        import_result_artifacts(
            result_path=result_path,
            handoff=handoff,
            run_root=run_root,
            store=_FakeStore((1,)),
        )


def test_import_traversal_worker_locator_is_rejected(tmp_path: Path) -> None:
    """A produced locator escaping the result directory fails closed."""
    _, run_root, worker_root = _roots(tmp_path)
    data = b"worker-produced-checkpoint-bytes"
    payload = _completed_payload()
    announced = ResultProducedArtifact(
        artifact_id="art-out-1",
        role="checkpoint",
        checksum=_sha256(data),
        size_bytes=len(data),
        bundle_locator="../evil.chk",
    )
    bundle = ResultBundle.new(
        schema="confflow.control.worker-result.v2",
        protocol_version="v2",
        run_id="run-1",
        step_id=_STEP_ID,
        work_item_id=_WORK_ITEM_ID,
        attempt_number=1,
        launch_token="tok-h1",
        work_item_digest=_DIGEST_A,
        environment={"program": "g16"},
        result=payload,
        produced_artifacts=(announced.model_dump(mode="json"),),
    )
    result_path = _write_result_tree(worker_root, "tok-h1", bundle, {"0001-out.chk": data})
    handoff = _handoff(_manifest(), token="tok-h1")
    with pytest.raises(StagingError):
        import_result_artifacts(
            result_path=result_path,
            handoff=handoff,
            run_root=run_root,
            store=_FakeStore((1,)),
        )
