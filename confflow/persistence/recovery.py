#!/usr/bin/env python3

"""Abandoned-``RUNNING`` owner reconciliation for V4 durable execution (V4-3).

When a controller claims a work item it records an
:class:`~confflow.persistence.contracts.OwnerIdentity` (owner token, pid,
process-group id, session id, creation time, wall stamp).  If that controller
vanishes, a later controller must decide whether the recorded owner boundary
is still alive before it may recover the abandoned ``RUNNING`` claim: a wrong
"dead" verdict launches a duplicate native boundary, while a wrong "alive"
verdict stalls recovery forever.

This module judges liveness of the recorded triple only, using read-only
inspection (``os.getpgid``/``os.getsid`` plus the optional ``psutil``
dependency).  It never signals, terminates, or reaps any process, performs no
SQLite or filesystem access, and never imports the executor
(``confflow.execution``) or any legacy runtime: the small helpers it needs
are vendored here.

Verdict rules (all fail closed — ``UNCERTAIN`` blocks duplicate launch):

1. ``pid`` is ``None`` → ``UNCERTAIN``.  Without a pid there is no identity
   token to prove anything about, so recovery must block.
2. No such pid **and** no process-group/session survivors in the recorded
   ``pgid``/``sid`` → ``DEFINITELY_DEAD``.  An empty pid slot proves the
   original process is gone (a dead process can never come back under the
   same pid without reuse, which rule 4 handles); the group scan additionally
   proves no orphaned descendant still holds the claim boundary.
3. A live process matches ``pid`` **and** its creation time matches the
   recorded ``create_time`` (when recorded) **and** it sits in the recorded
   ``pgid``/``sid`` (when recorded) → ``DEFINITELY_ALIVE``.  Every recorded
   axis agrees, so the original owner boundary is observably alive.  Any
   recorded axis that disagrees (or cannot be read) drops to ``UNCERTAIN``
   instead of guessing alive.
4. The pid slot holds a live process whose creation time mismatches the
   recorded one (pid reuse!) → the original owner is dead **only if** the
   recorded ``pgid``/``sid`` also show no live member, else ``UNCERTAIN``.
   The pid number was recycled by an unrelated process, so the slot tells
   nothing about the original; a surviving same-group member may still be a
   descendant holding the claim, and a reused group id is possible too, so
   any survivor forces ``UNCERTAIN``.
5. ``psutil`` missing or unusable → ``UNCERTAIN``.  Without creation times
   pid reuse cannot be detected and ``DEFINITELY_DEAD`` can never be proven,
   so every verdict degrades to ``UNCERTAIN`` rather than crashing.
6. ``owner_token`` mismatch is the caller's concern.  The token binds a claim
   to one controller; this function judges liveness of the recorded process
   triple only and never compares tokens.

Group-scan semantics: ``pgid``/``sid`` are matched with OR (a survivor in
either recorded group counts), zombies never count as survivors, the
reconciling process itself is never counted, and a candidate whose liveness
cannot be determined counts as a survivor (fail closed toward blocking).
When neither ``pgid`` nor ``sid`` was recorded there is no group to scan, so
a missing pid alone proves ``DEFINITELY_DEAD``; callers should therefore
always record the full triple on POSIX (see :func:`owner_identity_current`).
"""

from __future__ import annotations

import os
from typing import Any, Final, NamedTuple

from .contracts import OwnerIdentity, OwnerVerdict, wall_now

__all__ = [
    "owner_identity_current",
    "reconcile_owner",
]

#: Absolute tolerance, in seconds, for pid/create_time identity comparison.
#: Creation times are deterministic for one process (same boot time plus same
#: start-tick on every read, exact through JSON/SQLite round-trips), while
#: distinct processes differ by whole scheduler ticks, so a tight tolerance
#: both identifies the original and exposes pid reuse.
_CREATE_TIME_TOLERANCE: Final[float] = 1e-6

#: Sentinel selecting the process-wide optional ``psutil`` import.  Passing
#: an explicit module (or ``None`` to simulate its absence) is a test hook;
#: production callers leave the default so reconciliation uses the real
#: dependency when installed.
_AUTO_PSUTIL: Final[Any] = object()


def _maybe_import_psutil() -> Any:
    """Import ``psutil`` when available, otherwise return ``None``.

    Returns
    -------
    Any
        The imported ``psutil`` module, or ``None`` when it is not installed.
    """
    try:
        import psutil
    except ImportError:
        return None
    return psutil


#: Process-wide optional ``psutil`` import, resolved once at module load.
_PSUTIL: Final[Any] = _maybe_import_psutil()


def _resolve_psutil(psutil_module: Any) -> Any:
    """Resolve the effective ``psutil`` module for one call.

    Parameters
    ----------
    psutil_module : Any
        Either the :data:`_AUTO_PSUTIL` sentinel (use the process-wide
        import), an explicit module-like object (tests), or ``None`` to
        simulate an absent dependency.

    Returns
    -------
    Any
        The module to inspect processes with, or ``None`` when unavailable.
    """
    if psutil_module is _AUTO_PSUTIL:
        return _PSUTIL
    return psutil_module


def _gone_error_types(psutil_mod: Any) -> tuple[type[BaseException], ...]:
    """Return exception types proving a process slot is gone.

    Parameters
    ----------
    psutil_mod : Any
        The ``psutil`` module (or test double) in use for this call.

    Returns
    -------
    tuple[type[BaseException], ...]
        ``psutil`` disappearance markers plus builtin ``ProcessLookupError``
        (``psutil.NoSuchProcess`` does not subclass ``OSError``, so both
        families are needed), deduplicated.
    """
    collected: list[type[BaseException]] = [ProcessLookupError]
    for name in ("NoSuchProcess", "ZombieProcess"):
        marker = getattr(psutil_mod, name, None)
        if isinstance(marker, type) and issubclass(marker, BaseException):
            if marker not in collected:
                collected.append(marker)
    return tuple(collected)


def _inspection_error_types(psutil_mod: Any) -> tuple[type[BaseException], ...]:
    """Return exception types meaning liveness could not be determined.

    Parameters
    ----------
    psutil_mod : Any
        The ``psutil`` module (or test double) in use for this call.

    Returns
    -------
    tuple[type[BaseException], ...]
        ``psutil.Error`` (covers ``AccessDenied``, which is not an
        ``OSError``) plus ``OSError``, ``RuntimeError``, and
        ``AttributeError``.  Any of these collapses the verdict to
        ``UNCERTAIN``; none of them ever proves death.
    """
    collected: list[type[BaseException]] = [OSError, RuntimeError, AttributeError]
    marker = getattr(psutil_mod, "Error", None)
    if isinstance(marker, type) and issubclass(marker, BaseException):
        if marker not in collected:
            collected.insert(0, marker)
    return tuple(collected)


def _positive_int(value: object) -> int | None:
    """Return *value* when it is a positive int, otherwise ``None``.

    Parameters
    ----------
    value : object
        Candidate pid-like value (bools are rejected explicitly).

    Returns
    -------
    int | None
        The value itself when it is a positive int, else ``None``.
    """
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        return None
    return value


def _safe_process_group_id(pid: int | None) -> int | None:
    """Return the process-group id for *pid*, or ``None`` when unavailable.

    Parameters
    ----------
    pid : int | None
        Process id to inspect (read-only; nothing is signalled).

    Returns
    -------
    int | None
        Positive group id, or ``None`` on non-POSIX platforms, unknown pids,
        or exits racing the lookup.
    """
    if pid is None or not hasattr(os, "getpgid"):
        return None
    try:
        return _positive_int(os.getpgid(pid))
    except OSError:
        return None


def _safe_session_id(pid: int | None) -> int | None:
    """Return the session id for *pid*, or ``None`` when unavailable.

    Parameters
    ----------
    pid : int | None
        Process id to inspect (read-only; nothing is signalled).

    Returns
    -------
    int | None
        Positive session id, or ``None`` on non-POSIX platforms, unknown
        pids, or exits racing the lookup.
    """
    if pid is None or not hasattr(os, "getsid"):
        return None
    try:
        return _positive_int(os.getsid(pid))
    except OSError:
        return None


def _safe_create_time(pid: int | None, psutil_mod: Any) -> float | None:
    """Return the ``psutil`` creation time for *pid*, or ``None``.

    Parameters
    ----------
    pid : int | None
        Process id to inspect (read-only; nothing is signalled).
    psutil_mod : Any
        The ``psutil`` module (or test double) in use, or ``None``.

    Returns
    -------
    float | None
        Creation time in seconds, or ``None`` when ``psutil`` is absent or
        the pid cannot be inspected.
    """
    if pid is None or psutil_mod is None:
        return None
    try:
        return float(psutil_mod.Process(pid).create_time())
    except _inspection_error_types(psutil_mod):
        return None
    except _gone_error_types(psutil_mod):
        return None


class _PidProbe(NamedTuple):
    """Outcome of probing one recorded pid slot (read-only)."""

    live: bool | None
    create_time: float | None


def _probe_pid(*, pid: int, psutil_mod: Any) -> _PidProbe:
    """Probe whether *pid* currently holds a live process, without signalling.

    A zombie still occupies its pid slot but counts as not live: the original
    process has terminated and only descendants (checked separately) may
    still hold the boundary.  Any inspection failure that proves neither
    life nor death yields ``live=None`` so the caller degrades to
    ``UNCERTAIN``.

    Parameters
    ----------
    pid : int
        Recorded owner pid to probe.
    psutil_mod : Any
        The ``psutil`` module (or test double) in use for this call.

    Returns
    -------
    _PidProbe
        Liveness flag (``None`` when unknowable) plus the slot occupant's
        creation time (meaningful only when live).
    """
    gone = _gone_error_types(psutil_mod)
    errors = _inspection_error_types(psutil_mod)
    try:
        process = psutil_mod.Process(pid)
    except gone:
        return _PidProbe(live=False, create_time=None)
    except errors:
        return _PidProbe(live=None, create_time=None)
    try:
        running = bool(process.is_running())
    except gone:
        return _PidProbe(live=False, create_time=None)
    except errors:
        return _PidProbe(live=None, create_time=None)
    if not running:
        return _PidProbe(live=False, create_time=None)
    zombie_marker = getattr(psutil_mod, "STATUS_ZOMBIE", "zombie")
    try:
        is_zombie = process.status() == zombie_marker
    except gone:
        return _PidProbe(live=False, create_time=None)
    except errors:
        return _PidProbe(live=None, create_time=None)
    if is_zombie:
        return _PidProbe(live=False, create_time=None)
    try:
        create_time = float(process.create_time())
    except gone:
        # The occupant exited between the liveness check and the identity
        # read; the slot no longer provably holds a live process.
        return _PidProbe(live=False, create_time=None)
    except errors:
        return _PidProbe(live=None, create_time=None)
    return _PidProbe(live=True, create_time=create_time)


def _recorded_group_holds(*, pid: int, owner: OwnerIdentity) -> bool:
    """Check the live pid slot still sits in every recorded group.

    Parameters
    ----------
    pid : int
        Currently live pid whose membership is checked (read-only).
    owner : OwnerIdentity
        Recorded owner triple carrying the expected group ids.

    Returns
    -------
    bool
        True only when each recorded (non-``None``) group id matches the
        current lookup.  A moved group, an unreadable group, or an exit
        racing the lookup all return False so the caller refuses to guess
        alive.
    """
    if owner.process_group_id is not None:
        if _safe_process_group_id(pid) != owner.process_group_id:
            return False
    if owner.session_id is not None:
        if _safe_session_id(pid) != owner.session_id:
            return False
    return True


def _candidate_is_live(*, candidate: Any, psutil_mod: Any) -> bool | None:
    """Judge one group-matching scan candidate without signalling it.

    Parameters
    ----------
    candidate : Any
        One ``psutil`` process object from the group scan.
    psutil_mod : Any
        The ``psutil`` module (or test double) in use for this call.

    Returns
    -------
    bool | None
        True for an observably live member, False for a zombie, an exited
        pid, or a process that is no longer running, and ``None`` when
        liveness cannot be determined (the caller then counts it as a
        survivor, failing closed toward blocking recovery).
    """
    gone = _gone_error_types(psutil_mod)
    errors = _inspection_error_types(psutil_mod)
    status: Any = None
    info = getattr(candidate, "info", None)
    if isinstance(info, dict):
        status = info.get("status")
    if status is None:
        try:
            status = candidate.status()
        except gone:
            return False
        except errors:
            return None
    if status == getattr(psutil_mod, "STATUS_ZOMBIE", "zombie"):
        return False
    try:
        if not bool(candidate.is_running()):
            return False
    except gone:
        return False
    except errors:
        return None
    return True


def _boundary_has_survivor(*, owner: OwnerIdentity, psutil_mod: Any) -> bool | None:
    """Scan the recorded groups for any live member, without signalling.

    Parameters
    ----------
    owner : OwnerIdentity
        Recorded owner triple carrying the group ids to scan.
    psutil_mod : Any
        The ``psutil`` module (or test double) in use for this call.

    Returns
    -------
    bool | None
        True when a live same-group member exists (an orphaned descendant
        still holding the claim boundary), False when the recorded groups
        provably hold no live member (or no group was ever recorded), and
        ``None`` when the scan itself failed so survival is unproven.
    """
    pgid = owner.process_group_id
    sid = owner.session_id
    if pgid is None and sid is None:
        return False
    try:
        candidates = psutil_mod.process_iter(["pid", "status"])
    except Exception:
        return None
    self_pid = os.getpid()
    try:
        for candidate in candidates:
            member_pid = _positive_int(getattr(candidate, "pid", None))
            if member_pid is None or member_pid == self_pid:
                continue
            in_recorded_group = False
            if pgid is not None and _safe_process_group_id(member_pid) == pgid:
                in_recorded_group = True
            elif sid is not None and _safe_session_id(member_pid) == sid:
                in_recorded_group = True
            if not in_recorded_group:
                continue
            # A live member proves the boundary outlived its root; an
            # unreadable member is counted the same way so a scan hiccup
            # can never manufacture DEFINITELY_DEAD.
            if _candidate_is_live(candidate=candidate, psutil_mod=psutil_mod) is not False:
                return True
    except Exception:
        return None
    return False


def reconcile_owner(owner: OwnerIdentity, *, psutil_module: Any = _AUTO_PSUTIL) -> OwnerVerdict:
    """Judge liveness of the recorded owner triple, failing closed.

    The function only inspects (``psutil`` plus ``os.getpgid``/``os.getsid``)
    and never signals, terminates, or reaps anything.  ``owner_token`` is
    deliberately ignored: token mismatch is the caller's concern; this
    function judges liveness of the recorded process triple only.  A
    well-formed :class:`OwnerIdentity` never raises; every inspection
    failure collapses to ``UNCERTAIN``.

    Rules applied, in order (see the module docstring for rationale):

    1. ``pid`` is ``None`` → ``UNCERTAIN``.
    2. Pid slot empty and no group survivors → ``DEFINITELY_DEAD``; a group
       survivor instead means an orphaned descendant holds the boundary →
       ``DEFINITELY_ALIVE``.
    3. Pid slot live with matching creation time (when recorded) and
       matching recorded groups → ``DEFINITELY_ALIVE``; any mismatch or
       unreadable axis → ``UNCERTAIN``.
    4. Pid slot live with mismatching creation time (pid reuse) → original
       dead (``DEFINITELY_DEAD``) only with no group survivors, else
       ``UNCERTAIN``.
    5. ``psutil`` absent → ``UNCERTAIN`` (death is never declared unproven).

    Parameters
    ----------
    owner : OwnerIdentity
        Recorded owner triple captured at claim time.
    psutil_module : Any
        Test hook: leave the default to use the process-wide optional
        ``psutil`` import, or pass an explicit module-like object (or
        ``None`` to simulate its absence) without monkeypatching.

    Returns
    -------
    OwnerVerdict
        ``DEFINITELY_ALIVE``, ``DEFINITELY_DEAD``, or ``UNCERTAIN``.
    """
    if owner.pid is None:
        # Rule 1: no pid token, no proof possible.
        return OwnerVerdict.UNCERTAIN
    psutil_mod = _resolve_psutil(psutil_module)
    if psutil_mod is None:
        # Rule 5: without creation times pid reuse is undetectable.
        return OwnerVerdict.UNCERTAIN
    probe = _probe_pid(pid=owner.pid, psutil_mod=psutil_mod)
    if probe.live is None:
        return OwnerVerdict.UNCERTAIN
    if probe.live:
        if owner.create_time is not None:
            if probe.create_time is None:
                return OwnerVerdict.UNCERTAIN
            if abs(probe.create_time - owner.create_time) >= _CREATE_TIME_TOLERANCE:
                # Rule 4: the pid number was recycled; the slot occupant is
                # unrelated, so only the recorded groups can still speak for
                # the original boundary.
                survivors = _boundary_has_survivor(owner=owner, psutil_mod=psutil_mod)
                if survivors is None or survivors:
                    return OwnerVerdict.UNCERTAIN
                return OwnerVerdict.DEFINITELY_DEAD
        # The slot holds the original process (creation token matches, or
        # none was ever recorded); it counts as alive only while it still
        # sits in every recorded group.
        if not _recorded_group_holds(pid=owner.pid, owner=owner):
            return OwnerVerdict.UNCERTAIN
        return OwnerVerdict.DEFINITELY_ALIVE
    # Rule 2: the pid slot is empty (or holds only a zombie), so orphaned
    # descendants decide whether the boundary survives its root.
    survivors = _boundary_has_survivor(owner=owner, psutil_mod=psutil_mod)
    if survivors is None:
        return OwnerVerdict.UNCERTAIN
    if survivors:
        return OwnerVerdict.DEFINITELY_ALIVE
    return OwnerVerdict.DEFINITELY_DEAD


def owner_identity_current(*, owner_token: str, psutil_module: Any = _AUTO_PSUTIL) -> OwnerIdentity:
    """Capture this process's owner identity triple (read-only inspection).

    Records pid, process-group id, and session id via guarded ``os`` lookups
    plus the ``psutil`` creation time when available.  Nothing is signalled;
    ``claimed_wall`` carries provenance only and never participates in
    liveness decisions.

    Parameters
    ----------
    owner_token : str
        Claim token binding the identity to one controller (non-empty).
    psutil_module : Any
        Test hook: leave the default to use the process-wide optional
        ``psutil`` import, or pass an explicit module-like object (or
        ``None`` to simulate its absence) without monkeypatching.

    Returns
    -------
    OwnerIdentity
        Identity triple for this process, with ``create_time=None`` when
        ``psutil`` is unavailable.
    """
    pid = os.getpid()
    psutil_mod = _resolve_psutil(psutil_module)
    return OwnerIdentity(
        owner_token=owner_token,
        pid=_positive_int(pid),
        process_group_id=_safe_process_group_id(pid),
        session_id=_safe_session_id(pid),
        create_time=_safe_create_time(pid, psutil_mod),
        claimed_wall=wall_now(),
    )
