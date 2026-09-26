#!/usr/bin/env python3

"""V4-3 abandoned-``RUNNING`` owner reconciliation (fail-closed liveness).

Each test pins one row of the verdict-rule table in
:mod:`confflow.persistence.recovery`:

- a live owned boundary reconciles ``DEFINITELY_ALIVE`` with the full triple;
- an exited, reaped owner reconciles ``DEFINITELY_DEAD``;
- a ghost pid reconciles ``DEFINITELY_DEAD``, with and without group ids;
- ``pid=None`` reconciles ``UNCERTAIN``;
- a creation-time mismatch on a live pid never reconciles ``DEFINITELY_ALIVE``;
- an orphaned same-group descendant keeps the boundary ``DEFINITELY_ALIVE``
  after its root exited (``/bin/sh -c 'sleep 30 & exit'``);
- an absent ``psutil`` degrades every verdict to ``UNCERTAIN`` via the
  injectable module hook (no monkeypatching of production code);
- the owner token survives a ``to_dict`` round-trip.

All process tests use poll loops with generous deadlines instead of fixed
sub-second sleeps, and every spawned boundary is terminated in a ``finally``
so no stray ``sleep`` outlives the suite.
"""

from __future__ import annotations

import ast
import contextlib
import inspect
import os
import signal
import subprocess
import time
from collections.abc import Callable, Iterator
from typing import Final

import pytest

from confflow.persistence.contracts import OwnerIdentity, OwnerVerdict, wall_now
from confflow.persistence.recovery import owner_identity_current, reconcile_owner

psutil = pytest.importorskip("psutil")

#: Poll quantum (seconds) for liveness wait loops.
_POLL_SECONDS: Final[float] = 0.05

#: Generous deadline (seconds) for every wait loop; far above scheduling
#: jitter so loaded CI runners never flake.
_DEADLINE_SECONDS: float = 30.0

#: A pid that cannot name a real process on any supported platform.
_GHOST_PID: int = 2**30

#: A process-group id that cannot name a real group (never spawned here).
_GHOST_PGID: int = 2**29


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


def _identity_of(proc: subprocess.Popen[str], *, token: str) -> OwnerIdentity:
    """Capture the owner triple of a live spawned process.

    Parameters
    ----------
    proc : subprocess.Popen[str]
        Spawned child, still running (its pid slot is read, never signalled).
    token : str
        Owner token recorded alongside the triple.

    Returns
    -------
    OwnerIdentity
        Full triple (pid, process-group, session, creation time) for *proc*.
    """
    pid = proc.pid
    assert proc.poll() is None, "child exited before its triple could be captured"
    return OwnerIdentity(
        owner_token=token,
        pid=pid,
        process_group_id=os.getpgid(pid),
        session_id=os.getsid(pid),
        create_time=float(psutil.Process(pid).create_time()),
        claimed_wall=wall_now(),
    )


def _terminate_group(pgid: int | None, *, deadline: float = _DEADLINE_SECONDS) -> None:
    """Terminate every member of *pgid* without touching our own group.

    Parameters
    ----------
    pgid : int | None
        Process-group id to stop, or ``None`` when there is nothing to do.
    deadline : float
        Maximum seconds to wait for the group to go quiet.
    """
    if pgid is None or pgid <= 1 or pgid == os.getpgrp():
        return
    with contextlib.suppress(OSError):
        os.killpg(pgid, signal.SIGTERM)
    if _wait_until(lambda: _group_is_quiet(pgid), deadline=deadline):
        return
    with contextlib.suppress(OSError):
        os.killpg(pgid, signal.SIGKILL)
    quiet = _wait_until(lambda: _group_is_quiet(pgid), deadline=deadline)
    assert quiet, f"process group {pgid} stayed live after SIGKILL"


def _group_is_quiet(pgid: int) -> bool:
    """Return whether *pgid* currently holds no live non-zombie member.

    Parameters
    ----------
    pgid : int
        Process-group id probed read-only via ``psutil`` and ``os.getpgid``.

    Returns
    -------
    bool
        True when no live member of *pgid* (other than this process) exists.
    """
    zombie_marker = getattr(psutil, "STATUS_ZOMBIE", "zombie")
    self_pid = os.getpid()
    for candidate in psutil.process_iter(["pid", "status"]):
        member_pid = candidate.pid
        if not isinstance(member_pid, int) or member_pid <= 0 or member_pid == self_pid:
            continue
        try:
            if os.getpgid(member_pid) != pgid:
                continue
        except OSError:
            continue
        try:
            info = candidate.info
            status = info.get("status") if isinstance(info, dict) else None
            if status is None:
                status = candidate.status()
            if status == zombie_marker:
                continue
            if not candidate.is_running():
                continue
        except (psutil.Error, OSError, RuntimeError, AttributeError):
            continue
        return False
    return True


@contextlib.contextmanager
def _live_sleep(
    *, seconds: str = "30", token: str = "owner-test"
) -> Iterator[subprocess.Popen[str]]:
    """Yield a live ``sleep`` child in its own session, terminating it after.

    Parameters
    ----------
    seconds : str
        Sleep duration passed to ``sleep`` (long enough to outlive the test).
    token : str
        Unused label kept so call sites read symmetrically with identities.

    Yields
    ------
    subprocess.Popen[str]
        The running child, isolated via ``start_new_session=True``.
    """
    proc = subprocess.Popen(["sleep", seconds], start_new_session=True)
    try:
        assert _wait_until(lambda: proc.poll() is None, deadline=_DEADLINE_SECONDS)
        yield proc
    finally:
        with contextlib.suppress(OSError):
            proc.terminate()
        try:
            proc.wait(timeout=_DEADLINE_SECONDS)
        except subprocess.TimeoutExpired:
            with contextlib.suppress(OSError):
                proc.kill()
            proc.wait(timeout=_DEADLINE_SECONDS)


class TestLiveOwner:
    """A live owned boundary with the full triple is definitely alive."""

    def test_live_sleep_with_full_triple(self) -> None:
        with _live_sleep(token="owner-live") as proc:
            identity = _identity_of(proc, token="owner-live")
            assert identity.pid == proc.pid
            assert identity.process_group_id is not None
            assert identity.session_id is not None
            assert identity.create_time is not None
            assert reconcile_owner(identity) is OwnerVerdict.DEFINITELY_ALIVE


class TestExitedOwner:
    """An exited, reaped owner is definitely dead."""

    def test_exited_reaped_sleep(self) -> None:
        proc = subprocess.Popen(["sleep", "1"], start_new_session=True)
        try:
            identity = _identity_of(proc, token="owner-exited")
        except AssertionError:
            proc.wait(timeout=_DEADLINE_SECONDS)
            raise
        assert proc.wait(timeout=_DEADLINE_SECONDS) is not None
        assert proc.poll() is not None
        assert reconcile_owner(identity) is OwnerVerdict.DEFINITELY_DEAD


class TestGhostPid:
    """A pid that names nothing is dead, with or without recorded groups."""

    def test_ghost_pid_with_full_triple(self) -> None:
        assert psutil.pid_exists(_GHOST_PID) is False
        identity = OwnerIdentity(
            owner_token="owner-ghost",
            pid=_GHOST_PID,
            process_group_id=_GHOST_PID,
            session_id=_GHOST_PID,
            create_time=wall_now(),
            claimed_wall=wall_now(),
        )
        assert reconcile_owner(identity) is OwnerVerdict.DEFINITELY_DEAD

    def test_ghost_pid_with_empty_groups(self) -> None:
        assert psutil.pid_exists(_GHOST_PID) is False
        identity = OwnerIdentity(owner_token="owner-ghost-empty", pid=_GHOST_PID)
        assert identity.process_group_id is None
        assert identity.session_id is None
        assert reconcile_owner(identity) is OwnerVerdict.DEFINITELY_DEAD


class TestMissingPid:
    """Without a pid there is no identity token, so recovery must block."""

    def test_pid_none_is_uncertain(self) -> None:
        identity = OwnerIdentity(owner_token="owner-nopid", pid=None)
        assert reconcile_owner(identity) is OwnerVerdict.UNCERTAIN

    def test_pid_none_with_groups_is_uncertain(self) -> None:
        identity = OwnerIdentity(
            owner_token="owner-nopid-groups",
            pid=None,
            process_group_id=os.getpgrp(),
            session_id=os.getsid(0),
        )
        assert reconcile_owner(identity) is OwnerVerdict.UNCERTAIN
        assert reconcile_owner(identity, psutil_module=None) is OwnerVerdict.UNCERTAIN


class TestCreateTimeMismatch:
    """A creation-time mismatch (pid reuse) never reports definitely alive."""

    def test_mismatch_with_live_group_member_is_uncertain(self) -> None:
        with _live_sleep(token="owner-reused") as proc:
            genuine = _identity_of(proc, token="owner-reused")
            assert genuine.create_time is not None
            forged = OwnerIdentity(
                owner_token="owner-reused",
                pid=genuine.pid,
                process_group_id=genuine.process_group_id,
                session_id=genuine.session_id,
                create_time=genuine.create_time + 3600.0,
                claimed_wall=genuine.claimed_wall,
            )
            verdict = reconcile_owner(forged)
            assert verdict is not OwnerVerdict.DEFINITELY_ALIVE
            assert verdict is OwnerVerdict.UNCERTAIN

    def test_mismatch_with_empty_groups_is_dead(self) -> None:
        with _live_sleep(token="owner-reused-empty") as proc:
            genuine = _identity_of(proc, token="owner-reused-empty")
            assert genuine.create_time is not None
            forged = OwnerIdentity(
                owner_token="owner-reused-empty",
                pid=genuine.pid,
                process_group_id=_GHOST_PGID,
                session_id=_GHOST_PGID,
                create_time=genuine.create_time + 3600.0,
                claimed_wall=genuine.claimed_wall,
            )
            assert reconcile_owner(forged) is OwnerVerdict.DEFINITELY_DEAD


class TestOrphanDescendant:
    """A same-group descendant keeps the boundary alive after its root exits."""

    def test_parent_dead_but_group_child_alive(self) -> None:
        proc = subprocess.Popen(
            ["/bin/sh", "-c", "sleep 30 & exit"],
            start_new_session=True,
        )
        pgid: int | None = None
        try:
            assert proc.pid > 0
            pgid = os.getpgid(proc.pid)
            sid = os.getsid(proc.pid)
            create_time = float(psutil.Process(proc.pid).create_time())
            identity = OwnerIdentity(
                owner_token="owner-orphan",
                pid=proc.pid,
                process_group_id=pgid,
                session_id=sid,
                create_time=create_time,
                claimed_wall=wall_now(),
            )
            assert proc.wait(timeout=_DEADLINE_SECONDS) is not None
            assert proc.poll() is not None
            assert _wait_until(
                lambda: reconcile_owner(identity) is OwnerVerdict.DEFINITELY_ALIVE
            ), "orphaned same-group sleep must keep the boundary alive"
        finally:
            _terminate_group(pgid)
            with contextlib.suppress(OSError):
                proc.wait(timeout=_DEADLINE_SECONDS)

    def test_boundary_goes_dead_once_descendant_stops(self) -> None:
        proc = subprocess.Popen(
            ["/bin/sh", "-c", "sleep 30 & exit"],
            start_new_session=True,
        )
        pgid: int | None = None
        try:
            pgid = os.getpgid(proc.pid)
            identity = OwnerIdentity(
                owner_token="owner-orphan-drain",
                pid=proc.pid,
                process_group_id=pgid,
                session_id=os.getsid(proc.pid),
                create_time=float(psutil.Process(proc.pid).create_time()),
                claimed_wall=wall_now(),
            )
            assert proc.wait(timeout=_DEADLINE_SECONDS) is not None
            assert _wait_until(
                lambda: reconcile_owner(identity) is OwnerVerdict.DEFINITELY_ALIVE
            ), "orphaned same-group sleep must keep the boundary alive"
            _terminate_group(pgid)
            drained = _wait_until(lambda: reconcile_owner(identity) is OwnerVerdict.DEFINITELY_DEAD)
            assert drained, "drained boundary must reconcile dead on the same identity"
        finally:
            _terminate_group(pgid)
            with contextlib.suppress(OSError):
                proc.wait(timeout=_DEADLINE_SECONDS)


class TestPsutilAbsent:
    """Without ``psutil`` every verdict degrades to uncertain, never crashes."""

    def test_live_owner_without_psutil_is_uncertain(self) -> None:
        with _live_sleep(token="owner-nopsutil") as proc:
            identity = _identity_of(proc, token="owner-nopsutil")
            assert reconcile_owner(identity, psutil_module=None) is OwnerVerdict.UNCERTAIN

    def test_ghost_pid_without_psutil_is_uncertain(self) -> None:
        identity = OwnerIdentity(owner_token="owner-ghost-nopsutil", pid=_GHOST_PID)
        assert reconcile_owner(identity, psutil_module=None) is OwnerVerdict.UNCERTAIN

    def test_current_identity_without_psutil_has_no_create_time(self) -> None:
        identity = owner_identity_current(owner_token="owner-here", psutil_module=None)
        assert identity.pid == os.getpid()
        assert identity.create_time is None
        assert reconcile_owner(identity, psutil_module=None) is OwnerVerdict.UNCERTAIN


class TestCurrentIdentity:
    """The current process captures and reconciles its own triple."""

    def test_current_identity_matches_this_process(self) -> None:
        before = wall_now()
        identity = owner_identity_current(owner_token="owner-self")
        after = wall_now()
        assert identity.owner_token == "owner-self"
        assert identity.pid == os.getpid()
        assert identity.process_group_id == os.getpgid(os.getpid())
        assert identity.session_id == os.getsid(0)
        assert identity.create_time is not None
        assert identity.create_time <= after
        assert identity.claimed_wall is not None
        assert before <= identity.claimed_wall <= after

    def test_current_identity_reconciles_alive(self) -> None:
        identity = owner_identity_current(owner_token="owner-self-alive")
        assert reconcile_owner(identity) is OwnerVerdict.DEFINITELY_ALIVE


class TestTokenRoundTrip:
    """The owner token and triple survive a ``to_dict`` round-trip."""

    def test_to_dict_round_trip(self) -> None:
        with _live_sleep(token="owner-roundtrip") as proc:
            identity = _identity_of(proc, token="owner-roundtrip")
            payload = identity.to_dict()
            assert payload == {
                "owner_token": "owner-roundtrip",
                "pid": identity.pid,
                "process_group_id": identity.process_group_id,
                "session_id": identity.session_id,
                "create_time": identity.create_time,
                "claimed_wall": identity.claimed_wall,
            }
            rebuilt = OwnerIdentity(**payload)
            assert rebuilt == identity
            assert rebuilt.owner_token == "owner-roundtrip"
            assert reconcile_owner(rebuilt) is OwnerVerdict.DEFINITELY_ALIVE


def _code_text_only(source: str) -> str:
    """Strip docstrings, leaving code tokens only (mirrors the arch gate)."""
    tree = ast.parse(source)

    def _strip(node: ast.AST) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.Expr) and isinstance(child.value, ast.Constant):
                child.value.value = ""
            _strip(child)

    _strip(tree)
    return ast.unparse(tree)


class TestRecoveryHygiene:
    """The recovery module stays pure: no signalling, no executor imports."""

    def test_no_signalling_or_executor_vocabulary_as_code(self) -> None:
        import confflow.persistence.recovery as recovery_module

        text = _code_text_only(inspect.getsource(recovery_module))
        for forbidden in (
            "killpg",
            "os.kill",
            "Popen",
            "SIGKILL",
            "SIGTERM",
            "send_signal",
            "check_output",
            "execution",
            "calc",
            "shared",
            "config",
            "WorkflowPlan",
            "CalcStepRunner",
            "input_xyz",
            "output_path",
        ):
            assert forbidden not in text, forbidden

    def test_imports_are_stdlib_psutil_contracts_only(self) -> None:
        import confflow.persistence.recovery as recovery_module

        tree = ast.parse(inspect.getsource(recovery_module))
        imported: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                if node.level:
                    assert node.module == "contracts", node.module
                elif node.module is not None:
                    imported.add(node.module.split(".")[0])
        assert imported <= {"__future__", "os", "typing", "psutil"}, imported

    def test_production_never_monkeypatched(self) -> None:
        import confflow.persistence.recovery as recovery_module

        assert hasattr(recovery_module, "_AUTO_PSUTIL")
        assert recovery_module._PSUTIL is not None
        code_only = _code_text_only(inspect.getsource(recovery_module))
        assert "monkeypatch" not in code_only
        assert "sys.modules" not in code_only
