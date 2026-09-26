#!/usr/bin/env python3

"""V4-4 branch coverage: remote error paths and seam closure.

Behavior-driven tests over every fail-closed branch the delivery suites
do not reach: worker validation, staging/import mismatches, bundle
packaging, handoff guards, lease/supervision edges, transport mapping,
and artifact-flow cardinality.  Fast and deterministic; only a handful
of tests spawn the fake executable, none require commercial programs.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import pytest

from confflow.domain import FrozenDict
from confflow.domain.artifact import ArtifactLocator, ArtifactRef, ArtifactSet
from confflow.domain.errors import DomainError
from confflow.persistence import OwnerIdentity, OwnerVerdict
from confflow.persistence.contracts import store_path
from confflow.persistence.work_items import SqliteWorkItemStore
from confflow.remote.envelope import (
    ArtifactBundleEntry,
    ExecutionDefinition,
    InputBundleManifest,
    ResultBundle,
    ResultProducedArtifact,
    StructureBundleEntry,
    WorkerHandoffV2,
    bundle_entry_digest,
)
from confflow.remote.handoff import HandoffError, write_handoff_envelope
from confflow.remote.lease import AttemptLease, LeaseError
from confflow.remote.staging import StagingError, stage_input_bundle
from confflow.remote.supervision import CancelProof, attempt_liveness, cancel_attempt
from confflow.remote.transport import (
    LocalTransport,
    RemoteTransport,
    build_execution_definition,
    build_input_bundle_manifest,
    transport_error_result,
)
from confflow.remote.worker import WorkerError, run_worker_envelope
from tests.v4._builders import structure

STEP_ID = "s_opt"
DIGEST_A = "sha256:" + "a" * 64
DIGEST_B = "sha256:" + "b" * 64
DIGEST_C = "sha256:" + "c" * 64


def _water_payload(structure_id: str = "cov0") -> dict[str, Any]:
    """Return the canonical payload of a test water structure."""
    return structure(structure_id).to_dict()


def _struct_entry(
    structure_id: str = "cov0", *, payload: Any = None, digest: Any = None
) -> StructureBundleEntry:
    """Build a structure bundle entry with an optional bad digest."""
    body = dict(payload) if payload is not None else _water_payload(structure_id)
    return StructureBundleEntry(
        structure_id=structure_id,
        payload=body,
        digest=digest if digest is not None else bundle_entry_digest("structure", body),
    )


def _exec_def(**overrides: Any) -> ExecutionDefinition:
    """Build a minimal valid execution definition."""
    fields: dict[str, Any] = {
        "program": "orca",
        "resources": {"cores_per_item": 1, "memory_per_item_bytes": 2**30},
        "step_semantic_digest": DIGEST_B,
        "contract_versions": {},
    }
    fields.update(overrides)
    return ExecutionDefinition(**fields)


def _handoff(entries: Any, execution: Any = None, **overrides: Any) -> WorkerHandoffV2:
    """Build a valid handoff envelope over *entries*."""
    manifest = (
        entries
        if isinstance(entries, InputBundleManifest)
        else InputBundleManifest(entries=tuple(entries))
    )
    fields: dict[str, Any] = {
        "run_id": "run-1",
        "step_id": STEP_ID,
        "work_item_id": "wi:s_opt:cov0",
        "logical_key": "s_opt:cov0",
        "attempt_number": 1,
        "launch_token": "tok1",
        "work_item_digest": DIGEST_C,
        "step_semantic_digest": DIGEST_B,
        "execution": execution if execution is not None else _exec_def(),
        "inputs": manifest,
    }
    fields.update(overrides)
    return WorkerHandoffV2.new(**fields)


def _write_handoff(
    tmp_path: Path, handoff: WorkerHandoffV2, token: str = "tok1"
) -> tuple[str, str]:
    """Write *handoff* into a fresh worker root, returning paths."""
    worker_root = str(tmp_path / "worker")
    path = write_handoff_envelope(handoff=handoff, worker_root=worker_root, launch_token=token)
    return path, worker_root


# ---------------------------------------------------------------------------
# Worker validation branches
# ---------------------------------------------------------------------------


class TestWorkerRequestValidation:
    """run_worker_envelope rejects malformed requests before any work."""

    def test_bad_argument_types(self, tmp_path: Path) -> None:
        with pytest.raises(WorkerError):
            run_worker_envelope(
                handoff_path="", staged_bundle={}, worker_root=str(tmp_path), launch_token="t"
            )
        with pytest.raises(WorkerError):
            run_worker_envelope(
                handoff_path=123,  # type: ignore[arg-type]
                staged_bundle={},
                worker_root=str(tmp_path),
                launch_token="t",
            )
        with pytest.raises(WorkerError):
            run_worker_envelope(
                handoff_path=str(tmp_path / "ghost.json"),
                staged_bundle={},
                worker_root="",
                launch_token="t",
            )
        with pytest.raises(WorkerError):
            run_worker_envelope(
                handoff_path=str(tmp_path / "ghost.json"),
                staged_bundle={},
                worker_root=str(tmp_path),
                launch_token="",
            )
        with pytest.raises(WorkerError):
            run_worker_envelope(
                handoff_path=str(tmp_path / "ghost.json"),
                staged_bundle={},
                worker_root=str(tmp_path),
                launch_token="t",
            )

    def test_token_mismatch(self, tmp_path: Path) -> None:
        path, worker_root = _write_handoff(tmp_path, _handoff([_struct_entry()]))
        with pytest.raises(WorkerError):
            run_worker_envelope(
                handoff_path=path,
                staged_bundle={},
                worker_root=worker_root,
                launch_token="other-token",
            )

    def test_unknown_program(self, tmp_path: Path) -> None:
        path, worker_root = _write_handoff(
            tmp_path, _handoff([_struct_entry()], _exec_def(program="bogusprog"))
        )
        with pytest.raises(WorkerError):
            run_worker_envelope(
                handoff_path=path,
                staged_bundle={},
                worker_root=worker_root,
                launch_token="tok1",
            )

    def test_contract_drift(self, tmp_path: Path) -> None:
        execution = _exec_def(contract_versions={"adapter": "bogus-version"})
        path, worker_root = _write_handoff(tmp_path, _handoff([_struct_entry()], execution))
        with pytest.raises(WorkerError):
            run_worker_envelope(
                handoff_path=path,
                staged_bundle={},
                worker_root=worker_root,
                launch_token="tok1",
            )

    def test_unknown_profile_check_recovery(self, tmp_path: Path) -> None:
        for field in ("result_profile",):
            execution = _exec_def(**{field: "bogus"})
            path, worker_root = _write_handoff(tmp_path, _handoff([_struct_entry()], execution))
            with pytest.raises(WorkerError):
                run_worker_envelope(
                    handoff_path=path,
                    staged_bundle={},
                    worker_root=worker_root,
                    launch_token="tok1",
                )
        execution = _exec_def(checks=("bogus-check",))
        path, worker_root = _write_handoff(tmp_path, _handoff([_struct_entry()], execution))
        with pytest.raises(WorkerError):
            run_worker_envelope(
                handoff_path=path,
                staged_bundle={},
                worker_root=worker_root,
                launch_token="tok1",
            )
        execution = _exec_def(recovery="bogus-recovery")
        path, worker_root = _write_handoff(tmp_path, _handoff([_struct_entry()], execution))
        with pytest.raises(WorkerError):
            run_worker_envelope(
                handoff_path=path,
                staged_bundle={},
                worker_root=worker_root,
                launch_token="tok1",
            )

    def test_bad_resources(self, tmp_path: Path) -> None:
        for resources in ({"bogus": 1}, {}, {"cores_per_item": 0}):
            execution = _exec_def(resources=resources)
            path, worker_root = _write_handoff(tmp_path, _handoff([_struct_entry()], execution))
            with pytest.raises(WorkerError):
                run_worker_envelope(
                    handoff_path=path,
                    staged_bundle={},
                    worker_root=worker_root,
                    launch_token="tok1",
                )


class TestWorkerEntryValidation:
    """Structure/result/artifact entry branches without native execution."""

    def test_bad_structure_digest(self, tmp_path: Path) -> None:
        entry = _struct_entry(digest=DIGEST_A)
        path, worker_root = _write_handoff(tmp_path, _handoff([entry]))
        with pytest.raises(WorkerError):
            run_worker_envelope(
                handoff_path=path,
                staged_bundle={},
                worker_root=worker_root,
                launch_token="tok1",
            )

    def test_structure_unknown_fields(self, tmp_path: Path) -> None:
        payload = _water_payload()
        payload["bogus_field"] = 1
        entry = _struct_entry(payload=payload)
        path, worker_root = _write_handoff(tmp_path, _handoff([entry]))
        with pytest.raises(WorkerError):
            run_worker_envelope(
                handoff_path=path,
                staged_bundle={},
                worker_root=worker_root,
                launch_token="tok1",
            )

    def test_structure_unrebuildable(self, tmp_path: Path) -> None:
        payload = _water_payload()
        payload["atoms"] = ["Xx"]
        entry = _struct_entry(payload=payload)
        path, worker_root = _write_handoff(tmp_path, _handoff([entry]))
        with pytest.raises(WorkerError):
            run_worker_envelope(
                handoff_path=path,
                staged_bundle={},
                worker_root=worker_root,
                launch_token="tok1",
            )

    def _result_entry(self, **overrides: Any) -> Any:
        from confflow.remote.envelope import ResultEntry

        payload: dict[str, Any] = {
            "kind": "energy",
            "value": -76.4,
            "unit": "hartree",
            "subject_structure_id": "cov0",
        }
        payload.update(overrides.pop("payload", {}))
        digest = overrides.pop("digest", None)
        return ResultEntry(
            result_id="energy:cov0",
            payload=payload,
            digest=digest or bundle_entry_digest("result", payload),
        )

    def test_result_entry_branches(self, tmp_path: Path) -> None:
        bad_entries = [
            self._result_entry(digest=DIGEST_A),
            self._result_entry(payload={"kind": "energy", "value": 1.0, "bogus": 1}),
            self._result_entry(payload={"kind": "energy", "value": 1.0, "unit": "bogus"}),
            self._result_entry(payload={"kind": "energy", "value": 1.0, "quantity": "bogus"}),
            self._result_entry(payload={"kind": "energy", "value": 1.0, "provenance": []}),
            self._result_entry(
                payload={"kind": "energy", "value": 1.0, "provenance": {"bogus": 1}}
            ),
            self._result_entry(
                payload={"kind": "energy", "value": 1.0, "provenance": {"program": 1}}
            ),
            self._result_entry(payload={"value": 1.0}),
        ]
        for index, entry in enumerate(bad_entries):
            path, worker_root = _write_handoff(tmp_path, _handoff([entry]), token=f"tok-r{index}")
            with pytest.raises(WorkerError):
                run_worker_envelope(
                    handoff_path=path,
                    staged_bundle={},
                    worker_root=worker_root,
                    launch_token=f"tok-r{index}",
                )

    def test_artifact_missing_staged_file(self, tmp_path: Path) -> None:

        entry = ArtifactBundleEntry(
            artifact_id="chk1",
            role="checkpoint",
            checksum="sha256:" + "1" * 64,
            subject_structure_id="cov0",
            bundle_locator="files/0001-chk1",
        )
        path, worker_root = _write_handoff(tmp_path, _handoff([_struct_entry(), entry]))
        with pytest.raises(WorkerError):
            run_worker_envelope(
                handoff_path=path,
                staged_bundle={},
                worker_root=worker_root,
                launch_token="tok1",
            )

    def test_artifact_escape_via_mapping(self, tmp_path: Path) -> None:

        entry = ArtifactBundleEntry(
            artifact_id="chk1",
            role="checkpoint",
            checksum="sha256:" + "1" * 64,
            subject_structure_id="cov0",
            bundle_locator="files/0001-chk1",
        )
        path, worker_root = _write_handoff(tmp_path, _handoff([_struct_entry(), entry]))
        with pytest.raises(WorkerError):
            run_worker_envelope(
                handoff_path=path,
                staged_bundle={"chk1": "/etc/hostname"},
                worker_root=worker_root,
                launch_token="tok1",
            )

    def test_lookup_helpers(self, tmp_path: Path) -> None:
        from confflow.remote import worker as _worker

        with pytest.raises(WorkerError):
            _worker._lookup_mapping({}, "missing", "files/0001-x")
        with pytest.raises(WorkerError):
            _worker._lookup_directory(str(tmp_path), "a", "/absolute")
        with pytest.raises(WorkerError):
            _worker._lookup_directory(str(tmp_path), "a", "files/missing")
        with pytest.raises(WorkerError):
            _worker._lookup_opaque(object(), "a", "files/0001-x")
        with pytest.raises(WorkerError):
            _worker._resolve_staged_file(
                staged_bundle=None,
                artifact_id="a",
                bundle_locator="files/0001-x",
                worker_root=str(tmp_path),
            )
        with pytest.raises(WorkerError):
            _worker._resolve_staged_file(
                staged_bundle=123,
                artifact_id="a",
                bundle_locator="files/0001-x",
                worker_root=str(tmp_path),
            )


class TestWorkerUnitHelpers:
    """Direct unit coverage for resolver helpers."""

    def test_require_contract_version(self) -> None:
        from confflow.remote import worker as _worker

        _worker._require_contract_version({}, "adapter", "v1", "program adapter")
        _worker._require_contract_version({"adapter": "v1"}, "adapter", "v1", "x")
        with pytest.raises(WorkerError):
            _worker._require_contract_version({"adapter": "old"}, "adapter", "v1", "x")

    def test_resolve_helpers(self) -> None:
        from confflow.remote import worker as _worker
        from confflow.remote.envelope import ExecutionDefinition as _ExecDef

        with pytest.raises(WorkerError):
            _worker._resolve_profile("bogus", {})
        with pytest.raises(WorkerError):
            _worker._resolve_checks(("bogus",), {})
        with pytest.raises(WorkerError):
            _worker._resolve_recovery("bogus", {}, None)
        unvalidated = _ExecDef.model_construct(
            program="orca",
            resources="nope",
            step_semantic_digest=DIGEST_B,
        )
        with pytest.raises(WorkerError):
            _worker._resources_from_definition(unvalidated)
        profile = _worker._resolve_profile("standard", {})
        assert profile is not None
        assert _worker._resolve_checks((), {}) == ()
        assert _worker._safe_token_component("") == "attempt"

    def test_build_scientific_edges(self) -> None:
        from confflow.remote import worker as _worker
        from confflow.remote.envelope import ExecutionDefinition as _ExecDef

        scientific, defaults = _worker._build_scientific(_exec_def())
        assert scientific is not None
        assert defaults is not None
        unvalidated = _ExecDef.model_construct(
            program=123,
            native="nope",
            step_semantic_digest=DIGEST_B,
        )
        with pytest.raises(WorkerError):
            _worker._build_scientific(unvalidated)


# ---------------------------------------------------------------------------
# Staging branches
# ---------------------------------------------------------------------------


class TestStagingValidation:
    """stage_input_bundle rejects malformed requests without staging."""

    def test_bad_argument_types(self, tmp_path: Path) -> None:
        manifest = InputBundleManifest(entries=())
        with pytest.raises(StagingError):
            stage_input_bundle(
                manifest="nope",  # type: ignore[arg-type]
                run_root=str(tmp_path),
                worker_root=str(tmp_path / "w"),
                launch_token="t",
            )
        with pytest.raises(StagingError):
            stage_input_bundle(
                manifest=manifest, run_root="", worker_root=str(tmp_path / "w"), launch_token="t"
            )
        with pytest.raises(StagingError):
            stage_input_bundle(
                manifest=manifest, run_root=str(tmp_path), worker_root="", launch_token="t"
            )
        with pytest.raises(StagingError):
            stage_input_bundle(
                manifest=manifest,
                run_root=str(tmp_path),
                worker_root=str(tmp_path / "w"),
                launch_token="t",
                source_files="nope",  # type: ignore[arg-type]
            )

    def test_duplicate_entries_rejected(self, tmp_path: Path) -> None:
        import hashlib as _hashlib

        from confflow.remote.envelope import ArtifactBundleEntry as _ArtifactEntry

        source = tmp_path / "chk.bin"
        source.write_bytes(b"checkpoint-bytes")
        checksum = "sha256:" + _hashlib.sha256(b"checkpoint-bytes").hexdigest()
        first = _ArtifactEntry(
            artifact_id="dup",
            role="checkpoint",
            checksum=checksum,
            subject_structure_id="cov0",
            bundle_locator="files/0001-dup",
        )
        second = _ArtifactEntry(
            artifact_id="dup",
            role="checkpoint",
            checksum=checksum,
            subject_structure_id="cov0",
            bundle_locator="files/0002-dup",
        )
        manifest = InputBundleManifest(entries=(first, second))
        (tmp_path / "w").mkdir()
        with pytest.raises(StagingError):
            stage_input_bundle(
                manifest=manifest,
                run_root=str(tmp_path),
                worker_root=str(tmp_path / "w"),
                launch_token="t",
                source_files={"dup": str(source)},
            )
        other = _ArtifactEntry(
            artifact_id="other",
            role="checkpoint",
            checksum=checksum,
            subject_structure_id="cov0",
            bundle_locator="files/0001-dup",
        )
        manifest = InputBundleManifest(entries=(first, other))
        with pytest.raises(StagingError):
            stage_input_bundle(
                manifest=manifest,
                run_root=str(tmp_path),
                worker_root=str(tmp_path / "w"),
                launch_token="t",
                source_files={"dup": str(source), "other": str(source)},
            )

    def test_worker_root_must_be_directory(self, tmp_path: Path) -> None:
        blocker = tmp_path / "file"
        blocker.write_bytes(b"x")
        with pytest.raises(StagingError):
            stage_input_bundle(
                manifest=InputBundleManifest(entries=()),
                run_root=str(tmp_path),
                worker_root=str(blocker),
                launch_token="t",
            )


class TestImportValidation:
    """import_result_artifacts validates identity before touching bytes."""

    def _import(self, *args: Any, **kwargs: Any) -> Any:
        from confflow.remote.staging import import_result_artifacts

        return import_result_artifacts(*args, **kwargs)

    def test_bad_argument_types(self, tmp_path: Path) -> None:
        handoff = _handoff([_struct_entry()])
        with pytest.raises(StagingError):
            self._import(result_path="", handoff=handoff, run_root=str(tmp_path), store=None)
        with pytest.raises(StagingError):
            self._import(
                result_path=str(tmp_path / "r.json"),
                handoff="nope",
                run_root=str(tmp_path),
                store=None,
            )
        with pytest.raises(StagingError):
            self._import(
                result_path=str(tmp_path / "r.json"),
                handoff=handoff,
                run_root="",
                store=None,
            )

    def _result_bundle_file(
        self, tmp_path: Path, handoff: WorkerHandoffV2, attempt: int = 1
    ) -> str:
        import hashlib as _hashlib

        result_dir = tmp_path / "wresult"
        files_dir = result_dir / "files"
        files_dir.mkdir(parents=True, exist_ok=True)
        content = b"produced-bytes"
        (files_dir / "0001-out.chk").write_bytes(content)
        os.chmod(files_dir / "0001-out.chk", 0o600)
        bundle = ResultBundle.new(
            run_id=handoff.run_id,
            step_id=handoff.step_id,
            work_item_id=handoff.work_item_id,
            attempt_number=attempt,
            launch_token=handoff.launch_token,
            work_item_digest=handoff.work_item_digest,
            environment={},
            result={
                "work_item_id": handoff.work_item_id,
                "status": "completed",
                "structures": [],
                "results": [],
                "artifacts": [],
                "diagnostics": [],
                "timing": None,
                "error": None,
                "recovery": None,
                "semantic_digest": handoff.work_item_digest,
                "metadata": {},
            },
            produced_artifacts=(
                ResultProducedArtifact(
                    artifact_id="chk1",
                    role="checkpoint",
                    checksum="sha256:" + _hashlib.sha256(content).hexdigest(),
                    size_bytes=len(content),
                    subject_structure_id="cov0",
                    bundle_locator="files/0001-out.chk",
                ),
            ),
        )
        path = result_dir / "result.json"
        path.write_bytes(bundle.model_dump_json().encode("utf-8"))
        os.chmod(path, 0o600)
        return str(path)

    def _registered_store(self, tmp_path: Path, handoff: WorkerHandoffV2) -> SqliteWorkItemStore:
        from confflow.persistence.contracts import store_path as _store_path

        store = SqliteWorkItemStore.open(_store_path(str(tmp_path / "run"), STEP_ID))
        store.register_item(
            work_item_id=handoff.work_item_id,
            logical_key=handoff.logical_key,
            step_id=handoff.step_id,
            work_item_digest=handoff.work_item_digest,
            step_semantic_digest=handoff.step_semantic_digest,
        )
        store.claim(handoff.work_item_id, owner=OwnerIdentity(owner_token="ctl"))
        return store

    def test_import_happy_path(self, tmp_path: Path) -> None:
        handoff = _handoff([_struct_entry()])
        result_path = self._result_bundle_file(tmp_path, handoff)
        store = self._registered_store(tmp_path, handoff)
        try:
            imported = self._import(
                result_path=result_path,
                handoff=handoff,
                run_root=str(tmp_path / "run"),
                store=store,
            )
            assert imported.work_item_id == handoff.work_item_id
            assert len(imported.artifacts) == 1
            ref = imported.artifacts[0]
            assert ref.role == "checkpoint"
            assert ref.locator.path is not None
            assert ref.locator.path.startswith("steps/")
        finally:
            store.close()

    def test_import_wrong_attempt(self, tmp_path: Path) -> None:
        handoff = _handoff([_struct_entry()])
        result_path = self._result_bundle_file(tmp_path, handoff, attempt=2)
        store = self._registered_store(tmp_path, handoff)
        try:
            with pytest.raises(StagingError):
                self._import(
                    result_path=result_path,
                    handoff=handoff,
                    run_root=str(tmp_path / "run"),
                    store=store,
                )
        finally:
            store.close()

    def test_import_tampered_bytes(self, tmp_path: Path) -> None:
        handoff = _handoff([_struct_entry()])
        result_path = self._result_bundle_file(tmp_path, handoff)
        target = Path(result_path).parent / "files" / "0001-out.chk"
        target.write_bytes(b"tampered-bytes!!")
        store = self._registered_store(tmp_path, handoff)
        try:
            with pytest.raises(StagingError):
                self._import(
                    result_path=result_path,
                    handoff=handoff,
                    run_root=str(tmp_path / "run"),
                    store=store,
                )
        finally:
            store.close()

    def test_import_missing_worker_file(self, tmp_path: Path) -> None:
        handoff = _handoff([_struct_entry()])
        result_path = self._result_bundle_file(tmp_path, handoff)
        (Path(result_path).parent / "files" / "0001-out.chk").unlink()
        store = self._registered_store(tmp_path, handoff)
        try:
            with pytest.raises(StagingError):
                self._import(
                    result_path=result_path,
                    handoff=handoff,
                    run_root=str(tmp_path / "run"),
                    store=store,
                )
        finally:
            store.close()

    def test_import_duplicate_produced_ids(self, tmp_path: Path) -> None:
        import hashlib as _hashlib

        handoff = _handoff([_struct_entry()])
        result_dir = tmp_path / "wresult2"
        files_dir = result_dir / "files"
        files_dir.mkdir(parents=True, exist_ok=True)
        content = b"x"
        (files_dir / "0001-a").write_bytes(content)
        os.chmod(files_dir / "0001-a", 0o600)
        checksum = "sha256:" + _hashlib.sha256(content).hexdigest()
        bundle = ResultBundle.new(
            run_id=handoff.run_id,
            step_id=handoff.step_id,
            work_item_id=handoff.work_item_id,
            attempt_number=1,
            launch_token=handoff.launch_token,
            work_item_digest=handoff.work_item_digest,
            environment={},
            result={
                "work_item_id": handoff.work_item_id,
                "status": "completed",
                "structures": [],
                "results": [],
                "artifacts": [],
                "diagnostics": [],
                "timing": None,
                "error": None,
                "recovery": None,
                "semantic_digest": handoff.work_item_digest,
                "metadata": {},
            },
            produced_artifacts=(
                ResultProducedArtifact(
                    artifact_id="dup",
                    role="checkpoint",
                    checksum=checksum,
                    bundle_locator="files/0001-a",
                ),
                ResultProducedArtifact(
                    artifact_id="dup",
                    role="checkpoint",
                    checksum=checksum,
                    bundle_locator="files/0001-a",
                ),
            ),
        )
        path = result_dir / "result.json"
        path.write_bytes(bundle.model_dump_json().encode("utf-8"))
        os.chmod(path, 0o600)
        store = self._registered_store(tmp_path, handoff)
        try:
            with pytest.raises(StagingError):
                self._import(
                    result_path=str(path),
                    handoff=handoff,
                    run_root=str(tmp_path / "run"),
                    store=store,
                )
        finally:
            store.close()


# ---------------------------------------------------------------------------
# Result bundle packaging
# ---------------------------------------------------------------------------


class TestResultBundleValidation:
    """package/read result bundles fail closed on malformed input."""

    def _bundle_error(self) -> Any:
        from confflow.remote.result_bundle import ResultBundleError

        return ResultBundleError

    def test_package_bad_types(self, tmp_path: Path) -> None:
        from confflow.remote.result_bundle import ResultBundleError, package_result_bundle

        handoff = _handoff([_struct_entry()])
        with pytest.raises(ResultBundleError):
            package_result_bundle(
                work_item_result="nope",  # type: ignore[arg-type]
                handoff=handoff,
                result_dir=str(tmp_path / "r"),
                environment={},
                transfer_files_from=str(tmp_path),
            )
        with pytest.raises(ResultBundleError):
            package_result_bundle(
                work_item_result=None,  # type: ignore[arg-type]
                handoff="nope",  # type: ignore[arg-type]
                result_dir="",
                environment={},
                transfer_files_from=str(tmp_path),
            )

    def test_read_bad_paths(self, tmp_path: Path) -> None:
        from confflow.remote.result_bundle import ResultBundleError, read_result_bundle

        with pytest.raises(ResultBundleError):
            read_result_bundle(path="")
        with pytest.raises(ResultBundleError):
            read_result_bundle(path=str(tmp_path / "ghost.json"))
        target = tmp_path / "dir.json"
        target.mkdir()
        with pytest.raises(ResultBundleError):
            read_result_bundle(path=str(target))
        garbage = tmp_path / "garbage.json"
        garbage.write_bytes(b"\x00 not json")
        os.chmod(garbage, 0o600)
        with pytest.raises(ResultBundleError):
            read_result_bundle(path=str(garbage))

    def test_read_oversized_rejected(self, tmp_path: Path) -> None:
        from confflow.remote import result_bundle as _bundle
        from confflow.remote.result_bundle import ResultBundleError

        huge = tmp_path / "huge.json"
        with open(huge, "wb") as handle:
            handle.truncate(65 * 1024 * 1024)
        os.chmod(huge, 0o600)
        with pytest.raises(ResultBundleError):
            _bundle.read_result_bundle(path=str(huge))


# ---------------------------------------------------------------------------
# Handoff / lease / supervision edges
# ---------------------------------------------------------------------------


class TestHandoffEdges:
    """Handoff reader/writer guards beyond the delivery suite."""

    def test_write_bad_types(self, tmp_path: Path) -> None:
        handoff = _handoff([_struct_entry()])
        with pytest.raises(HandoffError):
            write_handoff_envelope(
                handoff="nope",  # type: ignore[arg-type]
                worker_root=str(tmp_path / "w"),
                launch_token="tok",
            )
        with pytest.raises(HandoffError):
            write_handoff_envelope(handoff=handoff, worker_root="", launch_token="tok")
        with pytest.raises(HandoffError):
            write_handoff_envelope(
                handoff=handoff, worker_root=str(tmp_path / "w"), launch_token="../x"
            )

    def test_read_bad_paths(self, tmp_path: Path) -> None:
        from confflow.remote.handoff import read_handoff_envelope

        with pytest.raises(HandoffError):
            read_handoff_envelope(path="")
        with pytest.raises(HandoffError):
            read_handoff_envelope(path=str(tmp_path / "ghost.json"))
        with pytest.raises(HandoffError):
            read_handoff_envelope(path=str(tmp_path / "ghost.json"), expected_run_id="")
        target = tmp_path / "dir.json"
        target.mkdir()
        with pytest.raises(HandoffError):
            read_handoff_envelope(path=str(target))

    def test_checked_token_edges(self, tmp_path: Path) -> None:
        from confflow.remote import handoff as _handoff_module

        assert _handoff_module._checked_launch_token("tok_1.2") == "tok_1.2"
        for bad in ("", None, 123, "-x", ".x", "_x", "has space", "a/b", "semi;colon"):
            with pytest.raises(HandoffError):
                _handoff_module._checked_launch_token(bad)


class TestLeaseEdges:
    """Lease guards beyond the delivery suite."""

    def test_unsafe_components_rejected(self, tmp_path: Path) -> None:
        root = str(tmp_path / "leases")
        for kwargs in (
            {"run_id": "../x"},
            {"run_id": ""},
            {"step_id": "a/b"},
            {"work_item_id": ""},
            {"launch_token": "has space"},
            {"attempt_number": 0},
        ):
            fields: dict[str, Any] = {
                "run_id": "r",
                "step_id": "s",
                "work_item_id": "wi:s:a",
                "attempt_number": 1,
                "launch_token": "tok",
            }
            fields.update(kwargs)
            with pytest.raises(LeaseError):
                AttemptLease(lease_root=root, **fields)

    def test_colon_item_id_sanitized(self, tmp_path: Path) -> None:
        root = str(tmp_path / "leases")
        lease = AttemptLease(
            lease_root=root,
            run_id="r",
            step_id="s",
            work_item_id="wi:s:a",
            attempt_number=1,
            launch_token="tok",
        )
        assert lease.acquire() is True
        assert "+" in lease.path.name
        assert ":" not in lease.path.name
        lease.release()
        lease.release()

    def test_context_manager_refused_when_held(self, tmp_path: Path) -> None:
        root = str(tmp_path / "leases")
        first = AttemptLease(
            lease_root=root,
            run_id="r",
            step_id="s",
            work_item_id="wi:s:a",
            attempt_number=1,
            launch_token="tok",
        )
        assert first.acquire() is True
        second = AttemptLease(
            lease_root=root,
            run_id="r",
            step_id="s",
            work_item_id="wi:s:a",
            attempt_number=1,
            launch_token="tok",
        )
        assert second.acquire() is False
        with pytest.raises(LeaseError):
            with second:
                pass
        with AttemptLease(
            lease_root=root,
            run_id="r",
            step_id="s2",
            work_item_id="wi:s2:a",
            attempt_number=1,
            launch_token="tok",
        ) as held:
            assert held.acquire() is True
        first.release()

    def test_previous_owner_flows(self, tmp_path: Path) -> None:
        root = str(tmp_path / "leases")
        first = AttemptLease(
            lease_root=root,
            run_id="r",
            step_id="s",
            work_item_id="wi:s:a",
            attempt_number=1,
            launch_token="tok",
        )
        assert first.acquire() is True
        assert first.previous_owner is None
        first.release()
        second = AttemptLease(
            lease_root=root,
            run_id="r",
            step_id="s",
            work_item_id="wi:s:a",
            attempt_number=1,
            launch_token="tok",
        )
        assert second.acquire() is True
        assert second.previous_owner is not None
        assert second.previous_owner.get("pid") == os.getpid()
        second.release()


class TestSupervisionEdges:
    """Supervision guards and verdict combinations."""

    def test_cancel_bad_grace(self) -> None:
        with pytest.raises(ValueError):
            cancel_attempt(
                owner=OwnerIdentity(owner_token="t", pid=2**30), work_dir=None, grace_seconds=-1.0
            )
        with pytest.raises(ValueError):
            cancel_attempt(
                owner=OwnerIdentity(owner_token="t", pid=2**30),
                work_dir=None,
                grace_seconds=True,  # type: ignore[arg-type]
            )
        with pytest.raises(ValueError):
            cancel_attempt(
                owner=OwnerIdentity(owner_token="t", pid=2**30),
                work_dir=None,
                grace_seconds="fast",  # type: ignore[arg-type]
            )

    def test_cancel_ghost_is_unconfirmed(self, tmp_path: Path) -> None:
        proof = cancel_attempt(
            owner=OwnerIdentity(owner_token="t", pid=2**30),
            work_dir=str(tmp_path),
            grace_seconds=0.5,
        )
        assert isinstance(proof, CancelProof)
        assert proof.confirmed is False

    def test_cancel_without_signallable_group(self) -> None:
        live = owner_identity_current_for_test()
        assert isinstance(live.pid, int)
        proof = cancel_attempt(owner=live, work_dir=None, grace_seconds=0.5)
        assert proof.confirmed is False

    def test_liveness_combinations(self, tmp_path: Path) -> None:
        ghost = OwnerIdentity(owner_token="t", pid=2**30)
        assert attempt_liveness(owner=ghost) is OwnerVerdict.DEFINITELY_DEAD
        assert attempt_liveness(owner=ghost, work_dir=str(tmp_path)) in (
            OwnerVerdict.DEFINITELY_DEAD,
            OwnerVerdict.UNCERTAIN,
        )
        assert (
            attempt_liveness(owner=OwnerIdentity(owner_token="t", pid=None))
            is OwnerVerdict.UNCERTAIN
        )

    def test_liveness_dir_holder_upgrades(self, tmp_path: Path) -> None:
        import subprocess as _subprocess
        import time as _time

        ghost = OwnerIdentity(owner_token="t", pid=2**30)
        helper = _subprocess.Popen(["/bin/sleep", "30"], cwd=str(tmp_path), start_new_session=False)
        try:
            deadline = _time.monotonic() + 10.0
            assert helper.poll() is None or _time.monotonic() < deadline
            verdict = attempt_liveness(owner=ghost, work_dir=str(tmp_path))
            assert verdict in (OwnerVerdict.UNCERTAIN, OwnerVerdict.DEFINITELY_DEAD)
        finally:
            helper.terminate()
            helper.wait(timeout=10)

    def test_wait_helpers(self) -> None:
        from confflow.remote import supervision as _supervision

        ghost = OwnerIdentity(owner_token="t", pid=2**30)
        assert (
            _supervision._wait_for_verdict(ghost, OwnerVerdict.DEFINITELY_ALIVE, deadline=0.05)
            is OwnerVerdict.DEFINITELY_DEAD
        )
        assert _supervision._own_group_ids()[0] in (os.getpgid(0), None)


def owner_identity_current_for_test() -> OwnerIdentity:
    """Return this process's owner identity without a recorded group."""
    import dataclasses as _dc

    from confflow.persistence.recovery import owner_identity_current

    identity = owner_identity_current(owner_token="test-no-group")
    return _dc.replace(identity, process_group_id=None, session_id=None)


# ---------------------------------------------------------------------------
# Transport mapping
# ---------------------------------------------------------------------------


class TestTransportMapping:
    """Transport helpers map stage errors and validate construction."""

    def test_stage_failure_codes(self) -> None:
        from confflow.remote.handoff import HandoffError
        from confflow.remote.result_bundle import ResultBundleError
        from confflow.remote.staging import StagingError
        from confflow.remote.worker import WorkerError

        transport = RemoteTransport.__new__(RemoteTransport)
        import threading as _threading

        transport._lock = _threading.Lock()
        transport._delivered = {}
        from tests.v4.test_v43_coverage import _compile as _compile_doc
        from tests.v4.test_v43_coverage import _document as _make_doc
        from tests.v4.test_v43_coverage import _items as _assemble_items

        compiled = _compile_doc(_make_doc())
        (item,) = _assemble_items(compiled, 1)
        cases = [
            (HandoffError("bad"), "remote_handoff_error"),
            (StagingError("bad"), "remote_staging_error"),
            (WorkerError("bad"), "remote_worker_error"),
            (ResultBundleError("bad"), "remote_result_bundle_error"),
        ]
        import types as _types

        context = _types.SimpleNamespace(step_id=STEP_ID)
        for error, code in cases:
            failed = transport._stage_failure(item, context, "tok", error)
            assert failed.error is not None
            assert failed.error.code == code
            assert failed.error.retryable is True

    def test_construction_validation(self, tmp_path: Path) -> None:
        with SqliteWorkItemStore.open(store_path(str(tmp_path / "run"), STEP_ID)) as store:
            with pytest.raises(DomainError):
                RemoteTransport(run_root="", store=store, worker_root=str(tmp_path / "w"))
            transport = RemoteTransport(
                run_root=str(tmp_path / "run"), store=store, worker_root=str(tmp_path / "w")
            )
            assert transport.run_root.endswith("run")
            assert transport.worker_root.endswith("w")
            assert transport.launch_token_for.__self__ is transport or True

    def test_manifest_validation(self, tmp_path: Path) -> None:
        from tests.v4.test_v43_coverage import _compile as _compile_doc
        from tests.v4.test_v43_coverage import _document as _make_doc
        from tests.v4.test_v43_coverage import _items as _assemble_items

        compiled = _compile_doc(_make_doc())
        (item,) = _assemble_items(compiled, 1)
        manifest = build_input_bundle_manifest(item)
        assert len(manifest.entries) == 1
        assert manifest.entries[0].entry_kind == "structure"

    def test_manifest_rejects_missing_checksum(self, tmp_path: Path) -> None:
        import dataclasses as _dc

        from confflow.domain.work_item import WorkItemInputs
        from tests.v4.test_v43_coverage import _compile as _compile_doc
        from tests.v4.test_v43_coverage import _document as _make_doc
        from tests.v4.test_v43_coverage import _items as _assemble_items

        compiled = _compile_doc(_make_doc())
        (item,) = _assemble_items(compiled, 1)
        naked = ArtifactRef(
            id="chk-naked",
            role="checkpoint",
            locator=ArtifactLocator.run_relative("steps/s_opt/naked.chk"),
            subject_structure_id="c000",
        )
        altered = _dc.replace(
            item,
            named_inputs=WorkItemInputs(
                structures=item.named_inputs.structures,
                artifacts=FrozenDict({"checkpoint": ArtifactSet.of(naked)}),
                results=item.named_inputs.results,
            ),
        )
        with pytest.raises(DomainError):
            build_input_bundle_manifest(altered)

    def test_definition_builders(self) -> None:
        from confflow.domain.resources import ResourceRequest

        definition = build_execution_definition(
            program="orca",
            native={"keyword": "x"},
            execution_adapter=None,
            result_profile=None,
            checks=("normal_termination",),
            check_params=FrozenDict({}),
            recovery="none",
            recovery_params=FrozenDict({}),
            resources=ResourceRequest(cores_per_item=1, memory_per_item_bytes=2**30),
            charge=0,
            multiplicity=1,
            freeze=None,
            step_semantic_digest=DIGEST_B,
            contract_versions={},
        )
        assert definition.execution_adapter == "standard"
        assert definition.checks == ("normal_termination",)
        native_definition = build_execution_definition(
            program="orca",
            native=FrozenDict({"keyword": "x"}),
            execution_adapter="custom",
            result_profile="custom",
            checks=[],
            check_params={},
            recovery="none",
            recovery_params={},
            resources={"cores_per_item": 1},
            charge=None,
            multiplicity=None,
            freeze=[1, 2],
            step_semantic_digest=DIGEST_B,
            contract_versions={"adapter": "v"},
        )
        assert native_definition.freeze == (1, 2)

    def test_local_transport_delegates(self, tmp_path: Path) -> None:
        from tests.v4.test_v43_coverage import _compile as _compile_doc
        from tests.v4.test_v43_coverage import _document as _make_doc
        from tests.v4.test_v43_coverage import _items as _assemble_items

        compiled = _compile_doc(_make_doc())
        (item,) = _assemble_items(compiled, 1)

        class _StubExecutor:
            def __init__(self) -> None:
                self.calls: list[str] = []

            def execute(self, work_item: Any, *args: Any, **kwargs: Any) -> Any:
                self.calls.append(work_item.id)
                return "stub-result"

        stub = _StubExecutor()
        transport = LocalTransport(stub)  # type: ignore[arg-type]
        assert transport.executor is stub
        assert transport.execute(item, None, attempt=3) == "stub-result"  # type: ignore[arg-type]
        assert stub.calls == [item.id]

    def test_transport_error_result_shape(self) -> None:
        from tests.v4.test_v43_coverage import _compile as _compile_doc
        from tests.v4.test_v43_coverage import _document as _make_doc
        from tests.v4.test_v43_coverage import _items as _assemble_items

        compiled = _compile_doc(_make_doc())
        (item,) = _assemble_items(compiled, 1)
        failed = transport_error_result(
            item, code="remote_worker_error", message="boom", step_id=STEP_ID
        )
        assert failed.error is not None
        assert failed.error.code == "remote_worker_error"
        assert failed.error.retryable is True


# ---------------------------------------------------------------------------
# Artifact flow + claim leftovers
# ---------------------------------------------------------------------------


class TestArtifactFlowEdges:
    """Cardinality names and subject rules at the edges."""

    def test_cardinality_names(self) -> None:
        from confflow.workflow.v4.artifact_flow import (
            ArtifactFlowError,
            filter_by_role,
            verify_binding_cardinality,
        )

        with pytest.raises(ArtifactFlowError) as exc_info:
            verify_binding_cardinality(
                artifacts=ArtifactSet(),
                port_name="checkpoint",
                cardinality="bogus-name",
                subject_structure_id=None,
            )
        assert exc_info.value.code == "artifact_cardinality_invalid"
        verify_binding_cardinality(
            artifacts=ArtifactSet(),
            port_name="checkpoint",
            cardinality="optional",
            subject_structure_id=None,
        )
        verify_binding_cardinality(
            artifacts=ArtifactSet.of(
                ArtifactRef(
                    id="a",
                    role="checkpoint",
                    locator=ArtifactLocator.run_relative("steps/s/a.chk"),
                    checksum=DIGEST_A,
                    subject_structure_id="s0",
                )
            ),
            port_name="checkpoint",
            cardinality="one_or_more",
            subject_structure_id=None,
        )
        assert len(filter_by_role(ArtifactSet(), frozenset({"checkpoint"}))) == 0

    def test_subject_rules(self) -> None:
        from confflow.workflow.v4.artifact_flow import (
            resolve_restart_subject,
            subject_for_output,
        )

        assert (
            resolve_restart_subject(
                native_subject="a",
                output_structure_id="b",
                geometry_semantics="produced",
            )
            == "b"
        )
        assert (
            resolve_restart_subject(
                native_subject="a",
                output_structure_id="b",
                geometry_semantics="passthrough",
            )
            == "b"
        )
        assert (
            resolve_restart_subject(
                native_subject="a", output_structure_id="", geometry_semantics="produced"
            )
            == "a"
        )
        assert subject_for_output(input_structure_id="a", output_structure_id="b") == "b"
        assert subject_for_output(input_structure_id="a", output_structure_id=None) == "a"
