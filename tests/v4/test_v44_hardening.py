#!/usr/bin/env python3

"""V4-4 branch-hardening round: error-path closure across remote modules.

Behavior-driven tests over fail-closed branches the delivery suites do
not reach.  Fast and deterministic; no commercial programs required.
"""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path
from typing import Any

import pytest

from confflow.domain import FrozenDict
from confflow.domain.artifact import ArtifactLocator, ArtifactRef, ArtifactSet
from confflow.domain.completion import StepStatus, WorkItemStatus
from confflow.domain.errors import DomainError
from confflow.persistence import OwnerIdentity, OwnerVerdict
from confflow.persistence.contracts import PersistenceError, store_path
from confflow.persistence.recovery import reconcile_owner
from confflow.persistence.work_items import SqliteWorkItemStore
from confflow.remote.envelope import (
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
from confflow.remote.staging import StagingError
from confflow.remote.supervision import cancel_attempt
from confflow.remote.transport import RemoteTransport
from confflow.remote.worker import WorkerError, run_worker_envelope
from tests.v4._builders import structure

FAKE_ORCA = Path(__file__).resolve().parent / "fakes" / "fake_orca.py"


def _compiled(plan_doc: Any) -> Any:
    from tests.v4.test_v43_coverage import _compile as _compile_doc

    return _compile_doc(plan_doc)


def _made_doc() -> Any:
    from tests.v4.test_v43_coverage import _document as _make_doc

    return _make_doc()


def _made_items(compiled: Any, count: int) -> Any:
    from tests.v4.test_v43_coverage import _items as _assemble_items

    return _assemble_items(compiled, count)


def _request(compiled: Any, items: Any, run_root: str) -> Any:
    """Build a local durable step request wired to the fake ORCA executable."""
    from confflow.execution import ExecutionBinding
    from confflow.execution.batch import StepExecutionRequest
    from confflow.execution.checks_standard import CHECKS
    from confflow.execution.profile_standard import PROFILES
    from confflow.execution.recovery_standard import RECOVERIES
    from confflow.programs.registry import get_program_adapter

    planned = compiled.steps[0]
    return StepExecutionRequest(
        step=planned,
        items=tuple(items),
        scientific=planned.scientific,
        scientific_defaults=compiled.scientific_defaults,
        adapter=get_program_adapter("orca"),
        profile=PROFILES["standard"],
        checks=(CHECKS["normal_termination"],),
        recovery=RECOVERIES["none"],
        execution_binding=ExecutionBinding(
            binding_id="test", executable=str(FAKE_ORCA), env=FrozenDict({})
        ),
        run_root=run_root,
        environment=None,
        definition_digest=compiled.definition_digest,
    )


STEP_ID = "s_opt"
DIGEST_A = "sha256:" + "a" * 64
DIGEST_B = "sha256:" + "b" * 64
DIGEST_C = "sha256:" + "c" * 64


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


def _struct_entry(structure_id: str = "cov0", **overrides: Any) -> StructureBundleEntry:
    """Build a structure bundle entry."""
    payload = dict(structure(structure_id).to_dict())
    payload.update(overrides.pop("payload_extra", {}))
    return StructureBundleEntry(
        structure_id=structure_id,
        payload=payload,
        digest=overrides.pop("digest", None) or bundle_entry_digest("structure", payload),
    )


def _handoff(entries: Any, execution: Any = None, **overrides: Any) -> WorkerHandoffV2:
    """Build a valid handoff envelope."""
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
    """Write *handoff* into a fresh worker root."""
    worker_root = str(tmp_path / "worker")
    return (
        write_handoff_envelope(handoff=handoff, worker_root=worker_root, launch_token=token),
        worker_root,
    )


def _run_worker(path: str, worker_root: str, token: str = "tok1") -> Any:
    """Run the worker envelope, returning the raised WorkerError or result."""
    try:
        return run_worker_envelope(
            handoff_path=path, staged_bundle={}, worker_root=worker_root, launch_token=token
        )
    except WorkerError as exc:
        return exc


# ---------------------------------------------------------------------------
# Worker direct-unit branches
# ---------------------------------------------------------------------------


class TestWorkerDirectUnits:
    """Private worker helpers fail closed on malformed input."""

    def test_structure_from_entry(self, tmp_path: Path) -> None:
        from confflow.remote import worker as _worker

        record = _worker._structure_from_entry(_struct_entry())
        assert record.id == "cov0"
        with pytest.raises(WorkerError):
            _worker._structure_from_entry(_struct_entry(digest=DIGEST_A))
        bad = _struct_entry()
        object.__setattr__(bad, "payload", {"id": "x"})
        with pytest.raises(WorkerError):
            _worker._structure_from_entry(bad)

    def test_result_from_entry(self, tmp_path: Path) -> None:
        from confflow.remote import worker as _worker
        from confflow.remote.envelope import ResultEntry

        def _entry(payload: Any, digest: Any = None) -> ResultEntry:
            return ResultEntry(
                result_id="energy:cov0",
                payload=payload,
                digest=digest or bundle_entry_digest("result", payload),
            )

        good = {"kind": "energy", "value": 1.0, "unit": "hartree"}
        assert _worker._result_from_entry(_entry(dict(good))).value == 1.0
        with pytest.raises(WorkerError):
            _worker._result_from_entry(_entry(dict(good), digest=DIGEST_A))
        with pytest.raises(WorkerError):
            _worker._result_from_entry(_entry({"kind": "energy", "value": 1.0, "zzz": 1}))
        with pytest.raises(WorkerError):
            _worker._result_from_entry(_entry({"kind": "energy", "value": 1.0, "unit": "zz"}))
        with pytest.raises(WorkerError):
            _worker._result_from_entry(_entry({"kind": "energy", "value": 1.0, "quantity": "zz"}))
        with pytest.raises(WorkerError):
            _worker._result_from_entry(
                {"kind": "energy", "value": 1.0, "provenance": "zz"}
                if False
                else _entry({"kind": "energy", "value": 1.0, "provenance": "zz"})
            )
        with pytest.raises(WorkerError):
            _worker._result_from_entry(_entry({"value": 1.0}))

    def test_artifact_rebuild_branches(self, tmp_path: Path) -> None:
        from confflow.remote import worker as _worker
        from confflow.remote.envelope import ArtifactBundleEntry as _ArtifactEntry

        blob = tmp_path / "chk.bin"
        blob.write_bytes(b"restart-bytes")
        staged = {"chk1": str(blob)}
        entry = _ArtifactEntry(
            artifact_id="chk1",
            role="checkpoint",
            checksum="sha256:" + __import__("hashlib").sha256(b"restart-bytes").hexdigest(),
            subject_structure_id="cov0",
            bundle_locator="files/0001-chk1",
        )
        grouped = _worker._artifacts_from_entries(
            [entry], staged_bundle=staged, worker_root=str(tmp_path)
        )
        assert grouped["checkpoint"][0].id == "chk1"
        assert grouped["checkpoint"][0].subject_structure_id == "cov0"
        with pytest.raises(WorkerError):
            _worker._artifacts_from_entries([entry], staged_bundle={}, worker_root=str(tmp_path))

    def test_resources_and_resolvers(self) -> None:
        from confflow.remote import worker as _worker
        from confflow.remote.envelope import ExecutionDefinition as _ExecDef

        request = _worker._resources_from_definition(_exec_def())
        assert request.cores_per_item == 1
        bad = _ExecDef.model_construct(
            program="orca", resources=None, step_semantic_digest=DIGEST_B
        )
        with pytest.raises(WorkerError):
            _worker._resources_from_definition(bad)
        with pytest.raises(WorkerError):
            _worker._execute_work_item(None, None, None)  # type: ignore[arg-type]

    def test_run_stage_wrapping(self) -> None:
        from confflow.remote import worker as _worker

        def _boom() -> None:
            raise RuntimeError("stage exploded")

        with pytest.raises(WorkerError, match="named-stage"):
            _worker._run_stage("named-stage", _boom)
        assert _worker._run_stage("ok", lambda: 42) == 42


# ---------------------------------------------------------------------------
# Staging import branches
# ---------------------------------------------------------------------------


def _bundle_file(tmp_path: Path, handoff: WorkerHandoffV2, **overrides: Any) -> str:
    """Write a minimal worker result bundle, returning its path."""
    import hashlib as _hashlib
    import json as _json

    result_dir = tmp_path / "wresult"
    files_dir = result_dir / "files"
    files_dir.mkdir(parents=True, exist_ok=True)
    content = b"produced-bytes"
    (files_dir / "0001-out.chk").write_bytes(content)
    os.chmod(files_dir / "0001-out.chk", 0o600)
    fields: dict[str, Any] = {
        "run_id": handoff.run_id,
        "step_id": handoff.step_id,
        "work_item_id": handoff.work_item_id,
        "attempt_number": 1,
        "launch_token": handoff.launch_token,
        "work_item_digest": handoff.work_item_digest,
        "environment": {},
        "result": {
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
        "produced_artifacts": (
            ResultProducedArtifact(
                artifact_id="chk1",
                role="checkpoint",
                checksum="sha256:" + _hashlib.sha256(content).hexdigest(),
                size_bytes=len(content),
                subject_structure_id="cov0",
                bundle_locator="files/0001-out.chk",
            ),
        ),
    }
    fields.update(overrides)
    bundle = ResultBundle.new(**fields)
    path = result_dir / "result.json"
    path.write_bytes(_json.dumps(bundle.model_dump(mode="json")).encode("utf-8"))
    os.chmod(path, 0o600)
    return str(path)


def _claimed_store(tmp_path: Path, handoff: WorkerHandoffV2) -> SqliteWorkItemStore:
    """Register and claim the handoff work item."""
    store = SqliteWorkItemStore.open(store_path(str(tmp_path / "run"), STEP_ID))
    store.register_item(
        work_item_id=handoff.work_item_id,
        logical_key=handoff.logical_key,
        step_id=handoff.step_id,
        work_item_digest=handoff.work_item_digest,
        step_semantic_digest=handoff.step_semantic_digest,
    )
    store.claim(handoff.work_item_id, owner=OwnerIdentity(owner_token="ctl"))
    return store


class TestImportBranches:
    """import_result_artifacts identity and transfer branches."""

    def _import(self, *args: Any, **kwargs: Any) -> Any:
        from confflow.remote.staging import import_result_artifacts

        return import_result_artifacts(*args, **kwargs)

    def test_identity_mismatches(self, tmp_path: Path) -> None:
        handoff = _handoff([_struct_entry()])
        for field, bad in (
            ("run_id", "other-run"),
            ("step_id", "other-step"),
            ("work_item_id", "wi:s_opt:other"),
            ("launch_token", "other-token"),
            ("work_item_digest", DIGEST_A),
        ):
            path = self._bundle_file(tmp_path, handoff, **{field: bad})
            store = _claimed_store(tmp_path, handoff)
            try:
                with pytest.raises(StagingError):
                    self._import(
                        result_path=path,
                        handoff=handoff,
                        run_root=str(tmp_path / "run"),
                        store=store,
                    )
            finally:
                store.close()

    def _bundle_file(self, tmp_path: Path, handoff: WorkerHandoffV2, **overrides: Any) -> str:
        return _bundle_file(tmp_path, handoff, **overrides)

    def test_stale_attempt(self, tmp_path: Path) -> None:
        handoff = _handoff([_struct_entry()])
        path = self._bundle_file(tmp_path, handoff)
        store = _claimed_store(tmp_path, handoff)
        try:
            store.mark_interrupted(handoff.work_item_id, reason="crash")
            store.claim(handoff.work_item_id, owner=OwnerIdentity(owner_token="ctl2"))
            with pytest.raises(StagingError):
                self._import(
                    result_path=path,
                    handoff=handoff,
                    run_root=str(tmp_path / "run"),
                    store=store,
                )
        finally:
            store.close()

    def test_store_without_history(self, tmp_path: Path) -> None:
        handoff = _handoff([_struct_entry()])
        path = self._bundle_file(tmp_path, handoff)

        class _EmptyStore:
            def get_attempts(self, work_item_id: str) -> tuple:
                return ()

        with pytest.raises(StagingError):
            self._import(
                result_path=path,
                handoff=handoff,
                run_root=str(tmp_path / "run"),
                store=_EmptyStore(),
            )

    def test_store_without_port(self, tmp_path: Path) -> None:
        handoff = _handoff([_struct_entry()])
        path = self._bundle_file(tmp_path, handoff)
        with pytest.raises(StagingError):
            self._import(
                result_path=path,
                handoff=handoff,
                run_root=str(tmp_path / "run"),
                store=object(),
            )

    def test_size_mismatch(self, tmp_path: Path) -> None:
        import hashlib as _hashlib

        handoff = _handoff([_struct_entry()])
        result_dir = tmp_path / "wresult-size"
        files_dir = result_dir / "files"
        files_dir.mkdir(parents=True, exist_ok=True)
        content = b"produced-bytes"
        (files_dir / "0001-out.chk").write_bytes(content)
        os.chmod(files_dir / "0001-out.chk", 0o600)
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
                    artifact_id="chk1",
                    role="checkpoint",
                    checksum="sha256:" + _hashlib.sha256(content).hexdigest(),
                    size_bytes=len(content) + 100,
                    subject_structure_id="cov0",
                    bundle_locator="files/0001-out.chk",
                ),
            ),
        )
        path = result_dir / "result.json"
        import json as _json

        path.write_bytes(_json.dumps(bundle.model_dump(mode="json")).encode("utf-8"))
        os.chmod(path, 0o600)
        store = _claimed_store(tmp_path, handoff)
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

    def test_result_file_variants(self, tmp_path: Path) -> None:
        handoff = _handoff([_struct_entry()])
        store = _claimed_store(tmp_path, handoff)
        try:
            with pytest.raises(StagingError):
                self._import(
                    result_path=str(tmp_path / "ghost.json"),
                    handoff=handoff,
                    run_root=str(tmp_path / "run"),
                    store=store,
                )
            non_utf8 = tmp_path / "binary.json"
            non_utf8.write_bytes(b"\xff\xfe binary")
            os.chmod(non_utf8, 0o600)
            with pytest.raises(StagingError):
                self._import(
                    result_path=str(non_utf8),
                    handoff=handoff,
                    run_root=str(tmp_path / "run"),
                    store=store,
                )
            constant = tmp_path / "constant.json"
            constant.write_bytes(b'{"value": NaN}')
            os.chmod(constant, 0o600)
            with pytest.raises(StagingError):
                self._import(
                    result_path=str(constant),
                    handoff=handoff,
                    run_root=str(tmp_path / "run"),
                    store=store,
                )
            as_list = tmp_path / "list.json"
            as_list.write_bytes(b"[1, 2]")
            os.chmod(as_list, 0o600)
            with pytest.raises(StagingError):
                self._import(
                    result_path=str(as_list),
                    handoff=handoff,
                    run_root=str(tmp_path / "run"),
                    store=store,
                )
        finally:
            store.close()


class TestStageCopyBranches:
    """Secure-copy helper branches."""

    def test_copy_validation(self, tmp_path: Path) -> None:
        from confflow.remote import staging as _staging

        source = tmp_path / "src.bin"
        source.write_bytes(b"data")
        with pytest.raises(StagingError):
            _staging._secure_copy_file(
                source_path=str(source),
                dest_path=str(tmp_path / "d" / "out.bin"),
                expected_checksum="no-colon-here",
                expected_size=None,
                anchor=str(tmp_path),
                owner=os.getuid(),
            )
        with pytest.raises(StagingError):
            _staging._secure_copy_file(
                source_path=str(source),
                dest_path=str(tmp_path / "d" / "out.bin"),
                expected_checksum="sha256:" + "0" * 64,
                expected_size=-1,
                anchor=str(tmp_path),
                owner=os.getuid(),
            )
        with pytest.raises(StagingError):
            _staging._secure_copy_file(
                source_path="",
                dest_path=str(tmp_path / "d" / "out.bin"),
                expected_checksum="sha256:" + "0" * 64,
                expected_size=None,
                anchor=str(tmp_path),
                owner=os.getuid(),
            )
        with pytest.raises(StagingError):
            _staging._secure_copy_file(
                source_path=str(source),
                dest_path=str(tmp_path / ".." / "escape.bin"),
                expected_checksum="sha256:" + "0" * 64,
                expected_size=None,
                anchor=str(tmp_path),
                owner=os.getuid(),
            )
        with pytest.raises(StagingError):
            _staging._secure_copy_file(
                source_path=str(tmp_path),
                dest_path=str(tmp_path / "d" / "out.bin"),
                expected_checksum="sha256:" + "0" * 64,
                expected_size=None,
                anchor=str(tmp_path),
                owner=os.getuid(),
            )

    def test_split_and_locator_validation(self, tmp_path: Path) -> None:
        from confflow.remote import staging as _staging

        with pytest.raises(StagingError):
            _staging._split_checksum("no-colon", "test")
        with pytest.raises(StagingError):
            _staging._validate_relative_locator("/absolute", "test")
        with pytest.raises(StagingError):
            _staging._validate_relative_locator("../escape", "test")
        with pytest.raises(StagingError):
            _staging._validate_relative_locator("", "test")
        assert _staging._validate_relative_locator("files/0001-x", "test") == "files/0001-x"

    def test_anchor_and_parent_validation(self, tmp_path: Path) -> None:
        from confflow.remote import staging as _staging

        blocker = tmp_path / "blocker"
        blocker.write_bytes(b"x")
        with pytest.raises(StagingError):
            _staging._check_anchor(str(blocker), owner=os.getuid(), what="test")
        with pytest.raises(StagingError):
            _staging._ensure_private_dir(str(blocker), anchor=str(tmp_path), owner=os.getuid())
        with pytest.raises(StagingError):
            _staging._safe_component("", "test")
        with pytest.raises(StagingError):
            _staging._safe_component("a\x00b", "test")
        with pytest.raises(StagingError):
            _staging._safe_component(123, "test")  # type: ignore[arg-type]
        # Over-long inputs truncate with a hash suffix; loose inputs sanitize.
        assert len(_staging._safe_component("x" * 201, "test")) <= 200 + 20
        assert _staging._safe_component("../x", "test") == "_x"
        assert _staging._safe_component("tok_1", "test") == "tok_1"


# ---------------------------------------------------------------------------
# Result bundle packaging branches
# ---------------------------------------------------------------------------


class TestPackageBranches:
    """package_result_bundle validation without full execution."""

    def test_missing_transfer_source(self, tmp_path: Path) -> None:
        from confflow.domain.completion import WorkItemStatus as _Status
        from confflow.remote.result_bundle import ResultBundleError, package_result_bundle
        from tests.v4.test_v43_coverage import _compile as _compile_doc
        from tests.v4.test_v43_coverage import _document as _make_doc
        from tests.v4.test_v43_coverage import _items as _assemble_items
        from tests.v4.test_v43_coverage import _result as _make_result

        compiled = _compile_doc(_make_doc())
        (item,) = _assemble_items(compiled, 1)
        result = _make_result(item, status=_Status.COMPLETED)
        handoff = _handoff([_struct_entry()])
        with pytest.raises(ResultBundleError):
            package_result_bundle(
                work_item_result=result,
                handoff=handoff,
                result_dir=str(tmp_path / "r"),
                environment={},
                transfer_files_from=str(tmp_path / "ghost-workdir"),
            )


# ---------------------------------------------------------------------------
# Handoff edge branches
# ---------------------------------------------------------------------------


class TestHandoffEdges:
    """Handoff reader guards beyond the delivery suite."""

    def test_read_non_utf8(self, tmp_path: Path) -> None:
        from confflow.remote.handoff import read_handoff_envelope

        target = tmp_path / "bad.json"
        target.write_bytes(b"\xff\xfe not utf8")
        os.chmod(target, 0o600)
        with pytest.raises(HandoffError):
            read_handoff_envelope(path=str(target))

    def test_read_constant_and_list(self, tmp_path: Path) -> None:
        from confflow.remote.handoff import read_handoff_envelope

        constant = tmp_path / "constant.json"
        constant.write_bytes(b'{"value": NaN}')
        os.chmod(constant, 0o600)
        with pytest.raises(HandoffError):
            read_handoff_envelope(path=str(constant))
        as_list = tmp_path / "list.json"
        as_list.write_bytes(b"[1, 2]")
        os.chmod(as_list, 0o600)
        with pytest.raises(HandoffError):
            read_handoff_envelope(path=str(as_list))

    def test_read_oversized_sparse(self, tmp_path: Path) -> None:
        from confflow.remote import handoff as _handoff_module
        from confflow.remote.handoff import read_handoff_envelope

        huge = tmp_path / "huge.json"
        with open(huge, "wb") as handle:
            handle.truncate(65 * 1024 * 1024)
        os.chmod(huge, 0o600)
        with pytest.raises(HandoffError):
            read_handoff_envelope(path=str(huge))
        assert _handoff_module.MAX_HANDOFF_BYTES == 64 * 1024 * 1024

    def test_worker_root_variants(self, tmp_path: Path) -> None:
        from confflow.remote import handoff as _handoff_module

        blocker = tmp_path / "blocker"
        blocker.write_bytes(b"x")
        with pytest.raises(HandoffError):
            _handoff_module._checked_worker_root(str(blocker))
        missing = _handoff_module._checked_worker_root(str(tmp_path / "new-root"))
        assert missing.endswith("new-root")

    def test_private_dir_on_file(self, tmp_path: Path) -> None:
        from confflow.remote import handoff as _handoff_module

        blocker = tmp_path / "blocker"
        blocker.write_bytes(b"x")
        with pytest.raises(HandoffError):
            _handoff_module._ensure_private_dir(str(blocker), label="test")


# ---------------------------------------------------------------------------
# Lease edge branches
# ---------------------------------------------------------------------------


class TestLeaseEdges:
    """Lease marker and sibling flows beyond the delivery suite."""

    def _lease(self, tmp_path: Path, **overrides: Any) -> AttemptLease:
        fields: dict[str, Any] = {
            "lease_root": str(tmp_path / "leases"),
            "run_id": "r",
            "step_id": "s",
            "work_item_id": "wi:s:a",
            "attempt_number": 1,
            "launch_token": "tok",
        }
        fields.update(overrides)
        return AttemptLease(**fields)

    def test_garbage_sibling_skipped(self, tmp_path: Path) -> None:
        first = self._lease(tmp_path)
        assert first.acquire() is True
        step_dir = Path(first.path).parent
        (step_dir / "wi+s+a.attempt-1.garbage.txt").write_bytes(b"not json {{{")
        (step_dir / "unrelated.json").write_bytes(b"{}")
        # Same token contends on the file lock itself; previous_owner stays
        # empty because no sibling scan runs on the same-file path.
        second = self._lease(tmp_path)
        assert second.acquire() is False
        assert second.previous_owner is None
        second.release()
        # A different token on the same attempt sees the live sibling owner.
        rival = self._lease(tmp_path, launch_token="rival-tok")
        assert rival.acquire() is False
        assert rival.previous_owner is not None
        assert rival.previous_owner.get("pid") == os.getpid()
        rival.release()
        first.release()
        freed = self._lease(tmp_path)
        assert freed.acquire() is True
        freed.release()

    def test_release_idempotent(self, tmp_path: Path) -> None:
        lease = self._lease(tmp_path)
        lease.release()
        assert lease.acquire() is True
        lease.release()
        lease.release()
        assert lease.path.is_file()

    def test_private_dir_guards(self, tmp_path: Path) -> None:
        from confflow.remote import lease as _lease_module

        blocker = tmp_path / "blocker"
        blocker.write_bytes(b"x")
        with pytest.raises(LeaseError):
            _lease_module._ensure_owner_private_dir(blocker)
        with pytest.raises(LeaseError):
            _lease_module._checked_component("../x", "test")
        with pytest.raises(LeaseError):
            _lease_module._checked_component("", "test")
        assert _lease_module._checked_component("tok_1.2", "test") == "tok_1.2"
        assert (
            _lease_module._checked_component("wi:s:a", "work_item_id", allow_colon=True) == "wi:s:a"
        )
        with pytest.raises(LeaseError):
            _lease_module._checked_component("wi:s:a", "work_item_id")

    def test_marker_helpers(self, tmp_path: Path) -> None:
        from confflow.remote import lease as _lease_module

        empty = tmp_path / "empty.json"
        empty.write_bytes(b"")
        fd = os.open(str(empty), os.O_RDONLY)
        try:
            assert _lease_module._read_marker_dict(fd) is None
        finally:
            os.close(fd)
        garbage = tmp_path / "garbage.json"
        garbage.write_bytes(b"not json {{{")
        import os as _os

        fd = _os.open(str(garbage), _os.O_RDONLY)
        try:
            assert _lease_module._read_marker_dict(fd) == {}
        finally:
            os.close(fd)
        listed = tmp_path / "listed.json"
        listed.write_bytes(b"[1, 2]")
        fd = os.open(str(listed), os.O_RDONLY)
        try:
            assert _lease_module._read_marker_dict(fd) == {}
        finally:
            os.close(fd)
        assert _lease_module._current_create_time() is None or isinstance(
            _lease_module._current_create_time(), float
        )

    def test_create_time_without_psutil(self, tmp_path: Path) -> None:
        import sys as _sys

        from confflow.remote import lease as _lease_module

        sentinel = _sys.modules.get("psutil")
        _sys.modules["psutil"] = None  # type: ignore[assignment]
        try:
            assert _lease_module._current_create_time() is None
        finally:
            if sentinel is not None:
                _sys.modules["psutil"] = sentinel
            else:
                del _sys.modules["psutil"]

    # ---------------------------------------------------------------------------
    # Supervision edge branches
    # ---------------------------------------------------------------------------


class TestSupervisionEdges:
    """Supervision escalation and scan branches."""

    @staticmethod
    def _sleep_owner(helper: Any) -> OwnerIdentity:
        """Build the owner triple of a live helper process."""
        import time as _time

        import psutil as _psutil

        pid = helper.pid
        return OwnerIdentity(
            owner_token="helper",
            pid=pid,
            process_group_id=os.getpgid(pid),
            session_id=os.getsid(pid),
            create_time=float(_psutil.Process(pid).create_time()),
            claimed_wall=_time.time(),
        )

    def test_term_immune_escalates_to_kill(self, tmp_path: Path) -> None:
        import subprocess as _subprocess

        helper = _subprocess.Popen(
            ["sh", "-c", "trap '' TERM; while true; do sleep 1; done"],
            start_new_session=True,
            stdout=_subprocess.DEVNULL,
            stderr=_subprocess.DEVNULL,
        )
        try:
            import time as _time

            # Settle so the trap is installed before any signal arrives;
            # otherwise an early SIGTERM kills the shell before it ignores.
            _time.sleep(0.5)
            owner = self._sleep_owner(helper)
            assert reconcile_owner(owner) is OwnerVerdict.DEFINITELY_ALIVE
            proof = cancel_attempt(owner=owner, work_dir=None, grace_seconds=4.0)
            assert proof.confirmed is True
            assert "SIGKILL" in proof.detail
            assert helper.poll() is not None
        finally:
            try:
                helper.kill()
            except OSError:
                pass
            helper.wait(timeout=10)

    def test_exited_group_is_not_cancelled(self, tmp_path: Path) -> None:
        import subprocess as _subprocess
        import time as _time

        helper = _subprocess.Popen(["/bin/sleep", "30"], start_new_session=True)
        owner = self._sleep_owner(helper)
        helper.terminate()
        helper.wait(timeout=10)
        _time.sleep(0.2)
        # The group is gone: cancel has nothing to stop, so it stays
        # unconfirmed; reconciliation (not cancellation) owns dead claims.
        proof = cancel_attempt(owner=owner, work_dir=None, grace_seconds=1.0)
        assert proof.confirmed is False
        assert "not provably alive" in proof.detail

    def test_killpg_refused_is_unconfirmed(self, tmp_path: Path) -> None:
        import subprocess as _subprocess

        helper = _subprocess.Popen(["/bin/sleep", "30"], start_new_session=True)
        try:
            owner = self._sleep_owner(helper)
            import unittest.mock as _mock

            with _mock.patch("os.killpg", side_effect=PermissionError("denied")):
                proof = cancel_attempt(owner=owner, work_dir=None, grace_seconds=1.0)
            assert proof.confirmed is False
            assert "refused" in proof.detail
            assert helper.poll() is None
        finally:
            helper.terminate()
            helper.wait(timeout=10)

    def test_killpg_gone_during_race(self, tmp_path: Path) -> None:
        import subprocess as _subprocess

        helper = _subprocess.Popen(["/bin/sleep", "1"], start_new_session=True)
        try:
            owner = self._sleep_owner(helper)
            import unittest.mock as _mock

            with _mock.patch("os.killpg", side_effect=ProcessLookupError("gone")):
                proof = cancel_attempt(owner=owner, work_dir=None, grace_seconds=1.0)
            # The race resolves through reconciliation of the live helper.
            assert proof.confirmed is False
            assert proof.verdict is OwnerVerdict.DEFINITELY_ALIVE
        finally:
            try:
                helper.terminate()
            except OSError:
                pass
            helper.wait(timeout=10)

    def test_psutil_missing_degrades(self, tmp_path: Path) -> None:
        import sys as _sys

        from confflow.remote import supervision as _supervision

        sentinel = _sys.modules.get("psutil")
        _sys.modules["psutil"] = None  # type: ignore[assignment]
        try:
            assert _supervision._maybe_import_psutil() is None
            ghost = OwnerIdentity(owner_token="t", pid=2**30)
            assert _supervision._workdir_holds_live_process(str(tmp_path), owner=ghost) is True
        finally:
            if sentinel is not None:
                _sys.modules["psutil"] = sentinel
            else:
                del _sys.modules["psutil"]

    def test_scan_helpers(self, tmp_path: Path) -> None:
        from confflow.remote import supervision as _supervision

        ghost = OwnerIdentity(owner_token="t", pid=2**30)
        assert _supervision._workdir_holds_live_process(str(tmp_path), owner=ghost) is False
        # With a working process table and no holder, the scan proves absence.
        assert (
            _supervision._workdir_holds_live_process(
                str(tmp_path), owner=OwnerIdentity(owner_token="t", pid=None)
            )
            is False
        )


# ---------------------------------------------------------------------------
# Transport leftover branches
# ---------------------------------------------------------------------------


class TestTransportLeftovers:
    """Transport recovery and source-resolution branches."""

    def test_recover_prior_variants(self, tmp_path: Path) -> None:
        run_root = str(tmp_path / "run")
        worker_root = str(tmp_path / "worker")
        os.makedirs(worker_root, exist_ok=True)
        with SqliteWorkItemStore.open(store_path(run_root, STEP_ID)) as store:
            transport = RemoteTransport(run_root=run_root, store=store, worker_root=worker_root)
            from tests.v4.test_v43_coverage import _compile as _compile_doc
            from tests.v4.test_v43_coverage import _document as _make_doc
            from tests.v4.test_v43_coverage import _items as _assemble_items

            compiled = _compile_doc(_make_doc())
            (item,) = _assemble_items(compiled, 1)
            assert transport._recover_prior_result(item, "tok-missing") is None
            garbage_dir = Path(worker_root) / "results" / "tok-missing"
            garbage_dir.mkdir(parents=True, exist_ok=True)
            (garbage_dir / "result.json").write_bytes(b"\x00 garbage")
            os.chmod(garbage_dir / "result.json", 0o600)
            assert transport._recover_prior_result(item, "tok-missing") is None

    def test_artifact_sources_errors(self, tmp_path: Path) -> None:
        import dataclasses as _dc

        from confflow.domain.work_item import WorkItemInputs
        from confflow.remote.transport import RemoteTransport as _Transport
        from tests.v4.test_v43_coverage import _compile as _compile_doc
        from tests.v4.test_v43_coverage import _document as _make_doc
        from tests.v4.test_v43_coverage import _items as _assemble_items

        compiled = _compile_doc(_make_doc())
        (item,) = _assemble_items(compiled, 1)
        run_root = str(tmp_path / "run")
        os.makedirs(run_root, exist_ok=True)
        with SqliteWorkItemStore.open(store_path(run_root, STEP_ID)) as store:
            transport = _Transport(run_root=run_root, store=store, worker_root=str(tmp_path / "w"))
            external = ArtifactRef(
                id="ext",
                role="checkpoint",
                locator=ArtifactLocator.external_uri("file:///x/y.chk"),
                subject_structure_id="c000",
            )
            altered = _dc.replace(
                item,
                named_inputs=WorkItemInputs(
                    structures=item.named_inputs.structures,
                    artifacts=FrozenDict({"checkpoint": ArtifactSet.of(external)}),
                    results=item.named_inputs.results,
                ),
            )
            with pytest.raises(DomainError):
                transport._artifact_sources(altered)
            naked = ArtifactRef(
                id="naked",
                role="checkpoint",
                locator=ArtifactLocator.run_relative("steps/s_opt/naked.chk"),
                subject_structure_id="c000",
            )
            target = Path(run_root) / "steps" / "s_opt" / "naked.chk"
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(b"naked-bytes")
            hashed = _dc.replace(
                item,
                named_inputs=WorkItemInputs(
                    structures=item.named_inputs.structures,
                    artifacts=FrozenDict({"checkpoint": ArtifactSet.of(naked)}),
                    results=item.named_inputs.results,
                ),
            )
            sources, checksums = transport._artifact_sources(hashed)
            assert sources["naked"].endswith("naked.chk")
            assert checksums["naked"].startswith("sha256:")

    def test_manifest_subject_rules(self, tmp_path: Path) -> None:
        from confflow.domain.artifact import ArtifactSet as _ArtifactSet
        from confflow.workflow.v4.artifact_flow import (
            filter_by_role,
            resolve_restart_subject,
            subject_for_output,
            verify_binding_cardinality,
        )

        assert resolve_restart_subject(
            native_subject=None, output_structure_id="o", geometry_semantics="produced"
        ) == ("o")
        assert (
            verify_binding_cardinality(
                artifacts=_ArtifactSet(),
                port_name="p",
                cardinality="many",
                subject_structure_id=None,
            )
            is None
        )
        assert subject_for_output(input_structure_id="a", output_structure_id=None) == "a"
        assert len(filter_by_role(_ArtifactSet(), frozenset())) == 0


class TestWorkItemsLeftovers:
    """Store paths the delivery suites do not reach."""

    def test_nul_and_type_guards(self, tmp_path: Path) -> None:
        with pytest.raises(PersistenceError):
            SqliteWorkItemStore.open(str(tmp_path / "a\x00b.sqlite"))
        with pytest.raises(PersistenceError):
            SqliteWorkItemStore.open(123)  # type: ignore[arg-type]
        with SqliteWorkItemStore.open(store_path(str(tmp_path / "run"), STEP_ID)) as store:
            with pytest.raises(PersistenceError):
                store.get_state(123)  # type: ignore[arg-type]
            with pytest.raises(PersistenceError):
                store.register_item(
                    work_item_id="",
                    logical_key="k",
                    step_id="s",
                    work_item_digest=DIGEST_A,
                    step_semantic_digest=DIGEST_B,
                )

    def test_sqlite_error_injection(self, tmp_path: Path) -> None:
        from confflow.domain.completion import WorkItemStatus as _Status
        from tests.v4.test_v43_coverage import _compile as _compile_doc
        from tests.v4.test_v43_coverage import _document as _make_doc
        from tests.v4.test_v43_coverage import _items as _assemble_items
        from tests.v4.test_v43_coverage import _result as _make_result

        compiled = _compile_doc(_make_doc())
        (item,) = _assemble_items(compiled, 1)
        with SqliteWorkItemStore.open(store_path(str(tmp_path / "run"), STEP_ID)) as store:
            store.register_item(
                work_item_id=item.id,
                logical_key=item.logical_key,
                step_id=item.step_id,
                work_item_digest=item.semantic_digest,
                step_semantic_digest=DIGEST_B,
            )
            store.claim(item.id, owner=OwnerIdentity(owner_token="t"))

            class _FailingConnection:
                def __init__(self, real: Any) -> None:
                    self._real = real

                def execute(self, sql: str, parameters: Any = ()) -> Any:
                    if "UPDATE" in sql or "INSERT" in sql:
                        raise sqlite3.OperationalError("disk I/O error")
                    return self._real.execute(sql, parameters)

            real = store._conn
            store._conn = _FailingConnection(real)  # type: ignore[assignment]
            try:
                with pytest.raises(PersistenceError):
                    store.complete(item.id, result=_make_result(item, status=_Status.COMPLETED))
            finally:
                store._conn = real
            with pytest.raises(PersistenceError):
                store.fail("wi:ghost", result=_make_result(item, status=_Status.FAILED))


# ---------------------------------------------------------------------------
# Round 2: fault-injected I/O branches and validation closure
# ---------------------------------------------------------------------------


class TestHandoffFaultInjection:
    """os-level fault injection for handoff temp/publish/read paths."""

    def _valid_file(self, tmp_path: Path) -> tuple[str, str]:
        handoff = _handoff([_struct_entry()])
        return _write_handoff(tmp_path, handoff)

    def test_worker_root_inspection_errors(self, tmp_path: Path) -> None:
        from confflow.remote import handoff as _handoff_module

        with pytest.raises(HandoffError):
            _handoff_module._checked_worker_root("/proc/version/x")
        link = tmp_path / "rootlink"
        os.symlink(str(tmp_path), str(link))
        with pytest.raises(HandoffError):
            _handoff_module._checked_worker_root(str(link))

    def test_make_dirs_failure(self, tmp_path: Path) -> None:
        handoff = _handoff([_struct_entry()])
        blocker = tmp_path / "blocker"
        blocker.write_bytes(b"x")
        with pytest.raises(HandoffError):
            write_handoff_envelope(
                handoff=handoff,
                worker_root=str(blocker / "sub"),
                launch_token="tok1",
            )

    def test_temp_create_failure(self, tmp_path: Path) -> None:
        import unittest.mock as _mock

        handoff = _handoff([_struct_entry()])
        worker_root = str(tmp_path / "worker")
        with _mock.patch("os.open", side_effect=OSError("no fds")):
            with pytest.raises(HandoffError):
                write_handoff_envelope(
                    handoff=handoff, worker_root=worker_root, launch_token="tok1"
                )

    def test_write_progress_failure(self, tmp_path: Path) -> None:
        import unittest.mock as _mock

        real_open = os.open
        handoff = _handoff([_struct_entry()])
        worker_root = str(tmp_path / "worker")

        def _no_progress(path: Any, *args: Any, **kwargs: Any) -> Any:
            if isinstance(path, str) and path.endswith(".json"):
                return real_open(path, *args, **kwargs)
            return real_open(path, *args, **kwargs)

        with _mock.patch("os.write", return_value=0):
            with pytest.raises(HandoffError):
                write_handoff_envelope(
                    handoff=handoff, worker_root=worker_root, launch_token="tok1"
                )

    def test_publish_failure(self, tmp_path: Path) -> None:
        import unittest.mock as _mock

        handoff = _handoff([_struct_entry()])
        worker_root = str(tmp_path / "worker")
        with _mock.patch("os.replace", side_effect=OSError("read-only fs")):
            with pytest.raises(HandoffError):
                write_handoff_envelope(
                    handoff=handoff, worker_root=worker_root, launch_token="tok1"
                )

    def test_dir_fsync_skipped_gracefully(self, tmp_path: Path) -> None:
        import unittest.mock as _mock

        real_open = os.open
        handoff = _handoff([_struct_entry()])

        def _fail_dir_open(path: Any, *args: Any, **kwargs: Any) -> Any:
            if args and (args[0] & os.O_RDONLY) and not (args[0] & os.O_WRONLY):
                import stat as _stat

                try:
                    if _stat.S_ISDIR(os.lstat(path).st_mode):
                        raise OSError("cannot open dir")
                except OSError:
                    raise
            return real_open(path, *args, **kwargs)

        with _mock.patch("os.open", side_effect=_fail_dir_open):
            _write_handoff(tmp_path, handoff)

    def test_read_inspect_and_stream_failures(self, tmp_path: Path) -> None:
        import unittest.mock as _mock

        from confflow.remote.handoff import read_handoff_envelope

        path, _root = self._valid_file(tmp_path)
        with _mock.patch("os.fstat", side_effect=OSError("bad fd")):
            with pytest.raises(HandoffError):
                read_handoff_envelope(path=path)
        real_read = os.read
        calls = {"count": 0}

        def _fail_second(fd: int, size: int) -> bytes:
            calls["count"] += 1
            if calls["count"] >= 2:
                raise OSError("stream reset")
            return real_read(fd, size)

        big = tmp_path / "big.json"
        with open(path, "rb") as handle:
            payload = handle.read()
        with open(big, "wb") as handle:
            handle.write(payload * 300000)
        os.chmod(big, 0o600)
        with _mock.patch("os.read", side_effect=_fail_second):
            with pytest.raises(HandoffError):
                read_handoff_envelope(path=str(big))

    def test_envelope_not_object(self, tmp_path: Path) -> None:
        from confflow.remote.handoff import read_handoff_envelope

        target = tmp_path / "list.json"
        target.write_bytes(b"[1, 2, 3]")
        os.chmod(target, 0o600)
        with pytest.raises(HandoffError):
            read_handoff_envelope(path=str(target))


class TestLeaseFaultBranches:
    """Lease marker and sibling edge branches."""

    def test_marker_on_directory(self, tmp_path: Path) -> None:
        from confflow.remote import lease as _lease_module

        with pytest.raises(OSError):
            _lease_module._open_marker(tmp_path)

    def test_read_marker_variants(self, tmp_path: Path) -> None:
        from confflow.remote import lease as _lease_module

        valid = tmp_path / "valid.json"
        valid.write_bytes(b'{"pid": 1}')
        fd = os.open(str(valid), os.O_RDONLY)
        try:
            assert _lease_module._read_marker_dict(fd) == {"pid": 1}
        finally:
            os.close(fd)
        huge = tmp_path / "huge.json"
        with open(huge, "wb") as handle:
            handle.truncate(200 * 1024)
        fd = os.open(str(huge), os.O_RDONLY)
        try:
            assert _lease_module._read_marker_dict(fd) == {}
        finally:
            os.close(fd)
        empty = tmp_path / "empty.json"
        empty.write_bytes(b"")
        fd = os.open(str(empty), os.O_RDONLY)
        try:
            assert _lease_module._read_marker_dict(fd) is None
        finally:
            os.close(fd)

    def test_sibling_unreadable_skipped(self, tmp_path: Path) -> None:
        first = AttemptLease(
            lease_root=str(tmp_path / "leases"),
            run_id="r",
            step_id="s",
            work_item_id="wi:s:a",
            attempt_number=1,
            launch_token="tok",
        )
        assert first.acquire() is True
        step_dir = Path(first.path).parent
        target = step_dir / "wi+s+a.attempt-1.junkdir"
        target.mkdir()
        rival = AttemptLease(
            lease_root=str(tmp_path / "leases"),
            run_id="r",
            step_id="s",
            work_item_id="wi:s:a",
            attempt_number=1,
            launch_token="rival",
        )
        # The directory sibling cannot be opened as a marker: skipped, while
        # the live marker still blocks the rival.
        assert rival.acquire() is False
        assert rival.previous_owner is not None
        rival.release()
        first.release()
        # With only the directory sibling left, acquisition proceeds.
        freed = AttemptLease(
            lease_root=str(tmp_path / "leases"),
            run_id="r",
            step_id="s",
            work_item_id="wi:s:a",
            attempt_number=1,
            launch_token="rival",
        )
        assert freed.acquire() is True
        freed.release()

    def test_acquire_twice_same_instance(self, tmp_path: Path) -> None:
        lease = AttemptLease(
            lease_root=str(tmp_path / "leases"),
            run_id="r",
            step_id="s",
            work_item_id="wi:s:a",
            attempt_number=1,
            launch_token="tok",
        )
        assert lease.acquire() is True
        assert lease.acquire() is True
        lease.release()

    def test_bad_attempt_numbers(self, tmp_path: Path) -> None:
        for bad in (0, -1, "x", True, 1.5):
            with pytest.raises(LeaseError):
                AttemptLease(
                    lease_root=str(tmp_path / "leases"),
                    run_id="r",
                    step_id="s",
                    work_item_id="wi:s:a",
                    attempt_number=bad,
                    launch_token="tok",
                )


class TestSupervisionFaultBranches:
    """Supervision scan branches with scripted process tables."""

    def _fake_psutil(self, **kwargs: Any) -> Any:
        class _Denied(Exception):
            pass

        class _Gone(Exception):
            pass

        class _Fake:
            AccessDenied = _Denied
            NoSuchProcess = _Gone
            ZombieProcess = _Gone

            def __init__(self, procs: Any) -> None:
                self._procs = procs

            def process_iter(self, attrs: Any = None) -> Any:
                behavior = kwargs.get("iter_behavior", "list")
                if behavior == "raise":
                    raise RuntimeError("iter exploded")
                return list(self._procs)

        return _Fake(kwargs.get("procs", []))

    def test_iter_failure_blocks(self, tmp_path: Path) -> None:
        import sys as _sys

        from confflow.remote import supervision as _supervision

        ghost = OwnerIdentity(owner_token="t", pid=2**30)
        fake = self._fake_psutil(iter_behavior="raise")
        sentinel = _sys.modules.get("psutil")
        _sys.modules["psutil"] = fake  # type: ignore[assignment]
        try:
            assert _supervision._workdir_holds_live_process(str(tmp_path), owner=ghost) is True
        finally:
            if sentinel is not None:
                _sys.modules["psutil"] = sentinel
            else:
                del _sys.modules["psutil"]

    def test_denied_markers_collected(self, tmp_path: Path) -> None:
        import sys as _sys

        from confflow.remote import supervision as _supervision

        class _Member:
            pid = 999991
            info = {"pid": 999991, "cwd": "/nonexistent-dir-xyz"}

            def is_running(self) -> bool:
                return True

        fake = self._fake_psutil(procs=[_Member()])
        sentinel = _sys.modules.get("psutil")
        _sys.modules["psutil"] = fake  # type: ignore[assignment]
        try:
            ghost = OwnerIdentity(owner_token="t", pid=2**30)
            assert _supervision._workdir_holds_live_process(str(tmp_path), owner=ghost) is False
        finally:
            if sentinel is not None:
                _sys.modules["psutil"] = sentinel
            else:
                del _sys.modules["psutil"]

    def test_non_dict_info_skipped(self, tmp_path: Path) -> None:
        import sys as _sys

        from confflow.remote import supervision as _supervision

        class _Weird:
            pid = 999993
            info = ["not", "a", "dict"]

            def is_running(self) -> bool:
                return True

        fake = self._fake_psutil(procs=[_Weird()])
        sentinel = _sys.modules.get("psutil")
        _sys.modules["psutil"] = fake  # type: ignore[assignment]
        try:
            ghost = OwnerIdentity(owner_token="t", pid=2**30)
            assert _supervision._workdir_holds_live_process(str(tmp_path), owner=ghost) is False
        finally:
            if sentinel is not None:
                _sys.modules["psutil"] = sentinel
            else:
                del _sys.modules["psutil"]

    def test_own_pgid_holds(self, tmp_path: Path) -> None:
        from confflow.remote import supervision as _supervision

        owner = OwnerIdentity(owner_token="t", pid=2**30, process_group_id=os.getpgid(os.getpid()))
        assert _supervision._workdir_holds_live_process(str(tmp_path), owner=owner) is True

    def test_reconcile_now_passthrough(self) -> None:
        from confflow.remote import supervision as _supervision

        ghost = OwnerIdentity(owner_token="t", pid=2**30)
        assert _supervision._reconcile_now(ghost) is OwnerVerdict.DEFINITELY_DEAD


class TestResultBundleBranches:
    """Result-bundle secure-read and packaging branches."""

    def test_secure_read_variants(self, tmp_path: Path) -> None:
        from confflow.remote import result_bundle as _bundle
        from confflow.remote.result_bundle import ResultBundleError as _BundleError

        missing = tmp_path / "ghost.json"
        with pytest.raises(_BundleError):
            _bundle._secure_read_bytes(path=str(missing), max_bytes=64, label="test")
        as_dir = tmp_path / "adir"
        as_dir.mkdir()
        with pytest.raises(_BundleError):
            _bundle._secure_read_bytes(path=str(as_dir), max_bytes=64, label="test")
        huge = tmp_path / "huge.json"
        with open(huge, "wb") as handle:
            handle.truncate(100)
        os.chmod(huge, 0o600)
        with pytest.raises(_BundleError):
            _bundle._secure_read_bytes(path=str(huge), max_bytes=10, label="test")

    def test_package_missing_produced_file(self, tmp_path: Path) -> None:
        import dataclasses as _dc

        from confflow.domain.artifact import ArtifactSet as _ArtifactSet
        from confflow.domain.completion import WorkItemStatus as _Status
        from confflow.remote.result_bundle import ResultBundleError, package_result_bundle
        from tests.v4.test_v43_coverage import _compile as _compile_doc
        from tests.v4.test_v43_coverage import _document as _make_doc
        from tests.v4.test_v43_coverage import _items as _assemble_items
        from tests.v4.test_v43_coverage import _result as _make_result

        compiled = _compile_doc(_make_doc())
        (item,) = _assemble_items(compiled, 1)
        result = _dc.replace(
            _make_result(item, status=_Status.COMPLETED),
            artifacts=_ArtifactSet.of(
                ArtifactRef(
                    id="ghost",
                    role="native_output",
                    locator=ArtifactLocator.run_relative("steps/s_opt/ghost.log"),
                    checksum=DIGEST_A,
                )
            ),
        )
        handoff = _handoff([_struct_entry()])
        transfer = tmp_path / "workdir"
        transfer.mkdir()
        with pytest.raises(ResultBundleError):
            package_result_bundle(
                work_item_result=result,
                handoff=handoff,
                result_dir=str(tmp_path / "r"),
                environment={},
                transfer_files_from=str(transfer),
            )

    def test_copy_and_write_helpers(self, tmp_path: Path) -> None:
        from confflow.remote import result_bundle as _bundle
        from confflow.remote.result_bundle import ResultBundleError

        source = tmp_path / "src.bin"
        source.write_bytes(b"payload")
        digest, size = _bundle._copy_file_atomic(str(source), str(tmp_path / "dst.bin"))
        assert size == 7
        assert digest.startswith("sha256:") or len(digest) == 64
        with pytest.raises(ResultBundleError):
            _bundle._copy_file_atomic(str(tmp_path / "ghost"), str(tmp_path / "d2"))
        _bundle._fsync_dir(str(tmp_path))
        with pytest.raises(OSError):
            _bundle._write_all(123, b"x")  # type: ignore[arg-type]


class TestStagingImportBranches:
    """Import helper branches: locators, sizes, and destinations."""

    def test_locator_variants(self, tmp_path: Path) -> None:
        handoff = _handoff([_struct_entry()])
        result_path = _bundle_file(tmp_path, handoff)
        store = _claimed_store(tmp_path, handoff)
        try:
            from confflow.remote import staging as _staging

            assert _staging._resolve_worker_file(
                str(Path(result_path).parent), "files/0001-out.chk"
            ).endswith("0001-out.chk")
            with pytest.raises(StagingError):
                _staging._resolve_worker_file(str(Path(result_path).parent), "../escape.chk")
            with pytest.raises(StagingError):
                _staging._resolve_worker_file(str(Path(result_path).parent), "files/ghost.chk")
        finally:
            store.close()

    def test_hash_and_reverify_helpers(self, tmp_path: Path) -> None:
        from confflow.remote import staging as _staging

        blob = tmp_path / "blob.bin"
        blob.write_bytes(b"0123456789")
        digest, total = _staging._hash_file_secure(str(blob), owner=os.getuid(), what="test")
        assert total == 10
        assert len(digest) == 64
        with pytest.raises(StagingError):
            _staging._hash_file_secure(str(tmp_path / "ghost"), owner=os.getuid(), what="test")
        with pytest.raises(StagingError):
            _staging._reverify_copy(
                str(blob),
                "sha256:" + "0" * 64,
                None,
                owner=os.getuid(),
            )
        with pytest.raises(StagingError):
            _staging._reverify_copy(
                str(blob),
                "sha256:" + digest,
                999,
                owner=os.getuid(),
            )
        _staging._reverify_copy(str(blob), "sha256:" + digest, 10, owner=os.getuid())
        _staging._remove_tree_best_effort(str(tmp_path / "ghost"), anchor=str(tmp_path))
        _staging._remove_tree_best_effort(str(tmp_path), anchor=str(tmp_path))


class TestWorkerResolveBranches:
    """Worker resolver branches via crafted envelopes."""

    def test_recovery_binding(self, tmp_path: Path) -> None:
        from confflow.programs.registry import get_program_adapter
        from confflow.remote import worker as _worker

        adapter = get_program_adapter("gaussian")
        policy = _worker._resolve_recovery("ts_rescue_scan", {}, adapter)
        assert policy is not None
        with pytest.raises(WorkerError):
            _worker._resolve_recovery("ts_rescue_scan", {"recovery": "bogus-version"}, adapter)

    def test_check_drift(self, tmp_path: Path) -> None:
        from confflow.remote import worker as _worker

        with pytest.raises(WorkerError):
            _worker._resolve_checks(("normal_termination",), {"check:normal_termination": "old"})

    def test_profile_drift(self, tmp_path: Path) -> None:
        from confflow.remote import worker as _worker

        with pytest.raises(WorkerError):
            _worker._resolve_profile("standard", {"profile": "old"})

    def test_adapter_drift(self, tmp_path: Path) -> None:
        path, worker_root = _write_handoff(
            tmp_path,
            _handoff(
                [_struct_entry()],
                _exec_def(contract_versions={"adapter": "bogus-adapter"}),
            ),
        )
        with pytest.raises(WorkerError):
            run_worker_envelope(
                handoff_path=path,
                staged_bundle={},
                worker_root=worker_root,
                launch_token="tok1",
            )

    def test_lookup_variants(self, tmp_path: Path) -> None:
        from confflow.remote import worker as _worker

        blob = tmp_path / "a.chk"
        blob.write_bytes(b"x")
        assert _worker._lookup_mapping({"a": str(blob)}, "a", "files/a") == str(blob)
        assert _worker._lookup_mapping({"files/a": str(blob)}, "b", "files/a") == str(blob)
        assert _worker._lookup_directory(str(tmp_path), "a", "a.chk").endswith("a.chk")

        class _DirHolder:
            def __init__(self, directory: str) -> None:
                self.work_dir = directory

        assert _worker._lookup_opaque(_DirHolder(str(tmp_path)), "a", "a.chk").endswith("a.chk")

        class _MappingHolder:
            def __init__(self, mapping: Any) -> None:
                self.files = mapping

        assert _worker._lookup_opaque(_MappingHolder({"a": str(blob)}), "a", "files/a") == str(blob)

        class _Getter:
            def get(self, key: str) -> Any:
                return str(blob) if key == "a" else None

        assert _worker._lookup_opaque(_Getter(), "a", "files/a") == str(blob)
        with pytest.raises(WorkerError):
            _worker._lookup_opaque(_Getter(), "missing", "files/missing")

        class _ExplodingGetter:
            def get(self, key: str) -> Any:
                raise RuntimeError("getter exploded")

        with pytest.raises(WorkerError):
            _worker._lookup_opaque(_ExplodingGetter(), "a", "files/a")

    def test_safe_token_variants(self) -> None:
        from confflow.remote import worker as _worker

        assert _worker._safe_token_component("a/b\x00c") == "a_b_c"


class TestStoreFaultBranches:
    """Store proxy faults for complete/fail/cancel paths."""

    def test_terminal_sqlite_errors(self, tmp_path: Path) -> None:
        from confflow.domain.completion import WorkItemStatus as _Status
        from tests.v4.test_v43_coverage import _compile as _compile_doc
        from tests.v4.test_v43_coverage import _document as _make_doc
        from tests.v4.test_v43_coverage import _items as _assemble_items
        from tests.v4.test_v43_coverage import _result as _make_result

        compiled = _compile_doc(_make_doc())
        (item,) = _assemble_items(compiled, 1)
        with SqliteWorkItemStore.open(store_path(str(tmp_path / "run"), STEP_ID)) as store:
            store.register_item(
                work_item_id=item.id,
                logical_key=item.logical_key,
                step_id=item.step_id,
                work_item_digest=item.semantic_digest,
                step_semantic_digest=DIGEST_B,
            )
            store.claim(item.id, owner=OwnerIdentity(owner_token="t"))

            class _FailingConnection:
                def __init__(self, real: Any) -> None:
                    self._real = real

                def execute(self, sql: str, parameters: Any = ()) -> Any:
                    if "UPDATE" in sql or "INSERT" in sql:
                        raise sqlite3.OperationalError("disk I/O error")
                    return self._real.execute(sql, parameters)

            real = store._conn
            store._conn = _FailingConnection(real)  # type: ignore[assignment]
            try:
                with pytest.raises(PersistenceError):
                    store.complete(item.id, result=_make_result(item, status=_Status.COMPLETED))
                with pytest.raises(PersistenceError):
                    store.fail(item.id, result=_make_result(item, status=_Status.FAILED))
                with pytest.raises(PersistenceError):
                    store.cancel_item(item.id)
                with pytest.raises(PersistenceError):
                    store.mark_interrupted(item.id)
            finally:
                store._conn = real


# ---------------------------------------------------------------------------
# Round 3: import paths, secure reads, supervision hardening, leftovers
# ---------------------------------------------------------------------------


class TestImportMoreBranches:
    """Deeper import_result_artifacts and helper branches."""

    def test_attempt_helper_branches(self, tmp_path: Path) -> None:
        from confflow.remote import staging as _staging

        with pytest.raises(StagingError):
            _staging._check_attempt_current(object(), "w", 1)

        class _GarbageStore:
            def get_attempts(self, work_item_id: str) -> Any:
                return ["not-an-attempt"]

        with pytest.raises(StagingError):
            _staging._check_attempt_current(_GarbageStore(), "w", 1)

        class _EmptyStore:
            def get_attempts(self, work_item_id: str) -> Any:
                return []

        with pytest.raises(StagingError):
            _staging._check_attempt_current(_EmptyStore(), "w", 1)

    def test_worker_file_fallback_layout(self, tmp_path: Path) -> None:
        from confflow.remote import staging as _staging

        result_dir = tmp_path / "rdir"
        bare_dir = result_dir / "files"
        bare_dir.mkdir(parents=True)
        (bare_dir / "plain.chk").write_bytes(b"bytes")
        resolved = _staging._resolve_worker_file(str(result_dir), "plain.chk")
        assert resolved.endswith(os.path.join("files", "plain.chk"))
        with pytest.raises(StagingError):
            _staging._resolve_worker_file(str(result_dir), "files/ghost.chk")
        with pytest.raises(StagingError):
            _staging._resolve_worker_file(str(result_dir), "../escape.chk")
        with pytest.raises(StagingError):
            _staging._resolve_worker_file(str(result_dir), "")
        with pytest.raises(StagingError):
            _staging._resolve_worker_file(str(result_dir), 123)  # type: ignore[arg-type]

    def test_locator_edge_forms(self) -> None:
        from confflow.remote import staging as _staging

        with pytest.raises(StagingError):
            _staging._validate_relative_locator("C:drive.chk", "test")
        with pytest.raises(StagingError):
            _staging._validate_relative_locator("a/./b", "test")
        with pytest.raises(StagingError):
            _staging._validate_relative_locator("a//b", "test")
        with pytest.raises(StagingError):
            _staging._validate_relative_locator(123, "test")  # type: ignore[arg-type]
        with pytest.raises(StagingError):
            _staging._validate_relative_locator("a\x00b", "test")

    def test_remove_tree_guards(self, tmp_path: Path) -> None:
        from confflow.remote import staging as _staging

        _staging._remove_tree_best_effort(str(tmp_path / "ghost"), anchor=str(tmp_path))
        _staging._remove_tree_best_effort(str(tmp_path), anchor=str(tmp_path))
        target = tmp_path / "afile"
        target.write_bytes(b"x")
        _staging._remove_tree_best_effort(str(target), anchor=str(tmp_path))
        assert target.is_file()
        link = tmp_path / "alink"
        os.symlink(str(target), str(link))
        _staging._remove_tree_best_effort(str(link), anchor=str(tmp_path))
        assert link.is_symlink()


class TestResultBundleMoreBranches:
    """Packaging transfer and read branches."""

    def test_transfer_unreadable_and_mismatch(self, tmp_path: Path) -> None:
        import dataclasses as _dc
        import hashlib as _hashlib

        from confflow.domain.artifact import ArtifactSet as _ArtifactSet
        from confflow.domain.completion import WorkItemStatus as _Status
        from confflow.remote.result_bundle import ResultBundleError, package_result_bundle
        from tests.v4.test_v43_coverage import _compile as _compile_doc
        from tests.v4.test_v43_coverage import _document as _make_doc
        from tests.v4.test_v43_coverage import _items as _assemble_items
        from tests.v4.test_v43_coverage import _result as _make_result

        compiled = _compile_doc(_make_doc())
        (item,) = _assemble_items(compiled, 1)
        transfer = tmp_path / "workdir"
        transfer.mkdir()
        handoff = _handoff([_struct_entry()])

        def _with_artifact(checksum: str, name: str = "ghost.log") -> Any:
            return _dc.replace(
                _make_result(item, status=_Status.COMPLETED),
                artifacts=_ArtifactSet.of(
                    ArtifactRef(
                        id="g1",
                        role="native_output",
                        locator=ArtifactLocator.run_relative(f"steps/s_opt/{name}"),
                        checksum=checksum,
                    )
                ),
            )

        with pytest.raises(ResultBundleError):
            package_result_bundle(
                work_item_result=_with_artifact(DIGEST_A),
                handoff=handoff,
                result_dir=str(tmp_path / "r1"),
                environment={},
                transfer_files_from=str(transfer),
            )
        (transfer / "ghost.log").write_bytes(b"real-bytes")
        with pytest.raises(ResultBundleError):
            package_result_bundle(
                work_item_result=_with_artifact(DIGEST_A),
                handoff=handoff,
                result_dir=str(tmp_path / "r2"),
                environment={},
                transfer_files_from=str(transfer),
            )
        blob = transfer / "real.log"
        blob.write_bytes(b"real-bytes")
        good = _dc.replace(
            _make_result(item, status=_Status.COMPLETED),
            artifacts=_ArtifactSet.of(
                ArtifactRef(
                    id="g1",
                    role="native_output",
                    locator=ArtifactLocator.run_relative("steps/s_opt/real.log"),
                    checksum="sha256:" + _hashlib.sha256(b"real-bytes").hexdigest(),
                )
            ),
        )
        path = package_result_bundle(
            work_item_result=good,
            handoff=handoff,
            result_dir=str(tmp_path / "r3"),
            environment={},
            transfer_files_from=str(transfer),
        )
        assert path.endswith("result.json")

    def test_read_validation_failure(self, tmp_path: Path) -> None:
        from confflow.remote.result_bundle import ResultBundleError, read_result_bundle

        target = tmp_path / "wrong.json"
        target.write_bytes(b'{"schema": "nope"}')
        os.chmod(target, 0o600)
        with pytest.raises(ResultBundleError):
            read_result_bundle(path=str(target))
        truncated = tmp_path / "truncated.json"
        truncated.write_bytes(b'{"schema": "confflow.control.worker-result.v2"')
        os.chmod(truncated, 0o600)
        with pytest.raises(ResultBundleError):
            read_result_bundle(path=str(truncated))


class TestWorkerMoreBranches:
    """Worker rebuild and packaging branches."""

    def test_result_entry_with_provenance(self, tmp_path: Path) -> None:
        from confflow.remote import worker as _worker
        from confflow.remote.envelope import ResultEntry
        from confflow.remote.envelope import bundle_entry_digest as _digest

        payload = {
            "kind": "energy",
            "value": 1.0,
            "unit": "hartree",
            "quantity": "energy",
            "subject_structure_id": "cov0",
            "provenance": {"program": "orca", "step_id": "s"},
        }
        entry = ResultEntry(
            result_id="energy:cov0", payload=payload, digest=_digest("result", payload)
        )
        record = _worker._result_from_entry(entry)
        assert record.provenance is not None
        assert record.provenance.program == "orca"

    def test_artifact_placeholder_and_roles(self, tmp_path: Path) -> None:
        from confflow.remote import worker as _worker
        from confflow.remote.envelope import ArtifactBundleEntry as _ArtifactEntry

        first = tmp_path / "first.chk"
        first.write_bytes(b"one")
        second = tmp_path / "second.log"
        second.write_bytes(b"two")
        entries = [
            _ArtifactEntry(
                artifact_id="chk",
                role="checkpoint",
                checksum="sha256:" + "0" * 64,
                subject_structure_id="cov0",
                bundle_locator="files/0001-chk",
            ),
            _ArtifactEntry(
                artifact_id="log",
                role="native_output",
                checksum="sha256:" + "0" * 64,
                subject_structure_id="cov0",
                bundle_locator="files/0002-log",
            ),
        ]
        staged = {"chk": str(first), "log": str(second)}
        grouped = _worker._artifacts_from_entries(
            entries, staged_bundle=staged, worker_root=str(tmp_path)
        )
        assert set(grouped) == {"checkpoint", "native_output"}
        assert grouped["checkpoint"][0].checksum is None

    def test_artifact_escape_rejected(self, tmp_path: Path) -> None:
        from confflow.remote import worker as _worker
        from confflow.remote.envelope import ArtifactBundleEntry as _ArtifactEntry

        entry = _ArtifactEntry(
            artifact_id="evil",
            role="checkpoint",
            checksum="sha256:" + "0" * 64,
            subject_structure_id="cov0",
            bundle_locator="files/0001-evil",
        )
        with pytest.raises(WorkerError):
            _worker._artifacts_from_entries(
                [entry],
                staged_bundle={"evil": "/etc/hostname"},
                worker_root=str(tmp_path),
            )

    def test_opaque_dir_holder(self, tmp_path: Path) -> None:
        from confflow.remote import worker as _worker
        from confflow.remote.envelope import ArtifactBundleEntry as _ArtifactEntry

        files_dir = tmp_path / "files"
        files_dir.mkdir()
        (files_dir / "0001-chk").write_bytes(b"bytes")
        entry = _ArtifactEntry(
            artifact_id="chk",
            role="checkpoint",
            checksum="sha256:" + "0" * 64,
            subject_structure_id="cov0",
            bundle_locator="files/0001-chk",
        )

        class _Holder:
            def __init__(self, directory: str) -> None:
                self.staging_dir = directory

        grouped = _worker._artifacts_from_entries(
            [entry], staged_bundle=_Holder(str(tmp_path)), worker_root=str(tmp_path)
        )
        assert grouped["checkpoint"][0].id == "chk"

    def test_rebuild_duplicate_structures(self, tmp_path: Path) -> None:
        handoff = _handoff([_struct_entry("dup"), _struct_entry("dup")])
        path, worker_root = _write_handoff(tmp_path, handoff)
        with pytest.raises(WorkerError):
            run_worker_envelope(
                handoff_path=path,
                staged_bundle={},
                worker_root=worker_root,
                launch_token="tok1",
            )


class TestHandoffMoreBranches:
    """Handoff reader: non-object JSON and run mismatch paths."""

    def test_expected_run_id_type_guards(self, tmp_path: Path) -> None:
        from confflow.remote.handoff import read_handoff_envelope

        path, _root = _write_handoff(tmp_path, _handoff([_struct_entry()]))
        with pytest.raises(HandoffError):
            read_handoff_envelope(path=path, expected_run_id="")
        with pytest.raises(HandoffError):
            read_handoff_envelope(path=path, expected_run_id=123)  # type: ignore[arg-type]
        with pytest.raises(HandoffError):
            read_handoff_envelope(path=path, expected_run_id="other-run")
        assert read_handoff_envelope(path=path, expected_run_id="run-1").run_id == "run-1"

    def test_fstat_and_group_branches(self, tmp_path: Path) -> None:
        import unittest.mock as _mock

        from confflow.remote.handoff import read_handoff_envelope

        path, _root = _write_handoff(tmp_path, _handoff([_struct_entry()]))
        with _mock.patch("os.fstat", side_effect=OSError("bad fd")):
            with pytest.raises(HandoffError):
                read_handoff_envelope(path=path)
        with _mock.patch("os.read", side_effect=OSError("stream reset")):
            with pytest.raises(HandoffError):
                read_handoff_envelope(path=path)


class TestLeaseMoreBranches:
    """Lease sibling liveness and marker content branches."""

    def test_empty_sibling_marker_skipped(self, tmp_path: Path) -> None:
        first = self._lease_at(tmp_path, launch_token="tok")
        assert first.acquire() is True
        step_dir = Path(first.path).parent
        (step_dir / "wi+s+a.attempt-1.empty.json").write_bytes(b"")
        rival = self._lease_at(tmp_path, launch_token="rival")
        assert rival.acquire() is False
        assert rival.previous_owner is not None
        rival.release()
        first.release()

    def test_non_dict_sibling_marker_skipped(self, tmp_path: Path) -> None:
        import fcntl as _fcntl

        first = self._lease_at(tmp_path, launch_token="tok")
        assert first.acquire() is True
        step_dir = Path(first.path).parent
        sibling = step_dir / "wi+s+a.attempt-1.list.json"
        sibling.write_bytes(b"[1, 2]")
        first.release()
        # An unlocked, non-dict sibling proves no live owner: skipped.
        freed = self._lease_at(tmp_path, launch_token="rival")
        assert freed.acquire() is True
        assert freed.previous_owner is None
        freed.release()
        # The same sibling with its lock held records an empty previous
        # owner instead of a structured one.
        held_fd = os.open(str(sibling), os.O_RDWR)
        try:
            _fcntl.flock(held_fd, _fcntl.LOCK_EX | _fcntl.LOCK_NB)
            blocked = self._lease_at(tmp_path, launch_token="rival2")
            assert blocked.acquire() is False
            assert blocked.previous_owner == {}
            blocked.release()
        finally:
            os.close(held_fd)

    def test_dead_sibling_marker_allows_claim(self, tmp_path: Path) -> None:
        import json as _json

        first = self._lease_at(tmp_path, launch_token="tok")
        assert first.acquire() is True
        step_dir = Path(first.path).parent
        (step_dir / "wi+s+a.attempt-1.dead.json").write_bytes(
            _json.dumps({"pid": 2**30, "pgid": 2**29}).encode("utf-8")
        )
        os.chmod(step_dir / "wi+s+a.attempt-1.dead.json", 0o600)
        rival = self._lease_at(tmp_path, launch_token="rival")
        # The dead sibling proves nothing live; acquisition still blocks on
        # the live first marker, but records nothing from the dead one.
        assert rival.acquire() is False
        first.release()
        freed = self._lease_at(tmp_path, launch_token="rival")
        assert freed.acquire() is True
        freed.release()

    def _lease_at(self, tmp_path: Path, **overrides: Any) -> AttemptLease:
        fields: dict[str, Any] = {
            "lease_root": str(tmp_path / "leases"),
            "run_id": "r",
            "step_id": "s",
            "work_item_id": "wi:s:a",
            "attempt_number": 1,
            "launch_token": "tok",
        }
        fields.update(overrides)
        return AttemptLease(**fields)


class TestSupervisionMoreBranches:
    """Supervision scan branches with scripted process tables."""

    def _fake_module(self, **kwargs: Any) -> Any:
        class _Denied(Exception):
            pass

        class _Gone(Exception):
            pass

        class _Fake:
            AccessDenied = _Denied
            NoSuchProcess = _Gone
            ZombieProcess = _Gone

            def __init__(self, procs: Any) -> None:
                self._procs = procs

            def process_iter(self, attrs: Any = None) -> Any:
                return list(self._procs)

        return _Fake(kwargs.get("procs", []))

    def test_denied_cwd_skipped(self, tmp_path: Path) -> None:
        import sys as _sys

        from confflow.remote import supervision as _supervision

        class _DeniedCwd(dict):
            def get(self, key: Any, default: Any = None) -> Any:
                if key == "cwd":
                    raise _supervision._maybe_import_psutil().AccessDenied("nope")
                return super().get(key, default)

        real_psutil = _sys.modules.get("psutil")
        assert real_psutil is not None

        class _Member:
            pid = 999981
            info = _DeniedCwd({"pid": 999981})

            def is_running(self) -> bool:
                return True

        fake = self._fake_module(procs=[_Member()])
        sentinel = _sys.modules.get("psutil")
        _sys.modules["psutil"] = fake  # type: ignore[assignment]
        try:
            ghost = OwnerIdentity(owner_token="t", pid=2**30)
            assert _supervision._workdir_holds_live_process(str(tmp_path), owner=ghost) is False
        finally:
            if sentinel is not None:
                _sys.modules["psutil"] = sentinel
            else:
                del _sys.modules["psutil"]

    def test_holder_in_parent_dir(self, tmp_path: Path) -> None:
        import subprocess as _subprocess
        import time as _time

        from confflow.remote import supervision as _supervision

        subdir = tmp_path / "sub"
        subdir.mkdir()
        helper = _subprocess.Popen(["/bin/sleep", "30"], cwd=str(subdir))
        try:
            _time.sleep(0.3)
            ghost = OwnerIdentity(owner_token="t", pid=2**30)
            assert _supervision._workdir_holds_live_process(str(tmp_path), owner=ghost) is True
        finally:
            helper.terminate()
            helper.wait(timeout=10)

    def test_kill_escalation_refused(self, tmp_path: Path) -> None:
        import subprocess as _subprocess
        import time as _time
        import unittest.mock as _mock

        helper = _subprocess.Popen(
            ["sh", "-c", "trap '' TERM; while true; do sleep 1; done"],
            start_new_session=True,
            stdout=_subprocess.DEVNULL,
            stderr=_subprocess.DEVNULL,
        )
        try:
            _time.sleep(0.5)
            owner = TestSupervisionEdges._sleep_owner(helper)
            real_killpg = os.killpg
            calls = {"count": 0}

            def _refuse_kill(pgid: int, sig: int) -> None:
                calls["count"] += 1
                if calls["count"] == 1:
                    return real_killpg(pgid, sig)
                raise OSError("audited refusal")

            with _mock.patch("os.killpg", side_effect=_refuse_kill):
                proof = cancel_attempt(owner=owner, work_dir=None, grace_seconds=1.0)
            # TERM could not stop the immune loop; the refused SIGKILL
            # leaves cancellation unconfirmed while the group stays alive.
            assert proof.confirmed is False
            assert helper.poll() is None
        finally:
            helper.kill()
            helper.wait(timeout=10)


class TestBatchLeftoverBranches:
    """Durable-runner degraded-registration and preflight branches."""

    def test_corrupt_provenance_executes_fresh(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import sqlite3 as _sqlite3

        from confflow.execution.batch import BatchStepExecutor as _Batch
        from confflow.execution.process import NativeProcessSupervisor as _Supervisor
        from confflow.execution.work_item_executor import WorkItemExecutor as _Executor
        from tests.v4.test_v43_coverage import _compile as _compile_doc
        from tests.v4.test_v43_coverage import _document as _make_doc
        from tests.v4.test_v43_coverage import _items as _assemble_items

        compiled = _compile_doc(_make_doc())
        (item,) = _assemble_items(compiled, 1)
        run_root = str(tmp_path / "run")
        with SqliteWorkItemStore.open(store_path(run_root, STEP_ID)) as store:
            store.register_item(
                work_item_id=item.id,
                logical_key=item.logical_key,
                step_id=item.step_id,
                work_item_digest=item.semantic_digest,
                step_semantic_digest=DIGEST_B,
            )
            connection = _sqlite3.connect(store.path)
            connection.execute(
                "UPDATE items SET producer_provenance = '{\"a\": 1}' WHERE work_item_id = ?",
                (item.id,),
            )
            connection.commit()
            connection.close()
            request = _request(compiled, (item,), run_root)
            batch = _Batch(_Executor()).with_supervisor(_Supervisor())
            monkeypatch.setenv("FAKE_MODE", "success_opt")
            result = batch.execute_step_resumable(
                request, store=store, run_root=run_root, owner_token="ctl"
            )
            assert result.status is StepStatus.COMPLETED

    def test_cancelled_without_stored_result_carried(self, tmp_path: Path) -> None:
        from confflow.execution.batch import BatchStepExecutor as _Batch
        from confflow.execution.process import NativeProcessSupervisor as _Supervisor
        from confflow.execution.work_item_executor import WorkItemExecutor as _Executor
        from tests.v4.test_v43_coverage import _compile as _compile_doc
        from tests.v4.test_v43_coverage import _document as _make_doc
        from tests.v4.test_v43_coverage import _items as _assemble_items

        compiled = _compile_doc(_make_doc())
        (item,) = _assemble_items(compiled, 1)
        run_root = str(tmp_path / "run")
        with SqliteWorkItemStore.open(store_path(run_root, STEP_ID)) as store:
            store.register_item(
                work_item_id=item.id,
                logical_key=item.logical_key,
                step_id=item.step_id,
                work_item_digest=item.semantic_digest,
                step_semantic_digest=DIGEST_B,
            )
            store.claim(item.id, owner=OwnerIdentity(owner_token="ctl"))
            store.cancel_item(item.id, reason="operator stop")
            request = _request(compiled, (item,), run_root)
            batch = _Batch(_Executor()).with_supervisor(_Supervisor())
            result = batch.execute_step_resumable(
                request, store=store, run_root=run_root, owner_token="ctl2"
            )
            assert result.status is StepStatus.CANCELLED
            assert result.item_results[0].status is WorkItemStatus.CANCELLED


class _FakeMonkeypatch:
    """Minimal monkeypatch stand-in for installing the counting wrapper."""

    def setenv(self, key: str, value: str) -> None:
        os.environ[key] = value


# ---------------------------------------------------------------------------
# Round 4: rebuild helpers, secure reads, supervision doubles, leftovers
# ---------------------------------------------------------------------------


class TestRebuildHelpers:
    """Pure staging rebuild helpers fail closed on misshaped payloads."""

    def test_require_mapping(self) -> None:
        from confflow.remote import staging as _staging

        assert _staging._require_mapping({"a": 1}, "test") == {"a": 1}
        with pytest.raises(StagingError):
            _staging._require_mapping([1], "test")

    def test_build_structure_set(self) -> None:
        from confflow.remote import staging as _staging

        assert len(_staging._build_structure_set(None, what="test")) == 0
        with pytest.raises(StagingError):
            _staging._build_structure_set({}, what="test")
        with pytest.raises(StagingError):
            _staging._build_structure_set(["x"], what="test")
        good = dict(structure("s0").to_dict())
        assert len(_staging._build_structure_set([good], what="test")) == 1
        bad = dict(good)
        bad["atoms"] = ["Xx"]
        with pytest.raises(StagingError):
            _staging._build_structure_set([bad], what="test")
        with pytest.raises(StagingError):
            _staging._build_structure_set([good, good], what="test")

    def test_build_result_set(self) -> None:
        from confflow.remote import staging as _staging

        assert len(_staging._build_result_set(None, what="test")) == 0
        with pytest.raises(StagingError):
            _staging._build_result_set({}, what="test")
        with pytest.raises(StagingError):
            _staging._build_result_set(["x"], what="test")
        good = {"kind": "energy", "value": 1.0, "unit": "hartree"}
        assert len(_staging._build_result_set([good], what="test")) == 1
        with pytest.raises(StagingError):
            _staging._build_result_set(
                [{"kind": "energy", "value": 1.0, "unit": "bogus"}], what="test"
            )
        with pytest.raises(StagingError):
            _staging._build_result_set(
                [{"kind": "energy", "value": 1.0, "provenance": []}], what="test"
            )
        # Unknown provenance keys are dropped (forward-compatible leniency);
        # shape violations still fail closed.
        assert (
            len(
                _staging._build_result_set(
                    [{"kind": "energy", "value": 1.0, "provenance": {"bogus": 1}}],
                    what="test",
                )
            )
            == 1
        )
        with pytest.raises(StagingError):
            _staging._build_result_set([{"value": 1.0}], what="test")
        provenanced = dict(good)
        provenanced["provenance"] = {"program": "orca"}
        assert len(_staging._build_result_set([provenanced], what="test")) == 1
        with pytest.raises(StagingError):
            _staging._build_result_set(
                [{"kind": "energy", "value": 1.0, "provenance": {"program": 1}}],
                what="test",
            )

    def test_build_diagnostics(self) -> None:
        from confflow.remote import staging as _staging

        assert _staging._build_diagnostics(None, what="test") == ()
        with pytest.raises(StagingError):
            _staging._build_diagnostics({}, what="test")
        with pytest.raises(StagingError):
            _staging._build_diagnostics(["x"], what="test")
        with pytest.raises(StagingError):
            _staging._build_diagnostics(
                [{"code": "x", "message": "y", "severity": "bogus"}], what="test"
            )
        good = {"code": "x", "message": "y"}
        assert len(_staging._build_diagnostics([good], what="test")) == 1

    def test_symlink_worker_file_rejected(self, tmp_path: Path) -> None:
        handoff = _handoff([_struct_entry()])
        result_path = _bundle_file(tmp_path, handoff)
        result_dir = str(Path(result_path).parent)
        (Path(result_dir) / "files" / "0001-out.chk").unlink()
        os.symlink("/etc/hostname", str(Path(result_dir) / "files" / "0001-out.chk"))
        store = _claimed_store(tmp_path, handoff)
        try:
            from confflow.remote.staging import import_result_artifacts

            with pytest.raises(StagingError):
                import_result_artifacts(
                    result_path=result_path,
                    handoff=handoff,
                    run_root=str(tmp_path / "run"),
                    store=store,
                )
        finally:
            store.close()

    def test_bare_locator_fallback(self, tmp_path: Path) -> None:
        from confflow.remote import staging as _staging

        result_dir = tmp_path / "rdir"
        files_dir = result_dir / "files"
        files_dir.mkdir(parents=True)
        (files_dir / "plain.chk").write_bytes(b"bytes")
        resolved = _staging._resolve_worker_file(str(result_dir), "plain.chk")
        assert resolved.endswith(os.path.join("files", "plain.chk"))

    def test_full_payload_import(self, tmp_path: Path) -> None:
        import json as _json

        from confflow.remote.envelope import ResultBundle

        handoff = _handoff([_struct_entry()])
        result_path = _bundle_file(tmp_path, handoff)
        payload = _json.loads(Path(result_path).read_bytes().decode("utf-8"))
        payload["result"]["structures"] = [dict(structure("s9").to_dict())]
        payload["result"]["results"] = [{"kind": "energy", "value": -1.0, "unit": "hartree"}]
        payload["result"]["diagnostics"] = [{"code": "x", "message": "y"}]
        # Re-sign after mutation: only the digest gate may reject, never the shape.
        payload.pop("bundle_digest", None)
        bundle = ResultBundle.new(**payload)
        Path(result_path).write_bytes(bundle.model_dump_json().encode("utf-8"))
        store = _claimed_store(tmp_path, handoff)
        try:
            from confflow.remote.staging import import_result_artifacts

            imported = import_result_artifacts(
                result_path=result_path,
                handoff=handoff,
                run_root=str(tmp_path / "run"),
                store=store,
            )
            assert len(imported.structures) == 1
            assert len(imported.results) == 1
            assert len(imported.diagnostics) == 1
        finally:
            store.close()


class TestSupervisionDoubles:
    """Scripted process tables for the workdir scan branches."""

    def _module(self, **kwargs: Any) -> Any:
        class _Denied(Exception):
            pass

        class _Gone(Exception):
            pass

        class _Fake:
            AccessDenied = _Denied
            NoSuchProcess = _Gone
            ZombieProcess = _Gone

            def __init__(self, procs: Any) -> None:
                self._procs = procs

            def process_iter(self, attrs: Any = None) -> Any:
                if kwargs.get("raise_iter"):
                    raise RuntimeError("iter exploded")
                return list(self._procs)

        return _Fake(kwargs.get("procs", []))

    def _scan(self, tmp_path: Path, module: Any, owner: Any) -> Any:
        import sys as _sys

        from confflow.remote import supervision as _supervision

        sentinel = _sys.modules.get("psutil")
        _sys.modules["psutil"] = module  # type: ignore[assignment]
        try:
            return _supervision._workdir_holds_live_process(str(tmp_path), owner=owner)
        finally:
            if sentinel is not None:
                _sys.modules["psutil"] = sentinel
            else:
                del _sys.modules["psutil"]

    def test_iter_explosion_blocks(self, tmp_path: Path) -> None:
        ghost = OwnerIdentity(owner_token="t", pid=2**30)
        assert self._scan(tmp_path, self._module(raise_iter=True), ghost) is True

    def test_info_explosion_skipped(self, tmp_path: Path) -> None:
        class _Weird:
            pid = 999971

            @property
            def info(self) -> Any:
                raise RuntimeError("info exploded")

            def is_running(self) -> bool:
                return True

        ghost = OwnerIdentity(owner_token="t", pid=2**30)
        assert self._scan(tmp_path, self._module(procs=[_Weird()]), ghost) is False

    def test_denied_info_skipped(self, tmp_path: Path) -> None:
        ghost = OwnerIdentity(owner_token="t", pid=2**30)
        assert self._scan(tmp_path, self._module(procs=[]), ghost) is False

    def test_zombie_member_ignored(self, tmp_path: Path) -> None:
        import sys as _sys

        real = _sys.modules.get("psutil")
        assert real is not None

        class _Zombie:
            pid = 999972
            info = {"pid": 999972, "status": real.STATUS_ZOMBIE}

            def is_running(self) -> bool:
                return True

        ghost = OwnerIdentity(owner_token="t", pid=2**30)
        assert self._scan(tmp_path, self._module(procs=[_Zombie()]), ghost) is False

    def test_exited_member_ignored(self, tmp_path: Path) -> None:
        class _Exited:
            pid = 999973
            info = {"pid": 999973, "status": "running"}

            def is_running(self) -> bool:
                return False

        ghost = OwnerIdentity(owner_token="t", pid=2**30)
        assert self._scan(tmp_path, self._module(procs=[_Exited()]), ghost) is False

    def test_unreadable_status_blocks(self, tmp_path: Path) -> None:
        class _Opaque:
            pid = 999974
            info = {"pid": 999974}

            def status(self) -> Any:
                raise RuntimeError("denied")

            def is_running(self) -> bool:
                return True

        # An unreadable status counts as a survivor only when the candidate
        # is actually in a recorded group; with real group tables a fake
        # pid never matches, so nothing is counted: deterministic False.
        # (A foreign pgid forces the scan path: our own group would answer
        # True at the killpg probe without ever scanning.)
        ghost = OwnerIdentity(owner_token="t", pid=2**30, process_group_id=2**20)
        assert self._scan(tmp_path, self._module(procs=[_Opaque()]), ghost) is False

    def test_killpg_gone_then_dead_confirms(self, tmp_path: Path) -> None:
        import subprocess as _subprocess
        import unittest.mock as _mock

        helper = _subprocess.Popen(["/bin/sleep", "30"], start_new_session=True)
        try:
            import time as _time

            _time.sleep(0.3)
            owner = TestSupervisionEdges._sleep_owner(helper)
            from confflow.remote import supervision as _supervision

            with _mock.patch.object(
                _supervision,
                "_reconcile_now",
                side_effect=[
                    OwnerVerdict.DEFINITELY_ALIVE,
                    OwnerVerdict.DEFINITELY_DEAD,
                ],
            ):
                with _mock.patch("os.killpg", side_effect=ProcessLookupError("gone")):
                    proof = cancel_attempt(owner=owner, work_dir=None, grace_seconds=1.0)
            assert proof.confirmed is True
            assert proof.verdict is OwnerVerdict.DEFINITELY_DEAD
        finally:
            helper.terminate()
            helper.wait(timeout=10)

    def test_kill_refused_after_term(self, tmp_path: Path) -> None:
        import unittest.mock as _mock

        from confflow.remote import supervision as _supervision

        ghost = OwnerIdentity(owner_token="t", pid=2**30)
        with _mock.patch.object(
            _supervision, "_reconcile_now", return_value=OwnerVerdict.DEFINITELY_ALIVE
        ):
            with _mock.patch("os.killpg", side_effect=OSError("audited")):
                proof = cancel_attempt(owner=ghost, work_dir=None, grace_seconds=1.0)
        assert proof.confirmed is False


class TestWorkerReaderAdaptation:
    """Alternate reader shapes behind the worker entry point."""

    def test_path_only_reader(self, tmp_path: Path) -> None:
        import unittest.mock as _mock

        handoff = _handoff([_struct_entry()])
        path, worker_root = _write_handoff(tmp_path, handoff)

        def _path_only_reader(handoff_path: str) -> Any:
            from confflow.remote.handoff import read_handoff_envelope

            return read_handoff_envelope(path=handoff_path)

        with _mock.patch(
            "confflow.remote.handoff.read_handoff_envelope", side_effect=_path_only_reader
        ):
            with pytest.raises(WorkerError):
                # Reader shape adapts, but the token still mismatches.
                run_worker_envelope(
                    handoff_path=path,
                    staged_bundle={},
                    worker_root=worker_root,
                    launch_token="wrong-token",
                )


class TestLeaseAcquireBranches:
    """Lease acquisition internals."""

    def test_mutex_serializes_claims(self, tmp_path: Path) -> None:
        first = AttemptLease(
            lease_root=str(tmp_path / "leases"),
            run_id="r",
            step_id="s",
            work_item_id="wi:s:a",
            attempt_number=1,
            launch_token="tok",
        )
        assert first.acquire() is True
        assert (Path(first.path).parent / "wi+s+a.attempt-1.mutex").is_file()
        first.release()

    def test_unreadable_step_dir_proceeds(self, tmp_path: Path) -> None:
        # A missing step dir is created privately; acquisition succeeds.
        lease = AttemptLease(
            lease_root=str(tmp_path / "leases"),
            run_id="r",
            step_id="fresh",
            work_item_id="wi:s:a",
            attempt_number=1,
            launch_token="tok",
        )
        assert lease.acquire() is True
        lease.release()
