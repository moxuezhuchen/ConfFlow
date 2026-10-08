#!/usr/bin/env python3

"""Durable terminal arbitration and generation ownership for formal V4 runs.

Single authority: ``os.replace`` integrity is not ordering; check-then-replace cannot agree.
``begin_generation`` installs owner; superseded writers can never overwrite current truth.
``terminal_publication`` claims terminal ownership under OS mutual exclusion; manifest
write + ``confirm`` happen inside same region.
Cancel before completion wins; completion first makes later cancel lose (project, never
rewrite); in-progress terminal claim refuses concurrent cancel promptly (fail closed).
``generation_publication_scope``/``compare_and_set_generation`` are the only owner-checked
generation write paths; check + write share one lock region. ``finalize_generation``
terminalizes without manifest (crash recovery/confirmed cancellation). Ordering frozen (CONTRACT 8): claim ->
manifest durable -> generation terminal durable -> projection. Every entry repairs
interrupted claims: same-generation manifest confirmed, claim without one revoked
(never linearized); kernel releases dead holder lock so crashes never wedge runs.
"""

from __future__ import annotations

import errno
import json
import os
import threading
import time
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass, field, replace
from typing import Any, cast

from ..domain._immutable import FrozenDict
from ..domain.canonical import canonical_json_bytes
from .contracts import CorruptStateError, validate_run_root, wall_now
from .fsatomic import publish_bytes
from .generation import (
    RunGeneration,
    load_run_generation,
    running_record,
    save_run_generation,
)

__all__ = [
    "GENERATION_LEDGER_FILENAME",
    "GENERATION_LEDGER_SCHEMA",
    "GENERATION_LOCK_FILENAME",
    "TERMINAL_STATUSES",
    "GenerationLedger",
    "PublicationScope",
    "StaleGenerationError",
    "TerminalClaim",
    "TerminalOwnershipLostError",
    "begin_generation",
    "compare_and_set_generation",
    "current_terminal_status",
    "finalize_current_generation",
    "finalize_generation",
    "generation_is_current",
    "generation_publication_scope",
    "load_ledger",
    "record_cancel_intent",
    "terminal_publication",
]

#: Internal arbitration ledger filename at the run root.
GENERATION_LEDGER_FILENAME = "generation_ownership.json"

#: Advisory mutual-exclusion lock filename at the run root.
GENERATION_LOCK_FILENAME = ".generation.lock"

#: Schema marker of the arbitration ledger payload.
GENERATION_LEDGER_SCHEMA = "confflow.generation_ownership.v1"

#: Terminal statuses a generation may reach.
TERMINAL_STATUSES: tuple[str, ...] = ("completed", "partial", "failed", "cancelled")

#: Claimant label per requested terminal status.
_CLAIMANTS: dict[str, str] = {
    "completed": "completion",
    "partial": "partial",
    "failed": "failure",
    "cancelled": "cancellation",
}

#: Bounded wait for the advisory lock (lock acquisition only, never race
#: mitigation).  A crashed holder is released by the kernel; a live holder
#: only ever holds the lock across one manifest/ledger publication.
_LOCK_TIMEOUT_SECONDS = 120.0
_LOCK_POLL_SECONDS = 0.005

try:  # POSIX advisory locking (the durable execution service is POSIX-only).
    import fcntl as _fcntl
except ImportError:  # pragma: no cover - non-POSIX fallback
    _fcntl = None  # type: ignore[assignment]

_FALLBACK_LOCKS: dict[str, threading.Lock] = {}
_FALLBACK_GUARD = threading.Lock()


class StaleGenerationError(CorruptStateError):
    """A writer tried to mutate a run root whose current generation is newer."""


class TerminalOwnershipLostError(CorruptStateError):
    """A writer tried to overwrite the terminal winner of its generation."""


@dataclass(frozen=True, slots=True)
class GenerationLedger:
    """Durable arbitration state of one run root's current generation."""

    run_id: str = ""
    current_generation_id: str | None = None
    definition_digest: str | None = None
    started_wall: float | None = None
    cancel_intent: FrozenDict | None = None
    terminal_status: str | None = None
    terminal_claimant: str | None = None
    terminal_confirmed: bool = False
    terminal_detail: FrozenDict = field(default_factory=FrozenDict)
    superseded_generation_ids: tuple[str, ...] = ()
    updated_wall: float = 0.0

    def __post_init__(self) -> None:
        for name in ("run_id",):
            value = getattr(self, name)
            if not isinstance(value, str):
                raise CorruptStateError(f"ledger {name} must be a string")
        for name in ("current_generation_id", "definition_digest", "terminal_claimant"):
            value = getattr(self, name)
            if value is not None and (not isinstance(value, str) or not value.strip()):
                raise CorruptStateError(f"ledger {name} must be a non-empty string or None")
        if self.started_wall is not None and not isinstance(self.started_wall, (int, float)):
            raise CorruptStateError("ledger started_wall must be a number or None")
        if self.terminal_status is not None and self.terminal_status not in TERMINAL_STATUSES:
            raise CorruptStateError(f"ledger terminal_status is invalid: {self.terminal_status!r}")
        if not isinstance(self.terminal_confirmed, bool):
            raise CorruptStateError("ledger terminal_confirmed must be a boolean")
        if self.cancel_intent is not None and not isinstance(self.cancel_intent, FrozenDict):
            object.__setattr__(self, "cancel_intent", FrozenDict(self.cancel_intent))
        if not isinstance(self.terminal_detail, FrozenDict):
            object.__setattr__(self, "terminal_detail", FrozenDict(self.terminal_detail))
        object.__setattr__(self, "superseded_generation_ids", tuple(self.superseded_generation_ids))

    def to_dict(self) -> dict[str, Any]:
        """Return the canonical JSON payload."""
        return {
            "schema": GENERATION_LEDGER_SCHEMA,
            "run_id": self.run_id,
            "current_generation_id": self.current_generation_id,
            "definition_digest": self.definition_digest,
            "started_wall": self.started_wall,
            "cancel_intent": self.cancel_intent.thaw() if self.cancel_intent is not None else None,
            "terminal_status": self.terminal_status,
            "terminal_claimant": self.terminal_claimant,
            "terminal_confirmed": self.terminal_confirmed,
            "terminal_detail": self.terminal_detail.thaw(),
            "superseded_generation_ids": list(self.superseded_generation_ids),
            "updated_wall": self.updated_wall,
        }

    @classmethod
    def from_dict(cls, payload: Any) -> GenerationLedger:
        """Rebuild a ledger, failing closed on any defect."""
        if not isinstance(payload, dict):
            raise CorruptStateError("generation ledger payload must be a mapping")
        if payload.get("schema") != GENERATION_LEDGER_SCHEMA:
            raise CorruptStateError(
                f"generation ledger schema must be {GENERATION_LEDGER_SCHEMA!r}"
            )
        cancel_intent = payload.get("cancel_intent")
        if cancel_intent is not None and not isinstance(cancel_intent, dict):
            raise CorruptStateError("generation ledger cancel_intent must be a mapping or null")
        terminal_detail = payload.get("terminal_detail") or {}
        if not isinstance(terminal_detail, dict):
            raise CorruptStateError("generation ledger terminal_detail must be a mapping")
        superseded = payload.get("superseded_generation_ids") or ()
        if not isinstance(superseded, (list, tuple)):
            raise CorruptStateError("generation ledger superseded ids must be a list")
        return cls(
            run_id=payload.get("run_id") or "",
            current_generation_id=payload.get("current_generation_id"),
            definition_digest=payload.get("definition_digest"),
            started_wall=payload.get("started_wall"),
            cancel_intent=FrozenDict(cancel_intent) if cancel_intent is not None else None,
            terminal_status=payload.get("terminal_status"),
            terminal_claimant=payload.get("terminal_claimant"),
            terminal_confirmed=bool(payload.get("terminal_confirmed", False)),
            terminal_detail=FrozenDict(terminal_detail),
            superseded_generation_ids=tuple(str(item) for item in superseded),
            updated_wall=float(payload.get("updated_wall") or 0.0),
        )


@dataclass(frozen=True, slots=True)
class TerminalClaim:
    """Outcome of one terminal-ownership claim."""

    generation_id: str
    status: str
    won: bool
    claimant: str


@dataclass(slots=True)
class PublicationScope:
    """Terminal publication scope: manifest write + generation confirmation."""

    claim: TerminalClaim
    confirmed: bool = False
    _confirm: Callable[..., None] | None = None

    def confirm(
        self,
        *,
        manifest_generation_id: str | None = None,
        completed_step_ids: tuple[str, ...] = (),
        active_step_id: str | None = None,
        failure: dict[str, Any] | None = None,
        diagnostics: tuple[dict[str, Any], ...] = (),
    ) -> None:
        """Confirm the winner and publish the terminal generation record."""
        if self.confirmed:
            return
        if self._confirm is None:  # pragma: no cover - construction invariant
            raise CorruptStateError("publication scope is not confirmable")
        self._confirm(
            manifest_generation_id=manifest_generation_id,
            completed_step_ids=completed_step_ids,
            active_step_id=active_step_id,
            failure=failure,
            diagnostics=diagnostics,
        )
        self.confirmed = True


# ---------------------------------------------------------------------------
# Locking
# ---------------------------------------------------------------------------


@contextmanager
def _generation_lock(run_root: str, *, nonblocking: bool = False) -> Iterator[bool]:
    """Hold the run root's generation arbitration lock for one operation."""
    root = validate_run_root(run_root)
    if _fcntl is not None:
        os.makedirs(root, exist_ok=True)
        path = os.path.join(root, GENERATION_LOCK_FILENAME)
        fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
        acquired = False
        try:
            if nonblocking:
                try:
                    _fcntl.flock(fd, _fcntl.LOCK_EX | _fcntl.LOCK_NB)
                    acquired = True
                except OSError as exc:
                    if exc.errno not in (errno.EACCES, errno.EAGAIN):
                        raise CorruptStateError(
                            f"cannot acquire generation arbitration lock at {path!r}: {exc}"
                        ) from exc
                yield acquired
                return
            deadline = time.monotonic() + _LOCK_TIMEOUT_SECONDS
            while True:
                try:
                    _fcntl.flock(fd, _fcntl.LOCK_EX | _fcntl.LOCK_NB)
                    acquired = True
                    break
                except OSError as exc:
                    if exc.errno not in (errno.EACCES, errno.EAGAIN):
                        raise CorruptStateError(
                            f"cannot acquire generation arbitration lock at {path!r}: {exc}"
                        ) from exc
                    if time.monotonic() >= deadline:
                        raise CorruptStateError(
                            f"generation arbitration lock timeout at {path!r}"
                        ) from exc
                    time.sleep(_LOCK_POLL_SECONDS)
            yield True
        finally:
            if acquired:
                _fcntl.flock(fd, _fcntl.LOCK_UN)
            os.close(fd)
        return
    # Non-POSIX fallback: process-local only.  The durable execution service
    # itself is POSIX-only, so production never relies on this branch.
    with _FALLBACK_GUARD:  # pragma: no cover - non-POSIX fallback
        lock = _FALLBACK_LOCKS.setdefault(root, threading.Lock())
    if nonblocking:  # pragma: no cover - non-POSIX fallback
        if not lock.acquire(blocking=False):
            yield False
            return
    elif not lock.acquire(timeout=_LOCK_TIMEOUT_SECONDS):  # pragma: no cover
        raise CorruptStateError(f"generation arbitration lock timeout at {root!r}")
    try:  # pragma: no cover - non-POSIX fallback
        yield True
    finally:
        lock.release()


# ---------------------------------------------------------------------------
# Ledger IO (callers must hold the lock for mutations)
# ---------------------------------------------------------------------------


def _ledger_path(root: str) -> str:
    return os.path.join(root, GENERATION_LEDGER_FILENAME)


def _load_ledger_file(root: str) -> GenerationLedger | None:
    path = _ledger_path(root)
    if not os.path.isfile(path):
        return None
    try:
        with open(path, "rb") as handle:
            raw = handle.read()
    except OSError as exc:
        raise CorruptStateError(f"cannot read generation ledger: {exc}") from exc
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise CorruptStateError(f"generation ledger is not valid JSON: {exc}") from exc
    return GenerationLedger.from_dict(payload)


def _save_ledger_locked(root: str, ledger: GenerationLedger) -> None:
    target = _ledger_path(root)
    publish_bytes(target, canonical_json_bytes(ledger.to_dict()))


def _init_ledger_from_public(root: str) -> GenerationLedger:
    public = load_run_generation(root)
    if public is None:
        return GenerationLedger()
    detail = FrozenDict(
        {
            "manifest_generation_id": public.manifest_generation_id,
            "completed_step_ids": list(public.completed_step_ids),
            "active_step_id": public.active_step_id,
            "failure": public.failure.thaw() if public.failure is not None else None,
            "diagnostics": [item.thaw() for item in public.diagnostics],
        }
    )
    return GenerationLedger(
        run_id=public.run_id,
        current_generation_id=public.generation_id,
        definition_digest=public.definition_digest,
        started_wall=public.started_wall,
        terminal_status=public.status if public.is_terminal else None,
        terminal_claimant="legacy" if public.is_terminal else None,
        terminal_confirmed=public.is_terminal,
        terminal_detail=detail,
        updated_wall=wall_now(),
    )


def _load_or_init_locked(root: str) -> GenerationLedger:
    ledger = _load_ledger_file(root)
    if ledger is not None:
        return ledger
    ledger = _init_ledger_from_public(root)
    if ledger.current_generation_id is not None:
        _save_ledger_locked(root, ledger)
    return ledger


def _manifest_payload(root: str, generation_id: str) -> dict[str, Any] | None:
    path = os.path.join(root, "run_result.json")
    try:
        with open(path, "rb") as handle:
            raw = handle.read()
    except OSError:
        return None
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return None
    if not isinstance(payload, dict) or payload.get("generation_id") != generation_id:
        return None
    if payload.get("status") not in TERMINAL_STATUSES:
        return None
    return payload


def _manifest_status(root: str, generation_id: str) -> str | None:
    payload = _manifest_payload(root, generation_id)
    return None if payload is None else str(payload["status"])


def _manifest_detail(root: str, generation_id: str, status: str) -> FrozenDict:
    payload = _manifest_payload(root, generation_id) or {}
    steps = payload.get("steps")
    completed = (
        tuple(
            str(step.get("id"))
            for step in steps
            if isinstance(step, dict) and step.get("status") == "completed"
        )
        if isinstance(steps, list)
        else ()
    )
    return FrozenDict(
        {
            "manifest_generation_id": generation_id,
            "completed_step_ids": list(completed),
            "active_step_id": None,
            "failure": None,
            "diagnostics": [],
            "recovered_from_manifest": True,
        }
    )


def _write_public_generation_locked(root: str, record: RunGeneration) -> None:
    """Write the public generation record while the lock is held."""
    save_run_generation(root, record)


def _terminal_record_from_ledger(root: str, ledger: GenerationLedger) -> RunGeneration:
    detail = ledger.terminal_detail
    failure = detail.get("failure")
    diagnostics = detail.get("diagnostics") or ()
    return RunGeneration(
        run_id=ledger.run_id or os.path.basename(root.rstrip(os.sep)) or "run",
        generation_id=ledger.current_generation_id,
        status=ledger.terminal_status,
        definition_digest=ledger.definition_digest,
        started_wall=ledger.started_wall,
        updated_wall=wall_now(),
        manifest_generation_id=detail.get("manifest_generation_id"),
        completed_step_ids=tuple(str(item) for item in (detail.get("completed_step_ids") or ())),
        active_step_id=detail.get("active_step_id"),
        failure=FrozenDict(dict(failure)) if isinstance(failure, Mapping) else None,
        diagnostics=tuple(
            FrozenDict(dict(item)) for item in diagnostics if isinstance(item, Mapping)
        ),
    )


def _write_terminal_from_ledger_locked(root: str, ledger: GenerationLedger) -> None:
    if ledger.terminal_status is None or ledger.current_generation_id is None:
        return
    _write_public_generation_locked(root, _terminal_record_from_ledger(root, ledger))


def _repair_locked(root: str, ledger: GenerationLedger) -> GenerationLedger:
    """Repair an interrupted publication after acquiring the lock."""
    current = ledger.current_generation_id
    if current is None:
        return ledger
    if ledger.terminal_status is not None and not ledger.terminal_confirmed:
        manifest_status = _manifest_status(root, current)
        if manifest_status is not None:
            ledger = replace(
                ledger,
                terminal_status=manifest_status,
                terminal_claimant=ledger.terminal_claimant
                or _CLAIMANTS.get(manifest_status, "recovered"),
                terminal_confirmed=True,
                terminal_detail=_manifest_detail(root, current, manifest_status),
                updated_wall=wall_now(),
            )
            _save_ledger_locked(root, ledger)
            _write_terminal_from_ledger_locked(root, ledger)
        else:
            ledger = replace(
                ledger,
                terminal_status=None,
                terminal_claimant=None,
                terminal_confirmed=False,
                terminal_detail=FrozenDict(),
                updated_wall=wall_now(),
            )
            _save_ledger_locked(root, ledger)
        return ledger
    if ledger.terminal_status is not None and ledger.terminal_confirmed:
        public = load_run_generation(root)
        if (
            public is None
            or public.generation_id != current
            or public.status != ledger.terminal_status
        ):
            _write_terminal_from_ledger_locked(root, ledger)
    return ledger


# ---------------------------------------------------------------------------
# Public arbitration API
# ---------------------------------------------------------------------------


def begin_generation(
    run_root: str,
    *,
    generation_id: str,
    run_id: str,
    definition_digest: str,
) -> GenerationLedger:
    """Install *generation_id* as the current owner of *run_root*."""
    for name, value in (
        ("generation_id", generation_id),
        ("run_id", run_id),
        ("definition_digest", definition_digest),
    ):
        if not isinstance(value, str) or not value.strip():
            raise CorruptStateError(f"begin_generation {name} must be a non-empty string")
    root = validate_run_root(run_root)
    with _generation_lock(root):
        ledger = _load_or_init_locked(root)
        ledger = _repair_locked(root, ledger)
        previous = ledger.current_generation_id
        superseded = ledger.superseded_generation_ids
        if previous is not None and previous != generation_id:
            superseded = (*superseded, previous)
        now = wall_now()
        ledger = GenerationLedger(
            run_id=run_id,
            current_generation_id=generation_id,
            definition_digest=definition_digest,
            started_wall=now,
            cancel_intent=None,
            terminal_status=None,
            terminal_claimant=None,
            terminal_confirmed=False,
            terminal_detail=FrozenDict(),
            superseded_generation_ids=superseded,
            updated_wall=now,
        )
        _save_ledger_locked(root, ledger)
        _write_public_generation_locked(
            root,
            running_record(
                run_id=run_id,
                generation_id=generation_id,
                definition_digest=definition_digest,
            ),
        )
        return ledger


def record_cancel_intent(
    run_root: str,
    *,
    source: str,
    token: str | None = None,
) -> str | None:
    """Record a durable cancellation claim for the current generation.

    Returns the terminal winner when the generation already terminated
    (``None`` means the cancel claim was recorded and cancellation wins the
    ordering unless a completion claim was recorded first).

    While a terminal publication holds the lock across its manifest write, a
    non-blocking probe finds the lock busy and the terminal status already
    visible in the ledger is returned immediately: the cancel is refused
    promptly (fail closed) instead of being reported accepted.  A stale
    unconfirmed claim with no live publication is repaired under the lock
    first, so it is revoked and the cancellation can still win.
    """
    if not isinstance(source, str) or not source.strip():
        raise CorruptStateError("cancel intent source must be a non-empty string")
    root = validate_run_root(run_root)
    with _generation_lock(root, nonblocking=True) as held:
        if held:
            return _cancel_intent_locked(root, source, token)
        visible = _visible_terminal_status(root)
        if visible is not None:
            return visible
        # The lock holder is a short non-terminal operation (begin/cancel/
        # finalize); wait for it and re-decide under the lock below.
    with _generation_lock(root):
        return _cancel_intent_locked(root, source, token)


def _cancel_intent_locked(root: str, source: str, token: str | None) -> str | None:
    ledger = _load_or_init_locked(root)
    ledger = _repair_locked(root, ledger)
    if ledger.current_generation_id is None:
        return None
    if ledger.terminal_status is not None:
        return ledger.terminal_status
    if ledger.cancel_intent is None:
        ledger = replace(
            ledger,
            cancel_intent=FrozenDict(
                {
                    "source": source,
                    "token": token,
                    "requested_wall": wall_now(),
                }
            ),
            updated_wall=wall_now(),
        )
        _save_ledger_locked(root, ledger)
    return None


def _visible_terminal_status(root: str) -> str | None:
    """Return a terminal status visible without acquiring the lock."""
    ledger = _load_ledger_file(root)
    if ledger is None or ledger.current_generation_id is None:
        return None
    return ledger.terminal_status


def current_terminal_status(run_root: str) -> str | None:
    """Return the current generation's terminal winner, or ``None``."""
    root = validate_run_root(run_root)
    with _generation_lock(root):
        ledger = _load_or_init_locked(root)
        ledger = _repair_locked(root, ledger)
        return ledger.terminal_status


def generation_is_current(run_root: str, generation_id: str) -> bool:
    """Return whether *generation_id* still owns publication authority."""
    if not isinstance(generation_id, str) or not generation_id.strip():
        raise CorruptStateError("generation_id must be a non-empty string")
    root = validate_run_root(run_root)
    with _generation_lock(root):
        ledger = _load_or_init_locked(root)
        ledger = _repair_locked(root, ledger)
        return ledger.current_generation_id == generation_id


def load_ledger(run_root: str) -> GenerationLedger | None:
    """Read the arbitration ledger without acquiring the lock (diagnostics)."""
    root = validate_run_root(run_root)
    return _load_ledger_file(root)


def compare_and_set_generation(
    run_root: str,
    *,
    expected_generation_id: str,
    record: RunGeneration,
) -> None:
    """Publish *record* only while *expected_generation_id* still owns the root."""
    if not isinstance(record, RunGeneration):
        raise CorruptStateError("compare_and_set_generation requires a RunGeneration")
    if record.generation_id != expected_generation_id:
        raise CorruptStateError(
            "compare_and_set_generation record generation does not match its expected owner"
        )
    root = validate_run_root(run_root)
    with _generation_lock(root):
        ledger = _load_or_init_locked(root)
        ledger = _repair_locked(root, ledger)
        if ledger.current_generation_id != expected_generation_id:
            raise StaleGenerationError(
                f"generation {expected_generation_id!r} is not the current owner of "
                f"{root!r} (current: {ledger.current_generation_id!r})"
            )
        if ledger.terminal_status is not None and record.status != ledger.terminal_status:
            raise TerminalOwnershipLostError(
                f"terminal ownership of {expected_generation_id!r} belongs to "
                f"{ledger.terminal_status!r}, not {record.status!r}"
            )
        if record.status == "running" and ledger.terminal_status is not None:
            raise TerminalOwnershipLostError(
                f"generation {expected_generation_id!r} is already terminal "
                f"({ledger.terminal_status!r})"
            )
        _write_public_generation_locked(root, record)


@contextmanager
def generation_publication_scope(
    run_root: str,
    *,
    expected_generation_id: str,
    action: str = "publication",
) -> Iterator[None]:
    """Hold the generation lock while one generation-owned artifact is written."""
    if not isinstance(expected_generation_id, str) or not expected_generation_id.strip():
        raise CorruptStateError("generation_publication_scope requires a generation id")
    if not isinstance(action, str) or not action.strip():
        raise CorruptStateError("generation_publication_scope requires an action label")
    root = validate_run_root(run_root)
    with _generation_lock(root):
        ledger = _load_or_init_locked(root)
        ledger = _repair_locked(root, ledger)
        if ledger.current_generation_id != expected_generation_id:
            raise StaleGenerationError(
                f"generation {expected_generation_id!r} lost publication authority over "
                f"{root!r} (current: {ledger.current_generation_id!r}); "
                f"stale {action} refused"
            )
        yield


@contextmanager
def terminal_publication(
    run_root: str,
    *,
    generation_id: str,
    requested_status: str,
    cancel_probe: Callable[[], bool] | None = None,
) -> Iterator[PublicationScope]:
    """Claim terminal ownership and hold the publication region."""
    if requested_status not in TERMINAL_STATUSES:
        raise CorruptStateError(f"unknown requested terminal status {requested_status!r}")
    root = validate_run_root(run_root)
    with _generation_lock(root):
        ledger = _load_or_init_locked(root)
        ledger = _repair_locked(root, ledger)
        if ledger.current_generation_id != generation_id:
            raise StaleGenerationError(
                f"generation {generation_id!r} lost publication authority over {root!r} "
                f"(current: {ledger.current_generation_id!r}); stale publication refused"
            )
        if ledger.terminal_status is not None:
            claim = TerminalClaim(
                generation_id=generation_id,
                status=ledger.terminal_status,
                won=False,
                claimant=ledger.terminal_claimant or "idempotent",
            )
        else:
            cancel_signal = ledger.cancel_intent is not None
            if not cancel_signal and cancel_probe is not None and requested_status != "cancelled":
                cancel_signal = bool(cancel_probe())
            if requested_status == "cancelled" or cancel_signal:
                winner = "cancelled"
                claimant = "cancellation"
                won = requested_status == "cancelled"
            else:
                winner = requested_status
                claimant = _CLAIMANTS[requested_status]
                won = True
            ledger = replace(
                ledger,
                terminal_status=winner,
                terminal_claimant=claimant,
                terminal_confirmed=False,
                terminal_detail=FrozenDict(),
                updated_wall=wall_now(),
            )
            _save_ledger_locked(root, ledger)
            claim = TerminalClaim(
                generation_id=generation_id,
                status=winner,
                won=won,
                claimant=claimant,
            )
        scope = PublicationScope(
            claim=claim,
            _confirm=_make_confirm(root, generation_id, claim),
        )
        try:
            yield scope
        except BaseException:
            _abandon_locked(root, generation_id, claim)
            raise
        else:
            if not scope.confirmed and claim.won:
                _abandon_locked(root, generation_id, claim)


def finalize_generation(
    run_root: str,
    *,
    generation_id: str,
    requested_status: str,
    cancel_probe: Callable[[], bool] | None = None,
    failure: dict[str, Any] | None = None,
    active_step_id: str | None = None,
    diagnostics: tuple[dict[str, Any], ...] = (),
) -> str:
    """Terminalize a generation without publishing a manifest."""
    if requested_status not in TERMINAL_STATUSES:
        raise CorruptStateError(f"unknown requested terminal status {requested_status!r}")
    root = validate_run_root(run_root)
    with _generation_lock(root):
        ledger = _load_or_init_locked(root)
        ledger = _repair_locked(root, ledger)
        if ledger.current_generation_id != generation_id:
            raise StaleGenerationError(
                f"generation {generation_id!r} lost publication authority over {root!r} "
                f"(current: {ledger.current_generation_id!r})"
            )
        return _finalize_locked(
            root,
            ledger,
            requested_status=requested_status,
            cancel_probe=cancel_probe,
            failure=failure,
            active_step_id=active_step_id,
            diagnostics=diagnostics,
        )


def finalize_current_generation(
    run_root: str,
    *,
    requested_status: str,
    cancel_probe: Callable[[], bool] | None = None,
    failure: dict[str, Any] | None = None,
    active_step_id: str | None = None,
    diagnostics: tuple[dict[str, Any], ...] = (),
) -> str:
    """Terminalize whichever generation currently owns *run_root*."""
    if requested_status not in TERMINAL_STATUSES:
        raise CorruptStateError(f"unknown requested terminal status {requested_status!r}")
    root = validate_run_root(run_root)
    with _generation_lock(root):
        ledger = _load_or_init_locked(root)
        ledger = _repair_locked(root, ledger)
        if ledger.current_generation_id is None:
            return "cancelled"
        return _finalize_locked(
            root,
            ledger,
            requested_status=requested_status,
            cancel_probe=cancel_probe,
            failure=failure,
            active_step_id=active_step_id,
            diagnostics=diagnostics,
        )


def _finalize_locked(
    root: str,
    ledger: GenerationLedger,
    *,
    requested_status: str,
    cancel_probe: Callable[[], bool] | None,
    failure: dict[str, Any] | None,
    active_step_id: str | None,
    diagnostics: tuple[dict[str, Any], ...],
) -> str:
    if ledger.terminal_status is not None and ledger.terminal_confirmed:
        return ledger.terminal_status
    if ledger.terminal_status is None:
        cancel_signal = ledger.cancel_intent is not None
        if not cancel_signal and cancel_probe is not None and requested_status != "cancelled":
            cancel_signal = bool(cancel_probe())
        if requested_status == "cancelled" or cancel_signal:
            winner = "cancelled"
            claimant = "cancellation"
        else:
            winner = requested_status
            claimant = _CLAIMANTS[requested_status]
        ledger = replace(
            ledger,
            terminal_status=winner,
            terminal_claimant=claimant,
            terminal_confirmed=False,
            terminal_detail=FrozenDict(),
            updated_wall=wall_now(),
        )
    winner = cast(str, ledger.terminal_status)
    assert winner is not None
    detail: dict[str, Any] = {
        "manifest_generation_id": None,
        "completed_step_ids": [],
        "active_step_id": active_step_id,
        "failure": (
            failure
            if failure is not None
            else {
                "type": "Cancelled" if winner == "cancelled" else "Failed",
                "message": (
                    "durable cancellation confirmed; no matching result manifest was published"
                    if winner == "cancelled"
                    else "generation terminated without a result manifest"
                ),
                "step_id": active_step_id,
                "blocked_downstream": False,
            }
        ),
        "diagnostics": [dict(item) for item in diagnostics],
    }
    ledger = replace(
        ledger,
        terminal_confirmed=True,
        terminal_detail=FrozenDict(detail),
        updated_wall=wall_now(),
    )
    _save_ledger_locked(root, ledger)
    _write_terminal_from_ledger_locked(root, ledger)
    return winner


def _make_confirm(
    root: str,
    generation_id: str,
    claim: TerminalClaim,
) -> Callable[..., None]:
    def _confirm(
        *,
        manifest_generation_id: str | None,
        completed_step_ids: tuple[str, ...],
        active_step_id: str | None,
        failure: dict[str, Any] | None,
        diagnostics: tuple[dict[str, Any], ...],
    ) -> None:
        ledger = _load_ledger_file(root)
        if ledger is None:
            raise CorruptStateError("generation ledger disappeared during publication")
        if ledger.current_generation_id != generation_id:
            raise StaleGenerationError(
                f"generation {generation_id!r} lost publication authority during confirmation"
            )
        if ledger.terminal_status != claim.status:
            raise TerminalOwnershipLostError(
                f"terminal ownership of {generation_id!r} belongs to "
                f"{ledger.terminal_status!r}, not {claim.status!r}"
            )
        detail = FrozenDict(
            {
                "manifest_generation_id": manifest_generation_id,
                "completed_step_ids": list(completed_step_ids),
                "active_step_id": active_step_id,
                "failure": failure,
                "diagnostics": [dict(item) for item in diagnostics],
            }
        )
        ledger = replace(
            ledger,
            terminal_confirmed=True,
            terminal_detail=detail,
            updated_wall=wall_now(),
        )
        _write_terminal_from_ledger_locked(root, ledger)
        _save_ledger_locked(root, ledger)

    return _confirm


def _abandon_locked(root: str, generation_id: str, claim: TerminalClaim) -> None:
    """Confirm-from-manifest or revoke an unconfirmed claim after a failure."""
    ledger = _load_ledger_file(root)
    if ledger is None:
        return
    if ledger.current_generation_id != generation_id:
        return
    if ledger.terminal_status is None or ledger.terminal_confirmed:
        return
    if claim.status != ledger.terminal_status:
        return
    manifest_status = _manifest_status(root, generation_id)
    if manifest_status is not None:
        ledger = replace(
            ledger,
            terminal_status=manifest_status,
            terminal_claimant=ledger.terminal_claimant
            or _CLAIMANTS.get(manifest_status, "recovered"),
            terminal_confirmed=True,
            terminal_detail=_manifest_detail(root, generation_id, manifest_status),
            updated_wall=wall_now(),
        )
        _save_ledger_locked(root, ledger)
        _write_terminal_from_ledger_locked(root, ledger)
        return
    ledger = replace(
        ledger,
        terminal_status=None,
        terminal_claimant=None,
        terminal_confirmed=False,
        terminal_detail=FrozenDict(),
        updated_wall=wall_now(),
    )
    _save_ledger_locked(root, ledger)
