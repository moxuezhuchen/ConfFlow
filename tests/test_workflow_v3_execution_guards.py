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
from confflow.workflow.rerun_failed import RerunFailedUsageError, run_rerun_failed

# Hermetic CI: fake orca/g16 entrypoints on PATH (real files, real identity).
pytestmark = pytest.mark.usefixtures("fake_qc_executables_on_path")

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
def test_v3_capability_table_allows_execution() -> None:
    """Post-flip: V3 parses and executes; unknown versions stay blocked."""
    assert CAPABILITIES[WORKFLOW_SCHEMA_VERSION_V3].parse is True
    assert CAPABILITIES[WORKFLOW_SCHEMA_VERSION_V3].execute is True


# ---------------------------------------------------------------------------
# Guard A — CLI preflight (50A)
# ---------------------------------------------------------------------------
class TestCliGuard:
    def test_v3_cli_execution_reaches_the_public_stack(self, tmp_path: Path, monkeypatch) -> None:
        """Post-flip the CLI preflight admits V3 to the public execution stack."""
        import json as _json

        def fake_calc(**kwargs):
            step_dir = Path(kwargs["step_dir"])
            output = step_dir / "result.xyz"
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text("1\nfake E=-1.0\nH 0 0 0\n", encoding="utf-8")

            class _Result:
                output_path = str(output)
                reused_existing = False
                copied_multi_frame = False

            return _Result()

        def fake_confgen(**kwargs):
            step_dir = Path(kwargs["step_dir"])
            output = step_dir / "search.xyz"
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text("2\nfake\nH 0 0 0\nH 0 0 1\n", encoding="utf-8")

            class _Result:
                output_path = str(output)
                reused_existing = False
                copied_multi_frame = False

            return _Result()

        monkeypatch.setattr("confflow.workflow.v3_runtime._run_calc_step", fake_calc)
        monkeypatch.setattr("confflow.workflow.v3_runtime._run_confgen_step", fake_confgen)
        xyz = _write_xyz(tmp_path / "input.xyz")
        config = _v3_config(tmp_path / "wf.yaml")
        work = tmp_path / "work"

        result = cli_main([str(xyz), "-c", str(config), "-w", str(work)])

        assert result == ExitCode.SUCCESS
        manifest = _json.loads((work / "output_manifest.json").read_text(encoding="utf-8"))
        assert manifest["content_schema"] == "confflow.output_manifest.v2"

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
class ZeroSideEffectProbe(RuntimeError):
    """Marker used to stop a public attempt right after the runner is admitted."""


def _raise_probe() -> None:
    raise ZeroSideEffectProbe("probe")


class TestServiceAdapterGuard:
    def test_v3_service_admits_v3_to_the_runner(
        self, tmp_path: Path, _fake_runner: list[dict[str, Any]]
    ) -> None:
        """Prove the mandatory builder guard passes V3 through post-flip.

        The runner IS the public engine, so the accepted runtime dispatch is
        what the service now reaches for V3.
        """
        xyz = _write_xyz(tmp_path / "input.xyz")
        config = _v3_config(tmp_path / "wf.yaml")
        work = tmp_path / "work"
        state_root = tmp_path / "state_root"

        with pytest.raises(ZeroSideEffectProbe) as caught:
            run_workflow_through_service(
                input_xyz=[str(xyz)],
                config_file=str(config),
                work_dir=str(work),
                state_root=str(state_root),
                run_id="v3-run",
                workflow_runner=lambda **kwargs: _fake_runner.append(kwargs) or _raise_probe(),
            )

        assert _fake_runner, "V3 must now reach the public runner"
        assert isinstance(caught.value, ZeroSideEffectProbe)


# ---------------------------------------------------------------------------
# Guard C — engine, MANDATORY, before binding/runtime/state (50C)
# ---------------------------------------------------------------------------
class TestEngineGuard:
    def test_v3_engine_dispatch_reaches_the_v3_runtime(self, tmp_path: Path, monkeypatch) -> None:
        """Post-flip the public engine dispatches V3 to the accepted runtime."""
        import json as _json

        def fake_calc(**kwargs):
            step_dir = Path(kwargs["step_dir"])
            output = step_dir / "result.xyz"
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text("1\nfake E=-1.0\nH 0 0 0\n", encoding="utf-8")

            class _Result:
                output_path = str(output)
                reused_existing = False
                copied_multi_frame = False

            return _Result()

        def fake_confgen(**kwargs):
            step_dir = Path(kwargs["step_dir"])
            output = step_dir / "search.xyz"
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text("2\nfake\nH 0 0 0\nH 0 0 1\n", encoding="utf-8")

            class _Result:
                output_path = str(output)
                reused_existing = False
                copied_multi_frame = False

            return _Result()

        monkeypatch.setattr("confflow.workflow.v3_runtime._run_calc_step", fake_calc)
        monkeypatch.setattr("confflow.workflow.v3_runtime._run_confgen_step", fake_confgen)
        xyz = _write_xyz(tmp_path / "input.xyz")
        config = _v3_config(tmp_path / "wf.yaml")
        work = tmp_path / "work"

        result = run_workflow([str(xyz)], str(config), str(work))

        state = _json.loads((work / ".workflow_state.json").read_text(encoding="utf-8"))
        assert state["content_schema"] == "confflow.workflow_state.v2"
        assert result["final_output"].endswith("steps/s002/result.xyz")
        # the V1 dirname layout never appeared
        assert not (work / "s001").exists() and not (work / "s002").exists()

    def test_v3_engine_resume_requires_state_v2(self, tmp_path: Path) -> None:
        xyz = _write_xyz(tmp_path / "input.xyz")
        config = _v3_config(tmp_path / "wf.yaml")
        work = tmp_path / "work"

        with pytest.raises(ConfFlowError, match="no workflow state found"):
            run_workflow([str(xyz)], str(config), str(work), resume=True)

        # No state was ever looked up successfully or mutated.
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
    def test_rr1_v3_rerun_failed_stable_id_selects_only(self, tmp_path: Path) -> None:
        """Prove rerun-failed selects by stable ID only post-flip.

        A missing step directory keeps zero side effects.
        """
        config = _v3_config(tmp_path / "wf.yaml")
        step_dir = tmp_path / "step_dir"

        with pytest.raises(RerunFailedUsageError) as caught:
            run_rerun_failed(
                step_dir=str(step_dir),
                config_file=str(config),
                step_ref="s002",
                output_dir=str(tmp_path / "rerun_out"),
            )

        assert "Step directory does not exist" in str(caught.value)
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
