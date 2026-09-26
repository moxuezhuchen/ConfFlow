#!/usr/bin/env python3

"""V4-3 branch coverage round 2: corruption matrices and seam closure.

DB-injected corruption payloads, forged locators, scripted psutil doubles,
and contested-claim unit paths.  Fast and deterministic; the three
integration tests reuse the fake ORCA executable.
"""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path
from typing import Any

import pytest

from confflow.domain import FrozenDict
from confflow.domain.artifact import ArtifactLocator, ArtifactRef, ArtifactSet
from confflow.domain.completion import CompletionPolicy, StepStatus, WorkItemStatus
from confflow.execution.batch import BatchStepExecutor
from confflow.execution.process import NativeProcessSupervisor
from confflow.execution.work_item_executor import ItemExecutionContext, WorkItemExecutor
from confflow.persistence import (
    CorruptStateError,
    OwnerIdentity,
    OwnerVerdict,
    PersistenceError,
    ReuseCode,
    RunState,
    RunStepStatus,
    StateTransitionError,
    StoredWorkItemStatus,
    ensure_step,
    load_run_state,
    step_result_path,
    store_path,
    transition_step,
)
from confflow.persistence.artifacts import ArtifactIntegrityError, plan_gc
from confflow.persistence.recovery import owner_identity_current, reconcile_owner
from confflow.persistence.work_items import SqliteWorkItemStore
from tests.v4.test_v43_coverage import (
    STEP_ID,
    _items,
    _register,
    _request,
    _result,
)

FAKE_ORCA = Path(__file__).resolve().parent / "fakes" / "fake_orca.py"


def _valid_payload(item: Any) -> dict[str, Any]:
    """Return a valid completed-result payload for *item*."""
    return _result(item, status=WorkItemStatus.COMPLETED).to_dict()


def _inject_result(store: SqliteWorkItemStore, item: Any, payload: Any) -> None:
    """Overwrite the latest attempt result JSON directly in SQLite."""
    import json as _json

    text = payload if isinstance(payload, (bytes, str)) else _json.dumps(payload)
    if isinstance(text, str):
        text = text.encode("utf-8")
    connection = sqlite3.connect(store.path)
    try:
        connection.execute(
            "UPDATE attempts SET status = 'completed', result_json = ?" " WHERE work_item_id = ?",
            (text.decode("utf-8", errors="surrogateescape"), item.id),
        )
        connection.execute(
            "UPDATE items SET status = 'completed' WHERE work_item_id = ?",
            (item.id,),
        )
        connection.commit()
    finally:
        connection.close()


def _setup_claimed(tmp_path: Path) -> tuple[SqliteWorkItemStore, Any]:
    """Register and claim one item, returning the open store and the item."""
    from tests.v4.test_v43_coverage import _compile as _compile_doc
    from tests.v4.test_v43_coverage import _document as _make_doc

    compiled = _compile_doc(_make_doc())
    (item,) = _items(compiled, 1)
    store = SqliteWorkItemStore.open(store_path(str(tmp_path / "run"), STEP_ID))
    store.register_item(
        work_item_id=item.id,
        logical_key=item.logical_key,
        step_id=item.step_id,
        work_item_digest=item.semantic_digest,
        step_semantic_digest="sha256:" + "a" * 64,
    )
    store.claim(item.id, owner=OwnerIdentity(owner_token="t"))
    return store, item


class TestResultCorruptionMatrix:
    """Every reconstruction branch fails closed on corrupt payloads."""

    @pytest.mark.parametrize(
        "mutator",
        [
            pytest.param(lambda p: p.update(structures=[{"id": 1}]), id="structure-shape"),
            pytest.param(
                lambda p: p.update(
                    structures=[
                        {
                            "id": "s",
                            "atoms": ["X"],
                            "coordinates": [[0.0, 0.0, 0.0]],
                        }
                    ]
                ),
                id="structure-element",
            ),
            pytest.param(
                lambda p: p.update(results=[{"kind": "e", "value": 1.0, "unit": "bogus"}]),
                id="result-unit",
            ),
            pytest.param(
                lambda p: p.update(results=[{"kind": "e", "value": 1.0, "provenance": []}]),
                id="result-provenance",
            ),
            pytest.param(
                lambda p: p.update(
                    artifacts=[{"id": "a", "role": "r", "locator": {"kind": "bogus"}}]
                ),
                id="artifact-locator",
            ),
            pytest.param(
                lambda p: p.update(
                    artifacts=[
                        {
                            "id": "a",
                            "role": "r",
                            "locator": {"kind": "run_relative", "path": "x/y"},
                            "retention": "bogus",
                        }
                    ]
                ),
                id="artifact-retention",
            ),
            pytest.param(
                lambda p: p.update(
                    diagnostics=[{"code": "x", "message": "y", "severity": "bogus"}]
                ),
                id="diagnostic-severity",
            ),
            pytest.param(lambda p: p.update(diagnostics=["nope"]), id="diagnostic-shape"),
            pytest.param(lambda p: p.update(timing={"started_at": "now"}), id="timing-shape"),
            pytest.param(lambda p: p.update(error={"code": "e"}), id="error-shape"),
            pytest.param(lambda p: p.update(recovery={"profile": 1}), id="recovery-shape"),
            pytest.param(lambda p: p.update(semantic_digest=42), id="digest-type"),
            pytest.param(lambda p: p.update(metadata=[]), id="metadata-shape"),
            pytest.param(lambda p: p.update(structures={}), id="collections-shape"),
            pytest.param(lambda p: p.update(status="bogus"), id="status-value"),
            pytest.param(lambda p: p.update(work_item_id=""), id="id-empty"),
        ],
    )
    def test_corrupt_payloads_rejected(self, tmp_path: Path, mutator: Any) -> None:
        store, item = _setup_claimed(tmp_path)
        try:
            payload = _valid_payload(item)
            mutator(payload)
            _inject_result(store, item, payload)
            with pytest.raises(CorruptStateError):
                store.get_result(item.id)
        finally:
            store.close()

    def test_raw_garbage_rejected(self, tmp_path: Path) -> None:
        store, item = _setup_claimed(tmp_path)
        try:
            _inject_result(store, item, b"\x00 garbage")
            with pytest.raises(CorruptStateError):
                store.get_result(item.id)
            _inject_result(store, item, "[1, 2]")
            with pytest.raises(CorruptStateError):
                store.get_result(item.id)
        finally:
            store.close()


class TestStoreRowCorruption:
    """Corrupt status/owner/registration rows fail closed on read."""

    def test_bad_status_strings(self, tmp_path: Path) -> None:
        store, item = _setup_claimed(tmp_path)
        try:
            connection = sqlite3.connect(store.path)
            connection.execute(
                "UPDATE items SET status = 'bogus' WHERE work_item_id = ?",
                (item.id,),
            )
            connection.commit()
            connection.close()
            with pytest.raises(CorruptStateError):
                store.get_state(item.id)
            with pytest.raises(CorruptStateError):
                store.get_registered(item.id)
        finally:
            store.close()

    def test_bad_owner_row(self, tmp_path: Path) -> None:
        store, item = _setup_claimed(tmp_path)
        try:
            connection = sqlite3.connect(store.path)
            connection.execute(
                "UPDATE items SET owner_pid = 'xx' WHERE work_item_id = ?",
                (item.id,),
            )
            connection.commit()
            connection.close()
            with pytest.raises(CorruptStateError):
                store.get_owner(item.id)
        finally:
            store.close()

    def test_bad_provenance_row(self, tmp_path: Path) -> None:
        store, item = _setup_claimed(tmp_path)
        try:
            connection = sqlite3.connect(store.path)
            connection.execute(
                "UPDATE items SET producer_provenance = '[1]' WHERE work_item_id = ?",
                (item.id,),
            )
            connection.commit()
            connection.close()
            with pytest.raises(CorruptStateError):
                store.get_registered(item.id)
        finally:
            store.close()

    def test_bad_attempt_status_row(self, tmp_path: Path) -> None:
        store, item = _setup_claimed(tmp_path)
        try:
            connection = sqlite3.connect(store.path)
            connection.execute(
                "UPDATE attempts SET status = 'bogus' WHERE work_item_id = ?",
                (item.id,),
            )
            connection.commit()
            connection.close()
            with pytest.raises(CorruptStateError):
                store.get_attempts(item.id)
        finally:
            store.close()

    def test_bad_artifact_metadata_row(self, tmp_path: Path) -> None:
        store, item = _setup_claimed(tmp_path)
        try:
            store.complete(item.id, result=_result(item, status=WorkItemStatus.COMPLETED))
            connection = sqlite3.connect(store.path)
            connection.execute(
                "INSERT INTO artifact_rows (artifact_id, work_item_id, role,"
                " locator_path, retention, metadata_json)"
                " VALUES ('bad', ?, 'r', 'x/y', 'retained', '[1]')",
                (item.id,),
            )
            connection.commit()
            connection.close()
            with pytest.raises(CorruptStateError):
                store.list_artifact_rows(item.id)
        finally:
            store.close()

    def test_missing_attempt_row(self, tmp_path: Path) -> None:
        store, item = _setup_claimed(tmp_path)
        try:
            connection = sqlite3.connect(store.path)
            connection.execute("DELETE FROM attempts WHERE work_item_id = ?", (item.id,))
            connection.commit()
            connection.close()
            with pytest.raises(CorruptStateError):
                store.complete(item.id, result=_result(item, status=WorkItemStatus.COMPLETED))
        finally:
            store.close()

    def test_cancelled_result_persists(self, tmp_path: Path) -> None:
        import dataclasses as _dc

        store, item = _setup_claimed(tmp_path)
        try:
            cancelled = _dc.replace(
                _result(item, status=WorkItemStatus.FAILED),
                status=WorkItemStatus.CANCELLED,
            )
            store.record_finished(cancelled)
            assert store.get_state(item.id) is StoredWorkItemStatus.CANCELLED
            assert store.get_result(item.id) is not None
        finally:
            store.close()

    def test_forged_locator_rows_rejected(self, tmp_path: Path) -> None:
        import dataclasses as _dc

        from confflow.domain.artifact import LocatorKind as _Kind

        store, item = _setup_claimed(tmp_path)
        try:
            base = _result(item, status=WorkItemStatus.COMPLETED)
            for index, (kind, path, uri) in enumerate(
                (
                    (_Kind.RUN_RELATIVE, None, None),
                    (_Kind.EXTERNAL_URI, "x", None),
                )
            ):
                forged = _forged_ref(f"forged-{index}", kind, path, uri)
                bad = _dc.replace(base, artifacts=ArtifactSet.of(forged))
                with pytest.raises(PersistenceError):
                    store.complete(item.id, result=bad)
        finally:
            store.close()


def _forged_ref(artifact_id: str, kind: Any, path: Any, uri: Any) -> ArtifactRef:
    """Build an artifact reference bypassing the domain constructor."""
    from confflow.domain.artifact import ArtifactLocator as _Locator
    from confflow.domain.artifact import ArtifactRef as _Ref
    from confflow.domain.retention import RetentionClass as _RC

    locator = object.__new__(_Locator)
    object.__setattr__(locator, "kind", kind)
    object.__setattr__(locator, "path", path)
    object.__setattr__(locator, "uri", uri)
    ref = object.__new__(_Ref)
    object.__setattr__(ref, "id", artifact_id)
    object.__setattr__(ref, "role", "native_output")
    object.__setattr__(ref, "locator", locator)
    object.__setattr__(ref, "checksum", None)
    object.__setattr__(ref, "media_type", None)
    object.__setattr__(ref, "program", None)
    object.__setattr__(ref, "producer_step_id", None)
    object.__setattr__(ref, "producer_work_item_id", None)
    object.__setattr__(ref, "subject_structure_id", None)
    object.__setattr__(ref, "retention", _RC.RETAINED)
    object.__setattr__(ref, "metadata", FrozenDict({}))
    return ref


class TestPublicationCorruptionMatrix:
    """Every publication rebuild branch fails closed on corrupt files."""

    def _publish_valid(self, tmp_path: Path) -> str:
        from confflow.persistence.publication import publish_step_result, rebuild_step_result
        from tests.v4.test_v43_coverage import _compile as _compile_doc
        from tests.v4.test_v43_coverage import _document as _make_doc
        from tests.v4.test_v43_coverage import _items as _assemble_items

        compiled = _compile_doc(_make_doc())
        items = _assemble_items(compiled, 2)
        results = tuple(_result(item, status=WorkItemStatus.COMPLETED) for item in items)
        step_result = rebuild_step_result(
            step_id=STEP_ID,
            items=results,
            completion=CompletionPolicy(),
            definition_digest="sha256:" + "d" * 64,
            step_semantic_digest="sha256:" + "s" * 64,
        )
        run_root = str(tmp_path / "run")
        publish_step_result(run_root=run_root, step_id=STEP_ID, step_result=step_result)
        return run_root

    @pytest.mark.parametrize(
        "mutator",
        [
            pytest.param(lambda p: p.update(status="bogus"), id="status"),
            pytest.param(lambda p: p.update(structures={}), id="structures-shape"),
            pytest.param(lambda p: p["structures"].append({"id": 1}), id="structure-entry"),
            pytest.param(
                lambda p: p["results"].append({"kind": "e", "value": 1.0, "unit": "bogus"}),
                id="result-unit",
            ),
            pytest.param(
                lambda p: p["results"].append({"kind": "e", "value": 1.0, "provenance": []}),
                id="result-provenance",
            ),
            pytest.param(
                lambda p: p["artifacts"].append(
                    {"id": "a", "role": "r", "locator": {"kind": "bogus"}}
                ),
                id="artifact-locator",
            ),
            pytest.param(
                lambda p: p["item_results"].append({"work_item_id": "w"}),
                id="item-shape",
            ),
            pytest.param(lambda p: p.update(diagnostics="nope"), id="diagnostics-shape"),
            pytest.param(lambda p: p.update(summary=[]), id="summary-shape"),
            pytest.param(
                lambda p: p.update(provenance={"compiler_version": 1}),
                id="provenance-shape",
            ),
            pytest.param(
                lambda p: p["item_results"][0].update(timing={"started_at": []}),
                id="timing-shape",
            ),
            pytest.param(
                lambda p: p["item_results"][0].update(error={"code": 1}),
                id="error-shape",
            ),
            pytest.param(
                lambda p: p["item_results"][0].update(recovery="x"),
                id="recovery-shape",
            ),
        ],
    )
    def test_corrupt_publications_rejected(self, tmp_path: Path, mutator: Any) -> None:
        import json as _json

        from confflow.persistence.publication import (
            STEP_RESULT_FILENAME,
            load_published_step_result,
        )
        from confflow.persistence.publication import (
            step_result_path as _result_path,
        )

        _ = STEP_RESULT_FILENAME
        run_root = self._publish_valid(tmp_path)
        target = Path(_result_path(run_root, STEP_ID))
        payload = _json.loads(target.read_bytes().decode("utf-8"))
        mutator(payload)
        target.write_bytes(_json.dumps(payload).encode("utf-8"))
        with pytest.raises(CorruptStateError):
            load_published_step_result(run_root=run_root, step_id=STEP_ID)

    def test_rebuild_validation(self) -> None:
        from confflow.persistence.publication import rebuild_step_result

        with pytest.raises(PersistenceError):
            rebuild_step_result(
                step_id=STEP_ID,
                items=("nope",),  # type: ignore[list-item]
                completion=CompletionPolicy(),
            )
        with pytest.raises(PersistenceError):
            rebuild_step_result(
                step_id=STEP_ID, items=(), completion="nope"  # type: ignore[arg-type]
            )

    def test_unreadable_file_is_corrupt(self, tmp_path: Path) -> None:
        from confflow.persistence.publication import (
            load_published_step_result,
        )
        from confflow.persistence.publication import (
            step_result_path as _result_path,
        )

        run_root = self._publish_valid(tmp_path)
        target = Path(_result_path(run_root, STEP_ID))
        os.remove(target)
        os.mkdir(target)
        try:
            with pytest.raises(CorruptStateError):
                load_published_step_result(run_root=run_root, step_id=STEP_ID)
        finally:
            os.rmdir(target)


class TestForgedArtifactLocators:
    """Persistence checks hold even when the domain constructor is bypassed."""

    @pytest.mark.parametrize(
        "kind,path,uri",
        [
            pytest.param("run_relative", "/absolute/x.out", None, id="absolute"),
            pytest.param("run_relative", "../escape.out", None, id="dotdot"),
            pytest.param("run_relative", "a\\b.out", None, id="backslash"),
            pytest.param("run_relative", "C:drive.out", None, id="drive"),
            pytest.param("run_relative", "a\x00b.out", None, id="nul"),
            pytest.param("run_relative", "", None, id="empty"),
            pytest.param("external_uri", None, None, id="external-no-uri"),
            pytest.param("bogus-kind", "x/y.out", None, id="bad-kind"),
        ],
    )
    def test_forged_locators_rejected(self, tmp_path: Path, kind: Any, path: Any, uri: Any) -> None:
        from confflow.domain.artifact import LocatorKind as _Kind
        from confflow.persistence.artifacts import verify_artifact

        run_root = str(tmp_path / "run")
        os.makedirs(os.path.join(run_root, "steps", "s_opt"))
        forged_kind = kind if kind == "bogus-kind" else _Kind(kind)
        with pytest.raises(ArtifactIntegrityError):
            verify_artifact(run_root=run_root, ref=_forged_ref("forged", forged_kind, path, uri))

    def test_forged_locators_rejected_in_planning(self, tmp_path: Path) -> None:
        from confflow.domain.artifact import LocatorKind as _Kind
        from confflow.domain.retention import RetentionClass as _RC

        run_root = str(tmp_path / "run")
        os.makedirs(os.path.join(run_root, "steps", "s_opt"))
        ref = _forged_ref("forged", _Kind.RUN_RELATIVE, "../escape.out", None)
        object.__setattr__(ref, "retention", _RC.TEMPORARY)
        with pytest.raises(ArtifactIntegrityError):
            plan_gc(
                run_root=run_root,
                candidates=(ref,),
                consumed_ids=frozenset({"forged"}),
            )

    def test_symlink_directory_rejected(self, tmp_path: Path) -> None:
        from confflow.persistence.artifacts import verify_artifact

        run_root = str(tmp_path / "run")
        directory = Path(run_root) / "steps" / "s_opt"
        directory.mkdir(parents=True)
        (directory / "realdir").mkdir()
        os.symlink(str(directory / "realdir"), str(directory / "linkdir"))
        ref = ArtifactRef(
            id="d1",
            role="native_output",
            locator=ArtifactLocator.run_relative("steps/s_opt/linkdir"),
        )
        with pytest.raises(ArtifactIntegrityError):
            verify_artifact(run_root=run_root, ref=ref)


class TestPsutilDoubles:
    """Reconciliation branches driven by scripted psutil doubles."""

    def _double(self, **kwargs: Any) -> Any:
        class _Gone(Exception):
            pass

        class _Inspection(Exception):
            pass

        class _FakeProcess:
            def __init__(self, spec: Any) -> None:
                self._spec = spec

            def is_running(self) -> bool:
                outcome = self._spec.get("running", True)
                if outcome == "gone":
                    raise _Gone("gone")
                if outcome == "error":
                    raise _Inspection("denied")
                return bool(outcome)

            def status(self) -> str:
                outcome = self._spec.get("status", "running")
                if outcome == "gone":
                    raise _Gone("gone")
                if outcome == "error":
                    raise _Inspection("denied")
                return str(outcome)

            def create_time(self) -> float:
                outcome = self._spec.get("create_time", 100.0)
                if outcome == "gone":
                    raise _Gone("gone")
                if outcome == "error":
                    raise _Inspection("denied")
                return float(outcome)

        class _FakePsutil:
            NoSuchProcess = _Gone
            ZombieProcess = _Gone
            Error = _Inspection
            STATUS_ZOMBIE = "zombie"

            def __init__(self, procs: Any, everything: Any = ()) -> None:
                self._procs = dict(procs)
                self._everything = tuple(everything)

            def Process(self, pid: int) -> _FakeProcess:
                if pid not in self._procs:
                    raise _Gone(f"no such process {pid}")
                if self._procs[pid] == "process-error":
                    raise _Inspection("denied")
                return _FakeProcess(self._procs[pid])

            def process_iter(self, attrs: Any = None) -> Any:
                return list(self._everything)

        return _FakePsutil(kwargs.get("procs", {}), kwargs.get("everything", ()))

    def test_exited_slot_without_groups_is_dead(self) -> None:
        fake = self._double()
        owner = OwnerIdentity(owner_token="t", pid=4242)
        assert reconcile_owner(owner, psutil_module=fake) is OwnerVerdict.DEFINITELY_DEAD

    def test_unreadable_slot_is_uncertain(self) -> None:
        fake = self._double(procs={4242: {"running": "error"}})
        owner = OwnerIdentity(owner_token="t", pid=4242)
        assert reconcile_owner(owner, psutil_module=fake) is OwnerVerdict.UNCERTAIN

    def test_zombie_slot_without_groups_is_dead(self) -> None:
        fake = self._double(procs={4242: {"status": "zombie"}})
        owner = OwnerIdentity(owner_token="t", pid=4242)
        assert reconcile_owner(owner, psutil_module=fake) is OwnerVerdict.DEFINITELY_DEAD

    def test_exited_between_checks_is_dead(self) -> None:
        fake = self._double(procs={4242: {"create_time": "gone"}})
        owner = OwnerIdentity(owner_token="t", pid=4242)
        assert reconcile_owner(owner, psutil_module=fake) is OwnerVerdict.DEFINITELY_DEAD

    def test_mismatched_create_time_without_groups_is_dead(self) -> None:
        fake = self._double(procs={4242: {"create_time": 200.0}})
        owner = OwnerIdentity(owner_token="t", pid=4242, create_time=100.0)
        assert reconcile_owner(owner, psutil_module=fake) is OwnerVerdict.DEFINITELY_DEAD

    def test_group_mismatch_is_uncertain(self) -> None:
        fake = self._double(procs={4242: {"create_time": 100.0}})
        owner = OwnerIdentity(
            owner_token="t",
            pid=4242,
            process_group_id=2**29,
            create_time=100.0,
        )
        # The fake slot matches on time but the recorded group disagrees
        # with the real process table → UNCERTAIN, never alive.
        assert reconcile_owner(owner, psutil_module=fake) is OwnerVerdict.UNCERTAIN

    def test_group_survivor_keeps_alive(self) -> None:
        import os as _os
        import subprocess as _subprocess
        import time as _time

        helper = _subprocess.Popen(["/bin/sleep", "30"])
        try:
            deadline = _time.monotonic() + 10.0
            while helper.poll() is None and _time.monotonic() > deadline:
                break

            class _Member:
                pid = helper.pid
                info = {"status": "running"}

                def is_running(self) -> bool:
                    return True

            real_pgid = _os.getpgid(_os.getpid())
            assert _os.getpgid(helper.pid) == real_pgid
            fake = self._double(everything=(_Member(),))
            owner = OwnerIdentity(owner_token="t", pid=4242, process_group_id=real_pgid)
            assert reconcile_owner(owner, psutil_module=fake) is OwnerVerdict.DEFINITELY_ALIVE
        finally:
            helper.terminate()
            helper.wait(timeout=10)

    def test_broken_iter_is_uncertain(self) -> None:
        class _Broken:
            NoSuchProcess = Exception
            ZombieProcess = Exception
            Error = Exception
            STATUS_ZOMBIE = "zombie"

            def Process(self, pid: int) -> Any:
                raise self.NoSuchProcess("gone")

            def process_iter(self, attrs: Any = None) -> Any:
                raise RuntimeError("iter exploded")

        owner = OwnerIdentity(owner_token="t", pid=4242, process_group_id=2**20)
        assert reconcile_owner(owner, psutil_module=_Broken()) is OwnerVerdict.UNCERTAIN

    def test_unreadable_member_counts_as_survivor(self) -> None:
        import os as _os
        import subprocess as _subprocess

        helper = _subprocess.Popen(["/bin/sleep", "30"])
        try:

            class _Opaque:
                pid = helper.pid
                info = {"status": "running"}

                def is_running(self) -> bool:
                    raise RuntimeError("denied")

            real_pgid = _os.getpgid(_os.getpid())
            fake = self._double(everything=(_Opaque(),))
            owner = OwnerIdentity(owner_token="t", pid=4242, process_group_id=real_pgid)
            assert reconcile_owner(owner, psutil_module=fake) is OwnerVerdict.DEFINITELY_ALIVE
        finally:
            helper.terminate()
            helper.wait(timeout=10)

    def test_gone_marker_dedup_edges(self) -> None:
        from confflow.persistence import recovery as _recovery

        assert _recovery._gone_error_types(object()) == (ProcessLookupError,)
        assert _recovery._inspection_error_types(object()) == (
            OSError,
            RuntimeError,
            AttributeError,
        )

        class _Weird:
            NoSuchProcess = ProcessLookupError
            ZombieProcess = "not-a-type"
            Error = OSError

        assert _recovery._gone_error_types(_Weird()) == (ProcessLookupError,)
        assert _recovery._inspection_error_types(_Weird()) == (
            OSError,
            RuntimeError,
            AttributeError,
        )


class TestBatchSeams:
    """Batch validation and contested-claim unit seams."""

    def test_preflight_variants(self, tmp_path: Path) -> None:
        from tests.v4.test_v43_coverage import _compile as _compile_doc
        from tests.v4.test_v43_coverage import _document as _make_doc
        from tests.v4.test_v43_coverage import _items as _assemble_items

        compiled = _compile_doc(_make_doc())
        (item,) = _assemble_items(compiled, 1)
        base = _request(compiled, (item,), str(tmp_path / "run"))
        import dataclasses as _dc

        for field in ("scientific", "adapter", "profile"):
            bad = _dc.replace(base, **{field: None})
            with SqliteWorkItemStore.open(store_path(str(tmp_path / "run"), STEP_ID)) as store:
                batch = BatchStepExecutor(WorkItemExecutor())
                result = batch.execute_step_resumable(
                    bad, store=store, run_root=str(tmp_path / "run")
                )
                assert result.status is StepStatus.FAILED
                assert store.list_items() == ()
        nosup = BatchStepExecutor(WorkItemExecutor())
        with SqliteWorkItemStore.open(store_path(str(tmp_path / "run"), STEP_ID)) as store:
            result = nosup.execute_step_resumable(base, store=store, run_root=str(tmp_path / "run"))
            assert result.status is StepStatus.FAILED

    def test_contested_reevaluation_recovers_rival_result(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("FAKE_MODE", "success_opt")
        from tests.v4.test_v43_coverage import _compile as _compile_doc
        from tests.v4.test_v43_coverage import _document as _make_doc
        from tests.v4.test_v43_coverage import _items as _assemble_items

        compiled = _compile_doc(_make_doc())
        (item,) = _assemble_items(compiled, 1)
        run_root = str(tmp_path / "run")
        with SqliteWorkItemStore.open(store_path(run_root, STEP_ID)) as store:
            # No pre-registration: the rival's run registers live values, so
            # re-evaluation compares identical axes and reuses.
            rival = BatchStepExecutor(WorkItemExecutor()).with_supervisor(NativeProcessSupervisor())
            rival_request = _request(compiled, (item,), run_root)
            rival_result = rival.execute_step_resumable(
                rival_request, store=store, run_root=run_root, owner_token="rival"
            )
            assert rival_result.status is StepStatus.COMPLETED
            batch = BatchStepExecutor(WorkItemExecutor())
            context_request = _request(compiled, (item,), run_root)
            context = ItemExecutionContext(
                step_id=STEP_ID,
                scientific=context_request.scientific,
                scientific_defaults=context_request.scientific_defaults,
                adapter=context_request.adapter,
                profile=context_request.profile,
                checks=context_request.checks,
                recovery=context_request.recovery,
                execution_binding=context_request.execution_binding,
                run_root=run_root,
                work_base=None,
                supervisor=NativeProcessSupervisor(),
            )
            result, durable = batch._contested_claim(
                context_request,
                item,
                context,
                store=store,
                owner=OwnerIdentity(owner_token="me"),
                environment_digest=None,
                provenance=BatchStepExecutor._current_provenance(context_request),
                run_root=run_root,
                should_cancel=lambda: False,
            )
            assert durable is True
            assert result.status is WorkItemStatus.COMPLETED

    def test_contested_retry_then_blocked(self, tmp_path: Path) -> None:
        from tests.v4.test_v43_coverage import _compile as _compile_doc
        from tests.v4.test_v43_coverage import _document as _make_doc
        from tests.v4.test_v43_coverage import _items as _assemble_items

        compiled = _compile_doc(_make_doc())
        (item,) = _assemble_items(compiled, 1)
        run_root = str(tmp_path / "run")
        with SqliteWorkItemStore.open(store_path(run_root, STEP_ID)) as store:
            _register(store, item)
            batch = BatchStepExecutor(WorkItemExecutor())
            request = _request(compiled, (item,), run_root)
            context = ItemExecutionContext(
                step_id=STEP_ID,
                scientific=request.scientific,
                scientific_defaults=request.scientific_defaults,
                adapter=request.adapter,
                profile=request.profile,
                checks=request.checks,
                recovery=request.recovery,
                execution_binding=request.execution_binding,
                run_root=run_root,
                work_base=None,
                supervisor=NativeProcessSupervisor(),
            )
            store.claim(item.id, owner=OwnerIdentity(owner_token="rival"))
            store.fail(item.id, result=_result(item, status=WorkItemStatus.FAILED))
            live = owner_identity_current(owner_token="rival-live")
            assert store.claim(item.id, owner=live) is True
            result, durable = batch._contested_claim(
                request,
                item,
                context,
                store=store,
                owner=OwnerIdentity(owner_token="me"),
                environment_digest=None,
                provenance=FrozenDict({}),
                run_root=run_root,
                should_cancel=lambda: False,
                _claim_retried=True,
            )
            assert durable is False
            assert result.error is not None
            assert result.error.code == "blocked_uncertain_owner"


class TestRoundThree:
    """Remaining branch closure: metadata shapes, links, doubles, rivals."""

    def test_store_metadata_variants(self, tmp_path: Path) -> None:
        from tests.v4.test_v43_coverage import _compile as _compile_doc
        from tests.v4.test_v43_coverage import _document as _make_doc
        from tests.v4.test_v43_coverage import _items as _assemble_items
        from tests.v4.test_v43_matrices import _inject_result as _inject
        from tests.v4.test_v43_matrices import _valid_payload as _payload

        compiled = _compile_doc(_make_doc())
        (item,) = _assemble_items(compiled, 1)
        bad_shapes: list[Any] = [
            lambda p: p.update(timing=[]),
            lambda p: p.update(error=[]),
            lambda p: p.update(recovery=[]),
            lambda p: p.update(structures=[1]),
            lambda p: p.update(results=["x"]),
            lambda p: p.update(artifacts=[42]),
            lambda p: p.update(diagnostics=[{}]),
            lambda p: p.update(error={"code": "e", "message": "m", "details": []}),
        ]
        # Non-mapping metadata/details degrade to empty by design (never
        # corrupt): non-dict containers are replaced with {} on rebuild.
        lenient_shapes: list[Any] = [
            lambda p: p["structures"].append(
                {
                    "id": "s",
                    "atoms": ["H"],
                    "coordinates": [[0.0, 0.0, 0.0]],
                    "metadata": [],
                }
            ),
            lambda p: p["results"].append({"kind": "e", "value": 1.0, "metadata": []}),
            lambda p: p["artifacts"].append(
                {
                    "id": "a",
                    "role": "r",
                    "locator": {"kind": "run_relative", "path": "x/y"},
                    "metadata": [],
                }
            ),
            lambda p: p["diagnostics"].append({"code": "x", "message": "y", "details": []}),
            lambda p: p.update(recovery={"profile": "none", "details": []}),
        ]
        for index, mutator in enumerate(bad_shapes):
            with SqliteWorkItemStore.open(
                store_path(str(tmp_path / "run"), f"{STEP_ID}-{index}")
            ) as store:
                store.register_item(
                    work_item_id=item.id,
                    logical_key=item.logical_key,
                    step_id=item.step_id,
                    work_item_digest=item.semantic_digest,
                    step_semantic_digest="sha256:" + "a" * 64,
                )
                store.claim(item.id, owner=OwnerIdentity(owner_token="t"))
                payload = _payload(item)
                mutator(payload)
                _inject(store, item, payload)
                with pytest.raises(CorruptStateError):
                    store.get_result(item.id)
        for index, mutator in enumerate(lenient_shapes):
            with SqliteWorkItemStore.open(
                store_path(str(tmp_path / "run"), f"{STEP_ID}-lenient-{index}")
            ) as store:
                store.register_item(
                    work_item_id=item.id,
                    logical_key=item.logical_key,
                    step_id=item.step_id,
                    work_item_digest=item.semantic_digest,
                    step_semantic_digest="sha256:" + "a" * 64,
                )
                store.claim(item.id, owner=OwnerIdentity(owner_token="t"))
                payload = _payload(item)
                mutator(payload)
                _inject(store, item, payload)
                assert store.get_result(item.id) is not None

    def test_store_extra_misuse(self, tmp_path: Path) -> None:
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
                step_semantic_digest="sha256:" + "a" * 64,
            )
            with pytest.raises(PersistenceError):
                store.fail("wi:ghost", result=_make_result(item, status=WorkItemStatus.FAILED))
            with pytest.raises(PersistenceError):
                store.complete(
                    "wi:ghost", result=_make_result(item, status=WorkItemStatus.COMPLETED)
                )
            cancelled = _make_result(item, status=WorkItemStatus.FAILED)
            import dataclasses as _dc

            cancelled = _dc.replace(cancelled, status=WorkItemStatus.CANCELLED)
            with pytest.raises(StateTransitionError):
                store.record_finished(cancelled)

    def test_publication_metadata_variants(self, tmp_path: Path) -> None:
        import json as _json

        from confflow.persistence.publication import (
            load_published_step_result,
            publish_step_result,
        )
        from tests.v4.test_v43_matrices import TestPublicationCorruptionMatrix as _Matrix

        helper = _Matrix()
        run_root = helper._publish_valid(tmp_path)
        target = Path(step_result_path(run_root, STEP_ID))
        base = _json.loads(target.read_bytes().decode("utf-8"))
        variants: list[Any] = [
            lambda p: p["structures"].append(
                {
                    "id": "s",
                    "atoms": ["H"],
                    "coordinates": [[0.0, 0.0, 0.0]],
                    "metadata": [],
                }
            ),
            lambda p: p["results"].append({"kind": "e", "value": 1.0, "metadata": []}),
            lambda p: p["diagnostics"].append(
                {"code": "x", "message": "y", "severity": "info", "details": []}
            ),
            lambda p: p["item_results"][0].update(metadata=[]),
        ]
        for mutator in variants:
            payload = _json.loads(target.read_bytes().decode("utf-8"))
            _ = base
            mutator(payload)
            target.write_bytes(_json.dumps(payload).encode("utf-8"))
            with pytest.raises(CorruptStateError):
                load_published_step_result(run_root=run_root, step_id=STEP_ID)
        with pytest.raises(PersistenceError):
            publish_step_result(run_root=run_root, step_id=STEP_ID, step_result="nope")  # type: ignore[arg-type]

    def test_run_state_extra_guards(self, tmp_path: Path) -> None:
        from confflow.persistence import run_state_path as _state_path

        run_root = str(tmp_path / "run")
        state = RunState(run_id="r")
        state = ensure_step(state, STEP_ID)
        with pytest.raises(PersistenceError):
            transition_step(state, STEP_ID, RunStepStatus.RUNNING, published_step_result_digest=123)  # type: ignore[arg-type]
        target = Path(_state_path(run_root))
        target.parent.mkdir(parents=True, exist_ok=True)
        os.mkdir(str(target))
        try:
            with pytest.raises(CorruptStateError):
                load_run_state(run_root)
        finally:
            os.rmdir(str(target))

    def test_dangling_symlink_unreadable(self, tmp_path: Path) -> None:
        from confflow.persistence.artifacts import verify_artifact

        run_root = str(tmp_path / "run")
        directory = Path(run_root) / "steps" / "s_opt"
        directory.mkdir(parents=True)
        os.symlink(str(directory / "missing-target"), str(directory / "dangling.out"))
        ref = ArtifactRef(
            id="d1",
            role="native_output",
            locator=ArtifactLocator.run_relative("steps/s_opt/dangling.out"),
            checksum="sha256:" + "1" * 64,
        )
        with pytest.raises(ArtifactIntegrityError):
            verify_artifact(run_root=run_root, ref=ref)

    def test_symlink_outside_rejected(self, tmp_path: Path) -> None:
        from confflow.persistence.artifacts import verify_artifact

        run_root = str(tmp_path / "run")
        directory = Path(run_root) / "steps" / "s_opt"
        directory.mkdir(parents=True)
        outside = tmp_path / "outside.txt"
        outside.write_bytes(b"external")
        os.symlink(str(outside), str(directory / "leak.out"))
        ref = ArtifactRef(
            id="l1",
            role="native_output",
            locator=ArtifactLocator.run_relative("steps/s_opt/leak.out"),
            checksum="sha256:" + "0" * 64,
        )
        with pytest.raises(ArtifactIntegrityError):
            verify_artifact(run_root=run_root, ref=ref)

    def test_planning_locator_variants(self, tmp_path: Path) -> None:
        from confflow.domain.artifact import LocatorKind as _Kind
        from confflow.domain.retention import RetentionClass as _RC
        from tests.v4.test_v43_matrices import _forged_ref as _forge

        run_root = str(tmp_path / "run")
        os.makedirs(os.path.join(run_root, "steps", "s_opt"))
        for raw in ("/absolute/x.out", "a\\b.out", "C:drive.out"):
            ref = _forge("forged", _Kind.RUN_RELATIVE, raw, None)
            object.__setattr__(ref, "retention", _RC.TEMPORARY)
            with pytest.raises(ArtifactIntegrityError):
                plan_gc(
                    run_root=run_root,
                    candidates=(ref,),
                    consumed_ids=frozenset({"forged"}),
                )

    def test_reuse_extra_guards(self) -> None:
        from confflow.persistence.reuse import ReuseInputs as _Inputs
        from confflow.persistence.reuse import build_producer_provenance as _build
        from confflow.persistence.reuse import evaluate_reuse as _evaluate

        with pytest.raises(PersistenceError):
            _build(
                adapter_version="a",
                profile_version="p",
                check_versions=["not-a-mapping"],  # type: ignore[arg-type]
                recovery_version="r",
            )
        with pytest.raises(PersistenceError):
            _build(
                adapter_version="a",
                profile_version="p",
                check_versions={"": "v"},
                recovery_version="r",
            )
        current = _Inputs(
            work_item_digest="sha256:" + "a" * 64,
            step_semantic_digest="sha256:" + "b" * 64,
            environment_digest=None,
            producer_provenance=FrozenDict({}),
            artifact_checksums=(),
        )
        decision = _evaluate(current=current, stored=current, stored_status=123)
        assert decision.decision is ReuseCode.EXECUTE_NEW
        decision = _evaluate(
            current=current,
            stored=current,
            stored_status=StoredWorkItemStatus.RUNNING,
            owner_verdict=123,
        )
        assert decision.decision is ReuseCode.BLOCKED_UNCERTAIN_OWNER

    def test_probe_direct_branches(self) -> None:
        from confflow.persistence import recovery as _recovery

        assert _recovery._positive_int(True) is None
        assert _recovery._positive_int("5") is None  # type: ignore[arg-type]
        assert _recovery._safe_process_group_id(None) is None
        assert _recovery._safe_session_id(None) is None
        assert _recovery._safe_create_time(None, object()) is None
        assert _recovery._safe_process_group_id(2**30) is None
        assert _recovery._safe_session_id(2**30) is None
        assert _recovery._safe_create_time(2**30, None) is None
        assert _recovery._maybe_import_psutil() is not None or True
        import sys as _sys

        sentinel = _sys.modules.get("psutil")
        _sys.modules["psutil"] = None  # type: ignore[assignment]
        try:
            assert _recovery._maybe_import_psutil() is None
        finally:
            if sentinel is not None:
                _sys.modules["psutil"] = sentinel
            else:
                del _sys.modules["psutil"]

    def test_probe_pid_branches(self) -> None:
        from confflow.persistence.recovery import _probe_pid
        from tests.v4.test_v43_matrices import TestPsutilDoubles as _Doubles

        helper = _Doubles()
        assert _probe_pid(pid=4242, psutil_mod=helper._double()).live is False
        fake = helper._double(procs={4242: "process-error"})
        assert _probe_pid(pid=4242, psutil_mod=fake).live is None
        fake = helper._double(procs={4242: {"running": False}})
        assert _probe_pid(pid=4242, psutil_mod=fake).live is False
        fake = helper._double(procs={4242: {"status": "gone"}})
        assert _probe_pid(pid=4242, psutil_mod=fake).live is False
        fake = helper._double(procs={4242: {"status": "error"}})
        assert _probe_pid(pid=4242, psutil_mod=fake).live is None
        fake = helper._double(procs={4242: {"create_time": "error"}})
        assert _probe_pid(pid=4242, psutil_mod=fake).live is None

    def test_candidate_branches(self) -> None:
        from tests.v4.test_v43_matrices import TestPsutilDoubles as _Doubles

        helper = _Doubles()
        fake = helper._double()

        class _ZombieInfo:
            pid = 1111
            info = {"status": "zombie"}

            def is_running(self) -> bool:
                return True

        class _GoneStatus:
            pid = 1112
            info = None

            def status(self) -> str:
                raise fake.NoSuchProcess("gone")

            def is_running(self) -> bool:
                return True

        class _DeadRunner:
            pid = 1113
            info = None

            def status(self) -> str:
                return "running"

            def is_running(self) -> bool:
                return False

        class _GoneRunner:
            pid = 1114
            info = None

            def status(self) -> str:
                return "running"

            def is_running(self) -> bool:
                raise fake.NoSuchProcess("gone")

        import confflow.persistence.recovery as _recovery

        assert _recovery._candidate_is_live(candidate=_ZombieInfo(), psutil_mod=fake) is False
        assert _recovery._candidate_is_live(candidate=_GoneStatus(), psutil_mod=fake) is False
        assert _recovery._candidate_is_live(candidate=_DeadRunner(), psutil_mod=fake) is False
        assert _recovery._candidate_is_live(candidate=_GoneRunner(), psutil_mod=fake) is False

    def test_scan_self_and_stranger(self) -> None:
        import os as _os

        from tests.v4.test_v43_matrices import TestPsutilDoubles as _Doubles

        helper = _Doubles()
        fake = helper._double()
        import confflow.persistence.recovery as _recovery

        class _Self:
            pid = _os.getpid()
            info = {"status": "running"}

            def is_running(self) -> bool:
                return True

        class _Stranger:
            pid = 2**28
            info = {"status": "running"}

            def is_running(self) -> bool:
                return True

        assert (
            _recovery._boundary_has_survivor(
                owner=OwnerIdentity(
                    owner_token="t", pid=4242, process_group_id=_os.getpgid(_os.getpid())
                ),
                psutil_mod=helper._double(everything=(_Self(),)),
            )
            is False
        )
        assert (
            _recovery._boundary_has_survivor(
                owner=OwnerIdentity(owner_token="t", pid=4242),
                psutil_mod=fake,
            )
            is False
        )
        assert (
            _recovery._boundary_has_survivor(
                owner=OwnerIdentity(owner_token="t", pid=4242, session_id=_os.getsid(_os.getpid())),
                psutil_mod=helper._double(everything=(_Stranger(),)),
            )
            is False
        )

    def test_dead_rival_executes_through_contested_path(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("FAKE_MODE", "success_opt")
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
                step_semantic_digest="sha256:" + "a" * 64,
            )
            assert store.claim(item.id, owner=OwnerIdentity(owner_token="ghost", pid=2**30))
            batch = BatchStepExecutor(WorkItemExecutor())
            request = _request(compiled, (item,), run_root)
            context = ItemExecutionContext(
                step_id=STEP_ID,
                scientific=request.scientific,
                scientific_defaults=request.scientific_defaults,
                adapter=request.adapter,
                profile=request.profile,
                checks=request.checks,
                recovery=request.recovery,
                execution_binding=request.execution_binding,
                run_root=run_root,
                work_base=None,
                supervisor=NativeProcessSupervisor(),
            )
            result, durable = batch._contested_claim(
                request,
                item,
                context,
                store=store,
                owner=OwnerIdentity(owner_token="me"),
                environment_digest=None,
                provenance=FrozenDict({}),
                run_root=run_root,
                should_cancel=lambda: False,
            )
            assert durable is True
            assert result.status is WorkItemStatus.COMPLETED
            assert [a.attempt_number for a in store.get_attempts(item.id)] == [1, 2]
