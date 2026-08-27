"""Contract tests for the Phase C workflow-to-service adapter."""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from confflow.application.execution import (
    ErrorCode,
    ExecutableIdentity,
    ExecutionLifecycle,
    ExecutionService,
    ExecutionServiceError,
    InMemoryExecutionRepository,
    LaunchRequest,
    PrepareRequest,
    RunState,
    ServiceWorkflowExecutor,
)
from confflow.application.execution.workflow_adapter import (
    FileIdentityVerifier,
    WorkflowRunSpec,
    _load_artifacts,
    _load_stats,
    _resolve_executable,
    build_workflow_service,
    executor_identity,
    run_workflow_through_service,
)
from confflow.contract import OUTPUT_MANIFEST_SCHEMA
from confflow.core.exceptions import StopRequestedError


def _files(tmp_path: Path) -> tuple[Path, Path, Path]:
    input_xyz = tmp_path / "input.xyz"
    input_xyz.write_text("1\ninput\nH 0 0 0\n", encoding="utf-8")
    config = tmp_path / "config.yaml"
    config.write_text("global: {}\nsteps: []\n", encoding="utf-8")
    return input_xyz, config, tmp_path / "work"


def test_adapter_loaders_reject_malformed_manifests_and_stats(tmp_path: Path):
    """Artifact and stats projections fail closed for malformed producer files."""
    work = tmp_path / "work"
    work.mkdir()
    manifest = work / "output_manifest.json"

    manifest.write_text("not-json", encoding="utf-8")
    assert _load_artifacts(str(work)) == ()
    manifest.write_text(json.dumps({"content_schema": "wrong", "terminals": {}}), encoding="utf-8")
    assert _load_artifacts(str(work)) == ()
    manifest.write_text(
        json.dumps({"content_schema": OUTPUT_MANIFEST_SCHEMA, "terminals": []}), encoding="utf-8"
    )
    assert _load_artifacts(str(work)) == ()
    manifest.write_text(
        json.dumps({"content_schema": OUTPUT_MANIFEST_SCHEMA, "terminals": {"step": "bad"}}),
        encoding="utf-8",
    )
    assert _load_artifacts(str(work)) == ()
    manifest.write_text(
        json.dumps({"content_schema": OUTPUT_MANIFEST_SCHEMA, "terminals": {"step": [1]}}),
        encoding="utf-8",
    )
    assert _load_artifacts(str(work)) == ()
    manifest.write_text(
        json.dumps(
            {"content_schema": OUTPUT_MANIFEST_SCHEMA, "terminals": {"step": ["../outside"]}}
        ),
        encoding="utf-8",
    )
    assert _load_artifacts(str(work)) == ()
    manifest.write_text(
        json.dumps({"content_schema": OUTPUT_MANIFEST_SCHEMA, "terminals": {"step": ["missing"]}}),
        encoding="utf-8",
    )
    assert _load_artifacts(str(work)) == ()

    output = work / "nested" / "output.xyz"
    output.parent.mkdir()
    output.write_text("output\n", encoding="utf-8")
    manifest.write_text(
        json.dumps(
            {
                "content_schema": OUTPUT_MANIFEST_SCHEMA,
                "terminals": {"step": ["nested/output.xyz"]},
            }
        ),
        encoding="utf-8",
    )
    artifacts = _load_artifacts(str(work))
    assert len(artifacts) == 1
    assert artifacts[0].path == "nested/output.xyz"

    stats = work / "workflow_stats.json"
    stats.write_text("not-json", encoding="utf-8")
    assert _load_stats(str(work)) is None
    stats.write_text("[]", encoding="utf-8")
    assert _load_stats(str(work)) is None
    stats.write_text(json.dumps({"steps": 1}), encoding="utf-8")
    assert _load_stats(str(work)) == {"steps": 1}


def test_service_executor_launch_idempotency_and_wait_contract(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """The adapter acknowledges one token once and preserves worker errors."""
    input_xyz, config, work = _files(tmp_path)
    spec = WorkflowRunSpec(
        run_id="run-adapter-contract",
        input_xyz=(str(input_xyz),),
        config_file=str(config),
        work_dir=str(work),
    )
    executor = ServiceWorkflowExecutor(spec, lambda **_kwargs: None)
    identity = ExecutableIdentity(sha256="a" * 64)
    request = LaunchRequest("run-adapter-contract", "token-1", 1, None, identity)

    executor._cancelled_tokens.add(request.token)  # noqa: SLF001
    assert executor.ensure_launched(request).cancelled
    executor._cancelled_tokens.clear()  # noqa: SLF001

    class DelayedThread:
        def __init__(self, target, args, *, daemon, name):
            del daemon, name
            self._target = target
            self._args = args

        def start(self):
            """Keep the thread parked so duplicate launch is deterministic."""

        def run(self):
            self._target(*self._args)

    monkeypatch.setattr(
        "confflow.application.execution.workflow_adapter.threading.Thread", DelayedThread
    )
    assert executor.ensure_launched(request).accepted
    assert executor.ensure_launched(request).accepted
    with pytest.raises(TimeoutError, match="did not finish"):
        executor.wait(timeout=0)
    executor._error = ValueError("runner failed")  # noqa: SLF001
    executor._finished.set()  # noqa: SLF001
    with pytest.raises(ValueError, match="runner failed"):
        executor.wait()


def test_service_executor_unbound_worker_fails_closed(tmp_path: Path):
    """A worker cannot run before its service binding is installed."""
    input_xyz, config, work = _files(tmp_path)
    executor = ServiceWorkflowExecutor(
        WorkflowRunSpec("run-unbound", (str(input_xyz),), str(config), str(work)),
        lambda **_kwargs: None,
    )
    request = LaunchRequest(
        "run-unbound", "token-unbound", 1, None, ExecutableIdentity(sha256="b" * 64)
    )
    executor._run(request)  # noqa: SLF001 - direct boundary failure coverage
    with pytest.raises(RuntimeError, match="not bound"):
        executor.wait()


def test_identity_verifier_resolves_relative_and_missing_executables():
    """Executable identity resolution accepts PATH entries and fails closed."""
    verifier = FileIdentityVerifier("python3")
    assert Path(verifier.executable).is_absolute()
    assert Path(_resolve_executable("python3")).is_absolute()
    with pytest.raises(ExecutionServiceError, match="Executable not found"):
        _resolve_executable("confflow-executable-that-does-not-exist")


def test_run_workflow_service_routes_existing_states_and_terminal_errors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """The synchronous facade preserves paused, running and terminal routing."""
    input_xyz, config, work = _files(tmp_path)
    identity = ExecutableIdentity(sha256="f" * 64)

    class StubExecutor:
        _result = {"ok": True}

        def __init__(self, wait_error: BaseException | None = None):
            self.wait_error = wait_error

        def wait(self):
            if self.wait_error is not None:
                raise self.wait_error

    class StubService:
        _identity_verifier = _StaticIdentityVerifier(identity)

        def __init__(self, initial: RunState, final: RunState | None = None):
            self.initial = initial
            self.final = initial if final is None else final

        def prepare(self, _request):
            return SimpleNamespace(state=self.initial)

        def execute(self, _run_id):
            return SimpleNamespace(state=RunState.QUEUED)

        def resume(self, _run_id):
            return SimpleNamespace(state=RunState.QUEUED)

        def status(self, _run_id):
            return SimpleNamespace(state=self.final)

    def invoke(service: StubService, executor: StubExecutor, *, resume: bool = False):
        monkeypatch.setattr(
            "confflow.application.execution.workflow_adapter.build_workflow_service",
            lambda _spec, *, state_root, workflow_runner: (service, executor),
        )
        return run_workflow_through_service(
            input_xyz=[str(input_xyz)],
            config_file=str(config),
            work_dir=str(work),
            state_root=tmp_path / "state",
            run_id="run-routing",
            resume=resume,
            workflow_runner=lambda **_kwargs: {"ignored": True},
        )

    with pytest.raises(ExecutionServiceError) as paused_error:
        invoke(StubService(RunState.PAUSED), StubExecutor())
    assert paused_error.value.code is ErrorCode.INVALID_STATE_TRANSITION

    with pytest.raises(ExecutionServiceError) as running_error:
        invoke(StubService(RunState.RUNNING), StubExecutor())
    assert running_error.value.code is ErrorCode.INVALID_STATE_TRANSITION

    work.mkdir()
    (work / "workflow_stats.json").write_text(json.dumps({"steps": 2}), encoding="utf-8")
    assert invoke(StubService(RunState.COMPLETED), StubExecutor()) == {"steps": 2}

    for terminal in (RunState.FAILED, RunState.CANCELLED):
        with pytest.raises(ExecutionServiceError) as terminal_error:
            invoke(StubService(terminal), StubExecutor())
        assert terminal_error.value.code is ErrorCode.TERMINAL_RUN

    with pytest.raises(StopRequestedError):
        invoke(StubService(RunState.PREPARED, RunState.PAUSED), StubExecutor())

    with pytest.raises(ExecutionServiceError) as ended_error:
        invoke(StubService(RunState.PREPARED, RunState.FAILED), StubExecutor())
    assert ended_error.value.code is ErrorCode.INTERNAL

    with pytest.raises(ExecutionServiceError) as cancelled_error:
        invoke(
            StubService(RunState.PREPARED, RunState.CANCELLED),
            StubExecutor(RuntimeError("transport")),
        )
    assert cancelled_error.value.code is ErrorCode.TERMINAL_RUN


def test_direct_adapter_prestart_cancel_terminal_winner_is_ignored(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """A terminal cancellation winner is stable if the parked worker starts late."""
    input_xyz, config, work = _files(tmp_path)

    class DelayedThread:
        def __init__(self, target, args, *, daemon, name):
            del daemon, name
            self._target = target
            self._args = args

        def start(self):
            """Leave execution parked until the cancellation winner is committed."""

        def run(self):
            self._target(*self._args)

    monkeypatch.setattr(
        "confflow.application.execution.workflow_adapter.threading.Thread", DelayedThread
    )
    spec = WorkflowRunSpec("run-adapter-cancel-winner", (str(input_xyz),), str(config), str(work))
    identity = ExecutableIdentity(sha256="c" * 64)
    executor = ServiceWorkflowExecutor(spec, lambda **_kwargs: pytest.fail("runner must not run"))
    service = ExecutionService(
        repository=InMemoryExecutionRepository(),
        executor=executor,
        identity_verifier=_StaticIdentityVerifier(identity),
    )
    executor.bind(service)
    service.prepare(
        PrepareRequest(
            spec.run_id,
            spec.run_id,
            "a" * 64,
            "b" * 64,
            "c" * 64,
            identity,
        )
    )
    assert service.execute(spec.run_id).state is RunState.QUEUED
    aggregate = service._repository.read(spec.run_id)  # noqa: SLF001
    assert aggregate is not None and aggregate.launch_token is not None
    thread = executor._threads[aggregate.launch_token]  # noqa: SLF001

    assert service.cancel(spec.run_id).state is RunState.QUEUED
    assert ExecutionLifecycle(service, spec.run_id, aggregate.launch_token).cancelled().state is (
        RunState.CANCELLED
    )
    thread.run()
    executor.wait(timeout=1)
    assert service.status(spec.run_id).state is RunState.CANCELLED
    assert executor._error is None  # noqa: SLF001


def test_direct_adapter_started_race_without_cancel_is_reported(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """A stale start callback remains an adapter error when no cancel won."""
    input_xyz, config, work = _files(tmp_path)

    class DelayedThread:
        def __init__(self, target, args, *, daemon, name):
            del daemon, name
            self._target = target
            self._args = args

        def start(self):
            """Keep execution parked while the test commits running."""

        def run(self):
            self._target(*self._args)

    monkeypatch.setattr(
        "confflow.application.execution.workflow_adapter.threading.Thread", DelayedThread
    )
    spec = WorkflowRunSpec("run-adapter-start-invalid", (str(input_xyz),), str(config), str(work))
    identity = ExecutableIdentity(sha256="d" * 64)
    executor = ServiceWorkflowExecutor(spec, lambda **_kwargs: pytest.fail("runner must not run"))
    service = ExecutionService(
        repository=InMemoryExecutionRepository(),
        executor=executor,
        identity_verifier=_StaticIdentityVerifier(identity),
    )
    executor.bind(service)
    service.prepare(
        PrepareRequest(
            spec.run_id,
            spec.run_id,
            "a" * 64,
            "b" * 64,
            "c" * 64,
            identity,
        )
    )
    assert service.execute(spec.run_id).state is RunState.QUEUED
    aggregate = service._repository.read(spec.run_id)  # noqa: SLF001
    assert aggregate is not None and aggregate.launch_token is not None
    token = aggregate.launch_token
    assert ExecutionLifecycle(service, spec.run_id, token).started().state is RunState.RUNNING
    thread = executor._threads[token]  # noqa: SLF001
    thread.run()
    with pytest.raises(ExecutionServiceError) as caught:
        executor.wait()
    assert caught.value.code is ErrorCode.INVALID_STATE_TRANSITION
    assert service.status(spec.run_id).state is RunState.FAILED


def test_direct_adapter_cancel_start_race_ignores_terminal_winner(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """A cancellation callback that wins the start CAS race remains terminal."""
    input_xyz, config, work = _files(tmp_path)

    class DelayedThread:
        def __init__(self, target, args, *, daemon, name):
            del daemon, name
            self._target = target
            self._args = args

        def start(self):
            """Park execution until the race setup is complete."""

        def run(self):
            self._target(*self._args)

    monkeypatch.setattr(
        "confflow.application.execution.workflow_adapter.threading.Thread", DelayedThread
    )
    spec = WorkflowRunSpec(
        "run-adapter-cancel-start-race", (str(input_xyz),), str(config), str(work)
    )
    identity = ExecutableIdentity(sha256="e" * 64)
    executor = ServiceWorkflowExecutor(spec, lambda **_kwargs: pytest.fail("runner must not run"))
    service = ExecutionService(
        repository=InMemoryExecutionRepository(),
        executor=executor,
        identity_verifier=_StaticIdentityVerifier(identity),
    )
    executor.bind(service)
    service.prepare(
        PrepareRequest(
            spec.run_id,
            spec.run_id,
            "a" * 64,
            "b" * 64,
            "c" * 64,
            identity,
        )
    )
    assert service.execute(spec.run_id).state is RunState.QUEUED
    aggregate = service._repository.read(spec.run_id)  # noqa: SLF001
    assert aggregate is not None and aggregate.launch_token is not None
    token = aggregate.launch_token
    original_started = ExecutionLifecycle.started

    def started_after_cancel(lifecycle: ExecutionLifecycle):
        assert service.cancel(spec.run_id).state is RunState.QUEUED
        with pytest.raises(ExecutionServiceError) as caught:
            original_started(lifecycle)
        assert caught.value.code is ErrorCode.INVALID_STATE_TRANSITION
        raise caught.value

    monkeypatch.setattr(
        "confflow.application.execution.workflow_adapter.ExecutionLifecycle.started",
        started_after_cancel,
    )
    thread = executor._threads[token]  # noqa: SLF001
    thread.run()
    executor.wait(timeout=1)
    assert service.status(spec.run_id).state is RunState.CANCELLED
    assert executor._error is None  # noqa: SLF001


def test_direct_adapter_preserves_non_cancel_start_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """Only an invalid-transition start race is cancellation-tolerant."""
    input_xyz, config, work = _files(tmp_path)
    spec = WorkflowRunSpec("run-adapter-start-error", (str(input_xyz),), str(config), str(work))
    identity = ExecutableIdentity(sha256="1" * 64)
    executor = ServiceWorkflowExecutor(spec, lambda **_kwargs: pytest.fail("runner must not run"))
    service = ExecutionService(
        repository=InMemoryExecutionRepository(),
        executor=executor,
        identity_verifier=_StaticIdentityVerifier(identity),
    )
    executor.bind(service)
    service.prepare(
        PrepareRequest(spec.run_id, spec.run_id, "a" * 64, "b" * 64, "c" * 64, identity)
    )

    class DelayedThread:
        def __init__(self, target, args, *, daemon, name):
            del daemon, name
            self._target = target
            self._args = args

        def start(self):
            """Park the worker until the injected start error is ready."""

        def run(self):
            self._target(*self._args)

    monkeypatch.setattr(
        "confflow.application.execution.workflow_adapter.threading.Thread", DelayedThread
    )
    monkeypatch.setattr(
        "confflow.application.execution.workflow_adapter.ExecutionLifecycle.started",
        lambda _lifecycle: (_ for _ in ()).throw(
            ExecutionServiceError(ErrorCode.INTERNAL, "start transport failed")
        ),
    )
    assert service.execute(spec.run_id).state is RunState.QUEUED
    aggregate = service._repository.read(spec.run_id)  # noqa: SLF001
    assert aggregate is not None and aggregate.launch_token is not None
    executor._threads[aggregate.launch_token].run()  # noqa: SLF001
    with pytest.raises(ExecutionServiceError) as caught:
        executor.wait()
    assert caught.value.code is ErrorCode.INTERNAL


def test_direct_adapter_preserves_non_invalid_lifecycle_errors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """A lifecycle callback error remains visible instead of being swallowed."""
    input_xyz, config, work = _files(tmp_path)
    spec = WorkflowRunSpec("run-adapter-lifecycle-error", (str(input_xyz),), str(config), str(work))
    identity = ExecutableIdentity(sha256="2" * 64)

    def runner(**_kwargs):
        raise ValueError("runner failed")

    executor = ServiceWorkflowExecutor(spec, runner)
    service = ExecutionService(
        repository=InMemoryExecutionRepository(),
        executor=executor,
        identity_verifier=_StaticIdentityVerifier(identity),
    )
    executor.bind(service)
    service.prepare(
        PrepareRequest(spec.run_id, spec.run_id, "a" * 64, "b" * 64, "c" * 64, identity)
    )

    class DelayedThread:
        def __init__(self, target, args, *, daemon, name):
            del daemon, name
            self._target = target
            self._args = args

        def start(self):
            """Park the worker until the callback error is injected."""

        def run(self):
            self._target(*self._args)

    monkeypatch.setattr(
        "confflow.application.execution.workflow_adapter.threading.Thread", DelayedThread
    )
    monkeypatch.setattr(
        "confflow.application.execution.workflow_adapter.ExecutionLifecycle.failed",
        lambda _lifecycle, *_args: (_ for _ in ()).throw(
            ExecutionServiceError(ErrorCode.INTERNAL, "failed callback transport")
        ),
    )
    assert service.execute(spec.run_id).state is RunState.QUEUED
    aggregate = service._repository.read(spec.run_id)  # noqa: SLF001
    assert aggregate is not None and aggregate.launch_token is not None
    request = LaunchRequest(spec.run_id, aggregate.launch_token, 1, None, identity)
    executor._run(request)  # noqa: SLF001 - deterministic callback error coverage
    with pytest.raises(ExecutionServiceError) as caught:
        executor.wait()
    assert caught.value.code is ErrorCode.INTERNAL


def test_direct_adapter_stop_requested_preserves_cancel_callback_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """A non-transition cancellation callback error is surfaced to the caller."""
    input_xyz, config, work = _files(tmp_path)
    spec = WorkflowRunSpec(
        "run-adapter-stop-error",
        (str(input_xyz),),
        str(config),
        str(work),
        cancel_beacon_file=str(tmp_path / "CANCEL"),
    )
    identity = ExecutableIdentity(sha256="3" * 64)
    service_holder: dict[str, ExecutionService] = {}

    def runner(**_kwargs):
        service_holder["service"].cancel(spec.run_id)
        raise StopRequestedError("cancel requested")

    executor = ServiceWorkflowExecutor(spec, runner)
    service = ExecutionService(
        repository=InMemoryExecutionRepository(),
        executor=executor,
        identity_verifier=_StaticIdentityVerifier(identity),
    )
    service_holder["service"] = service
    executor.bind(service)
    service.prepare(
        PrepareRequest(spec.run_id, spec.run_id, "a" * 64, "b" * 64, "c" * 64, identity)
    )

    class DelayedThread:
        def __init__(self, target, args, *, daemon, name):
            del daemon, name
            self._target = target
            self._args = args

        def start(self):
            """Park the worker until cancellation callback setup is complete."""

        def run(self):
            self._target(*self._args)

    monkeypatch.setattr(
        "confflow.application.execution.workflow_adapter.threading.Thread", DelayedThread
    )
    monkeypatch.setattr(
        "confflow.application.execution.workflow_adapter.ExecutionLifecycle.cancelled",
        lambda _lifecycle: (_ for _ in ()).throw(
            ExecutionServiceError(ErrorCode.INTERNAL, "cancel callback transport")
        ),
    )
    assert service.execute(spec.run_id).state is RunState.QUEUED
    aggregate = service._repository.read(spec.run_id)  # noqa: SLF001
    assert aggregate is not None and aggregate.launch_token is not None
    executor._run(
        LaunchRequest(spec.run_id, aggregate.launch_token, 1, None, identity)
    )  # noqa: SLF001
    with pytest.raises(ExecutionServiceError) as caught:
        executor.wait()
    assert caught.value.code is ErrorCode.INTERNAL


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

    monkeypatch.setattr(
        "confflow.application.execution.workflow_adapter.threading.Thread", DelayedThread
    )
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
