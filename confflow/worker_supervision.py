"""Stop-proof helpers for the producer-owned external worker."""

from __future__ import annotations

import os
from collections.abc import Callable
from pathlib import Path

import psutil

OwnerMarker = dict[str, object] | None
CompleteOwnerMarker = Callable[[OwnerMarker], bool]
HasLiveWorkProcess = Callable[..., bool]


def _complete_owner_marker(owner: OwnerMarker) -> bool:
    """Require a marker produced by the lease-aware worker implementation."""
    if not isinstance(owner, dict):
        return False
    pid = owner.get("pid")
    pgid = owner.get("pgid")
    return (
        isinstance(pid, int)
        and pid > 0
        and isinstance(pgid, int)
        and pgid > 0
        and owner.get("isolated_session") is True
    )


def _cancel_owner_is_stopped(
    work_dir: str,
    *,
    owner: OwnerMarker,
    complete_owner_marker: CompleteOwnerMarker | None = None,
    has_live_work_process: HasLiveWorkProcess | None = None,
) -> bool:
    """Prove that a queued cancellation has no active worker to stop."""
    complete_marker = (
        _complete_owner_marker if complete_owner_marker is None else complete_owner_marker
    )
    live_process = (
        _has_live_work_process if has_live_work_process is None else has_live_work_process
    )
    if owner is not None and not complete_marker(owner):
        return False
    return not live_process(work_dir, owner=owner)


def _has_live_work_process(work_dir: str, *, owner: OwnerMarker = None) -> bool:
    """Fail closed when a prior worker group or child owns the attempt directory.

    os.killpg(..., 0) only probes process-group existence; this helper never
    sends a termination signal. An operator or supervisor must drain an active
    or detached child before lifecycle recovery can proceed.
    """
    target = Path(work_dir).resolve(strict=False)
    owner_pgid = owner.get("pgid") if isinstance(owner, dict) else None
    if isinstance(owner_pgid, int) and owner_pgid > 0 and hasattr(os, "killpg"):
        try:
            os.killpg(owner_pgid, 0)
        except ProcessLookupError:
            pass
        except PermissionError:
            return True
        else:
            return True
    for process in psutil.process_iter(["pid", "cwd"]):
        if process.info.get("pid") == os.getpid():
            continue
        try:
            cwd = process.info.get("cwd")
        except (psutil.AccessDenied, psutil.NoSuchProcess, psutil.ZombieProcess):
            continue
        if cwd:
            try:
                resolved_cwd = Path(cwd).resolve(strict=False)
            except OSError:
                return True
            if resolved_cwd == target or target in resolved_cwd.parents:
                return True
    return False


__all__ = [
    "_cancel_owner_is_stopped",
    "_complete_owner_marker",
    "_has_live_work_process",
]
