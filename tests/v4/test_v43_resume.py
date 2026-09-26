#!/usr/bin/env python3

"""V4-3 per-item resume acceptance: the 95/5 gate and its companions.

A hundred deterministic work items run through
:meth:`BatchStepExecutor.execute_step_resumable` against a durable
:class:`SqliteWorkItemStore`.  After a simulated process restart (the store
is closed and reopened from disk, with fresh executors and supervisors),
resume must reuse every valid completed result and natively execute only
what is genuinely outstanding.  Native invocations are counted at the
process boundary (:meth:`submit`), not inferred from result counts, so a
duplicate launch would fail the gate even with correct-looking results.
"""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Any

import pytest

from confflow.domain import FrozenDict, StructureSet
from confflow.domain.completion import StepStatus
from confflow.execution import ExecutionBinding
from confflow.execution.batch import BatchStepExecutor, StepExecutionRequest
from confflow.execution.checks_standard import CHECKS
from confflow.execution.native import NativeExecutionRequest, NativeHandle
from confflow.execution.process import NativeProcessError, NativeProcessSupervisor
from confflow.execution.profile_standard import PROFILES
from confflow.execution.recovery_standard import RECOVERIES
from confflow.execution.work_item_executor import WorkItemExecutor, sanitize_job_name
from confflow.persistence.contracts import (
    RunState,
    RunStepStatus,
    StoredWorkItemStatus,
    store_path,
)
from confflow.persistence.run_state import (
    ensure_step,
    load_run_state,
    save_run_state,
    transition_step,
)
from confflow.persistence.work_items import SqliteWorkItemStore
from confflow.programs.registry import get_program_adapter
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
ORCA_NATIVE = {"keyword": "B3LYP D3BJ def2-SVP Opt"}
STEP_ID = "s_opt"


def _document(*, scheduler: dict[str, Any] | None = None) -> dict[str, Any]:
    """Build the single-calculation resume document."""
    step = calc_step(
        STEP_ID,
        program="orca",
        bindings={"structure": {"source": {"run": "structures"}}},
        native=dict(ORCA_NATIVE),
        checks=["normal_termination"],
        scheduler=scheduler or {"max_parallel_items": 8},
        resources={"cores_per_item": 1, "memory_per_item": "1GB"},
        execution={"binding_id": "test", "executable": str(FAKE_ORCA)},
    )
    return v4_doc([step], inputs=STRUCTURE_INPUTS)


def _compile(document: dict[str, Any]) -> Any:
    """Compile *document*, asserting a clean compile."""
    compiled = compile_doc(document)
    assert compiled.ok, [(item.code, item.message) for item in compiled.errors]
    assert compiled.plan is not None
    return compiled.plan


def _structures(count: int) -> StructureSet:
    """Build *count* distinct deterministic water structures."""
    return StructureSet.of(*(structure(f"m{i:03d}", offset=float(i) * 0.001) for i in range(count)))


def _items(plan: Any, structures: StructureSet) -> tuple[Any, ...]:
    """Assemble work items, asserting success."""
    assembly = assemble(plan, run_inputs(structures={"structures": structures}))
    assert assembly.ok, [item.message for item in assembly.errors]
    return tuple(assembly.items)


def _request(plan: Any, items: tuple[Any, ...], run_root: str) -> StepExecutionRequest:
    """Build a durable step request wired to the fake ORCA executable."""
    planned = plan.steps[0]
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
            binding_id="test", executable=str(FAKE_ORCA), env=FrozenDict({})
        ),
        run_root=run_root,
        environment=None,
        definition_digest=plan.definition_digest,
    )


class CountingSupervisor(NativeProcessSupervisor):
    """Supervisor counting boundary submissions, with injected failures."""

    def __init__(self, *, fail_basenames: frozenset[str] = frozenset()) -> None:
        super().__init__()
        self._lock = threading.Lock()
        self.submits = 0
        self.fail_basenames = fail_basenames

    def submit(self, request: NativeExecutionRequest) -> NativeHandle:
        basename = request.argv[1] if len(request.argv) > 1 else ""
        if basename in self.fail_basenames:
            raise NativeProcessError(f"injected native failure for {basename}")
        with self._lock:
            self.submits += 1
        return super().submit(request)


def _batch(supervisor: CountingSupervisor) -> BatchStepExecutor:
    """Build a batch executor over a counting supervisor."""
    return BatchStepExecutor(WorkItemExecutor()).with_supervisor(supervisor)


def _fail_basenames(items: tuple[Any, ...], indices: tuple[int, ...]) -> frozenset[str]:
    """Return failing input basenames for *indices* of sorted *items*."""
    ordered = sorted(items, key=lambda item: item.logical_key)
    return frozenset(f"{sanitize_job_name(ordered[index].logical_key)}.inp" for index in indices)


def _reused_count(step_result: Any) -> int:
    """Count item results carried over via durable reuse."""
    return sum(
        1
        for item in step_result.item_results
        if any(diagnostic.code == "reuse_hit" for diagnostic in item.diagnostics)
    )


class TestNinetyFiveFiveResume:
    """100 items: 95 completed, 3 failed, 2 pending → resume runs exactly 5."""

    def test_resume_reuses_95_and_executes_5(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("FAKE_MODE", "success_opt")
        run_root = str(tmp_path / "run")
        plan = _compile(_document())
        structures = _structures(100)
        by_key = {record.id: record for record in structures}
        first_ids = sorted(by_key)[:98]
        first_set = StructureSet.of(*(by_key[sid] for sid in first_ids))
        run1_items = _items(plan, first_set)

        store_file = store_path(run_root, STEP_ID)
        fail_names = _fail_basenames(run1_items, (10, 20, 30))
        supervisor1 = CountingSupervisor(fail_basenames=fail_names)
        state = ensure_step(
            RunState(run_id="v43-resume-95-5", definition_digest=plan.definition_digest),
            STEP_ID,
        )
        state = transition_step(state, STEP_ID, RunStepStatus.RUNNING)
        save_run_state(run_root, state)

        with SqliteWorkItemStore.open(store_file) as store:
            step_result1 = _batch(supervisor1).execute_step_resumable(
                _request(plan, run1_items, run_root),
                store=store,
                run_root=run_root,
                owner_token="controller-run-1",
            )
        # 95 genuine completions, 3 injected native failures, 2 never submitted.
        assert supervisor1.submits == 95
        assert step_result1.summary["completed"] == 95
        assert step_result1.summary["failed"] == 3
        assert step_result1.status is StepStatus.FAILED

        # Simulate a process restart: fresh store handle, executors, owners.
        supervisor2 = CountingSupervisor()
        with SqliteWorkItemStore.open(store_file) as store:
            assert store.list_items(StoredWorkItemStatus.COMPLETED) is not None
            assert len(store.list_items(StoredWorkItemStatus.COMPLETED)) == 95
            assert len(store.list_items(StoredWorkItemStatus.FAILED)) == 3
            all_items = _items(plan, structures)
            assert len(all_items) == 100
            step_result2 = _batch(supervisor2).execute_step_resumable(
                _request(plan, all_items, run_root),
                store=store,
                run_root=run_root,
                owner_token="controller-run-2",
            )
            assert _reused_count(step_result2) == 95
            assert supervisor2.submits == 5
            assert step_result2.summary["completed"] == 100
            assert step_result2.status is StepStatus.COMPLETED
            # Retry history is preserved, never overwritten.
            attempts = [store.get_attempts(item.id) for item in all_items]
            assert sum(1 for entry in attempts if len(entry) == 2) == 3
            assert sum(1 for entry in attempts if len(entry) == 1) == 97
            # Deterministic ordering survives the reuse/new mix.
            assert [item.work_item_id for item in step_result2.item_results] == sorted(
                item.work_item_id for item in step_result2.item_results
            )

        # Exactly one native invocation per item across both runs: no duplicate.
        assert supervisor1.submits + supervisor2.submits == 100

        # Publication and run-state repair close the loop without re-execution.
        from confflow.persistence.publication import load_published_step_result

        published = load_published_step_result(run_root=run_root, step_id=STEP_ID)
        assert published is not None
        assert published.status is StepStatus.COMPLETED
        assert len(published.item_results) == 100
        from confflow.persistence.run_state import detect_published

        digest = detect_published(run_root, STEP_ID)
        assert digest is not None
        repaired = transition_step(
            ensure_step(load_run_state(run_root), STEP_ID),
            STEP_ID,
            RunStepStatus.COMPLETED,
            published_step_result_digest=digest,
        )
        save_run_state(run_root, repaired)
        reloaded = load_run_state(run_root)
        assert reloaded is not None
        record = reloaded.step(STEP_ID)
        assert record is not None
        assert record.status is RunStepStatus.COMPLETED
        assert record.published_step_result_digest == digest


class TestAllowPartialResume:
    """allow_partial publishes PARTIAL, then completes after retry."""

    def test_partial_then_completed(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("FAKE_MODE", "success_opt")
        run_root = str(tmp_path / "run")
        document = _document()
        document["steps"][0]["completion"] = {"mode": "allow_partial"}
        plan = _compile(document)
        structures = _structures(10)
        items = _items(plan, structures)
        store_file = store_path(run_root, STEP_ID)

        fail_names = _fail_basenames(items, (4,))
        supervisor1 = CountingSupervisor(fail_basenames=fail_names)
        with SqliteWorkItemStore.open(store_file) as store:
            partial = _batch(supervisor1).execute_step_resumable(
                _request(plan, items, run_root),
                store=store,
                run_root=run_root,
                owner_token="controller-1",
            )
        assert partial.status is StepStatus.PARTIAL
        assert partial.summary["completed"] == 9
        assert partial.summary["failed"] == 1
        # The failed item is retained, never dropped.
        assert len(partial.item_results) == 10

        supervisor2 = CountingSupervisor()
        with SqliteWorkItemStore.open(store_file) as store:
            completed = _batch(supervisor2).execute_step_resumable(
                _request(plan, items, run_root),
                store=store,
                run_root=run_root,
                owner_token="controller-2",
            )
        assert _reused_count(completed) == 9
        assert supervisor2.submits == 1
        assert completed.status is StepStatus.COMPLETED
        assert completed.summary["completed"] == 10


class TestSchedulerWidthReuseSafe:
    """max_parallel_items changes never invalidate compatible work items."""

    def test_width_change_reuses_everything(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("FAKE_MODE", "success_opt")
        run_root = str(tmp_path / "run")
        narrow_plan = _compile(_document(scheduler={"max_parallel_items": 2}))
        wide_plan = _compile(_document(scheduler={"max_parallel_items": 8}))
        assert narrow_plan.steps[0].step_semantic_digest == wide_plan.steps[0].step_semantic_digest
        structures = _structures(10)
        store_file = store_path(run_root, STEP_ID)

        supervisor1 = CountingSupervisor()
        with SqliteWorkItemStore.open(store_file) as store:
            first = _batch(supervisor1).execute_step_resumable(
                _request(narrow_plan, _items(narrow_plan, structures), run_root),
                store=store,
                run_root=run_root,
                owner_token="controller-1",
            )
        assert first.status is StepStatus.COMPLETED
        assert supervisor1.submits == 10

        supervisor2 = CountingSupervisor()
        with SqliteWorkItemStore.open(store_file) as store:
            second = _batch(supervisor2).execute_step_resumable(
                _request(wide_plan, _items(wide_plan, structures), run_root),
                store=store,
                run_root=run_root,
                owner_token="controller-2",
            )
        assert _reused_count(second) == 10
        assert supervisor2.submits == 0
        assert second.status is StepStatus.COMPLETED
