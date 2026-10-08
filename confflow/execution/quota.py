#!/usr/bin/env python3
"""Server core/memory quota shared by native process groups.

Totals come from server.toml (default ~/.config/confflow/server.toml,
CONFFLOW_SERVER_CONFIG overrides the path); a missing file disables the
quota. The ledger lives in the server state directory under a file lock and binds
cores/memory to the real process group (reserved->running->released); leader_start_time excludes PID reuse.
"""

from __future__ import annotations

import json
import logging
import os
import time
from collections.abc import Callable
from pathlib import Path
from typing import cast

import psutil as _psutil

try:
    import fcntl as _fcntl
except ImportError:  # pragma: no cover - POSIX-only lock
    _fcntl = None  # type: ignore[assignment]

from ..domain.resources import parse_memory_bytes
from ..persistence.fsatomic import publish_bytes

__all__ = [
    "QuotaCancelled",
    "QuotaError",
    "QuotaExceeded",
    "ServerQuota",
    "load_server_quota",
    "read_server_config_toml",
    "server_config_path",
]

_CONFIG_ENV = "CONFFLOW_SERVER_CONFIG"
_STATE_ENV = "CONFFLOW_SERVER_STATE_DIR"

_LOG = logging.getLogger(__name__)


class QuotaError(Exception):
    """Unusable server quota configuration or request."""


class QuotaExceeded(QuotaError):
    """Request larger than the whole server; it never waits."""


class QuotaCancelled(QuotaError):
    """Quota wait abandoned through the cancellation signal."""


def _same_start(pid: int | None, expected: float | None) -> bool | None:
    """Check *pid* started at *expected*: True/False, None when unverifiable."""
    if pid is None or expected is None:
        return None
    try:
        proc = _psutil.Process(pid)
        if proc.status() == getattr(_psutil, "STATUS_ZOMBIE", "zombie"):
            return False
        return abs(float(proc.create_time()) - float(expected)) <= 0.01
    except Exception as exc:
        if type(exc).__name__ in ("NoSuchProcess", "ZombieProcess"):
            return False
        return None


def _pid_alive(pid: int | None) -> bool:
    """Check *pid* names a live process."""
    if pid is None or pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except OSError:
        return True
    return True


def _group_alive(record: dict) -> bool:
    """Check the recorded process group still runs its leader."""
    if _same_start(record.get("leader_pid"), record.get("leader_start_time")) is False:
        return False
    pgid = record.get("pgid")
    if pgid is not None and os.name == "posix" and pgid > 1:
        try:
            os.killpg(pgid, 0)
        except ProcessLookupError:
            return False
        except OSError:
            return True
        return True
    return _pid_alive(record.get("leader_pid"))


def server_config_path(config: str | Path | None = None) -> Path:
    """Return the server.toml path (explicit, env override, or default)."""
    override = config if config is not None else os.environ.get(_CONFIG_ENV)
    if override:
        return Path(override)
    return Path.home() / ".config" / "confflow" / "server.toml"


def read_server_config_toml(path: str | Path) -> dict:
    """Read *path* as TOML, failing closed on unreadable content."""
    location = Path(path)
    try:
        import tomllib as _toml
    except ImportError:
        import tomli as _toml  # type: ignore[import-not-found,no-redef]
    try:
        with open(location, "rb") as handle:
            data = _toml.load(handle)
    except Exception as exc:
        raise QuotaError(f"cannot read server config {location}: {exc}") from exc
    if not isinstance(data, dict):
        raise QuotaError(f"invalid server config {location}: top level must be a table")
    return data


def load_server_quota(config: str | Path | None = None) -> ServerQuota | None:
    """Load the server quota, or return None when server.toml is absent."""
    path = server_config_path(config)
    if not path.is_file():
        return None
    data = read_server_config_toml(path)
    raw_cores = data.get("total_cores")
    if isinstance(raw_cores, bool) or not isinstance(raw_cores, int) or raw_cores < 1:
        raise QuotaError(f"invalid server config {path}: 'total_cores' must be an integer >= 1")
    try:
        total_mem = parse_memory_bytes(cast("str | int | float", data.get("total_memory")))
    except Exception as exc:
        raise QuotaError(f"invalid server config {path}: 'total_memory': {exc}") from exc
    if total_mem <= 0:
        raise QuotaError(f"invalid server config {path}: 'total_memory' must be > 0")
    state = os.environ.get(_STATE_ENV) or str(
        Path.home() / ".local" / "state" / "confflow" / "server"
    )
    return ServerQuota(raw_cores, total_mem, Path(state))


class ServerQuota:
    """File-locked core/memory ledger bound to native process groups."""

    def __init__(self, total_cores: int, total_mem_bytes: int, state_dir: str | Path) -> None:
        self._totals = (int(total_cores), int(total_mem_bytes))
        self._dir = Path(state_dir)
        self._poll = 0.05
        self._grace = 2.0

    def acquire(
        self,
        cores: int | None,
        memory: int | None,
        run_id: str,
        work_item_id: str,
        should_cancel: Callable[[], bool] | None = None,
    ) -> str:
        """Reserve *cores*/*memory*; wait for room, fail fast when oversized."""
        total_cores, total_mem = self._totals
        need_cores = 1 if cores is None else cores
        need_memory = 0 if memory is None else memory
        for value, minimum in ((need_cores, 1), (need_memory, 0)):
            if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
                raise QuotaError(f"invalid quota request ({cores!r}, {memory!r})")
        if need_cores > total_cores or need_memory > total_mem:
            raise QuotaExceeded(
                f"requested ({need_cores} cores, {need_memory} bytes) exceeds server capacity ({total_cores} cores, {total_mem} bytes); oversized requests never wait"
            )
        lease_id = f"{os.getpid()}-{time.time_ns()}"
        record = {
            "run_id": str(run_id),
            "work_item_id": str(work_item_id),
            "cores": need_cores,
            "memory": need_memory,
            "state": "reserved",
            "owner_pid": os.getpid(),
            "reserved_at": 0.0,
        }

        def _claim(leases: dict) -> bool:
            used = [0, 0]
            for item in leases.values():
                if isinstance(item, dict):
                    for index, key in enumerate(("cores", "memory")):
                        value = item.get(key, 0)
                        if isinstance(value, int) and not isinstance(value, bool):
                            used[index] += value
            if used[0] + need_cores > total_cores or used[1] + need_memory > total_mem:
                return False
            record["reserved_at"] = time.time()
            leases[lease_id] = record
            return True

        announced = False
        while True:
            if should_cancel is not None and should_cancel():
                raise QuotaCancelled(f"quota wait for {run_id}/{work_item_id} was cancelled")
            if self._update(_claim):
                return lease_id
            if not announced:
                _LOG.info("quota waiting: %s/%s (%s cores)", run_id, work_item_id, need_cores)
                announced = True
            time.sleep(self._poll)

    def mark_running(
        self,
        lease_id: str,
        pgid: int | None,
        leader_pid: int | None,
        leader_start_time: float | None,
    ) -> None:
        """Promote a reserved lease to running against its process group."""

        def _promote(leases: dict) -> bool:
            record = leases.get(lease_id)
            if not isinstance(record, dict) or record.get("state") != "reserved":
                return False
            record["state"] = "running"
            record["pgid"] = pgid
            record["leader_pid"] = leader_pid
            record["leader_start_time"] = leader_start_time
            return True

        self._update(_promote)

    def release(self, lease_id: str) -> bool:
        """Release *lease_id*; only the first call takes effect."""
        return self._update(lambda leases: leases.pop(lease_id, None) is not None)

    def _update(self, mutate: Callable[[dict], bool]) -> bool:
        """Apply *mutate* to the leases under the file lock."""
        self._dir.mkdir(parents=True, exist_ok=True)
        with open(self._dir / "server-quota.lock", "a+b") as lock:
            if _fcntl is not None:
                _fcntl.flock(lock.fileno(), _fcntl.LOCK_EX)
            ledger = self._dir / "server-quota.json"
            try:
                text = ledger.read_text(encoding="utf-8")
            except FileNotFoundError:
                leases: dict = {}
            except OSError as exc:
                raise QuotaError(f"cannot read quota ledger {ledger}: {exc}") from exc
            else:
                try:
                    raw = json.loads(text)
                except ValueError as exc:
                    raise QuotaError(f"corrupt quota ledger {ledger}: {exc}") from exc
                if not isinstance(raw, dict):
                    raise QuotaError(f"corrupt quota ledger {ledger}: top level must be an object")
                candidate = raw.get("leases", {})
                if not isinstance(candidate, dict):
                    raise QuotaError(f"corrupt quota ledger {ledger}: 'leases' must be an object")
                leases = candidate
            reclaimed = self._reclaim_locked(leases)
            changed = bool(mutate(leases))
            if reclaimed or changed:
                publish_bytes(
                    str(ledger), json.dumps({"leases": leases}, sort_keys=True).encode("utf-8")
                )
            return changed

    def _reclaim_locked(self, leases: dict) -> bool:
        """Drop leases whose process group (or short-lived owner) is gone."""
        before = len(leases)
        for lease_id in list(leases):
            record = leases.get(lease_id)
            if not isinstance(record, dict):
                leases.pop(lease_id, None)
                continue
            try:
                if record.get("state") == "running":
                    if not _group_alive(record):
                        leases.pop(lease_id, None)
                elif time.time() - float(record.get("reserved_at", 0.0)) > self._grace:
                    if not _pid_alive(record.get("owner_pid")):
                        leases.pop(lease_id, None)
            except (TypeError, ValueError):
                leases.pop(lease_id, None)
        return len(leases) != before
