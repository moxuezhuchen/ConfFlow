"""Contract tests for the Phase C workflow-to-service adapter."""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path

import pytest

from confflow.application.execution import (
    ExecutableIdentity,
    ExecutionLifecycle,
    ExecutionService,
    InMemoryExecutionRepository,
    PrepareRequest,
    RunState,
    ServiceWorkflowExecutor,
)
from confflow.application.execution.workflow_adapter import (
    WorkflowRunSpec,
    build_workflow_service,
    executor_identity,
    run_workflow_through_service,
)
from confflow.core.exceptions import StopRequestedError


def _files(tmp_path: Path) -> tuple[Path, Path, Path]:
    input_xyz = tmp_path / "input.xyz"
    input_xyz.write_text("1\ninput\nH 0 0 0\n", encoding="utf-8")
    config = tmp_path / "config.yaml"
    config.write_text("global: {}\nsteps: []\n", encoding="utf-8")
    return input_xyz, config, tmp_path / "work"


class _StaticIdentityVerifier:
    def __init__(self, identity: ExecutableIdentity) -> None:
        self._identity = identity

    def measure(self) -> ExecutableIdentity:
        return self._identity


def test_direct_adapter_commits_lifecycle_and_manifest_artifacts(tmp_path: Path):
    input_xyz, config, work = _files(tmp_path)

    def fake_runner(**kwargs):
        output = Path(kwargs["work_dir"]) / "g16_opt" / "output.xyz"
        output.parent.mkdir(parents=True)
        output.write_text("1\noutput\nH 0 0 0\n", encoding="utf-8")
        (Path(kwargs["work_dir"]) / "output_manifest.json").write_text(
            json.dumps(
                {
                    "content_schema": "confflow.output_manifest.v1",
                    "terminals": {"g16_opt": ["g16_opt/output.xyz"]},
                }
            ),
            encoding="utf-8",
        )
        return {"result": "ok"}

    result = run_workflow_through_service(
        input_xyz=[str(input_xyz)],
        config_file=str(config),
        work_dir=str(work),
        state_root=str(tmp_path / "state"),
        run_id="run-adapter-001",
        workflow_runner=fake_runner,
    )

    assert result == {"result": "ok"}
    from confflow.application.execution import SQLiteExecutionRepository, StateRoot

    repository = SQLiteExecutionRepository(StateRoot.resolve(tmp_path / "state"))
    aggregate = repository.read("run-adapter-001")
    assert aggregate is not None
    assert aggregate.state is RunState.COMPLETED
    assert [event.type for event in aggregate.events] == [
        "prepared",
        "queued",
        "running",
        "completed",
    ]
    assert aggregate.artifacts[0].path == "g16_opt/output.xyz"
    assert aggregate.artifacts[0].content_schema == "confflow.output_manifest.v1"


def test_direct_adapter_resume_reuses_idempotency_request_and_checkpoint_boundary(tmp_path: Path):
    input_xyz, config, work = _files(tmp_path)
    calls = {"count": 0}

    def fake_runner(**kwargs):
        calls["count"] += 1
        if calls["count"] == 1:
            from confflow.workflow.state import StepRecord

            kwargs["on_step_status_change"](
                StepRecord(name="step", type="confgen", status="completed")
            )
            raise StopRequestedError("pause")
        return {"resumed": kwargs["resume"]}

    common = dict(
        input_xyz=[str(input_xyz)],
        config_file=str(config),
        work_dir=str(work),
        state_root=str(tmp_path / "state"),
        run_id="run-adapter-002",
        workflow_runner=fake_runner,
    )
    with pytest.raises(StopRequestedError):
        run_workflow_through_service(**common)

    result = run_workflow_through_service(**common, resume=True)
    assert result == {"resumed": True}


def test_direct_adapter_confirms_cancel_before_worker_start(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """A queued cancel is terminalized when its worker starts after the intent."""
    input_xyz, config, work = _files(tmp_path)
    started = threading.Event()

    def fake_runner(**_kwargs):
        started.set()
        raise AssertionError("cancelled pre-start work must not invoke the workflow runner")

    class DelayedThread:
        def __init__(self, target, args, *, daemon, name):
            del daemon, name
            self._target = target
            self._args = args

        def start(self):
            """Leave execution parked until the test explicitly releases it."""

        def run(self):
            self._target(*self._args)

    monkeypatch.setattr("confflow.application.execution.workflow_adapter.threading.Thread", DelayedThread)
    spec = WorkflowRunSpec(
        run_id="run-adapter-cancel-before-start",
        input_xyz=(str(input_xyz),),
        config_file=str(config),
        work_dir=str(work),
    )
    identity = ExecutableIdentity(sha256="d" * 64)
    executor = ServiceWorkflowExecutor(spec, fake_runner)
    service = ExecutionService(
        repository=InMemoryExecutionRepository(),
        executor=executor,
        identity_verifier=_StaticIdentityVerifier(identity),
    )
    executor.bind(service)
    service.prepare(
        PrepareRequest(
            run_id=spec.run_id,
            idempotency_key=spec.run_id,
            request_digest="a" * 64,
            workflow_config_digest="b" * 64,
            input_manifest_digest="c" * 64,
            expected_executable_identity=identity,
        )
    )

    assert service.execute(spec.run_id).state is RunState.QUEUED
    aggregate = service._repository.read(spec.run_id)  # noqa: SLF001
    assert aggregate is not None and aggregate.launch_token is not None
    thread = executor._threads[aggregate.launch_token]  # noqa: SLF001

    assert service.cancel(spec.run_id).state is RunState.QUEUED
    thread.run()
    executor.wait(timeout=1)

    assert not started.is_set()
    assert service.status(spec.run_id).state is RunState.CANCELLED
    aggregate = service._repository.read(spec.run_id)  # noqa: SLF001
    assert aggregate is not None
    assert [event.type for event in aggregate.events] == [
        "prepared",
        "queued",
        "cancel_requested",
        "cancelled",
    ]


def test_direct_adapter_cancel_between_prestart_check_and_started_is_not_failed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """A cancel arriving before the started CAS cannot be misreported as failure."""
    input_xyz, config, work = _files(tmp_path)
    entered_started = threading.Event()
    release_started = threading.Event()

    def fake_runner(**_kwargs):
        raise AssertionError("cancelled pre-start work must not invoke the workflow runner")

    identity = ExecutableIdentity(sha256="e" * 64)
    spec = WorkflowRunSpec(
        run_id="run-adapter-cancel-start-race",
        input_xyz=(str(input_xyz),),
        config_file=str(config),
        work_dir=str(work),
    )
    executor = ServiceWorkflowExecutor(spec, fake_runner)
    service = ExecutionService(
        repository=InMemoryExecutionRepository(),
        executor=executor,
        identity_verifier=_StaticIdentityVerifier(identity),
    )
    executor.bind(service)
    service.prepare(
        PrepareRequest(
            run_id=spec.run_id,
            idempotency_key=spec.run_id,
            request_digest="a" * 64,
            workflow_config_digest="b" * 64,
            input_manifest_digest="c" * 64,
            expected_executable_identity=identity,
        )
    )

    original_started = ExecutionLifecycle.started

    def gated_started(lifecycle):
        entered_started.set()
        assert release_started.wait(2)
        return original_started(lifecycle)

    monkeypatch.setattr(
        "confflow.application.execution.workflow_adapter.ExecutionLifecycle.started",
        gated_started,
    )
    assert service.execute(spec.run_id).state is RunState.QUEUED
    assert entered_started.wait(2)

    assert service.cancel(spec.run_id).state is RunState.QUEUED
    release_started.set()
    executor.wait(timeout=2)

    assert service.status(spec.run_id).state is RunState.CANCELLED
    aggregate = service._repository.read(spec.run_id)  # noqa: SLF001
    assert aggregate is not None
    assert [event.type for event in aggregate.events] == [
        "prepared",
        "queued",
        "cancel_requested",
        "cancelled",
    ]


def test_direct_adapter_active_cancel_uses_derived_cancel_beacon(tmp_path: Path):
    """An active direct run observes the state-root cancellation beacon."""
    input_xyz, config, work = _files(tmp_path)
    work.mkdir(parents=True)
    run_id = "run-adapter-active-cancel"
    expected_beacon = tmp_path / "state" / "v1" / "runs" / run_id / "work" / "CANCEL"
    started = threading.Event()
    beacon_seen = threading.Event()
    release = threading.Event()

    def fake_runner(**kwargs):
        beacon = Path(kwargs["cancel_beacon_file"])
        assert beacon == expected_beacon
        started.set()
        while not beacon.exists():
            time.sleep(0.005)
        beacon_seen.set()
        assert release.wait(2)
        raise StopRequestedError("cancelled")

    spec = WorkflowRunSpec(
        run_id=run_id,
        input_xyz=(str(input_xyz),),
        config_file=str(config),
        work_dir=str(work),
    )
    service, executor = build_workflow_service(
        spec,
        state_root=tmp_path / "state",
        workflow_runner=fake_runner,
    )
    identity = executor_identity(service)
    service.prepare(
        PrepareRequest(
            run_id=run_id,
            idempotency_key=run_id,
            request_digest="a" * 64,
            workflow_config_digest="b" * 64,
            input_manifest_digest="c" * 64,
            expected_executable_identity=identity,
        )
    )

    assert service.execute(run_id).state is RunState.QUEUED
    assert started.wait(2)
    assert service.cancel(run_id).state is RunState.RUNNING
    assert beacon_seen.wait(2)
    release.set()
    with pytest.raises(StopRequestedError):
        executor.wait(timeout=2)

    assert expected_beacon.exists()
    assert service.status(run_id).state is RunState.CANCELLED
