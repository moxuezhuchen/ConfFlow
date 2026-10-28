#!/usr/bin/env python3
"""Architecture follow-up F2: script identity is re-checked after the quota lease, before submit.

A script edited while its item waits for server quota must not run under the digest
recorded before the wait. The final check runs in ``before_submit`` once the lease is
held; a failure there releases the lease and fails the item without submitting.
"""

from __future__ import annotations

import hashlib
import json
import sys
import threading
from pathlib import Path
from typing import Any

import pytest

from confflow.domain import FrozenDict, StructureSet
from confflow.domain._immutable import freeze_value
from confflow.domain.completion import WorkItemStatus
from confflow.domain.resources import ResourceRequest
from confflow.domain.work_item import WorkItem, WorkItemInputs, make_work_item_id
from confflow.execution import script_executor as script_executor_module
from confflow.execution import work_item_executor as work_item_module
from confflow.execution.native import NativeExecutionRequest
from confflow.execution.process import NativeProcessSupervisor
from confflow.execution.quota import QuotaError, ServerQuota
from confflow.execution.script_executor import ScriptExecutor
from confflow.execution.script_registry import load_script_registry, script_identity
from confflow.execution.work_item_executor import ItemExecutionContext, WorkItemExecutor
from confflow.workflow.v4.document import ScientificDefaults, ScientificDefinition
from tests.v4._builders import structure

_GIB = 1024**3
_WAIT = 30.0

_ECHO_SCRIPT = """\
import sys
from pathlib import Path
Path("final.xyz").write_text("1\\nscript\\nHe 0 0 " + sys.argv[1] + "\\n")
"""

_OTHER_SCRIPT = """\
from pathlib import Path
Path("final.xyz").write_text("1\\nscript\\nHe 0 0 9\\n")
"""


@pytest.fixture(autouse=True)
def _isolated_server_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CONFFLOW_SERVER_CONFIG", str(tmp_path / "server.toml"))
    monkeypatch.setenv("CONFFLOW_SERVER_STATE_DIR", str(tmp_path / "quota-state"))


@pytest.fixture
def one_core(tmp_path: Path) -> tuple[Path, Path, Path]:
    """Return (script, server.toml, run_root) for a single-core server with the demo script."""
    script = tmp_path / "demo.py"
    script.write_text(_ECHO_SCRIPT, encoding="utf-8")
    config = tmp_path / "server.toml"
    config.write_text(
        "total_cores = 1\n"
        "total_memory = '192GB'\n\n"
        "[scripts.demo]\n"
        f"command = {json.dumps([sys.executable, str(script), '1'])}\n",
        encoding="utf-8",
    )
    run_root = tmp_path / "run"
    run_root.mkdir()
    return script, config, run_root


def _item(key: str = "queued-item") -> WorkItem:
    record = structure(f"s-{key}", charge=0, multiplicity=1)
    named = WorkItemInputs(structures=FrozenDict({"structure": StructureSet.of(record)}))
    digest = "sha256:" + hashlib.sha256(key.encode("utf-8")).hexdigest()
    return WorkItem(
        id=make_work_item_id(key),
        logical_key=key,
        step_id="s",
        named_inputs=named,
        resources=ResourceRequest(cores_per_item=1, memory_per_item_bytes=_GIB),
        semantic_digest=digest,
        ordinal=0,
    )


def _context(run_root: Path, expected: FrozenDict | None) -> ItemExecutionContext:
    return ItemExecutionContext(
        step_id="s",
        scientific=ScientificDefinition(
            script_id="demo",
            script_args=("1",),
            script_outputs=FrozenDict({"structures": "final.xyz"}),
        ),
        scientific_defaults=ScientificDefaults(),
        adapter=None,  # type: ignore[arg-type]  # script items never consult the adapter
        profile=None,  # type: ignore[arg-type]
        supervisor=NativeProcessSupervisor(),
        run_root=str(run_root),
        poll_interval_seconds=0.01,
        expected_script_identity=expected,
    )


def _run_in_thread(
    executor: ScriptExecutor, run_root: Path, expected: FrozenDict | None
) -> tuple[threading.Thread, dict[str, Any]]:
    box: dict[str, Any] = {}

    def _run() -> None:
        box["result"] = executor.execute(_item(), _context(run_root, expected))

    thread = threading.Thread(target=_run, daemon=True)
    thread.start()
    return thread, box


def _observe_acquire(monkeypatch: pytest.MonkeyPatch) -> threading.Event:
    """Signal when a quota acquire is entered; the real acquire still runs."""
    entered = threading.Event()
    real_acquire = ServerQuota.acquire

    def _acquire(self: ServerQuota, *args: Any, **kwargs: Any) -> str:
        entered.set()
        return real_acquire(self, *args, **kwargs)

    monkeypatch.setattr(ServerQuota, "acquire", _acquire)
    return entered


def _leases(state_dir: Path) -> dict:
    ledger = state_dir / "server-quota.json"
    return json.loads(ledger.read_text(encoding="utf-8")).get("leases", {})


class TestQueuedScriptRecheck:
    def test_script_edited_while_queued_is_not_published(
        self, one_core: tuple[Path, Path, Path], monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        script, _, run_root = one_core
        quota = ServerQuota(1, 192 * _GIB, tmp_path / "quota-state")
        holder = quota.acquire(1, _GIB, "holder-run", "holder-item")
        entered = _observe_acquire(monkeypatch)
        thread, box = _run_in_thread(ScriptExecutor(), run_root, None)
        assert entered.wait(_WAIT), "script step never reached quota acquire"
        script.write_text(_OTHER_SCRIPT, encoding="utf-8")
        assert quota.release(holder)
        thread.join(_WAIT)
        assert not thread.is_alive()
        result = box["result"]
        assert result.status is not WorkItemStatus.COMPLETED
        assert result.error is not None and result.error.code == "script_failed"
        assert "changed between registry load and launch" in result.diagnostics[0].message
        assert not result.structures
        assert not list(run_root.rglob("final.xyz"))
        assert _leases(tmp_path / "quota-state") == {}

    def test_unchanged_script_while_queued_completes(
        self, one_core: tuple[Path, Path, Path], monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        _, _, run_root = one_core
        quota = ServerQuota(1, 192 * _GIB, tmp_path / "quota-state")
        holder = quota.acquire(1, _GIB, "holder-run", "holder-item")
        entered = _observe_acquire(monkeypatch)
        thread, box = _run_in_thread(ScriptExecutor(), run_root, None)
        assert entered.wait(_WAIT), "script step never reached quota acquire"
        assert quota.release(holder)
        thread.join(_WAIT)
        assert not thread.is_alive()
        result = box["result"]
        assert result.status is WorkItemStatus.COMPLETED, [d.message for d in result.diagnostics]
        assert result.structures[0].coordinates[0][2] == pytest.approx(1.0)
        assert _leases(tmp_path / "quota-state") == {}

    def test_registration_changed_while_queued_is_refused(
        self, one_core: tuple[Path, Path, Path], monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        _, config, run_root = one_core
        expected = freeze_value(script_identity(load_script_registry()["demo"]))
        quota = ServerQuota(1, 192 * _GIB, tmp_path / "quota-state")
        holder = quota.acquire(1, _GIB, "holder-run", "holder-item")
        entered = _observe_acquire(monkeypatch)
        thread, box = _run_in_thread(ScriptExecutor(), run_root, expected)
        assert entered.wait(_WAIT), "script step never reached quota acquire"
        config.write_text(config.read_text().replace('"1"]', '"2"]'), encoding="utf-8")
        assert quota.release(holder)
        thread.join(_WAIT)
        assert not thread.is_alive()
        result = box["result"]
        assert result.status is not WorkItemStatus.COMPLETED
        assert "changed after the workflow was compiled" in result.diagnostics[0].message
        assert not result.structures
        assert _leases(tmp_path / "quota-state") == {}

    def test_registry_unreadable_while_queued_is_refused(
        self, one_core: tuple[Path, Path, Path], monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        _, _, run_root = one_core
        expected = freeze_value(script_identity(load_script_registry()["demo"]))
        quota = ServerQuota(1, 192 * _GIB, tmp_path / "quota-state")
        holder = quota.acquire(1, _GIB, "holder-run", "holder-item")
        entered = _observe_acquire(monkeypatch)
        real_load = script_executor_module.load_script_registry
        calls: list[int] = []

        def _load_once_then_fail() -> dict[str, Any]:
            calls.append(1)
            if len(calls) > 1:
                raise QuotaError("server config unreadable")
            return real_load()

        monkeypatch.setattr(script_executor_module, "load_script_registry", _load_once_then_fail)
        thread, box = _run_in_thread(ScriptExecutor(), run_root, expected)
        assert entered.wait(_WAIT), "script step never reached quota acquire"
        assert quota.release(holder)
        thread.join(_WAIT)
        assert not thread.is_alive()
        result = box["result"]
        assert result.status is not WorkItemStatus.COMPLETED
        assert result.error is not None and result.error.code == "script_failed"
        assert "server config unreadable" in result.diagnostics[0].message
        assert _leases(tmp_path / "quota-state") == {}


class _FakeQuota:
    def __init__(self, events: list[str]) -> None:
        self.events = events
        self.released: list[str] = []

    def acquire(self, cores: Any, memory: Any, run_id: str, item_id: str, cancel: Any) -> str:
        self.events.append("acquire")
        return "lease-1"

    def release(self, lease: str) -> bool:
        self.released.append(lease)
        return True


class _FakeSupervisor:
    def __init__(self, events: list[str]) -> None:
        self.events = events
        self.submitted = 0

    def submit(self, request: Any) -> Any:
        self.events.append("submit")
        self.submitted += 1
        raise RuntimeError("stop after submit")


def _request(tmp_path: Path) -> NativeExecutionRequest:
    return NativeExecutionRequest(
        executable="/bin/true",
        argv=("/bin/true",),
        work_dir=str(tmp_path),
        env=FrozenDict({"PATH": "/usr/bin:/bin"}),
    )


class TestBeforeSubmitHook:
    def test_raise_releases_lease_and_skips_submit(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        events: list[str] = []
        quota = _FakeQuota(events)
        monkeypatch.setattr(work_item_module, "load_server_quota", lambda: quota)
        supervisor = _FakeSupervisor(events)
        boom = ValueError("identity changed")

        def _refuse() -> None:
            events.append("before")
            raise boom

        with pytest.raises(ValueError) as caught:
            WorkItemExecutor().launch_and_wait(
                supervisor,  # type: ignore[arg-type]
                _request(tmp_path),
                0.01,
                should_cancel=None,
                quota=(None, "run", "item"),
                before_submit=_refuse,
            )
        assert caught.value is boom
        assert supervisor.submitted == 0
        assert events == ["acquire", "before"]
        assert quota.released == ["lease-1"]

    def test_runs_after_acquire_and_before_submit(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        events: list[str] = []
        quota = _FakeQuota(events)
        monkeypatch.setattr(work_item_module, "load_server_quota", lambda: quota)
        supervisor = _FakeSupervisor(events)
        result = WorkItemExecutor().launch_and_wait(
            supervisor,  # type: ignore[arg-type]
            _request(tmp_path),
            0.01,
            should_cancel=None,
            quota=(None, "run", "item"),
            before_submit=lambda: events.append("before"),
        )
        assert result is None  # the fake submit fails, which releases the lease
        assert events == ["acquire", "before", "submit"]
        assert quota.released == ["lease-1"]

    def test_without_quota_manager_called_once_before_submit(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        events: list[str] = []
        monkeypatch.setattr(work_item_module, "load_server_quota", lambda: None)
        supervisor = _FakeSupervisor(events)
        calls: list[str] = []
        result = WorkItemExecutor().launch_and_wait(
            supervisor,  # type: ignore[arg-type]
            _request(tmp_path),
            0.01,
            should_cancel=None,
            quota=None,
            before_submit=lambda: calls.append("before"),
        )
        assert result is None
        assert calls == ["before"]
        assert events == ["submit"]
        assert supervisor.submitted == 1

    def test_absent_hook_keeps_direct_submit(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        events: list[str] = []
        monkeypatch.setattr(work_item_module, "load_server_quota", lambda: None)
        supervisor = _FakeSupervisor(events)
        assert (
            WorkItemExecutor().launch_and_wait(
                supervisor,  # type: ignore[arg-type]
                _request(tmp_path),
                0.01,
                should_cancel=None,
                quota=None,
            )
            is None
        )
        assert events == ["submit"]
