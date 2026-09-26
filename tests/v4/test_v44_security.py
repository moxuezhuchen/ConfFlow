#!/usr/bin/env python3

"""V4-4 remote security: every handoff/staging/worker seam fails closed.

Each attack below is aimed at the REAL handoff/staging/worker entry points
(``confflow.remote.handoff`` / ``staging`` / ``worker`` plus the frozen
envelope models), never at copies.  All must fail closed with typed errors
(:class:`HandoffError`, :class:`StagingError`, the :class:`DomainError`
family, :class:`PersistenceError` / :class:`CorruptStateError`, or pydantic
:class:`ValidationError`):

- traversal / absolute / symlink-source / symlink-dest / symlink-parent /
  world-writable / checksum-swap against the staging seam,
- malformed-JSON / unknown-fields / oversized / wrong-run / work-item /
  attempt / digest against the handoff reader and envelope validators,
- duplicate-artifact-id / duplicate-subject-role (+ one valid control entry) /
  corrupt-result-bundle against staging and result import.

Launch tokens in file-level tests use the handoff/lease charset
(``[A-Za-z0-9._-]``, leading alnum); the frozen transport ``+`` token
convention is covered by the parity suite and reported as an interop gap.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from confflow.domain import FrozenDict
from confflow.domain.artifact import ArtifactLocator, ArtifactRef
from confflow.domain.errors import DomainError
from confflow.persistence.contracts import (
    CorruptStateError,
    PersistenceError,
    store_path,
)
from confflow.remote.envelope import HANDOFF_SCHEMA_V2
from confflow.remote.handoff import HandoffError
from confflow.remote.staging import StagingError

#: Any of these typed failures counts as failing closed.  ``StagingError``
#: subclasses ``DomainError``; ``HandoffError`` is a ``ValueError`` per the
#: handoff file-protocol contract.
TYPED_ERRORS = (DomainError, PersistenceError, CorruptStateError, ValidationError, HandoffError)

DIGEST_A = "sha256:" + "a1" * 32
DIGEST_B = "sha256:" + "b2" * 32

SAFE_TOKEN = "tok-h1"
STEP_ID = "s_opt"


def _execution_definition() -> Any:
    """Build compiled execution semantics for one synthetic work item."""
    from confflow.remote.envelope import ExecutionDefinition

    return ExecutionDefinition(
        program="orca",
        native={"keyword": "B3LYP D3BJ def2-SVP Opt"},
        execution_adapter="standard",
        result_profile="standard",
        checks=("normal_termination",),
        check_params={},
        recovery="none",
        recovery_params={},
        resources={},
        charge=0,
        multiplicity=1,
        freeze=None,
        step_semantic_digest=DIGEST_B,
        contract_versions={"adapter": "a.v1", "profile": "p.v1"},
    )


def _handoff_instances(**overrides: Any) -> dict[str, Any]:
    """Return valid handoff fields holding model instances (tamper tests)."""
    from confflow.remote.envelope import EnvironmentRequest, InputBundleManifest

    fields: dict[str, Any] = {
        "run_id": "run",
        "step_id": STEP_ID,
        "work_item_id": "wi:s_opt:a",
        "logical_key": "s_opt:a",
        "attempt_number": 1,
        "launch_token": SAFE_TOKEN,
        "work_item_digest": DIGEST_A,
        "step_semantic_digest": DIGEST_B,
        "producer_provenance": {},
        "environment_request": EnvironmentRequest(program="orca"),
        "execution": _execution_definition(),
        "inputs": InputBundleManifest(entries=()),
    }
    fields.update(overrides)
    return fields


def _valid_handoff(**overrides: Any) -> Any:
    """Build a digest-valid handoff via the proven ``.new()`` pattern.

    Nested contracts travel as ``mode="python"`` dumps (tuples preserved
    for strict validation) alongside the explicit schema and protocol
    version, exactly as the sibling handoff suite constructs envelopes.
    """
    from confflow.remote.envelope import EnvironmentRequest, InputBundleManifest, WorkerHandoffV2

    fields = _handoff_instances(**overrides)
    execution = fields.pop("execution")
    inputs = fields.pop("inputs")
    environment_request = fields.pop("environment_request")
    assert isinstance(environment_request, EnvironmentRequest)
    assert isinstance(inputs, InputBundleManifest)
    return WorkerHandoffV2.new(
        schema=HANDOFF_SCHEMA_V2,
        protocol_version="v2",
        **fields,
        environment_request=environment_request.model_dump(mode="python"),
        execution=execution.model_dump(mode="python"),
        inputs=inputs.model_dump(mode="python"),
    )


def _valid_result_bundle(handoff: Any, **overrides: Any) -> Any:
    """Build a digest-valid result bundle bound to *handoff*."""
    from confflow.remote.envelope import ResultBundle, compute_result_digest

    fields: dict[str, Any] = {
        "run_id": handoff.run_id,
        "step_id": handoff.step_id,
        "work_item_id": handoff.work_item_id,
        "attempt_number": handoff.attempt_number,
        "launch_token": handoff.launch_token,
        "work_item_digest": handoff.work_item_digest,
        "environment": {},
        "result": {
            "work_item_id": handoff.work_item_id,
            "status": "completed",
            "structures": [],
            "results": [],
            "semantic_digest": handoff.work_item_digest,
        },
        "produced_artifacts": (),
        "transport_metadata": {},
    }
    fields.update(overrides)
    probe = ResultBundle.model_construct(**fields, bundle_digest=DIGEST_A)
    digest = compute_result_digest(probe._digest_payload())
    return ResultBundle.model_validate({**fields, "bundle_digest": digest})


def _open_store(run_root: str, step_id: str) -> Any:
    """Open the durable step store (real ``confflow.persistence`` seam)."""
    from confflow.persistence.work_items import SqliteWorkItemStore

    return SqliteWorkItemStore.open(store_path(run_root, step_id))


def _plant_source(run_root: str, artifact_id: str, payload: bytes) -> str:
    """Plant producer source bytes and return the absolute source path."""
    source = os.path.join(run_root, "producer-bytes", f"{artifact_id}.bin")
    os.makedirs(os.path.dirname(source), exist_ok=True)
    with open(source, "wb") as handle:
        handle.write(payload)
    return source


def _manifest_with_artifact(
    artifact_id: str,
    checksum: str,
    bundle_locator: str,
    *,
    role: str = "checkpoint",
    subject: str = "struct_s0",
) -> Any:
    """Build an input manifest holding one artifact entry (real envelope)."""
    from confflow.remote.envelope import ArtifactBundleEntry, InputBundleManifest

    return InputBundleManifest(
        entries=(
            ArtifactBundleEntry(
                artifact_id=artifact_id,
                role=role,
                checksum=checksum,
                subject_structure_id=subject,
                bundle_locator=bundle_locator,
            ),
        )
    )


def _checksum(payload: bytes) -> str:
    """Return the ``sha256:<hex>`` checksum of *payload*."""
    return "sha256:" + hashlib.sha256(payload).hexdigest()


class TestHandoffEnvelopeValidation:
    """Envelope validators reject forged or malformed handoffs."""

    def test_unknown_fields_rejected(self) -> None:
        from confflow.remote.envelope import WorkerHandoffV2

        handoff = _valid_handoff()
        with pytest.raises(ValidationError):
            WorkerHandoffV2.model_validate(
                {**_handoff_instances(), "manifest_digest": handoff.manifest_digest, "evil": 1}
            )

    def test_result_bundle_unknown_fields_rejected(self) -> None:
        from confflow.remote.envelope import ResultBundle

        bundle = _valid_result_bundle(_valid_handoff())
        with pytest.raises(ValidationError):
            ResultBundle.model_validate(
                {
                    "run_id": bundle.run_id,
                    "step_id": bundle.step_id,
                    "work_item_id": bundle.work_item_id,
                    "attempt_number": bundle.attempt_number,
                    "launch_token": bundle.launch_token,
                    "work_item_digest": bundle.work_item_digest,
                    "environment": {},
                    "result": dict(bundle.result),
                    "produced_artifacts": (),
                    "transport_metadata": {},
                    "bundle_digest": bundle.bundle_digest,
                    "evil": 1,
                }
            )

    def test_malformed_json_rejected(self) -> None:
        handoff = _valid_handoff()
        raw = handoff.model_dump_json().encode("utf-8")
        with pytest.raises((json.JSONDecodeError, UnicodeDecodeError)):
            json.loads(raw[: len(raw) // 2].decode("utf-8", errors="strict"))
        with pytest.raises(ValidationError):
            type(handoff).model_validate("not-a-mapping")  # type: ignore[arg-type]

    def test_work_item_digest_tamper_rejected(self) -> None:
        from confflow.remote.envelope import WorkerHandoffV2

        handoff = _valid_handoff()
        with pytest.raises(ValidationError, match="digest mismatch"):
            WorkerHandoffV2.model_validate(
                {
                    **_handoff_instances(work_item_digest="sha256:" + "c3" * 32),
                    "manifest_digest": handoff.manifest_digest,
                }
            )

    def test_attempt_number_tamper_rejected(self) -> None:
        from confflow.remote.envelope import WorkerHandoffV2

        handoff = _valid_handoff()
        with pytest.raises(ValidationError, match="digest mismatch"):
            WorkerHandoffV2.model_validate(
                {
                    **_handoff_instances(attempt_number=2),
                    "manifest_digest": handoff.manifest_digest,
                }
            )

    def test_manifest_digest_tamper_rejected(self) -> None:
        from confflow.remote.envelope import WorkerHandoffV2

        with pytest.raises(ValidationError, match="digest mismatch"):
            WorkerHandoffV2.model_validate({**_handoff_instances(), "manifest_digest": DIGEST_A})

    def test_launch_token_tamper_rejected(self) -> None:
        from confflow.remote.envelope import WorkerHandoffV2

        handoff = _valid_handoff()
        with pytest.raises(ValidationError, match="digest mismatch"):
            WorkerHandoffV2.model_validate(
                {
                    **_handoff_instances(launch_token="tok-evil"),
                    "manifest_digest": handoff.manifest_digest,
                }
            )

    def test_run_id_tamper_rejected(self) -> None:
        from confflow.remote.envelope import WorkerHandoffV2

        handoff = _valid_handoff()
        with pytest.raises(ValidationError, match="digest mismatch"):
            WorkerHandoffV2.model_validate(
                {
                    **_handoff_instances(run_id="other-run"),
                    "manifest_digest": handoff.manifest_digest,
                }
            )

    def test_schema_downgrade_to_v1_rejected(self) -> None:
        from confflow.remote.envelope import WorkerHandoffV2

        handoff = _valid_handoff()
        with pytest.raises(ValidationError):
            WorkerHandoffV2.model_validate(
                {
                    **_handoff_instances(),
                    "manifest_digest": handoff.manifest_digest,
                    "schema": "confflow.control.worker-handoff.v1",
                }
            )

    def test_strict_types_rejected(self) -> None:
        from confflow.remote.envelope import WorkerHandoffV2

        handoff = _valid_handoff()
        with pytest.raises(ValidationError):
            WorkerHandoffV2.model_validate(
                {
                    **_handoff_instances(attempt_number="1"),  # type: ignore[dict-item]
                    "manifest_digest": handoff.manifest_digest,
                }
            )

    def test_handoff_size_bound_is_pinned(self) -> None:
        from confflow.remote.envelope import MAX_HANDOFF_BYTES, canonical_envelope_bytes

        assert MAX_HANDOFF_BYTES == 64 * 1024 * 1024
        assert len(canonical_envelope_bytes(_valid_handoff())) < MAX_HANDOFF_BYTES


class TestResultBundleValidation:
    """Result-bundle validators reject forged bundles."""

    def test_result_payload_tamper_rejected(self) -> None:
        from confflow.remote.envelope import ResultBundle

        handoff = _valid_handoff()
        bundle = _valid_result_bundle(handoff)
        with pytest.raises(ValidationError, match="digest mismatch"):
            ResultBundle.model_validate(
                {
                    "run_id": bundle.run_id,
                    "step_id": bundle.step_id,
                    "work_item_id": bundle.work_item_id,
                    "attempt_number": bundle.attempt_number,
                    "launch_token": bundle.launch_token,
                    "work_item_digest": bundle.work_item_digest,
                    "environment": {},
                    "result": {"status": "forged"},
                    "produced_artifacts": (),
                    "transport_metadata": {},
                    "bundle_digest": bundle.bundle_digest,
                }
            )

    def test_produced_artifact_checksum_tamper_rejected(self) -> None:
        from confflow.remote.envelope import ResultBundle, ResultProducedArtifact

        handoff = _valid_handoff()
        bundle = _valid_result_bundle(
            handoff,
            produced_artifacts=(
                ResultProducedArtifact(
                    artifact_id="art_1",
                    role="checkpoint",
                    checksum=DIGEST_A,
                    subject_structure_id="struct_s0",
                    bundle_locator="files/0001-art_1",
                ),
            ),
        )
        forged = ResultProducedArtifact(
            artifact_id="art_1",
            role="checkpoint",
            checksum="sha256:" + "c3" * 32,
            subject_structure_id="struct_s0",
            bundle_locator="files/0001-art_1",
        )
        with pytest.raises(ValidationError, match="digest mismatch"):
            ResultBundle.model_validate(
                {
                    "run_id": bundle.run_id,
                    "step_id": bundle.step_id,
                    "work_item_id": bundle.work_item_id,
                    "attempt_number": bundle.attempt_number,
                    "launch_token": bundle.launch_token,
                    "work_item_digest": bundle.work_item_digest,
                    "environment": {},
                    "result": dict(bundle.result),
                    "produced_artifacts": (forged,),
                    "transport_metadata": {},
                    "bundle_digest": bundle.bundle_digest,
                }
            )

    def test_bundle_digest_tamper_rejected(self) -> None:
        from confflow.remote.envelope import ResultBundle

        handoff = _valid_handoff()
        bundle = _valid_result_bundle(handoff)
        with pytest.raises(ValidationError, match="digest mismatch"):
            ResultBundle.model_validate(
                {
                    "run_id": bundle.run_id,
                    "step_id": bundle.step_id,
                    "work_item_id": bundle.work_item_id,
                    "attempt_number": bundle.attempt_number,
                    "launch_token": bundle.launch_token,
                    "work_item_digest": bundle.work_item_digest,
                    "environment": {},
                    "result": dict(bundle.result),
                    "produced_artifacts": (),
                    "transport_metadata": {},
                    "bundle_digest": DIGEST_A,
                }
            )


class TestHandoffReaderSecurity:
    """File-level handoff attacks against the real reader."""

    def _write_file(self, directory: Path, name: str, payload: bytes) -> str:
        path = directory / name
        path.write_bytes(payload)
        os.chmod(path, 0o600)
        return str(path)

    def test_write_read_round_trip(self, tmp_path: Path) -> None:
        from confflow.remote.handoff import read_handoff_envelope, write_handoff_envelope

        handoff = _valid_handoff()
        path = write_handoff_envelope(
            handoff=handoff, worker_root=str(tmp_path / "worker"), launch_token=SAFE_TOKEN
        )
        assert path == os.path.join(str(tmp_path / "worker"), "inbox", SAFE_TOKEN, "handoff.json")
        assert read_handoff_envelope(path=path, expected_run_id="run") == handoff

    def test_oversized_handoff_rejected(self, tmp_path: Path) -> None:
        from confflow.remote.envelope import MAX_HANDOFF_BYTES
        from confflow.remote.handoff import read_handoff_envelope

        handoff = _valid_handoff()
        blob = handoff.model_dump_json().encode("utf-8")
        assert len(blob) < MAX_HANDOFF_BYTES
        oversized = self._write_file(
            tmp_path, "handoff.json", blob + b" " * (MAX_HANDOFF_BYTES - len(blob) + 1)
        )
        with pytest.raises(HandoffError, match="exceeds the maximum size"):
            read_handoff_envelope(path=oversized, expected_run_id=handoff.run_id)

    def test_malformed_json_file_rejected(self, tmp_path: Path) -> None:
        from confflow.remote.handoff import read_handoff_envelope

        path = self._write_file(tmp_path, "handoff.json", b"{truncated")
        with pytest.raises(HandoffError, match="not valid JSON"):
            read_handoff_envelope(path=path, expected_run_id="run")

    def test_wrong_run_rejected(self, tmp_path: Path) -> None:
        from confflow.remote.handoff import read_handoff_envelope, write_handoff_envelope

        handoff = _valid_handoff()
        stored = write_handoff_envelope(
            handoff=handoff, worker_root=str(tmp_path / "worker"), launch_token=SAFE_TOKEN
        )
        with pytest.raises(HandoffError, match="run identity"):
            read_handoff_envelope(path=stored, expected_run_id="some-other-run")

    def test_unknown_fields_file_rejected(self, tmp_path: Path) -> None:
        from confflow.remote.handoff import read_handoff_envelope

        handoff = _valid_handoff()
        payload = json.loads(handoff.model_dump_json())
        payload["unknown_field"] = "evil"
        path = self._write_file(tmp_path, "handoff.json", json.dumps(payload).encode("utf-8"))
        with pytest.raises(HandoffError, match="strict validation"):
            read_handoff_envelope(path=path, expected_run_id=handoff.run_id)

    def test_digest_mismatch_file_rejected(self, tmp_path: Path) -> None:
        from confflow.remote.handoff import read_handoff_envelope

        handoff = _valid_handoff()
        payload = json.loads(handoff.model_dump_json())
        payload["work_item_digest"] = "sha256:" + "c3" * 32
        path = self._write_file(tmp_path, "handoff.json", json.dumps(payload).encode("utf-8"))
        with pytest.raises(HandoffError, match="strict validation"):
            read_handoff_envelope(path=path, expected_run_id=handoff.run_id)

    def test_group_writable_file_rejected(self, tmp_path: Path) -> None:
        from confflow.remote.handoff import read_handoff_envelope

        handoff = _valid_handoff()
        raw_path = tmp_path / "handoff.json"
        raw_path.write_bytes(handoff.model_dump_json().encode("utf-8"))
        os.chmod(raw_path, 0o640)
        with pytest.raises(HandoffError, match="group/world permissions"):
            read_handoff_envelope(path=str(raw_path), expected_run_id=handoff.run_id)

    def test_symlink_file_rejected(self, tmp_path: Path) -> None:
        from confflow.remote.handoff import read_handoff_envelope

        handoff = _valid_handoff()
        target = self._write_file(tmp_path, "real.json", handoff.model_dump_json().encode("utf-8"))
        link = tmp_path / "handoff.json"
        link.symlink_to(target)
        with pytest.raises(HandoffError):
            read_handoff_envelope(path=str(link), expected_run_id=handoff.run_id)


class TestStagingPathSecurity:
    """Path and symlink attacks against the real staging seam."""

    def test_clean_stage_verifies_bytes(self, tmp_path: Path) -> None:
        from confflow.remote.staging import stage_input_bundle

        run_root = str(tmp_path / "run")
        worker_root = str(tmp_path / "worker")
        os.makedirs(run_root, exist_ok=True)
        os.makedirs(worker_root, exist_ok=True)
        payload = b"clean-payload" * 64
        source = _plant_source(run_root, "art_clean", payload)
        manifest = _manifest_with_artifact("art_clean", _checksum(payload), "files/0001-art_clean")
        staged = stage_input_bundle(
            manifest=manifest,
            run_root=run_root,
            worker_root=worker_root,
            launch_token=SAFE_TOKEN,
            source_files={"art_clean": source},
        )
        assert staged.entries[0].checksum_verified is True
        assert staged.entries[0].staged_path is not None
        with open(staged.entries[0].staged_path, "rb") as handle:
            assert handle.read() == payload

    def test_traversal_locator_rejected_without_escape(self, tmp_path: Path) -> None:
        from confflow.remote.staging import stage_input_bundle

        run_root = str(tmp_path / "run")
        worker_root = str(tmp_path / "worker")
        os.makedirs(run_root, exist_ok=True)
        os.makedirs(worker_root, exist_ok=True)
        payload = b"payload-trav"
        source = _plant_source(run_root, "art_trav", payload)
        manifest = _manifest_with_artifact("art_trav", _checksum(payload), "../escape.bin")
        with pytest.raises(StagingError, match="escape the bundle"):
            stage_input_bundle(
                manifest=manifest,
                run_root=run_root,
                worker_root=worker_root,
                launch_token=SAFE_TOKEN,
                source_files={"art_trav": source},
            )
        assert not (tmp_path / "escape.bin").exists()

    def test_absolute_locator_rejected(self, tmp_path: Path) -> None:
        from confflow.remote.staging import stage_input_bundle

        run_root = str(tmp_path / "run")
        worker_root = str(tmp_path / "worker")
        os.makedirs(run_root, exist_ok=True)
        os.makedirs(worker_root, exist_ok=True)
        payload = b"payload-abs"
        source = _plant_source(run_root, "art_abs", payload)
        manifest = _manifest_with_artifact("art_abs", _checksum(payload), "/tmp/evil.bin")
        with pytest.raises(StagingError, match="not absolute"):
            stage_input_bundle(
                manifest=manifest,
                run_root=run_root,
                worker_root=worker_root,
                launch_token=SAFE_TOKEN,
                source_files={"art_abs": source},
            )
        assert not Path("/tmp/evil.bin").exists()

    def test_symlink_source_rejected(self, tmp_path: Path) -> None:
        from confflow.remote.staging import stage_input_bundle

        run_root = str(tmp_path / "run")
        worker_root = str(tmp_path / "worker")
        os.makedirs(run_root, exist_ok=True)
        os.makedirs(worker_root, exist_ok=True)
        outside = tmp_path / "outside.bin"
        outside.write_bytes(b"outside-bytes")
        link_path = os.path.join(run_root, "producer-bytes", "art_link.bin")
        os.makedirs(os.path.dirname(link_path), exist_ok=True)
        os.symlink(str(outside), link_path)
        manifest = _manifest_with_artifact(
            "art_link", _checksum(b"outside-bytes"), "files/0001-art_link"
        )
        with pytest.raises(StagingError):
            stage_input_bundle(
                manifest=manifest,
                run_root=run_root,
                worker_root=worker_root,
                launch_token=SAFE_TOKEN,
                source_files={"art_link": link_path},
            )

    def test_symlink_dest_rejected(self, tmp_path: Path) -> None:
        from confflow.remote.staging import stage_input_bundle

        run_root = str(tmp_path / "run")
        worker_root = str(tmp_path / "worker")
        os.makedirs(run_root, exist_ok=True)
        os.makedirs(worker_root, exist_ok=True)
        payload = b"payload-dest"
        source = _plant_source(run_root, "art_dest", payload)
        staged_dir = Path(worker_root) / "work" / SAFE_TOKEN / "files"
        staged_dir.mkdir(parents=True, exist_ok=True)
        (staged_dir / "0001-art_dest").symlink_to(tmp_path / "loot.bin")
        manifest = _manifest_with_artifact("art_dest", _checksum(payload), "files/0001-art_dest")
        with pytest.raises(StagingError, match="non-symlink"):
            stage_input_bundle(
                manifest=manifest,
                run_root=run_root,
                worker_root=worker_root,
                launch_token=SAFE_TOKEN,
                source_files={"art_dest": source},
            )
        assert not (tmp_path / "loot.bin").exists()

    def test_symlink_parent_rejected(self, tmp_path: Path) -> None:
        from confflow.remote.staging import stage_input_bundle

        run_root = str(tmp_path / "run")
        worker_root = str(tmp_path / "worker")
        os.makedirs(run_root, exist_ok=True)
        os.makedirs(worker_root, exist_ok=True)
        real_dir = tmp_path / "real"
        real_dir.mkdir(exist_ok=True)
        os.symlink(str(real_dir), os.path.join(worker_root, "work"))
        payload = b"payload-parent"
        source = _plant_source(run_root, "art_parent", payload)
        manifest = _manifest_with_artifact(
            "art_parent", _checksum(payload), "files/0001-art_parent"
        )
        with pytest.raises(StagingError, match="symlink"):
            stage_input_bundle(
                manifest=manifest,
                run_root=run_root,
                worker_root=worker_root,
                launch_token=SAFE_TOKEN,
                source_files={"art_parent": source},
            )

    def test_world_writable_source_dir_rejected(self, tmp_path: Path) -> None:
        from confflow.remote.staging import stage_input_bundle

        run_root = str(tmp_path / "run")
        worker_root = str(tmp_path / "worker")
        os.makedirs(run_root, exist_ok=True)
        os.makedirs(worker_root, exist_ok=True)
        payload = b"payload-ww"
        source = _plant_source(run_root, "art_ww", payload)
        os.chmod(os.path.dirname(source), 0o777)
        manifest = _manifest_with_artifact("art_ww", _checksum(payload), "files/0001-art_ww")
        try:
            with pytest.raises(StagingError, match="world-writable"):
                stage_input_bundle(
                    manifest=manifest,
                    run_root=run_root,
                    worker_root=worker_root,
                    launch_token=SAFE_TOKEN,
                    source_files={"art_ww": source},
                )
        finally:
            os.chmod(os.path.dirname(source), 0o755)

    def test_checksum_swap_rejected(self, tmp_path: Path) -> None:
        from confflow.remote.staging import stage_input_bundle

        run_root = str(tmp_path / "run")
        worker_root = str(tmp_path / "worker")
        os.makedirs(run_root, exist_ok=True)
        os.makedirs(worker_root, exist_ok=True)
        source = _plant_source(run_root, "art_swap", b"swapped-bytes")
        manifest = _manifest_with_artifact(
            "art_swap", _checksum(b"original-bytes"), "files/0001-art_swap"
        )
        with pytest.raises(StagingError, match="checksum mismatch"):
            stage_input_bundle(
                manifest=manifest,
                run_root=run_root,
                worker_root=worker_root,
                launch_token=SAFE_TOKEN,
                source_files={"art_swap": source},
            )

    def test_missing_source_fails_closed(self, tmp_path: Path) -> None:
        from confflow.remote.staging import stage_input_bundle

        run_root = str(tmp_path / "run")
        worker_root = str(tmp_path / "worker")
        os.makedirs(run_root, exist_ok=True)
        os.makedirs(worker_root, exist_ok=True)
        manifest = _manifest_with_artifact("art_ghost", _checksum(b"x"), "files/0001-art_ghost")
        with pytest.raises(StagingError, match="no producer source"):
            stage_input_bundle(
                manifest=manifest,
                run_root=run_root,
                worker_root=worker_root,
                launch_token=SAFE_TOKEN,
                source_files={},
            )


class TestStagingIdentitySecurity:
    """Duplicate-identity attacks against staging/import."""

    def test_duplicate_artifact_id_rejected(self, tmp_path: Path) -> None:
        from confflow.remote.envelope import ArtifactBundleEntry, InputBundleManifest
        from confflow.remote.staging import stage_input_bundle

        run_root = str(tmp_path / "run")
        worker_root = str(tmp_path / "worker")
        os.makedirs(run_root, exist_ok=True)
        os.makedirs(worker_root, exist_ok=True)
        payload = b"payload-dup"
        source = _plant_source(run_root, "art_dup", payload)
        manifest = InputBundleManifest(
            entries=(
                ArtifactBundleEntry(
                    artifact_id="art_dup",
                    role="checkpoint",
                    checksum=_checksum(payload),
                    subject_structure_id="struct_s0",
                    bundle_locator="files/0001-art_dup",
                ),
                ArtifactBundleEntry(
                    artifact_id="art_dup",
                    role="checkpoint",
                    checksum=_checksum(payload),
                    subject_structure_id="struct_s0",
                    bundle_locator="files/0002-art_dup",
                ),
            )
        )
        with pytest.raises(StagingError, match="duplicate artifact"):
            stage_input_bundle(
                manifest=manifest,
                run_root=run_root,
                worker_root=worker_root,
                launch_token=SAFE_TOKEN,
                source_files={"art_dup": source},
            )

    def test_duplicate_subject_role_plus_one_valid_rejected(self, tmp_path: Path) -> None:
        from confflow.remote.envelope import ArtifactBundleEntry, InputBundleManifest
        from confflow.remote.staging import stage_input_bundle

        run_root = str(tmp_path / "run")
        worker_root = str(tmp_path / "worker")
        os.makedirs(run_root, exist_ok=True)
        os.makedirs(worker_root, exist_ok=True)
        payload_a, payload_b, payload_ok = b"payload-a", b"payload-b", b"payload-ok"
        source_a = _plant_source(run_root, "art_sub_a", payload_a)
        source_b = _plant_source(run_root, "art_sub_b", payload_b)
        source_ok = _plant_source(run_root, "art_ok", payload_ok)
        # Two entries bind the same subject with the same role (ambiguous
        # binding) plus one valid control entry proving the detector is not
        # tripped by degenerate single-entry manifests.
        manifest = InputBundleManifest(
            entries=(
                ArtifactBundleEntry(
                    artifact_id="art_sub_a",
                    role="checkpoint",
                    checksum=_checksum(payload_a),
                    subject_structure_id="struct_shared",
                    bundle_locator="files/0001-art_sub_a",
                ),
                ArtifactBundleEntry(
                    artifact_id="art_sub_b",
                    role="checkpoint",
                    checksum=_checksum(payload_b),
                    subject_structure_id="struct_shared",
                    bundle_locator="files/0002-art_sub_b",
                ),
                ArtifactBundleEntry(
                    artifact_id="art_ok",
                    role="checkpoint",
                    checksum=_checksum(payload_ok),
                    subject_structure_id="struct_other",
                    bundle_locator="files/0003-art_ok",
                ),
            )
        )
        # Transport staging is faithful: distinct bytes under distinct ids
        # stage successfully even when they share (subject, role).  The
        # ambiguity belongs to the binding layer, which rejects it before
        # any execution (see below); staging must not break MANY bindings.
        staged = stage_input_bundle(
            manifest=manifest,
            run_root=run_root,
            worker_root=worker_root,
            launch_token=SAFE_TOKEN,
            source_files={
                "art_sub_a": source_a,
                "art_sub_b": source_b,
                "art_ok": source_ok,
            },
        )
        assert len(staged.entries) == 3
        # ...while a ONE-cardinality binding over the same pair fails closed.
        from confflow.domain.artifact import ArtifactRef as _ArtifactRef
        from confflow.domain.artifact import ArtifactSet as _ArtifactSet
        from confflow.workflow.v4.artifact_flow import ArtifactFlowError, select_restart_artifact

        pair = _ArtifactSet.of(
            _ArtifactRef(
                id="art_sub_a",
                role="checkpoint",
                locator=ArtifactLocator.run_relative("steps/s_a/a.chk"),
                checksum=_checksum(payload_a),
                subject_structure_id="struct_shared",
            ),
            _ArtifactRef(
                id="art_sub_b",
                role="checkpoint",
                locator=ArtifactLocator.run_relative("steps/s_a/b.chk"),
                checksum=_checksum(payload_b),
                subject_structure_id="struct_shared",
            ),
        )
        with pytest.raises(ArtifactFlowError) as exc_info:
            select_restart_artifact(
                artifacts=pair, subject_structure_id="struct_shared", role="checkpoint"
            )
        assert exc_info.value.code == "artifact_subject_ambiguous"


class TestResultImportSecurity:
    """Forged result bundles against the real import seam."""

    def _write_bundle(self, directory: Path, bundle: Any) -> str:
        result_dir = directory / "worker-result"
        files_dir = result_dir / "files"
        files_dir.mkdir(parents=True, exist_ok=True)
        path = result_dir / "result.json"
        path.write_bytes(bundle.model_dump_json().encode("utf-8"))
        os.chmod(path, 0o600)
        return str(path)

    def test_wrong_work_item_rejected_without_commit(self, tmp_path: Path) -> None:
        from confflow.remote.staging import import_result_artifacts

        run_root = str(tmp_path / "run")
        os.makedirs(run_root, exist_ok=True)
        handoff = _valid_handoff()
        other = _valid_handoff(
            work_item_id="wi:s_opt:other",
            logical_key="s_opt:other",
            launch_token="tok-other",
        )
        bundle_path = self._write_bundle(tmp_path, _valid_result_bundle(other))
        with _open_store(run_root, handoff.step_id) as store:
            with pytest.raises(StagingError, match="does not match the handoff"):
                import_result_artifacts(
                    result_path=bundle_path,
                    handoff=handoff,
                    run_root=run_root,
                    store=store,
                )
            assert store.get_result(handoff.work_item_id) is None

    def test_wrong_attempt_rejected_without_commit(self, tmp_path: Path) -> None:
        from confflow.remote.staging import import_result_artifacts

        run_root = str(tmp_path / "run")
        os.makedirs(run_root, exist_ok=True)
        handoff = _valid_handoff()
        bundle = _valid_result_bundle(handoff, attempt_number=handoff.attempt_number + 1)
        bundle_path = self._write_bundle(tmp_path, bundle)
        with _open_store(run_root, handoff.step_id) as store:
            with pytest.raises(StagingError, match="does not match the handoff"):
                import_result_artifacts(
                    result_path=bundle_path,
                    handoff=handoff,
                    run_root=run_root,
                    store=store,
                )
            assert store.get_result(handoff.work_item_id) is None

    def test_wrong_digest_rejected_without_commit(self, tmp_path: Path) -> None:
        from confflow.remote.staging import import_result_artifacts

        run_root = str(tmp_path / "run")
        os.makedirs(run_root, exist_ok=True)
        handoff = _valid_handoff()
        bundle = _valid_result_bundle(handoff, work_item_digest="sha256:" + "c3" * 32)
        bundle_path = self._write_bundle(tmp_path, bundle)
        with _open_store(run_root, handoff.step_id) as store:
            with pytest.raises(StagingError, match="does not match the handoff"):
                import_result_artifacts(
                    result_path=bundle_path,
                    handoff=handoff,
                    run_root=run_root,
                    store=store,
                )
            assert store.get_result(handoff.work_item_id) is None

    def test_corrupt_result_bundle_rejected_without_commit(self, tmp_path: Path) -> None:
        from confflow.remote.staging import import_result_artifacts

        run_root = str(tmp_path / "run")
        os.makedirs(run_root, exist_ok=True)
        handoff = _valid_handoff()
        bundle = _valid_result_bundle(handoff)
        raw = bytearray(bundle.model_dump_json().encode("utf-8"))
        raw[len(raw) // 2] ^= 0xFF
        result_dir = tmp_path / "worker-result"
        files_dir = result_dir / "files"
        files_dir.mkdir(parents=True, exist_ok=True)
        bundle_path = result_dir / "result.json"
        bundle_path.write_bytes(bytes(raw))
        os.chmod(bundle_path, 0o600)
        with _open_store(run_root, handoff.step_id) as store:
            with pytest.raises(StagingError):
                import_result_artifacts(
                    result_path=str(bundle_path),
                    handoff=handoff,
                    run_root=run_root,
                    store=store,
                )
            assert store.get_result(handoff.work_item_id) is None

    def test_imported_artifact_checksum_mismatch_rejected(self, tmp_path: Path) -> None:
        from confflow.persistence.contracts import OwnerIdentity
        from confflow.remote.envelope import ResultProducedArtifact
        from confflow.remote.staging import import_result_artifacts

        run_root = str(tmp_path / "run")
        os.makedirs(run_root, exist_ok=True)
        handoff = _valid_handoff()
        announced = _checksum(b"worker-bytes-v1")
        (tmp_path / "worker-result" / "files").mkdir(parents=True, exist_ok=True)
        (tmp_path / "worker-result" / "files" / "0001-art_w1").write_bytes(b"worker-bytes-v2")
        bundle = _valid_result_bundle(
            handoff,
            produced_artifacts=(
                ResultProducedArtifact(
                    artifact_id="art_w1",
                    role="checkpoint",
                    checksum=announced,
                    subject_structure_id="struct_s0",
                    bundle_locator="files/0001-art_w1",
                ),
            ),
        )
        result_path = tmp_path / "worker-result" / "result.json"
        result_path.write_bytes(bundle.model_dump_json().encode("utf-8"))
        os.chmod(result_path, 0o600)
        with _open_store(run_root, handoff.step_id) as store:
            store.register_item(
                work_item_id=handoff.work_item_id,
                logical_key=handoff.logical_key,
                step_id=handoff.step_id,
                work_item_digest=handoff.work_item_digest,
                step_semantic_digest=handoff.step_semantic_digest,
                producer_provenance={},
            )
            assert store.claim(handoff.work_item_id, owner=OwnerIdentity(owner_token="sec")) is True
            with pytest.raises(StagingError, match="changed"):
                import_result_artifacts(
                    result_path=str(result_path),
                    handoff=handoff,
                    run_root=run_root,
                    store=store,
                )
            assert store.get_result(handoff.work_item_id) is None


def test_frozen_dict_inputs_are_immutable() -> None:
    """Sanity: shared test provenance mappings cannot be mutated by seams."""
    provenance = FrozenDict({"adapter_version": "a.v1"})
    with pytest.raises(TypeError):
        provenance["adapter_version"] = "evil"  # type: ignore[index]


def test_artifact_ref_subject_binding() -> None:
    """Sanity: checkpoint refs carry the subject the parity suite compares."""
    ref = ArtifactRef(
        id="chk_s0",
        role="checkpoint",
        locator=ArtifactLocator.run_relative("steps/s_opt/chk_s0.chk"),
        checksum=DIGEST_A,
        subject_structure_id="struct_s0",
        producer_step_id=STEP_ID,
    )
    assert ref.subject_structure_id == "struct_s0"
