#!/usr/bin/env python3

"""V4-2 branch coverage: executor, batch, supervisor, and environment edges.

Scripted stubs drive every pre-launch validation branch, launch/wait
outcome, check/recovery decision, batch policy, and measurement edge without
real native programs (except a handful of instant local-process cases).
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import pytest

from confflow.domain import (
    ArtifactLocator,
    ArtifactRef,
    ArtifactSet,
    Diagnostic,
    DiagnosticSeverity,
    DomainError,
    FrozenDict,
    StructureSet,
    WorkItem,
    WorkItemInputs,
    WorkItemStatus,
    work_item_semantic_digest,
)
from confflow.domain.completion import StepStatus
from confflow.execution import ExecutionBinding
from confflow.execution.batch import (
    BatchStepExecutor,
    InMemoryReuseStore,
    InMemoryWorkItemRepository,
    StepExecutionRequest,
)
from confflow.execution.checks import CheckContext, CheckOutcome
from confflow.execution.checks_standard import CHECKS
from confflow.execution.environment import EnvironmentMeasurer, measure_executable
from confflow.execution.native import (
    CancelOutcome,
    GeometryOutput,
    InputFile,
    MaterializedNativeInput,
    NativeErrorCode,
    NativeExecutionRequest,
    NativeExecutionResult,
    NativeHandle,
    NativeResult,
    NativeStatus,
    ParsedGeometry,
    ProgramName,
)
from confflow.execution.process import NativeProcessError, NativeProcessSupervisor
from confflow.execution.profile_standard import PROFILES
from confflow.execution.recovery import RecoveryDecision, RecoveryExecution
from confflow.execution.recovery_standard import RECOVERIES
from confflow.execution.work_item_executor import (
    ItemExecutionContext,
    WorkItemExecutor,
    _diagnostic,
    check_code,
    error_result,
    select_driving_structure,
)
from tests.v4._builders import (
    assemble,
    calc_step,
    compile_doc,
    run_inputs,
    structure,
    structure_set,
    v4_doc,
)

STRUCTURE_INPUTS = {"structures": {"kind": "structure", "cardinality": "many"}}


def _document(**kwargs: Any) -> dict[str, Any]:
    """Build a single-calculation document."""
    return v4_doc(
        [calc_step("s_opt", bindings={"structure": {"source": {"run": "structures"}}}, **kwargs)],
        inputs=STRUCTURE_INPUTS,
    )


def _compiled(**kwargs: Any) -> Any:
    """Compile the standard document."""
    result = compile_doc(_document(**kwargs))
    assert result.ok, [(item.code, item.message) for item in result.errors]
    assert result.plan is not None
    return result.plan


def _item(plan: Any, structure_id: str = "s0") -> WorkItem:
    """Assemble one work item for *structure_id*."""
    assembly = assemble(plan, run_inputs(structures={"structures": structure_set(structure_id)}))
    assert assembly.ok, [item.message for item in assembly.errors]
    return assembly.items[0]


def _geometry(structure_id: str = "s0") -> ParsedGeometry:
    """Return the parsed geometry of a builder structure."""
    record = structure(structure_id)
    return ParsedGeometry(atoms=tuple(record.atoms), coordinates=tuple(record.coordinates))


# ---------------------------------------------------------------------------
# Scripted stubs
# ---------------------------------------------------------------------------


class StubAdapter:
    """Program adapter with scripted render/parse behavior."""

    def __init__(self, program: ProgramName = ProgramName.GAUSSIAN) -> None:
        self._program = program
        self.materialize_error: Exception | None = None
        self.build_error: Exception | None = None
        self.parse_result: NativeResult | None = None
        self.parse_error: Exception | None = None
        self.discovered: ArtifactSet = ArtifactSet()
        self.rendered: list[Any] = []

    @property
    def program_name(self) -> ProgramName:
        return self._program

    @property
    def adapter_version(self) -> str:
        return "test.adapter.v1"

    @property
    def parser_version(self) -> str:
        return "test.parser.v1"

    @property
    def input_extension(self) -> str:
        return "inp"

    @property
    def log_extension(self) -> str:
        return "log"

    @property
    def default_executable(self) -> str:
        return "stub-program"

    def materialize_native_input(self, inputs: Any) -> MaterializedNativeInput:
        if self.materialize_error is not None:
            raise self.materialize_error
        self.rendered.append(inputs)
        return MaterializedNativeInput(
            program=self._program,
            main_input_name="job.inp",
            files=(InputFile(name="job.inp", content="input"),),
        )

    def build_execution_request(
        self,
        materialized: Any,
        *,
        executable: str,
        work_dir: str,
        env: dict[str, str],
        walltime_seconds: float | None,
    ) -> NativeExecutionRequest:
        if self.build_error is not None:
            raise self.build_error
        return NativeExecutionRequest(
            executable=executable,
            argv=(executable, "job.inp"),
            work_dir=work_dir,
            env=FrozenDict(env),
            walltime_seconds=walltime_seconds,
        )

    def parse_native_result(
        self, *, work_dir: str, log_file_name: str, materialized: Any
    ) -> NativeResult:
        if self.parse_error is not None:
            raise self.parse_error
        assert self.parse_result is not None
        return self.parse_result

    def discover_artifacts(self, **kwargs: Any) -> ArtifactSet:
        return self.discovered

    def environment_probe(self, executable: str) -> dict[str, Any]:
        return {}


class StubSupervisor:
    """Process supervisor with a scripted poll/cancel/collect program."""

    def __init__(self) -> None:
        self.submit_error: Exception | None = None
        self.poll_script: list[NativeStatus] = [NativeStatus(is_terminal=True, exit_code=0)]
        self.poll_error: Exception | None = None
        self.cancel_outcome: CancelOutcome = CancelOutcome(confirmed=True)
        self.cancel_error: Exception | None = None
        self.collected: NativeExecutionResult = NativeExecutionResult(
            exit_code=0, wall_time_seconds=0.1
        )
        self.collect_error: Exception | None = None
        self.polls = 0
        self.cancels = 0

    def submit(self, request: NativeExecutionRequest) -> NativeHandle:
        if self.submit_error is not None:
            raise self.submit_error
        return NativeHandle(key="stub", pid=4242)

    def poll(self, handle: NativeHandle) -> NativeStatus:
        self.polls += 1
        if self.poll_error is not None:
            raise self.poll_error
        if self.poll_script:
            return self.poll_script.pop(0)
        return NativeStatus(is_terminal=True, exit_code=0)

    def cancel(self, handle: NativeHandle, *, grace_seconds: float = 2.0) -> CancelOutcome:
        self.cancels += 1
        if self.cancel_error is not None:
            raise self.cancel_error
        return self.cancel_outcome

    def collect(self, handle: NativeHandle) -> NativeExecutionResult:
        if self.collect_error is not None:
            raise self.collect_error
        return self.collected


class StubRecovery:
    """Recovery policy with scripted evaluate/execute."""

    def __init__(self, name: str = "stub") -> None:
        self._name = name
        self.decision = RecoveryDecision(attempt=False, reason="declined")
        self.execution: RecoveryExecution | None = None
        self.execute_error: Exception | None = None
        self.evaluations = 0

    @property
    def name(self) -> str:
        return self._name

    @property
    def contract_version(self) -> str:
        return "test.recovery.v1"

    def evaluate(self, context: Any) -> RecoveryDecision:
        self.evaluations += 1
        return self.decision

    def execute(self, context: Any, driver: Any) -> RecoveryExecution | None:
        if self.execute_error is not None:
            raise self.execute_error
        return self.execution


class ExplodingCheck:
    """Check that raises during evaluation."""

    @property
    def name(self) -> str:
        return "exploding"

    @property
    def contract_version(self) -> str:
        return "test.check.v1"

    def run(self, context: CheckContext) -> CheckOutcome:
        raise RuntimeError("boom")


def _context(
    plan: Any,
    item: WorkItem,
    run_root: str,
    supervisor: Any,
    adapter: Any,
    *,
    checks: tuple = (),
    recovery: Any = None,
    executable: str | None = None,
) -> ItemExecutionContext:
    """Build an item execution context."""
    from confflow.workflow.v4 import parse_workflow_document

    parsed = parse_workflow_document(_document())
    assert parsed.definition is not None
    scientific = parsed.definition.steps[0].scientific
    assert scientific is not None
    return ItemExecutionContext(
        step_id="s_opt",
        scientific=scientific,
        scientific_defaults=parsed.definition.scientific_defaults,
        adapter=adapter,
        profile=PROFILES["standard"],
        checks=checks,
        recovery=recovery if recovery is not None else RECOVERIES["none"],
        execution_binding=(
            ExecutionBinding(binding_id="t", executable=executable) if executable else None
        ),
        run_root=run_root,
        work_base=os.path.join(run_root, "items"),
        supervisor=supervisor,
        poll_interval_seconds=0.001,
    )


def _terminated_result(geometry: bool = True) -> NativeResult:
    """Build a terminated native result."""
    return NativeResult(
        program=ProgramName.GAUSSIAN,
        terminated_normally=True,
        geometry_output=GeometryOutput.PRODUCED if geometry else GeometryOutput.NONE,
        final_geometry=_geometry() if geometry else None,
        energies_hartree=FrozenDict({"electronic": -76.4}),
        log_file_name="job.log",
    )


# ---------------------------------------------------------------------------
# Executor pre-launch branches
# ---------------------------------------------------------------------------


class TestExecutorPrelaunch:
    """Every validation gate before the first subprocess."""

    def test_no_supervisor_is_an_environment_error(self, tmp_path: Path) -> None:
        plan = _compiled()
        item = _item(plan)
        context = _context(plan, item, str(tmp_path), None, StubAdapter())
        result = WorkItemExecutor().execute(item, context)
        assert result.status is WorkItemStatus.FAILED
        assert result.error is not None
        assert result.error.code == "environment_error"

    def test_missing_driving_structure(self, tmp_path: Path) -> None:
        plan = _compiled()
        item = _item(plan)
        bad = WorkItem(
            id=item.id,
            logical_key=item.logical_key,
            step_id=item.step_id,
            named_inputs=WorkItemInputs(),
            resources=item.resources,
            semantic_digest=item.semantic_digest,
        )
        context = _context(plan, item, str(tmp_path), StubSupervisor(), StubAdapter())
        result = WorkItemExecutor().execute(bad, context)
        assert result.status is WorkItemStatus.FAILED
        assert result.error is not None
        assert result.error.code == "native_input_error"

    def test_parity_error_blocks_rendering(self, tmp_path: Path) -> None:
        plan = _compiled()
        template = _item(plan)
        odd = structure("s0", charge=0, multiplicity=2)
        manual = WorkItem(
            id=template.id,
            logical_key=template.logical_key,
            step_id=template.step_id,
            named_inputs=WorkItemInputs(
                structures=FrozenDict({"structure": StructureSet.of(odd)}),
            ),
            resources=template.resources,
            semantic_digest=work_item_semantic_digest(
                step_semantic_digest="sha256:" + "a" * 64,
                input_payload={},
                resources=template.resources,
            ),
        )
        context = _context(plan, template, str(tmp_path), StubSupervisor(), StubAdapter())
        result = WorkItemExecutor().execute(manual, context)
        assert result.status is WorkItemStatus.FAILED
        assert result.error is not None
        assert result.error.code == "scientific_parameter_conflict"

    def test_unresolved_charge_blocks_rendering(self, tmp_path: Path) -> None:
        plan = _compiled()
        bare = structure("s0", charge=None, multiplicity=None)
        assembly = assemble(plan, run_inputs(structures={"structures": StructureSet.of(bare)}))
        assert assembly.ok
        item = assembly.items[0]
        context = _context(plan, item, str(tmp_path), StubSupervisor(), StubAdapter())
        result = WorkItemExecutor().execute(item, context)
        assert result.status is WorkItemStatus.FAILED
        assert result.error is not None
        assert "charge and multiplicity" in result.error.message

    def test_materialize_error_maps_to_input_error(self, tmp_path: Path) -> None:
        plan = _compiled()
        item = _item(plan)
        adapter = StubAdapter()
        adapter.materialize_error = ValueError("bad keyword")
        context = _context(plan, item, str(tmp_path), StubSupervisor(), adapter)
        result = WorkItemExecutor().execute(item, context)
        assert result.status is WorkItemStatus.FAILED
        assert result.error is not None
        assert result.error.code == "native_input_error"
        assert "bad keyword" in result.error.message

    def test_missing_executable_is_environment_error(self, tmp_path: Path) -> None:
        plan = _compiled()
        item = _item(plan)
        context = _context(
            plan,
            item,
            str(tmp_path),
            StubSupervisor(),
            StubAdapter(),
            executable="/nonexistent/absolute/program",
        )
        result = WorkItemExecutor().execute(item, context)
        assert result.status is WorkItemStatus.FAILED
        assert result.error is not None
        assert result.error.code == "environment_error"

    def test_build_request_error_maps_to_input_error(self, tmp_path: Path) -> None:
        plan = _compiled()
        item = _item(plan)
        adapter = StubAdapter()
        adapter.build_error = TypeError("bad env")
        context = _context(
            plan,
            item,
            str(tmp_path),
            StubSupervisor(),
            adapter,
            executable="/bin/true",
        )
        result = WorkItemExecutor().execute(item, context)
        assert result.status is WorkItemStatus.FAILED
        assert result.error is not None
        assert result.error.code == "native_input_error"

    def test_checkpoint_staging_validates_presence(self, tmp_path: Path) -> None:
        plan = _compiled()
        item = _item(plan)
        artifact = ArtifactRef(
            id="chk",
            role="checkpoint",
            locator=ArtifactLocator.run_relative("steps/prev/f.chk"),
            subject_structure_id="s0",
        )
        bad = WorkItem(
            id=item.id,
            logical_key=item.logical_key,
            step_id=item.step_id,
            named_inputs=WorkItemInputs(
                structures=item.named_inputs.structures,
                artifacts=FrozenDict({"checkpoint": ArtifactSet.of(artifact)}),
            ),
            resources=item.resources,
            semantic_digest=item.semantic_digest,
        )
        context = _context(plan, item, str(tmp_path), StubSupervisor(), StubAdapter())
        result = WorkItemExecutor().execute(bad, context)
        assert result.status is WorkItemStatus.FAILED
        assert result.error is not None
        assert result.error.code == "artifact_error"

    def test_checkpoint_checksum_mismatch(self, tmp_path: Path) -> None:
        (tmp_path / "steps" / "prev").mkdir(parents=True)
        (tmp_path / "steps" / "prev" / "f.chk").write_bytes(b"bytes")
        plan = _compiled()
        item = _item(plan)
        artifact = ArtifactRef(
            id="chk",
            role="checkpoint",
            locator=ArtifactLocator.run_relative("steps/prev/f.chk"),
            checksum="sha256:" + "0" * 64,
            subject_structure_id="s0",
        )
        bad = WorkItem(
            id=item.id,
            logical_key=item.logical_key,
            step_id=item.step_id,
            named_inputs=WorkItemInputs(
                structures=item.named_inputs.structures,
                artifacts=FrozenDict({"checkpoint": ArtifactSet.of(artifact)}),
            ),
            resources=item.resources,
            semantic_digest=item.semantic_digest,
        )
        context = _context(plan, item, str(tmp_path), StubSupervisor(), StubAdapter())
        result = WorkItemExecutor().execute(bad, context)
        assert result.status is WorkItemStatus.FAILED
        assert result.error is not None
        assert "checksum mismatch" in result.error.message

    def test_checkpoint_symlink_escape_refused(self, tmp_path: Path) -> None:
        outside = tmp_path / "outside.chk"
        outside.write_bytes(b"x")
        (tmp_path / "run").mkdir()
        os.symlink(str(outside), tmp_path / "run" / "link.chk")
        plan = _compiled()
        item = _item(plan)
        artifact = ArtifactRef(
            id="chk",
            role="checkpoint",
            locator=ArtifactLocator.run_relative("link.chk"),
            subject_structure_id="s0",
        )
        bad = WorkItem(
            id=item.id,
            logical_key=item.logical_key,
            step_id=item.step_id,
            named_inputs=WorkItemInputs(
                structures=item.named_inputs.structures,
                artifacts=FrozenDict({"checkpoint": ArtifactSet.of(artifact)}),
            ),
            resources=item.resources,
            semantic_digest=item.semantic_digest,
        )
        context = _context(plan, item, str(tmp_path / "run"), StubSupervisor(), StubAdapter())
        result = WorkItemExecutor().execute(bad, context)
        assert result.status is WorkItemStatus.FAILED
        assert result.error is not None
        assert "escapes the run root" in result.error.message


# ---------------------------------------------------------------------------
# Executor launch/wait branches
# ---------------------------------------------------------------------------


class TestExecutorLaunch:
    """Submit, poll, cancel, walltime, and collect outcomes."""

    def test_submit_failure_is_handle_lost(self, tmp_path: Path) -> None:
        plan = _compiled()
        item = _item(plan)
        supervisor = StubSupervisor()
        supervisor.submit_error = OSError("fork failed")
        context = _context(
            plan, item, str(tmp_path), supervisor, StubAdapter(), executable="/bin/true"
        )
        result = WorkItemExecutor().execute(item, context)
        assert result.status is WorkItemStatus.FAILED
        assert result.error is not None
        assert "handle was lost" in result.error.message

    def test_pre_cancel_marks_cancelled(self, tmp_path: Path) -> None:
        plan = _compiled()
        item = _item(plan)
        supervisor = StubSupervisor()
        supervisor.poll_script = [NativeStatus(is_terminal=False)]
        context = _context(
            plan, item, str(tmp_path), supervisor, StubAdapter(), executable="/bin/true"
        )
        result = WorkItemExecutor().execute(item, context, should_cancel=lambda: True)
        assert result.status is WorkItemStatus.CANCELLED
        assert result.error is not None
        assert result.error.code == "cancellation_error"

    def test_unconfirmed_cancel_fails_without_rescue(self, tmp_path: Path) -> None:
        plan = _compiled()
        item = _item(plan)
        supervisor = StubSupervisor()
        supervisor.poll_script = [NativeStatus(is_terminal=False)] * 3
        supervisor.cancel_outcome = CancelOutcome(confirmed=False, detail="still live")
        recovery = StubRecovery("ts_rescue_scan")
        context = _context(
            plan,
            item,
            str(tmp_path),
            supervisor,
            StubAdapter(),
            executable="/bin/true",
            recovery=recovery,
        )
        result = WorkItemExecutor().execute(item, context, should_cancel=lambda: True)
        assert result.status is WorkItemStatus.FAILED
        assert result.error is not None
        assert result.error.code == "cancellation_error"
        assert result.recovery is not None
        assert result.recovery.attempted is False
        assert recovery.evaluations == 0

    def test_walltime_timeout(self, tmp_path: Path) -> None:
        from confflow.execution.contracts import ExecutionBinding

        plan = _compiled()
        item = _item(plan)
        supervisor = StubSupervisor()
        supervisor.poll_script = [NativeStatus(is_terminal=False)] * 1000
        adapter = StubAdapter()
        adapter.parse_result = _terminated_result()
        context = ItemExecutionContext(
            step_id="s_opt",
            scientific=_scientific(),
            scientific_defaults=_defaults(),
            adapter=adapter,
            profile=PROFILES["standard"],
            checks=(),
            recovery=RECOVERIES["none"],
            execution_binding=ExecutionBinding(
                binding_id="t",
                executable="/bin/true",
                walltime_seconds=1,
            ),
            run_root=str(tmp_path),
            work_base=os.path.join(str(tmp_path), "items"),
            supervisor=supervisor,
            poll_interval_seconds=0.001,
        )
        result = WorkItemExecutor().execute(item, context)
        assert result.status is WorkItemStatus.FAILED
        assert result.error is not None
        assert result.error.code == "native_execution_error"

    def test_collect_failure_is_handle_lost(self, tmp_path: Path) -> None:
        plan = _compiled()
        item = _item(plan)
        supervisor = StubSupervisor()
        supervisor.collect_error = OSError("reaped")
        context = _context(
            plan, item, str(tmp_path), supervisor, StubAdapter(), executable="/bin/true"
        )
        result = WorkItemExecutor().execute(item, context)
        assert result.status is WorkItemStatus.FAILED
        assert "handle was lost" in (result.error.message if result.error else "")

    def test_parse_error_maps(self, tmp_path: Path) -> None:
        plan = _compiled()
        item = _item(plan)
        adapter = StubAdapter()
        adapter.parse_error = ValueError("truncated log")
        context = _context(
            plan, item, str(tmp_path), StubSupervisor(), adapter, executable="/bin/true"
        )
        result = WorkItemExecutor().execute(item, context)
        assert result.status is WorkItemStatus.FAILED
        assert result.error is not None
        assert result.error.code == "native_parse_error"

    def test_success_with_checks(self, tmp_path: Path) -> None:
        plan = _compiled()
        item = _item(plan)
        adapter = StubAdapter()
        adapter.parse_result = _terminated_result()
        context = _context(
            plan,
            item,
            str(tmp_path),
            StubSupervisor(),
            adapter,
            executable="/bin/true",
            checks=(CHECKS["normal_termination"], CHECKS["geometry_required"]),
        )
        result = WorkItemExecutor().execute(item, context)
        assert result.status is WorkItemStatus.COMPLETED
        assert len(result.structures) == 1
        assert result.error is None

    def test_exploding_check_fails_closed(self, tmp_path: Path) -> None:
        plan = _compiled()
        item = _item(plan)
        adapter = StubAdapter()
        adapter.parse_result = _terminated_result()
        context = _context(
            plan,
            item,
            str(tmp_path),
            StubSupervisor(),
            adapter,
            executable="/bin/true",
            checks=(ExplodingCheck(),),
        )
        result = WorkItemExecutor().execute(item, context)
        assert result.status is WorkItemStatus.FAILED
        assert result.error is not None
        assert result.error.code == "scientific_check_error"
        assert any(item.details.get("reason") == "check_raised" for item in result.diagnostics)


def _scientific():
    """Build a minimal scientific definition."""
    from confflow.domain import FrozenDict
    from confflow.workflow.v4.document import ScientificDefinition

    return ScientificDefinition(
        program="stub",
        result_profile="standard",
        native=FrozenDict({"keyword": "x"}),
    )


def _defaults():
    """Build empty scientific defaults."""
    from confflow.workflow.v4.document import ScientificDefaults

    return ScientificDefaults()


def _binding():
    """Build a test execution binding for /bin/true."""
    from confflow.execution import ExecutionBinding as _Binding

    return _Binding(binding_id="t", executable="/bin/true")


# ---------------------------------------------------------------------------
# Executor recovery branches
# ---------------------------------------------------------------------------


class TestExecutorRecovery:
    """Recovery decline, failure, error, and success paths."""

    def _recovery_context(self, tmp_path: Path, recovery: Any, adapter: Any):
        plan = _compiled()
        item = _item(plan)
        adapter.parse_result = _terminated_result(geometry=False)
        context = _context(
            plan,
            item,
            str(tmp_path),
            StubSupervisor(),
            adapter,
            executable="/bin/true",
            checks=(CHECKS["geometry_required"],),
            recovery=recovery,
        )
        return item, context

    def test_recovery_declined_keeps_failure(self, tmp_path: Path) -> None:
        adapter = StubAdapter()
        item, context = self._recovery_context(tmp_path, StubRecovery(), adapter)
        result = WorkItemExecutor().execute(item, context)
        assert result.status is WorkItemStatus.FAILED
        assert result.recovery is not None
        assert result.recovery.attempted is False
        assert any(item.code == "recovery_declined" for item in result.diagnostics)

    def test_recovery_execute_none_records_failure(self, tmp_path: Path) -> None:
        adapter = StubAdapter()
        recovery = StubRecovery()
        recovery.decision = RecoveryDecision(attempt=True, reason="try")
        item, context = self._recovery_context(tmp_path, recovery, adapter)
        result = WorkItemExecutor().execute(item, context)
        assert result.status is WorkItemStatus.FAILED
        assert result.recovery is not None
        assert result.recovery.attempted is True
        assert result.recovery.succeeded is False
        assert any(item.code == "recovery_failed" for item in result.diagnostics)

    def test_recovery_execute_raise_records_failure(self, tmp_path: Path) -> None:
        adapter = StubAdapter()
        recovery = StubRecovery()
        recovery.decision = RecoveryDecision(attempt=True, reason="try")
        recovery.execute_error = RuntimeError("driver blew up")
        item, context = self._recovery_context(tmp_path, recovery, adapter)
        result = WorkItemExecutor().execute(item, context)
        assert result.status is WorkItemStatus.FAILED
        assert result.recovery is not None
        assert result.recovery.succeeded is False
        assert any("driver blew up" in item.message for item in result.diagnostics)

    def test_recovery_success_recovers_item(self, tmp_path: Path) -> None:
        from confflow.execution.recovery import RecoveryExecution

        adapter = StubAdapter()
        recovery = StubRecovery()
        recovery.decision = RecoveryDecision(attempt=True, reason="try")
        recovery.execution = RecoveryExecution(
            native_result=_terminated_result(geometry=True),
            execution_result=NativeExecutionResult(exit_code=0, wall_time_seconds=0.2),
            materialized=MaterializedNativeInput(
                program=ProgramName.GAUSSIAN, main_input_name="r.gjf"
            ),
            diagnostics=(
                Diagnostic(
                    code="rescue_scan",
                    message="peak found",
                    severity=DiagnosticSeverity.INFO,
                ),
            ),
            metadata=FrozenDict({"rescued_by_scan": True}),
        )
        item, context = self._recovery_context(tmp_path, recovery, adapter)
        result = WorkItemExecutor().execute(item, context)
        assert result.status is WorkItemStatus.COMPLETED
        assert result.recovery is not None
        assert result.recovery.attempted is True
        assert result.recovery.succeeded is True
        assert any(item.code == "recovery_succeeded" for item in result.diagnostics)

    def test_abnormal_termination_recovers_through_policy(self, tmp_path: Path) -> None:
        adapter = StubAdapter()
        recovery = StubRecovery()
        item, context = self._recovery_context(tmp_path, recovery, adapter)
        bad = _terminated_result()
        adapter.parse_result = NativeResult(
            program=bad.program,
            terminated_normally=False,
            geometry_output=GeometryOutput.NONE,
            log_file_name="job.log",
        )
        result = WorkItemExecutor().execute(item, context)
        assert result.status is WorkItemStatus.FAILED
        assert any(
            item.details.get("reason") == "abnormal_termination" for item in result.diagnostics
        )
        assert recovery.evaluations == 1


# ---------------------------------------------------------------------------
# Module helpers
# ---------------------------------------------------------------------------


class TestModuleHelpers:
    """sanitize_job_name, check_code, error_result, select_driving_structure."""

    def test_sanitize_job_name(self) -> None:
        from confflow.execution.work_item_executor import sanitize_job_name as clean

        assert clean("s_opt:s0") == "s_opt_s0"
        assert clean("a/b\\c:d") == "a_b_c_d"
        assert clean("...") == "job"
        assert clean("") == "job"

    def test_check_code(self) -> None:
        assert check_code(NativeErrorCode.NATIVE_INPUT_ERROR) == "native_input_error"
        assert check_code("custom") == "custom"

    def test_error_result_shape(self) -> None:
        plan = _compiled()
        item = _item(plan)
        result = error_result(item, NativeErrorCode.NATIVE_INPUT_ERROR, "bad")
        assert result.status is WorkItemStatus.FAILED
        assert result.error is not None
        assert result.error.code == "native_input_error"
        assert result.error.retryable is False
        assert result.semantic_digest == item.semantic_digest
        assert result.structures.is_empty

    def test_error_result_cancelled_status(self) -> None:
        from confflow.domain.completion import WorkItemStatus as Status

        plan = _compiled()
        item = _item(plan)
        result = error_result(
            item,
            NativeErrorCode.CANCELLATION_ERROR,
            "stop",
            status=Status.CANCELLED,
        )
        assert result.status is Status.CANCELLED

    def test_diagnostic_builder(self) -> None:
        diagnostic = _diagnostic(
            NativeErrorCode.NATIVE_PARSE_ERROR,
            "truncated",
            step_id="s",
            work_item_id="w",
            logical_key="s:a",
            field_path="f",
            details={"r": 1},
        )
        assert diagnostic.code == "native_parse_error"
        assert diagnostic.field_path == "f"
        assert diagnostic.details["r"] == 1

    def test_select_driving_structure_rejects_multi(self) -> None:
        plan = _compiled()
        item = _item(plan)
        records = structure_set("s0", "s1")
        bad = WorkItem(
            id=item.id,
            logical_key=item.logical_key,
            step_id=item.step_id,
            named_inputs=WorkItemInputs(
                structures=FrozenDict({"structure": records}),
            ),
            resources=item.resources,
            semantic_digest=item.semantic_digest,
        )
        with pytest.raises(DomainError):
            select_driving_structure(bad)

    def test_subject_of_none_without_driving(self) -> None:
        from confflow.execution.work_item_executor import WorkItemExecutor as E

        plan = _compiled()
        item = _item(plan)
        bad = WorkItem(
            id=item.id,
            logical_key=item.logical_key,
            step_id=item.step_id,
            named_inputs=WorkItemInputs(),
            resources=item.resources,
            semantic_digest=item.semantic_digest,
        )
        assert E._subject_of(bad) is None


# ---------------------------------------------------------------------------
# Batch branches
# ---------------------------------------------------------------------------


class TestBatchBranches:
    """Request validation, preflight, sequential path, reuse, and records."""

    def _request(self, plan: Any, items: Any, **overrides: Any) -> StepExecutionRequest:
        from confflow.execution import ExecutionBinding as _Binding

        planned = plan.steps[0]
        fields: dict[str, Any] = {
            "step": planned,
            "items": tuple(items),
            "scientific": planned.scientific,
            "scientific_defaults": plan.scientific_defaults,
            "adapter": StubAdapter(),
            "profile": PROFILES["standard"],
            "checks": (),
            "recovery": RECOVERIES["none"],
            "execution_binding": _Binding(binding_id="t", executable="/bin/true"),
            "run_root": "/tmp/v4-test",
            "definition_digest": plan.definition_digest,
        }
        fields.update(overrides)
        return StepExecutionRequest(**fields)

    def test_missing_scientific_fails_step(self, tmp_path: Path) -> None:
        plan = _compiled()
        items = [_item(plan)]
        batch = BatchStepExecutor().with_supervisor(StubSupervisor())
        result = batch.execute_step(self._request(plan, items, scientific=None))
        assert result.status is StepStatus.FAILED
        assert result.summary["total"] == 1

    def test_missing_adapter_fails_step(self, tmp_path: Path) -> None:
        plan = _compiled()
        items = [_item(plan)]
        batch = BatchStepExecutor().with_supervisor(StubSupervisor())
        result = batch.execute_step(self._request(plan, items, adapter=None))
        assert result.status is StepStatus.FAILED

    def test_missing_profile_fails_step(self, tmp_path: Path) -> None:
        plan = _compiled()
        items = [_item(plan)]
        batch = BatchStepExecutor().with_supervisor(StubSupervisor())
        result = batch.execute_step(self._request(plan, items, profile=None))
        assert result.status is StepStatus.FAILED

    def test_missing_supervisor_fails_step(self, tmp_path: Path) -> None:
        plan = _compiled()
        items = [_item(plan)]
        batch = BatchStepExecutor()
        result = batch.execute_step(self._request(plan, items))
        assert result.status is StepStatus.FAILED

    def test_sequential_path_single_worker(self, tmp_path: Path) -> None:
        plan = _compiled(scheduler={"max_parallel_items": 1})
        items = [_item(plan)]
        adapter = StubAdapter()
        adapter.parse_result = _terminated_result()
        batch = BatchStepExecutor().with_supervisor(StubSupervisor())
        request = self._request(plan, items, adapter=adapter)
        result = batch.execute_step(request)
        assert result.status is StepStatus.COMPLETED

    def test_repository_records_transitions(self, tmp_path: Path) -> None:
        plan = _compiled()
        items = [_item(plan)]
        adapter = StubAdapter()
        adapter.parse_result = _terminated_result()
        repository = InMemoryWorkItemRepository()
        batch = BatchStepExecutor(repository=repository).with_supervisor(StubSupervisor())
        result = batch.execute_step(self._request(plan, items, adapter=adapter))
        assert result.status is StepStatus.COMPLETED
        assert repository.started_ids() == (items[0].id,)
        assert repository.get_result(items[0].id) is not None
        assert repository.get_result("missing") is None
        assert batch.repository is repository

    def test_reuse_hit_skips_execution(self, tmp_path: Path) -> None:
        plan = _compiled()
        items = [_item(plan)]
        store = InMemoryReuseStore()
        working = StubAdapter()
        working.parse_result = _terminated_result()
        done = (
            BatchStepExecutor()
            .with_supervisor(StubSupervisor())
            .execute_step(self._request(plan, items, adapter=working))
        )
        assert done.status is StepStatus.COMPLETED
        stored = done.item_results[0]
        store.store(stored)
        # stale/non-completed results are never stored
        failed_like = error_result(items[0], "x", "y")
        store.store(failed_like)
        assert store.lookup(items[0].semantic_digest) is stored
        guarded = StubAdapter()
        guarded.parse_result = _terminated_result()
        guarded.materialize_error = ValueError("must not render on reuse hit")
        second = BatchStepExecutor(reuse_store=store).with_supervisor(StubSupervisor())
        reused = second.execute_step(self._request(plan, items, adapter=guarded))
        assert reused.status is StepStatus.COMPLETED
        assert any(item.code == "reuse_hit" for item in reused.item_results[0].diagnostics)
        assert guarded.rendered == []

    def test_reuse_miss_executes(self, tmp_path: Path) -> None:
        plan = _compiled()
        items = [_item(plan)]
        adapter = StubAdapter()
        adapter.parse_result = _terminated_result()
        batch = BatchStepExecutor(reuse_store=InMemoryReuseStore()).with_supervisor(
            StubSupervisor()
        )
        result = batch.execute_step(self._request(plan, items, adapter=adapter))
        assert result.status is StepStatus.COMPLETED
        assert len(adapter.rendered) == 1


# ---------------------------------------------------------------------------
# Environment branches
# ---------------------------------------------------------------------------


class TestEnvironmentBranches:
    """Executable validation, measurement, probing, and caching."""

    @pytest.mark.parametrize("candidate", ["", "  ", "a;b", "a|b", "a\0b", "a`b", "a$b"])
    def test_bad_candidates_rejected(self, candidate: str) -> None:
        with pytest.raises(DomainError):
            measure_executable(candidate)

    def test_missing_executable_rejected(self, tmp_path: Path) -> None:
        with pytest.raises(DomainError, match="[Rr]esolv"):
            measure_executable(str(tmp_path / "ghost"))

    def test_measure_file(self, tmp_path: Path) -> None:
        target = tmp_path / "prog"
        target.write_bytes(b"#!/bin/sh\necho hi\n")
        identity = measure_executable(str(target))
        assert identity.digest.startswith("sha256:")
        assert identity.size_bytes > 0
        assert identity.program_version is None

    def test_measure_by_path_name(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        target = tmp_path / "myprog"
        target.write_bytes(b"x")
        target.chmod(0o755)
        monkeypatch.setenv("PATH", str(tmp_path) + os.pathsep + os.environ["PATH"])
        identity = measure_executable("myprog")
        assert identity.resolved_path.endswith("myprog")

    def test_probe_junk_is_tolerated(self, tmp_path: Path) -> None:
        class JunkProbe:
            @property
            def program_name(self):  # type: ignore[no-untyped-def]
                from confflow.execution.native import ProgramName as P

                return P.GAUSSIAN

            @property
            def adapter_version(self) -> str:
                return "v"

            @property
            def parser_version(self) -> str:
                return "v"

            def environment_probe(self, executable: str) -> dict[str, Any]:
                return {"program_version": "X1", "junk": object()}

        target = tmp_path / "prog"
        target.write_bytes(b"x")
        measurer = EnvironmentMeasurer()
        environment = measurer.build_environment(str(target), adapter=JunkProbe())  # type: ignore[arg-type]
        assert environment.program == "gaussian"
        assert environment.program_version == "X1"
        assert environment.digest().startswith("sha256:")
        # cached identity is reused
        again = measurer.measure(str(target), adapter=JunkProbe())  # type: ignore[arg-type]
        assert again.digest == environment.executable_digest

    def test_probe_version_flows(self, tmp_path: Path) -> None:
        adapter = StubAdapter()
        target = tmp_path / "prog"
        target.write_bytes(b"x")
        identity = measure_executable(str(target), adapter=adapter)
        assert identity.program_version is None


# ---------------------------------------------------------------------------
# Supervisor instant branches
# ---------------------------------------------------------------------------


class TestSupervisorEdges:
    """Constructor guards and unknown-handle behavior without processes."""

    def test_bad_timeouts_rejected(self) -> None:
        with pytest.raises(ValueError):
            NativeProcessSupervisor(terminate_timeout=-1.0)
        with pytest.raises(ValueError):
            NativeProcessSupervisor(kill_timeout=float("nan"))

    def test_unknown_handles_raise(self) -> None:
        supervisor = NativeProcessSupervisor()
        ghost = NativeHandle(key="ghost")
        with pytest.raises(NativeProcessError):
            supervisor.poll(ghost)
        with pytest.raises(NativeProcessError):
            supervisor.cancel(ghost)
        with pytest.raises(NativeProcessError):
            supervisor.collect(ghost)

    def test_submit_validates_request(self, tmp_path: Path) -> None:
        from confflow.execution.native import NativeExecutionRequest as R

        supervisor = NativeProcessSupervisor()
        blocker = tmp_path / "blocker"
        blocker.write_bytes(b"x")
        with pytest.raises(NativeProcessError):
            supervisor.submit(R(executable="/bin/true", argv=("/bin/true",), work_dir=str(blocker)))

    def test_true_process_roundtrip(self, tmp_path: Path) -> None:
        from confflow.execution.native import NativeExecutionRequest as R

        supervisor = NativeProcessSupervisor()
        handle = supervisor.submit(
            R(executable="/bin/true", argv=("/bin/true",), work_dir=str(tmp_path))
        )
        assert handle.pid is not None
        import time as _time

        deadline = _time.monotonic() + 10.0
        while True:
            status = supervisor.poll(handle)
            if status.is_terminal:
                break
            assert _time.monotonic() < deadline
            _time.sleep(0.01)
        assert status.exit_code == 0
        collected = supervisor.collect(handle)
        assert collected.exit_code == 0
        assert collected.wall_time_seconds >= 0.0
        with pytest.raises(NativeProcessError):
            supervisor.collect(handle)

    def test_lock_guards_shared_state(self) -> None:
        supervisor = NativeProcessSupervisor()
        assert hasattr(supervisor._lock, "acquire")
        assert hasattr(supervisor._lock, "release")


class TestRescueDriverDirect:
    """The rescue driver maps launch outcomes without subprocesses."""

    def _driver(self, tmp_path: Path, supervisor: Any, adapter: Any):
        from confflow.execution.work_item_executor import _RescueDriver

        plan = _compiled()
        item = _item(plan)
        context = _context(plan, item, str(tmp_path), supervisor, adapter)
        return (
            WorkItemExecutor(),
            _RescueDriver(WorkItemExecutor(), context, supervisor, str(tmp_path), None),
            adapter,
        )

    def _materialized(self, adapter: Any):
        return adapter.materialize_native_input.__self__ and MaterializedNativeInput(
            program=ProgramName.GAUSSIAN, main_input_name="r.gjf"
        )

    def _request(self, tmp_path: Path) -> NativeExecutionRequest:
        return NativeExecutionRequest(
            executable="/bin/true", argv=("/bin/true",), work_dir=str(tmp_path)
        )

    def test_submit_failure_yields_empty(self, tmp_path: Path) -> None:
        _, driver, _ = self._driver(tmp_path, StubSupervisor(), StubAdapter())
        supervisor = StubSupervisor()
        supervisor.submit_error = OSError("gone")
        _, driver, _ = self._driver(tmp_path, supervisor, StubAdapter())
        result, parsed = driver.run_native(
            self._materialized(StubAdapter()), self._request(tmp_path), stage="s"
        )
        assert result.exit_code is None
        assert parsed is None

    def test_unclean_exit_skips_parse(self, tmp_path: Path) -> None:
        supervisor = StubSupervisor()
        supervisor.collected = NativeExecutionResult(exit_code=3, wall_time_seconds=0.1)
        _, driver, adapter = self._driver(tmp_path, supervisor, StubAdapter())
        result, parsed = driver.run_native(
            self._materialized(adapter), self._request(tmp_path), stage="s"
        )
        assert result.exit_code == 3
        assert parsed is None

    def test_parse_error_yields_empty(self, tmp_path: Path) -> None:
        adapter = StubAdapter()
        adapter.parse_error = ValueError("truncated")
        _, driver, _ = self._driver(tmp_path, StubSupervisor(), adapter)
        result, parsed = driver.run_native(
            self._materialized(adapter), self._request(tmp_path), stage="s"
        )
        assert result.exit_code == 0
        assert parsed is None

    def test_success_parses(self, tmp_path: Path) -> None:
        adapter = StubAdapter()
        adapter.parse_result = _terminated_result()
        _, driver, _ = self._driver(tmp_path, StubSupervisor(), adapter)
        result, parsed = driver.run_native(
            self._materialized(adapter), self._request(tmp_path), stage="s"
        )
        assert result.exit_code == 0
        assert parsed is not None
        assert (tmp_path / "recovery" / "s").is_dir()


class TestResolveExecutablePath:
    """PATH lookup resolves bare executable names."""

    def test_bare_name_resolves_via_path(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        plan = _compiled()
        item = _item(plan)
        adapter_with_path = StubAdapterWithExe("true")
        adapter_with_path.parse_result = _terminated_result()
        context = _context(plan, item, str(tmp_path), StubSupervisor(), adapter_with_path)
        result = WorkItemExecutor().execute(item, context)
        assert result.status is WorkItemStatus.COMPLETED

    def test_bare_name_missing_is_environment_error(self, tmp_path: Path) -> None:
        plan = _compiled()
        item = _item(plan)
        adapter = StubAdapterWithExe("definitely-not-a-program-xyz")
        context = _context(plan, item, str(tmp_path), StubSupervisor(), adapter)
        result = WorkItemExecutor().execute(item, context)
        assert result.status is WorkItemStatus.FAILED
        assert result.error is not None
        assert result.error.code == "environment_error"

    def test_submit_fail_with_cancel_marks_cancelled(self, tmp_path: Path) -> None:
        plan = _compiled()
        item = _item(plan)
        supervisor = StubSupervisor()
        supervisor.submit_error = OSError("gone")
        context = _context(
            plan, item, str(tmp_path), supervisor, StubAdapter(), executable="/bin/true"
        )
        result = WorkItemExecutor().execute(item, context, should_cancel=lambda: True)
        assert result.status is WorkItemStatus.CANCELLED


class StubAdapterWithExe(StubAdapter):
    """Stub adapter with a configurable default executable."""

    def __init__(self, executable: str) -> None:
        super().__init__()
        self._exe = executable

    @property
    def default_executable(self) -> str:
        return self._exe


class TestScopedAndAssemble:
    """Diagnostic scoping and failure assembly edges."""

    def test_scoped_passthrough(self, tmp_path: Path) -> None:
        plan = _compiled()
        item = _item(plan)
        context = _context(plan, item, str(tmp_path), StubSupervisor(), StubAdapter())
        executor = WorkItemExecutor()
        scoped = Diagnostic(code="x", message="y", severity=DiagnosticSeverity.INFO, step_id="s9")
        assert executor._scoped(scoped, context, item) is scoped

    def test_assemble_failed_defaults(self, tmp_path: Path) -> None:
        plan = _compiled()
        item = _item(plan)
        context = _context(plan, item, str(tmp_path), StubSupervisor(), StubAdapter())
        executor = WorkItemExecutor()
        timing = executor._timing(0.0, 0.0)
        assert timing.duration_seconds >= 0.0
        failed = executor._assemble_failed(
            item,
            context,
            [],
            0.0,
            0.0,
            (),
            attempted=False,
            succeeded=None,
            extra_diagnostics=(
                Diagnostic(code="z", message="w", severity=DiagnosticSeverity.INFO),
            ),
        )
        assert failed.status is WorkItemStatus.FAILED
        assert failed.error is not None
        assert failed.error.message == "work item failed checks"
        assert any(item.code == "z" for item in failed.diagnostics)

    def test_recovery_bond_merge_visible_to_policy(self, tmp_path: Path) -> None:
        from confflow.domain import FrozenDict as _Frozen
        from confflow.workflow.v4.document import ScientificDefinition

        plan = _compiled()
        item = _item(plan)
        adapter = StubAdapter()
        adapter.parse_result = _terminated_result(geometry=False)
        recovery = StubRecovery()
        scientific = ScientificDefinition(
            program="stub",
            result_profile="standard",
            native=_Frozen({"keyword": "x"}),
            checks=("geometry_required", "bond_drift"),
            check_params=_Frozen({"bond_drift": {"atoms": [1, 2]}}),
        )
        context = ItemExecutionContext(
            step_id="s_opt",
            scientific=scientific,
            scientific_defaults=_defaults(),
            adapter=adapter,
            profile=PROFILES["standard"],
            checks=(CHECKS["geometry_required"], CHECKS["bond_drift"]),
            recovery=recovery,
            execution_binding=_binding(),
            run_root=str(tmp_path),
            work_base=os.path.join(str(tmp_path), "items"),
            supervisor=StubSupervisor(),
            poll_interval_seconds=0.001,
        )
        result = WorkItemExecutor().execute(item, context)
        assert result.status is WorkItemStatus.FAILED
        assert recovery.evaluations == 1

    def test_external_uri_artifact_skipped(self, tmp_path: Path) -> None:
        from confflow.domain.artifact import ArtifactLocator as Locator

        plan = _compiled()
        item = _item(plan)
        artifact = ArtifactRef(
            id="ext",
            role="reference",
            locator=Locator.external_uri("file:///x/y"),
            subject_structure_id="s0",
        )
        manual = WorkItem(
            id=item.id,
            logical_key=item.logical_key,
            step_id=item.step_id,
            named_inputs=WorkItemInputs(
                structures=item.named_inputs.structures,
                artifacts=FrozenDict({"reference": ArtifactSet.of(artifact)}),
            ),
            resources=item.resources,
            semantic_digest=item.semantic_digest,
        )
        adapter = StubAdapter()
        adapter.parse_result = _terminated_result()
        context = _context(
            plan, item, str(tmp_path), StubSupervisor(), adapter, executable="/bin/true"
        )
        result = WorkItemExecutor().execute(manual, context)
        assert result.status is WorkItemStatus.COMPLETED

    def test_staging_without_run_root(self, tmp_path: Path) -> None:
        plan = _compiled()
        item = _item(plan)
        artifact = ArtifactRef(
            id="chk",
            role="checkpoint",
            locator=ArtifactLocator.run_relative("a.chk"),
            subject_structure_id="s0",
        )
        manual = WorkItem(
            id=item.id,
            logical_key=item.logical_key,
            step_id=item.step_id,
            named_inputs=WorkItemInputs(
                structures=item.named_inputs.structures,
                artifacts=FrozenDict({"checkpoint": ArtifactSet.of(artifact)}),
            ),
            resources=item.resources,
            semantic_digest=item.semantic_digest,
        )
        context = _context(plan, item, "", StubSupervisor(), StubAdapter())
        result = WorkItemExecutor().execute(manual, context)
        assert result.status is WorkItemStatus.FAILED
        assert result.error is not None
        assert "run root" in result.error.message

    def test_subject_of_none(self, tmp_path: Path) -> None:
        from confflow.execution.work_item_executor import WorkItemExecutor as _E

        plan = _compiled()
        item = _item(plan)
        assert _E._subject_of(item) == "s0"


class TestBatchBindRecovery:
    """Recovery binding substitutes step-bound rescue policies."""

    def test_bind_none_and_other_passthrough(self) -> None:
        from confflow.execution.batch import BatchStepExecutor as _B

        assert _B._bind_recovery(None, StubAdapter()) is None
        none_policy = RECOVERIES["none"]
        assert _B._bind_recovery(none_policy, StubAdapter()) is none_policy
        assert _B._bind_recovery(none_policy, None) is none_policy

    def test_bind_unbound_ts_policy(self) -> None:
        from confflow.execution.batch import BatchStepExecutor as _B
        from confflow.execution.recovery_standard import TsRescueScanPolicy

        unbound = RECOVERIES["ts_rescue_scan"]
        adapter = StubAdapter()
        bound = _B._bind_recovery(unbound, adapter)
        assert isinstance(bound, TsRescueScanPolicy)
        assert bound is not unbound
        already = TsRescueScanPolicy(adapter=adapter)
        assert _B._bind_recovery(already, adapter) is already
        assert _B._bind_recovery(unbound, None) is unbound

    def test_request_defaults_fill(self, tmp_path: Path) -> None:
        plan = _compiled()
        items = [_item(plan)]
        request = StepExecutionRequest(step=plan.steps[0], items=tuple(items))
        assert request.scientific_defaults is not None
        assert request.checks == ()
        batch = BatchStepExecutor().with_supervisor(StubSupervisor())
        result = batch.execute_step(request)
        assert result.status is StepStatus.FAILED


class TestEnvironmentEdges:
    """Empty files, tiny hash windows, and probe shapes."""

    def test_empty_file_measures(self, tmp_path: Path) -> None:
        target = tmp_path / "empty"
        target.write_bytes(b"")
        identity = measure_executable(str(target))
        assert identity.digest.startswith("sha256:")
        assert identity.size_bytes == 0

    def test_tiny_hash_window(self, tmp_path: Path) -> None:
        target = tmp_path / "prog"
        target.write_bytes(b"x" * 100)
        identity = measure_executable(str(target), max_hash_bytes=10)
        assert identity.digest.startswith("sha256:")

    def test_non_dict_probe_skipped(self, tmp_path: Path) -> None:
        class ListProbe:
            @property
            def program_name(self):  # type: ignore[no-untyped-def]
                from confflow.execution.native import ProgramName as _P

                return _P.ORCA

            @property
            def adapter_version(self) -> str:
                return "v"

            @property
            def parser_version(self) -> str:
                return "v"

            def environment_probe(self, executable: str) -> Any:
                return ["not", "a", "dict"]

        target = tmp_path / "prog"
        target.write_bytes(b"x")
        environment = EnvironmentMeasurer().build_environment(
            str(target), adapter=ListProbe()  # type: ignore[arg-type]
        )
        assert environment.program_version is None

    def test_raising_probe_tolerated(self, tmp_path: Path) -> None:
        class RaisingProbe:
            @property
            def program_name(self):  # type: ignore[no-untyped-def]
                from confflow.execution.native import ProgramName as _P

                return _P.ORCA

            @property
            def adapter_version(self) -> str:
                return "v"

            @property
            def parser_version(self) -> str:
                return "v"

            def environment_probe(self, executable: str) -> Any:
                raise RuntimeError("probe exploded")

        target = tmp_path / "prog"
        target.write_bytes(b"x")
        identity = measure_executable(str(target), adapter=RaisingProbe())  # type: ignore[arg-type]
        assert identity.program_version is None


class TestLaunchWaitEdges:
    """Poll/cancel/walltime exception edges in the wait loop."""

    def test_poll_error_is_handle_lost(self, tmp_path: Path) -> None:
        plan = _compiled()
        item = _item(plan)
        supervisor = StubSupervisor()
        supervisor.poll_error = RuntimeError("poll exploded")
        context = _context(
            plan, item, str(tmp_path), supervisor, StubAdapter(), executable="/bin/true"
        )
        result = WorkItemExecutor().execute(item, context)
        assert result.status is WorkItemStatus.FAILED
        assert "handle was lost" in (result.error.message if result.error else "")

    def test_cancel_error_is_handle_lost(self, tmp_path: Path) -> None:
        plan = _compiled()
        item = _item(plan)
        supervisor = StubSupervisor()
        supervisor.poll_script = [NativeStatus(is_terminal=False)]
        supervisor.cancel_error = RuntimeError("cancel exploded")
        context = _context(
            plan, item, str(tmp_path), supervisor, StubAdapter(), executable="/bin/true"
        )
        result = WorkItemExecutor().execute(item, context, should_cancel=lambda: True)
        assert result.status is WorkItemStatus.FAILED
        assert result.error is not None
        assert result.error.code == "cancellation_error"
        assert result.recovery is not None
        assert result.recovery.attempted is False

    def test_negative_poll_interval_is_handle_lost(self, tmp_path: Path) -> None:
        plan = _compiled()
        item = _item(plan)
        supervisor = StubSupervisor()
        supervisor.poll_script = [NativeStatus(is_terminal=False)] * 5
        adapter = StubAdapter()
        adapter.parse_result = _terminated_result()
        base = _context(plan, item, str(tmp_path), supervisor, adapter, executable="/bin/true")
        context = ItemExecutionContext(
            step_id=base.step_id,
            scientific=base.scientific,
            scientific_defaults=base.scientific_defaults,
            adapter=adapter,
            profile=base.profile,
            checks=(),
            recovery=base.recovery,
            execution_binding=base.execution_binding,
            run_root=base.run_root,
            work_base=base.work_base,
            supervisor=supervisor,
            poll_interval_seconds=-1.0,
        )
        result = WorkItemExecutor().execute(item, context)
        assert result.status is WorkItemStatus.FAILED
        assert "handle was lost" in (result.error.message if result.error else "")

    def test_staging_success_with_checksum(self, tmp_path: Path) -> None:
        import hashlib as _hashlib

        (tmp_path / "steps" / "prev").mkdir(parents=True)
        content = b"checkpoint-bytes"
        (tmp_path / "steps" / "prev" / "f.chk").write_bytes(content)
        digest = "sha256:" + _hashlib.sha256(content).hexdigest()
        plan = _compiled()
        item = _item(plan)
        artifact = ArtifactRef(
            id="chk",
            role="checkpoint",
            locator=ArtifactLocator.run_relative("steps/prev/f.chk"),
            checksum=digest,
            subject_structure_id="s0",
        )
        manual = WorkItem(
            id=item.id,
            logical_key=item.logical_key,
            step_id=item.step_id,
            named_inputs=WorkItemInputs(
                structures=item.named_inputs.structures,
                artifacts=FrozenDict({"checkpoint": ArtifactSet.of(artifact)}),
            ),
            resources=item.resources,
            semantic_digest=item.semantic_digest,
        )
        adapter = StubAdapter()
        adapter.parse_result = _terminated_result()
        context = _context(
            plan, item, str(tmp_path), StubSupervisor(), adapter, executable="/bin/true"
        )
        result = WorkItemExecutor().execute(manual, context)
        assert result.status is WorkItemStatus.COMPLETED
        staged = adapter.rendered[0].checkpoints
        assert len(staged) == 1
        assert staged[0].local_name == os.path.join("staged", "input-checkpoint-0.chk")

    def test_staging_success_without_checksum(self, tmp_path: Path) -> None:
        (tmp_path / "steps" / "prev").mkdir(parents=True)
        (tmp_path / "steps" / "prev" / "f.chk").write_bytes(b"bytes")
        plan = _compiled()
        item = _item(plan)
        artifact = ArtifactRef(
            id="chk",
            role="checkpoint",
            locator=ArtifactLocator.run_relative("steps/prev/f.chk"),
            subject_structure_id="s0",
        )
        manual = WorkItem(
            id=item.id,
            logical_key=item.logical_key,
            step_id=item.step_id,
            named_inputs=WorkItemInputs(
                structures=item.named_inputs.structures,
                artifacts=FrozenDict({"checkpoint": ArtifactSet.of(artifact)}),
            ),
            resources=item.resources,
            semantic_digest=item.semantic_digest,
        )
        adapter = StubAdapter()
        adapter.parse_result = _terminated_result()
        context = _context(
            plan, item, str(tmp_path), StubSupervisor(), adapter, executable="/bin/true"
        )
        result = WorkItemExecutor().execute(manual, context)
        assert result.status is WorkItemStatus.COMPLETED

    def test_recovery_recheck_still_fails(self, tmp_path: Path) -> None:
        from confflow.execution.recovery import RecoveryExecution

        adapter = StubAdapter()
        recovery = StubRecovery()
        recovery.decision = RecoveryDecision(attempt=True, reason="try")
        recovery.execution = RecoveryExecution(
            native_result=_terminated_result(geometry=False),
            execution_result=NativeExecutionResult(exit_code=0, wall_time_seconds=0.2),
            materialized=MaterializedNativeInput(
                program=ProgramName.GAUSSIAN, main_input_name="r.gjf"
            ),
            diagnostics=(),
            metadata=FrozenDict({}),
        )
        plan = _compiled()
        item = _item(plan)
        context = _context(
            plan,
            item,
            str(tmp_path),
            StubSupervisor(),
            adapter,
            executable="/bin/true",
            checks=(CHECKS["geometry_required"],),
            recovery=recovery,
        )
        # initial parse has no geometry either, so recovery triggers and re-fails
        adapter.parse_result = _terminated_result(geometry=False)
        result = WorkItemExecutor().execute(item, context)
        assert result.status is WorkItemStatus.FAILED
        assert result.recovery is not None
        assert result.recovery.attempted is True
        assert result.recovery.succeeded is False

    def test_repeated_recovery_failure_terminates(self, tmp_path: Path) -> None:
        """Recovery attempts increment so policy caps engage (no retry loop)."""
        from confflow.execution.recovery import RecoveryExecution

        attempts_seen: list[int] = []

        class CappedRecovery(StubRecovery):
            def evaluate(self, context: Any) -> RecoveryDecision:
                attempts_seen.append(context.attempt)
                if context.attempt >= 1:
                    return RecoveryDecision(attempt=False, reason="max_attempts_reached")
                return RecoveryDecision(attempt=True, reason="try")

        adapter = StubAdapter()
        adapter.parse_result = _terminated_result(geometry=False)
        recovery = CappedRecovery()
        recovery.execution = RecoveryExecution(
            native_result=_terminated_result(geometry=False),
            execution_result=NativeExecutionResult(exit_code=0, wall_time_seconds=0.2),
            materialized=MaterializedNativeInput(
                program=ProgramName.GAUSSIAN, main_input_name="r.gjf"
            ),
            diagnostics=(),
            metadata=FrozenDict({}),
        )
        plan = _compiled()
        item = _item(plan)
        context = _context(
            plan,
            item,
            str(tmp_path),
            StubSupervisor(),
            adapter,
            executable="/bin/true",
            checks=(CHECKS["geometry_required"],),
            recovery=recovery,
        )
        result = WorkItemExecutor().execute(item, context)
        assert result.status is WorkItemStatus.FAILED
        assert attempts_seen == [0]
        assert result.recovery is not None
        assert result.recovery.attempted is True
        assert result.recovery.succeeded is False
