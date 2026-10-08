#!/usr/bin/env python3
"""E-round changed-coverage supplements (behavioral, no science change)."""

from __future__ import annotations

import json
import threading
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import pytest


@pytest.fixture(autouse=True)
def _isolated_server_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("CONFFLOW_SERVER_CONFIG", str(tmp_path / "server.toml"))
    monkeypatch.setenv("CONFFLOW_SERVER_STATE_DIR", str(tmp_path / "quota-state"))
    return tmp_path


# --- cli.py:375 (_process_owner_uid tuple/list) -------------------------------


def test_process_owner_uid_accepts_tuple_and_list() -> None:
    from confflow.cli import _process_owner_uid

    assert _process_owner_uid((1234, 1234, 1234)) == 1234
    assert _process_owner_uid([5678, 5678]) == 5678
    assert _process_owner_uid(None) is None
    assert _process_owner_uid(object()) is None


def test_cli_verbose_logs_on_non_v4_document(tmp_path: Path) -> None:
    from confflow.cli import main

    xyz = tmp_path / "input.xyz"
    xyz.write_text("1\nframe\nHe 0 0 0\n", encoding="utf-8")
    cfg = tmp_path / "confflow.yaml"
    cfg.write_text("schema: confflow.workflow.v1\nsteps: []\n", encoding="utf-8")
    rc = main([str(xyz), "-c", str(cfg), "-w", str(tmp_path / "work"), "--verbose"])
    assert int(rc) != 0


# --- quota.py:247-248 (unreadable ledger), 255 (top-level list) ----------------


def test_quota_unreadable_ledger_fails_closed(tmp_path: Path) -> None:
    from confflow.execution.quota import QuotaError, ServerQuota

    state = tmp_path / "state"
    state.mkdir(parents=True, exist_ok=True)
    ledger = state / "server-quota.json"
    ledger.mkdir(parents=True, exist_ok=True)  # read_text -> IsADirectoryError (OSError)
    quota = ServerQuota(4, 8 * 1024**3, state)
    with pytest.raises(QuotaError, match="cannot read quota ledger"):
        quota.acquire(1, 1024**3, "run", "item")


def test_quota_top_level_list_fails_closed(tmp_path: Path) -> None:
    from confflow.execution.quota import QuotaError, ServerQuota

    state = tmp_path / "state"
    state.mkdir(parents=True, exist_ok=True)
    (state / "server-quota.json").write_text("[]", encoding="utf-8")
    quota = ServerQuota(4, 8 * 1024**3, state)
    with pytest.raises(QuotaError, match="top level must be an object"):
        quota.acquire(1, 1024**3, "run", "item")


# --- xyz_import.py ------------------------------------------------------------


def test_xyz_empty_blocks_guard(monkeypatch: pytest.MonkeyPatch) -> None:
    import confflow.execution.xyz_import as xi
    from confflow.domain.errors import DomainError

    monkeypatch.setattr(xi, "_split_xyz_blocks", lambda text: [])
    with pytest.raises(DomainError, match="carries no molecule blocks"):
        xi.import_xyz("1\nframe\nHe 0 0 0\n")


def test_xyz_entity_id_validation() -> None:
    from confflow.domain.errors import DomainError
    from confflow.execution.xyz_import import import_xyz

    good = "1\nframe\nHe 0 0 0\n"
    with pytest.raises(DomainError, match="explicit entity ids were supplied"):
        import_xyz(good, entity_ids=("a", "b"))
    with pytest.raises(DomainError, match="must be a non-empty string"):
        import_xyz(good, entity_ids=("",))
    with pytest.raises(DomainError, match="duplicate entity id"):
        import_xyz("1\nf1\nHe 0 0 0\n1\nf2\nHe 0 0 1\n", entity_ids=("x", "x"))


def test_xyz_malformed_and_nonnumeric_rows() -> None:
    from confflow.domain.errors import DomainError
    from confflow.execution.xyz_import import import_xyz

    with pytest.raises(DomainError, match="malformed row"):
        import_xyz("1\nframe\nHe 0\n")
    with pytest.raises(DomainError, match="non-numeric coordinates"):
        import_xyz("1\nframe\nHe a b c\n")


def test_xyz_count_mismatch_guard(monkeypatch: pytest.MonkeyPatch) -> None:
    import confflow.execution.xyz_import as xi
    from confflow.domain.errors import DomainError

    monkeypatch.setattr(xi, "_split_xyz_blocks", lambda text: [(2, ["He 0 0 0"])])
    with pytest.raises(DomainError, match="declares 2 atoms but carries 1"):
        xi.import_xyz("anything")


def test_xyz_split_edge_branches() -> None:
    from confflow.domain.errors import DomainError
    from confflow.execution.xyz_import import import_xyz

    # Trailing blank lines exercise the `break` after skipping blanks.
    result = import_xyz("1\nframe\nHe 0 0 0\n\n\n")
    assert len(result) == 1
    with pytest.raises(DomainError, match="has no atoms"):
        import_xyz("0\nframe\n")


# --- script_executor.py:268-270 (unconfirmed cancel + timed out) --------------


def _script_helpers():
    import hashlib

    from confflow.domain import FrozenDict, StructureSet
    from confflow.domain.resources import ResourceRequest
    from confflow.domain.work_item import WorkItem, WorkItemInputs, make_work_item_id
    from confflow.execution.work_item_executor import ItemExecutionContext
    from confflow.workflow.v4.document import ScientificDefaults, ScientificDefinition
    from tests.v4._builders import structure

    return (
        hashlib,
        FrozenDict,
        StructureSet,
        ResourceRequest,
        WorkItem,
        WorkItemInputs,
        (make_work_item_id,),
        ItemExecutionContext,
        ScientificDefaults,
        ScientificDefinition,
        structure,
    )


def test_script_executor_unconfirmed_cancel_with_timeout(tmp_path: Path) -> None:
    import hashlib
    import sys as _sys

    from confflow.domain import FrozenDict, StructureSet
    from confflow.domain.resources import ResourceRequest
    from confflow.domain.work_item import WorkItem, WorkItemInputs, make_work_item_id
    from confflow.execution.native import CancelOutcome, NativeExecutionResult
    from confflow.execution.process import NativeProcessSupervisor
    from confflow.execution.script_executor import ScriptExecutor
    from confflow.execution.work_item_executor import ItemExecutionContext
    from confflow.workflow.v4.document import ScientificDefaults, ScientificDefinition
    from tests.v4._builders import structure

    _GIB = 1024**3
    script = tmp_path / "demo.py"
    script.write_text("open('final.xyz','w').write('1\\nframe\\nHe 0 0 0\\n')\n")
    # Write a valid TOML manually to avoid quoting pitfalls.
    toml = (
        "total_cores = 96\ntotal_memory = '192GB'\n\n[scripts.demo]\n"
        f"command = [{json.dumps(_sys.executable)}, {json.dumps(str(script))}]\n"
    )
    (tmp_path / "server.toml").write_text(toml, encoding="utf-8")
    run_root = tmp_path / "run"
    run_root.mkdir()

    record = structure("s-script-timeout", charge=0, multiplicity=1)
    item = WorkItem(
        id=make_work_item_id("script-timeout"),
        logical_key="script-timeout",
        step_id="s1",
        named_inputs=WorkItemInputs(structures=FrozenDict({"structure": StructureSet.of(record)})),
        resources=ResourceRequest(cores_per_item=1, memory_per_item_bytes=_GIB),
        semantic_digest="sha256:" + hashlib.sha256(b"script-timeout").hexdigest(),
        ordinal=0,
    )
    context = ItemExecutionContext(
        step_id="s1",
        scientific=ScientificDefinition(
            script_id="demo",
            script_args=("{input}",),
            script_outputs=FrozenDict({"structures": "final.xyz"}),
        ),
        scientific_defaults=ScientificDefaults(),
        adapter=None,
        profile=None,
        supervisor=NativeProcessSupervisor(),
        run_root=str(run_root),
        poll_interval_seconds=0.01,
    )
    executor = ScriptExecutor()
    fake_result = NativeExecutionResult(
        exit_code=None,
        wall_time_seconds=1.0,
        timed_out=True,
        stdout_file="stdout.log",
        stderr_file="stderr.log",
    )
    fake_cancel = CancelOutcome(confirmed=False, detail="still live")
    executor._launcher.launch_and_wait = (  # type: ignore[method-assign]
        lambda *a, **k: (fake_result, fake_cancel)
    )
    result = executor.execute(item, context)
    assert result.status.value == "failed"
    assert result.error is not None
    assert result.error.code == "cancellation_error"
    details = dict(result.diagnostics[0].details)
    assert details.get("timed_out") is True


# --- work_item_executor.py:682 + 1085-1086 ------------------------------------


def test_work_item_unconfirmed_cancel_carries_walltime(tmp_path: Path) -> None:
    import hashlib

    from confflow.domain import FrozenDict, StructureSet
    from confflow.domain.resources import ResourceRequest
    from confflow.domain.work_item import WorkItem, WorkItemInputs, make_work_item_id
    from confflow.execution.contracts import ExecutionBinding
    from confflow.execution.native import (
        MaterializedNativeInput,
        NativeHandle,
        NativeStatus,
        ProgramName,
    )
    from confflow.execution.work_item_executor import ItemExecutionContext, WorkItemExecutor
    from confflow.workflow.v4.document import ScientificDefaults, ScientificDefinition
    from tests.v4._builders import structure

    _GIB = 1024**3

    class _StubAdapter:
        default_executable = "/bin/true"

        def materialize_native_input(self, inputs: object) -> MaterializedNativeInput:
            return MaterializedNativeInput(
                program=ProgramName.GAUSSIAN, main_input_name="job.inp", files=()
            )

        def build_execution_request(  # type: ignore[no-untyped-def]
            self, materialized, *, executable, work_dir, env, walltime_seconds
        ):
            from confflow.execution.native import NativeExecutionRequest

            return NativeExecutionRequest(
                executable=executable,
                argv=(executable,),
                work_dir=work_dir,
                env=FrozenDict(env),
                walltime_seconds=walltime_seconds,
                stdout_file="out.log",
                stderr_file="err.log",
            )

    class _Inner:
        def __getattr__(self, name: str) -> Any:
            return getattr(_StubAdapter(), name)

        def build_execution_request(  # type: ignore[no-untyped-def]
            self, materialized, *, executable, work_dir, env, walltime_seconds
        ):
            from confflow.execution.native import NativeExecutionRequest

            return NativeExecutionRequest(
                executable=executable,
                argv=(executable,),
                work_dir=work_dir,
                env=FrozenDict(env),
                walltime_seconds=0.05,
                stdout_file="out.log",
                stderr_file="err.log",
            )

    class _NeverUnconfirmed:
        def submit(self, request: Any) -> NativeHandle:
            return NativeHandle(key="unconfirmed:walltime", pid=None)

        def poll(self, handle: NativeHandle) -> NativeStatus:
            return NativeStatus(is_terminal=False, exit_code=None)

        def cancel(self, handle: NativeHandle, **kwargs: Any) -> Any:
            from confflow.execution.native import CancelOutcome

            return CancelOutcome(confirmed=False, detail="boundary still live")

        def collect(self, handle: NativeHandle) -> Any:
            raise AssertionError("never terminal")

    record = structure("s-wie-walltime", charge=0, multiplicity=1)
    item = WorkItem(
        id=make_work_item_id("wie-walltime"),
        logical_key="wie-walltime",
        step_id="s1",
        named_inputs=WorkItemInputs(structures=FrozenDict({"structure": StructureSet.of(record)})),
        resources=ResourceRequest(cores_per_item=1, memory_per_item_bytes=_GIB),
        semantic_digest="sha256:" + hashlib.sha256(b"wie-walltime").hexdigest(),
        ordinal=0,
    )
    context = ItemExecutionContext(
        step_id="s1",
        scientific=ScientificDefinition(),
        scientific_defaults=ScientificDefaults(),
        adapter=_Inner(),  # type: ignore[arg-type]
        profile=None,  # type: ignore[arg-type]
        supervisor=_NeverUnconfirmed(),  # type: ignore[arg-type]
        run_root=str(tmp_path),
        poll_interval_seconds=0.01,
        execution_binding=ExecutionBinding(binding_id="b", walltime_seconds=60),
    )
    result = WorkItemExecutor().execute(item, context)
    assert result.error is not None
    assert result.error.code == "cancellation_error"
    details = dict(result.error.details)
    assert details.get("timed_out") is True
    assert details.get("walltime_seconds") == 60.0


def test_work_item_timeout_cancel_raise_is_unconfirmed(tmp_path: Path) -> None:
    from confflow.execution.native import NativeExecutionRequest, NativeHandle
    from confflow.execution.work_item_executor import WorkItemExecutor

    request = NativeExecutionRequest(
        executable="/bin/true",
        argv=("/bin/true",),
        work_dir=str(tmp_path),
        walltime_seconds=0.05,
    )

    class _TimeoutSupervisor:
        def submit(self, req: Any) -> NativeHandle:
            return NativeHandle(key="k-timeout-raise", pid=None)

        def poll(self, handle: NativeHandle):  # type: ignore[no-untyped-def]
            from confflow.execution.native import NativeStatus

            return NativeStatus(is_terminal=False, exit_code=None)

        def cancel(self, handle: NativeHandle):  # type: ignore[no-untyped-def]
            raise RuntimeError("signal failed")

        def collect(self, handle: NativeHandle):  # type: ignore[no-untyped-def]
            raise AssertionError("no collect")

    launched = WorkItemExecutor()._launch_and_wait(
        _TimeoutSupervisor(),
        request,
        0.001,
        should_cancel=None,
        quota=None,
    )
    assert launched is not None
    execution_result, cancel_outcome = launched
    assert execution_result.timed_out is True
    assert cancel_outcome is not None and not cancel_outcome.confirmed
    assert "cancel request failed after timeout" in cancel_outcome.detail


# --- process.py tombstone races: 365, 431, 530-537 -----------------------------


def _tombstone_handle():
    from unittest.mock import MagicMock

    from confflow.execution.native import NativeHandle
    from confflow.execution.process import _CancelledTombstone, _ProcessRecord

    proc = MagicMock()
    proc.poll.return_value = None
    live = _ProcessRecord(
        proc=proc,
        stdout_stream=None,
        stderr_stream=None,
        stdout_name="o",
        stderr_name="e",
        work_dir="/tmp",
        pid=999999,
        process_group_id=None,
        session_id=None,
        create_time=None,
        lock=threading.RLock(),
    )
    tomb = _CancelledTombstone(
        exit_code=7,
        stdout_name="o",
        stderr_name="e",
        started_monotonic=0.0,
    )
    handle = NativeHandle(key="k-race", pid=None)
    return live, tomb, handle


def test_process_poll_sees_tombstone_on_recheck() -> None:
    from confflow.execution.process import NativeProcessSupervisor

    live, tomb, handle = _tombstone_handle()
    supervisor = NativeProcessSupervisor()
    supervisor._live_record_for = MagicMock(side_effect=[live, tomb])  # type: ignore[method-assign]
    status = supervisor.poll(handle)
    assert status.is_terminal is True
    assert status.exit_code == 7


def test_process_cancel_sees_tombstone_on_recheck() -> None:
    from confflow.execution.process import NativeProcessSupervisor

    live, tomb, handle = _tombstone_handle()
    supervisor = NativeProcessSupervisor()
    supervisor._live_record_for = MagicMock(side_effect=[live, tomb])  # type: ignore[method-assign]
    outcome = supervisor.cancel(handle)
    assert outcome.confirmed is True
    assert outcome.detail == "already cancelled"


def test_process_collect_sees_tombstone_on_recheck() -> None:
    from confflow.execution.process import NativeProcessSupervisor

    live, tomb, handle = _tombstone_handle()
    supervisor = NativeProcessSupervisor()
    supervisor._records["k-race"] = tomb
    supervisor._live_record_for = MagicMock(side_effect=[live, tomb])  # type: ignore[method-assign]
    result = supervisor.collect(handle)
    assert result.exit_code == 7
    assert "k-race" not in supervisor._records


# --- assembly.py:242-246 (artifact IDS selector) -------------------------------


def test_assembly_artifact_ids_selector() -> None:
    from types import SimpleNamespace

    from confflow.domain import ArtifactSet, ResultSet, StructureSet
    from confflow.domain.binding import PortKind, SelectorKind
    from confflow.workflow.v4.assembly import _apply_selector
    from tests.v4._builders import checkpoint

    artifacts = ArtifactSet.of(checkpoint("s1"), checkpoint("s2"))
    edge = SimpleNamespace(
        source=SimpleNamespace(
            selector=SimpleNamespace(kind=SelectorKind.IDS, ids=("chk_s1", "missing-chk"))
        ),
        target_port=SimpleNamespace(kind=PortKind.ARTIFACT),
    )
    structures, selected, results, missing = _apply_selector(
        StructureSet(),
        artifacts,
        ResultSet(),
        edge,
    )
    assert selected.ids == ("chk_s1",)
    assert "missing-chk" in missing


# --- engine.py:408 + 575-576 (registry without _ordered) -----------------------


def test_engine_inherited_states_falls_back_to_descriptors() -> None:
    from types import SimpleNamespace

    from confflow.science.confgen.engine import _collect_inherited_states
    from confflow.science.confgen.kernel_records import ComponentStateKey

    key = ComponentStateKey(components={"torsions": {"T1": 1.0}})
    scope = {"torsions": {"kind": "x"}}
    registry = SimpleNamespace(descriptors=[])  # no _ordered -> fallback
    assert _collect_inherited_states(key, scope, registry) == []


def test_engine_component_diagnostics_falls_back_to_descriptors() -> None:
    from types import SimpleNamespace

    from confflow.science.confgen.engine import _collect_component_diagnostics

    registry = SimpleNamespace(descriptors=[])  # no _ordered -> fallback
    assert _collect_component_diagnostics(registry, {}, SimpleNamespace()) == {}


# --- ring import-failure fallbacks --------------------------------------------


def test_ring_realization_tolerates_missing_tolerances(monkeypatch: pytest.MonkeyPatch) -> None:
    import sys as _sys

    import confflow.science.confgen.ring.realization as realizes
    from confflow.science.confgen.ring.realization import RingNumericalFailure

    monkeypatch.setitem(_sys.modules, "confflow.science.confgen.tolerances", None)
    with pytest.raises(RingNumericalFailure):
        realizes.realize_cp_target(
            [[0.0, 0.0, 0.0]],
            ["H"],
            None,
            SimpleNamespace(atoms=[]),
            None,
            rigid_units=(),
            tolerances=None,
        )


def test_ring_edge_kind_tolerates_missing_graph(monkeypatch: pytest.MonkeyPatch) -> None:
    import sys as _sys

    from confflow.science.confgen.ring.rigid_units import _edge_kind_is_covalent

    monkeypatch.setitem(_sys.modules, "confflow.science.confgen.graph", None)
    assert _edge_kind_is_covalent("covalent") is True
    assert _edge_kind_is_covalent(None) is None


# --- process.py:707 + 791 (census OSError tolerance) ---------------------------


def test_process_boundary_census_tolerates_os_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    import os as _os
    from unittest.mock import MagicMock

    import confflow.execution.process as proc_mod
    from confflow.execution.process import NativeProcessSupervisor, _ProcessRecord

    supervisor = NativeProcessSupervisor()
    record = _ProcessRecord(
        proc=MagicMock(),
        stdout_stream=None,
        stderr_stream=None,
        stdout_name="o",
        stderr_name="e",
        work_dir="/tmp",
        pid=None,
        process_group_id=999983,
        session_id=999979,
        create_time=None,
        known_processes={},
        lock=threading.RLock(),
    )

    class _FakeProc:
        pid = 424242
        info = {"status": "running"}

    assert proc_mod._psutil is not None
    monkeypatch.setattr(proc_mod._psutil, "process_iter", lambda *a, **k: [_FakeProc()])

    real_getpgid = _os.getpgid
    real_getsid = _os.getsid

    def _boom_getpgid(pid: int) -> int:
        raise OSError("no such process")

    def _boom_getsid(pid: int) -> int:
        raise OSError("no such process")

    monkeypatch.setattr(_os, "getpgid", _boom_getpgid)
    monkeypatch.setattr(_os, "getsid", _boom_getsid)
    try:
        assert supervisor._group_is_safe_to_signal(record) is False
    finally:
        monkeypatch.setattr(_os, "getpgid", real_getpgid)
        monkeypatch.setattr(_os, "getsid", real_getsid)


def test_process_live_boundary_tolerates_permission_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import os as _os
    from unittest.mock import MagicMock

    import confflow.execution.process as proc_mod
    from confflow.execution.process import NativeProcessSupervisor, _ProcessRecord

    supervisor = NativeProcessSupervisor()
    record = _ProcessRecord(
        proc=MagicMock(),
        stdout_stream=None,
        stderr_stream=None,
        stdout_name="o",
        stderr_name="e",
        work_dir="/tmp",
        pid=None,
        process_group_id=999983,
        session_id=999979,
        create_time=None,
        known_processes={},
        lock=threading.RLock(),
    )
    monkeypatch.setattr(supervisor, "_group_is_safe_to_signal", lambda _r: True)

    class _FakeProc:
        pid = 424243
        info = {"status": "running"}

    assert proc_mod._psutil is not None
    monkeypatch.setattr(proc_mod._psutil, "process_iter", lambda *a, **k: [_FakeProc()])

    def _denied_getpgid(pid: int) -> int:
        raise PermissionError("denied")

    monkeypatch.setattr(_os, "getpgid", _denied_getpgid)
    live = supervisor._live_boundary_processes(record, root_reaped=True)
    assert -3 in live


# --- engine.py:452 (descriptors iteration TypeError) ---------------------------


def test_engine_report_entries_tolerates_bad_descriptors() -> None:
    from types import SimpleNamespace

    from confflow.science.confgen.engine import _inherited_report_entries

    context = SimpleNamespace(resolved_spec={}, registry=SimpleNamespace(descriptors=123))
    assert _inherited_report_entries([], SimpleNamespace(), context) == []


# --- engine.py:461 (carries property TypeError) --------------------------------


def test_engine_report_entries_tolerates_exploding_carries() -> None:
    from types import SimpleNamespace

    from confflow.science.confgen.engine import _inherited_report_entries
    from confflow.science.confgen.kernel_records import ComponentStateKey

    class _Evil:
        id = "c"

        @property
        def carries_inherited_locks(self) -> bool:
            raise TypeError("boom")

    key = ComponentStateKey(components={"c": {"ax": 1.0}})
    states = [SimpleNamespace(component_id="c", payload={"ax": {"model": "x"}})]
    context = SimpleNamespace(resolved_spec={}, registry=SimpleNamespace(descriptors=[_Evil()]))
    assert _inherited_report_entries(states, key, context) == []


# --- engine.py:476 (resolved non-iterable) --------------------------------------


def test_engine_report_entries_tolerates_bad_resolved() -> None:
    from types import SimpleNamespace

    from confflow.science.confgen.engine import _inherited_report_entries
    from confflow.science.confgen.kernel_records import ComponentStateKey

    descriptor = SimpleNamespace(id="c", carries_inherited_locks=True)
    key = ComponentStateKey(components={"c": {"ax": 1.0}})
    payload = {"ax": {"model": "absolute_dihedral_grid", "frame": None}}
    states = [SimpleNamespace(component_id="c", payload=payload)]
    context = SimpleNamespace(
        resolved_spec={"c": 123}, registry=SimpleNamespace(descriptors=[descriptor])
    )
    entries = _inherited_report_entries(states, key, context)
    assert len(entries) == 1
    assert entries[0]["axis"] == "c.ax"


# --- engine.py:503 (bad chemical states map) ------------------------------------


def test_engine_report_entries_skips_bad_chemical_state() -> None:
    from types import SimpleNamespace

    from confflow.science.confgen.engine import _inherited_report_entries
    from confflow.science.confgen.kernel_records import ComponentStateKey

    descriptor = SimpleNamespace(id="c", carries_inherited_locks=True)
    key = ComponentStateKey(components={"c": {"ax": "missing-label"}})
    payload = {"ax": {"model": "chemical", "states": {}, "reference_frame_value": 0.0}}
    states = [SimpleNamespace(component_id="c", payload=payload)]
    context = SimpleNamespace(resolved_spec={}, registry=SimpleNamespace(descriptors=[descriptor]))
    assert _inherited_report_entries(states, key, context) == []


# --- engine.py:581 (exploding descriptor id) -------------------------------------


def test_engine_diagnostics_skips_exploding_id() -> None:
    from types import SimpleNamespace

    from confflow.science.confgen.engine import _collect_component_diagnostics

    class _BadId:
        @property
        def id(self) -> str:
            raise TypeError("boom")

    registry = SimpleNamespace(_ordered=lambda: [_BadId()], descriptors=[_BadId()])
    assert _collect_component_diagnostics(registry, {}, SimpleNamespace()) == {}


# --- engine.py:588 (exploding bound mapping) --------------------------------------


def test_engine_diagnostics_tolerates_exploding_bound() -> None:
    from collections.abc import Mapping
    from types import SimpleNamespace

    from confflow.science.confgen.engine import _collect_component_diagnostics

    class _BoomMap(Mapping):
        def __getitem__(self, key: object) -> object:
            raise KeyError(key)

        def __iter__(self):  # type: ignore[no-untyped-def]
            return iter(())

        def __len__(self) -> int:
            return 0

        def get(self, key: object, default: object = None) -> object:
            raise TypeError("boom")

    descriptor = SimpleNamespace(id="x")
    registry = SimpleNamespace(_ordered=lambda: [descriptor], descriptors=[descriptor])
    assert _collect_component_diagnostics(registry, _BoomMap(), SimpleNamespace()) == {}


# --- engine.py:2075/2081/2085 (bond integrity fallbacks) ---------------------------


def test_engine_bond_integrity_tolerates_exploding_hooks() -> None:
    from confflow.science.confgen.engine import _RunState

    class _EvilProp:
        @property
        def check_bond_integrity(self) -> bool:
            raise TypeError("boom")

        def __init__(self) -> None:
            self.__dict__["check_bond_integrity"] = self

    # Overridden stage whose property explodes -> False (2075).
    class _Overridden:
        def __init__(self) -> None:
            self.__dict__["check_bond_integrity"] = _EvilProp()

        @property
        def check_bond_integrity(self) -> bool:  # type: ignore[override]
            raise TypeError("boom")

    shell = _RunState.__new__(_RunState)
    assert shell._check_bond_integrity("ax", _Overridden()) is False  # type: ignore[arg-type]

    # Descriptor whose attribute explodes -> falls through to stage default (2081).
    class _BadDesc:
        @property
        def check_bond_integrity(self) -> bool:
            raise TypeError("boom")

    shell2 = _RunState.__new__(_RunState)
    shell2._descriptor_for = lambda _axis: _BadDesc()  # type: ignore[method-assign]
    assert shell2._check_bond_integrity("ax", SimpleNamespace()) is False

    # Stage with exploding __getattr__ on the final fallback -> False (2085).
    class _EvilGetattr:
        def __getattr__(self, name: str) -> object:
            raise TypeError("boom")

    shell3 = _RunState.__new__(_RunState)
    shell3._descriptor_for = lambda _axis: None  # type: ignore[method-assign]
    assert shell3._check_bond_integrity("ax", _EvilGetattr()) is False  # type: ignore[arg-type]


# --- engine.py:2793/2799/2803 (carries inherited fallbacks) --------------------------


def test_engine_carries_inherited_tolerates_exploding_hooks() -> None:
    from confflow.science.confgen.engine import _RunState

    class _Overridden:
        @property
        def carries_inherited_locks(self) -> bool:  # type: ignore[override]
            raise ValueError("boom")

    shell = _RunState.__new__(_RunState)
    assert shell._carries_inherited_locks("ax", _Overridden()) is False  # type: ignore[arg-type]

    class _BadDesc:
        @property
        def carries_inherited_locks(self) -> bool:
            raise TypeError("boom")

    shell2 = _RunState.__new__(_RunState)
    shell2._descriptor_for = lambda _axis: _BadDesc()  # type: ignore[method-assign]
    assert shell2._carries_inherited_locks("ax", SimpleNamespace()) is False

    class _EvilGetattr:
        def __getattr__(self, name: str) -> object:
            raise ValueError("boom")

    shell3 = _RunState.__new__(_RunState)
    shell3._descriptor_for = lambda _axis: None  # type: ignore[method-assign]
    assert shell3._carries_inherited_locks("ax", _EvilGetattr()) is False  # type: ignore[arg-type]
