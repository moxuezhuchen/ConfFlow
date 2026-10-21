"""Regression tests: an execution binding's allow-list and sandbox bind every item launch.

The step-level check in ``V4RunApplication`` is one layer; the work-item executor repeats
the check before the item directory or any input file exists, so direct batch callers
cannot bypass it.
"""

from __future__ import annotations

import dataclasses
import os
import sys
from pathlib import Path
from typing import Any

import pytest

from confflow.application.v4_run import V4RunApplication, V4RunRequest, import_xyz
from confflow.domain import FrozenDict
from confflow.domain.completion import WorkItemStatus
from confflow.domain.errors import DomainError
from confflow.execution import ExecutionBinding
from confflow.execution.process import NativeProcessSupervisor
from confflow.execution.work_item_executor import WorkItemExecutor
from confflow.workflow.v4.assembly import RunInputs
from tests.v4._builders import calc_step, structure_set, v4_doc
from tests.v4._helpers.v42_executors import (
    FAKE_ORCA,
    ORCA_NATIVE,
    assemble_items,
    calculation_doc,
    compile_plan,
    item_context,
)

WATER_XYZ = "3\nwater\nO 0 0 0\nH 0.76 0.59 0\nH -0.76 0.59 0\n"


def _fake_orca_copy(directory: Path, name: str = "orca") -> Path:
    """Write the fake ORCA program under *directory* with a python shebang."""
    directory.mkdir(parents=True, exist_ok=True)
    original = FAKE_ORCA.read_text(encoding="utf-8")
    target = directory / name
    target.write_text("#!" + sys.executable + "\n" + original.split("\n", 1)[1], encoding="utf-8")
    target.chmod(0o700)
    return target


def _context(plan: Any, fake: Path, run_root: Path, work_base: Path, binding: Any) -> Any:
    context = item_context(
        plan,
        "orca",
        str(fake),
        str(run_root),
        str(work_base),
        NativeProcessSupervisor(),
        ["normal_termination"],
    )
    return dataclasses.replace(context, execution_binding=binding)


def _executor_run(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    binding_kwargs: dict[str, Any],
    *,
    fake: Path | None = None,
    run_root: Path | None = None,
) -> tuple[Any, Path]:
    monkeypatch.setenv("FAKE_MODE", "success_opt")
    fake = fake or _fake_orca_copy(tmp_path / "bin")
    plan = compile_plan(calculation_doc("orca", ORCA_NATIVE, str(fake)))
    items = assemble_items(plan, structure_set("s0"))
    run_root = run_root or (tmp_path / "run")
    work_base = run_root / "items"
    binding = ExecutionBinding(binding_id="restricted", env=FrozenDict({}), **binding_kwargs)
    context = _context(plan, fake, run_root, work_base, binding)
    result = WorkItemExecutor().execute(items[0], context)
    return result, work_base


def _failure_text(result: Any) -> str:
    messages = [d.message for d in result.diagnostics]
    if result.error is not None:
        messages.append(str(result.error))
    return " ".join(messages)


def _no_program_files(root: Path) -> list[Path]:
    if not root.exists():
        return []
    return [path for path in root.rglob("*") if path.is_file()]


# -- (1) full run: a forbidden program writes no input and starts nothing -------------


def test_run_with_forbidden_executable_writes_no_input(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FAKE_MODE", "success_opt")
    monkeypatch.setenv("CONFFLOW_SERVER_CONFIG", str(tmp_path / "absent.toml"))
    fake = _fake_orca_copy(tmp_path / "bin")
    allowed = tmp_path / "allowed"
    allowed.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    allowed.chmod(0o700)
    run_root = tmp_path / "run"
    step = calc_step(
        "s",
        program="orca",
        native={"keyword": "HF Opt"},
        checks=["normal_termination"],
        bindings={"structure": {"source": {"run": "structures"}}},
        resources={"cores_per_item": 1, "memory_per_item": "1GB"},
        execution={
            "binding_id": "restricted",
            "executable": str(fake),
            "allowed_executables": [str(allowed)],
        },
    )
    document = v4_doc(
        [step],
        inputs={"structures": {"kind": "structure", "cardinality": "many"}},
        global_config={"scientific_defaults": {"charge": 0, "multiplicity": 1}},
    )
    request = V4RunRequest(
        workflow_document=document,
        run_inputs=RunInputs(structures=FrozenDict({"structures": import_xyz(WATER_XYZ)})),
        import_sources=FrozenDict({"structures": WATER_XYZ}),
        run_root=str(run_root),
        executables=FrozenDict({}),
    )
    # the step-level check refuses the forbidden program before any measurement or write
    with pytest.raises(DomainError, match="not allowed by allowed_executables"):
        V4RunApplication(supervisor=NativeProcessSupervisor()).run(request)
    assert list(run_root.rglob("*.inp")) == []


# -- (2) an allowed executable passes ------------------------------------------------


def test_allowed_absolute_executable_runs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _fake_orca_copy(tmp_path / "bin")
    result, _ = _executor_run(
        tmp_path,
        monkeypatch,
        {"executable": str(fake), "allowed_executables": (str(fake),)},
        fake=fake,
    )
    assert result.status is WorkItemStatus.COMPLETED
    assert result.is_completed


# -- (3) sandbox: run roots inside pass, outside fail --------------------------------


def test_run_root_outside_sandbox_fails_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = _fake_orca_copy(tmp_path / "bin")
    sandbox = tmp_path / "sandbox"
    sandbox.mkdir()
    outside = tmp_path / "outside-run"
    result, work_base = _executor_run(
        tmp_path,
        monkeypatch,
        {"executable": str(fake), "sandbox": str(sandbox)},
        fake=fake,
        run_root=outside,
    )
    assert result.status is WorkItemStatus.FAILED
    assert "sandbox" in _failure_text(result)
    assert not work_base.exists()
    assert _no_program_files(outside) == []


def test_run_root_inside_sandbox_runs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _fake_orca_copy(tmp_path / "bin")
    sandbox = tmp_path / "sandbox"
    result, _ = _executor_run(
        tmp_path,
        monkeypatch,
        {"executable": str(fake), "sandbox": str(sandbox)},
        fake=fake,
        run_root=sandbox / "run",
    )
    assert result.status is WorkItemStatus.COMPLETED


# -- (4) bare-name allow-list entries match a PATH-resolved program -----------------


def test_bare_name_allow_list_matches_path_program(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bin_dir = tmp_path / "pathbin"
    _fake_orca_copy(bin_dir, name="confflow_review_orca")
    monkeypatch.setenv("PATH", f"{bin_dir}:{os.environ.get('PATH', '')}")
    result, _ = _executor_run(
        tmp_path,
        monkeypatch,
        {
            "executable": "confflow_review_orca",
            "allowed_executables": ("confflow_review_orca",),
        },
        fake=FAKE_ORCA,
    )
    assert result.status is WorkItemStatus.COMPLETED


def test_bare_name_not_on_allow_list_is_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bin_dir = tmp_path / "pathbin"
    _fake_orca_copy(bin_dir, name="confflow_review_orca")
    monkeypatch.setenv("PATH", f"{bin_dir}:{os.environ.get('PATH', '')}")
    result, work_base = _executor_run(
        tmp_path,
        monkeypatch,
        {"executable": "confflow_review_orca", "allowed_executables": ("other-orca",)},
        fake=FAKE_ORCA,
    )
    assert result.status is WorkItemStatus.FAILED
    assert "allowed_executables" in _failure_text(result)
    assert not work_base.exists()


# -- (5) executor level: a forbidden binding fails closed with no files --------------


def test_executor_forbidden_binding_fails_closed_without_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = _fake_orca_copy(tmp_path / "bin")
    result, work_base = _executor_run(
        tmp_path,
        monkeypatch,
        {"executable": str(fake), "allowed_executables": ("/nonexistent/only-this",)},
        fake=fake,
    )
    assert result.status is WorkItemStatus.FAILED
    assert result.error is not None
    assert not work_base.exists()
    assert _no_program_files(tmp_path / "run") == []


def test_executor_missing_program_on_allow_list_fails_before_io(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    result, work_base = _executor_run(
        tmp_path,
        monkeypatch,
        {"executable": str(tmp_path / "not-installed"), "allowed_executables": (str(FAKE_ORCA),)},
        fake=FAKE_ORCA,
    )
    assert result.status is WorkItemStatus.FAILED
    assert "allowed_executables" in _failure_text(result)
    assert not work_base.exists()
