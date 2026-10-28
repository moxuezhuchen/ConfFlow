"""Storage policy for durable SQLite state: local WAL, approval-gated DELETE, mode read-back."""

from __future__ import annotations

import ast
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

import confflow.persistence.storage_policy as storage_policy
from confflow.application.execution import ErrorCode, ExecutionServiceError
from confflow.application.execution.sqlite import SQLiteExecutionRepository
from confflow.application.execution.state_root import StateRoot
from confflow.persistence import work_items
from confflow.persistence.contracts import PersistenceError
from confflow.persistence.storage_policy import (
    ApprovalVerifier,
    StorageUnsupportedError,
    assert_local_wal_filesystem,
    detect_filesystem_type,
    select_journal_mode,
)
from confflow.persistence.work_items import SqliteWorkItemStore

REPO_ROOT = Path(__file__).resolve().parents[1]
POLICY_SOURCE = REPO_ROOT / "confflow" / "persistence" / "storage_policy.py"


def _store_path(tmp_path: Path) -> str:
    """Return a store path nested under a managed run root."""
    return str(tmp_path / "run" / "steps" / "s_opt" / "work_items.sqlite")


def _local_mounts(monkeypatch, rows=(("/", "ext4"),)) -> None:
    """Pin the mount table so policy decisions do not depend on the host."""
    monkeypatch.setattr(storage_policy, "mount_table", lambda: list(rows))


def _root_owned(monkeypatch) -> None:
    """Make a test-created approval file look root-owned, as on a real admin deployment."""
    real_fstat = os.fstat

    def root_owned_fstat(fd: int):
        values = list(real_fstat(fd))
        values[4] = 0
        return os.stat_result(values)

    monkeypatch.setattr(storage_policy.os, "fstat", root_owned_fstat)


def _approval(tmp_path: Path, directory: Path, filesystem_type: str) -> Path:
    """Write a valid approval file bound to *directory*."""
    stat_result = directory.stat()
    path = tmp_path / "approval.json"
    path.write_text(
        json.dumps(
            {
                "approval_id": "approval-1",
                "root_realpath": str(directory.resolve()),
                "device": stat_result.st_dev,
                "inode": stat_result.st_ino,
                "filesystem_type": filesystem_type,
                "guarantees": {"locking": True, "atomic_rename": True, "fsync": True},
            }
        ),
        encoding="utf-8",
    )
    path.chmod(0o600)
    return path


def _journal_mode(store: SqliteWorkItemStore) -> str:
    """Return the journal mode SQLite reports for an open store."""
    return str(store._conn.execute("PRAGMA journal_mode").fetchone()[0]).lower()


@pytest.mark.parametrize("filesystem", ["9p", "nfs4", "cifs", "unknown"])
def test_store_refuses_non_local_filesystem_and_names_it(tmp_path: Path, filesystem: str):
    """Unsupported or unknown storage is refused with its type and no database is created."""
    path = _store_path(tmp_path)
    with pytest.raises(StorageUnsupportedError) as error:
        SqliteWorkItemStore.open(path, filesystem_detector=lambda _: filesystem)
    assert isinstance(error.value, PersistenceError)
    assert repr(filesystem) in str(error.value)
    assert "local filesystem" in str(error.value)
    assert not os.path.exists(path)


def test_store_local_filesystem_uses_wal_and_verifies_actual_mode(tmp_path: Path, monkeypatch):
    """A local filesystem enables WAL and the store reads back the mode SQLite applied."""
    _local_mounts(monkeypatch)
    with SqliteWorkItemStore.open(
        _store_path(tmp_path), filesystem_detector=lambda _: "ext4"
    ) as store:
        assert _journal_mode(store) == "wal"


def test_store_default_detector_on_local_mount_uses_wal(tmp_path: Path, monkeypatch):
    """Without an injected detector the mount table decides, and local storage keeps WAL."""
    _local_mounts(monkeypatch, (("/", "ext4"),))
    with SqliteWorkItemStore.open(_store_path(tmp_path)) as store:
        assert _journal_mode(store) == "wal"


def test_store_valid_approval_uses_delete_journal(tmp_path: Path, monkeypatch):
    """A verified root-owned approval for a non-local root selects DELETE journal mode."""
    run_dir = tmp_path / "run" / "steps" / "s_opt"
    run_dir.mkdir(parents=True)
    approval = _approval(tmp_path, run_dir, "nfs4")
    _root_owned(monkeypatch)
    with SqliteWorkItemStore.open(
        _store_path(tmp_path),
        shared_fs_approval_path=approval,
        filesystem_detector=lambda _: "nfs4",
    ) as store:
        assert _journal_mode(store) == "delete"


def test_store_approval_for_other_filesystem_is_refused(tmp_path: Path, monkeypatch):
    """An approval naming one filesystem does not bind a root on a different one."""
    run_dir = tmp_path / "run" / "steps" / "s_opt"
    run_dir.mkdir(parents=True)
    approval = _approval(tmp_path, run_dir, "nfs4")
    _root_owned(monkeypatch)
    with pytest.raises(StorageUnsupportedError, match="filesystem_type|Invalid shared-FS approval"):
        SqliteWorkItemStore.open(
            _store_path(tmp_path),
            shared_fs_approval_path=approval,
            filesystem_detector=lambda _: "ext4",
        )


class _ReportingConnection:
    """Wrap a SQLite connection so the read-back reports a mode SQLite did not apply."""

    def __init__(self, real, reported: str) -> None:
        self._real = real
        self._reported = reported

    def execute(self, sql, *args):
        if sql == "PRAGMA journal_mode":
            return _Rows(self._reported)
        return self._real.execute(sql, *args)

    def close(self) -> None:
        self._real.close()


class _Rows:
    def __init__(self, value: str) -> None:
        self._value = value

    def fetchone(self):
        return (self._value,)


def test_store_actual_mode_mismatch_raises(tmp_path: Path, monkeypatch):
    """A requested WAL that SQLite reports as another mode is an error, not silently accepted."""
    _local_mounts(monkeypatch)
    real_connect = work_items.sqlite3.connect
    monkeypatch.setattr(
        work_items.sqlite3,
        "connect",
        lambda *args, **kwargs: _ReportingConnection(real_connect(*args, **kwargs), "memory"),
    )
    with pytest.raises(StorageUnsupportedError) as error:
        SqliteWorkItemStore.open(_store_path(tmp_path), filesystem_detector=lambda _: "ext4")
    assert "'memory'" in str(error.value)
    assert "'wal'" in str(error.value)


@pytest.mark.parametrize(
    ("rows", "detected", "expected"),
    [
        ([("/", "9p")], "ext4", "9p"),
        ([], "ext4", "unknown"),
    ],
)
def test_wal_check_refuses_mount_table_mismatch(
    monkeypatch, tmp_path: Path, rows, detected, expected
):
    """The mount table is checked as well: a local detector cannot override a network mount."""
    _local_mounts(monkeypatch, rows)
    with pytest.raises(StorageUnsupportedError) as error:
        assert_local_wal_filesystem(tmp_path, lambda _: detected)
    assert repr(expected) in str(error.value)


def test_wal_check_reports_unverifiable_mount_table(monkeypatch, tmp_path: Path):
    """A missing mount table is reported as an unverifiable filesystem, not a crash."""

    def missing():
        raise FileNotFoundError("/proc/mounts")

    monkeypatch.setattr(storage_policy, "mount_table", missing)
    with pytest.raises(StorageUnsupportedError, match="Cannot verify filesystem type for WAL"):
        assert_local_wal_filesystem(tmp_path, lambda _: "ext4")


def test_detect_and_select_journal_mode_on_local_root(monkeypatch, tmp_path: Path):
    """Detection follows the longest mount point and an unapproved local root selects WAL."""
    _local_mounts(monkeypatch, (("/", "9p"), (str(tmp_path), "ext4")))
    assert detect_filesystem_type(tmp_path) == "ext4"
    assert select_journal_mode(tmp_path) == "wal"
    _local_mounts(monkeypatch, ())
    assert detect_filesystem_type(tmp_path) == "unknown"


def test_approval_verifier_rejects_group_writable_file(tmp_path: Path):
    """Approval evidence that a group or other user can write is refused."""
    approval = _approval(tmp_path, tmp_path, "nfs4")
    approval.chmod(0o666)
    with pytest.raises(StorageUnsupportedError, match="root-owned"):
        ApprovalVerifier().verify(approval, root=tmp_path, filesystem_type="nfs4")


def test_control_repository_still_rejects_9p_with_same_code(tmp_path: Path):
    """The application control repository keeps its REPOSITORY_UNAVAILABLE refusal and text."""
    root_dir = tmp_path / "state"
    root_dir.mkdir(mode=0o700)
    with pytest.raises(ExecutionServiceError) as error:
        SQLiteExecutionRepository(StateRoot.resolve(root_dir), filesystem_detector=lambda _: "9p")
    assert error.value.code is ErrorCode.REPOSITORY_UNAVAILABLE
    assert str(error.value) == "WAL requires a verified local filesystem; use shared_fs=True"


def test_control_repository_unverifiable_filesystem_keeps_message(tmp_path: Path, monkeypatch):
    """A missing mount table keeps the control repository's unverifiable-filesystem message."""
    root_dir = tmp_path / "state"
    root_dir.mkdir(mode=0o700)

    def missing():
        raise FileNotFoundError("/proc/mounts")

    monkeypatch.setattr(storage_policy, "mount_table", missing)
    with pytest.raises(ExecutionServiceError) as error:
        SQLiteExecutionRepository(StateRoot.resolve(root_dir), filesystem_detector=lambda _: "ext4")
    assert error.value.code is ErrorCode.REPOSITORY_UNAVAILABLE
    assert str(error.value) == "Cannot verify filesystem type for WAL"


def test_storage_policy_imports_nothing_from_application():
    """Persistence policy stays below the application layer in both source and runtime."""
    tree = ast.parse(POLICY_SOURCE.read_text(encoding="utf-8"))
    imported: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            # Relative imports carry their package depth; only "application" matters here.
            imported.append(node.module or "")
    offenders = sorted({name for name in imported if "application" in name.split(".")})
    assert offenders == []

    probe = (
        "import sys, confflow.persistence.storage_policy as p; "
        "assert not [m for m in sys.modules if m.startswith('confflow.application')], "
        "sorted(m for m in sys.modules if m.startswith('confflow.application'))"
    )
    subprocess.run([sys.executable, "-c", probe], cwd=REPO_ROOT, check=True, capture_output=True)


def test_approval_verifier_requires_regular_file_and_nofollow(tmp_path: Path, monkeypatch):
    """Approval evidence must be a regular file opened without following links."""
    with pytest.raises(StorageUnsupportedError, match="regular non-symlink"):
        ApprovalVerifier().verify(tmp_path, root=tmp_path, filesystem_type="nfs4")
    approval = _approval(tmp_path, tmp_path, "nfs4")
    monkeypatch.delattr(storage_policy.os, "O_NOFOLLOW", raising=False)
    with pytest.raises(StorageUnsupportedError, match="O_NOFOLLOW"):
        ApprovalVerifier().verify(approval, root=tmp_path, filesystem_type="nfs4")
