#!/usr/bin/env python3

"""V4-4 claim() fault discrimination: contention vs disk I/O vs corruption.

``SqliteWorkItemStore.claim`` must return ``False`` only for genuine lock
contention (bounded by the busy timeout).  Operational disk failures raise
a typed persistence error and integrity violations raise corruption —
neither may masquerade as a clean claim conflict.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

import pytest

from confflow.persistence import (
    CorruptStateError,
    OwnerIdentity,
    PersistenceError,
    StoredWorkItemStatus,
    store_path,
)
from confflow.persistence.work_items import SqliteWorkItemStore
from tests.v4.test_v43_coverage import _compile as _compile_doc
from tests.v4.test_v43_coverage import _document as _make_doc
from tests.v4.test_v43_coverage import _items as _assemble_items

STEP_ID = "s_opt"


class _FailingConnection:
    """sqlite3 connection proxy failing closed on matching statements."""

    def __init__(self, real: sqlite3.Connection, fail_on: str, error: Exception) -> None:
        self._real = real
        self._fail_on = fail_on
        self._error = error

    def execute(self, sql: str, parameters: Any = ()) -> Any:
        if self._fail_on in str(sql):
            raise self._error
        return self._real.execute(sql, parameters)


def _setup(tmp_path: Path) -> tuple[SqliteWorkItemStore, Any]:
    """Register one pending item, returning the open store and the item."""
    compiled = _compile_doc(_make_doc())
    (item,) = _assemble_items(compiled, 1)
    store = SqliteWorkItemStore.open(store_path(str(tmp_path / "run"), STEP_ID))
    store.register_item(
        work_item_id=item.id,
        logical_key=item.logical_key,
        step_id=item.step_id,
        work_item_digest=item.semantic_digest,
        step_semantic_digest="sha256:" + "a" * 64,
    )
    return store, item


class TestClaimFaultDiscrimination:
    """Contention is False; I/O and integrity failures raise typed errors."""

    def test_busy_lock_is_clean_conflict(self, tmp_path: Path) -> None:
        store, item = _setup(tmp_path)
        try:
            store._conn.execute("PRAGMA busy_timeout = 100")
            rival = sqlite3.connect(store.path, timeout=5.0, isolation_level=None)
            try:
                rival.execute("BEGIN IMMEDIATE")
                rival.execute(
                    "UPDATE items SET updated_wall = 0.0 WHERE work_item_id = ?",
                    (item.id,),
                )
                # Rival holds the write lock past our 100ms timeout.
                assert store.claim(item.id, owner=OwnerIdentity(owner_token="me")) is False
                assert store.get_state(item.id) is StoredWorkItemStatus.PENDING
                rival.execute("ROLLBACK")
                # Lock released: the same claim now succeeds.
                assert store.claim(item.id, owner=OwnerIdentity(owner_token="me")) is True
            finally:
                rival.close()
        finally:
            store.close()

    def test_disk_io_error_raises(self, tmp_path: Path) -> None:
        store, item = _setup(tmp_path)
        try:
            real = store._conn
            store._conn = _FailingConnection(  # type: ignore[assignment]
                real, "UPDATE items", sqlite3.OperationalError("disk I/O error")
            )
            try:
                with pytest.raises(PersistenceError, match="I/O failure"):
                    store.claim(item.id, owner=OwnerIdentity(owner_token="me"))
            finally:
                store._conn = real
            assert store.get_state(item.id) is StoredWorkItemStatus.PENDING
            assert store.get_attempts(item.id) == ()
        finally:
            store.close()

    def test_integrity_error_is_corruption(self, tmp_path: Path) -> None:
        store, item = _setup(tmp_path)
        try:
            real = store._conn
            store._conn = _FailingConnection(  # type: ignore[assignment]
                real,
                "INSERT INTO attempts",
                sqlite3.IntegrityError("UNIQUE constraint failed"),
            )
            try:
                with pytest.raises(CorruptStateError):
                    store.claim(item.id, owner=OwnerIdentity(owner_token="me"))
            finally:
                store._conn = real
        finally:
            store.close()

    def test_generic_operational_error_raises(self, tmp_path: Path) -> None:
        store, item = _setup(tmp_path)
        try:
            real = store._conn
            store._conn = _FailingConnection(  # type: ignore[assignment]
                real, "SELECT", sqlite3.OperationalError("file is not a database")
            )
            try:
                with pytest.raises(PersistenceError):
                    store.claim(item.id, owner=OwnerIdentity(owner_token="me"))
            finally:
                store._conn = real
        finally:
            store.close()
