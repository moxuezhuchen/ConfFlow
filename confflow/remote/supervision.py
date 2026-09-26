#!/usr/bin/env python3

"""Remote attempt liveness and cancellation for V4 execution (V4-4).

Liveness of an abandoned ``RUNNING`` claim is judged in two layers that
share a single verdict vocabulary (:class:`OwnerVerdict` from
:mod:`confflow.persistence.contracts`; no second vocabulary is defined
here):

- the primary verdict comes from the V4-3 owner reconciliation
  (:func:`confflow.persistence.recovery.reconcile_owner`), which proves
  whether the recorded pid/process-group/session triple is still alive;
- when a *work_dir* is given, a working-directory scan adapted from the
  legacy work-process probe additionally checks whether any live process
  still holds that directory (process-group probe plus a cwd-prefix scan,
  the reconciling process itself excluded, failing closed toward "live"
  on any ambiguity).

The layers combine as follows:

==================  ================  ==========
reconcile verdict   workdir holds     combined
==================  ================  ==========
DEFINITELY_ALIVE    live or dead      DEFINITELY_ALIVE
UNCERTAIN           live or dead      UNCERTAIN
DEFINITELY_DEAD     dead              DEFINITELY_DEAD
DEFINITELY_DEAD     live              UNCERTAIN (a descendant may hold the dir)
==================  ================  ==========

Cancellation (:func:`cancel_attempt`) signals the owner process group with
SIGTERM-then-SIGKILL escalation and re-proves death afterwards, but
signalling a possibly-foreign process group is dangerous, so the signal
goes out **only** when (a) reconciliation still reports the owner
``DEFINITELY_ALIVE`` (the pid slot observably holds the recorded owner)
**and** (b) the owner group differs from our own process group and
session (never signal our own groups).  Anything else returns an
unconfirmed proof and signals nothing; the caller maps unconfirmed
cancellation to ``BLOCKED``, never to ``CANCELLED``.

Dependency rule: this module imports the standard library plus
``confflow.persistence.contracts`` types at module load.  Owner
reconciliation and ``psutil`` are imported deferred inside functions, and
this module never imports ``confflow.execution``, ``confflow.workflow``,
``confflow.calc``, ``confflow.core``, or any legacy runtime.  The
liveness and cancellation algorithms are ported (not imported) from the
legacy supervision helpers.
"""

from __future__ import annotations

import os
import signal
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

from ..persistence.contracts import OwnerIdentity, OwnerVerdict

__all__ = ["CancelProof", "attempt_liveness", "cancel_attempt"]

#: Poll quantum (seconds) while waiting for a signalled owner to die.
_CANCEL_POLL_SECONDS: Final[float] = 0.05

#: Bounded follow-up wait (seconds) after the SIGKILL escalation.
_CANCEL_KILL_WAIT_SECONDS: Final[float] = 10.0


@dataclass(frozen=True, slots=True)
class CancelProof:
    """Outcome of one :func:`cancel_attempt` call.

    Parameters
    ----------
    confirmed : bool
        True only when death of the owner boundary was re-proven after
        signalling.  False means nothing was signalled, or death could
        not be re-proven; the caller must map that to ``BLOCKED``.
    detail : str
        Human-readable explanation naming the verdict and what was (or
        was not) signalled.
    verdict : OwnerVerdict
        Final liveness verdict for the owner: ``DEFINITELY_DEAD`` on a
        confirmed kill, otherwise the current (blocking) verdict.
    """

    confirmed: bool
    detail: str
    verdict: OwnerVerdict


def _maybe_import_psutil() -> Any:
    """Import ``psutil`` when available, otherwise return ``None``.

    Returns
    -------
    Any
        The imported ``psutil`` module, or ``None`` when it is missing or
        broken.  Callers fail closed (assume live) without it.
    """
    try:
        import psutil
    except ImportError:
        return None
    return psutil


def _workdir_holds_live_process(work_dir: str, *, owner: OwnerIdentity) -> bool:
    """Return whether any live process still holds *work_dir*.

    The check adapts the legacy work-process probe: a ``killpg(pgid, 0)``
    existence probe against the recorded owner group (which never sends a
    termination signal), followed by a cwd-prefix scan over live
    processes.  The calling process itself is never counted, and any
    ambiguity (unreadable state, missing ``psutil``, scan failure) counts
    as live so recovery blocks instead of launching a duplicate.

    Parameters
    ----------
    work_dir : str
        Attempt working directory to scan for live holders.
    owner : OwnerIdentity
        Recorded owner whose process group is probed first.

    Returns
    -------
    bool
        True when a live holder exists or liveness cannot be disproven.
    """
    target = Path(work_dir).resolve(strict=False)
    pgid = owner.process_group_id
    if isinstance(pgid, int) and pgid > 0 and hasattr(os, "killpg"):
        try:
            os.killpg(pgid, 0)
        except ProcessLookupError:
            pass
        except PermissionError:
            return True
        except OSError:
            return True
        else:
            return True
    psutil_mod = _maybe_import_psutil()
    if psutil_mod is None:
        return True
    denied = tuple(
        marker
        for name in ("AccessDenied", "NoSuchProcess", "ZombieProcess")
        for marker in (getattr(psutil_mod, name, None),)
        if isinstance(marker, type) and issubclass(marker, BaseException)
    )
    try:
        candidates = psutil_mod.process_iter(["pid", "cwd"])
    except Exception:
        return True
    self_pid = os.getpid()
    for candidate in candidates:
        try:
            info = candidate.info
        except Exception:
            continue
        if not isinstance(info, dict) or info.get("pid") == self_pid:
            continue
        try:
            cwd = info.get("cwd")
        except denied as exc:
            del exc
            continue
        except Exception:
            return True
        if not cwd:
            continue
        try:
            resolved = Path(cwd).resolve(strict=False)
        except OSError:
            return True
        if resolved == target or target in resolved.parents:
            return True
    return False


def attempt_liveness(*, owner: OwnerIdentity, work_dir: str | None = None) -> OwnerVerdict:
    """Judge liveness of a recorded attempt owner, failing closed.

    Parameters
    ----------
    owner : OwnerIdentity
        Process-boundary identity recorded at claim time.
    work_dir : str | None
        Attempt working directory.  When given, a live holder of this
        directory upgrades a ``DEFINITELY_DEAD`` reconcile verdict to
        ``UNCERTAIN`` (an orphaned descendant may still hold the claim
        boundary); ``DEFINITELY_ALIVE`` and ``UNCERTAIN`` reconcile
        verdicts pass through unchanged.

    Returns
    -------
    OwnerVerdict
        The combined verdict; ``UNCERTAIN`` blocks duplicate launch.
    """
    from ..persistence.recovery import reconcile_owner

    primary = reconcile_owner(owner)
    if work_dir is None or primary is not OwnerVerdict.DEFINITELY_DEAD:
        return primary
    if _workdir_holds_live_process(work_dir, owner=owner):
        return OwnerVerdict.UNCERTAIN
    return OwnerVerdict.DEFINITELY_DEAD


def _own_group_ids() -> tuple[int | None, int | None]:
    """Return this process's ``(pgid, sid)``, with ``None`` when unreadable."""
    try:
        own_pgid: int | None = os.getpgid(0)
    except OSError:
        own_pgid = None
    if not hasattr(os, "getsid"):
        return own_pgid, None
    try:
        return own_pgid, os.getsid(0)
    except OSError:
        return own_pgid, None


def _reconcile_now(owner: OwnerIdentity) -> OwnerVerdict:
    """Re-prove owner liveness with a deferred reconciliation import."""
    from ..persistence.recovery import reconcile_owner

    return reconcile_owner(owner)


def _wait_for_verdict(owner: OwnerIdentity, want: OwnerVerdict, *, deadline: float) -> OwnerVerdict:
    """Poll reconciliation until *want* or *deadline* seconds elapse.

    Parameters
    ----------
    owner : OwnerIdentity
        Recorded owner to re-prove.
    want : OwnerVerdict
        Verdict that ends the wait early.
    deadline : float
        Maximum seconds to wait (monotonic).

    Returns
    -------
    OwnerVerdict
        The last verdict observed (``want`` when it arrived in time).
    """
    end = time.monotonic() + max(0.0, deadline)
    current = _reconcile_now(owner)
    while current is not want:
        if time.monotonic() >= end:
            break
        time.sleep(_CANCEL_POLL_SECONDS)
        current = _reconcile_now(owner)
    return current


def cancel_attempt(
    *, owner: OwnerIdentity, work_dir: str | None, grace_seconds: float = 2.0
) -> CancelProof:
    """Cancel a live attempt owner with SIGTERM-then-SIGKILL escalation.

    The owner process group is signalled only when reconciliation still
    reports the owner ``DEFINITELY_ALIVE`` and the owner group differs
    from our own process group and session.  Every other case returns an
    unconfirmed proof without signalling anything.  After signalling,
    death is re-proven through reconciliation: only ``DEFINITELY_DEAD``
    confirms the cancellation.

    Parameters
    ----------
    owner : OwnerIdentity
        Recorded owner to cancel.
    work_dir : str | None
        Provenance only; reserved for caller-side diagnostics and never
        used to select a signal target.  The signal target is always the
        recorded owner process group.
    grace_seconds : float
        Seconds to wait for death after SIGTERM before escalating to
        SIGKILL.  Must be non-negative.

    Returns
    -------
    CancelProof
        Confirmed proof when death was re-proven after signalling, else
        an unconfirmed proof carrying the current (blocking) verdict.

    Raises
    ------
    ValueError
        When *grace_seconds* is negative.
    """
    del work_dir
    if not isinstance(grace_seconds, (int, float)) or isinstance(grace_seconds, bool):
        raise ValueError(f"grace_seconds must be a number: {grace_seconds!r}")
    if grace_seconds < 0:
        raise ValueError(f"grace_seconds must be non-negative: {grace_seconds!r}")
    verdict = _reconcile_now(owner)
    if verdict is not OwnerVerdict.DEFINITELY_ALIVE:
        return CancelProof(
            confirmed=False,
            detail=f"owner is not provably alive (verdict={verdict.value}); nothing signalled",
            verdict=verdict,
        )
    pid = owner.pid
    pgid = owner.process_group_id
    if (
        not isinstance(pid, int)
        or isinstance(pid, bool)
        or pid <= 0
        or not isinstance(pgid, int)
        or isinstance(pgid, bool)
        or pgid <= 1
    ):
        return CancelProof(
            confirmed=False,
            detail="owner lacks a signallable process group; nothing signalled",
            verdict=verdict,
        )
    own_pgid, own_sid = _own_group_ids()
    if (own_pgid is not None and pgid == own_pgid) or (own_sid is not None and pgid == own_sid):
        return CancelProof(
            confirmed=False,
            detail=f"owner group {pgid} matches our own group/session; nothing signalled",
            verdict=verdict,
        )
    try:
        os.killpg(pgid, signal.SIGTERM)
    except ProcessLookupError:
        final = _reconcile_now(owner)
        return CancelProof(
            confirmed=final is OwnerVerdict.DEFINITELY_DEAD,
            detail=f"owner group {pgid} already gone (verdict={final.value})",
            verdict=final,
        )
    except OSError as exc:
        current = _reconcile_now(owner)
        return CancelProof(
            confirmed=False,
            detail=f"signal to owner group {pgid} refused ({exc}); nothing terminated",
            verdict=current,
        )
    current = _wait_for_verdict(owner, OwnerVerdict.DEFINITELY_DEAD, deadline=grace_seconds)
    if current is OwnerVerdict.DEFINITELY_DEAD:
        return CancelProof(
            confirmed=True,
            detail=f"owner group {pgid} terminated after SIGTERM",
            verdict=current,
        )
    try:
        os.killpg(pgid, signal.SIGKILL)
    except ProcessLookupError:
        final = _reconcile_now(owner)
        return CancelProof(
            confirmed=final is OwnerVerdict.DEFINITELY_DEAD,
            detail=f"owner group {pgid} exited during escalation (verdict={final.value})",
            verdict=final,
        )
    except OSError as exc:
        current = _reconcile_now(owner)
        return CancelProof(
            confirmed=False,
            detail=f"SIGKILL to owner group {pgid} refused ({exc}); not confirmed",
            verdict=current,
        )
    final = _wait_for_verdict(
        owner, OwnerVerdict.DEFINITELY_DEAD, deadline=_CANCEL_KILL_WAIT_SECONDS
    )
    if final is OwnerVerdict.DEFINITELY_DEAD:
        return CancelProof(
            confirmed=True,
            detail=f"owner group {pgid} terminated after SIGKILL",
            verdict=final,
        )
    return CancelProof(
        confirmed=False,
        detail=f"owner group {pgid} survived SIGKILL (verdict={final.value}); not confirmed",
        verdict=final,
    )
