"""Legacy execution guard — the mandatory service-construction fail-closed path.

``build_workflow_service`` is the lowest shared service boundary: every caller
(``run_workflow_through_service``, ``_prepare_failed_retry``, the control
worker via ``run_worker_attempt``, any direct API user) must be refused for a
non-V4 workflow document BEFORE any persistent side effect — state-root
creation, run paths, SQLite repository, service preparation, runner launch.

The V3 document used below was a Workflow V3 wire document; after the
Architecture Diet PR-7 retired that never-released wire, a V3 document is an
unknown schema and is refused by the same V4 authority. The worker attempt
boundary is covered too: ``run_worker_attempt`` gates on the staged config
before ``ensure_run_paths`` creates anything.

All guards read the single V4 document authority
(``confflow.application.v4_entry.require_v4_document_file``); the retired
``execution_versions`` capability table no longer exists.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml

from confflow.application.execution.workflow_adapter import build_workflow_service
from confflow.worker_attempt import run_worker_attempt

V3 = "confflow.workflow.v3"


class ZeroSideEffectProbe(RuntimeError):
    pass


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


class TestBuilderDirectGuard:
    def test_v3_spec_refused_before_the_service_builder(self, tmp_path: Path) -> None:
        """The builder guard refuses a V3 document before any persistent side effect.

        The builder is the mandatory lowest shared boundary; for a
        non-V4 document it raises before creating the state root, run
        paths, SQLite, or service preparation. The runner is never invoked.
        """
        from confflow.core.exceptions import ConfFlowError

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
        with pytest.raises(ConfFlowError, match="legacy_workflow_not_executable"):
            build_workflow_service(spec, state_root=state_root, workflow_runner=_NeverRunner())
        _assert_no_persistent_traces(state_root)
        assert not work_dir.exists()


class TestWorkerDirectPathGuard:
    def test_v3_worker_attempt_refused_before_the_service_builder(self, tmp_path: Path) -> None:
        """The worker preflight refuses a V3 document before ensure_run_paths.

        The preflight fails closed for non-V4 staged configs; the service
        builder is never reached and no run layout is created.
        """
        from confflow.application.execution.state_root import StateRoot
        from confflow.core.exceptions import ConfFlowError

        xyz = _write_xyz(tmp_path / "input.xyz")
        config = _v3_config(tmp_path / "wf.yaml")
        state_root = tmp_path / "state_root"
        state_root.mkdir(mode=0o700)  # worker attaches to a prepared root
        root = StateRoot.resolve(state_root)

        launched: list[Any] = []

        class _ProbeService:
            def consume_queued_launch(self, run_id: str) -> Any:
                raise ZeroSideEffectProbe(f"queued intent consumed for {run_id}")

        class _ProbeExecutor:
            def wait(self) -> None:  # pragma: no cover - never reached
                raise AssertionError("executor must not wait for a probe")

        def _builder(spec: Any, **kwargs: Any) -> Any:
            launched.append(spec)
            return _ProbeService(), _ProbeExecutor()

        with pytest.raises(ConfFlowError, match="legacy_workflow_not_executable"):
            run_worker_attempt(
                root=root,
                run_id="v3-worker-run",
                staged_config=str(config),
                staged_tasks=[{"input_xyz": str(xyz), "work_dir": str(tmp_path / "work")}],
                resume=False,
                workflow_runner=_NeverRunner(),
                service_builder=_builder,
            )

        assert not launched, "V3 worker attempt must never reach the service builder"
        assert not (state_root / "v1" / "runs" / "v3-worker-run").exists()
        assert not list(state_root.rglob(".workflow_state.json"))
        assert not list(state_root.rglob("steps"))

    def test_v2_worker_attempt_refused_before_the_service_builder(self, tmp_path: Path) -> None:
        """The single V4 authority refuses V2 staged configs too.

        The guard is not a no-op for V2 either: only V4 documents proceed to
        the service builder.
        """
        from types import SimpleNamespace

        from confflow.application.execution.models import RunState
        from confflow.application.execution.state_root import StateRoot
        from confflow.core.exceptions import ConfFlowError

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
            def wait(self) -> None:  # pragma: no cover - never reached
                raise AssertionError("refused attempt must not wait")

        def _builder(spec: Any, **kwargs: Any) -> tuple[Service, Executor]:
            built.append(spec)
            return Service(), Executor()

        with pytest.raises(ConfFlowError, match="legacy_workflow_not_executable"):
            run_worker_attempt(
                root=root,
                run_id="v2-worker-run",
                staged_config=str(config),
                staged_tasks=[{"input_xyz": str(xyz), "work_dir": str(tmp_path / "work")}],
                resume=False,
                workflow_runner=_NeverRunner(),
                service_builder=_builder,
            )
        assert not built, "V2 worker attempt must never reach the service builder"
