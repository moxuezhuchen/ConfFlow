"""Shared publication/psutil double helpers (moved verbatim; self removed)."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

from confflow.domain import FrozenDict
from confflow.domain.artifact import ArtifactRef
from confflow.domain.completion import CompletionPolicy, WorkItemStatus
from confflow.persistence.work_items import SqliteWorkItemStore

from .v43_coverage import STEP_ID, _result


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


def _valid_payload(item: Any) -> dict[str, Any]:
    """Return a valid completed-result payload for *item*."""
    return _result(item, status=WorkItemStatus.COMPLETED).to_dict()


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


def _publish_valid(tmp_path: Path) -> str:
    from confflow.persistence.publication import publish_step_result, rebuild_step_result
    from tests.v4._helpers.v43_coverage import _compile as _compile_doc
    from tests.v4._helpers.v43_coverage import _document as _make_doc
    from tests.v4._helpers.v43_coverage import _items as _assemble_items

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


def _double(**kwargs: Any) -> Any:
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
