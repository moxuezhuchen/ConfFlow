"""R4.5 — V3 integration with ExecutionService, worker and remote (SVC/WK/RM), SEALED.

The V4-only runtime cutover retired the V3 execution path: every formal
entry (``build_workflow_service``, ``run_workflow_through_service``,
``run_worker_attempt``) refuses V2/V3 staged configs with
``legacy_workflow_not_executable`` before any persistent side effect. Each
test below preserves its scenario's config construction and proves the
refusal — the runner/builder is never called and no state, steps, or
manifests are written. Direct-engine follow-up legs were dropped
(formal-entry scope).
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
    build_workflow_service,
    executor_identity,
    measure_executable,
    open_control_service,
)
from confflow.config.canonical.execution_versions import CAPABILITIES, VersionCapability
from confflow.config.canonical.schema import WORKFLOW_SCHEMA_VERSION_V3
from confflow.control_worker import HANDOFF_SCHEMA, _canonical_json, run_control_worker
from confflow.core.exceptions import ConfFlowError
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


def _never_runner(calls: list[dict[str, Any]]):
    """Build a workflow runner probe that must never run for a sealed version."""

    def _runner(**kwargs: Any) -> None:
        calls.append(kwargs)
        raise AssertionError("the workflow runner must never run for a sealed version")

    return _runner


def _never_builder(launched: list[Any]):
    """Build a service-builder probe that must never run for a sealed version."""

    def _builder(spec: Any, **kwargs: Any) -> Any:
        launched.append(spec)
        raise AssertionError("the service builder must never run for a sealed version")

    return _builder


def _assert_no_work_traces(work: Path) -> None:
    assert not (work / ".workflow_state.json").exists()
    assert not (work / "output_manifest.json").exists()
    assert not (work / "steps").exists()


def _sealed_service_build(
    tmp_path: Path,
    *,
    spec: WorkflowRunSpec,
    state_root: Path,
    runner_calls: list[dict[str, Any]],
) -> None:
    """Drive one service build; assert the V4 guard refuses with zero side effects."""
    with pytest.raises(ConfFlowError, match="legacy_workflow_not_executable"):
        build_workflow_service(
            spec, state_root=state_root, workflow_runner=_never_runner(runner_calls)
        )
    assert runner_calls == []
    assert not state_root.exists()


def _sealed_worker_attempt(
    tmp_path: Path,
    *,
    run_id: str,
    config: Path,
    input_xyz: Path,
    work: Path,
    resume: bool = False,
) -> Path:
    """Drive one worker attempt; assert the V4 guard refuses with zero side effects."""
    from confflow.application.execution.state_root import StateRoot
    from confflow.worker_attempt import run_worker_attempt

    state_root = tmp_path / "state"
    state_root.mkdir(mode=0o700, exist_ok=True)
    ran: list[dict[str, Any]] = []
    launched: list[Any] = []
    with pytest.raises(ConfFlowError, match="legacy_workflow_not_executable"):
        run_worker_attempt(
            root=StateRoot.resolve(state_root),
            run_id=run_id,
            staged_config=str(config),
            staged_tasks=[{"input_xyz": str(input_xyz), "work_dir": str(work)}],
            resume=resume,
            workflow_runner=_never_runner(ran),
            service_builder=_never_builder(launched),
        )
    assert ran == []
    assert launched == []
    return state_root


# ---------------------------------------------------------------------------
# SVC — local ExecutionService
# ---------------------------------------------------------------------------
class TestService:
    def test_svc1_local_v3_service_run_sealed(self, tmp_path: Path, monkeypatch) -> None:
        """V4 guard seals the run (scenario: local V3 service run, confgen+calc)."""
        handlers = _FakeHandlers(monkeypatch)
        input_xyz = _write_xyz(tmp_path / "input.xyz")
        config = _v3_config(tmp_path / "wf.yaml")
        work = tmp_path / "work"
        spec = WorkflowRunSpec(
            run_id="v3-svc-run",
            input_xyz=(str(input_xyz),),
            config_file=str(config),
            work_dir=str(work),
        )
        calls: list[dict[str, Any]] = []
        _sealed_service_build(
            tmp_path, spec=spec, state_root=tmp_path / "state", runner_calls=calls
        )
        assert handlers.calc_calls == []
        assert handlers.confgen_calls == []
        _assert_no_work_traces(work)

    def test_svc2_artifact_load_manifest_v2_sealed(self, tmp_path: Path) -> None:
        """V4 guard seals the run (scenario: artifact load off manifest v2)."""
        input_xyz = _write_xyz(tmp_path / "input.xyz")
        config = _v3_config(tmp_path / "wf.yaml")
        work = tmp_path / "work"
        spec = WorkflowRunSpec(
            run_id="v3-svc-run",
            input_xyz=(str(input_xyz),),
            config_file=str(config),
            work_dir=str(work),
        )
        calls: list[dict[str, Any]] = []
        _sealed_service_build(
            tmp_path, spec=spec, state_root=tmp_path / "state", runner_calls=calls
        )
        # no manifest is ever published for the loader to project
        _assert_no_work_traces(work)

    def test_svc3_stats_load_v2_sealed(self, tmp_path: Path) -> None:
        """V4 guard seals the run (scenario: stats load off workflow_stats v2)."""
        input_xyz = _write_xyz(tmp_path / "input.xyz")
        config = _v3_config(tmp_path / "wf.yaml")
        work = tmp_path / "work"
        spec = WorkflowRunSpec(
            run_id="v3-svc-run",
            input_xyz=(str(input_xyz),),
            config_file=str(config),
            work_dir=str(work),
        )
        calls: list[dict[str, Any]] = []
        _sealed_service_build(
            tmp_path, spec=spec, state_root=tmp_path / "state", runner_calls=calls
        )
        _assert_no_work_traces(work)

    def test_svc4_callbacks_carry_stable_step_ids_sealed(self, tmp_path: Path) -> None:
        """V4 guard seals the run (scenario: start callbacks with stable step IDs)."""
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
        calls: list[dict[str, Any]] = []
        _sealed_service_build(
            tmp_path, spec=spec, state_root=tmp_path / "state", runner_calls=calls
        )
        # no step ever started, so no callback fired
        assert started == []
        _assert_no_work_traces(work)

    def test_svc5_resume_after_failure_sealed(self, tmp_path: Path, monkeypatch) -> None:
        """V4 guard seals the run (scenario: resume reusing completed s001)."""
        handlers = _FakeHandlers(monkeypatch, fail_calc_ids={"s002"})
        input_xyz = _write_xyz(tmp_path / "input.xyz")
        config = _v3_config(tmp_path / "wf.yaml")
        work = tmp_path / "work"
        spec = WorkflowRunSpec(
            run_id="v3-svc-run.resume.1",
            input_xyz=(str(input_xyz),),
            config_file=str(config),
            work_dir=str(work),
            resume=True,
        )
        calls: list[dict[str, Any]] = []
        _sealed_service_build(
            tmp_path, spec=spec, state_root=tmp_path / "state", runner_calls=calls
        )
        assert handlers.calc_calls == []
        _assert_no_work_traces(work)

    def test_svc6_rerun_failed_stable_id_sealed(self, tmp_path: Path) -> None:
        """V4 guard seals the run (scenario: rerun-failed by stable ID).

        The ``run_rerun_failed`` leg is dropped: no work layout ever exists
        for a rerun to address.
        """
        input_xyz = _write_xyz(tmp_path / "input.xyz")
        config = _v3_config(tmp_path / "wf.yaml")
        work = tmp_path / "work"
        spec = WorkflowRunSpec(
            run_id="v3-rerun-run",
            input_xyz=(str(input_xyz),),
            config_file=str(config),
            work_dir=str(work),
        )
        calls: list[dict[str, Any]] = []
        _sealed_service_build(
            tmp_path, spec=spec, state_root=tmp_path / "state", runner_calls=calls
        )
        _assert_no_work_traces(work)

    def test_svc7_pause_then_resume_sealed(self, tmp_path: Path) -> None:
        """V4 guard seals the run (scenario: pause after s001, then resume).

        The resume follow-up leg is dropped: nothing is ever queued, paused,
        or persisted, so there is no paused run to resume.
        """
        input_xyz = _write_xyz(tmp_path / "input.xyz")
        config = _v3_config(tmp_path / "wf.yaml")
        work = tmp_path / "work"
        work.mkdir(parents=True)
        (work / "PAUSE").touch()  # pause beacon present before the attempt
        started: list[str] = []
        spec = WorkflowRunSpec(
            run_id="v3-svc-run",
            input_xyz=(str(input_xyz),),
            config_file=str(config),
            work_dir=str(work),
            pause_beacon_file=str(work / "PAUSE"),
            cancel_beacon_file=str(work / "CANCEL"),
            step_started_callback=lambda name, _type, _dir: started.append(name),
        )
        calls: list[dict[str, Any]] = []
        _sealed_service_build(
            tmp_path, spec=spec, state_root=tmp_path / "state", runner_calls=calls
        )
        assert started == []
        _assert_no_work_traces(work)

    # Cross-file reference: TestPublicControl.test_pe_c1_c2 points at the
    # pre-seal svc7 name; keep the sealed body visible under that name so the
    # sealed inventory stays the single source of truth.
    test_svc7_pause_then_resume = test_svc7_pause_then_resume_sealed

    def test_svc8_v2_service_unchanged_sealed(self, tmp_path: Path, monkeypatch) -> None:
        """V4 guard seals the run (scenario: V2 config path, manifest v1/state v1)."""
        calls: list[dict[str, Any]] = []

        def fake_confgen(step_dir, *args, **kwargs):
            calls.append(step_dir)
            raise AssertionError("the legacy confgen handler must never run")

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
        runner_calls: list[dict[str, Any]] = []
        _sealed_service_build(
            tmp_path, spec=spec, state_root=tmp_path / "state", runner_calls=runner_calls
        )
        assert calls == []
        _assert_no_work_traces(work)


# ---------------------------------------------------------------------------
# WK — worker path
# ---------------------------------------------------------------------------
class TestWorker:
    def test_wk1_worker_direct_path_preflight_refuses_before_run_paths_sealed(
        self, tmp_path: Path
    ) -> None:
        """V4 guard seals the attempt (scenario: worker direct path, V3 staged config)."""
        input_xyz = _write_xyz(tmp_path / "input.xyz")
        config = _v3_config(tmp_path / "wf.yaml")
        work = tmp_path / "work"
        state_root = _sealed_worker_attempt(
            tmp_path, run_id="wk1-refused-run", config=config, input_xyz=input_xyz, work=work
        )

        assert not (state_root / "v1" / "runs" / "wk1-refused-run").exists()
        assert not (state_root / "v1" / "repository.sqlite3").exists()
        assert not work.exists()

    def test_wk2_wk6_worker_computes_site_c_and_manifest_v2_sealed(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        """V4 guard seals the attempt (scenario: worker-site C and manifest v2).

        The site-C computation and manifest legs are dropped: the worker
        preflight refuses the staged config before any site resolution.
        """
        _FakeHandlers(monkeypatch)
        exe = tmp_path / "fake_orca"
        exe.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        exe.chmod(0o755)
        config = _v3_config(tmp_path / "wf.yaml", orca_path=str(exe))
        input_xyz = _write_xyz(tmp_path / "input.xyz")
        work = tmp_path / "results" / "task0_confflow_work"
        work.parent.mkdir(parents=True, exist_ok=True)
        state_root = _sealed_worker_attempt(
            tmp_path, run_id="wk-run", config=config, input_xyz=input_xyz, work=work
        )
        assert not (state_root / "v1" / "runs" / "wk-run").exists()
        _assert_no_work_traces(work)

    def test_wk3_controller_identity_cannot_override_worker_c_sealed(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        """V4 guard seals the attempt (scenario: controller identity vs worker C).

        The decoy-binary comparison legs are dropped: no binding is ever
        written, so no executable identity can leak into one.
        """
        _FakeHandlers(monkeypatch)
        exe = tmp_path / "fake_orca"
        exe.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        exe.chmod(0o755)
        other = tmp_path / "decoy"
        other.write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
        other.chmod(0o755)
        config = _v3_config(tmp_path / "wf.yaml", orca_path=str(exe))
        decoy_config = _v3_config(tmp_path / "decoy.yaml", orca_path=str(other))
        assert decoy_config.read_bytes() != config.read_bytes()
        input_xyz = _write_xyz(tmp_path / "input.xyz")
        work = tmp_path / "results" / "task0_confflow_work"
        work.parent.mkdir(parents=True, exist_ok=True)
        state_root = _sealed_worker_attempt(
            tmp_path, run_id="wk3-run", config=config, input_xyz=input_xyz, work=work
        )
        assert not (state_root / "v1" / "runs" / "wk3-run").exists()
        _assert_no_work_traces(work)

    def test_wk4_binding_written_before_handler_execution_sealed(self, tmp_path: Path) -> None:
        """V4 guard seals the run (scenario: binding persisted before handlers).

        The handler-observed snapshot legs are dropped: the builder refuses
        before any binding is written, so no handler can observe one.
        """
        input_xyz = _write_xyz(tmp_path / "input.xyz")
        config = _v3_config(tmp_path / "wf.yaml")
        work = tmp_path / "work"
        spec = WorkflowRunSpec(
            run_id="wk4-run",
            input_xyz=(str(input_xyz),),
            config_file=str(config),
            work_dir=str(work),
        )
        calls: list[dict[str, Any]] = []
        _sealed_service_build(
            tmp_path, spec=spec, state_root=tmp_path / "state", runner_calls=calls
        )
        _assert_no_work_traces(work)

    def test_wk5_binding_mismatch_before_mutation_sealed(self, tmp_path: Path) -> None:
        """V4 guard seals the run (scenario: resume with changed executable bytes).

        The direct-engine ``run_workflow`` resume leg is dropped: it is not
        a formal entry, so it is out of the sealed scope.
        """
        exe = tmp_path / "fake_orca"
        exe.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        exe.chmod(0o755)
        config = _v3_config(tmp_path / "wf2.yaml", orca_path=str(exe))
        exe.write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
        input_xyz = _write_xyz(tmp_path / "input.xyz")
        work = tmp_path / "work"
        spec = WorkflowRunSpec(
            run_id="wk5-run",
            input_xyz=(str(input_xyz),),
            config_file=str(config),
            work_dir=str(work),
            resume=True,
        )
        calls: list[dict[str, Any]] = []
        _sealed_service_build(
            tmp_path, spec=spec, state_root=tmp_path / "state", runner_calls=calls
        )
        _assert_no_work_traces(work)


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

    def test_rm1_remote_v3_basic_run_sealed(self, tmp_path: Path, monkeypatch) -> None:
        """V4 guard seals the attempt (scenario: basic remote V3 run).

        The controller queue leg still runs; the worker preflight refuses
        the staged config, so the queued intent stays QUEUED and the work
        dir is never touched.
        """
        _FakeHandlers(monkeypatch)
        service, _handoff_path, config, input_xyz, work, _exe = self._remote_setup(tmp_path)
        _sealed_worker_attempt(
            tmp_path, run_id="rm-run", config=config, input_xyz=input_xyz, work=work
        )
        assert service.status("rm-run").state is RunState.QUEUED
        _assert_no_work_traces(work)

    def test_rm2_remote_worker_local_c_sealed(self, tmp_path: Path, monkeypatch) -> None:
        """V4 guard seals the attempt (scenario: controller X vs worker Y).

        The two execution sites still resolve distinct fingerprints, but the
        worker preflight refuses before any durable binding is written, so
        neither fingerprint lands anywhere.
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
        service, _handoff_path = _queue_remote(
            tmp_path, run_id="rm2-run", config=config, input_xyz=input_xyz, work=work
        )

        # 3. Worker site resolves its own C from executable Y
        monkeypatch.setenv("PATH", f"{worker_bin}:{orig_path}")
        plan_worker = build_workflow_plan([str(input_xyz)], str(config))
        context_worker = resolve_execution_context_v3(
            plan_worker, input_files=plan_worker.input_files
        )
        worker_c = workflow_execution_fingerprint_v3(plan_worker, context_worker)

        # Prove the two site fingerprints are strictly different
        assert controller_c != worker_c

        # 4. The worker attempt is refused before either C is persisted
        _FakeHandlers(monkeypatch)
        _sealed_worker_attempt(
            tmp_path, run_id="rm2-run", config=config, input_xyz=input_xyz, work=work
        )
        assert service.status("rm2-run").state is RunState.QUEUED
        _assert_no_work_traces(work)

    def test_rm3_input_basename_transfer_does_not_move_c_sealed(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        """V4 guard seals the attempts (scenario: renamed input bytes in transfer).

        Both transfer legs are queued and both worker attempts are refused;
        no content-bound fingerprint is ever persisted under either name.
        """
        _FakeHandlers(monkeypatch)
        exe = tmp_path / "fake_orca"
        exe.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        exe.chmod(0o755)

        for run_id, name in (("rm3-a", "original.xyz"), ("rm3-b", "renamed.xyz")):
            config = _v3_config(tmp_path / f"{run_id}.yaml", orca_path=str(exe))
            input_xyz = _write_xyz(tmp_path / run_id / name, note="same bytes")
            work = tmp_path / "results" / f"{run_id}_confflow_work"
            work.parent.mkdir(parents=True, exist_ok=True)
            service, _handoff_path = _queue_remote(
                tmp_path, run_id=run_id, config=config, input_xyz=input_xyz, work=work
            )
            _sealed_worker_attempt(
                tmp_path, run_id=run_id, config=config, input_xyz=input_xyz, work=work
            )
            assert service.status(run_id).state is RunState.QUEUED
            _assert_no_work_traces(work)

    def test_rm4_remote_resume_after_failed_step_sealed(self, tmp_path: Path, monkeypatch) -> None:
        """V4 guard seals the attempt (scenario: remote resume after a failed step).

        The retry follow-up leg is dropped: the first attempt is refused, so
        no failed run exists to resume.
        """
        handlers = _FakeHandlers(monkeypatch, fail_calc_ids={"s002"})
        service, _handoff_path, config, input_xyz, work, _exe = self._remote_setup(tmp_path)
        _sealed_worker_attempt(
            tmp_path, run_id="rm-run", config=config, input_xyz=input_xyz, work=work, resume=True
        )
        assert handlers.calc_calls == []
        assert service.status("rm-run").state is RunState.QUEUED
        _assert_no_work_traces(work)

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

    def test_rm6_result_manifest_and_artifacts_roundtrip_sealed(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        """V4 guard seals the attempt (scenario: result manifest/artifacts roundtrip).

        The roundtrip legs are dropped: no manifest or artifact is ever
        written for the controller to fetch.
        """
        _FakeHandlers(monkeypatch)
        service, _handoff_path, config, input_xyz, work, _exe = self._remote_setup(tmp_path)
        _sealed_worker_attempt(
            tmp_path, run_id="rm-run", config=config, input_xyz=input_xyz, work=work
        )
        assert service.status("rm-run").state is RunState.QUEUED
        _assert_no_work_traces(work)
