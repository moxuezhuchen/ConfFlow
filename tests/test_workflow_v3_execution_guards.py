"""R3.5 — V3 execution guards: CLI, service adapter, engine, rerun/resume.

Every guard reuses the one capability table (``CAPABILITIES`` /
``require_executable``); none of them writes ``if version == V3``. The suite
proves each layer rejects a valid RUNNABLE V3 document with **zero side
effects** — no state root, no SQLite, no run paths, no workflow state, no
binding, no directories, no subprocess — while V2 executions keep working.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml

from confflow.application.execution.workflow_adapter import run_workflow_through_service
from confflow.cli import main as cli_main
from confflow.config.canonical import CAPABILITIES, WORKFLOW_SCHEMA_VERSION_V3
from confflow.core.contracts import ExitCode
from confflow.core.exceptions import ConfFlowError
from confflow.workflow.engine import run_workflow
from confflow.workflow.rerun_failed import run_rerun_failed

V3 = "confflow.workflow.v3"


def _write_xyz(path: Path) -> Path:
    path.write_text("1\nseed\nH 0 0 0\n", encoding="utf-8")
    return path


def _v3_config(path: Path) -> Path:
    path.write_text(
        yaml.safe_dump(
            {
                "schema": V3,
                "steps": [
                    {"id": "s001", "type": "confgen", "inputs": [], "params": {"chains": ["1-2"]}},
                    {"id": "s002", "type": "calc", "inputs": ["s001"], "params": {"keyword": "HF"}},
                ],
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    return path


def _v2_config(path: Path) -> Path:
    path.write_text(
        yaml.safe_dump(
            {
                "global": {"iprog": "orca", "itask": "sp", "total_memory": "4GB"},
                "steps": [
                    {"name": "gen", "type": "confgen", "params": {"chains": ["1-2"]}},
                    {"name": "opt", "type": "calc", "inputs": ["gen"], "params": {"keyword": "HF"}},
                ],
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    return path


def _no_runtime_traces(work: Path) -> None:
    assert not work.exists()
    assert not (work / ".confflow_execution").exists()
    assert not list(work.parent.glob("**/.workflow_state.json")) if work.parent.exists() else True
    assert not list(work.parent.glob("**/workflow_binding*")) if work.parent.exists() else True
    for forbidden in ("s001", "s002", "steps", "failed"):
        assert not (work / forbidden).exists() if work.exists() else True


@pytest.fixture()
def _fake_runner(monkeypatch) -> list[dict[str, Any]]:
    calls: list[dict[str, Any]] = []

    def _runner(**kwargs: Any) -> None:
        calls.append(kwargs)
        raise AssertionError("the workflow runner must never be called for V3")

    monkeypatch.setattr(
        "confflow.application.execution.workflow_adapter.default_workflow_runner", _runner
    )
    return calls


# ---------------------------------------------------------------------------
# Capability table (§36 hard gate)
# ---------------------------------------------------------------------------
def test_v3_capability_table_stays_execute_false() -> None:
    assert CAPABILITIES[WORKFLOW_SCHEMA_VERSION_V3].parse is True
    assert CAPABILITIES[WORKFLOW_SCHEMA_VERSION_V3].execute is False


# ---------------------------------------------------------------------------
# Guard A — CLI preflight (50A)
# ---------------------------------------------------------------------------
class TestCliGuard:
    def test_v3_execution_blocked_before_managed_path_side_effects(
        self, tmp_path: Path, capsys
    ) -> None:
        xyz = _write_xyz(tmp_path / "input.xyz")
        config = _v3_config(tmp_path / "wf.yaml")
        work = tmp_path / "work"

        result = cli_main([str(xyz), "-c", str(config), "-w", str(work)])

        assert result == ExitCode.RUNTIME_ERROR
        error = capsys.readouterr().err
        assert "execution requires state/binding v2" in error
        # No lease file, no converted-inputs directory, no state root.
        assert not work.exists()
        assert not list(tmp_path.glob("**/.confflow-work.lock"))

    def test_v3_dry_run_still_allowed(self, tmp_path: Path, capsys) -> None:
        xyz = _write_xyz(tmp_path / "input.xyz")
        config = _v3_config(tmp_path / "wf.yaml")
        assert cli_main(["--dry-run", str(xyz), "-c", str(config), "-w", str(tmp_path / "w")]) == (
            ExitCode.SUCCESS
        )
        assert not (tmp_path / "w").exists()


# ---------------------------------------------------------------------------
# Guard B — service adapter, MANDATORY, before build_workflow_service (50B)
# ---------------------------------------------------------------------------
class TestServiceAdapterGuard:
    def test_v3_blocked_before_state_root_sqlite_or_prepare(
        self, tmp_path: Path, _fake_runner: list[dict[str, Any]]
    ) -> None:
        xyz = _write_xyz(tmp_path / "input.xyz")
        config = _v3_config(tmp_path / "wf.yaml")
        work = tmp_path / "work"
        state_root = tmp_path / "state_root"

        with pytest.raises(ConfFlowError) as caught:
            run_workflow_through_service(
                input_xyz=[str(xyz)],
                config_file=str(config),
                work_dir=str(work),
                state_root=str(state_root),
                run_id="v3-run",
                workflow_runner=lambda **kwargs: _fake_runner.append(kwargs) or {},
            )

        assert "execution requires state/binding v2" in str(caught.value)
        assert not state_root.exists()  # no _ensure_state_root
        assert not state_root.glob("**/*.sqlite") if state_root.exists() else True
        assert not work.exists()  # no run paths, no staging/work dirs
        assert _fake_runner == []  # runner not called

    def test_v3_resume_blocked_before_state_lookup(
        self, tmp_path: Path, _fake_runner: list[dict[str, Any]]
    ) -> None:
        xyz = _write_xyz(tmp_path / "input.xyz")
        config = _v3_config(tmp_path / "wf.yaml")
        state_root = tmp_path / "state_root"

        with pytest.raises(ConfFlowError):
            run_workflow_through_service(
                input_xyz=[str(xyz)],
                config_file=str(config),
                work_dir=str(tmp_path / "work"),
                state_root=str(state_root),
                run_id="v3-resume",
                resume=True,
                workflow_runner=lambda **kwargs: _fake_runner.append(kwargs) or {},
            )

        assert not state_root.exists()
        assert _fake_runner == []


# ---------------------------------------------------------------------------
# Guard C — engine, MANDATORY, before binding/runtime/state (50C)
# ---------------------------------------------------------------------------
class TestEngineGuard:
    def test_v3_plans_then_blocks_before_any_runtime_side_effect(self, tmp_path: Path) -> None:
        xyz = _write_xyz(tmp_path / "input.xyz")
        config = _v3_config(tmp_path / "wf.yaml")
        work = tmp_path / "work"

        with pytest.raises(ConfFlowError) as caught:
            run_workflow([str(xyz)], str(config), str(work))

        # Planning is legal for V3; the guard fires before the binding,
        # resume prevalidation, runtime context, state or step directories.
        assert "execution requires state/binding v2" in str(caught.value)
        _no_runtime_traces(work)

    def test_v3_engine_resume_blocks_before_state_store_load(self, tmp_path: Path) -> None:
        xyz = _write_xyz(tmp_path / "input.xyz")
        config = _v3_config(tmp_path / "wf.yaml")
        work = tmp_path / "work"

        with pytest.raises(ConfFlowError):
            run_workflow([str(xyz)], str(config), str(work), resume=True)

        # No V1 state was ever looked up or mutated.
        _no_runtime_traces(work)

    def test_v2_engine_execution_unchanged(self, tmp_path: Path, monkeypatch) -> None:
        xyz = _write_xyz(tmp_path / "input.xyz")
        config = _v2_config(tmp_path / "wf.yaml")
        work = tmp_path / "work"

        # Stop the engine right after the guard, at the first post-guard
        # side effect, and verify the guard is a no-op for V2: the binding
        # stage is reached (which only V2 executions reach).
        from confflow.workflow import engine as engine_module

        original_binding = engine_module.build_workflow_binding
        observed: list[Any] = []
        monkeypatch.setattr(
            engine_module,
            "build_workflow_binding",
            lambda plan: observed.append(plan) or original_binding(plan),
        )

        def _stop_after_binding(**kwargs: Any) -> dict[str, Any]:
            raise KeyboardInterrupt("V2 path reached runtime initialization")

        monkeypatch.setattr(engine_module, "initialize_runtime_context", _stop_after_binding)
        with pytest.raises(KeyboardInterrupt):
            run_workflow([str(xyz)], str(config), str(work))

        assert observed, "V2 execution must still reach the binding stage"
        assert workflow_source_version_is_v2(observed[0])


def workflow_source_version_is_v2(plan: Any) -> bool:
    return plan.source_version == "confflow.workflow.v2"


# ---------------------------------------------------------------------------
# Guard D — rerun-failed / resume preflight (RR1–RR4)
# ---------------------------------------------------------------------------
class TestRerunGuard:
    def test_rr1_v3_rerun_failed_blocked(self, tmp_path: Path, capsys) -> None:
        config = _v3_config(tmp_path / "wf.yaml")
        step_dir = tmp_path / "step_dir"

        with pytest.raises(ConfFlowError) as caught:
            run_rerun_failed(
                step_dir=str(step_dir),
                config_file=str(config),
                step_ref="s002",
                output_dir=str(tmp_path / "rerun_out"),
            )

        assert "execution requires state/binding v2" in str(caught.value)
        # No rerun output directory was created and the step dir was never
        # probed for artifacts (stable ids never touch V1 state).
        assert not (tmp_path / "rerun_out").exists()
        assert not step_dir.exists()

    def test_rr3_no_v1_state_mutation_for_v3_resume(self, tmp_path: Path) -> None:
        # A pre-existing V1 state file next to a V3 config must be untouched.
        xyz = _write_xyz(tmp_path / "input.xyz")
        config = _v3_config(tmp_path / "wf.yaml")
        work = tmp_path / "work"
        work.mkdir()
        state_file = work / ".workflow_state.json"
        state_file.write_text('{"final_status": "completed"}', encoding="utf-8")
        before = state_file.read_text(encoding="utf-8")

        with pytest.raises(ConfFlowError):
            run_workflow([str(xyz)], str(config), str(work), resume=True)

        assert state_file.read_text(encoding="utf-8") == before


# ---------------------------------------------------------------------------
# Unreadable configs keep historical behaviour (no gate, no change)
# ---------------------------------------------------------------------------
def test_unreadable_config_preflight_is_pass_through(tmp_path: Path) -> None:
    from confflow.config.canonical import require_executable_workflow_file

    missing = tmp_path / "missing.yaml"
    assert require_executable_workflow_file(str(missing)) is None
    broken = tmp_path / "broken.yaml"
    broken.write_text("::: not yaml: [", encoding="utf-8")
    assert require_executable_workflow_file(str(broken)) is None
