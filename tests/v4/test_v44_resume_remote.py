#!/usr/bin/env python3

"""V4-4 remote acceptance scenarios (main-owned E2E).

Local artifact chains, local↔remote checkpoint flow, duplicate dispatch,
response-loss recovery, endpoint/width inertness, and environment
invalidation — all through the real handoff/staging/worker path with the
fake ORCA executable.  Native invocations are counted at the process
boundary (a logging wrapper script), so duplicates fail loudly.
"""

from __future__ import annotations

import os
import stat
import threading
from pathlib import Path
from typing import Any

import pytest

from confflow.domain import FrozenDict, StructureSet
from confflow.domain.artifact import ArtifactSet
from confflow.domain.completion import StepStatus, WorkItemStatus
from confflow.execution import ExecutionBinding
from confflow.execution.batch import BatchStepExecutor, StepExecutionRequest
from confflow.execution.checks_standard import CHECKS
from confflow.execution.process import NativeProcessSupervisor
from confflow.execution.profile_standard import PROFILES
from confflow.execution.recovery_standard import RECOVERIES
from confflow.execution.work_item_executor import (
    ItemExecutionContext,
    WorkItemExecutor,
    sanitize_job_name,
)
from confflow.persistence.contracts import StoredWorkItemStatus, store_path
from confflow.persistence.work_items import SqliteWorkItemStore
from confflow.programs.registry import get_program_adapter
from confflow.remote.transport import RemoteTransport
from confflow.workflow.v4.assembly import MaterializedOutputs, StepOutputs
from tests.v4._builders import (
    assemble,
    calc_step,
    compile_doc,
    run_inputs,
    structure,
    v4_doc,
)

FAKES_DIR = Path(__file__).resolve().parent / "fakes"
FAKE_ORCA = FAKES_DIR / "fake_orca.py"
STRUCTURE_INPUTS = {"structures": {"kind": "structure", "cardinality": "many"}}

WRAPPER_SCRIPT = """#!/bin/sh
# Logging wrapper: counts real native invocations, fails listed basenames.
base=$(basename "$1")
printf '%s\\n' "$base" >> "$COUNT_FILE"
if [ -n "$FAIL_FILE" ] && [ -f "$FAIL_FILE" ] && grep -qx "$base" "$FAIL_FILE"; then
  exit 2
fi
exec python3 "$FAKE_REAL" "$@"
"""


def _install_wrapper(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path, Path]:
    """Install the counting wrapper as ``orca`` on PATH.

    Returns ``(wrapper, count_file, fail_file)``.  Both local bindings
    (absolute wrapper path) and remote workers (``orca`` via PATH) land
    in the same count file.
    """
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(parents=True, exist_ok=True)
    wrapper = bin_dir / "orca"
    wrapper.write_text(WRAPPER_SCRIPT)
    wrapper.chmod(0o755)
    count_file = tmp_path / "native.count"
    count_file.write_text("")
    fail_file = tmp_path / "native.fail"
    fail_file.write_text("")
    monkeypatch.setenv("PATH", str(bin_dir) + os.pathsep + os.environ["PATH"])
    monkeypatch.setenv("FAKE_MODE", "success_opt")
    monkeypatch.setenv("FAKE_REAL", str(FAKE_ORCA))
    monkeypatch.setenv("COUNT_FILE", str(count_file))
    monkeypatch.setenv("FAIL_FILE", str(fail_file))
    assert os.access(wrapper, os.X_OK)
    assert not bool(wrapper.stat().st_mode & (stat.S_IWGRP | stat.S_IWOTH))
    return wrapper, count_file, fail_file


def _native_count(count_file: Path) -> int:
    """Return the number of logged native invocations."""
    return len([line for line in count_file.read_text().splitlines() if line.strip()])


def _single_step_doc(**overrides: Any) -> dict[str, Any]:
    """Build a single-calculation document bound to the wrapper."""
    scheduler = overrides.pop("scheduler", {"max_parallel_items": 4})
    step = calc_step(
        "s_opt",
        program="orca",
        bindings={"structure": {"source": {"run": "structures"}}},
        native={"keyword": "B3LYP Opt"},
        checks=["normal_termination"],
        scheduler=scheduler,
        resources={"cores_per_item": 1, "memory_per_item": "1GB"},
        execution={"binding_id": "test", "executable": "orca"},
    )
    return v4_doc([step], inputs=STRUCTURE_INPUTS)


def _chain_doc() -> dict[str, Any]:
    """Build the two-step freq→ts-style checkpoint chain document."""
    step_a = calc_step(
        "s_a",
        program="orca",
        bindings={"structure": {"source": {"run": "structures"}}},
        native={"keyword": "B3LYP Opt"},
        checks=["normal_termination"],
        scheduler={"max_parallel_items": 4},
        resources={"cores_per_item": 1, "memory_per_item": "1GB"},
        execution={"binding_id": "test", "executable": "orca"},
    )
    step_b = calc_step(
        "s_b",
        program="orca",
        bindings={
            "structure": {"source": {"step": "s_a", "port": "structures"}},
            "checkpoint": {
                "source": {"step": "s_a", "port": "artifacts", "select": {"role": "checkpoint"}},
                "pairing": "by_subject",
                "cardinality": "one",
            },
        },
        native={"keyword": "B3LYP Opt"},
        checks=["normal_termination"],
        scheduler={"max_parallel_items": 4},
        resources={"cores_per_item": 1, "memory_per_item": "1GB"},
        execution={"binding_id": "test", "executable": "orca"},
    )
    return v4_doc([step_a, step_b], inputs=STRUCTURE_INPUTS)


def _compile(document: dict[str, Any]) -> Any:
    """Compile *document*, asserting a clean compile."""
    compiled = compile_doc(document)
    assert compiled.ok, [(item.code, item.message) for item in compiled.errors]
    assert compiled.plan is not None
    return compiled.plan


def _structures(prefix: str, count: int) -> StructureSet:
    """Build *count* distinct deterministic structures."""
    return StructureSet.of(
        *(structure(f"{prefix}{i}", offset=float(i) * 0.013) for i in range(count))
    )


def _assemble_step(plan: Any, step_id: str, structures: StructureSet, **kwargs: Any) -> Any:
    """Assemble items for *step_id* with optional materialized outputs."""
    assembly = assemble(plan, run_inputs(structures={"structures": structures}), **kwargs)
    assert assembly.ok, [item.message for item in assembly.errors]
    return assembly


def _request(
    plan: Any, step_id: str, items: tuple[Any, ...], run_root: str, wrapper: Path
) -> StepExecutionRequest:
    """Build a durable step request wired to the counting wrapper."""
    planned = next(step for step in plan.steps if step.step_id == step_id)
    return StepExecutionRequest(
        step=planned,
        items=tuple(items),
        scientific=planned.scientific,
        scientific_defaults=plan.scientific_defaults,
        adapter=get_program_adapter("orca"),
        profile=PROFILES["standard"],
        checks=(CHECKS["normal_termination"],),
        recovery=RECOVERIES["none"],
        execution_binding=ExecutionBinding(
            binding_id="test", executable=str(wrapper), env=FrozenDict({})
        ),
        run_root=run_root,
        environment=None,
        definition_digest=plan.definition_digest,
    )


def _batch() -> BatchStepExecutor:
    """Build a batch executor with a fresh supervisor."""
    return BatchStepExecutor(WorkItemExecutor()).with_supervisor(NativeProcessSupervisor())


def _context_for(plan: Any, step_id: str, run_root: str, wrapper: Path) -> ItemExecutionContext:
    """Build a local item context for direct transport calls."""
    planned = next(step for step in plan.steps if step.step_id == step_id)
    return ItemExecutionContext(
        step_id=step_id,
        scientific=planned.scientific,
        scientific_defaults=plan.scientific_defaults,
        adapter=get_program_adapter("orca"),
        profile=PROFILES["standard"],
        checks=(CHECKS["normal_termination"],),
        recovery=RECOVERIES["none"],
        execution_binding=ExecutionBinding(
            binding_id="test", executable=str(wrapper), env=FrozenDict({})
        ),
        run_root=run_root,
        work_base=os.path.join(run_root, "work"),
        supervisor=NativeProcessSupervisor(),
        environment=None,
        poll_interval_seconds=0.05,
    )


class TestRemoteResumeLite:
    """20 items remote: 16 completed, 2 failed, 2 pending → 4 native on resume."""

    def test_remote_resume_reuses_16_and_runs_4(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        wrapper, count_file, fail_file = _install_wrapper(tmp_path, monkeypatch)
        run_root = str(tmp_path / "run")
        worker_root = str(tmp_path / "worker")
        plan = _compile(_single_step_doc())
        structures = _structures("q", 20)
        by_id = {record.id: record for record in structures}
        first = StructureSet.of(*(by_id[sid] for sid in sorted(by_id)[:18]))
        run1_items = _assemble_step(plan, "s_opt", first).for_step("s_opt")
        fail_file.write_text(
            "".join(
                f"{sanitize_job_name(item.logical_key)}.inp\n"
                for item in sorted(run1_items, key=lambda entry: entry.logical_key)[6:8]
            )
        )
        with SqliteWorkItemStore.open(store_path(run_root, "s_opt")) as store:
            transport = RemoteTransport(run_root=run_root, store=store, worker_root=worker_root)
            result1 = _batch().execute_step_resumable(
                _request(plan, "s_opt", tuple(run1_items), run_root, wrapper),
                store=store,
                run_root=run_root,
                owner_token="ctl-1",
                transport=transport,
            )
        assert result1.summary["completed"] == 16
        assert result1.summary["failed"] == 2
        # 16 successes + 2 launched-but-failed invocations.
        assert _native_count(count_file) == 18

        with SqliteWorkItemStore.open(store_path(run_root, "s_opt")) as store:
            assert len(store.list_items(StoredWorkItemStatus.COMPLETED)) == 16
            assert len(store.list_items(StoredWorkItemStatus.FAILED)) == 2
            fail_file.write_text("")
            all_items = _assemble_step(plan, "s_opt", structures).for_step("s_opt")
            transport = RemoteTransport(run_root=run_root, store=store, worker_root=worker_root)
            result2 = _batch().execute_step_resumable(
                _request(plan, "s_opt", tuple(all_items), run_root, wrapper),
                store=store,
                run_root=run_root,
                owner_token="ctl-2",
                transport=transport,
            )
            reused = sum(
                1
                for item in result2.item_results
                if any(d.code == "reuse_hit" for d in item.diagnostics)
            )
            assert reused == 16
            assert _native_count(count_file) == 22
            assert result2.summary["completed"] == 20
            assert result2.status is StepStatus.COMPLETED


def _run_step_a_local(
    plan: Any, structures: StructureSet, run_root: str, wrapper: Path, owner: str
) -> Any:
    """Run step A locally, returning its step result."""
    items = _assemble_step(plan, "s_a", structures).for_step("s_a")
    with SqliteWorkItemStore.open(store_path(run_root, "s_a")) as store:
        return _batch().execute_step_resumable(
            _request(plan, "s_a", tuple(items), run_root, wrapper),
            store=store,
            run_root=run_root,
            owner_token=owner,
        )


def _materialized(step_result: Any, step_id: str) -> MaterializedOutputs:
    """Wrap a step result as materialized producer outputs."""
    from confflow.domain import FrozenDict as _Frozen

    return MaterializedOutputs(
        steps=_Frozen(
            {
                step_id: StepOutputs(
                    step_id=step_id,
                    structures=step_result.structures,
                    artifacts=step_result.artifacts,
                )
            }
        )
    )


def _checkpoint_subjects(step_result: Any) -> dict[str, str]:
    """Map checkpoint artifact id to its subject structure id."""
    return {
        artifact.id: artifact.subject_structure_id or ""
        for artifact in step_result.artifacts
        if artifact.role == "checkpoint"
    }


class TestScenarioALocalChain:
    """3 structures → freq → 3 checkpoints → next calculation, by subject."""

    def test_subject_matching_survives_shuffle(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        wrapper, count_file, _fail = _install_wrapper(tmp_path, monkeypatch)
        run_root = str(tmp_path / "run")
        plan = _compile(_chain_doc())
        structures = _structures("a", 3)
        result_a = _run_step_a_local(plan, structures, run_root, wrapper, "ctl-a")
        assert result_a.summary["completed"] == 3
        subjects = _checkpoint_subjects(result_a)
        assert len(subjects) == 3
        # Every checkpoint subject is a PRODUCED output structure, never the input.
        output_ids = {record.id for record in result_a.structures}
        assert set(subjects.values()) <= output_ids
        assert not (set(subjects.values()) & {record.id for record in structures})

        materialized = _materialized(result_a, "s_a")
        assembly = _assemble_step(plan, "s_b", structures, materialized=materialized)
        items_b = assembly.for_step("s_b")
        assert len(items_b) == 3
        for item in items_b:
            driving = item.named_inputs.structures["structure"][0]
            bound = item.named_inputs.artifacts["checkpoint"]
            assert len(bound) == 1
            assert bound[0].subject_structure_id == driving.id
            assert bound[0].role == "checkpoint"

        # Shuffled producer order assembles identically: no positional matching.
        shuffled = MaterializedOutputs(
            steps=materialized.steps.__class__(
                {
                    "s_a": StepOutputs(
                        step_id="s_a",
                        structures=result_a.structures,
                        artifacts=ArtifactSet(tuple(reversed(tuple(result_a.artifacts)))),
                    )
                }
            )
        )
        reshuffled = _assemble_step(plan, "s_b", structures, materialized=shuffled)
        assert [
            (entry.logical_key, entry.named_inputs.artifacts["checkpoint"][0].id)
            for entry in reshuffled.for_step("s_b")
        ] == [
            (entry.logical_key, entry.named_inputs.artifacts["checkpoint"][0].id)
            for entry in items_b
        ]

        with SqliteWorkItemStore.open(store_path(run_root, "s_b")) as store:
            result_b = _batch().execute_step_resumable(
                _request(plan, "s_b", tuple(items_b), run_root, wrapper),
                store=store,
                run_root=run_root,
                owner_token="ctl-b",
            )
        assert result_b.summary["completed"] == 3
        assert result_b.status is StepStatus.COMPLETED

    def test_wrong_subject_fails_before_launch(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import dataclasses as _dc

        from confflow.domain.work_item import WorkItem, WorkItemInputs

        wrapper, count_file, _fail = _install_wrapper(tmp_path, monkeypatch)
        run_root = str(tmp_path / "run")
        plan = _compile(_chain_doc())
        structures = _structures("w", 3)
        result_a = _run_step_a_local(plan, structures, run_root, wrapper, "ctl-a")
        before = _native_count(count_file)
        materialized = _materialized(result_a, "s_a")
        items_b = _assemble_step(plan, "s_b", structures, materialized=materialized).for_step("s_b")
        victim = items_b[0]
        checkpoints = victim.named_inputs.artifacts["checkpoint"]
        assert len(checkpoints) == 1
        siblings = [
            record.id
            for record in result_a.structures
            if record.id != checkpoints[0].subject_structure_id
        ]
        assert siblings, "need a sibling subject to forge a mismatch"
        forged = _dc.replace(checkpoints[0], subject_structure_id=siblings[0])
        tampered = WorkItem(
            id=victim.id,
            logical_key=victim.logical_key,
            step_id=victim.step_id,
            named_inputs=WorkItemInputs(
                structures=victim.named_inputs.structures,
                artifacts=FrozenDict({"checkpoint": ArtifactSet.of(forged)}),
                results=victim.named_inputs.results,
            ),
            resources=victim.resources,
            semantic_digest="sha256:" + "e" * 64,
            ordinal=victim.ordinal,
            group_key=victim.group_key,
            lineage_root_id=victim.lineage_root_id,
        )
        with SqliteWorkItemStore.open(store_path(run_root, "s_b")) as store:
            result = _batch().execute_step_resumable(
                _request(plan, "s_b", (tampered,), run_root, wrapper),
                store=store,
                run_root=run_root,
                owner_token="ctl-bad",
            )
        assert result.summary["failed"] == 1
        assert result.item_results[0].error is not None
        assert result.item_results[0].error.code == "artifact_error"
        assert _native_count(count_file) == before


class TestScenarioLocalRemote:
    """Local step A → remote step B with identical checkpoint semantics."""

    def test_local_to_remote(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        wrapper, count_file, _fail = _install_wrapper(tmp_path, monkeypatch)
        run_root = str(tmp_path / "run")
        worker_root = str(tmp_path / "worker")
        plan = _compile(_chain_doc())
        structures = _structures("b", 3)
        result_a = _run_step_a_local(plan, structures, run_root, wrapper, "ctl-a")
        materialized = _materialized(result_a, "s_a")
        items_b = _assemble_step(plan, "s_b", structures, materialized=materialized).for_step("s_b")
        with SqliteWorkItemStore.open(store_path(run_root, "s_b")) as store:
            transport = RemoteTransport(run_root=run_root, store=store, worker_root=worker_root)
            result_b = _batch().execute_step_resumable(
                _request(plan, "s_b", tuple(items_b), run_root, wrapper),
                store=store,
                run_root=run_root,
                owner_token="ctl-b",
                transport=transport,
            )
        assert result_b.summary["completed"] == 3
        assert result_b.status is StepStatus.COMPLETED
        for item in result_b.item_results:
            assert item.error is None


class TestScenarioRemoteLocal:
    """Remote step A → imported artifacts → local step B."""

    def test_remote_to_local(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        wrapper, count_file, _fail = _install_wrapper(tmp_path, monkeypatch)
        run_root = str(tmp_path / "run")
        worker_root = str(tmp_path / "worker")
        plan = _compile(_chain_doc())
        structures = _structures("c", 3)
        items_a = _assemble_step(plan, "s_a", structures).for_step("s_a")
        with SqliteWorkItemStore.open(store_path(run_root, "s_a")) as store:
            transport = RemoteTransport(run_root=run_root, store=store, worker_root=worker_root)
            result_a = _batch().execute_step_resumable(
                _request(plan, "s_a", tuple(items_a), run_root, wrapper),
                store=store,
                run_root=run_root,
                owner_token="ctl-a",
                transport=transport,
            )
        assert result_a.summary["completed"] == 3
        subjects = _checkpoint_subjects(result_a)
        assert len(subjects) == 3
        materialized = _materialized(result_a, "s_a")
        items_b = _assemble_step(plan, "s_b", structures, materialized=materialized).for_step("s_b")
        assert len(items_b) == 3
        with SqliteWorkItemStore.open(store_path(run_root, "s_b")) as store:
            result_b = _batch().execute_step_resumable(
                _request(plan, "s_b", tuple(items_b), run_root, wrapper),
                store=store,
                run_root=run_root,
                owner_token="ctl-b",
            )
        assert result_b.summary["completed"] == 3
        assert result_b.status is StepStatus.COMPLETED


class TestScenarioDuplicateDispatch:
    """Same attempt dispatched twice concurrently → exactly 1 native launch."""

    def test_concurrent_duplicate_dispatch(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _install_wrapper(tmp_path, monkeypatch)
        run_root = str(tmp_path / "run")
        worker_root = str(tmp_path / "worker")
        plan = _compile(_single_step_doc())
        (item,) = _assemble_step(plan, "s_opt", _structures("d", 1)).for_step("s_opt")
        context = _context_for(plan, "s_opt", run_root, tmp_path / "bin" / "orca")
        with SqliteWorkItemStore.open(store_path(run_root, "s_opt")) as store:
            from confflow.persistence import OwnerIdentity

            store.register_item(
                work_item_id=item.id,
                logical_key=item.logical_key,
                step_id=item.step_id,
                work_item_digest=item.semantic_digest,
                step_semantic_digest=plan.steps[0].step_semantic_digest,
            )
            assert store.claim(item.id, owner=OwnerIdentity(owner_token="ctl"))
            transport = RemoteTransport(run_root=run_root, store=store, worker_root=worker_root)
            barrier = threading.Barrier(2)
            outcomes: list[Any] = []

            def _dispatch() -> None:
                barrier.wait(timeout=30)
                outcomes.append(transport.execute(item, context, attempt=1))

            threads = [threading.Thread(target=_dispatch) for _ in range(2)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(timeout=120)
            assert len(outcomes) == 2
            assert all(result.status is WorkItemStatus.COMPLETED for result in outcomes)
        count_file = tmp_path / "native.count"
        assert _native_count(count_file) == 1


class TestScenarioResponseLoss:
    """A lost response recovers the prior bundle without relaunching."""

    def test_prior_bundle_recovered_after_forget(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _install_wrapper(tmp_path, monkeypatch)
        run_root = str(tmp_path / "run")
        worker_root = str(tmp_path / "worker")
        plan = _compile(_single_step_doc())
        (item,) = _assemble_step(plan, "s_opt", _structures("e", 1)).for_step("s_opt")
        context = _context_for(plan, "s_opt", run_root, tmp_path / "bin" / "orca")
        with SqliteWorkItemStore.open(store_path(run_root, "s_opt")) as store:
            from confflow.persistence import OwnerIdentity

            store.register_item(
                work_item_id=item.id,
                logical_key=item.logical_key,
                step_id=item.step_id,
                work_item_digest=item.semantic_digest,
                step_semantic_digest=plan.steps[0].step_semantic_digest,
            )
            assert store.claim(item.id, owner=OwnerIdentity(owner_token="ctl"))
            transport = RemoteTransport(run_root=run_root, store=store, worker_root=worker_root)
            first = transport.execute(item, context, attempt=1)
            assert first.status is WorkItemStatus.COMPLETED
            count_file = tmp_path / "native.count"
            assert _native_count(count_file) == 1
            # Producer loses the response object; a restarted transport with
            # the same roots recovers the prior bundle instead of relaunching.
            transport2 = RemoteTransport(run_root=run_root, store=store, worker_root=worker_root)
            second = transport2.execute(item, context, attempt=1)
            assert second.status is WorkItemStatus.COMPLETED
            assert _native_count(count_file) == 1
            assert second.work_item_id == item.id


class TestScenarioEndpointInertness:
    """Transport endpoint and scheduler width never move the definition digest."""

    def test_changed_endpoint_and_width_reuse(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        wrapper, count_file, _fail = _install_wrapper(tmp_path, monkeypatch)
        run_root = str(tmp_path / "run")
        narrow = _compile(_single_step_doc(scheduler={"max_parallel_items": 1}))
        wide = _compile(_single_step_doc(scheduler={"max_parallel_items": 4}))
        assert narrow.steps[0].step_semantic_digest == wide.steps[0].step_semantic_digest
        structures = _structures("g", 4)
        with SqliteWorkItemStore.open(store_path(run_root, "s_opt")) as store:
            transport1 = RemoteTransport(
                run_root=run_root, store=store, worker_root=str(tmp_path / "w1")
            )
            first = _batch().execute_step_resumable(
                _request(
                    narrow,
                    "s_opt",
                    tuple(_assemble_step(narrow, "s_opt", structures).for_step("s_opt")),
                    run_root,
                    wrapper,
                ),
                store=store,
                run_root=run_root,
                owner_token="ctl-1",
                transport=transport1,
            )
            assert first.status is StepStatus.COMPLETED
            assert _native_count(count_file) == 4
            transport2 = RemoteTransport(
                run_root=run_root, store=store, worker_root=str(tmp_path / "w2")
            )
            second = _batch().execute_step_resumable(
                _request(
                    wide,
                    "s_opt",
                    tuple(_assemble_step(wide, "s_opt", structures).for_step("s_opt")),
                    run_root,
                    wrapper,
                ),
                store=store,
                run_root=run_root,
                owner_token="ctl-2",
                transport=transport2,
            )
            reused = sum(
                1
                for entry in second.item_results
                if any(d.code == "reuse_hit" for d in entry.diagnostics)
            )
            assert reused == 4
            assert _native_count(count_file) == 4
            assert second.status is StepStatus.COMPLETED


class TestScenarioEnvironmentInvalidation:
    """A changed remote binary invalidates per V4-3 environment policy."""

    def test_binary_change_invalidates(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from confflow.execution.contracts import ExecutionEnvironment

        wrapper, count_file, _fail = _install_wrapper(tmp_path, monkeypatch)
        run_root = str(tmp_path / "run")
        worker_root = str(tmp_path / "worker")
        plan = _compile(_single_step_doc())
        structures = _structures("h", 2)
        items = _assemble_step(plan, "s_opt", structures).for_step("s_opt")
        with SqliteWorkItemStore.open(store_path(run_root, "s_opt")) as store:
            transport = RemoteTransport(run_root=run_root, store=store, worker_root=worker_root)
            first_request = _request(plan, "s_opt", tuple(items), run_root, wrapper)
            import dataclasses as _dc

            first_request = _dc.replace(
                first_request,
                environment=ExecutionEnvironment(
                    program="orca", executable_digest="sha256:" + "1" * 64
                ),
            )
            first = _batch().execute_step_resumable(
                first_request,
                store=store,
                run_root=run_root,
                owner_token="ctl-1",
                transport=transport,
            )
            assert first.status is StepStatus.COMPLETED
            assert _native_count(count_file) == 2
            second_request = _dc.replace(
                first_request,
                environment=ExecutionEnvironment(
                    program="orca", executable_digest="sha256:" + "2" * 64
                ),
            )
            second = _batch().execute_step_resumable(
                second_request,
                store=store,
                run_root=run_root,
                owner_token="ctl-2",
                transport=transport,
            )
            assert _native_count(count_file) == 2
            assert second.status is StepStatus.FAILED
            codes = {entry.error.code for entry in second.item_results if entry.error is not None}
            assert codes == {"invalidate_environment"}
