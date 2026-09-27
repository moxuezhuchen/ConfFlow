#!/usr/bin/env python3

"""Durable current-generation identity for formal V4 runs.

A run root may hold several formal executions over its lifetime (initial
run plus explicit resumes).  ``run_result.json`` alone cannot tell a
consumer whether its ``completed`` status belongs to the invocation that
is running now or to a superseded generation: when a new generation fails
during assembly before the manifest is rewritten, the old completed
manifest would otherwise masquerade as current truth.

``run_generation.json`` is the durable lifecycle record that closes this
hole:

- Written ``running`` before any step executes (before compile even), so a
  consumer can distinguish "old completed generation" from "current
  running generation".
- Rewritten terminal (``completed``/``partial``/``failed``/``cancelled``)
  after the generation's manifest is published, carrying the manifest's
  generation id, the failure location, and the completed step ids.
- Written terminal even when the invocation raises (assembly error,
  blocked downstream step, persistence failure, compile failure), before
  the exception is re-raised, so a failed current generation is never
  invisible.

The record is operational lifecycle truth, not scientific identity: it
never enters definition/step/work-item/environment digests.  It is
published atomically with file and directory fsync.
"""

from __future__ import annotations

import itertools
import os
import uuid
from dataclasses import dataclass
from typing import Any

from ..domain._immutable import FrozenDict
from ..domain.canonical import canonical_json_bytes
from .contracts import CorruptStateError, validate_run_root, wall_now

__all__ = [
    "GENERATION_STATUSES",
    "RUN_GENERATION_FILENAME",
    "RUN_GENERATION_SCHEMA",
    "RunGeneration",
    "load_run_generation",
    "new_generation_id",
    "save_run_generation",
]

#: Durable lifecycle record filename at the run root.
RUN_GENERATION_FILENAME = "run_generation.json"

#: Schema marker of the generation record payload.
RUN_GENERATION_SCHEMA = "confflow.run_generation.v1"

#: Allowed lifecycle statuses of one formal generation.
GENERATION_STATUSES: frozenset[str] = frozenset(
    {"running", "completed", "partial", "failed", "cancelled"}
)

_TMP_COUNTER = itertools.count()


def new_generation_id() -> str:
    """Return a fresh opaque generation id (never reused, never positional)."""
    return f"gen-{uuid.uuid4().hex}"


@dataclass(frozen=True, slots=True)
class RunGeneration:
    """Durable lifecycle state of one formal run/resume generation."""

    run_id: str
    generation_id: str
    status: str
    definition_digest: str | None = None
    started_wall: float | None = None
    updated_wall: float | None = None
    manifest_generation_id: str | None = None
    completed_step_ids: tuple[str, ...] = ()
    active_step_id: str | None = None
    failure: FrozenDict | None = None
    diagnostics: tuple[FrozenDict, ...] = ()

    def __post_init__(self) -> None:
        for name in ("run_id", "generation_id"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise CorruptStateError(f"{name} must be a non-empty string")
        if self.status not in GENERATION_STATUSES:
            raise CorruptStateError(f"unknown generation status {self.status!r}")
        if self.definition_digest is not None and (
            not isinstance(self.definition_digest, str) or not self.definition_digest.strip()
        ):
            raise CorruptStateError("definition_digest must be a non-empty string or None")
        object.__setattr__(self, "completed_step_ids", tuple(self.completed_step_ids))
        if self.failure is not None and not isinstance(self.failure, FrozenDict):
            object.__setattr__(self, "failure", FrozenDict(self.failure))
        object.__setattr__(self, "diagnostics", tuple(self.diagnostics))

    @property
    def is_terminal(self) -> bool:
        """Return whether this generation reached a terminal status."""
        return self.status != "running"

    def to_dict(self) -> dict[str, Any]:
        """Return the canonical, JSON-compatible payload."""
        return {
            "schema": RUN_GENERATION_SCHEMA,
            "run_id": self.run_id,
            "generation_id": self.generation_id,
            "status": self.status,
            "definition_digest": self.definition_digest,
            "started_wall": self.started_wall,
            "updated_wall": self.updated_wall,
            "manifest_generation_id": self.manifest_generation_id,
            "completed_step_ids": list(self.completed_step_ids),
            "active_step_id": self.active_step_id,
            "failure": self.failure.thaw() if self.failure is not None else None,
            "diagnostics": [item.thaw() for item in self.diagnostics],
        }

    @classmethod
    def from_dict(cls, payload: Any) -> RunGeneration:
        """Rebuild a generation record, failing closed on any defect."""
        if not isinstance(payload, dict):
            raise CorruptStateError("run generation payload must be a mapping")
        if payload.get("schema") != RUN_GENERATION_SCHEMA:
            raise CorruptStateError(f"run generation schema must be {RUN_GENERATION_SCHEMA!r}")
        failure = payload.get("failure")
        if failure is not None and not isinstance(failure, dict):
            raise CorruptStateError("run generation failure must be a mapping or null")
        diagnostics = payload.get("diagnostics") or ()
        if not isinstance(diagnostics, (list, tuple)):
            raise CorruptStateError("run generation diagnostics must be a list")
        completed = payload.get("completed_step_ids") or ()
        if not isinstance(completed, (list, tuple)):
            raise CorruptStateError("completed_step_ids must be a list")
        return cls(
            run_id=payload.get("run_id"),
            generation_id=payload.get("generation_id"),
            status=payload.get("status"),
            definition_digest=payload.get("definition_digest"),
            started_wall=payload.get("started_wall"),
            updated_wall=payload.get("updated_wall"),
            manifest_generation_id=payload.get("manifest_generation_id"),
            completed_step_ids=tuple(str(item) for item in completed),
            active_step_id=payload.get("active_step_id"),
            failure=FrozenDict(failure) if failure is not None else None,
            diagnostics=tuple(FrozenDict(item) for item in diagnostics),
        )


def save_run_generation(run_root: str, record: RunGeneration) -> None:
    """Atomically publish *record* at ``<run_root>/run_generation.json``."""
    root = validate_run_root(run_root)
    if not isinstance(record, RunGeneration):
        raise CorruptStateError("record must be a RunGeneration")
    target = os.path.join(root, RUN_GENERATION_FILENAME)
    payload = canonical_json_bytes(record.to_dict())
    tmp_path = f"{target}.tmp.{os.getpid()}.{next(_TMP_COUNTER)}"
    try:
        with open(tmp_path, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_path, target)
    finally:
        try:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
        except OSError:
            pass
    try:
        dir_fd = os.open(root, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(dir_fd)
    except OSError:
        pass
    finally:
        os.close(dir_fd)


def load_run_generation(run_root: str) -> RunGeneration | None:
    """Load the durable generation record, or ``None`` when absent.

    Unreadable or invalid payloads raise :class:`CorruptStateError`; a
    consumer must never silently treat a corrupt current-generation record
    as "no record" and fall back to a stale manifest.
    """
    root = validate_run_root(run_root)
    path = os.path.join(root, RUN_GENERATION_FILENAME)
    if not os.path.isfile(path):
        return None
    try:
        with open(path, "rb") as handle:
            raw = handle.read()
    except OSError as exc:
        raise CorruptStateError(f"cannot read run generation record: {exc}") from exc
    import json

    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise CorruptStateError(f"run generation record is not valid JSON: {exc}") from exc
    return RunGeneration.from_dict(payload)


def running_record(
    *,
    run_id: str,
    generation_id: str,
    definition_digest: str | None = None,
) -> RunGeneration:
    """Build a fresh ``running`` generation record."""
    now = wall_now()
    return RunGeneration(
        run_id=run_id,
        generation_id=generation_id,
        status="running",
        definition_digest=definition_digest,
        started_wall=now,
        updated_wall=now,
    )


def terminal_record(
    record: RunGeneration,
    *,
    status: str,
    manifest_generation_id: str | None = None,
    completed_step_ids: tuple[str, ...] = (),
    active_step_id: str | None = None,
    failure: dict[str, Any] | None = None,
    diagnostics: tuple[dict[str, Any], ...] = (),
) -> RunGeneration:
    """Derive the terminal version of a running *record*."""
    if status == "running":
        raise CorruptStateError("terminal_record requires a terminal status")
    return RunGeneration(
        run_id=record.run_id,
        generation_id=record.generation_id,
        status=status,
        definition_digest=record.definition_digest,
        started_wall=record.started_wall,
        updated_wall=wall_now(),
        manifest_generation_id=manifest_generation_id,
        completed_step_ids=tuple(completed_step_ids),
        active_step_id=active_step_id,
        failure=FrozenDict(failure) if failure is not None else None,
        diagnostics=tuple(FrozenDict(item) for item in diagnostics),
    )
