#!/usr/bin/env python3

"""V4-2 executors: supervisor boundary, work-item execution, and batch steps.

End-to-end coverage of the V4-2 execution seam with the real
:class:`NativeProcessSupervisor`, :class:`WorkItemExecutor`, and
:class:`BatchStepExecutor` driven by the fake native executables (no real
quantum chemistry). Every test uses ``tmp_path`` only:

- process boundary (submit/poll/collect, unknown handles, SIGTERM versus
  SIGKILL cancellation with proof, already-terminal verdicts),
- single work-item execution for both programs (success, abnormal,
  imaginary-count gating, input validation, missing supervisor),
- the section-26 gate: three structures compile and assemble into three
  work-item results and one step result with no legacy
  ``input_xyz``/``output_path``/``TaskRunner``/``CalcStepRunner`` involvement,
- acceptance (require-all failure, allow-partial outcome),
- digest inertness across scheduler widths and execution bindings,
- environment measurement (content-sensitive, path-insensitive digests),
- confirmed cancellation with the ``cancel_trap`` fake,
- unconfirmed cancellation never triggering rescue,
- digest-keyed reuse hits.
"""

from __future__ import annotations

import os
import shutil
import threading
import time
from pathlib import Path
from typing import Any

import pytest

from confflow.domain import FrozenDict, StructureSet
from confflow.domain.completion import (
    CompletionMode,
    CompletionPolicy,
    StepStatus,
    WorkItemStatus,
    evaluate_step_status,
)
from confflow.execution import ExecutionBinding, ExecutionEnvironment
from confflow.execution.batch import (
    BatchStepExecutor,
    InMemoryReuseStore,
    InMemoryWorkItemRepository,
    StepExecutionRequest,
)
from confflow.execution.checks_standard import CHECKS
from confflow.execution.environment import EnvironmentMeasurer, measure_executable
from confflow.execution.native import (
    CancelOutcome,
    NativeExecutionRequest,
    NativeHandle,
    NativeStatus,
)
from confflow.execution.process import NativeProcessError, NativeProcessSupervisor
from confflow.execution.profile_standard import PROFILES
from confflow.execution.recovery_standard import RECOVERIES
from confflow.execution.work_item_executor import ItemExecutionContext, WorkItemExecutor
from confflow.programs.registry import get_program_adapter
from tests.v4._builders import (
    assemble,
    calc_step,
    compile_doc,
    run_inputs,
    structure,
    structure_set,
    v4_doc,
)

FAKES_DIR = Path(__file__).resolve().parent / "fakes"
FAKE_G16 = FAKES_DIR / "fake_g16.py"
FAKE_ORCA = FAKES_DIR / "fake_orca.py"

STRUCTURE_INPUTS = {"structures": {"kind": "structure", "cardinality": "many"}}

ORCA_NATIVE = {"keyword": "B3LYP D3BJ def2-SVP Opt"}
ORCA_FREQ_NATIVE = {"keyword": "B3LYP D3BJ def2-SVP Opt Freq"}
GAUSSIAN_NATIVE = {"keyword": "B3LYP/6-31G* Opt"}

LEGACY_TOKENS = (
    "input_xyz",
    "output_path",
    "TaskRunner",
    "CalcStepRunner",
    "TaskName",
    "get_itask",
    "CalcStepRunner",
)


def calculation_doc(
    program: str,
    native: dict[str, Any],
    executable: str,
    *,
    checks: list[str] | None = None,
    check_params: dict[str, dict[str, Any]] | None = None,
    step_id: str = "s_opt",
    scheduler: dict[str, Any] | None = None,
    completion: dict[str, Any] | None = None,
    resources: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a single-calculation document bound to *executable*."""
    step = calc_step(
        step_id,
        program="g16" if program == "gaussian" else "orca",
        bindings={"structure": {"source": {"run": "structures"}}},
        native=native,
        checks=checks if checks is not None else ["normal_termination"],
        scheduler=scheduler,
        completion=completion,
        resources=(
            resources if resources is not None else {"cores_per_item": 4, "memory_per_item": "16GB"}
        ),
        execution={"binding_id": "test", "executable": executable},
    )
    if check_params:
        step["calculation"]["check_params"] = check_params
    return v4_doc([step], inputs=STRUCTURE_INPUTS)


def compile_plan(document: dict[str, Any]) -> Any:
    """Compile *document*, asserting a clean compile."""
    compiled = compile_doc(document)
    assert compiled.ok, [(item.code, item.message) for item in compiled.errors]
    assert compiled.plan is not None
    return compiled.plan


def assemble_items(plan: Any, structures: StructureSet) -> Any:
    """Assemble work items for *structures*, asserting success."""
    assembly = assemble(plan, run_inputs(structures={"structures": structures}))
    assert assembly.ok, [item.message for item in assembly.errors]
    return assembly.items


def binding_for(executable: str) -> ExecutionBinding:
    """Build a test execution binding for a fake executable."""
    return ExecutionBinding(binding_id="test", executable=executable, env=FrozenDict({}))


def item_context(
    plan: Any,
    program: str,
    executable: str,
    run_root: str,
    work_base: str,
    supervisor: Any,
    checks: list[str],
    *,
    recovery: Any = None,
) -> ItemExecutionContext:
    """Build an item execution context wired to real adapters and profiles."""
    planned = plan.steps[0]
    return ItemExecutionContext(
        step_id=planned.step_id,
        scientific=planned.scientific,
        scientific_defaults=plan.scientific_defaults,
        adapter=get_program_adapter(program),
        profile=PROFILES["standard"],
        checks=tuple(CHECKS[name] for name in checks),
        recovery=recovery if recovery is not None else RECOVERIES["none"],
        execution_binding=binding_for(executable),
        run_root=run_root,
        work_base=work_base,
        supervisor=supervisor,
        environment=None,
        poll_interval_seconds=0.05,
    )


def step_request(
    plan: Any,
    items: Any,
    program: str,
    executable: str,
    run_root: str,
    work_base: str,
    checks: list[str],
) -> StepExecutionRequest:
    """Build a batch step request wired to real adapters and profiles."""
    planned = plan.steps[0]
    return StepExecutionRequest(
        step=planned,
        items=tuple(items),
        scientific=planned.scientific,
        scientific_defaults=plan.scientific_defaults,
        adapter=get_program_adapter(program),
        profile=PROFILES["standard"],
        checks=tuple(CHECKS[name] for name in checks),
        recovery=RECOVERIES["none"],
        execution_binding=binding_for(executable),
        run_root=run_root,
        work_base=work_base,
        environment=None,
        definition_digest=plan.definition_digest,
    )


def batch_executor() -> BatchStepExecutor:
    """Build a batch executor bound to a fresh supervisor."""
    return BatchStepExecutor(WorkItemExecutor()).with_supervisor(NativeProcessSupervisor())


class FakeUnconfirmedSupervisor:
    """Supervisor stub whose cancellation can never be confirmed."""

    def submit(self, request: NativeExecutionRequest) -> NativeHandle:
        return NativeHandle(key="fake:0", pid=1)

    def poll(self, handle: NativeHandle) -> NativeStatus:
        return NativeStatus(is_terminal=False, exit_code=None)

    def cancel(self, handle: NativeHandle, *, grace_seconds: float = 2.0) -> CancelOutcome:
        return CancelOutcome(confirmed=False, detail="process boundary is still live")

    def collect(self, handle: NativeHandle) -> Any:
        raise NativeProcessError("fake supervisor never reaches terminal state")


class RecordingRecovery:
    """Recovery double recording every evaluation and execution."""

    def __init__(self) -> None:
        self.evaluations = 0
        self.executions = 0

    @property
    def name(self) -> str:
        return "recording"

    @property
    def contract_version(self) -> str:
        return "test.contract.recovery.recording.v1"

    def evaluate(self, context: Any) -> Any:
        from confflow.execution.recovery import RecoveryDecision

        self.evaluations += 1
        return RecoveryDecision(attempt=False, reason="recording_declines")

    def execute(self, context: Any, driver: Any) -> Any:
        self.executions += 1
        return None


class TestSupervisorBoundary:
    """Submit, poll, collect, and proven cancellation."""

    def _request(self, argv: list[str], work_dir: Path, extra_env: dict[str, str]) -> Any:
        env = {"PATH": os.environ.get("PATH", ""), **extra_env}
        return NativeExecutionRequest(
            executable=argv[0],
            argv=tuple(argv),
            work_dir=str(work_dir),
            env=FrozenDict(env),
        )

    def test_submit_poll_collect_success(self, tmp_path: Path) -> None:
        supervisor = NativeProcessSupervisor()
        request = self._request([str(FAKE_ORCA)], tmp_path, {"FAKE_MODE": "success_sp"})
        (tmp_path / "job.inp").write_text(
            "* xyz 0 1\nO 0.0 0.0 0.0\nH 0.76 0.59 0.0\nH 0.76 -0.59 0.0\n*\n",
            encoding="utf-8",
        )
        handle = supervisor.submit(request)
        assert handle.pid is not None and handle.pid > 0
        assert handle.process_group_id is not None
        deadline = 60
        start = time.monotonic()
        while time.monotonic() - start < deadline:
            status = supervisor.poll(handle)
            if status.is_terminal:
                break
        assert status.is_terminal
        assert status.exit_code == 0
        result = supervisor.collect(handle)
        assert result.exit_code == 0
        assert result.timed_out is False
        assert result.wall_time_seconds >= 0.0
        assert (tmp_path / "stdout.log").exists()
        assert (tmp_path / "stderr.log").exists()

    def test_unknown_handle_errors(self, tmp_path: Path) -> None:
        supervisor = NativeProcessSupervisor()
        handle = NativeHandle(key="np:missing", pid=None)
        with pytest.raises(NativeProcessError):
            supervisor.poll(handle)
        with pytest.raises(NativeProcessError):
            supervisor.collect(handle)
        with pytest.raises(NativeProcessError):
            supervisor.cancel(handle)

    def test_collect_before_terminal_errors(self, tmp_path: Path) -> None:
        supervisor = NativeProcessSupervisor()
        request = self._request([str(FAKE_ORCA)], tmp_path, {"FAKE_MODE": "cancel_trap"})
        handle = supervisor.submit(request)
        try:
            with pytest.raises(NativeProcessError):
                supervisor.collect(handle)
        finally:
            supervisor.cancel(handle, grace_seconds=0.5)

    def test_cancel_term_responsive_confirms_after_sigterm(self, tmp_path: Path) -> None:
        supervisor = NativeProcessSupervisor()
        request = self._request([str(FAKE_ORCA)], tmp_path, {"FAKE_MODE": "slow_term"})
        handle = supervisor.submit(request)
        outcome = supervisor.cancel(handle, grace_seconds=2.0)
        assert outcome.confirmed is True
        assert outcome.detail == "cancelled: boundary stopped after SIGTERM"
        result = supervisor.collect(handle)
        assert result.exit_code is not None and result.exit_code != 0

    def test_cancel_trap_escalates_to_sigkill(self, tmp_path: Path) -> None:
        supervisor = NativeProcessSupervisor()
        request = self._request([str(FAKE_ORCA)], tmp_path, {"FAKE_MODE": "cancel_trap"})
        handle = supervisor.submit(request)
        for _ in range(600):
            if not supervisor.poll(handle).is_terminal:
                break
            time.sleep(0.05)
        time.sleep(1.0)
        outcome = supervisor.cancel(handle, grace_seconds=0.5)
        assert outcome.confirmed is True
        assert outcome.detail == "cancelled: boundary stopped after SIGKILL"
        import signal

        result = supervisor.collect(handle)
        assert result.exit_code == -signal.SIGKILL

    def test_cancel_already_terminal(self, tmp_path: Path) -> None:
        supervisor = NativeProcessSupervisor()
        request = self._request([str(FAKE_ORCA)], tmp_path, {"FAKE_MODE": "success_sp"})
        (tmp_path / "job.inp").write_text("* xyz 0 1\nO 0.0 0.0 0.0\n*\n", encoding="utf-8")
        handle = supervisor.submit(request)
        for _ in range(600):
            if supervisor.poll(handle).is_terminal:
                break
            time.sleep(0.05)
        outcome = supervisor.cancel(handle)
        assert outcome.confirmed is True
        assert outcome.detail == "already terminal"
        supervisor.collect(handle)

    def test_invalid_timeouts_rejected(self) -> None:
        with pytest.raises(ValueError):
            NativeProcessSupervisor(terminate_timeout=-1.0)

    def test_invalid_grace_rejected(self, tmp_path: Path) -> None:
        supervisor = NativeProcessSupervisor()
        request = self._request([str(FAKE_ORCA)], tmp_path, {"FAKE_MODE": "cancel_trap"})
        handle = supervisor.submit(request)
        try:
            with pytest.raises(ValueError):
                supervisor.cancel(handle, grace_seconds=-1.0)
        finally:
            supervisor.cancel(handle, grace_seconds=0.5)


class TestWorkItemExecutor:
    """Single work-item execution for both programs."""

    def test_orca_success_opt(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("FAKE_MODE", "success_opt")
        plan = compile_plan(calculation_doc("orca", ORCA_NATIVE, str(FAKE_ORCA)))
        items = assemble_items(plan, structure_set("s0", "s1", "s2"))
        supervisor = NativeProcessSupervisor()
        context = item_context(
            plan,
            "orca",
            str(FAKE_ORCA),
            str(tmp_path),
            str(tmp_path / "items"),
            supervisor,
            ["normal_termination"],
        )
        result = WorkItemExecutor().execute(items[0], context)
        assert result.is_completed
        assert len(result.structures) == 1
        assert len(result.results) >= 1
        assert result.semantic_digest == items[0].semantic_digest
        assert result.timing is not None and result.timing.duration_seconds is not None
        assert result.recovery is not None and result.recovery.attempted is False

    def test_gaussian_success_opt(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("FAKE_MODE", "success_opt")
        plan = compile_plan(calculation_doc("gaussian", GAUSSIAN_NATIVE, str(FAKE_G16)))
        items = assemble_items(plan, structure_set("s0"))
        supervisor = NativeProcessSupervisor()
        context = item_context(
            plan,
            "gaussian",
            str(FAKE_G16),
            str(tmp_path),
            str(tmp_path / "items"),
            supervisor,
            ["normal_termination"],
        )
        result = WorkItemExecutor().execute(items[0], context)
        assert result.is_completed
        assert result.structures[0].parent_ids == ("s0",)
        checkpoint_roles = {ref.role for ref in result.artifacts}
        assert "checkpoint" in checkpoint_roles

    def test_abnormal_fails_with_check_error(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("FAKE_MODE", "abnormal")
        plan = compile_plan(calculation_doc("orca", ORCA_NATIVE, str(FAKE_ORCA)))
        items = assemble_items(plan, structure_set("s0"))
        supervisor = NativeProcessSupervisor()
        context = item_context(
            plan,
            "orca",
            str(FAKE_ORCA),
            str(tmp_path),
            str(tmp_path / "items"),
            supervisor,
            ["normal_termination"],
        )
        result = WorkItemExecutor().execute(items[0], context)
        assert result.status is WorkItemStatus.FAILED
        assert result.error is not None
        assert result.error.code == "scientific_check_error"

    def test_imaginary_count_gates_completion(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("FAKE_MODE", "ts_candidate")
        checks = ["normal_termination", "frequencies_required", "imaginary_frequency_count"]
        plan = compile_plan(
            calculation_doc(
                "orca",
                ORCA_FREQ_NATIVE,
                str(FAKE_ORCA),
                checks=checks,
                check_params={"imaginary_frequency_count": {"expected": 1}},
            )
        )
        items = assemble_items(plan, structure_set("s0"))
        supervisor = NativeProcessSupervisor()
        context = item_context(
            plan,
            "orca",
            str(FAKE_ORCA),
            str(tmp_path),
            str(tmp_path / "items"),
            supervisor,
            checks,
        )
        assert WorkItemExecutor().execute(items[0], context).is_completed

        strict_plan = compile_plan(
            calculation_doc("orca", ORCA_FREQ_NATIVE, str(FAKE_ORCA), checks=checks)
        )
        strict_items = assemble_items(strict_plan, structure_set("s0"))
        strict_context = item_context(
            strict_plan,
            "orca",
            str(FAKE_ORCA),
            str(tmp_path),
            str(tmp_path / "strict"),
            supervisor,
            checks,
        )
        failed = WorkItemExecutor().execute(strict_items[0], strict_context)
        assert failed.status is WorkItemStatus.FAILED
        assert failed.error is not None
        assert failed.error.details["reason"] == "imaginary_count_mismatch"

    def test_unresolved_charge_is_input_error(self, tmp_path: Path) -> None:
        plan = compile_plan(calculation_doc("orca", ORCA_NATIVE, str(FAKE_ORCA)))
        bare = StructureSet.of(structure("free", charge=None, multiplicity=None))
        items = assemble_items(plan, bare)
        supervisor = NativeProcessSupervisor()
        context = item_context(
            plan,
            "orca",
            str(FAKE_ORCA),
            str(tmp_path),
            str(tmp_path / "items"),
            supervisor,
            ["normal_termination"],
        )
        result = WorkItemExecutor().execute(items[0], context)
        assert result.status is WorkItemStatus.FAILED
        assert result.error is not None
        assert result.error.code == "native_input_error"

    def test_missing_supervisor_is_environment_error(self, tmp_path: Path) -> None:
        plan = compile_plan(calculation_doc("orca", ORCA_NATIVE, str(FAKE_ORCA)))
        items = assemble_items(plan, structure_set("s0"))
        context = item_context(
            plan,
            "orca",
            str(FAKE_ORCA),
            str(tmp_path),
            str(tmp_path / "items"),
            None,
            ["normal_termination"],
        )
        result = WorkItemExecutor().execute(items[0], context)
        assert result.status is WorkItemStatus.FAILED
        assert result.error is not None
        assert result.error.code == "environment_error"


class TestBatchSection26Gate:
    """Three structures compile, assemble, execute, and publish one step."""

    def test_orca_three_items_one_step_result(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("FAKE_MODE", "success_opt")
        plan = compile_plan(calculation_doc("orca", ORCA_NATIVE, str(FAKE_ORCA)))
        items = assemble_items(plan, structure_set("s0", "s1", "s2"))
        assert len(items) == 3
        request = step_request(
            plan,
            items,
            "orca",
            str(FAKE_ORCA),
            str(tmp_path),
            str(tmp_path / "items"),
            ["normal_termination"],
        )
        step_result = batch_executor().execute_step(request)
        assert step_result.step_id == "s_opt"
        assert step_result.status is StepStatus.COMPLETED
        assert len(step_result.item_results) == 3
        assert len(step_result.structures) == 3
        assert len(step_result.results) >= 3
        assert step_result.summary["total"] == 3
        assert step_result.summary["completed"] == 3
        assert step_result.provenance is not None
        assert step_result.provenance.step_semantic_digest == plan.steps[0].step_semantic_digest
        payload = step_result.to_dict()
        assert set(payload) == {
            "step_id",
            "status",
            "structures",
            "results",
            "artifacts",
            "item_results",
            "diagnostics",
            "summary",
            "provenance",
        }
        text = repr(payload)
        for token in LEGACY_TOKENS:
            assert token not in text, token
        for item_result in step_result.item_results:
            for token in LEGACY_TOKENS:
                assert not hasattr(item_result, token), token
            assert set(item_result.to_dict()) == {
                "work_item_id",
                "status",
                "structures",
                "results",
                "artifacts",
                "diagnostics",
                "timing",
                "error",
                "recovery",
                "semantic_digest",
                "metadata",
            }

    def test_gaussian_three_items_one_step_result(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("FAKE_MODE", "success_opt")
        plan = compile_plan(calculation_doc("gaussian", GAUSSIAN_NATIVE, str(FAKE_G16)))
        items = assemble_items(plan, structure_set("s0", "s1", "s2"))
        request = step_request(
            plan,
            items,
            "gaussian",
            str(FAKE_G16),
            str(tmp_path),
            str(tmp_path / "items"),
            ["normal_termination"],
        )
        step_result = batch_executor().execute_step(request)
        assert step_result.status is StepStatus.COMPLETED
        assert len(step_result.item_results) == 3
        assert len(step_result.structures) == 3

    def test_require_all_with_one_failure(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("FAKE_MODE", "success_opt")
        plan = compile_plan(calculation_doc("orca", ORCA_NATIVE, str(FAKE_ORCA)))
        mixed = StructureSet.of(
            structure("s0"), structure("s1"), structure("s2", charge=None, multiplicity=None)
        )
        items = assemble_items(plan, mixed)
        request = step_request(
            plan,
            items,
            "orca",
            str(FAKE_ORCA),
            str(tmp_path),
            str(tmp_path / "items"),
            ["normal_termination"],
        )
        step_result = batch_executor().execute_step(request)
        assert step_result.status is StepStatus.FAILED
        assert step_result.summary["completed"] == 2
        assert step_result.summary["failed"] == 1

    def test_allow_partial_with_one_failure(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("FAKE_MODE", "success_opt")
        plan = compile_plan(
            calculation_doc(
                "orca",
                ORCA_NATIVE,
                str(FAKE_ORCA),
                completion={"mode": "allow_partial"},
            )
        )
        mixed = StructureSet.of(
            structure("s0"), structure("s1"), structure("s2", charge=None, multiplicity=None)
        )
        items = assemble_items(plan, mixed)
        request = step_request(
            plan,
            items,
            "orca",
            str(FAKE_ORCA),
            str(tmp_path),
            str(tmp_path / "items"),
            ["normal_termination"],
        )
        step_result = batch_executor().execute_step(request)
        assert step_result.status is StepStatus.PARTIAL
        assert len(step_result.structures) == 2

    def test_repository_records_transitions(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("FAKE_MODE", "success_opt")
        plan = compile_plan(calculation_doc("orca", ORCA_NATIVE, str(FAKE_ORCA)))
        items = assemble_items(plan, structure_set("s0", "s1"))
        repository = InMemoryWorkItemRepository()
        executor = BatchStepExecutor(WorkItemExecutor(), repository=repository)
        executor = executor.with_supervisor(NativeProcessSupervisor())
        request = step_request(
            plan,
            items,
            "orca",
            str(FAKE_ORCA),
            str(tmp_path),
            str(tmp_path / "items"),
            ["normal_termination"],
        )
        step_result = executor.execute_step(request)
        assert step_result.status is StepStatus.COMPLETED
        assert repository.started_ids() == tuple(sorted(item.id for item in items))
        for item in items:
            assert repository.get_result(item.id) is not None

    def test_reuse_store_short_circuits_relaunch(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("FAKE_MODE", "success_opt")
        plan = compile_plan(calculation_doc("orca", ORCA_NATIVE, str(FAKE_ORCA)))
        items = assemble_items(plan, structure_set("s0"))
        store = InMemoryReuseStore()
        first = BatchStepExecutor(WorkItemExecutor(), reuse_store=store).with_supervisor(
            NativeProcessSupervisor()
        )
        request = step_request(
            plan,
            items,
            "orca",
            str(FAKE_ORCA),
            str(tmp_path),
            str(tmp_path / "first"),
            ["normal_termination"],
        )
        assert first.execute_step(request).status is StepStatus.COMPLETED
        second = BatchStepExecutor(WorkItemExecutor(), reuse_store=store).with_supervisor(
            NativeProcessSupervisor()
        )
        request = step_request(
            plan,
            items,
            "orca",
            str(FAKE_ORCA),
            str(tmp_path),
            str(tmp_path / "second"),
            ["normal_termination"],
        )
        repeated = second.execute_step(request)
        assert repeated.status is StepStatus.COMPLETED
        codes = [item.code for item in repeated.item_results[0].diagnostics]
        assert "reuse_hit" in codes


class TestParallelismAndDigests:
    """Scheduler width never changes scientific identity or native inputs."""

    def _native_input_bytes(self, work_base: Path) -> list[bytes]:
        return sorted(
            path.read_bytes() for path in sorted(work_base.rglob("*.inp")) if path.is_file()
        )

    def test_max_parallel_widths_agree(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("FAKE_MODE", "success_opt")
        narrow_plan = compile_plan(
            calculation_doc(
                "orca", ORCA_NATIVE, str(FAKE_ORCA), scheduler={"max_parallel_items": 1}
            )
        )
        wide_plan = compile_plan(
            calculation_doc(
                "orca", ORCA_NATIVE, str(FAKE_ORCA), scheduler={"max_parallel_items": 4}
            )
        )
        assert narrow_plan.steps[0].step_semantic_digest == wide_plan.steps[0].step_semantic_digest
        narrow_items = assemble_items(narrow_plan, structure_set("s0", "s1", "s2"))
        wide_items = assemble_items(wide_plan, structure_set("s0", "s1", "s2"))
        assert [item.semantic_digest for item in narrow_items] == [
            item.semantic_digest for item in wide_items
        ]
        narrow_request = step_request(
            narrow_plan,
            narrow_items,
            "orca",
            str(FAKE_ORCA),
            str(tmp_path),
            str(tmp_path / "narrow"),
            ["normal_termination"],
        )
        wide_request = step_request(
            wide_plan,
            wide_items,
            "orca",
            str(FAKE_ORCA),
            str(tmp_path),
            str(tmp_path / "wide"),
            ["normal_termination"],
        )
        narrow_result = batch_executor().execute_step(narrow_request)
        wide_result = batch_executor().execute_step(wide_request)
        assert narrow_result.status is StepStatus.COMPLETED
        assert wide_result.status is StepStatus.COMPLETED
        assert self._native_input_bytes(tmp_path / "narrow") == self._native_input_bytes(
            tmp_path / "wide"
        )
        assert narrow_result.structures.geometry_digests == wide_result.structures.geometry_digests

    def test_execution_binding_is_digest_inert(self, tmp_path: Path) -> None:
        first = compile_plan(calculation_doc("orca", ORCA_NATIVE, str(FAKE_ORCA)))
        second = compile_plan(
            calculation_doc(
                "orca",
                ORCA_NATIVE,
                str(FAKE_ORCA),
                scheduler={"max_parallel_items": 4, "on_failure": "fail_fast"},
            )
        )
        assert first.definition_digest == second.definition_digest
        first_items = assemble_items(first, structure_set("s0"))
        second_items = assemble_items(second, structure_set("s0"))
        assert first_items[0].semantic_digest == second_items[0].semantic_digest

    def test_completion_policy_evaluation(self) -> None:
        require_all = CompletionPolicy(mode=CompletionMode.REQUIRE_ALL)
        assert (
            evaluate_step_status(require_all, [WorkItemStatus.COMPLETED, WorkItemStatus.FAILED])
            is StepStatus.FAILED
        )
        partial = CompletionPolicy(mode=CompletionMode.ALLOW_PARTIAL)
        assert (
            evaluate_step_status(partial, [WorkItemStatus.COMPLETED, WorkItemStatus.FAILED])
            is StepStatus.PARTIAL
        )
        assert (
            evaluate_step_status(require_all, [WorkItemStatus.COMPLETED, WorkItemStatus.CANCELLED])
            is StepStatus.CANCELLED
        )


class TestEnvironmentMeasurement:
    """Executable content moves the environment digest; paths do not."""

    def test_measure_fake_executable(self) -> None:
        identity = measure_executable(str(FAKE_ORCA), adapter=get_program_adapter("orca"))
        assert identity.digest.startswith("sha256:")
        assert identity.program_version is None
        assert identity.size_bytes > 0

    def test_content_change_moves_digest(self, tmp_path: Path) -> None:
        baseline = measure_executable(str(FAKE_ORCA), adapter=get_program_adapter("orca"))
        altered = tmp_path / "fake_orca.py"
        altered.write_bytes(Path(str(FAKE_ORCA)).read_bytes() + b"# extra\n")
        changed = measure_executable(str(altered), adapter=get_program_adapter("orca"))
        assert changed.digest != baseline.digest
        measurer = EnvironmentMeasurer()
        first = measurer.build_environment(
            str(FAKE_ORCA), adapter=get_program_adapter("orca"), target="node-1"
        )
        second = measurer.build_environment(
            str(altered), adapter=get_program_adapter("orca"), target="node-1"
        )
        assert isinstance(first, ExecutionEnvironment)
        assert first.digest() != second.digest()

    def test_relocated_identical_binary_measures_same(self, tmp_path: Path) -> None:
        relocated = tmp_path / "relocated_orca.py"
        shutil.copy2(str(FAKE_ORCA), relocated)
        first = measure_executable(str(FAKE_ORCA), adapter=get_program_adapter("orca"))
        second = measure_executable(str(relocated), adapter=get_program_adapter("orca"))
        assert first.digest == second.digest
        measurer = EnvironmentMeasurer()
        assert measurer.measure(str(FAKE_ORCA), adapter=get_program_adapter("orca")) is (
            measurer.measure(str(FAKE_ORCA), adapter=get_program_adapter("orca"))
        )

    def test_target_moves_environment_digest(self) -> None:
        measurer = EnvironmentMeasurer()
        first = measurer.build_environment(
            str(FAKE_ORCA), adapter=get_program_adapter("orca"), target="node-1"
        )
        second = measurer.build_environment(
            str(FAKE_ORCA), adapter=get_program_adapter("orca"), target="node-2"
        )
        assert first.digest() != second.digest()

    def test_unknown_executable_rejected(self) -> None:
        from confflow.domain.errors import DomainError

        with pytest.raises(DomainError):
            measure_executable("/nonexistent/bin/orca", adapter=get_program_adapter("orca"))

    def test_executable_identity_is_stable(self) -> None:
        first = measure_executable(str(FAKE_G16))
        second = measure_executable(str(FAKE_G16))
        assert first.sha256 == second.sha256
        assert len(first.sha256) == 64
        assert first.digest == f"sha256:{first.sha256}"


class TestCancellation:
    """Confirmed cancellation stops items; unconfirmed cancellation never rescues."""

    def test_confirmed_cancel_with_trap(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("FAKE_MODE", "cancel_trap")
        plan = compile_plan(
            calculation_doc(
                "orca", ORCA_NATIVE, str(FAKE_ORCA), scheduler={"max_parallel_items": 4}
            )
        )
        items = assemble_items(plan, structure_set("s0", "s1", "s2"))
        stop = threading.Event()
        timer = threading.Timer(1.0, stop.set)
        timer.start()
        try:
            planned = plan.steps[0]
            request = StepExecutionRequest(
                step=planned,
                items=tuple(items),
                scientific=planned.scientific,
                scientific_defaults=plan.scientific_defaults,
                adapter=get_program_adapter("orca"),
                profile=PROFILES["standard"],
                checks=(CHECKS["normal_termination"],),
                recovery=RECOVERIES["none"],
                execution_binding=binding_for(str(FAKE_ORCA)),
                run_root=str(tmp_path),
                work_base=str(tmp_path / "items"),
                environment=None,
                definition_digest=plan.definition_digest,
                should_cancel=stop.is_set,
            )
            executor = BatchStepExecutor(WorkItemExecutor()).with_supervisor(
                NativeProcessSupervisor()
            )
            step_result = executor.execute_step(request)
        finally:
            timer.cancel()
        assert step_result.status is StepStatus.CANCELLED
        assert step_result.summary["cancelled"] == 3
        for item_result in step_result.item_results:
            assert item_result.status is WorkItemStatus.CANCELLED
            assert item_result.recovery is not None
            assert item_result.recovery.attempted is False

    def test_unconfirmed_cancel_never_rescues(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("FAKE_MODE", "success_opt")
        plan = compile_plan(calculation_doc("orca", ORCA_NATIVE, str(FAKE_ORCA)))
        items = assemble_items(plan, structure_set("s0"))
        recording = RecordingRecovery()
        context = item_context(
            plan,
            "orca",
            str(FAKE_ORCA),
            str(tmp_path),
            str(tmp_path / "items"),
            FakeUnconfirmedSupervisor(),
            ["normal_termination"],
            recovery=recording,
        )
        result = WorkItemExecutor().execute(items[0], context, should_cancel=lambda: True)
        assert result.status is WorkItemStatus.FAILED
        assert result.error is not None
        assert result.error.code == "cancellation_error"
        assert result.recovery is not None
        assert result.recovery.attempted is False
        assert recording.evaluations == 0
        assert recording.executions == 0

    def test_prelaunch_cancellation_marks_items_without_launch(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A stop set before launch cancels every item without starting processes."""
        monkeypatch.setenv("FAKE_MODE", "cancel_trap")
        plan = compile_plan(
            calculation_doc(
                "orca", ORCA_NATIVE, str(FAKE_ORCA), scheduler={"max_parallel_items": 1}
            )
        )
        items = assemble_items(plan, structure_set("s0", "s1", "s2"))
        stop = threading.Event()
        stop.set()
        planned = plan.steps[0]
        request = StepExecutionRequest(
            step=planned,
            items=tuple(items),
            scientific=planned.scientific,
            scientific_defaults=plan.scientific_defaults,
            adapter=get_program_adapter("orca"),
            profile=PROFILES["standard"],
            checks=(CHECKS["normal_termination"],),
            recovery=RECOVERIES["none"],
            execution_binding=binding_for(str(FAKE_ORCA)),
            run_root=str(tmp_path),
            work_base=str(tmp_path / "items"),
            environment=None,
            definition_digest=plan.definition_digest,
            should_cancel=stop.is_set,
        )
        executor = BatchStepExecutor(WorkItemExecutor()).with_supervisor(NativeProcessSupervisor())
        step_result = executor.execute_step(request)
        assert step_result.status is StepStatus.CANCELLED
        assert [item.work_item_id for item in step_result.item_results] == [
            item.id for item in items
        ]
        for item_result in step_result.item_results:
            assert item_result.status is WorkItemStatus.CANCELLED
            assert item_result.error is not None
            assert item_result.error.code == "cancellation_error"
