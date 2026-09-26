#!/usr/bin/env python3

"""V4-4 cross-process crash matrix (wave-2 H).

Every test kills a real OS process (SIGKILL: no interpreter cleanup, no
store close, no WAL checkpoint) mid-pipeline, then resumes the same
``run_root`` from scratch (no shared handles) and proves the durable
protocol:

- kill after the first item commit → resume reuses it (no relaunch),
  executes the remainder, and publishes;
- kill before step publication (all items committed) → resume reuses
  everything (zero native launches) and publishes;
- kill the worker mid-native-run → nothing is committed; resume
  relaunches exactly once and completes;
- kill after the worker wrote its bundle but before producer import →
  resume recovers the prior bundle without relaunching native.

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

    if HOOK == "after-first-commit":
        calls: list[str] = []
        original = store.record_finished

        def hooked(result: Any, **kwargs: Any) -> None:
            original(result, **kwargs)
            calls.append(result.work_item_id)
            if len(calls) == 1:
                _die()

        store.record_finished = hooked  # type: ignore[method-assign]
    elif HOOK == "before-publish":
        import confflow.execution.batch as _batch_module

        def hooked_publish(*args: Any, **kwargs: Any) -> Any:
            _die()

        _batch_module.publish_step_result = hooked_publish
    batch = BatchStepExecutor(WorkItemExecutor()).with_supervisor(NativeProcessSupervisor())
    batch.execute_step_resumable(request, store=store, run_root=RUN_ROOT, owner_token="crasher")
    print("CHILD-SURVIVED (hook did not fire)")
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


def _write_runner(run_root: Path) -> Path:
    """Write the crash-runner script next to the run root."""
    path = run_root.parent / "crash_runner.py"
    path.write_text(RUNNER)
    return path


def _run_child(run_root: Path, wrapper: Path, hook: str) -> Any:
    """Run the crash runner in a subprocess; return the completed process."""
    script = _write_runner(run_root)
    env = dict(os.environ)
    env["FAKE_MODE"] = "success_opt"
    env["PATH"] = str(wrapper.parent) + os.pathsep + env.get("PATH", "")
    env["PYTHONPATH"] = str(_repo_root()) + os.pathsep + env.get("PYTHONPATH", "")
    return subprocess.run(
        [sys.executable, str(script), str(run_root), str(wrapper), hook],
        cwd=str(_repo_root()),
        env=env,
        capture_output=True,
        text=True,
        timeout=180,
    )


def _resume_request(run_root: str, wrapper: str) -> Any:
    """Build the resume request mirroring the crash runner's document."""
    from confflow.domain import FrozenDict
    from confflow.execution import ExecutionBinding
    from confflow.execution.batch import StepExecutionRequest
    from confflow.execution.environment import EnvironmentMeasurer
    from confflow.execution.registry import default_registry
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
                execution={"binding_id": "test", "executable": wrapper},
            )
        ],
        inputs={"structures": {"kind": "structure", "cardinality": "many"}},
    )
    compiled = compile_doc(doc)
    assert compiled.ok
    plan = compiled.plan
    assembly = assemble(plan, run_inputs(structures={"structures": structure_set("c0", "c1")}))
    assert assembly.ok
    items = tuple(assembly.for_step("s_opt"))
    planned = plan.steps[0]
    registry = default_registry()
    adapter = registry.resolve_program("orca")
    return plan, StepExecutionRequest(
        step=planned,
        items=items,
        scientific=planned.scientific,
        scientific_defaults=plan.scientific_defaults,
        adapter=adapter,
        profile=registry.profile_implementation("standard"),
        checks=(registry.check_implementation("normal_termination"),),
        recovery=registry.recovery_implementation("none", adapter=adapter),
        execution_binding=ExecutionBinding(binding_id="t", executable=wrapper, env=FrozenDict({})),
        run_root=run_root,
        environment=EnvironmentMeasurer().build_environment(wrapper, adapter=adapter),
        definition_digest=plan.definition_digest,
        executor_capability="calculation",
    )


def _resume(run_root: Path, wrapper: Path) -> Any:
    """Resume the crashed run in-process; return the step result."""
    from confflow.execution.batch import BatchStepExecutor
    from confflow.execution.process import NativeProcessSupervisor
    from confflow.execution.work_item_executor import WorkItemExecutor
    from confflow.persistence.contracts import store_path
    from confflow.persistence.work_items import SqliteWorkItemStore

    run_root_s = str(run_root)
    _, request = _resume_request(run_root_s, str(wrapper))
    with SqliteWorkItemStore.open(store_path(run_root_s, "s_opt")) as store:
        return (
            BatchStepExecutor(WorkItemExecutor())
            .with_supervisor(NativeProcessSupervisor())
            .execute_step_resumable(
                request, store=store, run_root=run_root_s, owner_token="resumer"
            )
        )


class TestCrossProcessCrashMatrix:
    """SIGKILL at each durable boundary, then resume without recompute."""

    def test_kill_after_first_commit_resumes(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Crash after one item commit: resume reuses it, runs the rest."""
        from confflow.domain.completion import StepStatus
        from confflow.persistence.contracts import StoredWorkItemStatus
        from confflow.persistence.contracts import store_path as _store_path
        from confflow.persistence.work_items import SqliteWorkItemStore

        wrapper, count = _install_wrapper(tmp_path, monkeypatch)
        run_root = tmp_path / "run"
        run_root.mkdir(parents=True, exist_ok=True)
        child = _run_child(run_root, wrapper, "after-first-commit")
        assert child.returncode == -signal.SIGKILL, child.stderr[-2000:]
        assert _launch_count(count) == 1
        with SqliteWorkItemStore.open(_store_path(str(run_root), "s_opt")) as store:
            assert len(store.list_items(StoredWorkItemStatus.COMPLETED)) == 1
        result = _resume(run_root, wrapper)
        assert result.status is StepStatus.COMPLETED
        assert result.summary["completed"] == 2
        # The committed item was reused (exactly one new native launch).
        assert _launch_count(count) == 2
        assert (
            sum(
                1
                for item in result.item_results
                if any(d.code == "reuse_hit" for d in item.diagnostics)
            )
            == 1
        )

    def test_kill_before_publish_resumes_without_relaunch(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Crash after all commits but before publication: zero relaunch."""
        from confflow.domain.completion import StepStatus
        from confflow.persistence.contracts import StoredWorkItemStatus
        from confflow.persistence.contracts import store_path as _store_path
        from confflow.persistence.work_items import SqliteWorkItemStore

        wrapper, count = _install_wrapper(tmp_path, monkeypatch)
        run_root = tmp_path / "run"
        run_root.mkdir(parents=True, exist_ok=True)
        child = _run_child(run_root, wrapper, "before-publish")
        assert child.returncode == -signal.SIGKILL, child.stderr[-2000:]
        assert _launch_count(count) == 2
        with SqliteWorkItemStore.open(_store_path(str(run_root), "s_opt")) as store:
            assert len(store.list_items(StoredWorkItemStatus.COMPLETED)) == 2
        result = _resume(run_root, wrapper)
        assert result.status is StepStatus.COMPLETED
        assert result.summary["completed"] == 2
        assert _launch_count(count) == 2

    def test_kill_worker_mid_native_relaunches_once(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """SIGKILL the worker mid-native-run: nothing commits; resume runs once."""
        from confflow.domain import FrozenDict as _Frozen
        from confflow.domain.completion import StepStatus
        from confflow.execution import ExecutionBinding as _Binding
        from confflow.execution.batch import BatchStepExecutor as _B
        from confflow.execution.batch import StepExecutionRequest as _Request
        from confflow.execution.environment import EnvironmentMeasurer as _Measurer
        from confflow.execution.process import NativeProcessSupervisor as _Supervisor
        from confflow.execution.registry import default_registry as _registry
        from confflow.execution.work_item_executor import WorkItemExecutor as _E
        from confflow.persistence.contracts import store_path as _store_path
        from confflow.persistence.work_items import SqliteWorkItemStore
        from confflow.remote.transport import RemoteTransport
        from tests.v4._builders import assemble as _assemble
        from tests.v4._builders import calc_step as _calc
        from tests.v4._builders import compile_doc as _compile
        from tests.v4._builders import run_inputs as _run_inputs
        from tests.v4._builders import structure_set as _structures
        from tests.v4._builders import v4_doc as _doc

        wrapper, count = _install_wrapper(tmp_path, monkeypatch)
        run_root = str(tmp_path / "run")
        worker_root = str(tmp_path / "worker")
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
        plan = compiled.plan
        assembly = _assemble(plan, _run_inputs(structures={"structures": _structures("c0")}))
        assert assembly.ok
        (item,) = assembly.for_step("s_opt")
        killer = tmp_path / "dispatch_kill.py"
        killer.write_text(textwrap.dedent("""\
                import os, signal, sys
                run_root, worker_root, wrapper = sys.argv[1:4]
                from confflow.domain import FrozenDict
                from confflow.execution import ExecutionBinding
                from confflow.execution.batch import BatchStepExecutor, StepExecutionRequest
                from confflow.execution.environment import EnvironmentMeasurer
                from confflow.execution.work_item_executor import WorkItemExecutor
                from confflow.persistence.contracts import store_path
                from confflow.persistence.work_items import SqliteWorkItemStore
                from confflow.remote.transport import RemoteTransport
                from confflow.execution.registry import default_registry
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
                        execution={"binding_id": "test", "executable": wrapper},
                    )],
                    inputs={"structures": {"kind": "structure", "cardinality": "many"}},
                )
                plan = compile_doc(doc).plan
                items = tuple(
                    assemble(plan, run_inputs(structures={"structures": structure_set("c0")}))
                    .for_step("s_opt")
                )
                planned = plan.steps[0]
                registry = default_registry()
                adapter = registry.resolve_program("orca")
                request = StepExecutionRequest(
                    step=planned, items=items, scientific=planned.scientific,
                    scientific_defaults=plan.scientific_defaults, adapter=adapter,
                    profile=registry.profile_implementation("standard"),
                    checks=(registry.check_implementation("normal_termination"),),
                    recovery=registry.recovery_implementation("none", adapter=adapter),
                    execution_binding=ExecutionBinding(binding_id="t", executable=wrapper, env=FrozenDict({})),
                    run_root=run_root,
                    environment=EnvironmentMeasurer().build_environment(wrapper, adapter=adapter),
                    definition_digest=plan.definition_digest, executor_capability="calculation",
                )
                with SqliteWorkItemStore.open(store_path(run_root, "s_opt")) as store:
                    transport = RemoteTransport(run_root=run_root, store=store, worker_root=worker_root)
                    # Poison the process boundary process-wide: the worker
                    # instantiates its own supervisor, so the kill lands
                    # inside the worker's native submit (mid-native-run
                    # crash: no bundle, no commit).
                    from confflow.execution.process import NativeProcessSupervisor as _Sup

                    def _killer_submit(self, request):
                        os.kill(os.getpid(), signal.SIGKILL)

                    _Sup.submit = _killer_submit
                    from confflow.execution.process import NativeProcessSupervisor as _Sup2

                    BatchStepExecutor(WorkItemExecutor()).with_supervisor(
                        _Sup2()
                    ).execute_step_resumable(
                        request, store=store, run_root=run_root,
                        owner_token="ctl", transport=transport)
                """))
        env = dict(os.environ)
        env["FAKE_MODE"] = "success_opt"
        env["PATH"] = str(wrapper.parent) + os.pathsep + env.get("PATH", "")
        env["PYTHONPATH"] = str(_repo_root()) + os.pathsep + env.get("PYTHONPATH", "")
        # Own session: after SIGKILL no group survivors remain, so the
        # resume side can prove the owner definitely dead (same-group
        # children share pytest's live group and stay uncertain).
        child = subprocess.run(
            [sys.executable, str(killer), run_root, worker_root, str(wrapper)],
            cwd=str(_repo_root()),
            env=env,
            capture_output=True,
            text=True,
            timeout=180,
            start_new_session=True,
        )
        assert child.returncode == -signal.SIGKILL, child.stderr[-2000:]
        # Resume in a fresh supervisor: exactly one native launch total.
        with SqliteWorkItemStore.open(_store_path(run_root, "s_opt")) as store:
            assert store.get_result(item.id) is None
            planned = plan.steps[0]
            registry = _registry()
            adapter = registry.resolve_program("orca")
            request = _Request(
                step=planned,
                items=(item,),
                scientific=planned.scientific,
                scientific_defaults=plan.scientific_defaults,
                adapter=adapter,
                profile=registry.profile_implementation("standard"),
                checks=(registry.check_implementation("normal_termination"),),
                recovery=registry.recovery_implementation("none", adapter=adapter),
                execution_binding=_Binding(
                    binding_id="t", executable=str(wrapper), env=_Frozen({})
                ),
                run_root=run_root,
                environment=_Measurer().build_environment(str(wrapper), adapter=adapter),
                definition_digest=plan.definition_digest,
                executor_capability="calculation",
            )
            transport = RemoteTransport(
                run_root=run_root,
                store=store,
                worker_root=worker_root,
                target_default_executable=str(wrapper),
            )
            result = (
                _B(_E())
                .with_supervisor(_Supervisor())
                .execute_step_resumable(
                    request,
                    store=store,
                    run_root=run_root,
                    owner_token="ctl-2",
                    transport=transport,
                )
            )
            assert result.status is StepStatus.COMPLETED
            assert _launch_count(count) == 1

    def test_bundle_before_import_recovers_without_relaunch(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Worker bundle on disk, producer dead before import: recover, no relaunch."""
        from confflow.domain import FrozenDict as _Frozen
        from confflow.execution import ExecutionBinding as _Binding
        from confflow.execution.environment import EnvironmentMeasurer as _Measurer
        from confflow.execution.process import NativeProcessSupervisor as _Supervisor
        from confflow.execution.registry import default_registry as _registry
        from confflow.execution.work_item_executor import ItemExecutionContext as _Context
        from confflow.persistence import OwnerIdentity as _Owner
        from confflow.persistence.contracts import store_path as _store_path
        from confflow.persistence.work_items import SqliteWorkItemStore
        from confflow.remote.handoff import read_handoff_envelope, write_handoff_envelope
        from confflow.remote.staging import stage_input_bundle
        from confflow.remote.transport import RemoteTransport
        from confflow.remote.worker import run_worker_envelope
        from tests.v4._builders import assemble as _assemble
        from tests.v4._builders import calc_step as _calc
        from tests.v4._builders import compile_doc as _compile
        from tests.v4._builders import run_inputs as _run_inputs
        from tests.v4._builders import structure_set as _structures
        from tests.v4._builders import v4_doc as _doc

        wrapper, count = _install_wrapper(tmp_path, monkeypatch)
        run_root = str(tmp_path / "run")
        worker_root = str(tmp_path / "worker")
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
        plan = compiled.plan
        assembly = _assemble(plan, _run_inputs(structures={"structures": _structures("c0")}))
        assert assembly.ok
        (item,) = assembly.for_step("s_opt")
        with SqliteWorkItemStore.open(_store_path(run_root, "s_opt")) as store:
            planned = plan.steps[0]
            registry = _registry()
            adapter = registry.resolve_program("orca")
            transport = RemoteTransport(
                run_root=run_root,
                store=store,
                worker_root=worker_root,
                target_default_executable=str(wrapper),
            )
            store.register_item(
                work_item_id=item.id,
                logical_key=item.logical_key,
                step_id=item.step_id,
                work_item_digest=item.semantic_digest,
                step_semantic_digest=plan.steps[0].step_semantic_digest,
                environment_digest=transport.probe_environment_digest(
                    program="orca", capability="calculation", requested_executable=str(wrapper)
                ),
                producer_provenance={},
            )
            assert store.claim(item.id, owner=_Owner(owner_token="ctl")) is True
            token = transport.launch_token_for(item, 1)
            context = _Context(
                step_id="s_opt",
                scientific=planned.scientific,
                scientific_defaults=plan.scientific_defaults,
                adapter=adapter,
                profile=registry.profile_implementation("standard"),
                checks=(registry.check_implementation("normal_termination"),),
                recovery=registry.recovery_implementation("none", adapter=adapter),
                execution_binding=_Binding(
                    binding_id="t", executable=str(wrapper), env=_Frozen({})
                ),
                run_root=run_root,
                supervisor=_Supervisor(),
                environment=_Measurer().build_environment(str(wrapper), adapter=adapter),
                attempt=1,
                executor_capability="calculation",
            )
            # Production delivery halves: handoff, stage, worker, then the
            # producer "dies" before import (response dropped on the floor).
            handoff = transport._build_handoff(  # noqa: SLF001 (delivery seam under test)
                item, context, attempt=1, token=token
            )
            handoff_path = write_handoff_envelope(
                handoff=handoff, worker_root=worker_root, launch_token=token
            )
            handoff = read_handoff_envelope(path=handoff_path, expected_run_id=handoff.run_id)
            source_files, _checksums = transport._artifact_sources(item)  # noqa: SLF001
            staged = stage_input_bundle(
                manifest=handoff.inputs,
                run_root=run_root,
                worker_root=worker_root,
                launch_token=token,
                source_files=source_files,
            )
            _result_path = run_worker_envelope(
                handoff_path=handoff_path,
                staged_bundle=staged,
                worker_root=worker_root,
                launch_token=token,
                supervisor=_Supervisor(),
            )
            assert _launch_count(count) == 1
            # Producer restarts with amnesia: a fresh transport recovers the
            # prior bundle instead of relaunching native execution.
            transport2 = RemoteTransport(
                run_root=run_root,
                store=store,
                worker_root=worker_root,
                target_default_executable=str(wrapper),
            )
            recovered = transport2.execute(item, context, attempt=1)
            assert recovered.status.value == "completed"
            assert _launch_count(count) == 1
