"""P0 review fix — the mandatory service-construction execution guard.

``build_workflow_service`` is the lowest shared service boundary: every caller
(``run_workflow_through_service``, ``_prepare_failed_retry``, the control
worker via ``run_worker_attempt``, any direct API user) must be refused for a
non-executable workflow version BEFORE any persistent side effect — state-root
creation, run paths, SQLite repository, service preparation, runner launch.

The worker attempt boundary is covered too: ``run_worker_attempt`` gates on
the staged config before ``ensure_run_paths`` creates anything.

All guards read the single ``CAPABILITIES``/``require_executable`` truth.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml

from confflow.application.execution.workflow_adapter import build_workflow_service
from confflow.config.canonical import CAPABILITIES, WORKFLOW_SCHEMA_VERSION_V3
from confflow.core.exceptions import ConfFlowError
from confflow.worker_attempt import run_worker_attempt

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


class _NeverRunner:
    def __call__(self, **kwargs: Any) -> dict[str, Any]:  # pragma: no cover - must not run
        raise AssertionError("workflow runner must never be called for a refused version")


def _assert_no_persistent_traces(state_root: Path) -> None:
    # No versioned repository layout, no SQLite, no run paths, no workflow
    # state, no binding, no steps/ — the builder refused before any of it.
    assert not (state_root / "v1").exists()
    assert not (state_root / "v1" / "repository.sqlite3").exists()
    assert not (state_root / "v1" / "runs").exists()
    assert not list(state_root.rglob(".workflow_state.json"))
    assert not list(state_root.rglob("workflow_binding*"))
    assert not list(state_root.rglob("steps"))


def test_capability_table_stays_execute_false() -> None:
    assert CAPABILITIES[WORKFLOW_SCHEMA_VERSION_V3].execute is False


class TestBuilderDirectGuard:
    def test_v3_spec_refused_before_any_persistent_side_effect(self, tmp_path: Path) -> None:
        xyz = _write_xyz(tmp_path / "input.xyz")
        config = _v3_config(tmp_path / "wf.yaml")
        state_root = tmp_path / "state_root"  # does not exist yet
        work_dir = tmp_path / "work"

        from confflow.application.execution.workflow_adapter import WorkflowRunSpec

        spec = WorkflowRunSpec(
            run_id="v3-builder-run",
            input_xyz=(str(xyz),),
            config_file=str(config),
            work_dir=str(work_dir),
        )
        with pytest.raises(ConfFlowError) as caught:
            build_workflow_service(spec, state_root=state_root, workflow_runner=_NeverRunner())

        assert "execution requires state/binding v2" in str(caught.value)
        assert not state_root.exists()  # _ensure_state_root never ran (no mkdir)
        _assert_no_persistent_traces(state_root)
        assert not work_dir.exists()


class TestWorkerDirectPathGuard:
    def test_v3_worker_attempt_refused_before_run_paths(self, tmp_path: Path) -> None:
        """Refuse the worker direct path before it creates run paths.

        The worker path (control_worker → run_worker_attempt →
        build_workflow_service) used to bypass run_workflow_through_service;
        the gate must now fire before run_worker_attempt creates run paths.
        """
        from confflow.application.execution.state_root import StateRoot

        xyz = _write_xyz(tmp_path / "input.xyz")
        config = _v3_config(tmp_path / "wf.yaml")
        state_root = tmp_path / "state_root"
        state_root.mkdir(mode=0o700)  # worker attaches to a prepared root
        root = StateRoot.resolve(state_root)

        launched: list[Any] = []

        def _builder(spec: Any, **kwargs: Any) -> Any:
            launched.append(spec)
            raise AssertionError("service builder must not be reached for a refused version")

        with pytest.raises(ConfFlowError) as caught:
            run_worker_attempt(
                root=root,
                run_id="v3-worker-run",
                staged_config=str(config),
                staged_tasks=[{"input_xyz": str(xyz), "work_dir": str(tmp_path / "work")}],
                resume=False,
                workflow_runner=_NeverRunner(),
                service_builder=_builder,
            )

        assert "execution requires state/binding v2" in str(caught.value)
        assert launched == []  # service never built, prepare never called
        # ensure_run_paths must not have created the run layout either.
        assert not (state_root / "v1" / "runs" / "v3-worker-run").exists()
        assert not (state_root / "v1" / "repository.sqlite3").exists()
        assert not list(state_root.rglob(".workflow_state.json"))
        assert not list(state_root.rglob("steps"))

    def test_v2_worker_attempt_reaches_the_service_builder(self, tmp_path: Path) -> None:
        """V2 worker attempt still reaches the service builder.

        The guard is a no-op for V2, and the builder here is a fake, so no
        real side effects occur.
        """
        from types import SimpleNamespace

        from confflow.application.execution.models import RunState
        from confflow.application.execution.state_root import StateRoot

        xyz = _write_xyz(tmp_path / "input.xyz")
        config = tmp_path / "wf.yaml"
        config.write_text(
            yaml.safe_dump(
                {
                    "global": {"iprog": "orca", "itask": "sp", "total_memory": "4GB"},
                    "steps": [{"name": "gen", "type": "confgen", "params": {"chains": ["1-2"]}}],
                },
                sort_keys=False,
            ),
            encoding="utf-8",
        )
        state_root = tmp_path / "state_root"
        state_root.mkdir(mode=0o700)
        root = StateRoot.resolve(state_root)
        built: list[Any] = []

        class Service:
            def consume_queued_launch(self, run_id: str) -> Any:
                return SimpleNamespace(state=RunState.COMPLETED)

        class Executor:
            def wait(self) -> None:  # pragma: no cover - terminal snapshot short-circuits
                raise AssertionError("terminal consumption must not wait")

        def _builder(spec: Any, **kwargs: Any) -> tuple[Service, Executor]:
            built.append(spec)
            return Service(), Executor()

        state = run_worker_attempt(
            root=root,
            run_id="v2-worker-run",
            staged_config=str(config),
            staged_tasks=[{"input_xyz": str(xyz), "work_dir": str(tmp_path / "work")}],
            resume=False,
            workflow_runner=_NeverRunner(),
            service_builder=_builder,
        )
        assert built, "V2 worker attempt must still reach the service builder"
        assert state is RunState.COMPLETED
