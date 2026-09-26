#!/usr/bin/env python3

"""V4-4 cross-process crash matrix, part 2 (worker H).

Extends ``test_v44_crash_matrix.py`` (which that module must not be
rewritten to accommodate) with eight further SIGKILL boundaries through
the same durable protocol, reusing the same SIGKILL-subprocess pattern:
a real OS process dies mid-pipeline, then the same ``run_root`` resumes
from scratch and proves no recompute and no silent loss:

- kill before launch (nothing staged, nothing committed);
- kill during the native run (nothing committed);
- kill after native, before commit (bytes on disk, nothing committed);
- kill after commit, before publication (committed, unpublished);
- kill after publication, before the caller records RunState;
- remote bundle complete, producer dead before import;
- committed response lost on the wire (caller never saw it);
- duplicate/late delivery converges without relaunch.

Children run real batch/transport code with real fake-ORCA binaries in
subprocesses; crashes are real signals, never injected exceptions.
"""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import textwrap
from pathlib import Path
from typing import Any

import pytest

RUNNER = textwrap.dedent("""\
    import os
    import signal
    import sys
    from typing import Any

    RUN_ROOT = sys.argv[1]
    WRAPPER = sys.argv[2]
    HOOK = sys.argv[3]
    STORE_STEP = "s_opt"

    from confflow.domain import FrozenDict
    from confflow.execution import ExecutionBinding
    from confflow.execution.batch import BatchStepExecutor, StepExecutionRequest
    from confflow.execution.environment import EnvironmentMeasurer
    from confflow.execution.process import NativeProcessSupervisor
    from confflow.execution.registry import default_registry
    from confflow.execution.work_item_executor import WorkItemExecutor
    from confflow.persistence.contracts import store_path
    from confflow.persistence.work_items import SqliteWorkItemStore
    from tests.v4._builders import (
        assemble,
        calc_step,
        compile_doc,
        run_inputs,
        structure_set,
        v4_doc,
    )

    doc = v4_doc(
        [calc_step(
            "s_opt",
            program="orca",
            bindings={"structure": {"source": {"run": "structures"}}},
            native={"keyword": "B3LYP Opt"},
            checks=["normal_termination"],
            scheduler={"max_parallel_items": 1},
            resources={"cores_per_item": 1, "memory_per_item": "1GB"},
            execution={"binding_id": "test", "executable": WRAPPER},
        )],
        inputs={"structures": {"kind": "structure", "cardinality": "many"}},
    )
    compiled = compile_doc(doc)
    assert compiled.ok
    assert compiled.plan is not None
    plan = compiled.plan
    assembly = assemble(plan, run_inputs(structures={"structures": structure_set("c0", "c1")}))
    assert assembly.ok
    items = tuple(assembly.for_step("s_opt"))
    planned = plan.steps[0]
    registry = default_registry()
    adapter = registry.resolve_program("orca")
    request = StepExecutionRequest(
        step=planned,
        items=items,
        scientific=planned.scientific,
        scientific_defaults=plan.scientific_defaults,
        adapter=adapter,
        profile=registry.profile_implementation("standard"),
        checks=(registry.check_implementation("normal_termination"),),
        recovery=registry.recovery_implementation("none", adapter=adapter),
        execution_binding=ExecutionBinding(binding_id="t", executable=WRAPPER, env=FrozenDict({})),
        run_root=RUN_ROOT,
        environment=EnvironmentMeasurer().build_environment(WRAPPER, adapter=adapter),
        definition_digest=plan.definition_digest,
        executor_capability="calculation",
    )
    store = SqliteWorkItemStore.open(store_path(RUN_ROOT, STORE_STEP))

    def _die() -> None:
        os.kill(os.getpid(), signal.SIGKILL)

    if HOOK == "before-launch":
        _die()
    elif HOOK == "mid-native":
        from confflow.execution.process import NativeProcessSupervisor as _Sup

        def _killer_submit(self, request):
            _die()

        _Sup.submit = _killer_submit
    elif HOOK == "pre-commit":
        original = store.record_finished

        def _die_before(result: Any, **kwargs: Any) -> Any:
            _die()
            return original(result, **kwargs)

        store.record_finished = _die_before  # type: ignore[method-assign]
    elif HOOK == "after-first-commit":
        calls: list[str] = []
        original = store.record_finished

        def _die_after(result: Any, **kwargs: Any) -> Any:
            value = original(result, **kwargs)
            calls.append(result.work_item_id)
            if len(calls) == 1:
                _die()
            return value

        store.record_finished = _die_after  # type: ignore[method-assign]
    elif HOOK == "pre-publish":
        import confflow.execution.batch as _batch_module

        def _die_publish(*args: Any, **kwargs: Any) -> Any:
            _die()

        _batch_module.publish_step_result = _die_publish
    batch = BatchStepExecutor(WorkItemExecutor()).with_supervisor(NativeProcessSupervisor())
    result = batch.execute_step_resumable(request, store=store, run_root=RUN_ROOT, owner_token="crasher")
    if HOOK == "post-publish":
        _die()
    print(f"CHILD-SURVIVED hook={HOOK} status={result.status}")
    """)

REMOTE_RUNNER = textwrap.dedent("""\
    import os
    import signal
    import sys

    RUN_ROOT, WORKER_ROOT, WRAPPER = sys.argv[1:4]
    from confflow.domain import FrozenDict
    from confflow.execution import ExecutionBinding
    from confflow.execution.batch import StepExecutionRequest
    from confflow.execution.environment import EnvironmentMeasurer
    from confflow.execution.process import NativeProcessSupervisor
    from confflow.execution.registry import default_registry
    from confflow.execution.work_item_executor import ItemExecutionContext
    from confflow.persistence import OwnerIdentity
    from confflow.persistence.contracts import store_path
    from confflow.persistence.recovery import owner_identity_current
    from confflow.persistence.work_items import SqliteWorkItemStore
    from confflow.remote.handoff import read_handoff_envelope, write_handoff_envelope
    from confflow.remote.staging import import_result_artifacts, stage_input_bundle
    from confflow.remote.transport import RemoteTransport
    from confflow.remote.worker import run_worker_envelope
    from tests.v4._builders import (
        assemble, calc_step, compile_doc, run_inputs, structure_set, v4_doc,
    )

    doc = v4_doc(
        [calc_step(
            "s_opt",
            program="orca",
            bindings={"structure": {"source": {"run": "structures"}}},
            native={"keyword": "B3LYP Opt"},
            checks=["normal_termination"],
            scheduler={"max_parallel_items": 1},
            resources={"cores_per_item": 1, "memory_per_item": "1GB"},
            execution={"binding_id": "test", "executable": WRAPPER},
        )],
        inputs={"structures": {"kind": "structure", "cardinality": "many"}},
    )
    plan = compile_doc(doc).plan
    (item,) = assemble(plan, run_inputs(structures={"structures": structure_set("c0")})).for_step("s_opt")
    with SqliteWorkItemStore.open(store_path(RUN_ROOT, "s_opt")) as store:
        planned = plan.steps[0]
        registry = default_registry()
        adapter = registry.resolve_program("orca")
        transport = RemoteTransport(
            run_root=RUN_ROOT, store=store, worker_root=WORKER_ROOT,
            target_default_executable=WRAPPER,
        )
        store.register_item(
            work_item_id=item.id, logical_key=item.logical_key, step_id=item.step_id,
            work_item_digest=item.semantic_digest,
            step_semantic_digest=plan.steps[0].step_semantic_digest,
            environment_digest=transport.probe_environment_digest(
                program="orca", capability="calculation",
                requested_executable=WRAPPER),
            producer_provenance={},
        )
        assert store.claim(item.id, owner=owner_identity_current(owner_token="crasher")) is True
        token = transport.launch_token_for(item, 1)
        context = ItemExecutionContext(
            step_id="s_opt", scientific=planned.scientific,
            scientific_defaults=plan.scientific_defaults, adapter=adapter,
            profile=registry.profile_implementation("standard"),
            checks=(registry.check_implementation("normal_termination"),),
            recovery=registry.recovery_implementation("none", adapter=adapter),
            execution_binding=ExecutionBinding(
                binding_id="t", executable=WRAPPER, env=FrozenDict({})),
            run_root=RUN_ROOT, supervisor=NativeProcessSupervisor(),
            environment=EnvironmentMeasurer().build_environment(WRAPPER, adapter=adapter),
            attempt=1, executor_capability="calculation",
        )
        handoff = transport._build_handoff(  # noqa: SLF001 (delivery seam under test)
            item, context, attempt=1, token=token)
        handoff_path = write_handoff_envelope(
            handoff=handoff, worker_root=WORKER_ROOT, launch_token=token)
        handoff = read_handoff_envelope(path=handoff_path, expected_run_id=handoff.run_id)
        source_files, _checksums = transport._artifact_sources(item)  # noqa: SLF001
        staged = stage_input_bundle(
            manifest=handoff.inputs, run_root=RUN_ROOT, worker_root=WORKER_ROOT,
            launch_token=token, source_files=source_files)
        run_worker_envelope(
            handoff_path=handoff_path, staged_bundle=staged, worker_root=WORKER_ROOT,
            launch_token=token, supervisor=NativeProcessSupervisor())
        # Bundle complete on disk; the producer dies before importing it.
        os.kill(os.getpid(), signal.SIGKILL)
    """)


def _repo_root() -> Path:
    """Return the repository root owning ``confflow``."""
    return Path(__file__).resolve().parent.parent.parent


def _install_wrapper(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path]:
    """Install the counting fake-ORCA wrapper; return ``(wrapper, count)``."""
    fakes = _repo_root() / "tests" / "v4" / "fakes"
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(parents=True, exist_ok=True)
    wrapper = bin_dir / "orca"
    wrapper.write_text(
        "#!/bin/sh\n"
        f'printf \'%s\\n\' "$(basename "$1")" >> "{tmp_path / "native.count"}"\n'
        f'exec "{sys.executable}" "{fakes / "fake_orca.py"}" "$@"\n'
    )
    wrapper.chmod(0o755)
    count = tmp_path / "native.count"
    count.write_text("")
    monkeypatch.setenv("PATH", str(bin_dir) + os.pathsep + os.environ.get("PATH", ""))
    monkeypatch.setenv("FAKE_MODE", "success_opt")
    return wrapper, count


def _launch_count(count: Path) -> int:
    """Return the number of recorded native launches."""
    if not count.exists():
        return 0
    return len([line for line in count.read_text().splitlines() if line.strip()])


def _run_script(
    tmp_path: Path, wrapper: Path, run_root: Path, script: str, tag: str, *args: str
) -> Any:
    """Run *script* text in a subprocess in its own session."""
    path = tmp_path / f"runner_{tag}.py"
    path.write_text(script)
    env = dict(os.environ)
    env["FAKE_MODE"] = "success_opt"
    env["PATH"] = str(wrapper.parent) + os.pathsep + env.get("PATH", "")
    env["PYTHONPATH"] = str(_repo_root()) + os.pathsep + env.get("PYTHONPATH", "")
    return subprocess.run(
        [sys.executable, str(path), str(run_root), *[str(arg) for arg in args]],
        cwd=str(_repo_root()),
        env=env,
        capture_output=True,
        text=True,
        timeout=180,
        start_new_session=True,
    )


def _run_child(run_root: Path, tmp_path: Path, wrapper: Path, hook: str) -> Any:
    """Run the batch crash runner in a subprocess."""
    return _run_script(tmp_path, wrapper, run_root, RUNNER, f"batch_{hook}", str(wrapper), hook)


def _resume(run_root: Path, wrapper: Path) -> Any:
    """Resume the crashed run in-process; return the step result."""
    from confflow.domain import FrozenDict
    from confflow.execution import ExecutionBinding
    from confflow.execution.batch import BatchStepExecutor, StepExecutionRequest
    from confflow.execution.environment import EnvironmentMeasurer
    from confflow.execution.process import NativeProcessSupervisor
    from confflow.execution.registry import default_registry
    from confflow.execution.work_item_executor import WorkItemExecutor
    from confflow.persistence.contracts import store_path
    from confflow.persistence.work_items import SqliteWorkItemStore
    from tests.v4._builders import (
        assemble,
        calc_step,
        compile_doc,
        run_inputs,
        structure_set,
        v4_doc,
    )

    doc = v4_doc(
        [
            calc_step(
                "s_opt",
                program="orca",
                bindings={"structure": {"source": {"run": "structures"}}},
                native={"keyword": "B3LYP Opt"},
                checks=["normal_termination"],
                scheduler={"max_parallel_items": 1},
                resources={"cores_per_item": 1, "memory_per_item": "1GB"},
                execution={"binding_id": "test", "executable": str(wrapper)},
            )
        ],
        inputs={"structures": {"kind": "structure", "cardinality": "many"}},
    )
    compiled = compile_doc(doc)
    assert compiled.ok
    assert compiled.plan is not None
    plan = compiled.plan
    assembly = assemble(plan, run_inputs(structures={"structures": structure_set("c0", "c1")}))
    assert assembly.ok
    items = tuple(assembly.for_step("s_opt"))
    planned = plan.steps[0]
    registry = default_registry()
    adapter = registry.resolve_program("orca")
    request = StepExecutionRequest(
        step=planned,
        items=items,
        scientific=planned.scientific,
        scientific_defaults=plan.scientific_defaults,
        adapter=adapter,
        profile=registry.profile_implementation("standard"),
        checks=(registry.check_implementation("normal_termination"),),
        recovery=registry.recovery_implementation("none", adapter=adapter),
        execution_binding=ExecutionBinding(
            binding_id="t", executable=str(wrapper), env=FrozenDict({})
        ),
        run_root=str(run_root),
        environment=EnvironmentMeasurer().build_environment(str(wrapper), adapter=adapter),
        definition_digest=plan.definition_digest,
        executor_capability="calculation",
    )
    with SqliteWorkItemStore.open(store_path(str(run_root), "s_opt")) as store:
        return (
            BatchStepExecutor(WorkItemExecutor())
            .with_supervisor(NativeProcessSupervisor())
            .execute_step_resumable(
                request, store=store, run_root=str(run_root), owner_token="resumer"
            )
        )


class TestCrashMatrix2BatchBoundaries:
    """SIGKILL at six further batch boundaries, then resume without loss."""

    def test_kill_before_launch_resumes_fresh(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Crash before any launch: resume executes everything once."""
        from confflow.domain.completion import StepStatus

        wrapper, count = _install_wrapper(tmp_path, monkeypatch)
        run_root = tmp_path / "run"
        run_root.mkdir(parents=True, exist_ok=True)
        child = _run_child(run_root, tmp_path, wrapper, "before-launch")
        assert child.returncode == -signal.SIGKILL, child.stderr[-2000:]
        assert _launch_count(count) == 0
        result = _resume(run_root, wrapper)
        assert result.status is StepStatus.COMPLETED
        assert result.summary["completed"] == 2
        assert _launch_count(count) == 2

    def test_kill_during_native_relaunches(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Crash inside the native run: nothing commits; resume runs both."""
        from confflow.domain.completion import StepStatus
        from confflow.persistence.contracts import StoredWorkItemStatus
        from confflow.persistence.contracts import store_path as _store_path
        from confflow.persistence.work_items import SqliteWorkItemStore

        wrapper, count = _install_wrapper(tmp_path, monkeypatch)
        run_root = tmp_path / "run"
        run_root.mkdir(parents=True, exist_ok=True)
        child = _run_child(run_root, tmp_path, wrapper, "mid-native")
        assert child.returncode == -signal.SIGKILL, child.stderr[-2000:]
        assert _launch_count(count) == 0
        with SqliteWorkItemStore.open(_store_path(str(run_root), "s_opt")) as store:
            assert len(store.list_items(StoredWorkItemStatus.COMPLETED)) == 0
        result = _resume(run_root, wrapper)
        assert result.status is StepStatus.COMPLETED
        assert result.summary["completed"] == 2
        assert _launch_count(count) == 2

    def test_kill_after_native_pre_commit_relaunches(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Native bytes on disk but no commit: resume relaunches cleanly."""
        from confflow.domain.completion import StepStatus
        from confflow.persistence.contracts import StoredWorkItemStatus
        from confflow.persistence.contracts import store_path as _store_path
        from confflow.persistence.work_items import SqliteWorkItemStore

        wrapper, count = _install_wrapper(tmp_path, monkeypatch)
        run_root = tmp_path / "run"
        run_root.mkdir(parents=True, exist_ok=True)
        child = _run_child(run_root, tmp_path, wrapper, "pre-commit")
        assert child.returncode == -signal.SIGKILL, child.stderr[-2000:]
        assert _launch_count(count) == 1
        with SqliteWorkItemStore.open(_store_path(str(run_root), "s_opt")) as store:
            assert len(store.list_items(StoredWorkItemStatus.COMPLETED)) == 0
        result = _resume(run_root, wrapper)
        assert result.status is StepStatus.COMPLETED
        assert result.summary["completed"] == 2
        assert _launch_count(count) == 3

    def test_kill_post_commit_pre_publication_reuses(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """All items committed, publication never ran: zero relaunch."""
        from confflow.domain.completion import StepStatus
        from confflow.persistence.contracts import StoredWorkItemStatus
        from confflow.persistence.contracts import store_path as _store_path
        from confflow.persistence.work_items import SqliteWorkItemStore

        wrapper, count = _install_wrapper(tmp_path, monkeypatch)
        run_root = tmp_path / "run"
        run_root.mkdir(parents=True, exist_ok=True)
        child = _run_child(run_root, tmp_path, wrapper, "pre-publish")
        assert child.returncode == -signal.SIGKILL, child.stderr[-2000:]
        assert _launch_count(count) == 2
        with SqliteWorkItemStore.open(_store_path(str(run_root), "s_opt")) as store:
            assert len(store.list_items(StoredWorkItemStatus.COMPLETED)) == 2
        result = _resume(run_root, wrapper)
        assert result.status is StepStatus.COMPLETED
        assert result.summary["completed"] == 2
        assert _launch_count(count) == 2

    def test_kill_post_publication_pre_runstate_reuses(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Publication durable, caller-side RunState never recorded: reuse."""
        from confflow.domain.completion import StepStatus

        wrapper, count = _install_wrapper(tmp_path, monkeypatch)
        run_root = tmp_path / "run"
        run_root.mkdir(parents=True, exist_ok=True)
        child = _run_child(run_root, tmp_path, wrapper, "post-publish")
        assert child.returncode == -signal.SIGKILL, child.stderr[-2000:]
        assert _launch_count(count) == 2
        result = _resume(run_root, wrapper)
        assert result.status is StepStatus.COMPLETED
        assert result.summary["completed"] == 2
        assert _launch_count(count) == 2

    def test_response_loss_after_commit_recovers(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Committed response never reached the caller: resume completes."""
        from confflow.domain.completion import StepStatus

        wrapper, count = _install_wrapper(tmp_path, monkeypatch)
        run_root = tmp_path / "run"
        run_root.mkdir(parents=True, exist_ok=True)
        child = _run_child(run_root, tmp_path, wrapper, "after-first-commit")
        assert child.returncode == -signal.SIGKILL, child.stderr[-2000:]
        assert _launch_count(count) == 1
        result = _resume(run_root, wrapper)
        assert result.status is StepStatus.COMPLETED
        assert result.summary["completed"] == 2
        assert _launch_count(count) == 2
        assert (
            sum(
                1
                for item in result.item_results
                if any(d.code == "reuse_hit" for d in item.diagnostics)
            )
            == 1
        )


class TestCrashMatrix2RemoteAndDelivery:
    """Remote-bundle and duplicate-delivery boundaries across processes."""

    def test_remote_complete_pre_producer_commit_recovers(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Worker bundle on disk, producer SIGKILLed before import: recover."""
        wrapper, count = _install_wrapper(tmp_path, monkeypatch)
        run_root = tmp_path / "run"
        run_root.mkdir(parents=True, exist_ok=True)
        worker_root = tmp_path / "worker"
        child = _run_script(
            tmp_path,
            wrapper,
            run_root,
            REMOTE_RUNNER,
            "remote_bundle",
            str(worker_root),
            str(wrapper),
        )
        assert child.returncode == -signal.SIGKILL, child.stderr[-2000:]
        assert _launch_count(count) == 1
        # The producer restarts with amnesia: a fresh transport recovers the
        # prior bundle for the SAME attempt (same launch token) instead of
        # relaunching native execution (mirrors the in-process half of the
        # delivery protocol, now across a real process boundary).
        from confflow.domain import FrozenDict as _Frozen
        from confflow.execution import ExecutionBinding as _Binding
        from confflow.execution.environment import EnvironmentMeasurer as _Measurer
        from confflow.execution.process import NativeProcessSupervisor as _Supervisor
        from confflow.execution.registry import default_registry as _registry
        from confflow.execution.work_item_executor import ItemExecutionContext as _Context
        from confflow.persistence.contracts import store_path as _store_path
        from confflow.persistence.work_items import SqliteWorkItemStore
        from confflow.remote.transport import RemoteTransport
        from tests.v4._builders import assemble as _assemble
        from tests.v4._builders import calc_step as _calc
        from tests.v4._builders import compile_doc as _compile
        from tests.v4._builders import run_inputs as _run_inputs
        from tests.v4._builders import structure_set as _structures
        from tests.v4._builders import v4_doc as _doc

        doc = _doc(
            [
                _calc(
                    "s_opt",
                    program="orca",
                    bindings={"structure": {"source": {"run": "structures"}}},
                    native={"keyword": "B3LYP Opt"},
                    checks=["normal_termination"],
                    scheduler={"max_parallel_items": 1},
                    resources={"cores_per_item": 1, "memory_per_item": "1GB"},
                    execution={"binding_id": "test", "executable": str(wrapper)},
                )
            ],
            inputs={"structures": {"kind": "structure", "cardinality": "many"}},
        )
        compiled = _compile(doc)
        assert compiled.ok
        assert compiled.plan is not None
        plan = compiled.plan
        assembly = _assemble(plan, _run_inputs(structures={"structures": _structures("c0")}))
        assert assembly.ok
        (item,) = assembly.for_step("s_opt")
        planned = plan.steps[0]
        registry = _registry()
        adapter = registry.resolve_program("orca")
        context = _Context(
            step_id="s_opt",
            scientific=planned.scientific,
            scientific_defaults=plan.scientific_defaults,
            adapter=adapter,
            profile=registry.profile_implementation("standard"),
            checks=(registry.check_implementation("normal_termination"),),
            recovery=registry.recovery_implementation("none", adapter=adapter),
            execution_binding=_Binding(binding_id="t", executable=str(wrapper), env=_Frozen({})),
            run_root=str(run_root),
            supervisor=_Supervisor(),
            environment=_Measurer().build_environment(str(wrapper), adapter=adapter),
            attempt=1,
            executor_capability="calculation",
        )
        with SqliteWorkItemStore.open(_store_path(str(run_root), "s_opt")) as store:
            transport = RemoteTransport(
                run_root=str(run_root),
                store=store,
                worker_root=str(worker_root),
                target_default_executable=str(wrapper),
            )
            recovered = transport.execute(item, context, attempt=1)
            assert recovered.status.value == "completed"
            assert _launch_count(count) == 1

    def test_duplicate_late_delivery_converges(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A late duplicate delivery after success never relaunches native."""
        from confflow.domain.completion import StepStatus

        wrapper, count = _install_wrapper(tmp_path, monkeypatch)
        run_root = tmp_path / "run"
        run_root.mkdir(parents=True, exist_ok=True)
        child = _run_child(run_root, tmp_path, wrapper, "post-publish")
        assert child.returncode == -signal.SIGKILL, child.stderr[-2000:]
        assert _launch_count(count) == 2
        first = _resume(run_root, wrapper)
        assert first.status is StepStatus.COMPLETED
        assert _launch_count(count) == 2
        # The crashed attempt's late duplicate arrives after success.
        second = _resume(run_root, wrapper)
        assert second.status is StepStatus.COMPLETED
        assert second.summary["completed"] == 2
        assert _launch_count(count) == 2
        assert first.summary == second.summary
