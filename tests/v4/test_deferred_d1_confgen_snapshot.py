#!/usr/bin/env python3

"""D1: the ConfGen search executor refuses to run without an environment snapshot.

``context.native_env`` is the step's single digested environment. A direct
executor caller that omits it must fail closed before any directory is created
or any process is launched; it must never read the ambient ``os.environ``.
"""

from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from confflow.domain import FrozenDict
from confflow.domain.resources import ResourceRequest
from confflow.domain.structure import StructureRecord, StructureSet
from confflow.domain.work_item import WorkItem, WorkItemInputs, make_work_item_id
from confflow.execution.binding_resolution import effective_native_env
from confflow.execution.confgen_executor import ConfgenExecutor
from confflow.execution.contracts import ExecutionBinding
from confflow.execution.native import NativeExecutionResult
from confflow.execution.process import NativeProcessSupervisor
from confflow.execution.work_item_executor import ItemExecutionContext, WorkItemExecutor
from confflow.workflow.v4.confgen_schema import ConfgenModelV3
from tests.v4._dg_search_helpers import _toy, _write_fake

_SEARCH = {
    "budget": 12,
    "starts": 1,
    "embed_timeout_seconds": 20,
    "max_cycles": 100,
    "bond_scale": 1.25,
}
_SITES = [{"id": f"s{i}", "atoms": [d]} for i, d in enumerate((2, 3, 4, 5))]


def _native() -> FrozenDict:
    scope = {
        "schema_version": 4,
        "coordination": {"metal_center": 1, "binding_sites": _SITES, "shapes": ["square_planar"]},
        "seed": 7,
        "search": dict(_SEARCH),
    }
    return FrozenDict(ConfgenModelV3.model_validate(scope).scientific_native())


def _driving() -> StructureRecord:
    els, ref = _toy()
    return StructureRecord(
        id="m1",
        atoms=tuple(els),
        coordinates=tuple((float(x), float(y), float(z)) for x, y, z in ref),
        charge=0,
        multiplicity=1,
    )


def _item() -> WorkItem:
    driving = _driving()
    return WorkItem(
        id=make_work_item_id("cg-item"),
        logical_key="cg-item",
        step_id="cg",
        named_inputs=WorkItemInputs(
            structures=FrozenDict({"structure": StructureSet((driving,))}),
            results=FrozenDict({}),
        ),
        resources=ResourceRequest(cores_per_item=2, memory_per_item_bytes=2**30),
        semantic_digest="sha256:" + "0" * 64,
        ordinal=0,
    )


def _ctx(fake: Path, run_root: Path, native_env: Any) -> ItemExecutionContext:
    scientific = SimpleNamespace(seed=7, native=_native(), overrides=FrozenDict({}))
    return ItemExecutionContext(
        step_id="cg",
        scientific=scientific,
        scientific_defaults=SimpleNamespace(),
        adapter=None,
        profile=None,
        supervisor=NativeProcessSupervisor(),
        run_root=str(run_root),
        poll_interval_seconds=0.01,
        execution_binding=ExecutionBinding(binding_id="t", executable=str(fake)),
        native_env=native_env,
    )


def _tree(root: Path) -> set[str]:
    return {str(p.relative_to(root)) for p in root.rglob("*")}


def test_missing_snapshot_rejected_before_directories_or_launch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = _write_fake(tmp_path)
    run_root = tmp_path / "run"
    run_root.mkdir()
    before = _tree(tmp_path)
    launches: list[Any] = []

    def _record(*args: Any, **kwargs: Any) -> Any:
        launches.append(args)
        raise AssertionError("no process may be launched without an environment snapshot")

    monkeypatch.setattr(WorkItemExecutor, "launch_and_wait", _record)
    item = _item()
    out = ConfgenExecutor().execute(item, _ctx(fake, run_root, None))

    assert out.status.value == "failed"
    message = out.diagnostics[0].message
    assert "environment snapshot" in message
    assert "native_env" in message
    assert launches == []
    assert _tree(tmp_path) == before
    assert not (Path(_ctx(fake, run_root, None).attempt_dir(item)) / "search_run").exists()


def test_snapshot_is_the_launch_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = _write_fake(tmp_path)
    run_root = tmp_path / "run"
    run_root.mkdir()
    snapshot = FrozenDict(
        effective_native_env(None, inherit={"PATH": os.defpath, "D1_MARK": "snap"})
    )
    monkeypatch.setenv("D1_MARK", "ambient")
    launched: list[Any] = []

    def _capture(*args: Any, **kwargs: Any) -> Any:
        launched.append(args[2])
        return (NativeExecutionResult(exit_code=1, wall_time_seconds=0.0, timed_out=False), None)

    monkeypatch.setattr(WorkItemExecutor, "launch_and_wait", _capture)
    out = ConfgenExecutor().execute(_item(), _ctx(fake, run_root, snapshot))

    assert len(launched) == 1
    request = launched[0]
    assert dict(request.env) == dict(snapshot)
    assert request.env["D1_MARK"] == "snap"
    assert out.status.value == "failed"
    assert (Path(request.work_dir)).is_dir()
