"""CI hermetic executable-identity tests (CI-1..CI-6).

These tests prove the workflow suites need NO system QC programs on PATH:
every test sanitizes PATH in-test to remove any directory providing ``orca``
or ``g16`` (asserting ``shutil.which`` finds neither), then drives all
identity flows through hermetic fake executables — real files with the real
exec bit, so downstream computes the real realpath and the real SHA-256.

Production fail-closed semantics are never weakened: nothing here mocks
``resolve_executable_identity`` or the C fingerprint, and the fakes are only
 ever read, never executed.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
from pathlib import Path
from typing import Any

import pytest

from confflow.core.exceptions import ConfFlowError
from confflow.workflow.execution_context import (
    resolve_executable_identity,
    resolve_execution_context_v3,
    workflow_execution_fingerprint_v3,
)
from confflow.workflow.plan import WorkflowV3Plan, build_workflow_plan

V3 = "confflow.workflow.v3"


def _sanitized_path(monkeypatch) -> str:
    """Drop every PATH entry that provides ``orca`` or ``g16``, then prove it.

    Returns the sanitized PATH string. After this call ``shutil.which``
    finds neither program, so any successful resolution must come from the
    test's own hermetic fakes — never from system QC installations.
    """
    clean: list[str] = []
    for entry in os.environ.get("PATH", "").split(os.pathsep):
        if not entry:
            continue
        try:
            provides_qc = (Path(entry) / "orca").is_file() or (Path(entry) / "g16").is_file()
        except OSError:
            continue
        if not provides_qc:
            clean.append(entry)
    monkeypatch.setenv("PATH", os.pathsep.join(clean))
    assert shutil.which("orca") is None, "sanitized PATH must not provide orca"
    assert shutil.which("g16") is None, "sanitized PATH must not provide g16"
    return os.environ["PATH"]


def _write_xyz(path: Path, note: str = "seed") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"1\n{note}\nH 0 0 0\n", encoding="utf-8")
    return path


def _plan(
    tmp_path: Path,
    *,
    subdir: str = "",
    orca_path: str | None = None,
    iprog: str = "orca",
) -> WorkflowV3Plan:
    base = tmp_path / subdir if subdir else tmp_path
    base.mkdir(parents=True, exist_ok=True)
    _write_xyz(base / "input.xyz")
    definition: dict[str, Any] = {
        "schema": V3,
        "steps": [
            {"id": "s001", "type": "confgen", "inputs": [], "params": {"chains": ["1-2"]}},
            {"id": "s002", "type": "calc", "inputs": ["s001"], "params": {"keyword": "HF"}},
        ],
    }
    if orca_path is not None or iprog != "orca":
        definition["global"] = {"iprog": iprog}
        if orca_path is not None:
            definition["global"]["orca_path"] = orca_path
    (base / "wf.yaml").write_text(json.dumps(definition), encoding="utf-8")
    plan = build_workflow_plan([str(base / "input.xyz")], str(base / "wf.yaml"))
    assert isinstance(plan, WorkflowV3Plan)
    return plan


def test_ci1_no_system_qc_needed_explicit_paths(
    tmp_path: Path, monkeypatch, fake_qc_executables: dict[str, str]
) -> None:
    """CI-1: context finalizes with sanitized PATH via explicit fake paths."""
    _sanitized_path(monkeypatch)
    plan = _plan(tmp_path, orca_path=fake_qc_executables["orca"])
    context = resolve_execution_context_v3(plan, input_files=plan.input_files)
    assert "s002" in dict(context.step_executables)
    assert workflow_execution_fingerprint_v3(plan, context).startswith("sha256:")


def test_ci2_bare_names_resolve_via_path_fake(
    tmp_path: Path, monkeypatch, fake_qc_executables: dict[str, str]
) -> None:
    """CI-2: bare ``orca``/``g16`` resolve against the fake bin on sanitized PATH."""
    base_path = _sanitized_path(monkeypatch)
    bin_dir = str(Path(fake_qc_executables["orca"]).parent)
    monkeypatch.setenv("PATH", bin_dir + os.pathsep + base_path)
    plan = _plan(tmp_path)  # bare default "orca", no explicit path
    context = resolve_execution_context_v3(plan, input_files=plan.input_files)
    identity = dict(context.step_executables)["s002"]
    assert identity.resolved_path == os.path.realpath(fake_qc_executables["orca"])
    assert workflow_execution_fingerprint_v3(plan, context).startswith("sha256:")


def test_ci3_identity_is_real_realpath_and_sha256(
    tmp_path: Path, monkeypatch, fake_qc_executables: dict[str, str]
) -> None:
    """CI-3: fake identity carries the real realpath and real entrypoint SHA-256."""
    _sanitized_path(monkeypatch)
    exe = fake_qc_executables["orca"]
    identity = resolve_executable_identity("orca", exe)
    assert identity.program == "orca"
    assert identity.resolved_path == os.path.realpath(exe)
    expected = "sha256:" + hashlib.sha256(Path(exe).read_bytes()).hexdigest()
    assert identity.entrypoint_sha256 == expected
    # A symlink spelling resolves to the same identity (canonical realpath).
    link = tmp_path / "link-orca"
    link.symlink_to(exe)
    assert resolve_executable_identity("orca", str(link)) == identity


def test_ci4_byte_mutation_changes_identity_and_c(
    tmp_path: Path, monkeypatch, fake_qc_executables: dict[str, str]
) -> None:
    """CI-4: mutating fake bytes changes the identity and fingerprint C, not A."""
    _sanitized_path(monkeypatch)
    plan = _plan(tmp_path, orca_path=fake_qc_executables["orca"])
    before_c = workflow_execution_fingerprint_v3(
        plan, resolve_execution_context_v3(plan, input_files=plan.input_files)
    )
    before_a = plan.definition_fingerprint
    assert before_c.startswith("sha256:")
    Path(fake_qc_executables["orca"]).write_text("#!/bin/sh\n# rebuilt\nexit 0\n", encoding="utf-8")
    after_c = workflow_execution_fingerprint_v3(
        plan, resolve_execution_context_v3(plan, input_files=plan.input_files)
    )
    assert plan.definition_fingerprint == before_a
    assert after_c != before_c


def test_ci5_missing_and_unreadable_executables_fail_closed(
    tmp_path: Path, monkeypatch, fake_qc_executables: dict[str, str]
) -> None:
    """CI-5: nonexistent/dir/missing-PATH executables fail closed on clean PATH."""
    _sanitized_path(monkeypatch)
    # Explicit absolute path that does not exist.
    with pytest.raises(ConfFlowError):
        resolve_executable_identity("orca", str(tmp_path / "ghost"))
    with pytest.raises(ConfFlowError):
        resolve_executable_identity("orca", "/nonexistent/orca")
    # A directory is not a regular file.
    with pytest.raises(ConfFlowError):
        resolve_executable_identity("orca", str(tmp_path))
    # A bare name with nothing providing it on the sanitized PATH.
    with pytest.raises(ConfFlowError, match="not found on PATH"):
        resolve_executable_identity("orca", "orca")
    with pytest.raises(ConfFlowError, match="not found on PATH"):
        resolve_executable_identity("g16", "g16")
    # An enabled calc step naming a missing executable cannot finalize C.
    plan = _plan(tmp_path, subdir="enabled", orca_path="/nonexistent/orca")
    with pytest.raises(ConfFlowError):
        resolve_execution_context_v3(plan, input_files=plan.input_files)
    # The hermetic fake itself still resolves on the same sanitized PATH.
    assert resolve_executable_identity("orca", fake_qc_executables["orca"]).program == "orca"


def test_ci6_worker_side_resolution_without_controller_state(tmp_path: Path, monkeypatch) -> None:
    """CI-6: worker-site resolution depends only on the worker PATH + config.

    Two fake sites (controller X vs worker Y, distinct bytes and paths) yield
    distinct C values; the worker C is reproducible from the worker site alone
    with no controller state, cwd or cache.
    """
    _sanitized_path(monkeypatch)
    controller_bin = tmp_path / "controller_bin"
    worker_bin = tmp_path / "worker_bin"
    controller_bin.mkdir(parents=True, exist_ok=True)
    worker_bin.mkdir(parents=True, exist_ok=True)
    exe_x = controller_bin / "orca"
    exe_y = worker_bin / "orca"
    exe_x.write_text("#!/bin/sh\n# controller ORCA X\nexit 0\n", encoding="utf-8")
    exe_y.write_text("#!/bin/sh\n# worker ORCA Y\nexit 0\n", encoding="utf-8")
    exe_x.chmod(0o755)
    exe_y.chmod(0o755)
    assert exe_x.read_bytes() != exe_y.read_bytes()

    _write_xyz(tmp_path / "input.xyz")
    config = tmp_path / "wf.yaml"
    config.write_text(
        json.dumps(
            {
                "schema": V3,
                "global": {"orca_path": "orca"},  # bare name: resolved per-site via PATH
                "steps": [
                    {
                        "id": "s001",
                        "type": "confgen",
                        "inputs": [],
                        "params": {"chains": ["1-2"]},
                    },
                    {"id": "s002", "type": "calc", "inputs": ["s001"], "params": {"keyword": "HF"}},
                ],
            }
        ),
        encoding="utf-8",
    )
    base_path = os.environ["PATH"]

    monkeypatch.setenv("PATH", str(controller_bin) + os.pathsep + base_path)
    controller_plan = build_workflow_plan([str(tmp_path / "input.xyz")], str(config))
    assert isinstance(controller_plan, WorkflowV3Plan)
    controller_c = workflow_execution_fingerprint_v3(
        controller_plan,
        resolve_execution_context_v3(controller_plan, input_files=controller_plan.input_files),
    )

    monkeypatch.setenv("PATH", str(worker_bin) + os.pathsep + base_path)
    worker_plan = build_workflow_plan([str(tmp_path / "input.xyz")], str(config))
    assert isinstance(worker_plan, WorkflowV3Plan)
    worker_context = resolve_execution_context_v3(worker_plan, input_files=worker_plan.input_files)
    worker_c = workflow_execution_fingerprint_v3(worker_plan, worker_context)

    assert controller_c != worker_c
    # Same worker site, fresh resolution: identical C (no controller state).
    worker_c_again = workflow_execution_fingerprint_v3(
        worker_plan,
        resolve_execution_context_v3(worker_plan, input_files=worker_plan.input_files),
    )
    assert worker_c_again == worker_c
    assert dict(worker_context.step_executables)["s002"].resolved_path == os.path.realpath(
        str(exe_y)
    )
