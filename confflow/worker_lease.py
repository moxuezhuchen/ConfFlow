"""Control-worker lease ownership and crash-recovery reconciliation."""

from __future__ import annotations

import os
from pathlib import Path

import psutil

from .application.execution.launch_lease import TokenLaunchLease


class TokenLeaseManager:
    """Wrap one token lease and guard recovery with owner/process evidence."""

    def __init__(self, runs_root: str | Path, run_id: str, token: str) -> None:
        self._lease = TokenLaunchLease(runs_root, run_id, token)

    @property
    def path(self) -> Path:
        """Return the wrapped lease's diagnostic marker path."""
        return self._lease.path

    @property
    def previous_owner(self) -> dict[str, object] | None:
        """Return the last worker identity recorded before this claim."""
        return self._lease.previous_owner

    def acquire(self) -> bool:
        """Acquire the wrapped token lease without changing its semantics."""
        return self._lease.acquire()

    def release(self) -> None:
        """Release the wrapped token lease without deleting its marker."""
        self._lease.release()

    def can_recover(self, work_dir: str | Path) -> bool:
        """Return true only when the previous owner is known and gone."""
        owner = self.previous_owner
        return _complete_owner_marker(owner) and not _has_live_work_process(
            str(work_dir), owner=owner
        )

    def __enter__(self) -> TokenLeaseManager:
        if not self.acquire():
            raise RuntimeError("worker launch token is already claimed")
        return self

    def __exit__(self, exc_type: object, exc_value: object, traceback: object) -> None:
        del exc_type, exc_value, traceback
        self.release()


def _complete_owner_marker(owner: dict[str, object] | None) -> bool:
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


def _has_live_work_process(work_dir: str, *, owner: dict[str, object] | None = None) -> bool:
    """Fail closed when a prior worker group or child owns the attempt directory."""
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


__all__ = ["TokenLeaseManager"]
