#!/usr/bin/env python3
"""Server-level core/memory quota for native process groups."""

from __future__ import annotations

import hashlib
import json
import multiprocessing
import os
import signal
import subprocess
import threading
import time
from pathlib import Path

import psutil
import pytest

from confflow.domain import FrozenDict, StructureSet
from confflow.domain.resources import ResourceRequest
from confflow.domain.work_item import WorkItem, WorkItemInputs, make_work_item_id
from confflow.execution.native import (
    MaterializedNativeInput,
    NativeExecutionRequest,
    NativeExecutionResult,
    NativeHandle,
    NativeStatus,
    ProgramName,
)
from confflow.execution.process import NativeProcessSupervisor
from confflow.execution.quota import (
    QuotaCancelled,
    QuotaError,
    QuotaExceeded,
    ServerQuota,
    load_server_quota,
)
from confflow.execution.work_item_executor import ItemExecutionContext, WorkItemExecutor
from confflow.workflow.v4.document import ScientificDefaults, ScientificDefinition
from tests.v4._builders import structure

_GIB = 1024**3


def _quota(state_dir: Path, cores: int = 4, memory: int = 8 * _GIB) -> ServerQuota:
    return ServerQuota(cores, memory, state_dir / "quota-state")


def _active_config(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, cores: int) -> ServerQuota:
    (tmp_path / "server.toml").write_text(
        f"total_cores = {cores}\ntotal_memory = '192GB'\n", encoding="utf-8"
    )
    monkeypatch.setenv("CONFFLOW_SERVER_CONFIG", str(tmp_path / "server.toml"))
    monkeypatch.setenv("CONFFLOW_SERVER_STATE_DIR", str(tmp_path / "quota-state"))
    loaded = load_server_quota()
    assert loaded is not None
    return loaded


def _spawn_session_sleep(seconds: str = "30") -> subprocess.Popen:
    return subprocess.Popen(["/bin/sleep", seconds], start_new_session=True)


def _group_identity(proc: subprocess.Popen) -> tuple[int, int, float]:
    assert proc.pid is not None
    return os.getpgid(proc.pid), proc.pid, float(psutil.Process(proc.pid).create_time())


def _acquire_in_background(quota: ServerQuota, cores: int, memory: int, **kwargs: object) -> dict:
    box: dict = {}

    def _run() -> None:
        try:
            box["lease"] = quota.acquire(cores, memory, "run-bg", "item-bg", **kwargs)  # type: ignore[arg-type]
        except Exception as exc:
            box["error"] = exc
        finally:
            box["done"] = True

    box["thread"] = threading.Thread(target=_run, daemon=True)
    box["thread"].start()
    return box


def _start_and_join(threads: list[threading.Thread], timeout: float) -> None:
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=timeout)


def _hold_quota_then_wait(
    state_dir: str, group: tuple[int, int, float], ready: multiprocessing.Event
) -> None:
    pgid, pid, start = group
    manager = ServerQuota(48, 192 * _GIB, Path(state_dir))
    lease = manager.acquire(48, 192 * _GIB, "run-victim", "item-victim")
    manager.mark_running(lease, pgid, pid, start)
    ready.set()
    time.sleep(30.0)


class TestQuotaConfig:

    def test_missing_config_disables_quota(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("CONFFLOW_SERVER_CONFIG", str(tmp_path / "absent.toml"))
        assert load_server_quota() is None
        monkeypatch.delenv("CONFFLOW_SERVER_CONFIG", raising=False)
        monkeypatch.setenv("HOME", str(tmp_path / "empty-home"))
        assert load_server_quota() is None

    def test_invalid_config_fails_closed(self, tmp_path: Path) -> None:
        bad = tmp_path / "bad.toml"
        bad.write_text("total_cores = 0\ntotal_memory = '1GB'\n", encoding="utf-8")
        with pytest.raises(QuotaError, match="total_cores"):
            load_server_quota(bad)
        bad.write_text("total_cores = 4\ntotal_memory = 'nope'\n", encoding="utf-8")
        with pytest.raises(QuotaError, match="total_memory"):
            load_server_quota(bad)


class TestQuotaWaiting:

    def test_oversized_request_fails_immediately(self, tmp_path: Path) -> None:
        quota = _quota(tmp_path, cores=48, memory=48 * _GIB)
        started = time.monotonic()
        with pytest.raises(QuotaExceeded, match="exceeds server capacity"):
            quota.acquire(49, _GIB, "run", "item")
        with pytest.raises(QuotaExceeded, match="exceeds server capacity"):
            quota.acquire(1, 49 * _GIB, "run", "item")
        with pytest.raises(QuotaError, match="invalid quota request"):
            quota.acquire(0, _GIB, "run", "item")
        assert time.monotonic() - started < 2.0

    def test_waiting_cancel_takes_effect_promptly(self, tmp_path: Path) -> None:
        quota = _quota(tmp_path, cores=1, memory=_GIB)
        quota.acquire(1, _GIB, "run", "holder")
        flag = {"cancel": False}
        box = _acquire_in_background(quota, 1, _GIB, should_cancel=lambda: flag["cancel"])
        time.sleep(0.2)
        flag["cancel"] = True
        box["thread"].join(timeout=5.0)
        assert box.get("done") is True
        assert isinstance(box.get("error"), QuotaCancelled)

    def test_quota_limits_concurrent_groups(self, tmp_path: Path) -> None:
        quota = _quota(tmp_path, cores=96, memory=192 * _GIB)
        running = {"current": 0, "peak": 0}
        guard = threading.Lock()

        def _one() -> None:
            lease = quota.acquire(48, 96 * _GIB, "run", "item")
            child = _spawn_session_sleep("1")
            try:
                quota.mark_running(lease, *_group_identity(child))
                with guard:
                    running["current"] += 1
                    running["peak"] = max(running["peak"], running["current"])
                time.sleep(0.3)
            finally:
                with guard:
                    running["current"] -= 1
                quota.release(lease)
                child.kill()
                child.wait()

        threads = [threading.Thread(target=_one) for _ in range(4)]
        _start_and_join(threads, timeout=15.0)
        assert running["peak"] == 2


class TestQuotaReclaim:

    def test_killed_owner_holds_lease_while_group_lives(self, tmp_path: Path) -> None:
        state_dir = tmp_path / "quota-state"
        quota = ServerQuota(48, 192 * _GIB, state_dir)
        compute = _spawn_session_sleep("30")
        try:
            ready = multiprocessing.Event()
            victim = multiprocessing.Process(
                target=_hold_quota_then_wait,
                args=(str(state_dir), _group_identity(compute), ready),
                daemon=True,
            )
            victim.start()
            try:
                assert ready.wait(timeout=5.0)
                assert victim.pid is not None
                os.kill(victim.pid, signal.SIGKILL)
                victim.join(timeout=5.0)
                box = _acquire_in_background(quota, 48, 192 * _GIB)
                time.sleep(0.5)
                assert box.get("done") is not True
                os.killpg(os.getpgid(compute.pid), signal.SIGKILL)  # type: ignore[arg-type]
                compute.wait(timeout=5.0)
                box["thread"].join(timeout=5.0)
                assert isinstance(box.get("lease"), str)
                quota.release(str(box["lease"]))
            finally:
                if victim.is_alive():
                    victim.terminate()
                    victim.join(timeout=5.0)
        finally:
            compute.kill()
            compute.wait()

    def test_reserved_lease_reclaimed_after_owner_gone(self, tmp_path: Path) -> None:
        quota = _quota(tmp_path, cores=1, memory=_GIB)
        holder = quota.acquire(1, _GIB, "run", "holder")
        owner = subprocess.Popen(["/bin/true"])
        owner.wait()
        state_file = tmp_path / "quota-state" / "server-quota.json"
        payload = json.loads(state_file.read_text(encoding="utf-8"))
        payload["leases"][holder].update(
            {"owner_pid": owner.pid, "reserved_at": time.time() - 30.0}
        )
        state_file.write_text(json.dumps(payload), encoding="utf-8")
        assert quota.acquire(1, _GIB, "run", "next") is not None

    def test_pid_reuse_suspected_by_start_time(self, tmp_path: Path) -> None:
        quota = _quota(tmp_path, cores=2, memory=2 * _GIB)
        child = _spawn_session_sleep("30")
        try:
            pgid, pid, start = _group_identity(child)
            forged = quota.acquire(2, 2 * _GIB, "run", "forged")
            quota.mark_running(forged, pgid, pid, start + 3600.0)
            assert quota.acquire(2, 2 * _GIB, "run", "next") is not None
        finally:
            child.kill()
            child.wait()


class TestQuotaRelease:

    def test_concurrent_releases_grant_single_effect(self, tmp_path: Path) -> None:
        quota = _quota(tmp_path)
        lease = quota.acquire(1, _GIB, "run", "item")
        outcomes: list[bool] = []
        guard = threading.Lock()

        def _release_once() -> None:
            outcome = quota.release(lease)
            with guard:
                outcomes.append(outcome)

        threads = [threading.Thread(target=_release_once) for _ in range(8)]
        _start_and_join(threads, timeout=10.0)
        assert sorted(outcomes) == [False] * 7 + [True]
        quota.mark_running(lease, None, None, None)
        assert quota.acquire(4, 8 * _GIB, "run", "next") is not None

    def test_launch_releases_quota_on_every_path(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _active_config(monkeypatch, tmp_path, cores=2)
        state_dir = tmp_path / "quota-state"
        supervisor = NativeProcessSupervisor()
        executor = WorkItemExecutor()
        env = FrozenDict({"PATH": os.environ.get("PATH", "")})

        def _request(argv: tuple[str, ...], work_dir: Path) -> NativeExecutionRequest:
            work_dir.mkdir(parents=True, exist_ok=True)
            return NativeExecutionRequest(
                executable=argv[0], argv=argv, work_dir=str(work_dir), env=env
            )

        resources = ResourceRequest(cores_per_item=1, memory_per_item_bytes=_GIB)
        state_file = state_dir / "server-quota.json"
        outcome = executor._launch_and_wait(
            supervisor,
            _request(("/bin/true",), tmp_path / "w-ok"),
            0.01,
            quota=(resources, "run", "item-ok"),
            should_cancel=None,
        )
        assert outcome is not None and outcome[0].exit_code == 0
        assert json.loads(state_file.read_text(encoding="utf-8"))["leases"] == {}
        flag = {"cancel": False}
        timer = threading.Timer(0.3, lambda: flag.__setitem__("cancel", True))
        timer.start()
        outcome = executor._launch_and_wait(
            supervisor,
            _request(("/bin/sleep", "30"), tmp_path / "w-cancel"),
            0.01,
            quota=(resources, "run", "item-cancel"),
            should_cancel=lambda: flag["cancel"],
        )
        timer.cancel()
        assert outcome is not None and outcome[1] is not None and outcome[1].confirmed
        assert json.loads(state_file.read_text(encoding="utf-8"))["leases"] == {}

    def test_launch_without_config_keeps_legacy_behavior(self, tmp_path: Path) -> None:
        class _LegacySupervisor:
            def submit(self, request: NativeExecutionRequest) -> NativeHandle:
                return NativeHandle(key="legacy:1", pid=None)

            def poll(self, handle: NativeHandle) -> NativeStatus:
                return NativeStatus(is_terminal=True, exit_code=0)

            def collect(self, handle: NativeHandle) -> NativeExecutionResult:
                return NativeExecutionResult(exit_code=0, wall_time_seconds=0.0)

        work_dir = tmp_path / "w-legacy"
        work_dir.mkdir(parents=True, exist_ok=True)
        env = FrozenDict({"PATH": os.environ.get("PATH", "")})
        request = NativeExecutionRequest(
            executable="/bin/true", argv=("/bin/true",), work_dir=str(work_dir), env=env
        )
        outcome = WorkItemExecutor()._launch_and_wait(
            _LegacySupervisor(), request, 0.01, should_cancel=None
        )
        assert outcome is not None and outcome[0].exit_code == 0


class _StubAdapter:

    default_executable = "/bin/true"

    def materialize_native_input(self, inputs: object) -> MaterializedNativeInput:
        return MaterializedNativeInput(
            program=ProgramName.GAUSSIAN, main_input_name="job.inp", files=()
        )

    def build_execution_request(
        self,
        materialized: MaterializedNativeInput,
        *,
        executable: str,
        work_dir: str,
        env: dict[str, str],
        walltime_seconds: float | None,
    ) -> NativeExecutionRequest:
        return NativeExecutionRequest(
            executable=executable,
            argv=(executable,),
            work_dir=work_dir,
            env=FrozenDict(env),
            walltime_seconds=walltime_seconds,
            stdout_file="out.log",
            stderr_file="err.log",
        )


def _executor_item(cores: int, key: str = "quota-item") -> WorkItem:
    record = structure(f"s-{key}", charge=0, multiplicity=1)
    named = WorkItemInputs(structures=FrozenDict({"structure": StructureSet.of(record)}))
    digest = "sha256:" + hashlib.sha256(key.encode("utf-8")).hexdigest()
    return WorkItem(
        id=make_work_item_id(key),
        logical_key=key,
        step_id="step-quota",
        named_inputs=named,
        resources=ResourceRequest(cores_per_item=cores, memory_per_item_bytes=_GIB),
        semantic_digest=digest,
        ordinal=0,
    )


def _executor_context(supervisor: object, run_root: Path) -> ItemExecutionContext:
    return ItemExecutionContext(
        step_id="step-quota",
        scientific=ScientificDefinition(),
        scientific_defaults=ScientificDefaults(),
        adapter=_StubAdapter(),  # type: ignore[arg-type]
        profile=None,  # type: ignore[arg-type]
        supervisor=supervisor,  # type: ignore[arg-type]
        run_root=str(run_root),
        poll_interval_seconds=0.01,
    )


class TestExecutorQuota:

    def test_oversized_request_fails_with_quota_error(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _active_config(monkeypatch, tmp_path, cores=96)
        supervisor = NativeProcessSupervisor()
        item = _executor_item(cores=10**9, key="quota-huge")
        result = WorkItemExecutor().execute(item, _executor_context(supervisor, tmp_path))
        assert result.error is not None
        assert result.error.code == "quota_exceeded"

    def test_quota_wait_cancel_reports_cancelled(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        manager = _active_config(monkeypatch, tmp_path, cores=1)
        holder = manager.acquire(1, _GIB, "run", "holder")
        try:
            supervisor = NativeProcessSupervisor()
            item = _executor_item(cores=1, key="quota-waiter")
            context = _executor_context(supervisor, tmp_path)
            result = WorkItemExecutor().execute(item, context, should_cancel=lambda: True)
            assert result.status.value == "cancelled"
        finally:
            manager.release(holder)
