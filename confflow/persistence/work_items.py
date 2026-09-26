#!/usr/bin/env python3

"""Durable SQLite work-item store for ConfFlow Workflow V4 (V4-3 milestone).

This module owns per-item execution truth for one step: registration digests,
claim ownership, attempt history, terminal results, and digest-keyed reuse
lookups.  Scientific payloads stay generic JSON; there are no chemistry-shaped
columns anywhere in the schema.

Design notes
------------
- Every public method runs one short transaction; no transaction is ever held
  across native execution (this API has no execution hooks, only row ops).
- All JSON is serialized with :func:`canonical_json_bytes` over ``to_dict``
  payloads so stored bytes are deterministic across processes.
- :class:`WorkItemResult` has no ``from_dict``; reconstruction from stored
  payloads is strict and fails closed with :class:`CorruptStateError`.
- The store duck-type satisfies the ``WorkItemRepository`` and ``ReuseStore``
  protocols from ``confflow.execution.batch`` (``record_started``,
  ``record_finished``, ``get_result``, ``lookup``, ``store``) without
  importing ``confflow.execution``.  One semantic difference from the
  in-memory reuse store is deliberate: :meth:`store` only acknowledges
  results already completed through :meth:`complete`; results unknown to the
  store are ignored rather than indexed, because the durable store must never
  invent execution history it did not observe.
"""

from __future__ import annotations

import json
import os
import sqlite3
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..domain.artifact import ArtifactLocator, ArtifactRef, LocatorKind
from ..domain.canonical import canonical_json_bytes
from ..domain.completion import WorkItemStatus
from ..domain.diagnostics import Diagnostic, DiagnosticSeverity
from ..domain.errors import CanonicalizationError, DomainError
from ..domain.result import Provenance, QuantityKind, ResultSet, ScientificResult
from ..domain.retention import RetentionClass
from ..domain.structure import StructureRecord, StructureSet
from ..domain.units import Unit
from ..domain.work_item import (
    ArtifactSet,
    RecoveryInfo,
    ResultError,
    Timing,
    WorkItemResult,
)
from .contracts import (
    PERSISTENCE_SCHEMA_VERSION,
    CorruptStateError,
    OwnerIdentity,
    PersistenceError,
    SchemaVersionError,
    StateTransitionError,
    StoredWorkItemStatus,
    validate_run_root,
    wall_now,
)

__all__ = [
    "SqliteWorkItemStore",
    "StoredAttempt",
]

_SCHEMA_KIND_ROW: str = "schema_version"

_BUSY_TIMEOUT_MS: int = 5000

_CONTENTION_MARKERS: tuple[str, ...] = ("locked", "busy")


def _is_lock_contention(error: sqlite3.Error) -> bool:
    """Return whether *error* signals lock contention rather than I/O failure.

    ``SQLITE_BUSY`` (``database is locked``) means another claimant holds
    the write lock past our bounded ``busy_timeout``: a clean claim
    conflict.  Every other operational failure (disk I/O, corrupt file,
    bad parameter) fails closed as a persistence error instead of
    masquerading as contention.
    """
    text = str(error).lower()
    return any(marker in text for marker in _CONTENTION_MARKERS)


_REQUIRED_TABLES: tuple[str, ...] = ("meta", "items", "attempts", "artifact_rows")

_DDL_META: str = "CREATE TABLE meta (kind TEXT PRIMARY KEY, value TEXT NOT NULL)"

_DDL_ITEMS: str = (
    "CREATE TABLE items ("
    "work_item_id TEXT PRIMARY KEY, "
    "logical_key TEXT NOT NULL, "
    "step_id TEXT NOT NULL, "
    "work_item_digest TEXT NOT NULL, "
    "step_semantic_digest TEXT NOT NULL, "
    "environment_digest TEXT, "
    "producer_provenance TEXT NOT NULL, "
    "status TEXT NOT NULL, "
    "current_attempt INTEGER NOT NULL DEFAULT 0, "
    "owner_token TEXT, "
    "owner_pid INTEGER, "
    "owner_pgid INTEGER, "
    "owner_sid INTEGER, "
    "owner_create_time REAL, "
    "owner_claimed_wall REAL, "
    "created_wall REAL NOT NULL, "
    "updated_wall REAL NOT NULL)"
)

_DDL_ATTEMPTS: str = (
    "CREATE TABLE attempts ("
    "attempt_id INTEGER PRIMARY KEY AUTOINCREMENT, "
    "work_item_id TEXT NOT NULL REFERENCES items(work_item_id), "
    "attempt_number INTEGER NOT NULL, "
    "status TEXT NOT NULL, "
    "started_wall REAL, "
    "finished_wall REAL, "
    "duration_monotonic REAL, "
    "environment_digest TEXT, "
    "error_json TEXT, "
    "diagnostics_json TEXT, "
    "result_json TEXT, "
    "recovery_json TEXT, "
    "UNIQUE(work_item_id, attempt_number))"
)

_DDL_ARTIFACT_ROWS: str = (
    "CREATE TABLE artifact_rows ("
    "artifact_id TEXT NOT NULL, "
    "work_item_id TEXT NOT NULL REFERENCES items(work_item_id), "
    "role TEXT NOT NULL, "
    "locator_path TEXT NOT NULL, "
    "checksum TEXT, "
    "size_bytes INTEGER, "
    "subject_structure_id TEXT, "
    "retention TEXT NOT NULL, "
    "metadata_json TEXT NOT NULL, "
    "PRIMARY KEY (work_item_id, artifact_id))"
)

_DDL_INDEXES: tuple[str, ...] = (
    "CREATE INDEX idx_attempts_item ON attempts(work_item_id, attempt_number)",
    "CREATE INDEX idx_items_status ON items(status)",
    "CREATE INDEX idx_items_logical ON items(logical_key)",
)


@dataclass(frozen=True, slots=True)
class StoredAttempt:
    """One durable execution attempt of a work item.

    Parameters
    ----------
    work_item_id : str
        Item this attempt belongs to.
    attempt_number : int
        One-based attempt index, ordered ascending per item.
    status : StoredWorkItemStatus
        Terminal or running status of this attempt.
    started_wall : float | None
        Wall-clock provenance of attempt start.
    finished_wall : float | None
        Wall-clock provenance of attempt finish, when finished.
    duration_monotonic : float | None
        Caller-reported duration (from result timing); never derived by
        subtracting wall timestamps.
    environment_digest : str | None
        Environment digest recorded for this attempt, when known.
    has_result : bool
        Whether a terminal result payload is stored for this attempt.
    """

    work_item_id: str
    attempt_number: int
    status: StoredWorkItemStatus
    started_wall: float | None = None
    finished_wall: float | None = None
    duration_monotonic: float | None = None
    environment_digest: str | None = None
    has_result: bool = False

    def to_dict(self) -> dict[str, Any]:
        """Return a canonical, JSON-compatible representation."""
        return {
            "work_item_id": self.work_item_id,
            "attempt_number": self.attempt_number,
            "status": self.status.value,
            "started_wall": self.started_wall,
            "finished_wall": self.finished_wall,
            "duration_monotonic": self.duration_monotonic,
            "environment_digest": self.environment_digest,
            "has_result": self.has_result,
        }


def _canonical_text(payload: Any) -> str:
    """Serialize *payload* to canonical JSON text, failing closed."""
    try:
        return canonical_json_bytes(payload).decode("utf-8")
    except (CanonicalizationError, DomainError) as exc:
        raise PersistenceError(f"value is not canonically serializable: {exc}") from exc


def _decode_json(text: str | None, *, field_name: str) -> Any:
    """Decode stored canonical JSON text, failing closed on corruption."""
    if text is None:
        return None
    try:
        return json.loads(text)
    except (json.JSONDecodeError, TypeError, ValueError) as exc:
        raise CorruptStateError(f"stored {field_name} is not valid JSON: {exc}") from exc


def _require_text(value: Any, field_name: str) -> str:
    """Validate *value* as a non-empty, unpadded string."""
    if not isinstance(value, str) or not value or value != value.strip():
        raise PersistenceError(f"{field_name} must be a non-empty string")
    return value


def _require_digest_text(value: Any, field_name: str) -> str:
    """Validate *value* as a non-empty digest string."""
    text = _require_text(value, field_name)
    if "\x00" in text:
        raise PersistenceError(f"{field_name} must not contain NUL")
    return text


def _decode_status(value: Any, *, field_name: str) -> StoredWorkItemStatus:
    """Decode a stored status string, failing closed on corruption."""
    try:
        return StoredWorkItemStatus(value)
    except ValueError as exc:
        raise CorruptStateError(f"stored {field_name} is invalid: {value!r}") from exc


def _build_diagnostic(payload: Any) -> Diagnostic:
    """Rebuild one diagnostic from a ``to_dict`` payload, failing closed."""
    if not isinstance(payload, dict):
        raise CorruptStateError("diagnostic payload must be a mapping")
    try:
        severity_raw = payload.get("severity", DiagnosticSeverity.ERROR.value)
        severity = DiagnosticSeverity(severity_raw)
    except ValueError as exc:
        raise CorruptStateError(f"diagnostic severity is invalid: {payload!r}") from exc
    try:
        details = payload.get("details", {})
        return Diagnostic(
            code=payload["code"],
            message=payload["message"],
            severity=severity,
            step_id=payload.get("step_id"),
            work_item_id=payload.get("work_item_id"),
            logical_key=payload.get("logical_key"),
            field_path=payload.get("field_path"),
            details=details if isinstance(details, dict) else {},
        )
    except (KeyError, TypeError, DomainError, ValueError) as exc:
        raise CorruptStateError(f"diagnostic payload is invalid: {exc}") from exc


def _build_structure(payload: Any) -> StructureRecord:
    """Rebuild one structure record from a ``to_dict`` payload."""
    if not isinstance(payload, dict):
        raise CorruptStateError("structure payload must be a mapping")
    try:
        metadata = payload.get("metadata", {})
        return StructureRecord(
            id=payload["id"],
            atoms=payload["atoms"],
            coordinates=payload["coordinates"],
            charge=payload.get("charge"),
            multiplicity=payload.get("multiplicity"),
            parent_ids=tuple(payload.get("parent_ids", ())),
            lineage_root_id=payload.get("lineage_root_id"),
            source_step_id=payload.get("source_step_id"),
            source_work_item_id=payload.get("source_work_item_id"),
            role=payload.get("role"),
            ordinal=payload.get("ordinal"),
            group_key=payload.get("group_key"),
            metadata=metadata if isinstance(metadata, dict) else {},
        )
    except (KeyError, TypeError, DomainError, ValueError) as exc:
        raise CorruptStateError(f"structure payload is invalid: {exc}") from exc


def _build_provenance(payload: Any) -> Provenance | None:
    """Rebuild result provenance from a ``to_dict`` payload."""
    if payload is None:
        return None
    if not isinstance(payload, dict):
        raise CorruptStateError("provenance payload must be a mapping or None")
    try:
        metadata = payload.get("metadata", {})
        return Provenance(
            program=payload.get("program"),
            program_version=payload.get("program_version"),
            method=payload.get("method"),
            basis=payload.get("basis"),
            adapter=payload.get("adapter"),
            step_id=payload.get("step_id"),
            work_item_id=payload.get("work_item_id"),
            metadata=metadata if isinstance(metadata, dict) else {},
        )
    except (TypeError, DomainError, ValueError) as exc:
        raise CorruptStateError(f"provenance payload is invalid: {exc}") from exc


def _build_scientific_result(payload: Any) -> ScientificResult:
    """Rebuild one scientific result from a ``to_dict`` payload."""
    if not isinstance(payload, dict):
        raise CorruptStateError("scientific result payload must be a mapping")
    try:
        unit_raw = payload.get("unit")
        unit = Unit(unit_raw) if unit_raw is not None else None
        quantity_raw = payload.get("quantity")
        quantity = QuantityKind(quantity_raw) if quantity_raw is not None else None
    except ValueError as exc:
        raise CorruptStateError(f"scientific result unit/quantity is invalid: {exc}") from exc
    try:
        metadata = payload.get("metadata", {})
        return ScientificResult(
            kind=payload["kind"],
            value=payload["value"],
            unit=unit,
            quantity=quantity,
            subject_structure_id=payload.get("subject_structure_id"),
            source_step_id=payload.get("source_step_id"),
            source_work_item_id=payload.get("source_work_item_id"),
            provenance=_build_provenance(payload.get("provenance")),
            metadata=metadata if isinstance(metadata, dict) else {},
        )
    except (KeyError, TypeError, DomainError, ValueError) as exc:
        raise CorruptStateError(f"scientific result payload is invalid: {exc}") from exc


def _build_artifact(payload: Any) -> ArtifactRef:
    """Rebuild one artifact reference from a ``to_dict`` payload."""
    if not isinstance(payload, dict):
        raise CorruptStateError("artifact payload must be a mapping")
    locator_payload = payload.get("locator")
    if not isinstance(locator_payload, dict):
        raise CorruptStateError("artifact locator payload must be a mapping")
    try:
        kind = LocatorKind(locator_payload["kind"])
        locator = ArtifactLocator(
            kind, path=locator_payload.get("path"), uri=locator_payload.get("uri")
        )
    except (KeyError, TypeError, DomainError, ValueError) as exc:
        raise CorruptStateError(f"artifact locator payload is invalid: {exc}") from exc
    try:
        retention = RetentionClass(payload.get("retention", RetentionClass.RETAINED.value))
    except ValueError as exc:
        raise CorruptStateError(f"artifact retention is invalid: {exc}") from exc
    try:
        metadata = payload.get("metadata", {})
        return ArtifactRef(
            id=payload["id"],
            role=payload["role"],
            locator=locator,
            checksum=payload.get("checksum"),
            media_type=payload.get("media_type"),
            program=payload.get("program"),
            producer_step_id=payload.get("producer_step_id"),
            producer_work_item_id=payload.get("producer_work_item_id"),
            subject_structure_id=payload.get("subject_structure_id"),
            retention=retention,
            metadata=metadata if isinstance(metadata, dict) else {},
        )
    except (KeyError, TypeError, DomainError, ValueError) as exc:
        raise CorruptStateError(f"artifact payload is invalid: {exc}") from exc


def _result_from_dict(payload: Any) -> WorkItemResult:
    """Rebuild a result from a ``to_dict`` payload, failing closed.

    Parameters
    ----------
    payload : Any
        Decoded JSON payload previously produced by
        :meth:`WorkItemResult.to_dict`.

    Returns
    -------
    WorkItemResult
        The reconstructed result.

    Raises
    ------
    CorruptStateError
        Raised when the payload is missing, misshaped, or violates any
        domain invariant.
    """
    if not isinstance(payload, dict):
        raise CorruptStateError("result payload must be a mapping")
    try:
        status = WorkItemStatus(payload["status"])
    except (KeyError, TypeError, ValueError) as exc:
        raise CorruptStateError(f"result status is invalid: {exc}") from exc
    work_item_id = payload.get("work_item_id")
    if not isinstance(work_item_id, str) or not work_item_id:
        raise CorruptStateError("result work_item_id must be a non-empty string")
    for key in ("structures", "results", "artifacts", "diagnostics"):
        value = payload.get(key, ())
        if value is None:
            continue
        if not isinstance(value, list):
            raise CorruptStateError(f"result field {key!r} must be a list")
    try:
        structures = StructureSet(
            tuple(_build_structure(entry) for entry in (payload.get("structures") or ()))
        )
        results = ResultSet(
            tuple(_build_scientific_result(entry) for entry in (payload.get("results") or ()))
        )
        artifacts = ArtifactSet(
            tuple(_build_artifact(entry) for entry in (payload.get("artifacts") or ()))
        )
        diagnostics = tuple(
            _build_diagnostic(entry) for entry in (payload.get("diagnostics") or ())
        )
    except CorruptStateError:
        raise
    except (TypeError, DomainError, ValueError) as exc:
        raise CorruptStateError(f"result collections are invalid: {exc}") from exc
    timing_payload = payload.get("timing")
    if timing_payload is not None:
        if not isinstance(timing_payload, dict):
            raise CorruptStateError("result timing must be a mapping or None")
        try:
            timing = Timing(
                started_at=timing_payload.get("started_at"),
                finished_at=timing_payload.get("finished_at"),
                duration_seconds=timing_payload.get("duration_seconds"),
            )
        except (TypeError, DomainError, ValueError) as exc:
            raise CorruptStateError(f"result timing is invalid: {exc}") from exc
    else:
        timing = None
    error_payload = payload.get("error")
    if error_payload is not None:
        if not isinstance(error_payload, dict):
            raise CorruptStateError("result error must be a mapping or None")
        try:
            details = error_payload.get("details", {})
            error = ResultError(
                code=error_payload["code"],
                message=error_payload["message"],
                retryable=bool(error_payload.get("retryable", False)),
                details=details if isinstance(details, dict) else {},
            )
        except (KeyError, TypeError, DomainError, ValueError) as exc:
            raise CorruptStateError(f"result error is invalid: {exc}") from exc
    else:
        error = None
    recovery_payload = payload.get("recovery")
    if recovery_payload is not None:
        if not isinstance(recovery_payload, dict):
            raise CorruptStateError("result recovery must be a mapping or None")
        try:
            details = recovery_payload.get("details", {})
            recovery = RecoveryInfo(
                profile=recovery_payload["profile"],
                attempted=bool(recovery_payload.get("attempted", False)),
                succeeded=recovery_payload.get("succeeded"),
                details=details if isinstance(details, dict) else {},
            )
        except (KeyError, TypeError, DomainError, ValueError) as exc:
            raise CorruptStateError(f"result recovery is invalid: {exc}") from exc
    else:
        recovery = None
    semantic_digest = payload.get("semantic_digest")
    if semantic_digest is not None and not isinstance(semantic_digest, str):
        raise CorruptStateError("result semantic_digest must be a string or None")
    metadata = payload.get("metadata", {})
    if not isinstance(metadata, dict):
        raise CorruptStateError("result metadata must be a mapping")
    try:
        return WorkItemResult(
            work_item_id=work_item_id,
            status=status,
            structures=structures,
            results=results,
            artifacts=artifacts,
            diagnostics=diagnostics,
            timing=timing,
            error=error,
            recovery=recovery,
            semantic_digest=semantic_digest,
            metadata=metadata,
        )
    except (TypeError, DomainError, ValueError) as exc:
        raise CorruptStateError(f"result payload violates domain invariants: {exc}") from exc


def _validate_store_path(path: str | Path) -> str:
    """Resolve *path* to an absolute durable store path, failing closed."""
    if isinstance(path, Path):
        raw = os.fspath(path)
    elif isinstance(path, str):
        raw = path
    else:
        raise PersistenceError("store path must be a string or Path")
    if raw == ":memory:":
        raise PersistenceError("durable store requires a file path, not :memory:")
    if not raw or not raw.strip():
        raise PersistenceError("store path must be a non-empty string")
    if "\x00" in raw:
        raise PersistenceError("store path must not contain NUL")
    absolute = os.path.abspath(raw)
    validate_run_root(absolute)
    ancestor = os.path.dirname(absolute)
    while ancestor and ancestor != os.path.dirname(ancestor):
        validate_run_root(ancestor)
        ancestor = os.path.dirname(ancestor)
    return absolute


class SqliteWorkItemStore:
    """Durable SQLite store for work-item execution truth.

    One store file owns one step's items.  Use :meth:`open` to create or
    reopen a store; the instance is a context manager that closes on exit.
    Instances are thread-safe: every method runs one short transaction
    under a single lock, so concurrent claims stay atomic.
    """

    def __init__(self, resolved_path: str, connection: sqlite3.Connection) -> None:
        """Build a store over an open, schema-validated connection."""
        self._path = resolved_path
        self._conn = connection
        self._lock = threading.RLock()
        self._closed = False

    @property
    def path(self) -> str:
        """Return the absolute store file path."""
        return self._path

    @classmethod
    def open(cls, path: str | Path, *, create: bool = True) -> SqliteWorkItemStore:
        """Open the durable store at *path*.

        Parameters
        ----------
        path : str | Path
            Store file path under a managed run root.  ``:memory:`` is
            refused because this store is durable only.
        create : bool
            When ``True`` (default), a missing file is created with the
            current schema.  When ``False``, a missing file raises
            :class:`PersistenceError`.

        Returns
        -------
        SqliteWorkItemStore
            The opened store.

        Raises
        ------
        PersistenceError
            Raised for unusable paths or a missing file with
            ``create=False``.
        SchemaVersionError
            Raised when the file carries another schema version.
        CorruptStateError
            Raised when the file is not a readable store (missing tables
            or corrupt meta).  Never drops, rebuilds, or migrates data.
        """
        resolved = _validate_store_path(path)
        existed = os.path.exists(resolved)
        if not existed and not create:
            raise PersistenceError(f"work-item store does not exist: {resolved}")
        if existed and os.path.isdir(resolved):
            raise PersistenceError(f"work-item store path is a directory: {resolved}")
        if create:
            parent = os.path.dirname(resolved)
            if parent:
                os.makedirs(parent, exist_ok=True)
        try:
            connection = sqlite3.connect(
                resolved, timeout=5.0, isolation_level=None, check_same_thread=False
            )
        except sqlite3.Error as exc:
            raise PersistenceError(f"cannot open work-item store: {exc}") from exc
        store = cls(resolved, connection)
        try:
            store._configure()
            if not existed:
                store._init_schema()
            else:
                store._check_schema()
        except (SchemaVersionError, CorruptStateError, PersistenceError):
            connection.close()
            raise
        except sqlite3.Error as exc:
            connection.close()
            raise CorruptStateError(f"work-item store is unreadable: {exc}") from exc
        return store

    def close(self) -> None:
        """Close the store connection; idempotent."""
        with self._lock:
            if not self._closed:
                self._closed = True
                try:
                    self._conn.close()
                except sqlite3.Error:
                    pass

    def __enter__(self) -> SqliteWorkItemStore:
        """Return this store for use as a context manager."""
        return self

    def __exit__(self, exc_type: Any, exc: Any, traceback: Any) -> None:
        """Close the store without suppressing exceptions."""
        self.close()

    # ------------------------------------------------------------------
    # Registration and claims
    # ------------------------------------------------------------------

    def register_item(
        self,
        *,
        work_item_id: str,
        logical_key: str,
        step_id: str,
        work_item_digest: str,
        step_semantic_digest: str,
        environment_digest: str | None = None,
        producer_provenance: dict | None = None,
    ) -> None:
        """Register one work item; idempotent on identical digests.

        Parameters
        ----------
        work_item_id : str
            Deterministic persistence address of the item.
        logical_key : str
            Stable logical address within the plan.
        step_id : str
            Step this item belongs to.
        work_item_digest : str
            Content identity controlling reuse validity.
        step_semantic_digest : str
            Semantic digest of the producing step definition.
        environment_digest : str | None
            Environment digest, when known.
        producer_provenance : dict | None
            Producer provenance mapping, stored as canonical JSON.

        Raises
        ------
        PersistenceError
            Raised for invalid arguments.
        CorruptStateError
            Raised when the id is already registered with conflicting
            digests or identity fields.
        """
        item_id = _require_text(work_item_id, "work_item_id")
        logical = _require_text(logical_key, "logical_key")
        step = _require_text(step_id, "step_id")
        item_digest = _require_digest_text(work_item_digest, "work_item_digest")
        step_digest = _require_digest_text(step_semantic_digest, "step_semantic_digest")
        environment: str | None = None
        if environment_digest is not None:
            environment = _require_digest_text(environment_digest, "environment_digest")
        provenance_text = _canonical_text(dict(producer_provenance or {}))
        with self._lock:
            connection = self._guard_open()
            try:
                connection.execute("BEGIN IMMEDIATE")
                row = connection.execute(
                    "SELECT logical_key, step_id, work_item_digest, step_semantic_digest,"
                    " environment_digest, producer_provenance FROM items WHERE work_item_id = ?",
                    (item_id,),
                ).fetchone()
                now = wall_now()
                if row is None:
                    connection.execute(
                        "INSERT INTO items (work_item_id, logical_key, step_id,"
                        " work_item_digest, step_semantic_digest, environment_digest,"
                        " producer_provenance, status, current_attempt,"
                        " created_wall, updated_wall)"
                        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?)",
                        (
                            item_id,
                            logical,
                            step,
                            item_digest,
                            step_digest,
                            environment,
                            provenance_text,
                            StoredWorkItemStatus.PENDING.value,
                            now,
                            now,
                        ),
                    )
                else:
                    (
                        existing_logical,
                        existing_step,
                        existing_item_digest,
                        existing_step_digest,
                        existing_environment,
                        existing_provenance,
                    ) = row
                    if (
                        existing_logical != logical
                        or existing_step != step
                        or existing_item_digest != item_digest
                        or existing_step_digest != step_digest
                        or existing_environment != environment
                        or existing_provenance != provenance_text
                    ):
                        raise CorruptStateError(
                            f"work item {item_id!r} is already registered "
                            "with conflicting digests or identity"
                        )
                    connection.execute(
                        "UPDATE items SET updated_wall = ? WHERE work_item_id = ?",
                        (now, item_id),
                    )
                connection.execute("COMMIT")
            except (CorruptStateError, PersistenceError):
                connection.execute("ROLLBACK")
                raise
            except sqlite3.Error as exc:
                connection.execute("ROLLBACK")
                raise PersistenceError(f"cannot register work item: {exc}") from exc

    def claim(self, work_item_id: str, *, owner: OwnerIdentity) -> bool:
        """Claim one item for execution in a single atomic transaction.

        ``PENDING`` items and retryable ``FAILED``/``INTERRUPTED`` items move
        to ``RUNNING`` and gain a new attempt row.  Already ``RUNNING``,
        ``COMPLETED``, or ``CANCELLED`` items — and unknown ids — return
        ``False``; contention never raises.

        Parameters
        ----------
        work_item_id : str
            Item to claim.
        owner : OwnerIdentity
            Claim owner recorded on the item row.

        Returns
        -------
        bool
            ``True`` when the claim was granted.
        """
        if not isinstance(owner, OwnerIdentity):
            raise PersistenceError("owner must be an OwnerIdentity")
        item_id = _require_text(work_item_id, "work_item_id")
        with self._lock:
            connection = self._guard_open()
            try:
                connection.execute("BEGIN IMMEDIATE")
                row = connection.execute(
                    "SELECT status, current_attempt FROM items WHERE work_item_id = ?",
                    (item_id,),
                ).fetchone()
                if row is None:
                    connection.execute("COMMIT")
                    return False
                try:
                    status = StoredWorkItemStatus(row[0])
                except ValueError as exc:
                    raise CorruptStateError(f"stored status is invalid: {row[0]!r}") from exc
                if status in (
                    StoredWorkItemStatus.RUNNING,
                    StoredWorkItemStatus.COMPLETED,
                    StoredWorkItemStatus.CANCELLED,
                ):
                    connection.execute("COMMIT")
                    return False
                if status not in (
                    StoredWorkItemStatus.PENDING,
                    StoredWorkItemStatus.FAILED,
                    StoredWorkItemStatus.INTERRUPTED,
                ):
                    raise CorruptStateError(f"stored status is invalid: {row[0]!r}")
                attempt_number = int(row[1]) + 1
                now = wall_now()
                started = owner.claimed_wall if owner.claimed_wall is not None else now
                connection.execute(
                    "UPDATE items SET status = ?, current_attempt = ?, owner_token = ?,"
                    " owner_pid = ?, owner_pgid = ?, owner_sid = ?,"
                    " owner_create_time = ?, owner_claimed_wall = ?, updated_wall = ?"
                    " WHERE work_item_id = ?",
                    (
                        StoredWorkItemStatus.RUNNING.value,
                        attempt_number,
                        owner.owner_token,
                        owner.pid,
                        owner.process_group_id,
                        owner.session_id,
                        owner.create_time,
                        owner.claimed_wall if owner.claimed_wall is not None else now,
                        now,
                        item_id,
                    ),
                )
                connection.execute(
                    "INSERT INTO attempts (work_item_id, attempt_number, status, started_wall)"
                    " VALUES (?, ?, ?, ?)",
                    (
                        item_id,
                        attempt_number,
                        StoredWorkItemStatus.RUNNING.value,
                        float(started),
                    ),
                )
                connection.execute("COMMIT")
                return True
            except (CorruptStateError, PersistenceError):
                connection.execute("ROLLBACK")
                raise
            except sqlite3.OperationalError as exc:
                try:
                    connection.execute("ROLLBACK")
                except sqlite3.Error:
                    pass
                if _is_lock_contention(exc):
                    # Genuine contention (another claimant holds the write
                    # lock past our bounded busy timeout): a clean conflict,
                    # never an error.
                    return False
                raise PersistenceError(f"work-item store I/O failure during claim: {exc}") from exc
            except sqlite3.IntegrityError as exc:
                try:
                    connection.execute("ROLLBACK")
                except sqlite3.Error:
                    pass
                raise CorruptStateError(
                    f"work-item store integrity failure during claim: {exc}"
                ) from exc
            except sqlite3.Error as exc:
                try:
                    connection.execute("ROLLBACK")
                except sqlite3.Error:
                    pass
                raise PersistenceError(f"work-item store failure during claim: {exc}") from exc

    # ------------------------------------------------------------------
    # Terminal transitions
    # ------------------------------------------------------------------

    def complete(
        self,
        work_item_id: str,
        *,
        result: WorkItemResult,
        environment_digest: str | None = None,
    ) -> None:
        """Record a completed result for a ``RUNNING`` item.

        All payload validation happens before the transaction opens, so a
        rejected payload leaves the item ``RUNNING`` with attempt history
        untouched.
        """
        item_id = _require_text(work_item_id, "work_item_id")
        if not isinstance(result, WorkItemResult):
            raise PersistenceError("result must be a WorkItemResult")
        if result.work_item_id != item_id:
            raise PersistenceError("result work_item_id does not match the stored item")
        if result.status is not WorkItemStatus.COMPLETED:
            raise PersistenceError("complete() requires a COMPLETED result")
        environment: str | None = None
        if environment_digest is not None:
            environment = _require_digest_text(environment_digest, "environment_digest")
        payload = result.to_dict()
        result_text = _canonical_text(payload)
        diagnostics_text = _canonical_text(payload["diagnostics"])
        recovery_text = _canonical_text(payload["recovery"])
        duration: float | None = None
        if result.timing is not None and result.timing.duration_seconds is not None:
            duration = float(result.timing.duration_seconds)
        with self._lock:
            connection = self._guard_open()
            try:
                connection.execute("BEGIN IMMEDIATE")
                attempt = self._require_running(connection, item_id, "complete")
                now = wall_now()
                connection.execute(
                    "UPDATE attempts SET status = ?, finished_wall = ?,"
                    " duration_monotonic = ?, environment_digest = ?,"
                    " diagnostics_json = ?, result_json = ?, recovery_json = ?"
                    " WHERE work_item_id = ? AND attempt_number = ?",
                    (
                        StoredWorkItemStatus.COMPLETED.value,
                        now,
                        duration,
                        environment,
                        diagnostics_text,
                        result_text,
                        recovery_text,
                        item_id,
                        attempt,
                    ),
                )
                if environment is not None:
                    connection.execute(
                        "UPDATE items SET status = ?, environment_digest = ?,"
                        " updated_wall = ? WHERE work_item_id = ?",
                        (StoredWorkItemStatus.COMPLETED.value, environment, now, item_id),
                    )
                else:
                    connection.execute(
                        "UPDATE items SET status = ?, updated_wall = ?" " WHERE work_item_id = ?",
                        (StoredWorkItemStatus.COMPLETED.value, now, item_id),
                    )
                self._rewrite_artifact_rows(connection, item_id, result)
                connection.execute("COMMIT")
            except (StateTransitionError, CorruptStateError, PersistenceError):
                connection.execute("ROLLBACK")
                raise
            except sqlite3.Error as exc:
                connection.execute("ROLLBACK")
                raise PersistenceError(f"cannot complete work item: {exc}") from exc

    def fail(self, work_item_id: str, *, result: WorkItemResult) -> None:
        """Record a failed result for a ``RUNNING`` item."""
        item_id = _require_text(work_item_id, "work_item_id")
        if not isinstance(result, WorkItemResult):
            raise PersistenceError("result must be a WorkItemResult")
        if result.work_item_id != item_id:
            raise PersistenceError("result work_item_id does not match the stored item")
        if result.status is not WorkItemStatus.FAILED:
            raise PersistenceError("fail() requires a FAILED result")
        payload = result.to_dict()
        result_text = _canonical_text(payload)
        diagnostics_text = _canonical_text(payload["diagnostics"])
        error_text = _canonical_text(payload["error"]) if payload["error"] is not None else None
        recovery_text = _canonical_text(payload["recovery"])
        duration: float | None = None
        if result.timing is not None and result.timing.duration_seconds is not None:
            duration = float(result.timing.duration_seconds)
        with self._lock:
            connection = self._guard_open()
            try:
                connection.execute("BEGIN IMMEDIATE")
                attempt = self._require_running(connection, item_id, "fail")
                now = wall_now()
                connection.execute(
                    "UPDATE attempts SET status = ?, finished_wall = ?,"
                    " duration_monotonic = ?, error_json = ?, diagnostics_json = ?,"
                    " result_json = ?, recovery_json = ?"
                    " WHERE work_item_id = ? AND attempt_number = ?",
                    (
                        StoredWorkItemStatus.FAILED.value,
                        now,
                        duration,
                        error_text,
                        diagnostics_text,
                        result_text,
                        recovery_text,
                        item_id,
                        attempt,
                    ),
                )
                connection.execute(
                    "UPDATE items SET status = ?, updated_wall = ? WHERE work_item_id = ?",
                    (StoredWorkItemStatus.FAILED.value, now, item_id),
                )
                connection.execute("COMMIT")
            except (StateTransitionError, CorruptStateError, PersistenceError):
                connection.execute("ROLLBACK")
                raise
            except sqlite3.Error as exc:
                connection.execute("ROLLBACK")
                raise PersistenceError(f"cannot fail work item: {exc}") from exc

    def cancel_item(self, work_item_id: str, *, reason: str = "") -> None:
        """Move a ``RUNNING`` item to ``CANCELLED``."""
        item_id = _require_text(work_item_id, "work_item_id")
        if not isinstance(reason, str):
            raise PersistenceError("reason must be a string")
        with self._lock:
            connection = self._guard_open()
            try:
                connection.execute("BEGIN IMMEDIATE")
                attempt = self._require_running(connection, item_id, "cancel")
                now = wall_now()
                connection.execute(
                    "UPDATE attempts SET status = ?, finished_wall = ?"
                    " WHERE work_item_id = ? AND attempt_number = ?",
                    (StoredWorkItemStatus.CANCELLED.value, now, item_id, attempt),
                )
                connection.execute(
                    "UPDATE items SET status = ?, updated_wall = ? WHERE work_item_id = ?",
                    (StoredWorkItemStatus.CANCELLED.value, now, item_id),
                )
                connection.execute("COMMIT")
            except (StateTransitionError, CorruptStateError, PersistenceError):
                connection.execute("ROLLBACK")
                raise
            except sqlite3.Error as exc:
                connection.execute("ROLLBACK")
                raise PersistenceError(f"cannot cancel work item: {exc}") from exc

    def mark_interrupted(self, work_item_id: str, *, reason: str = "") -> None:
        """Move a ``RUNNING`` item to ``INTERRUPTED`` for later recovery."""
        item_id = _require_text(work_item_id, "work_item_id")
        if not isinstance(reason, str):
            raise PersistenceError("reason must be a string")
        with self._lock:
            connection = self._guard_open()
            try:
                connection.execute("BEGIN IMMEDIATE")
                attempt = self._require_running(connection, item_id, "interrupt")
                now = wall_now()
                connection.execute(
                    "UPDATE attempts SET status = ?, finished_wall = ?"
                    " WHERE work_item_id = ? AND attempt_number = ?",
                    (StoredWorkItemStatus.INTERRUPTED.value, now, item_id, attempt),
                )
                connection.execute(
                    "UPDATE items SET status = ?, updated_wall = ? WHERE work_item_id = ?",
                    (StoredWorkItemStatus.INTERRUPTED.value, now, item_id),
                )
                connection.execute("COMMIT")
            except (StateTransitionError, CorruptStateError, PersistenceError):
                connection.execute("ROLLBACK")
                raise
            except sqlite3.Error as exc:
                connection.execute("ROLLBACK")
                raise PersistenceError(f"cannot interrupt work item: {exc}") from exc

    # ------------------------------------------------------------------
    # Readers
    # ------------------------------------------------------------------

    def get_state(self, work_item_id: str) -> StoredWorkItemStatus | None:
        """Return the durable status of *work_item_id*, or ``None``."""
        item_id = _require_text(work_item_id, "work_item_id")
        with self._lock:
            connection = self._guard_open()
            try:
                row = connection.execute(
                    "SELECT status FROM items WHERE work_item_id = ?", (item_id,)
                ).fetchone()
            except sqlite3.Error as exc:
                raise PersistenceError(f"cannot read work-item state: {exc}") from exc
        if row is None:
            return None
        return _decode_status(row[0], field_name="status")

    def get_owner(self, work_item_id: str) -> OwnerIdentity | None:
        """Return the recorded claim owner, or ``None`` when unclaimed."""
        item_id = _require_text(work_item_id, "work_item_id")
        with self._lock:
            connection = self._guard_open()
            try:
                row = connection.execute(
                    "SELECT owner_token, owner_pid, owner_pgid, owner_sid,"
                    " owner_create_time, owner_claimed_wall"
                    " FROM items WHERE work_item_id = ?",
                    (item_id,),
                ).fetchone()
            except sqlite3.Error as exc:
                raise PersistenceError(f"cannot read work-item owner: {exc}") from exc
        if row is None or row[0] is None:
            return None
        try:
            return OwnerIdentity(
                owner_token=row[0],
                pid=row[1],
                process_group_id=row[2],
                session_id=row[3],
                create_time=row[4],
                claimed_wall=row[5],
            )
        except (PersistenceError, TypeError, ValueError) as exc:
            raise CorruptStateError(f"stored owner identity is invalid: {exc}") from exc

    def get_attempts(self, work_item_id: str) -> tuple[StoredAttempt, ...]:
        """Return attempt records ordered by attempt number."""
        item_id = _require_text(work_item_id, "work_item_id")
        with self._lock:
            connection = self._guard_open()
            try:
                rows = connection.execute(
                    "SELECT work_item_id, attempt_number, status, started_wall,"
                    " finished_wall, duration_monotonic, environment_digest,"
                    " result_json FROM attempts WHERE work_item_id = ?"
                    " ORDER BY attempt_number ASC",
                    (item_id,),
                ).fetchall()
            except sqlite3.Error as exc:
                raise PersistenceError(f"cannot read attempts: {exc}") from exc
        attempts: list[StoredAttempt] = []
        for row in rows:
            attempts.append(
                StoredAttempt(
                    work_item_id=row[0],
                    attempt_number=int(row[1]),
                    status=_decode_status(row[2], field_name="attempt status"),
                    started_wall=row[3],
                    finished_wall=row[4],
                    duration_monotonic=row[5],
                    environment_digest=row[6],
                    has_result=row[7] is not None,
                )
            )
        return tuple(attempts)

    def list_items(self, status: StoredWorkItemStatus | None = None) -> tuple[str, ...]:
        """Return work-item ids sorted by logical key for determinism."""
        if status is not None and not isinstance(status, StoredWorkItemStatus):
            raise PersistenceError("status must be a StoredWorkItemStatus or None")
        with self._lock:
            connection = self._guard_open()
            try:
                if status is None:
                    rows = connection.execute(
                        "SELECT work_item_id FROM items"
                        " ORDER BY logical_key ASC, work_item_id ASC"
                    ).fetchall()
                else:
                    rows = connection.execute(
                        "SELECT work_item_id FROM items WHERE status = ?"
                        " ORDER BY logical_key ASC, work_item_id ASC",
                        (status.value,),
                    ).fetchall()
            except sqlite3.Error as exc:
                raise PersistenceError(f"cannot list work items: {exc}") from exc
        return tuple(row[0] for row in rows)

    def get_registered(self, work_item_id: str) -> dict[str, Any]:
        """Return the registered digest and provenance fields of one item."""
        item_id = _require_text(work_item_id, "work_item_id")
        with self._lock:
            connection = self._guard_open()
            try:
                row = connection.execute(
                    "SELECT work_item_id, logical_key, step_id, work_item_digest,"
                    " step_semantic_digest, environment_digest, producer_provenance,"
                    " status, current_attempt FROM items WHERE work_item_id = ?",
                    (item_id,),
                ).fetchone()
            except sqlite3.Error as exc:
                raise PersistenceError(f"cannot read registration: {exc}") from exc
        if row is None:
            raise PersistenceError(f"work item is not registered: {item_id!r}")
        provenance = _decode_json(row[6], field_name="producer_provenance")
        if not isinstance(provenance, dict):
            raise CorruptStateError("stored producer provenance must be a mapping")
        return {
            "work_item_id": row[0],
            "logical_key": row[1],
            "step_id": row[2],
            "work_item_digest": row[3],
            "step_semantic_digest": row[4],
            "environment_digest": row[5],
            "producer_provenance": provenance,
            "status": _decode_status(row[7], field_name="status").value,
            "current_attempt": int(row[8]),
        }

    def list_artifact_rows(self, work_item_id: str) -> tuple[dict[str, Any], ...]:
        """Return durable artifact rows of one item ordered by artifact id."""
        item_id = _require_text(work_item_id, "work_item_id")
        with self._lock:
            connection = self._guard_open()
            try:
                rows = connection.execute(
                    "SELECT artifact_id, work_item_id, role, locator_path, checksum,"
                    " size_bytes, subject_structure_id, retention, metadata_json"
                    " FROM artifact_rows WHERE work_item_id = ?"
                    " ORDER BY artifact_id ASC",
                    (item_id,),
                ).fetchall()
            except sqlite3.Error as exc:
                raise PersistenceError(f"cannot read artifact rows: {exc}") from exc
        entries: list[dict[str, Any]] = []
        for row in rows:
            metadata = _decode_json(row[8], field_name="artifact metadata")
            if not isinstance(metadata, dict):
                raise CorruptStateError("stored artifact metadata must be a mapping")
            entries.append(
                {
                    "artifact_id": row[0],
                    "work_item_id": row[1],
                    "role": row[2],
                    "locator_path": row[3],
                    "checksum": row[4],
                    "size_bytes": row[5],
                    "subject_structure_id": row[6],
                    "retention": row[7],
                    "metadata": metadata,
                }
            )
        return tuple(entries)

    def get_result(self, work_item_id: str) -> WorkItemResult | None:
        """Return the latest terminal result, or ``None`` when absent."""
        item_id = _require_text(work_item_id, "work_item_id")
        with self._lock:
            connection = self._guard_open()
            try:
                row = connection.execute(
                    "SELECT result_json FROM attempts WHERE work_item_id = ?"
                    " AND result_json IS NOT NULL ORDER BY attempt_number DESC LIMIT 1",
                    (item_id,),
                ).fetchone()
            except sqlite3.Error as exc:
                raise PersistenceError(f"cannot read work-item result: {exc}") from exc
        if row is None:
            return None
        payload = _decode_json(row[0], field_name="result")
        return _result_from_dict(payload)

    # ------------------------------------------------------------------
    # Execution ports (duck-type the batch protocols without importing them)
    # ------------------------------------------------------------------

    def record_started(self, work_item_id: str, step_id: str, logical_key: str) -> None:
        """Record execution start, claiming the item with an ephemeral owner.

        The item must already be registered; unknown ids raise
        :class:`PersistenceError`.  ``PENDING`` and retryable
        ``FAILED``/``INTERRUPTED`` items are claimed; already ``RUNNING``
        items are verified against *step_id*/*logical_key* and left alone;
        terminal items raise :class:`PersistenceError` so duplicate launches
        fail closed instead of silently replaying.
        """
        item_id = _require_text(work_item_id, "work_item_id")
        step = _require_text(step_id, "step_id")
        logical = _require_text(logical_key, "logical_key")
        with self._lock:
            connection = self._guard_open()
            try:
                row = connection.execute(
                    "SELECT logical_key, step_id, status FROM items WHERE work_item_id = ?",
                    (item_id,),
                ).fetchone()
            except sqlite3.Error as exc:
                raise PersistenceError(f"cannot record start: {exc}") from exc
        if row is None:
            raise PersistenceError(f"work item is not registered: {item_id!r}")
        if row[0] != logical or row[1] != step:
            raise PersistenceError(f"work item {item_id!r} identity does not match registration")
        status = _decode_status(row[2], field_name="status")
        if status is StoredWorkItemStatus.RUNNING:
            return
        if status in (StoredWorkItemStatus.COMPLETED, StoredWorkItemStatus.CANCELLED):
            raise PersistenceError(f"work item {item_id!r} is already terminal: {status.value}")
        claimed = self.claim(
            item_id,
            owner=OwnerIdentity(owner_token=f"ephemeral:{item_id}", claimed_wall=wall_now()),
        )
        if not claimed:
            raise PersistenceError(f"work item {item_id!r} could not be claimed")

    def record_finished(self, result: WorkItemResult) -> None:
        """Record the terminal result of a ``RUNNING`` item.

        ``COMPLETED``/``FAILED`` results persist through the matching
        terminal transition; ``CANCELLED`` results move the item to
        ``CANCELLED`` while still persisting the result payload so
        :meth:`get_result` can return it.
        """
        if not isinstance(result, WorkItemResult):
            raise PersistenceError("result must be a WorkItemResult")
        if result.status is WorkItemStatus.COMPLETED:
            self.complete(result.work_item_id, result=result)
        elif result.status is WorkItemStatus.FAILED:
            self.fail(result.work_item_id, result=result)
        else:
            self._record_cancelled(result)

    def lookup(self, semantic_digest: str) -> WorkItemResult | None:
        """Return a reusable completed result for *semantic_digest*, if any.

        Terminal ``COMPLETED`` attempts are scanned in deterministic
        (logical-key, work-item-id) order; the first row whose registered
        work-item digest — or whose stored result semantic digest — equals
        *semantic_digest* wins.  Logical keys are never compared.
        """
        digest = _require_digest_text(semantic_digest, "semantic_digest")
        with self._lock:
            connection = self._guard_open()
            try:
                rows = connection.execute(
                    "SELECT a.result_json, a.work_item_id, i.work_item_digest"
                    " FROM attempts a JOIN items i ON a.work_item_id = i.work_item_id"
                    " WHERE a.status = ? AND a.result_json IS NOT NULL"
                    " ORDER BY i.logical_key ASC, i.work_item_id ASC",
                    (StoredWorkItemStatus.COMPLETED.value,),
                ).fetchall()
            except sqlite3.Error as exc:
                raise PersistenceError(f"cannot scan reuse candidates: {exc}") from exc
        for result_json, _candidate_id, item_digest in rows:
            if item_digest == digest:
                return _result_from_dict(_decode_json(result_json, field_name="result"))
        for result_json, _candidate_id, _item_digest in rows:
            payload = _decode_json(result_json, field_name="result")
            if isinstance(payload, dict) and payload.get("semantic_digest") == digest:
                return _result_from_dict(payload)
        return None

    def store(self, result: WorkItemResult) -> None:
        """Acknowledge a completed result for future reuse lookups.

        This is intentionally a no-op beyond validation: the durable store
        only indexes results already completed through :meth:`complete`, so
        results unknown to the store are ignored rather than indexed.  This
        differs from the in-memory reuse store, which indexes any completed
        result handed to it, because the durable store must never invent
        execution history it did not observe.
        """
        if not isinstance(result, WorkItemResult):
            raise PersistenceError("result must be a WorkItemResult")
        if not result.is_completed or result.semantic_digest is None:
            return
        item_id = _require_text(result.work_item_id, "work_item_id")
        with self._lock:
            connection = self._guard_open()
            try:
                row = connection.execute(
                    "SELECT status FROM items WHERE work_item_id = ?", (item_id,)
                ).fetchone()
            except sqlite3.Error as exc:
                raise PersistenceError(f"cannot inspect reuse index: {exc}") from exc
        if row is None:
            return
        _decode_status(row[0], field_name="status")

    # ------------------------------------------------------------------
    # Internal helpers (caller must hold the lock unless noted)
    # ------------------------------------------------------------------

    def _guard_open(self) -> sqlite3.Connection:
        """Return the live connection or raise when closed."""
        if self._closed:
            raise PersistenceError("work-item store is closed")
        return self._conn

    def _configure(self) -> None:
        """Enable WAL mode and a bounded busy timeout."""
        try:
            self._conn.execute(f"PRAGMA busy_timeout = {_BUSY_TIMEOUT_MS}")
            self._conn.execute("PRAGMA journal_mode = WAL")
        except sqlite3.DatabaseError as exc:
            raise CorruptStateError(f"work-item store is not a readable database: {exc}") from exc
        except sqlite3.Error as exc:
            raise PersistenceError(f"cannot configure work-item store: {exc}") from exc

    def _init_schema(self) -> None:
        """Create the schema and stamp the version row in one transaction."""
        try:
            self._conn.execute("BEGIN IMMEDIATE")
            self._conn.execute(_DDL_META)
            self._conn.execute(_DDL_ITEMS)
            self._conn.execute(_DDL_ATTEMPTS)
            self._conn.execute(_DDL_ARTIFACT_ROWS)
            for statement in _DDL_INDEXES:
                self._conn.execute(statement)
            self._conn.execute(
                "INSERT INTO meta (kind, value) VALUES (?, ?)",
                (_SCHEMA_KIND_ROW, str(PERSISTENCE_SCHEMA_VERSION)),
            )
            self._conn.execute("COMMIT")
        except sqlite3.Error as exc:
            try:
                self._conn.execute("ROLLBACK")
            except sqlite3.Error:
                pass
            raise PersistenceError(f"cannot initialize work-item store: {exc}") from exc

    def _check_schema(self) -> None:
        """Validate tables and the version row, failing closed."""
        try:
            tables = {
                row[0]
                for row in self._conn.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                ).fetchall()
            }
        except sqlite3.DatabaseError as exc:
            raise CorruptStateError(f"work-item store is not a readable database: {exc}") from exc
        except sqlite3.Error as exc:
            raise CorruptStateError(f"work-item store is unreadable: {exc}") from exc
        missing = [name for name in _REQUIRED_TABLES if name not in tables]
        if missing:
            raise CorruptStateError(f"work-item store is missing tables: {', '.join(missing)}")
        try:
            rows = self._conn.execute(
                "SELECT value FROM meta WHERE kind = ?", (_SCHEMA_KIND_ROW,)
            ).fetchall()
        except sqlite3.Error as exc:
            raise CorruptStateError(f"work-item store meta is unreadable: {exc}") from exc
        if not rows:
            raise CorruptStateError("work-item store has no schema version row")
        try:
            version = int(rows[0][0])
        except (TypeError, ValueError) as exc:
            raise CorruptStateError(
                f"work-item store schema version is corrupt: {rows[0][0]!r}"
            ) from exc
        if version != PERSISTENCE_SCHEMA_VERSION:
            raise SchemaVersionError(
                f"work-item store schema version {version} is not supported"
                f" (expected {PERSISTENCE_SCHEMA_VERSION})"
            )

    def _require_running(
        self, connection: sqlite3.Connection, work_item_id: str, action: str
    ) -> int:
        """Return the current attempt number when the item is ``RUNNING``."""
        row = connection.execute(
            "SELECT status, current_attempt FROM items WHERE work_item_id = ?",
            (work_item_id,),
        ).fetchone()
        if row is None:
            raise PersistenceError(f"work item is not registered: {work_item_id!r}")
        status = _decode_status(row[0], field_name="status")
        if status is not StoredWorkItemStatus.RUNNING:
            raise StateTransitionError(
                f"cannot {action} work item {work_item_id!r} from status {status.value!r}"
            )
        attempt_number = int(row[1])
        attempt_row = connection.execute(
            "SELECT attempt_id FROM attempts WHERE work_item_id = ? AND attempt_number = ?",
            (work_item_id, attempt_number),
        ).fetchone()
        if attempt_row is None:
            raise CorruptStateError(f"running work item {work_item_id!r} has no attempt row")
        return attempt_number

    def _rewrite_artifact_rows(
        self, connection: sqlite3.Connection, work_item_id: str, result: WorkItemResult
    ) -> None:
        """Replace durable artifact rows from a completed result."""
        connection.execute("DELETE FROM artifact_rows WHERE work_item_id = ?", (work_item_id,))
        for record in sorted(result.artifacts, key=lambda entry: entry.id):
            locator = record.locator
            if locator.kind is LocatorKind.RUN_RELATIVE:
                if locator.path is None:
                    raise PersistenceError("run-relative locator has no path")
                ArtifactLocator.run_relative(locator.path)
                locator_target = locator.path
            else:
                if locator.uri is None:
                    raise PersistenceError("external-uri locator has no uri")
                locator_target = locator.uri
            connection.execute(
                "INSERT INTO artifact_rows (artifact_id, work_item_id, role, locator_path,"
                " checksum, size_bytes, subject_structure_id, retention, metadata_json)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    record.id,
                    work_item_id,
                    record.role,
                    locator_target,
                    record.checksum,
                    None,
                    record.subject_structure_id,
                    record.retention.value,
                    _canonical_text(record.metadata.thaw()),
                ),
            )

    def _record_cancelled(self, result: WorkItemResult) -> None:
        """Persist a ``CANCELLED`` result through the cancel transition."""
        item_id = _require_text(result.work_item_id, "work_item_id")
        payload = result.to_dict()
        result_text = _canonical_text(payload)
        diagnostics_text = _canonical_text(payload["diagnostics"])
        recovery_text = _canonical_text(payload["recovery"])
        duration: float | None = None
        if result.timing is not None and result.timing.duration_seconds is not None:
            duration = float(result.timing.duration_seconds)
        with self._lock:
            connection = self._guard_open()
            try:
                connection.execute("BEGIN IMMEDIATE")
                attempt = self._require_running(connection, item_id, "cancel")
                now = wall_now()
                connection.execute(
                    "UPDATE attempts SET status = ?, finished_wall = ?,"
                    " duration_monotonic = ?, diagnostics_json = ?,"
                    " result_json = ?, recovery_json = ?"
                    " WHERE work_item_id = ? AND attempt_number = ?",
                    (
                        StoredWorkItemStatus.CANCELLED.value,
                        now,
                        duration,
                        diagnostics_text,
                        result_text,
                        recovery_text,
                        item_id,
                        attempt,
                    ),
                )
                connection.execute(
                    "UPDATE items SET status = ?, updated_wall = ? WHERE work_item_id = ?",
                    (StoredWorkItemStatus.CANCELLED.value, now, item_id),
                )
                connection.execute("COMMIT")
            except (StateTransitionError, CorruptStateError, PersistenceError):
                connection.execute("ROLLBACK")
                raise
            except sqlite3.Error as exc:
                connection.execute("ROLLBACK")
                raise PersistenceError(f"cannot cancel work item: {exc}") from exc
