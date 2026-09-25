"""R4.2 — Workflow Binding V2 and the Execution Fingerprint C.

Covers the ordered external-input identity
(I1–I6), the execution-site executable identity (EX1–EX7), the producer/
schema/canonicalization compatibility policies (PR1–PR7, SC1–SC6), the
layered A/B/C comparator (B1–B10), the C determinism properties (C1–C9),
and the execution-site executable identity, plus the Execution Fingerprint C
determinism properties. No V3 execution, no runtime directories, no
engine/service/worker wiring.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from confflow.core.exceptions import ConfFlowError
from confflow.workflow.execution_context import (
    resolve_executable_identity,
    resolve_execution_context_v3,
    resolve_external_input_identity,
    workflow_execution_fingerprint_v3,
)
from confflow.workflow.plan import WorkflowV3Plan, build_workflow_plan

V3 = "confflow.workflow.v3"


def _write_xyz(path: Path, note: str = "seed") -> Path:
    path.write_text(f"1\n{note}\nH 0 0 0\n", encoding="utf-8")
    return path


def _v3_plan(
    tmp_path: Path,
    steps: list[dict[str, Any]] | None = None,
    *,
    subdir: str = "",
) -> WorkflowV3Plan:
    base = tmp_path / subdir if subdir else tmp_path
    base.mkdir(parents=True, exist_ok=True)
    _write_xyz(base / "input.xyz")
    document = {
        "schema": V3,
        "steps": (
            steps
            if steps is not None
            else [
                {"id": "s001", "type": "confgen", "inputs": [], "params": {"chains": ["1-2"]}},
                {"id": "s002", "type": "calc", "inputs": ["s001"], "params": {"keyword": "HF"}},
            ]
        ),
    }
    (base / "wf.yaml").write_text(json.dumps(document), encoding="utf-8")
    plan = build_workflow_plan([str(base / "input.xyz")], str(base / "wf.yaml"))
    assert isinstance(plan, WorkflowV3Plan)
    return plan


def _executable(path: Path, body: str = "#!/bin/sh\nexit 0\n") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")
    path.chmod(0o755)
    return path


def _orca_config(tmp_path: Path, exe: Path) -> dict[str, Any]:
    return {
        "schema": V3,
        "global": {"iprog": "orca", "itask": "sp", "orca_path": str(exe)},
        "steps": [
            {"id": "s001", "type": "confgen", "inputs": [], "params": {"chains": ["1-2"]}},
            {"id": "s002", "type": "calc", "inputs": ["s001"], "params": {"keyword": "HF"}},
        ],
    }


def _orca_plan(tmp_path: Path, exe: Path, *, subdir: str = "") -> WorkflowV3Plan:
    base = tmp_path / subdir if subdir else tmp_path
    base.mkdir(parents=True, exist_ok=True)
    _write_xyz(base / "input.xyz")
    (base / "wf.yaml").write_text(json.dumps(_orca_config(tmp_path, exe)), encoding="utf-8")
    plan = build_workflow_plan([str(base / "input.xyz")], str(base / "wf.yaml"))
    assert isinstance(plan, WorkflowV3Plan)
    return plan


def _context(plan: WorkflowV3Plan, *, input_files: list[str] | None = None):
    files = input_files if input_files is not None else plan.input_files
    return resolve_execution_context_v3(plan, input_files=files)


def _c(plan: WorkflowV3Plan, *, input_files: list[str] | None = None) -> str:
    """Return the Execution Fingerprint C over the plan's (or given) inputs."""
    return workflow_execution_fingerprint_v3(plan, _context(plan, input_files=input_files))


# ---------------------------------------------------------------------------
# External input identity (I1–I6, PD-2)
# ---------------------------------------------------------------------------
class TestExternalInputIdentity:
    def test_i1_same_bytes_different_directory_same_c(self, tmp_path: Path) -> None:
        plan_a = _v3_plan(tmp_path / "a")
        plan_b = _v3_plan(tmp_path / "b")
        first = workflow_execution_fingerprint_v3(plan_a, _context(plan_a))
        second = workflow_execution_fingerprint_v3(plan_b, _context(plan_b))
        assert first == second

    def test_i2_renamed_basename_same_c(self, tmp_path: Path) -> None:
        plan = _v3_plan(tmp_path)
        renamed = _write_xyz(tmp_path / "totally-different-name.xyz")
        context = _context(plan, input_files=[str(renamed)])
        assert workflow_execution_fingerprint_v3(plan, context) == (
            workflow_execution_fingerprint_v3(plan, _context(plan))
        )

    def test_i3_one_byte_change_different_c(self, tmp_path: Path) -> None:
        plan = _v3_plan(tmp_path)
        changed = tmp_path / "changed.xyz"
        changed.write_text("1\nseedX\nH 0 0 0\n", encoding="utf-8")
        context = _context(plan, input_files=[str(changed)])
        assert workflow_execution_fingerprint_v3(plan, context) != (
            workflow_execution_fingerprint_v3(plan, _context(plan))
        )

    def test_i4_order_matters(self, tmp_path: Path) -> None:
        first_file = _write_xyz(tmp_path / "first.xyz", "atomA")
        second_file = _write_xyz(tmp_path / "second.xyz", "atomB")
        plan = _v3_plan(tmp_path)
        forward = _context(plan, input_files=[str(first_file), str(second_file)])
        backward = _context(plan, input_files=[str(second_file), str(first_file)])
        assert forward.input_digests != backward.input_digests
        assert workflow_execution_fingerprint_v3(plan, forward) != (
            workflow_execution_fingerprint_v3(plan, backward)
        )

    def test_i5_count_changes_c(self, tmp_path: Path) -> None:
        plan = _v3_plan(tmp_path)
        extra = _write_xyz(tmp_path / "extra.xyz")
        one = _context(plan, input_files=[plan.input_files[0]])
        two = _context(plan, input_files=[plan.input_files[0], str(extra)])
        assert one.input_count == 1 and two.input_count == 2
        assert workflow_execution_fingerprint_v3(plan, one) != (
            workflow_execution_fingerprint_v3(plan, two)
        )

    def test_i6_duplicate_content_slots_deterministic(self, tmp_path: Path) -> None:
        same = _write_xyz(tmp_path / "same.xyz")
        other = _write_xyz(tmp_path / "other.xyz", "other")
        plan = _v3_plan(tmp_path)
        left = _context(plan, input_files=[str(same), str(other)])
        right = _context(plan, input_files=[str(same), str(other)])
        assert left.input_digests == right.input_digests
        assert len(left.input_digests) == 2

    def test_missing_input_fails_closed(self, tmp_path: Path) -> None:
        with pytest.raises(ConfFlowError):
            resolve_external_input_identity([str(tmp_path / "ghost.xyz")])
        with pytest.raises(ConfFlowError):
            resolve_external_input_identity([str(tmp_path)])  # a directory


# ---------------------------------------------------------------------------
# Executable identity (EX1–EX7, PD-5)
# ---------------------------------------------------------------------------
class TestExecutableIdentity:
    def test_ex1_same_path_same_bytes_same_identity(self, tmp_path: Path) -> None:
        exe = _executable(tmp_path / "orca")
        first = resolve_executable_identity("orca", str(exe))
        second = resolve_executable_identity("orca", str(exe))
        assert first == second

    def test_ex2_changed_bytes_different_identity(self, tmp_path: Path) -> None:
        exe = _executable(tmp_path / "orca")
        first = resolve_executable_identity("orca", str(exe))
        _executable(exe, "#!/bin/sh\nexit 1\n")
        second = resolve_executable_identity("orca", str(exe))
        assert first != second

    def test_ex3_different_realpath_same_bytes_different_identity(self, tmp_path: Path) -> None:
        exe_a = _executable(tmp_path / "a" / "orca")
        exe_b = _executable(tmp_path / "b" / "orca")
        first = resolve_executable_identity("orca", str(exe_a))
        second = resolve_executable_identity("orca", str(exe_b))
        assert first.entrypoint_sha256 == second.entrypoint_sha256
        assert first.resolved_path != second.resolved_path
        assert first != second

    def test_ex4_symlink_spelling_resolves_same_identity(self, tmp_path: Path) -> None:
        real = _executable(tmp_path / "real" / "orca")
        link = tmp_path / "link-orca"
        link.symlink_to(real)
        via_link = resolve_executable_identity("orca", str(link))
        via_real = resolve_executable_identity("orca", str(real))
        assert via_link == via_real

    def test_ex5_nonexistent_fails_closed(self, tmp_path: Path) -> None:
        with pytest.raises(ConfFlowError):
            resolve_executable_identity("orca", str(tmp_path / "ghost"))

    def test_ex6_directory_and_unreadable_fail_closed(self, tmp_path: Path, monkeypatch) -> None:
        with pytest.raises(ConfFlowError):
            resolve_executable_identity("orca", str(tmp_path))  # directory, not a file
        exe = _executable(tmp_path / "orca")
        import confflow.workflow.execution_context as context_module

        def _boom(path: Any) -> str:
            raise OSError("simulated unreadable entrypoint")

        monkeypatch.setattr(context_module, "_file_sha256", _boom)
        with pytest.raises(ConfFlowError):
            resolve_executable_identity("orca", str(exe))

    def test_ex7_executable_change_moves_c_not_a(self, tmp_path: Path) -> None:
        exe = _executable(tmp_path / "orca")
        plan = _orca_plan(tmp_path, exe)
        definition_fingerprint = plan.definition_fingerprint
        before = _c(plan)
        _executable(exe, "#!/bin/sh\n# upgraded\nexit 0\n")
        assert plan.definition_fingerprint == definition_fingerprint
        assert _c(plan) != before

    def test_no_subprocess_is_ever_launched(self, tmp_path: Path, monkeypatch) -> None:
        import subprocess

        def _boom(*args: Any, **kwargs: Any) -> None:
            raise AssertionError("executable identity must not launch anything")

        monkeypatch.setattr(subprocess, "run", _boom)
        monkeypatch.setattr(subprocess, "Popen", _boom)
        exe = _executable(tmp_path / "orca")
        plan = _orca_plan(tmp_path, exe)
        resolve_execution_context_v3(plan, input_files=plan.input_files)


# ---------------------------------------------------------------------------
# ResolvedExecutionContextV3 / C determinism (C1–C9)
# ---------------------------------------------------------------------------
class TestExecutionFingerprintProperties:
    def test_c1_same_semantics_same_c(self, tmp_path: Path) -> None:
        assert _c(_v3_plan(tmp_path / "a")) == _c(_v3_plan(tmp_path / "b"))

    def test_c2_c3_c4_label_reorder_annotations_same_c(self, tmp_path: Path) -> None:
        exe = _executable(tmp_path / "orca")
        base = _orca_plan(tmp_path, exe)
        baseline = _c(base)
        # label + annotations + array reorder, same semantics
        steps = _orca_config(tmp_path, exe)["steps"]
        document = _orca_config(tmp_path, exe)
        document["steps"] = [
            dict(steps[1], label="renamed", annotations={"confflow.migration.v2": {"x": 1}}),
            steps[0],
        ]
        (tmp_path / "reordered").mkdir()
        _write_xyz(tmp_path / "reordered" / "input.xyz")
        (tmp_path / "reordered" / "wf.yaml").write_text(json.dumps(document), encoding="utf-8")
        reordered_plan = build_workflow_plan(
            [str(tmp_path / "reordered" / "input.xyz")],
            str(tmp_path / "reordered" / "wf.yaml"),
        )
        assert isinstance(reordered_plan, WorkflowV3Plan)
        assert _c(reordered_plan) == baseline

    def test_c5_c6_c7_semantic_and_execution_changes_move_c(self, tmp_path: Path) -> None:
        exe = _executable(tmp_path / "orca")
        baseline_plan = _orca_plan(tmp_path, exe)
        baseline = _c(baseline_plan)

        # scientific param change (A) — via a different keyword
        document = _orca_config(tmp_path, exe)
        document["steps"][1]["params"]["keyword"] = "B3LYP"
        (tmp_path / "sci").mkdir()
        _write_xyz(tmp_path / "sci" / "input.xyz")
        (tmp_path / "sci" / "wf.yaml").write_text(json.dumps(document), encoding="utf-8")
        sci_plan = build_workflow_plan(
            [str(tmp_path / "sci" / "input.xyz")], str(tmp_path / "sci" / "wf.yaml")
        )
        assert sci_plan.definition_fingerprint != baseline_plan.definition_fingerprint
        assert _c(sci_plan) != baseline

        # execution-class global change (C only)
        document = _orca_config(tmp_path, exe)
        document["global"]["cores_per_task"] = 4
        (tmp_path / "res").mkdir()
        _write_xyz(tmp_path / "res" / "input.xyz")
        (tmp_path / "res" / "wf.yaml").write_text(json.dumps(document), encoding="utf-8")
        res_plan = build_workflow_plan(
            [str(tmp_path / "res" / "input.xyz")], str(tmp_path / "res" / "wf.yaml")
        )
        assert res_plan.definition_fingerprint == baseline_plan.definition_fingerprint
        assert _c(res_plan) != baseline

        # execution-class step param change (workers on the confgen step)
        document = _orca_config(tmp_path, exe)
        document["steps"][0]["params"]["workers"] = 3
        (tmp_path / "workers").mkdir()
        _write_xyz(tmp_path / "workers" / "input.xyz")
        (tmp_path / "workers" / "wf.yaml").write_text(json.dumps(document), encoding="utf-8")
        workers_plan = build_workflow_plan(
            [str(tmp_path / "workers" / "input.xyz")], str(tmp_path / "workers" / "wf.yaml")
        )
        assert workers_plan.definition_fingerprint == baseline_plan.definition_fingerprint
        assert _c(workers_plan) != baseline

    def test_c8_input_rename_same_c(self, tmp_path: Path) -> None:
        plan = _v3_plan(tmp_path)
        renamed = _write_xyz(tmp_path / "renamed.xyz")
        assert workflow_execution_fingerprint_v3(
            plan, _context(plan, input_files=[str(renamed)])
        ) == workflow_execution_fingerprint_v3(plan, _context(plan))

    def test_c9_executable_bytes_change_c(self, tmp_path: Path) -> None:
        exe = _executable(tmp_path / "orca")
        plan = _orca_plan(tmp_path, exe)
        before = _c(plan)
        _executable(exe, "#!/bin/sh\nexit 2\n")
        assert _c(plan) != before

    def test_run_id_work_dir_config_path_excluded_from_c(self, tmp_path: Path) -> None:
        plan = _v3_plan(tmp_path)
        binding_one = _c(plan)
        # Same semantics resolved from a different config filename / work dir.
        other_dir = tmp_path / "elsewhere"
        other_dir.mkdir()
        _write_xyz(other_dir / "different-input-name.xyz")
        (other_dir / "other-name.yaml").write_text(
            (tmp_path / "wf.yaml").read_text(encoding="utf-8"), encoding="utf-8"
        )
        other_plan = build_workflow_plan(
            [str(other_dir / "different-input-name.xyz")], str(other_dir / "other-name.yaml")
        )
        assert binding_one == _c(other_plan)


# ---------------------------------------------------------------------------
# Disabled-step execution context (D1–D7, frozen enabled-only policy)
# ---------------------------------------------------------------------------
def _mixed_orca_plan(tmp_path: Path, exe: Path, *, subdir: str = "") -> WorkflowV3Plan:
    """Confgen + enabled calc + disabled calc (dormant ORCA config)."""
    base = tmp_path / subdir if subdir else tmp_path
    base.mkdir(parents=True, exist_ok=True)
    _write_xyz(base / "input.xyz")
    document = {
        "schema": V3,
        "global": {"iprog": "orca", "itask": "sp", "orca_path": str(exe)},
        "steps": [
            {"id": "s001", "type": "confgen", "inputs": [], "params": {"chains": ["1-2"]}},
            {"id": "s002", "type": "calc", "inputs": ["s001"], "params": {"keyword": "HF"}},
            {
                "id": "s003",
                "type": "calc",
                "enabled": False,
                "inputs": ["s002"],
                "params": {"keyword": "HF", "iprog": "orca", "orca_path": "/nonexistent/orca"},
            },
        ],
    }
    (base / "wf.yaml").write_text(json.dumps(document), encoding="utf-8")
    plan = build_workflow_plan([str(base / "input.xyz")], str(base / "wf.yaml"))
    assert isinstance(plan, WorkflowV3Plan)
    return plan


class TestDisabledExecutionContext:
    def test_d1_disabled_calc_with_missing_program_still_finalizes_c(self, tmp_path: Path) -> None:
        # s003 is disabled and names a nonexistent ORCA; only s002 executes.
        plan = _mixed_orca_plan(tmp_path, _executable(tmp_path / "orca"))
        context = resolve_execution_context_v3(plan, input_files=plan.input_files)
        assert "s002" in dict(context.step_executables)
        assert "s003" not in dict(context.step_executables)
        assert workflow_execution_fingerprint_v3(plan, context).startswith("sha256:")

    def test_d2_disabled_executable_config_change_c_unchanged(self, tmp_path: Path) -> None:
        exe = _executable(tmp_path / "orca")
        plan = _mixed_orca_plan(tmp_path, exe)
        before = workflow_execution_fingerprint_v3(plan, _context(plan))
        document = json.loads((tmp_path / "wf.yaml").read_text(encoding="utf-8"))
        document["steps"][2]["params"]["orca_path"] = "/some/other/orca"
        (tmp_path / "changed").mkdir()
        _write_xyz(tmp_path / "changed" / "input.xyz")
        (tmp_path / "changed" / "wf.yaml").write_text(json.dumps(document), encoding="utf-8")
        changed_plan = build_workflow_plan(
            [str(tmp_path / "changed" / "input.xyz")], str(tmp_path / "changed" / "wf.yaml")
        )
        assert isinstance(changed_plan, WorkflowV3Plan)
        assert workflow_execution_fingerprint_v3(changed_plan, _context(changed_plan)) == before

    def test_d3_d7_disabled_resource_and_workers_changes_c_unchanged(self, tmp_path: Path) -> None:
        exe = _executable(tmp_path / "orca")
        plan = _mixed_orca_plan(tmp_path, exe)
        before = workflow_execution_fingerprint_v3(plan, _context(plan))
        document = json.loads((tmp_path / "wf.yaml").read_text(encoding="utf-8"))
        document["steps"][2]["params"]["cores_per_task"] = 8
        (tmp_path / "res").mkdir()
        _write_xyz(tmp_path / "res" / "input.xyz")
        (tmp_path / "res" / "wf.yaml").write_text(json.dumps(document), encoding="utf-8")
        changed_plan = build_workflow_plan(
            [str(tmp_path / "res" / "input.xyz")], str(tmp_path / "res" / "wf.yaml")
        )
        assert isinstance(changed_plan, WorkflowV3Plan)
        assert workflow_execution_fingerprint_v3(changed_plan, _context(changed_plan)) == before
        # D7: a disabled confgen's dormant workers are equally inert.
        confgen_doc = {
            "schema": V3,
            "steps": [
                {"id": "s001", "type": "confgen", "inputs": [], "params": {"chains": ["1-2"]}},
                {
                    "id": "s002",
                    "type": "confgen",
                    "enabled": False,
                    "inputs": [],
                    "params": {"chains": ["3-4"], "workers": 7},
                },
            ],
        }
        (tmp_path / "d7").mkdir()
        _write_xyz(tmp_path / "d7" / "input.xyz")
        (tmp_path / "d7" / "wf.yaml").write_text(json.dumps(confgen_doc), encoding="utf-8")
        d7_plan = build_workflow_plan(
            [str(tmp_path / "d7" / "input.xyz")], str(tmp_path / "d7" / "wf.yaml")
        )
        assert isinstance(d7_plan, WorkflowV3Plan)
        baseline_doc = {
            "schema": V3,
            "steps": [
                {"id": "s001", "type": "confgen", "inputs": [], "params": {"chains": ["1-2"]}},
                {
                    "id": "s002",
                    "type": "confgen",
                    "enabled": False,
                    "inputs": [],
                    "params": {"chains": ["3-4"], "workers": 99},
                },
            ],
        }
        (tmp_path / "d7b").mkdir()
        _write_xyz(tmp_path / "d7b" / "input.xyz")
        (tmp_path / "d7b" / "wf.yaml").write_text(json.dumps(baseline_doc), encoding="utf-8")
        d7b_plan = build_workflow_plan(
            [str(tmp_path / "d7b" / "input.xyz")], str(tmp_path / "d7b" / "wf.yaml")
        )
        assert isinstance(d7b_plan, WorkflowV3Plan)
        assert workflow_execution_fingerprint_v3(
            d7_plan, _context(d7_plan)
        ) == workflow_execution_fingerprint_v3(d7b_plan, _context(d7b_plan))

    def test_d4_enabled_calc_missing_executable_fails_closed(self, tmp_path: Path) -> None:
        # Same machine, but the calc step is ENABLED and ORCA does not exist.
        document = _orca_config(tmp_path, Path("/nonexistent/orca"))
        (tmp_path / "enabled").mkdir()
        _write_xyz(tmp_path / "enabled" / "input.xyz")
        (tmp_path / "enabled" / "wf.yaml").write_text(json.dumps(document), encoding="utf-8")
        plan = build_workflow_plan(
            [str(tmp_path / "enabled" / "input.xyz")], str(tmp_path / "enabled" / "wf.yaml")
        )
        assert isinstance(plan, WorkflowV3Plan)
        with pytest.raises(ConfFlowError):
            resolve_execution_context_v3(plan, input_files=plan.input_files)

    def test_d5_enabled_executable_bytes_change_c_changes(self, tmp_path: Path) -> None:
        exe = _executable(tmp_path / "orca")
        plan = _orca_plan(tmp_path, exe)
        before = workflow_execution_fingerprint_v3(plan, _context(plan))
        _executable(exe, "#!/bin/sh\n# rebuilt\nexit 0\n")
        assert workflow_execution_fingerprint_v3(plan, _context(plan)) != before

    def test_d6_disabled_to_enabled_flips_a(self, tmp_path: Path) -> None:
        exe = _executable(tmp_path / "orca")
        disabled_plan = _mixed_orca_plan(tmp_path, exe)
        document = json.loads((tmp_path / "wf.yaml").read_text(encoding="utf-8"))
        document["steps"][2]["enabled"] = True
        document["steps"][2]["params"]["orca_path"] = str(exe)
        (tmp_path / "enabled").mkdir()
        _write_xyz(tmp_path / "enabled" / "input.xyz")
        (tmp_path / "enabled" / "wf.yaml").write_text(json.dumps(document), encoding="utf-8")
        enabled_plan = build_workflow_plan(
            [str(tmp_path / "enabled" / "input.xyz")], str(tmp_path / "enabled" / "wf.yaml")
        )
        assert isinstance(enabled_plan, WorkflowV3Plan)
        assert enabled_plan.definition_fingerprint != disabled_plan.definition_fingerprint
        # and the newly-enabled step's executable is now required and resolved
        context = resolve_execution_context_v3(enabled_plan, input_files=enabled_plan.input_files)
        assert "s003" in dict(context.step_executables)

    def test_dormant_executable_is_never_inspected(self, tmp_path: Path, monkeypatch) -> None:
        import confflow.workflow.execution_context as context_module

        calls: list[str] = []
        original = context_module.resolve_executable_identity

        def _spy(program: str, configured: str, **kwargs: Any):
            calls.append(f"{program}:{configured}")
            return original(program, configured, **kwargs)

        monkeypatch.setattr(context_module, "resolve_executable_identity", _spy)
        plan = _mixed_orca_plan(tmp_path, _executable(tmp_path / "orca"))
        resolve_execution_context_v3(plan, input_files=plan.input_files)
        # Only the enabled calc's executable was inspected; the dormant
        # /nonexistent/orca was never touched.
        assert calls == ["orca:" + str(tmp_path / "orca")]
