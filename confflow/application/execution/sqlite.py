"""SQLite implementation of the single durable execution aggregate repository."""

from __future__ import annotations

import json
import os
import sqlite3
import stat
from collections.abc import Callable
from contextlib import closing
from pathlib import Path

from .errors import ErrorCode, ExecutionServiceError, RepositoryConflict
from .models import (
    Artifact,
    Checkpoint,
    ExecutableIdentity,
    ExecutionAggregate,
    ExecutionEvent,
    PrepareRequest,
    RunState,
)
from .service import _canonical_path, _is_digest, _is_identifier
from .shared_fs_approval import ApprovalVerifier
from .state_root import StateRoot

_SCHEMA_VERSION = 1


class SQLiteExecutionRepository:
    """Per-operation SQLite connections with atomic aggregate/event CAS commits."""

    def __init__(
        self,
        state_root: StateRoot,
        *,
        shared_fs: bool = False,
        shared_fs_approval_path=None,
        busy_timeout_ms: int = 5000,
        filesystem_detector=None,
    ):
        self._root = state_root
        self._path = state_root.database_path
        self._shared_fs = shared_fs
        self._busy = busy_timeout_ms
        self._journal_mode = "delete" if shared_fs else "wal"
        self._detector = filesystem_detector or _filesystem_type
        if shared_fs:
            if shared_fs_approval_path is None:
                raise _unavailable("DELETE mode requires a verified root-owned approval file")
            self._approval = ApprovalVerifier().verify(
                shared_fs_approval_path,
                root=state_root.path,
                filesystem_type=self._detector(self._path.parent),
            )
        if not shared_fs:
            _assert_wal_supported(self._path.parent, self._detector)
        self._migrate()

    @property
    def journal_mode(self) -> str:
        return self._journal_mode

    @property
    def schema_version(self) -> int:
        with closing(self._connect()) as con:
            return int(con.execute("PRAGMA user_version").fetchone()[0])

    def close(self) -> None:
        """Compatibility no-op: operations own and close their own connections."""

    def create_or_get(self, request: PrepareRequest) -> ExecutionAggregate:
        try:
            with self._transaction("IMMEDIATE") as con:
                row = con.execute(
                    "SELECT run_id, request_digest FROM aggregates WHERE idempotency_key=?",
                    (request.idempotency_key,),
                ).fetchone()
                if row is not None:
                    if row[1] != request.request_digest:
                        raise ExecutionServiceError(
                            ErrorCode.IDEMPOTENCY_CONFLICT, "Idempotency key conflicts"
                        )
                    record = self._load(con, row[0])
                    assert record is not None
                    return record
                if con.execute(
                    "SELECT 1 FROM aggregates WHERE run_id=?", (request.run_id,)
                ).fetchone():
                    raise ExecutionServiceError(ErrorCode.INVALID_REQUEST, "Run ID already exists")
                record = ExecutionAggregate(
                    request.run_id,
                    request.idempotency_key,
                    request.request_digest,
                    request.workflow_config_digest,
                    request.input_manifest_digest,
                    request.expected_executable_identity,
                    1,
                    RunState.PREPARED,
                )
                self._insert(con, record, "prepared")
                record = self._load(con, request.run_id)
                assert record is not None
                return record
        except ExecutionServiceError:
            raise
        except Exception as error:
            raise _unavailable(f"Repository create unavailable: {error}", retryable=True) from error

    def read(self, run_id: str) -> ExecutionAggregate | None:
        try:
            with closing(self._connect()) as con:
                con.execute("BEGIN")
                try:
                    return self._load(con, run_id, missing_ok=True)
                finally:
                    # One explicit read transaction keeps the aggregate, events
                    # and artifacts SELECTs on the same WAL snapshot; without
                    # it a concurrent CAS commit between statements produces a
                    # torn projection.
                    con.execute("COMMIT")
        except ExecutionServiceError:
            raise
        except Exception as error:
            raise _unavailable(f"Repository read unavailable: {error}", retryable=True) from error

    def compare_and_mutate(
        self,
        run_id: str,
        expected_revision: int,
        mutate: Callable[[ExecutionAggregate], tuple[ExecutionAggregate, str]],
    ) -> ExecutionAggregate:
        try:
            with self._transaction("IMMEDIATE") as con:
                current = self._load(con, run_id)
                assert current is not None
                if current.revision != expected_revision:
                    raise RepositoryConflict(run_id)
                candidate, kind = mutate(current)
                revision = current.revision + 1
                cursor = con.execute(
                    "UPDATE aggregates SET revision=?,state=?,attempt=?,launch_token=?,launch_checkpoint=?,cancel_token=?,cancel_pending=?,checkpoint_json=?,identity_json=? WHERE run_id=? AND revision=?",
                    (
                        revision,
                        candidate.state.value,
                        candidate.attempt,
                        candidate.launch_token,
                        candidate.launch_checkpoint,
                        candidate.cancel_token,
                        int(candidate.cancel_pending),
                        _dump_checkpoint(candidate.checkpoint),
                        _dump_identity(candidate.expected_executable_identity),
                        run_id,
                        expected_revision,
                    ),
                )
                if cursor.rowcount != 1:
                    raise RepositoryConflict(run_id)
                con.execute("DELETE FROM artifacts WHERE run_id=?", (run_id,))
                self._insert_artifacts(con, run_id, candidate.artifacts)
                con.execute(
                    "INSERT INTO events(run_id,revision,cursor,type) VALUES(?,?,?,?)",
                    (run_id, revision, f"r{revision:020d}", kind),
                )
                self._commit(con)
                record = self._load(con, run_id)
                assert record is not None
                return record
        except (ExecutionServiceError, RepositoryConflict):
            raise
        except Exception as error:
            raise _unavailable(
                f"Repository mutation unavailable: {error}", retryable=True
            ) from error

    def _migrate(self) -> None:
        try:
            with self._transaction("EXCLUSIVE") as con:
                version = int(con.execute("PRAGMA user_version").fetchone()[0])
                if version > _SCHEMA_VERSION:
                    raise _unavailable("Repository schema is newer than this ConfFlow")
                if version == 0:
                    con.execute(
                        "CREATE TABLE aggregates(run_id TEXT PRIMARY KEY,idempotency_key TEXT UNIQUE NOT NULL,request_digest TEXT NOT NULL,workflow_digest TEXT NOT NULL,input_digest TEXT NOT NULL,identity_json TEXT NOT NULL,revision INTEGER NOT NULL,state TEXT NOT NULL,attempt INTEGER NOT NULL,launch_token TEXT,launch_checkpoint TEXT,cancel_token TEXT,cancel_pending INTEGER NOT NULL,checkpoint_json TEXT)"
                    )
                    con.execute(
                        "CREATE TABLE events(run_id TEXT NOT NULL,revision INTEGER NOT NULL,cursor TEXT NOT NULL,type TEXT NOT NULL,PRIMARY KEY(run_id,revision),UNIQUE(run_id,cursor),FOREIGN KEY(run_id) REFERENCES aggregates(run_id))"
                    )
                    con.execute(
                        "CREATE TABLE artifacts(run_id TEXT NOT NULL,terminal TEXT NOT NULL,path TEXT NOT NULL,sha256 TEXT NOT NULL,size INTEGER NOT NULL,content_schema TEXT NOT NULL,PRIMARY KEY(run_id,terminal,path),FOREIGN KEY(run_id) REFERENCES aggregates(run_id))"
                    )
                    con.execute("PRAGMA user_version=1")
                con.execute(
                    "CREATE TABLE IF NOT EXISTS repository_meta(key TEXT PRIMARY KEY,value TEXT NOT NULL)"
                )
                if self._shared_fs:
                    approval = self._approval
                    bound = json.dumps(
                        {
                            "approval_id": approval.approval_id,
                            "root": approval.root_realpath,
                            "device": approval.device,
                            "inode": approval.inode,
                            "filesystem": approval.filesystem_type,
                        },
                        sort_keys=True,
                    )
                    prior = con.execute(
                        "SELECT value FROM repository_meta WHERE key='shared_fs_approval'"
                    ).fetchone()
                    if prior is not None and prior[0] != bound:
                        raise _unavailable(
                            "Shared-FS approval does not match persisted repository binding"
                        )
                    con.execute(
                        "INSERT OR REPLACE INTO repository_meta(key,value) VALUES('shared_fs_approval',?)",
                        (bound,),
                    )
                self._commit(con)
            os.chmod(self._path, 0o600)
            self._secure_database_files()
        except ExecutionServiceError:
            raise
        except Exception as error:
            raise _unavailable(
                f"Repository migration unavailable: {error}", retryable=True
            ) from error

    def _connect(self) -> sqlite3.Connection:
        self._reject_database_symlinks()
        con = sqlite3.connect(str(self._path), timeout=self._busy / 1000, isolation_level=None)
        try:
            con.execute("PRAGMA foreign_keys=ON")
            con.execute(f"PRAGMA busy_timeout={self._busy}")
            con.execute("PRAGMA synchronous=FULL")
            actual = str(
                con.execute(f"PRAGMA journal_mode={self._journal_mode.upper()}").fetchone()[0]
            ).lower()
            if actual != self._journal_mode:
                raise _unavailable(f"Requested journal mode {self._journal_mode} unavailable")
            self._secure_database_files()
            return con
        except Exception:
            con.close()
            raise

    def _secure_database_files(self) -> None:
        """Force and verify owner-only modes for database and SQLite sidecars."""
        for path in (self._path, Path(f"{self._path}-wal"), Path(f"{self._path}-shm")):
            try:
                metadata = os.lstat(path)
            except FileNotFoundError:
                continue
            if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
                raise _unavailable("Repository database files must be regular non-symlink files")
            try:
                os.chmod(path, 0o600, follow_symlinks=False)
                verified = os.lstat(path)
            except FileNotFoundError:
                # SQLite may remove a sidecar as another connection closes it.
                # A vanished sidecar is safe; a new connection will recreate it.
                continue
            if stat.S_ISLNK(verified.st_mode) or stat.S_IMODE(verified.st_mode) != 0o600:
                raise _unavailable("Repository database files must have mode 0600")

    def _reject_database_symlinks(self) -> None:
        """Reject pre-created database or sidecar links before SQLite can open them."""
        for path in (self._path, Path(f"{self._path}-wal"), Path(f"{self._path}-shm")):
            try:
                metadata = os.lstat(path)
            except FileNotFoundError:
                continue
            if stat.S_ISLNK(metadata.st_mode):
                raise _unavailable("Repository database files must not be symlinks")

    def _transaction(self, mode: str):
        con = self._connect()
        try:
            con.execute(f"BEGIN {mode}")
        except Exception:
            con.close()
            raise

        class Tx:
            def __enter__(self_non):
                return con

            def __exit__(self_non, typ, value, trace):
                try:
                    if typ is not None:
                        con.rollback()
                    elif con.in_transaction:
                        con.commit()
                finally:
                    con.close()

        return Tx()

    def _commit(self, con: sqlite3.Connection) -> None:
        """Testable explicit commit; context never performs a second commit."""
        con.commit()

    def _insert(self, con: sqlite3.Connection, record: ExecutionAggregate, event_type: str) -> None:
        con.execute(
            "INSERT INTO aggregates VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                record.run_id,
                record.idempotency_key,
                record.request_digest,
                record.workflow_config_digest,
                record.input_manifest_digest,
                _dump_identity(record.expected_executable_identity),
                record.revision,
                record.state.value,
                record.attempt,
                record.launch_token,
                record.launch_checkpoint,
                record.cancel_token,
                int(record.cancel_pending),
                _dump_checkpoint(record.checkpoint),
            ),
        )
        con.execute(
            "INSERT INTO events VALUES(?,?,?,?)",
            (record.run_id, record.revision, f"r{record.revision:020d}", event_type),
        )

    def _insert_artifacts(self, con, run_id: str, artifacts: tuple[Artifact, ...]) -> None:
        con.executemany(
            "INSERT INTO artifacts VALUES(?,?,?,?,?,?)",
            [(run_id, a.terminal, a.path, a.sha256, a.size, a.content_schema) for a in artifacts],
        )

    def _load(self, con, run_id: str, missing_ok: bool = False) -> ExecutionAggregate | None:
        row = con.execute("SELECT * FROM aggregates WHERE run_id=?", (run_id,)).fetchone()
        if row is None:
            if missing_ok:
                return None
            raise _unavailable("Aggregate disappeared")
        try:
            if (
                not all(isinstance(row[index], str) for index in range(0, 6))
                or not isinstance(row[6], int)
                or row[6] < 1
                or not isinstance(row[7], str)
                or not isinstance(row[8], int)
                or row[8] < 0
                or any(
                    row[index] is not None and not isinstance(row[index], str)
                    for index in (9, 10, 11, 13)
                )
                or row[12] not in (0, 1)
            ):
                raise ValueError("invalid aggregate column types")
            if (
                not _is_identifier(row[0])
                or not _is_identifier(row[1])
                or not all(_is_digest(row[index]) for index in (2, 3, 4))
                or any(row[index] is not None and not _is_token(row[index]) for index in (9, 11))
                or (row[10] is not None and not row[10])
            ):
                raise ValueError("invalid aggregate identifiers or digests")
            artifacts = tuple(
                Artifact(*item[1:])
                for item in con.execute(
                    "SELECT run_id,terminal,path,sha256,size,content_schema FROM artifacts WHERE run_id=? ORDER BY terminal,path",
                    (run_id,),
                )
            )
            if any(
                not isinstance(a.terminal, str)
                or not _is_identifier(a.terminal)
                or not isinstance(a.path, str)
                or _canonical_path(a.path) != a.path
                or not isinstance(a.sha256, str)
                or not _is_digest(a.sha256)
                or not isinstance(a.size, int)
                or a.size < 0
                or not isinstance(a.content_schema, str)
                or not a.content_schema
                for a in artifacts
            ):
                raise ValueError("invalid artifact column types")
            events = tuple(
                ExecutionEvent(item[2], item[1], item[3])
                for item in con.execute(
                    "SELECT run_id,revision,cursor,type FROM events WHERE run_id=? ORDER BY revision",
                    (run_id,),
                )
            )
            if any(
                not isinstance(e.cursor, str)
                or not isinstance(e.revision, int)
                or e.revision < 1
                or e.cursor != f"r{e.revision:020d}"
                or not isinstance(e.type, str)
                or not e.type
                for e in events
            ):
                raise ValueError("invalid event column types")
            if (
                not events
                or tuple(event.revision for event in events) != tuple(range(1, row[6] + 1))
                or events[-1].revision != row[6]
            ):
                raise ValueError("event stream does not match aggregate revision")
            return ExecutionAggregate(
                row[0],
                row[1],
                row[2],
                row[3],
                row[4],
                _load_identity(row[5]),
                row[6],
                RunState(row[7]),
                row[8],
                row[9],
                row[10],
                row[11],
                bool(row[12]),
                _load_checkpoint(row[13]),
                artifacts,
                events,
            )
        except (ExecutionServiceError, TypeError, ValueError) as error:
            raise _unavailable(f"Corrupt aggregate data: {error}") from error


def _dump_identity(value: ExecutableIdentity) -> str:
    return json.dumps(
        {
            "v": 1,
            "sha256": value.sha256,
            "realpath": value.realpath,
            "device_inode": value.device_inode,
        },
        sort_keys=True,
    )


def _load_identity(raw: str) -> ExecutableIdentity:
    try:
        value = json.loads(raw)
        if (
            not isinstance(value, dict)
            or set(value) != {"v", "sha256", "realpath", "device_inode"}
            or value.get("v") != 1
            or not isinstance(value.get("sha256"), str)
            or not _is_digest(value["sha256"])
            or (value.get("realpath") is not None and not isinstance(value.get("realpath"), str))
            or (
                value.get("device_inode") is not None
                and not isinstance(value.get("device_inode"), str)
            )
        ):
            raise ValueError("invalid identity")
        return ExecutableIdentity(value["sha256"], value.get("realpath"), value.get("device_inode"))
    except Exception as error:
        raise _unavailable(f"Corrupt identity JSON: {error}") from error


def _dump_checkpoint(value: Checkpoint | None) -> str | None:
    return None if value is None else json.dumps({"v": 1, "id": value.checkpoint_id})


def _load_checkpoint(raw: str | None) -> Checkpoint | None:
    if raw is None:
        return None
    try:
        value = json.loads(raw)
        if (
            not isinstance(value, dict)
            or set(value) != {"v", "id"}
            or value.get("v") != 1
            or not isinstance(value.get("id"), str)
            or not value["id"]
        ):
            raise ValueError("invalid checkpoint")
        return Checkpoint(value["id"])
    except Exception as error:
        raise _unavailable(f"Corrupt checkpoint JSON: {error}") from error


def _unavailable(message: str, retryable: bool = False) -> ExecutionServiceError:
    return ExecutionServiceError(ErrorCode.REPOSITORY_UNAVAILABLE, message, retryable=retryable)


def _is_token(value: str) -> bool:
    """Validate durable executor tokens, which may include a maximum-length run ID."""
    allowed = frozenset("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789._-")
    return bool(value) and len(value) <= 256 and all(character in allowed for character in value)


def _assert_wal_supported(path, detector) -> None:
    """Reject known or unknown shared filesystems before attempting WAL."""
    try:
        filesystem = detector(path)
        if filesystem not in {"ext4", "xfs", "btrfs", "tmpfs", "overlay", "apfs"}:
            raise _unavailable("WAL requires a verified local filesystem; use shared_fs=True")
        target = os.path.realpath(path)
        mounts = []
        for line in Path("/proc/mounts").read_text(encoding="utf-8").splitlines():
            fields = line.split()
            if len(fields) >= 3:
                mounts.append((os.path.realpath(fields[1].replace("\\040", " ")), fields[2]))
        match = max(
            (
                item
                for item in mounts
                if target.startswith(item[0].rstrip("/") + "/") or target == item[0]
            ),
            key=lambda item: len(item[0]),
            default=None,
        )
        if match is None or match[1] in {"9p", "nfs", "nfs4", "cifs", "smbfs", "fuse.sshfs"}:
            raise _unavailable("WAL requires a verified local filesystem; use shared_fs=True")
    except FileNotFoundError as error:
        raise _unavailable("Cannot verify filesystem type for WAL") from error


def _filesystem_type(path) -> str:
    """Return the longest-mount filesystem type, or an unknown sentinel."""
    target = os.path.realpath(path)
    rows = [line.split() for line in Path("/proc/mounts").read_text(encoding="utf-8").splitlines()]
    choices = [
        (os.path.realpath(row[1].replace("\\040", " ")), row[2]) for row in rows if len(row) >= 3
    ]
    match = max(
        (
            item
            for item in choices
            if target.startswith(item[0].rstrip("/") + "/") or target == item[0]
        ),
        key=lambda item: len(item[0]),
        default=None,
    )
    return "unknown" if match is None else match[1]
