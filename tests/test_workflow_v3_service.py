"""R4.5 — V3 integration with ExecutionService, worker and remote (SVC/WK/RM).

The public path reaches exactly one V3 runtime core: the service adapter
dispatches the accepted ``run_v3_workflow`` seam through the public engine,
so local CLI, through-service and worker attempts share state v2, binding v2,
manifest v2 and resume semantics.
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
from pathlib import Path
from typing import Any

import pytest
import yaml

from confflow.application.execution.models import PrepareRequest, RunState
from confflow.application.execution.workflow_adapter import (
    WorkflowRunSpec,
    _load_artifacts,
    _load_stats,
    build_workflow_service,
    executor_identity,
    measure_executable,
    open_control_service,
    step_record_identity,
)
from confflow.config.canonical.execution_versions import CAPABILITIES, VersionCapability
from confflow.config.canonical.schema import WORKFLOW_SCHEMA_VERSION_V3
from confflow.control_worker import HANDOFF_SCHEMA, _canonical_json, run_control_worker
from confflow.core.exceptions import StopRequestedError
from confflow.workflow.engine import run_workflow

pytestmark = [
    pytest.mark.skipif(os.name != "posix", reason="durable service contract requires POSIX"),
    # Hermetic CI: resolve bare "orca"/"g16" defaults against fake executables
    # on PATH (no system QC programs required). Explicit fail-closed spellings
    # never touch PATH and stay fail-closed.
    pytest.mark.usefixtures("fake_qc_executables_on_path"),
]

V3 = "confflow.workflow.v3"


@pytest.fixture(autouse=True)
def _v3_execution_enabled(monkeypatch):
    """Scoped simulation of the final capability flip for public-path tests.

    These tests exercise the real public execution stack (service, worker,
    remote), which is capability-gated. The patch is scoped to this module
    and always restored; the real capability table stays
    ``execute=False`` until the final flip commit.
    """
    monkeypatch.setitem(
        CAPABILITIES,
        WORKFLOW_SCHEMA_VERSION_V3,
        VersionCapability(parse=True, execute=True),
    )


# ---------------------------------------------------------------------------
# Harness
# ---------------------------------------------------------------------------
def _write_xyz(path: Path, note: str = "seed", energy: str | None = None) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    comment = f"{note} E={energy}" if energy is not None else note
    path.write_text(f"1\n{comment}\nH 0 0 0\n", encoding="utf-8")
    return path


class _FakeHandlers:
    """Fake calc/confgen handlers with optional failure injection by step id."""

    def __init__(
        self,
        monkeypatch,
        *,
        fail_calc_ids: set[str] | None = None,
        write_failed: set[str] | None = None,
    ) -> None:
        self.calc_calls: list[dict[str, Any]] = []
        self.confgen_calls: list[dict[str, Any]] = []
        self.fail_calc_ids = fail_calc_ids or set()
        self.write_failed = write_failed or set()
        monkeypatch.setattr("confflow.workflow.v3_runtime._run_calc_step", self._calc)
        monkeypatch.setattr("confflow.workflow.v3_runtime._run_confgen_step", self._confgen)

    def _calc(self, **kwargs: Any):
        self.calc_calls.append(kwargs)
        if Path(kwargs["step_dir"]).name in self.fail_calc_ids:
            raise RuntimeError(f"injected failure for {kwargs['step_name']}")
        step_dir = Path(kwargs["step_dir"])
        output = step_dir / "result.xyz"
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text("1\nfake calc E=-1.0\nH 0 0 0\n", encoding="utf-8")
        if Path(kwargs["step_dir"]).name in self.write_failed:
            (step_dir / "failed.xyz").write_text("1\nfailed\nH 0 0 0\n", encoding="utf-8")

        class _Result:
            output_path = str(output)
            reused_existing = False

        return _Result()

    def _confgen(self, **kwargs: Any):
        self.confgen_calls.append(kwargs)
        output = Path(kwargs["step_dir"]) / "search.xyz"
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text("2\nfake confgen\nH 0 0 0\nH 0 0 1\n", encoding="utf-8")

        class _Result:
            output_path = str(output)
            reused_existing = False
            copied_multi_frame = False

        return _Result()


def _v3_config(
    path: Path,
    *,
    orca_path: str | None = None,
    keyword: str = "HF",
) -> Path:
    document: dict[str, Any] = {
        "schema": V3,
        "steps": [
            {"id": "s001", "type": "confgen", "inputs": [], "params": {"chains": ["1-2"]}},
            {"id": "s002", "type": "calc", "inputs": ["s001"], "params": {"keyword": keyword}},
        ],
    }
    if orca_path is not None:
        document["global"] = {"orca_path": orca_path}
    path.write_text(yaml.safe_dump(document, sort_keys=False), encoding="utf-8")
    return path


def _service_run(
    tmp_path: Path,
    monkeypatch,
    *,
    run_id: str = "v3-svc-run",
    resume: bool = False,
    pause: bool = False,
    cancel: bool = False,
    fail_calc_ids: set[str] | None = None,
    orca_path: str | None = None,
) -> Any:
    """Run one V3 attempt through the real service executor boundary."""
    handlers = _FakeHandlers(monkeypatch, fail_calc_ids=fail_calc_ids)
    input_xyz = _write_xyz(tmp_path / "input.xyz")
    config = _v3_config(tmp_path / "wf.yaml", orca_path=orca_path)
    work = tmp_path / "work"
    pause_file = work / "PAUSE"
    cancel_file = work / "CANCEL"
    if pause:
        pause_file.parent.mkdir(parents=True, exist_ok=True)
        pause_file.touch()
    if cancel:
        cancel_file.parent.mkdir(parents=True, exist_ok=True)
        cancel_file.touch()
    spec = WorkflowRunSpec(
        run_id=run_id,
        input_xyz=(str(input_xyz),),
        config_file=str(config),
        work_dir=str(work),
        resume=resume,
        pause_beacon_file=str(pause_file),
        cancel_beacon_file=str(cancel_file),
    )
    from confflow.application.execution.workflow_adapter import _prepare_request

    service, executor = build_workflow_service(
        spec,
        state_root=tmp_path / "state",
        workflow_runner=run_workflow,
    )
    service.prepare(_prepare_request(spec, executor_identity(service)))
    service.execute(run_id)
    return service, executor, handlers, work, spec


def _queue_remote(
    tmp_path: Path,
    *,
    run_id: str,
    config: Path,
    input_xyz: Path,
    work: Path,
) -> Any:
    """Queue one prepared run on the controller half of the simulated remote."""
    service = open_control_service(tmp_path / "state", identity_executable=sys.executable)
    identity = measure_executable(sys.executable)
    handoff = {
        "content_schema": HANDOFF_SCHEMA,
        "run_id": run_id,
        "workflow_config": {
            "path": str(config),
            "sha256": hashlib.sha256(config.read_bytes()).hexdigest(),
        },
        "tasks": [
            {
                "task_id": "task0",
                "input_xyz": str(input_xyz),
                "work_dir": str(work),
                "sha256": hashlib.sha256(input_xyz.read_bytes()).hexdigest(),
            }
        ],
    }
    handoff_path = tmp_path / f"{run_id}.handoff.json"
    handoff_path.write_bytes(_canonical_json(handoff))
    service.prepare(
        PrepareRequest(
            run_id=run_id,
            idempotency_key=run_id,
            request_digest="a" * 64,
            workflow_config_digest=handoff["workflow_config"]["sha256"],
            input_manifest_digest=hashlib.sha256(handoff_path.read_bytes()).hexdigest(),
            expected_executable_identity=identity,
        )
    )
    assert service.execute(run_id).state is RunState.QUEUED
    return service, handoff_path


def _worker_attempt(
    tmp_path: Path,
    monkeypatch,
    *,
    run_id: str,
    handoff_path: Path,
    config: Path,
    input_xyz: Path,
    work: Path,
    resume: bool = False,
    fail_calc_ids: set[str] | None = None,
) -> RunState:
    """Consume the queued intent on the worker half of the simulated remote.

    The attempt boundary returns None after a successful wait and re-raises
    runner errors; the durable repository projection is the truth either way.
    """
    _FakeHandlers(monkeypatch, fail_calc_ids=fail_calc_ids)
    from confflow.application.execution.sqlite import SQLiteExecutionRepository
    from confflow.application.execution.state_root import StateRoot
    from confflow.worker_attempt import run_worker_attempt

    try:
        run_worker_attempt(
            root=StateRoot.resolve(tmp_path / "state"),
            run_id=run_id,
            staged_config=str(config),
            staged_tasks=[{"input_xyz": str(input_xyz), "work_dir": str(work)}],
            resume=resume,
            workflow_runner=run_workflow,
            service_builder=build_workflow_service,
        )
    except Exception:  # noqa: BLE001 - the durable projection carries the outcome
        pass
    record = SQLiteExecutionRepository(StateRoot.resolve(tmp_path / "state")).read(run_id)
    assert record is not None
    return record.state


def _binding(work: Path) -> dict[str, Any]:
    state = json.loads((work / ".workflow_state.json").read_text(encoding="utf-8"))
    assert state["content_schema"] == "confflow.workflow_state.v2"
    return state["binding"]


# ---------------------------------------------------------------------------
# SVC — local ExecutionService
# ---------------------------------------------------------------------------
class TestService:
    def test_svc1_local_v3_service_run(self, tmp_path: Path, monkeypatch) -> None:
        service, executor, handlers, work, _spec = _service_run(tmp_path, monkeypatch)
        executor.wait()
        assert service.status("v3-svc-run").state is RunState.COMPLETED
        state = json.loads((work / ".workflow_state.json").read_text(encoding="utf-8"))
        assert state["content_schema"] == "confflow.workflow_state.v2"
        assert state["final_status"] == "completed"
        assert {step_id for step_id in state["steps"]} == {"s001", "s002"}
        manifest = json.loads((work / "output_manifest.json").read_text(encoding="utf-8"))
        assert manifest["content_schema"] == "confflow.output_manifest.v2"
        assert [call["step_name"] for call in handlers.calc_calls] == ["s002"]

    def test_svc2_artifact_load_manifest_v2(self, tmp_path: Path, monkeypatch) -> None:
        service, executor, _handlers, work, _spec = _service_run(tmp_path, monkeypatch)
        executor.wait()
        manifest = service.artifacts("v3-svc-run")
        assert manifest.artifacts
        # terminal identity is the stable ID; paths stay relative and safe
        assert {artifact.terminal for artifact in manifest.artifacts} == {"s002"}
        assert all(not artifact.path.startswith("/") for artifact in manifest.artifacts)
        # the strict loader projects the same v2 artifacts
        loaded = _load_artifacts(str(work))
        assert {artifact.terminal for artifact in loaded} == {"s002"}
        assert all(a.content_schema == "confflow.output_manifest.v2" for a in loaded)

    def test_svc3_stats_load_v2(self, tmp_path: Path, monkeypatch) -> None:
        service, executor, _handlers, work, spec = _service_run(tmp_path, monkeypatch)
        executor.wait()
        stats = _load_stats(str(work))
        assert stats is not None
        assert stats["content_schema"] == "confflow.workflow_stats.v2"
        assert [step["id"] for step in stats["steps"]] == ["s001", "s002"]
        # the completed-run attach path validates the v2 stats outputs
        from confflow.application.execution.workflow_adapter import _load_completed_stats

        loaded = _load_completed_stats(service, "v3-svc-run", str(work), spec)
        assert loaded["content_schema"] == "confflow.workflow_stats.v2"

    def test_svc4_callbacks_carry_stable_step_ids(self, tmp_path: Path, monkeypatch) -> None:
        _handlers = _FakeHandlers(monkeypatch)
        input_xyz = _write_xyz(tmp_path / "input.xyz")
        config = _v3_config(tmp_path / "wf.yaml")
        work = tmp_path / "work"
        started: list[str] = []
        spec = WorkflowRunSpec(
            run_id="v3-cb-run",
            input_xyz=(str(input_xyz),),
            config_file=str(config),
            work_dir=str(work),
            step_started_callback=lambda name, _type, _dir: started.append(name),
        )
        from types import SimpleNamespace

        from confflow.application.execution.workflow_adapter import _prepare_request

        service, executor = build_workflow_service(
            spec, state_root=tmp_path / "state", workflow_runner=run_workflow
        )
        service.prepare(_prepare_request(spec, executor_identity(service)))
        service.execute("v3-cb-run")
        executor.wait()
        # start events and durable checkpoint ids both carry the stable ID
        assert started == ["s001", "s002"]
        assert service._repository.read("v3-cb-run").checkpoint.checkpoint_id == (
            "checkpoint.s002.0.completed"
        )
        # the identity accessor: v1 records answer name, v2 records answer id
        assert step_record_identity(SimpleNamespace(name="gen")) == "gen"
        assert step_record_identity(SimpleNamespace(id="s002", name=None)) == "s002"

    def test_svc5_resume_after_failure(self, tmp_path: Path, monkeypatch) -> None:
        service, executor, handlers, work, _spec = _service_run(
            tmp_path, monkeypatch, fail_calc_ids={"s002"}
        )
        with pytest.raises(RuntimeError, match="injected failure"):
            executor.wait()
        assert service.status("v3-svc-run").state is RunState.FAILED
        state_path = work / ".workflow_state.json"

        # a resumed attempt reuses the completed s001 and reruns s002
        _service, executor, _handlers, _work, _spec = _service_run(
            tmp_path,
            monkeypatch,
            run_id="v3-svc-run.resume.1",
            resume=True,
        )
        executor.wait()
        state = json.loads(state_path.read_text(encoding="utf-8"))
        assert state["final_status"] == "completed"
        assert state["steps"]["s001"]["status"] == "completed"
        assert state["steps"]["s002"]["status"] == "completed"

    def test_svc6_rerun_failed_stable_id(self, tmp_path: Path, monkeypatch) -> None:
        """V3 rerun-failed selects by stable id and writes beside steps/<id>."""
        _handlers = _FakeHandlers(monkeypatch, write_failed={"s002"})
        input_xyz = _write_xyz(tmp_path / "input.xyz")
        config = _v3_config(tmp_path / "wf.yaml")
        # the rerun-failed boundary drives the real calc runner; fake it
        from types import SimpleNamespace as _NS

        class _FakeCalcRunner:
            def run(self, request):
                output = Path(request.step_dir) / "result.xyz"
                output.parent.mkdir(parents=True, exist_ok=True)
                output.write_text("1\nrerun E=-2.0\nH 0 0 0\n", encoding="utf-8")
                return _NS(output_path=str(output))

        monkeypatch.setattr("confflow.workflow.rerun_failed.CalcStepRunner", _FakeCalcRunner)
        work = tmp_path / "work"
        spec = WorkflowRunSpec(
            run_id="v3-rerun-run",
            input_xyz=(str(input_xyz),),
            config_file=str(config),
            work_dir=str(work),
        )
        from confflow.application.execution.workflow_adapter import _prepare_request

        service, executor = build_workflow_service(
            spec, state_root=tmp_path / "state", workflow_runner=run_workflow
        )
        service.prepare(_prepare_request(spec, executor_identity(service)))
        service.execute("v3-rerun-run")
        executor.wait()
        from confflow.workflow.rerun_failed import run_rerun_failed

        result = run_rerun_failed(
            step_dir=str(tmp_path / "work" / "steps" / "s002"),
            config_file=str(tmp_path / "wf.yaml"),
            step_ref="s002",
        )
        assert result.step_label == "s002"
        assert Path(result.output_dir).name == "s002_rerun"

    def test_svc7_pause_then_resume(self, tmp_path: Path, monkeypatch) -> None:
        _handlers = _FakeHandlers(monkeypatch)
        input_xyz = _write_xyz(tmp_path / "input.xyz")
        config = _v3_config(tmp_path / "wf.yaml")
        work = tmp_path / "work"
        work.mkdir(parents=True)
        started: list[str] = []

        def on_start(name: str, _type: str, _dir: str) -> None:
            started.append(name)
            if name == "s001":
                (work / "PAUSE").touch()  # pause after s001's checkpoint exists

        from confflow.application.execution.workflow_adapter import _prepare_request

        spec = WorkflowRunSpec(
            run_id="v3-svc-run",
            input_xyz=(str(input_xyz),),
            config_file=str(config),
            work_dir=str(work),
            pause_beacon_file=str(work / "PAUSE"),
            cancel_beacon_file=str(work / "CANCEL"),
            step_started_callback=on_start,
        )
        service, executor = build_workflow_service(
            spec, state_root=tmp_path / "state", workflow_runner=run_workflow
        )
        service.prepare(_prepare_request(spec, executor_identity(service)))
        service.execute("v3-svc-run")
        with pytest.raises(StopRequestedError):
            executor.wait()
        assert service.status("v3-svc-run").state is RunState.PAUSED

        (work / "PAUSE").unlink()
        # resuming the paused run completes it through the same service
        resume_spec = WorkflowRunSpec(
            run_id="v3-svc-run",
            input_xyz=(str(input_xyz),),
            config_file=str(config),
            work_dir=str(work),
            resume=True,
            cancel_beacon_file=str(work / "CANCEL"),
            step_started_callback=on_start,
        )
        service2, executor2 = build_workflow_service(
            resume_spec, state_root=tmp_path / "state", workflow_runner=run_workflow
        )
        service2.prepare(_prepare_request(resume_spec, executor_identity(service2)))
        snapshot = service2.resume("v3-svc-run")
        assert snapshot.state is RunState.QUEUED
        service2.execute("v3-svc-run")
        executor2.wait()
        state = json.loads((work / ".workflow_state.json").read_text(encoding="utf-8"))
        assert state["final_status"] == "completed"
        # s001 was never re-executed after the resume
        assert started == ["s001", "s002"]

    def test_svc8_v2_service_unchanged(self, tmp_path: Path, monkeypatch) -> None:
        """The V2 config path keeps manifest v1, state v1 and name identity."""
        from confflow.contract import OUTPUT_MANIFEST_SCHEMA, WORKFLOW_STATE_SCHEMA

        def fake_confgen(step_dir, *args, **kwargs):
            output = Path(step_dir) / "search.xyz"
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text("1\nfake\nH 0 0 0\n", encoding="utf-8")

            class _Result:
                output_path = str(output)
                reused_existing = False
                copied_multi_frame = False

            return _Result()

        monkeypatch.setattr("confflow.workflow.engine._run_confgen_step", fake_confgen)
        input_xyz = _write_xyz(tmp_path / "input.xyz")
        config = tmp_path / "v2.yaml"
        config.write_text(
            yaml.safe_dump(
                {"steps": [{"name": "gen", "type": "confgen", "params": {"chains": "1-2"}}]}
            ),
            encoding="utf-8",
        )
        work = tmp_path / "work"
        spec = WorkflowRunSpec(
            run_id="v2-svc-run",
            input_xyz=(str(input_xyz),),
            config_file=str(config),
            work_dir=str(work),
        )
        from confflow.application.execution.workflow_adapter import _prepare_request

        service, executor = build_workflow_service(
            spec, state_root=tmp_path / "state", workflow_runner=run_workflow
        )
        service.prepare(_prepare_request(spec, executor_identity(service)))
        service.execute("v2-svc-run")
        executor.wait()
        state = json.loads((work / ".workflow_state.json").read_text(encoding="utf-8"))
        assert state["content_schema"] == WORKFLOW_STATE_SCHEMA
        manifest = json.loads((work / "output_manifest.json").read_text(encoding="utf-8"))
        assert manifest["content_schema"] == OUTPUT_MANIFEST_SCHEMA
        assert isinstance(manifest["terminals"], dict)


# ---------------------------------------------------------------------------
# WK — worker path
# ---------------------------------------------------------------------------
class TestWorker:
    def test_wk1_worker_direct_path_preflight_refuses_before_run_paths(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        """Worker direct path fast-fails before ensure_run_paths creates the run layout."""
        from confflow.application.execution.state_root import StateRoot
        from confflow.config.canonical.execution_versions import CAPABILITIES, VersionCapability
        from confflow.core.exceptions import ConfFlowError
        from confflow.worker_attempt import run_worker_attempt

        monkeypatch.setitem(
            CAPABILITIES,
            WORKFLOW_SCHEMA_VERSION_V3,
            VersionCapability(parse=True, execute=False),
        )
        input_xyz = _write_xyz(tmp_path / "input.xyz")
        config = _v3_config(tmp_path / "wf.yaml")
        state_root = tmp_path / "state"
        state_root.mkdir(mode=0o700)
        root = StateRoot.resolve(state_root)

        with pytest.raises(ConfFlowError, match="requires state/binding v2"):
            run_worker_attempt(
                root=root,
                run_id="wk1-refused-run",
                staged_config=str(config),
                staged_tasks=[{"input_xyz": str(input_xyz), "work_dir": str(tmp_path / "work")}],
                resume=False,
                workflow_runner=run_workflow,
                service_builder=build_workflow_service,
            )

        assert not (state_root / "v1" / "runs" / "wk1-refused-run").exists()
        assert not (state_root / "v1" / "repository.sqlite3").exists()
        assert not (tmp_path / "work").exists()

    def test_wk2_wk6_worker_computes_site_c_and_manifest_v2(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        """Prove the durable C is the worker-site resolved fingerprint.

        The controller never provides a value; the worker emits manifest v2.
        """
        exe = tmp_path / "fake_orca"
        exe.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        exe.chmod(0o755)
        config = _v3_config(tmp_path / "wf.yaml", orca_path=str(exe))
        input_xyz = _write_xyz(tmp_path / "input.xyz")
        work = tmp_path / "results" / "task0_confflow_work"
        work.parent.mkdir(parents=True, exist_ok=True)
        service, handoff_path = _queue_remote(
            tmp_path, run_id="wk-run", config=config, input_xyz=input_xyz, work=work
        )
        state = _worker_attempt(
            tmp_path,
            monkeypatch,
            run_id="wk-run",
            handoff_path=handoff_path,
            config=config,
            input_xyz=input_xyz,
            work=work,
        )
        assert state is RunState.COMPLETED
        binding = _binding(work)
        assert binding["schema"] == "confflow.workflow_binding.v2"

        # recompute C at the worker site from the staged inputs
        from confflow.workflow.execution_context import (
            resolve_execution_context_v3,
            workflow_execution_fingerprint_v3,
        )
        from confflow.workflow.plan import WorkflowV3Plan, build_workflow_plan

        plan = build_workflow_plan([str(input_xyz)], str(config))
        assert isinstance(plan, WorkflowV3Plan)
        context = resolve_execution_context_v3(plan, input_files=plan.input_files)
        assert binding["execution_fingerprint"] == workflow_execution_fingerprint_v3(plan, context)
        # the controller identity (this python interpreter) is nowhere in the binding
        controller_sha = measure_executable(sys.executable).sha256
        assert controller_sha not in json.dumps(binding)
        assert service.status("wk-run").state is RunState.COMPLETED
        manifest = json.loads((work / "output_manifest.json").read_text(encoding="utf-8"))
        assert manifest["content_schema"] == "confflow.output_manifest.v2"

    def test_wk3_controller_identity_cannot_override_worker_c(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        """Prove controller-side executable metadata cannot change the binding.

        The worker always resolves its own execution site.
        """
        exe = tmp_path / "fake_orca"
        exe.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        exe.chmod(0o755)
        other = tmp_path / "decoy"
        other.write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
        other.chmod(0o755)
        config = _v3_config(tmp_path / "wf.yaml", orca_path=str(exe))
        input_xyz = _write_xyz(tmp_path / "input.xyz")
        work = tmp_path / "results" / "task0_confflow_work"
        work.parent.mkdir(parents=True, exist_ok=True)
        _queue_remote(tmp_path, run_id="wk3-run", config=config, input_xyz=input_xyz, work=work)
        assert (
            _worker_attempt(
                tmp_path,
                monkeypatch,
                run_id="wk3-run",
                handoff_path=tmp_path / "wk3-run.handoff.json",
                config=config,
                input_xyz=input_xyz,
                work=work,
            )
            is RunState.COMPLETED
        )
        binding_c = _binding(work)["execution_fingerprint"]

        # a decoy binary would produce a different site fingerprint; the
        # durable binding cannot contain it
        from confflow.workflow.execution_context import (
            resolve_execution_context_v3,
            workflow_execution_fingerprint_v3,
        )
        from confflow.workflow.plan import WorkflowV3Plan, build_workflow_plan

        decoy_config = _v3_config(tmp_path / "decoy.yaml", orca_path=str(other))
        decoy_plan = build_workflow_plan([str(input_xyz)], str(decoy_config))
        assert isinstance(decoy_plan, WorkflowV3Plan)
        decoy_c = workflow_execution_fingerprint_v3(
            decoy_plan,
            resolve_execution_context_v3(decoy_plan, input_files=decoy_plan.input_files),
        )
        assert decoy_c != binding_c
        assert _binding(work)["execution_fingerprint"] == binding_c

    def test_wk4_binding_written_before_handler_execution(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        state_seen_at_execution: list[dict[str, Any]] = []

        class _SpyHandlers(_FakeHandlers):
            def _calc(self, **kwargs: Any):
                state_file = Path(kwargs["step_dir"]).parents[1] / ".workflow_state.json"
                state_seen_at_execution.append(json.loads(state_file.read_text(encoding="utf-8")))
                return super()._calc(**kwargs)

        monkeypatch.setattr(
            "confflow.workflow.v3_runtime._run_confgen_step", _FakeHandlers._confgen
        )
        spy = _SpyHandlers(monkeypatch)
        del spy
        input_xyz = _write_xyz(tmp_path / "input.xyz")
        config = _v3_config(tmp_path / "wf.yaml")
        work = tmp_path / "work"
        spec = WorkflowRunSpec(
            run_id="wk4-run",
            input_xyz=(str(input_xyz),),
            config_file=str(config),
            work_dir=str(work),
        )
        from confflow.application.execution.workflow_adapter import _prepare_request

        service, executor = build_workflow_service(
            spec, state_root=tmp_path / "state", workflow_runner=run_workflow
        )
        service.prepare(_prepare_request(spec, executor_identity(service)))
        service.execute("wk4-run")
        executor.wait()
        assert state_seen_at_execution, "calc handler must observe the persisted state"
        for snapshot in state_seen_at_execution:
            assert snapshot["binding"]["schema"] == "confflow.workflow_binding.v2"
            assert snapshot["binding"]["execution_fingerprint"].startswith("sha256:")

    def test_wk5_binding_mismatch_before_mutation(self, tmp_path: Path, monkeypatch) -> None:
        _service, executor, _handlers, _work, _spec = _service_run(tmp_path, monkeypatch)
        executor.wait()
        state_path = tmp_path / "work" / ".workflow_state.json"
        before = state_path.read_bytes()

        from confflow.core.exceptions import ConfFlowError

        # resume with changed executable bytes: C mismatch, zero side effects
        exe = tmp_path / "fake_orca"
        exe.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        exe.chmod(0o755)
        config = _v3_config(tmp_path / "wf2.yaml", orca_path=str(exe))
        exe.write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
        with pytest.raises(ConfFlowError, match="binding mismatch"):
            run_workflow(
                [str(tmp_path / "input.xyz")],
                str(config),
                str(tmp_path / "work"),
                resume=True,
            )
        assert state_path.read_bytes() == before


# ---------------------------------------------------------------------------
# RM — simulated remote (controller + worker on one host)
# ---------------------------------------------------------------------------
@pytest.mark.integration
class TestRemote:
    def _remote_setup(self, tmp_path: Path, monkeypatch=None, *, run_id: str = "rm-run"):
        exe = tmp_path / "fake_orca"
        exe.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        exe.chmod(0o755)
        config = _v3_config(tmp_path / "wf.yaml", orca_path=str(exe))
        input_xyz = _write_xyz(tmp_path / "input.xyz")
        work = tmp_path / "results" / "task0_confflow_work"
        work.parent.mkdir(parents=True, exist_ok=True)
        service, handoff_path = _queue_remote(
            tmp_path, run_id=run_id, config=config, input_xyz=input_xyz, work=work
        )
        return service, handoff_path, config, input_xyz, work, exe

    def test_rm1_remote_v3_basic_run(self, tmp_path: Path, monkeypatch) -> None:
        service, handoff_path, config, input_xyz, work, _exe = self._remote_setup(tmp_path)
        assert (
            _worker_attempt(
                tmp_path,
                monkeypatch,
                run_id="rm-run",
                handoff_path=handoff_path,
                config=config,
                input_xyz=input_xyz,
                work=work,
            )
            is RunState.COMPLETED
        )
        assert service.status("rm-run").state is RunState.COMPLETED
        state = json.loads((work / ".workflow_state.json").read_text(encoding="utf-8"))
        assert state["final_status"] == "completed"
        manifest = json.loads((work / "output_manifest.json").read_text(encoding="utf-8"))
        assert manifest["content_schema"] == "confflow.output_manifest.v2"

    def test_rm2_remote_worker_local_c(self, tmp_path: Path, monkeypatch) -> None:
        """Prove durable BindingV2 in work_dir has C computed from worker executable Y, NOT controller X.

        Simulates controller host having chemistry executable X, while worker
        execution host has chemistry executable Y (different path and hash).
        The durable binding in work_dir records execution_fingerprint C finalized
        at the worker execution site from Y, completely independent of controller's X.
        """
        # 1. Controller execution environment: has executable X
        controller_bin = tmp_path / "controller_bin"
        controller_bin.mkdir(parents=True, exist_ok=True)
        exe_x = controller_bin / "orca"
        exe_x.write_text("#!/bin/sh\n# Controller ORCA X\nexit 0\n", encoding="utf-8")
        exe_x.chmod(0o755)

        # 2. Worker execution environment: has executable Y (different hash and path)
        worker_bin = tmp_path / "worker_bin"
        worker_bin.mkdir(parents=True, exist_ok=True)
        exe_y = worker_bin / "orca"
        exe_y.write_text("#!/bin/sh\n# Worker ORCA Y (distinct binary)\nexit 0\n", encoding="utf-8")
        exe_y.chmod(0o755)

        assert exe_x.read_bytes() != exe_y.read_bytes()
        assert exe_x.resolve() != exe_y.resolve()

        # Config references configured executable 'orca' (resolved via PATH on each host)
        config = _v3_config(tmp_path / "wf.yaml", orca_path="orca")
        input_xyz = _write_xyz(tmp_path / "input.xyz")
        work = tmp_path / "results" / "task0_confflow_work"
        work.parent.mkdir(parents=True, exist_ok=True)

        # Controller resolves its site C based on executable X
        from confflow.workflow.execution_context import (
            resolve_execution_context_v3,
            workflow_execution_fingerprint_v3,
        )
        from confflow.workflow.plan import WorkflowV3Plan, build_workflow_plan

        orig_path = os.environ.get("PATH", "")
        monkeypatch.setenv("PATH", f"{controller_bin}:{orig_path}")

        plan_controller = build_workflow_plan([str(input_xyz)], str(config))
        assert isinstance(plan_controller, WorkflowV3Plan)
        context_controller = resolve_execution_context_v3(
            plan_controller, input_files=plan_controller.input_files
        )
        controller_c = workflow_execution_fingerprint_v3(plan_controller, context_controller)

        # Controller queues the remote run
        service, handoff_path = _queue_remote(
            tmp_path, run_id="rm2-run", config=config, input_xyz=input_xyz, work=work
        )

        # 3. Worker executes the attempt in its own execution environment (PATH pointing to worker_bin)
        monkeypatch.setenv("PATH", f"{worker_bin}:{orig_path}")

        # Compute worker site C from executable Y
        plan_worker = build_workflow_plan([str(input_xyz)], str(config))
        context_worker = resolve_execution_context_v3(
            plan_worker, input_files=plan_worker.input_files
        )
        worker_c = workflow_execution_fingerprint_v3(plan_worker, context_worker)

        # Prove the two site fingerprints are strictly different
        assert controller_c != worker_c

        # Worker attempts and completes the execution
        assert (
            _worker_attempt(
                tmp_path,
                monkeypatch,
                run_id="rm2-run",
                handoff_path=handoff_path,
                config=config,
                input_xyz=input_xyz,
                work=work,
            )
            is RunState.COMPLETED
        )
        assert service.status("rm2-run").state is RunState.COMPLETED

        # 4. Verify durable BindingV2 in work_dir
        binding = _binding(work)
        assert binding["schema"] == "confflow.workflow_binding.v2"

        # C in durable binding MUST match worker_c (computed from Y), and NOT controller_c (from X)
        assert binding["execution_fingerprint"] == worker_c
        assert binding["execution_fingerprint"] != controller_c

        # Controller executable identity (path or hash) is nowhere in the durable binding
        exe_x_sha = hashlib.sha256(exe_x.read_bytes()).hexdigest()
        assert exe_x_sha not in json.dumps(binding)
        assert str(exe_x) not in json.dumps(binding)

    def test_rm3_input_basename_transfer_does_not_move_c(self, tmp_path: Path, monkeypatch) -> None:
        """Prove renamed input bytes during 'transfer' do not move C.

        C is bound to ordered content digests, so identity is unchanged.
        """
        exe = tmp_path / "fake_orca"
        exe.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        exe.chmod(0o755)

        binding_cs: list[str] = []
        for run_id, name in (("rm3-a", "original.xyz"), ("rm3-b", "renamed.xyz")):
            config = _v3_config(tmp_path / f"{run_id}.yaml", orca_path=str(exe))
            input_xyz = _write_xyz(tmp_path / run_id / name, note="same bytes")
            work = tmp_path / "results" / f"{run_id}_confflow_work"
            work.parent.mkdir(parents=True, exist_ok=True)
            _queue_remote(tmp_path, run_id=run_id, config=config, input_xyz=input_xyz, work=work)
            assert (
                _worker_attempt(
                    tmp_path,
                    monkeypatch,
                    run_id=run_id,
                    handoff_path=tmp_path / f"{run_id}.handoff.json",
                    config=config,
                    input_xyz=input_xyz,
                    work=work,
                )
                is RunState.COMPLETED
            )
            binding_cs.append(_binding(work)["execution_fingerprint"])

        assert binding_cs[0] == binding_cs[1]

    def test_rm4_remote_resume_after_failed_step(self, tmp_path: Path, monkeypatch) -> None:
        service, handoff_path, config, input_xyz, work, _exe = self._remote_setup(tmp_path)
        assert (
            _worker_attempt(
                tmp_path,
                monkeypatch,
                run_id="rm-run",
                handoff_path=handoff_path,
                config=config,
                input_xyz=input_xyz,
                work=work,
                fail_calc_ids={"s002"},
            )
            is RunState.FAILED
        )
        assert service.status("rm-run").state is RunState.FAILED
        state_path = work / ".workflow_state.json"
        s001_completed_at = json.loads(state_path.read_text(encoding="utf-8"))["steps"]["s001"][
            "completed_at"
        ]

        # the producer re-queues a resumed attempt over the same work dir
        retry_service, retry_handoff = _queue_remote(
            tmp_path, run_id="rm-run.resume.1", config=config, input_xyz=input_xyz, work=work
        )
        assert (
            _worker_attempt(
                tmp_path,
                monkeypatch,
                run_id="rm-run.resume.1",
                handoff_path=retry_handoff,
                config=config,
                input_xyz=input_xyz,
                work=work,
                resume=True,
            )
            is RunState.COMPLETED
        )
        assert retry_service.status("rm-run.resume.1").state is RunState.COMPLETED
        state = json.loads(state_path.read_text(encoding="utf-8"))
        assert state["final_status"] == "completed"
        # the completed upstream step was reused, not recomputed
        assert state["steps"]["s001"]["completed_at"] == s001_completed_at

    def test_rm5_remote_cancel_before_worker_starts(self, tmp_path: Path, monkeypatch) -> None:
        service, handoff_path, config, input_xyz, work, _exe = self._remote_setup(tmp_path)
        service.cancel("rm-run")
        _FakeHandlers(monkeypatch)
        # the queued-cancel path is owned by run_control_worker directly
        state = run_control_worker(
            state_root=tmp_path / "state",
            run_id="rm-run",
            handoff_path=handoff_path,
            workflow_runner=run_workflow,
        )
        assert state is RunState.CANCELLED
        # PE-C4: no later steps ran; no state, no steps/ layout, no manifest
        assert not (work / ".workflow_state.json").exists()
        assert not (work / "steps").exists()
        assert not (work / "output_manifest.json").exists()

    def test_rm6_result_manifest_and_artifacts_roundtrip(self, tmp_path: Path, monkeypatch) -> None:
        service, handoff_path, config, input_xyz, work, _exe = self._remote_setup(tmp_path)
        assert (
            _worker_attempt(
                tmp_path,
                monkeypatch,
                run_id="rm-run",
                handoff_path=handoff_path,
                config=config,
                input_xyz=input_xyz,
                work=work,
            )
            is RunState.COMPLETED
        )
        manifest = service.artifacts("rm-run")
        assert {artifact.terminal for artifact in manifest.artifacts} == {"s002"}
        for artifact in manifest.artifacts:
            candidate = work / artifact.path
            assert candidate.is_file()
            assert hashlib.sha256(candidate.read_bytes()).hexdigest() == artifact.sha256
            assert candidate.stat().st_size == artifact.size
