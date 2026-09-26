#!/usr/bin/env python3

"""V4-4 remote lifecycle: attempt leases plus supervision (liveness/cancel).

Lease rows:

- the same attempt and token from many threads: exactly one acquisition;
- the same attempt and token from another process while held: refused;
- the same attempt under a different launch token: refused
  (duplicate-launch prevention);
- a child that exits without release drops the kernel lock, so the parent
  can claim the same token and reads the child back as ``previous_owner``;
- ``previous_owner`` round-trips the recorded attempt identity;
- unsafe identity components (``/``, ``..``, empty, leading punctuation,
  spaces, ``:`` outside ``work_item_id``) raise :class:`LeaseError`;
- ``work_item_id`` may contain ``':'`` (mapped to ``'+'`` in the marker
  name) as the single documented exception.

Supervision rows (verdict-combination table is pinned in
:mod:`confflow.remote.supervision`):

- a live owned ``sleep`` in its workdir reconciles ``DEFINITELY_ALIVE``;
- an exited, reaped owner reconciles ``DEFINITELY_DEAD``;
- a ghost pid reconciles ``DEFINITELY_DEAD`` (pinned actual behaviour);
- ``DEFINITELY_DEAD`` plus a live holder of the workdir combines to
  ``UNCERTAIN``; a dead workdir stays ``DEFINITELY_DEAD``;
- cancelling an owned ``sleep`` boundary confirms and the process is gone;
- cancelling a ghost is unconfirmed and signals nothing (an unrelated
  marker process stays alive);
- an owner describing our own process group is never signalled.

Every spawned process is reaped in a ``finally``; a trailing guard test
terminates stragglers and asserts no ``sleep 119`` marker process
survives the file.
"""

from __future__ import annotations

import concurrent.futures
import dataclasses
import multiprocessing
import os
import shutil
import subprocess
import threading
import time
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any, Final

import psutil
import pytest

from confflow.persistence.contracts import OwnerIdentity, OwnerVerdict, wall_now
from confflow.remote.lease import AttemptLease, LeaseError
from confflow.remote.supervision import CancelProof, attempt_liveness, cancel_attempt

#: Marker sleep duration identifying processes spawned by this file.
_SLEEP_SECONDS: Final[str] = "119"

#: Poll quantum (seconds) for liveness wait loops.
_POLL_SECONDS: Final[float] = 0.05

#: Generous deadline (seconds) for every wait loop; far above scheduling
#: jitter so loaded CI runners never flake.
_DEADLINE_SECONDS: Final[float] = 30.0

#: A pid that cannot name a real process on any supported platform.
_GHOST_PID: Final[int] = 2**30

#: Group/session ids that cannot name a real group (never spawned here).
_GHOST_PGID: Final[int] = 2**29
_GHOST_SID: Final[int] = 2**28

#: Every child spawned by this file, for the trailing stray-process guard.
_SPAWNED: Final[list[subprocess.Popen[bytes]]] = []


def _wait_until(predicate: Callable[[], bool], *, deadline: float = _DEADLINE_SECONDS) -> bool:
    """Poll *predicate* until true or *deadline* seconds elapse.

    Parameters
    ----------
    predicate : Callable[[], bool]
        Condition polled every :data:`_POLL_SECONDS`.
    deadline : float
        Maximum seconds to wait.

    Returns
    -------
    bool
        True when *predicate* held before the deadline, else False.
    """
    end = time.monotonic() + deadline
    while True:
        if predicate():
            return True
        if time.monotonic() >= end:
            return False
        time.sleep(_POLL_SECONDS)


def _spawn_sleep(work_dir: str | None = None) -> subprocess.Popen[bytes]:
    """Spawn ``sleep 119`` in its own session, tracking it for cleanup.

    Parameters
    ----------
    work_dir : str | None
        Working directory of the child (its cwd is what the workdir
        scan observes), or ``None`` to inherit.

    Returns
    -------
    subprocess.Popen[bytes]
        The tracked child handle.
    """
    proc = subprocess.Popen(["sleep", _SLEEP_SECONDS], cwd=work_dir, start_new_session=True)
    _SPAWNED.append(proc)
    return proc


def _terminate(proc: subprocess.Popen[bytes]) -> None:
    """Terminate *proc*, escalating to kill, then reap it.

    Parameters
    ----------
    proc : subprocess.Popen[bytes]
        Child to stop and reap.
    """
    if proc.poll() is None:
        proc.terminate()
        try:
            proc.wait(timeout=15.0)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=15.0)


def _owner_of(proc: subprocess.Popen[bytes], token: str) -> OwnerIdentity:
    """Capture the live owner triple of a spawned child.

    Parameters
    ----------
    proc : subprocess.Popen[bytes]
        Live child whose boundary is recorded.
    token : str
        Claim token binding the identity to one controller.

    Returns
    -------
    OwnerIdentity
        Identity triple for the child process.
    """
    assert proc.pid is not None
    return OwnerIdentity(
        owner_token=token,
        pid=proc.pid,
        process_group_id=os.getpgid(proc.pid),
        session_id=os.getsid(proc.pid),
        create_time=psutil.Process(proc.pid).create_time(),
        claimed_wall=wall_now(),
    )


def _ghost_owner(token: str = "ghost-token") -> OwnerIdentity:
    """Return an owner pointing at a pid that can never exist.

    Parameters
    ----------
    token : str
        Claim token carried by the ghost identity.

    Returns
    -------
    OwnerIdentity
        Identity with a ghost pid plus ghost group/session ids.
    """
    return OwnerIdentity(
        owner_token=token,
        pid=_GHOST_PID,
        process_group_id=_GHOST_PGID,
        session_id=_GHOST_SID,
        create_time=1.0,
        claimed_wall=1.0,
    )


@pytest.fixture()
def lease_root(tmp_path: Path) -> Iterator[str]:
    """Yield an owner-private lease root under ``tmp_path``.

    Parameters
    ----------
    tmp_path : Path
        Pytest-provided temporary directory.

    Yields
    ------
    str
        Owner-private directory path used as the lease root.
    """
    root = tmp_path / "leases"
    root.mkdir(mode=0o700)
    os.chmod(root, 0o700)
    yield str(root)


def _lease_params(lease_root: str, token: str = "tok-hex-01") -> dict[str, Any]:
    """Return standard lease constructor arguments for one attempt.

    Parameters
    ----------
    lease_root : str
        Lease root directory.
    token : str
        Launch token for the attempt.

    Returns
    -------
    dict[str, Any]
        Keyword arguments for :class:`AttemptLease`.
    """
    return {
        "lease_root": lease_root,
        "run_id": "run01",
        "step_id": "stepA",
        "work_item_id": "mol:0007",
        "attempt_number": 2,
        "launch_token": token,
    }


def _mp_try_acquire(params: dict[str, Any]) -> bool:
    """Process-pool worker: attempt one acquisition, reporting success.

    Parameters
    ----------
    params : dict[str, Any]
        Keyword arguments for :class:`AttemptLease`.

    Returns
    -------
    bool
        True when the acquisition succeeded.
    """
    from confflow.remote.lease import AttemptLease

    return AttemptLease(**params).acquire()


def _mp_acquire_and_exit(params: dict[str, Any]) -> None:
    """Forked child: acquire once, then exit without releasing.

    The kernel drops the advisory lock on exit, modelling a crashed
    holder whose audit marker survives.

    Parameters
    ----------
    params : dict[str, Any]
        Keyword arguments for :class:`AttemptLease`.
    """
    from confflow.remote.lease import AttemptLease

    acquired = AttemptLease(**params).acquire()
    os._exit(0 if acquired else 1)


class TestAttemptLeaseIdentity:
    """Marker layout and identity validation."""

    def test_marker_path_shape(self, lease_root: str) -> None:
        lease = AttemptLease(**_lease_params(lease_root))
        expected = (
            Path(lease_root) / "v4" / "run01" / "stepA" / "mol+0007.attempt-2.tok-hex-01.json"
        )
        assert lease.path == expected

    def test_colon_only_exception_for_work_item_id(self, lease_root: str) -> None:
        lease = AttemptLease(**_lease_params(lease_root))
        assert lease.acquire() is True
        lease.release()
        assert lease.path.exists()

    @pytest.mark.parametrize(
        "field,value",
        [
            ("run_id", ""),
            ("run_id", ".."),
            ("run_id", "."),
            ("run_id", "/"),
            ("run_id", "a/b"),
            ("run_id", "-lead"),
            ("run_id", "_lead"),
            ("run_id", ".lead"),
            ("run_id", "has space"),
            ("run_id", "with:colon"),
            ("run_id", "with+plus"),
            ("step_id", ""),
            ("step_id", ".."),
            ("step_id", "a/b"),
            ("step_id", "trail/"),
            ("step_id", "-lead"),
            ("step_id", "a:b"),
            ("work_item_id", ""),
            ("work_item_id", ".."),
            ("work_item_id", "."),
            ("work_item_id", "a/b"),
            ("work_item_id", "-lead"),
            ("work_item_id", "has space"),
            ("work_item_id", "a+b"),
            ("launch_token", ""),
            ("launch_token", ".."),
            ("launch_token", "a/b"),
            ("launch_token", "-lead"),
            ("launch_token", "tok:01"),
            ("launch_token", "tok 01"),
        ],
    )
    def test_unsafe_components_rejected(self, lease_root: str, field: str, value: str) -> None:
        params = _lease_params(lease_root)
        params[field] = value
        with pytest.raises(LeaseError):
            AttemptLease(**params)

    @pytest.mark.parametrize("attempt", [0, -1, True, "1", 1.5, None])
    def test_bad_attempt_number_rejected(self, lease_root: str, attempt: Any) -> None:
        params = _lease_params(lease_root)
        params["attempt_number"] = attempt
        with pytest.raises(LeaseError):
            AttemptLease(**params)

    def test_lease_error_is_value_error(self) -> None:
        assert issubclass(LeaseError, ValueError)


class TestAttemptLeaseExclusion:
    """Exactly one holder per attempt; crashes release the lock."""

    def test_threads_same_token_exactly_one_acquires(self, lease_root: str) -> None:
        params = _lease_params(lease_root)
        count = 8
        barrier = threading.Barrier(count)
        outcomes: list[bool | None] = [None] * count
        holders: list[AttemptLease] = []
        holders_lock = threading.Lock()

        def _worker(index: int) -> None:
            barrier.wait(timeout=_DEADLINE_SECONDS)
            lease = AttemptLease(**params)
            outcomes[index] = lease.acquire()
            if outcomes[index]:
                with holders_lock:
                    holders.append(lease)

        threads = [threading.Thread(target=_worker, args=(index,)) for index in range(count)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=_DEADLINE_SECONDS)
        assert all(outcome is not None for outcome in outcomes)
        assert sum(1 for outcome in outcomes if outcome) == 1
        for lease in holders:
            lease.release()

    def test_process_same_token_refused_while_held(self, lease_root: str) -> None:
        params = _lease_params(lease_root)
        lease = AttemptLease(**params)
        assert lease.acquire() is True
        try:
            with concurrent.futures.ProcessPoolExecutor(max_workers=1) as pool:
                future = pool.submit(_mp_try_acquire, params)
                assert future.result(timeout=_DEADLINE_SECONDS) is False
        finally:
            lease.release()

    def test_different_token_same_attempt_refused(self, lease_root: str) -> None:
        first = AttemptLease(**_lease_params(lease_root, token="tok-A"))
        assert first.acquire() is True
        try:
            second = AttemptLease(**_lease_params(lease_root, token="tok-B"))
            assert second.acquire() is False
            assert second.path != first.path
            assert second.path.exists() is False
        finally:
            first.release()

    def test_crash_releases_lock_parent_reacquires(self, lease_root: str) -> None:
        params = _lease_params(lease_root)
        context = multiprocessing.get_context("fork")
        proc = context.Process(target=_mp_acquire_and_exit, args=(params,))
        proc.start()
        proc.join(timeout=_DEADLINE_SECONDS)
        assert proc.exitcode == 0
        parent = AttemptLease(**params)
        assert parent.acquire() is True
        try:
            previous = parent.previous_owner
            assert isinstance(previous, dict)
            assert previous["pid"] == proc.pid
            assert previous["launch_token"] == params["launch_token"]
            assert previous["work_item_id"] == params["work_item_id"]
        finally:
            parent.release()

    def test_previous_owner_roundtrip(self, lease_root: str) -> None:
        params = _lease_params(lease_root)
        first = AttemptLease(**params)
        assert first.acquire() is True
        assert first.previous_owner is None
        first.release()
        assert first.path.exists()
        second = AttemptLease(**params)
        assert second.acquire() is True
        try:
            previous = second.previous_owner
            assert isinstance(previous, dict)
            assert previous["pid"] == os.getpid()
            assert previous["run_id"] == "run01"
            assert previous["step_id"] == "stepA"
            assert previous["work_item_id"] == "mol:0007"
            assert previous["attempt_number"] == 2
            assert previous["launch_token"] == "tok-hex-01"
            assert "claimed_wall" in previous
        finally:
            second.release()

    def test_context_manager_acquires_and_releases(self, lease_root: str) -> None:
        params = _lease_params(lease_root)
        with AttemptLease(**params) as lease:
            assert lease.path.exists()
        assert lease.path.exists()
        again = AttemptLease(**params)
        assert again.acquire() is True
        again.release()

    def test_context_manager_refused_when_held(self, lease_root: str) -> None:
        params = _lease_params(lease_root)
        holder = AttemptLease(**params)
        assert holder.acquire() is True
        try:
            with pytest.raises(LeaseError):
                with AttemptLease(**params):
                    pass
        finally:
            holder.release()


class TestAttemptLiveness:
    """Liveness verdicts plus the workdir combination table."""

    def test_live_owned_sleep_is_alive(self, tmp_path: Path) -> None:
        work_dir = tmp_path / "work"
        work_dir.mkdir()
        proc = _spawn_sleep(str(work_dir))
        try:
            assert _wait_until(lambda: os.getpgid(proc.pid) > 0, deadline=5.0)
            owner = _owner_of(proc, "tok-live")
            assert attempt_liveness(owner=owner) is OwnerVerdict.DEFINITELY_ALIVE
            assert (
                attempt_liveness(owner=owner, work_dir=str(work_dir))
                is OwnerVerdict.DEFINITELY_ALIVE
            )
        finally:
            _terminate(proc)

    def test_alive_with_dead_workdir_stays_alive(self, tmp_path: Path) -> None:
        empty_dir = tmp_path / "empty"
        empty_dir.mkdir()
        proc = _spawn_sleep()
        try:
            owner = _owner_of(proc, "tok-live")
            assert (
                attempt_liveness(owner=owner, work_dir=str(empty_dir))
                is OwnerVerdict.DEFINITELY_ALIVE
            )
        finally:
            _terminate(proc)

    def test_exited_owner_is_dead(self, tmp_path: Path) -> None:
        work_dir = tmp_path / "work"
        work_dir.mkdir()
        proc = _spawn_sleep()
        owner = _owner_of(proc, "tok-exited")
        _terminate(proc)
        assert proc.wait(timeout=_DEADLINE_SECONDS) is not None
        assert attempt_liveness(owner=owner) is OwnerVerdict.DEFINITELY_DEAD
        assert attempt_liveness(owner=owner, work_dir=str(work_dir)) is OwnerVerdict.DEFINITELY_DEAD

    def test_ghost_owner_is_dead(self, tmp_path: Path) -> None:
        work_dir = tmp_path / "work"
        work_dir.mkdir()
        ghost = _ghost_owner()
        assert attempt_liveness(owner=ghost) is OwnerVerdict.DEFINITELY_DEAD
        assert attempt_liveness(owner=ghost, work_dir=str(work_dir)) is OwnerVerdict.DEFINITELY_DEAD

    def test_ghost_without_groups_is_dead(self) -> None:
        ghost = OwnerIdentity(owner_token="ghost-nogroup", pid=_GHOST_PID)
        assert attempt_liveness(owner=ghost) is OwnerVerdict.DEFINITELY_DEAD

    def test_dead_reconcile_with_live_workdir_is_uncertain(self, tmp_path: Path) -> None:
        work_dir = tmp_path / "work"
        work_dir.mkdir()
        holder = _spawn_sleep(str(work_dir))
        try:
            ghost = _ghost_owner()
            assert attempt_liveness(owner=ghost) is OwnerVerdict.DEFINITELY_DEAD
            assert attempt_liveness(owner=ghost, work_dir=str(work_dir)) is OwnerVerdict.UNCERTAIN
        finally:
            _terminate(holder)

    def test_missing_pid_is_uncertain(self) -> None:
        owner = OwnerIdentity(owner_token="tok-nopid")
        assert attempt_liveness(owner=owner) is OwnerVerdict.UNCERTAIN


class TestCancelAttempt:
    """SIGTERM-then-SIGKILL escalation with re-proven death."""

    def test_cancel_confirmed_on_owned_sleep(self, tmp_path: Path) -> None:
        work_dir = tmp_path / "work"
        work_dir.mkdir()
        proc = _spawn_sleep(str(work_dir))
        try:
            owner = _owner_of(proc, "tok-cancel")
            assert attempt_liveness(owner=owner) is OwnerVerdict.DEFINITELY_ALIVE
            proof = cancel_attempt(owner=owner, work_dir=str(work_dir), grace_seconds=5.0)
            assert isinstance(proof, CancelProof)
            assert proof.confirmed is True
            assert proof.verdict is OwnerVerdict.DEFINITELY_DEAD
            assert _wait_until(lambda: proc.poll() is not None)
        finally:
            _terminate(proc)

    def test_cancel_ghost_unconfirmed_nothing_signalled(self) -> None:
        marker = _spawn_sleep()
        try:
            ghost = _ghost_owner()
            proof = cancel_attempt(owner=ghost, work_dir=None, grace_seconds=2.0)
            assert proof.confirmed is False
            assert proof.verdict is OwnerVerdict.DEFINITELY_DEAD
            assert "nothing signalled" in proof.detail
            assert marker.poll() is None
            assert _wait_until(lambda: marker.poll() is None, deadline=2.0)
        finally:
            _terminate(marker)

    def test_cancel_never_signals_own_group(self) -> None:
        own = OwnerIdentity(
            owner_token="tok-self",
            pid=os.getpid(),
            process_group_id=os.getpgid(0),
            session_id=os.getsid(0),
            create_time=psutil.Process().create_time(),
            claimed_wall=wall_now(),
        )
        proof = cancel_attempt(owner=own, work_dir=None, grace_seconds=2.0)
        assert proof.confirmed is False
        assert proof.verdict in (OwnerVerdict.DEFINITELY_ALIVE, OwnerVerdict.UNCERTAIN)
        os.killpg(os.getpgid(0), 0)

    def test_cancel_proof_is_frozen(self) -> None:
        proof = CancelProof(confirmed=False, detail="detail", verdict=OwnerVerdict.UNCERTAIN)
        with pytest.raises(dataclasses.FrozenInstanceError):
            proof.confirmed = True  # type: ignore[misc]

    def test_cancel_negative_grace_rejected(self) -> None:
        with pytest.raises(ValueError):
            cancel_attempt(owner=_ghost_owner(), work_dir=None, grace_seconds=-1.0)


def test_zzz_no_stray_marker_processes() -> None:
    """Guard: every ``sleep 119`` spawned above is reaped before exit."""
    for proc in _SPAWNED:
        if proc.poll() is None:
            _terminate(proc)
    assert all(proc.poll() is not None for proc in _SPAWNED)
    if shutil.which("pgrep") is None:  # pragma: no cover - pgrep always exists here
        return
    found = subprocess.run(
        ["pgrep", "-xf", f"sleep {_SLEEP_SECONDS}"],
        capture_output=True,
        text=True,
        timeout=_DEADLINE_SECONDS,
    )
    assert found.returncode == 1, found.stdout
