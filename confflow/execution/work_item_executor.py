#!/usr/bin/env python3

"""V4 work-item execution.

A :class:`WorkItemExecutor` runs one deterministic :class:`WorkItem` through
the full native pipeline: input validation, artifact staging, native
rendering, process execution, parsing, result profiling, scientific checks,
and explicit recovery.  It knows nothing about DAGs, step ordering, labels,
YAML, or legacy config shapes.

Diagnostic codes are plain strings matching the frozen workflow families
(``native_input_error``, ``native_execution_error``, ``cancellation_error``,
``native_parse_error``, ``scientific_check_error``, ``recovery_failed``,
``artifact_error``, ``environment_error``); the workflow layer owns the
matching enum and never needs to parse messages.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from ..domain._immutable import FrozenDict
from ..domain.artifact import ArtifactSet
from ..domain.completion import WorkItemStatus
from ..domain.diagnostics import Diagnostic, DiagnosticSeverity
from ..domain.errors import DomainError
from ..domain.result import ResultSet
from ..domain.structure import StructureRecord, StructureSet
from ..domain.work_item import (
    RecoveryInfo,
    ResultError,
    Timing,
    WorkItem,
    WorkItemResult,
)
from .checks import CHECK_DEFAULTS, CheckContext, ScientificCheck
from .contracts import ExecutionBinding
from .native import (
    CancelOutcome,
    MaterializedNativeInput,
    NativeErrorCode,
    NativeExecutionRequest,
    NativeExecutionResult,
    NativeResult,
    ProcessSupervisor,
    ProgramAdapter,
    ResolvedCalculationInputs,
    StagedArtifact,
)
from .profiles import ProfileContext, ResultProfile

if TYPE_CHECKING:
    from ..workflow.v4.document import ScientificDefaults, ScientificDefinition

__all__ = [
    "DRIVING_STRUCTURE_PORT",
    "ItemExecutionContext",
    "WorkItemExecutor",
    "check_code",
    "error_result",
    "sanitize_job_name",
    "select_driving_structure",
]

#: The only structure port the V4-2 executor drives natively.  Named
#: multi-structure execution (reactant/product/guess) is representable in the
#: compiler but executes in a later milestone with its own executor path.
DRIVING_STRUCTURE_PORT = "structure"

_POLL_INTERVAL_SECONDS = 0.2


def sanitize_job_name(logical_key: str) -> str:
    """Return a filesystem-safe job name derived from a logical key."""
    cleaned = "".join(
        char if char.isalnum() or char in ("_", "-", ".") else "_" for char in logical_key
    )
    return cleaned.strip("._") or "job"


def check_code(code: NativeErrorCode | str) -> str:
    """Return the diagnostic code string for a native error code."""
    return code.value if isinstance(code, NativeErrorCode) else str(code)


def select_driving_structure(work_item: WorkItem) -> StructureRecord:
    """Return the single driving structure of *work_item*.

    Raises
    ------
    DomainError
        Raised when the item has no ``structure`` port or not exactly one
        structure on it.  Multi-structure execution is explicit future work,
        never positional guessing.
    """
    structures = work_item.named_inputs.structures.get(DRIVING_STRUCTURE_PORT)
    if structures is None or len(structures) != 1:
        raise DomainError(
            f"work item {work_item.logical_key!r} must carry exactly one structure "
            f"on port {DRIVING_STRUCTURE_PORT!r} for standard execution"
        )
    record = structures[0]
    if not isinstance(record, StructureRecord):  # pragma: no cover - guarded by model
        raise DomainError(f"port {DRIVING_STRUCTURE_PORT!r} holds a non-structure value")
    return record


def error_result(
    work_item: WorkItem,
    code: NativeErrorCode | str,
    message: str,
    *,
    diagnostics: tuple[Diagnostic, ...] = (),
    details: dict[str, Any] | None = None,
    timing: Timing | None = None,
    recovery: RecoveryInfo | None = None,
    status: WorkItemStatus = WorkItemStatus.FAILED,
) -> WorkItemResult:
    """Build a failed or cancelled work-item result with a typed error."""
    return WorkItemResult(
        work_item_id=work_item.id,
        status=status,
        structures=StructureSet(),
        results=ResultSet(),
        artifacts=ArtifactSet(),
        diagnostics=diagnostics,
        timing=timing,
        error=ResultError(
            code=check_code(code),
            message=message,
            retryable=False,
            details=FrozenDict(details or {}),
        ),
        recovery=recovery,
        semantic_digest=work_item.semantic_digest,
    )


def _diagnostic(
    code: NativeErrorCode | str,
    message: str,
    *,
    step_id: str | None = None,
    work_item_id: str | None = None,
    logical_key: str | None = None,
    field_path: str | None = None,
    details: dict[str, Any] | None = None,
    severity: DiagnosticSeverity = DiagnosticSeverity.ERROR,
) -> Diagnostic:
    merged: dict[str, Any] = dict(details or {})
    return Diagnostic(
        code=check_code(code),
        message=message,
        severity=severity,
        step_id=step_id,
        work_item_id=work_item_id,
        logical_key=logical_key,
        field_path=field_path,
        details=FrozenDict(merged),
    )


@dataclass(frozen=True, slots=True)
class ItemExecutionContext:
    """Everything one work-item execution may read."""

    step_id: str
    scientific: ScientificDefinition
    scientific_defaults: ScientificDefaults
    adapter: ProgramAdapter
    profile: ResultProfile
    checks: tuple[ScientificCheck, ...] = ()
    recovery: Any = None
    execution_binding: ExecutionBinding | None = None
    run_root: str = ""
    work_base: str | None = None
    supervisor: ProcessSupervisor | None = None
    environment: Any = None
    poll_interval_seconds: float = _POLL_INTERVAL_SECONDS

    def item_directory(self, logical_key: str) -> str:
        """Return the deterministic work directory for a logical key."""
        base = self.work_base or os.path.join(self.run_root, "items")
        return os.path.join(base, self.step_id, sanitize_job_name(logical_key))


class _RescueDriver:
    """RescueDriver bound to one item execution."""

    def __init__(
        self,
        executor: WorkItemExecutor,
        context: ItemExecutionContext,
        supervisor: ProcessSupervisor,
        item_dir: str,
        should_cancel: Callable[[], bool] | None,
    ) -> None:
        self._executor = executor
        self._context = context
        self._supervisor = supervisor
        self._item_dir = item_dir
        self._should_cancel = should_cancel

    def run_native(
        self,
        materialized: MaterializedNativeInput,
        request: NativeExecutionRequest,
        *,
        stage: str,
    ) -> tuple[NativeExecutionResult, NativeResult | None]:
        stage_dir = os.path.join(self._item_dir, "recovery", sanitize_job_name(stage))
        os.makedirs(stage_dir, exist_ok=True)
        for entry in materialized.files:
            path = os.path.join(stage_dir, entry.name)
            with open(path, "w", encoding="utf-8") as handle:
                handle.write(entry.content)
        staged_request = NativeExecutionRequest(
            executable=request.executable,
            argv=request.argv,
            work_dir=stage_dir,
            env=request.env,
            walltime_seconds=request.walltime_seconds,
            stdout_file=request.stdout_file,
            stderr_file=request.stderr_file,
            metadata=request.metadata,
        )
        execution = self._executor._launch_and_wait(
            self._supervisor,
            staged_request,
            self._context.poll_interval_seconds,
            should_cancel=self._should_cancel,
        )
        if execution is None:
            return (
                NativeExecutionResult(exit_code=None, wall_time_seconds=0.0),
                None,
            )
        execution_result, _cancel_outcome = execution
        if not execution_result.exited_cleanly:
            return execution_result, None
        try:
            native_result = self._context.adapter.parse_native_result(
                work_dir=stage_dir,
                log_file_name=materialized.main_input_name.rsplit(".", 1)[0]
                + "."
                + self._context.adapter.log_extension,
                materialized=materialized,
            )
        except (OSError, ValueError, TypeError, RuntimeError):
            return execution_result, None
        return execution_result, native_result


class WorkItemExecutor:
    """Execute one work item through the native pipeline."""

    def __init__(self) -> None:
        self._lock = threading.Lock()

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------

    def execute(
        self,
        work_item: WorkItem,
        context: ItemExecutionContext,
        *,
        should_cancel: Callable[[], bool] | None = None,
    ) -> WorkItemResult:
        """Execute *work_item* and return its normalized result."""
        wall_start = time.time()
        monotonic_start = time.monotonic()
        supervisor = context.supervisor
        if supervisor is None:
            return error_result(
                work_item,
                NativeErrorCode.ENVIRONMENT_ERROR,
                "no process supervisor is configured",
                diagnostics=(
                    _diagnostic(
                        NativeErrorCode.ENVIRONMENT_ERROR,
                        "no process supervisor is configured",
                        step_id=context.step_id,
                        work_item_id=work_item.id,
                        logical_key=work_item.logical_key,
                    ),
                ),
                timing=Timing(finished_at=wall_start, duration_seconds=0.0),
            )
        try:
            driving = select_driving_structure(work_item)
        except DomainError as exc:
            return self._finish_error(
                work_item,
                context,
                NativeErrorCode.NATIVE_INPUT_ERROR,
                str(exc),
                wall_start,
                monotonic_start,
            )
        from ..workflow.v4.scientific import resolve_scientific_parameters

        effective, scientific_diagnostics = resolve_scientific_parameters(
            structure=driving,
            overrides=context.scientific.overrides,
            defaults=context.scientific_defaults,
        )
        diagnostics: list[Diagnostic] = [
            self._scoped(diagnostic, context, work_item) for diagnostic in scientific_diagnostics
        ]
        if any(item.severity is DiagnosticSeverity.ERROR for item in scientific_diagnostics):
            return self._finish_error(
                work_item,
                context,
                "scientific_parameter_conflict",
                "scientific parameters are chemically inconsistent",
                wall_start,
                monotonic_start,
                diagnostics=tuple(diagnostics),
            )
        if effective.charge is None or effective.multiplicity is None:
            return self._finish_error(
                work_item,
                context,
                NativeErrorCode.NATIVE_INPUT_ERROR,
                "charge and multiplicity must be resolved before native rendering",
                wall_start,
                monotonic_start,
                diagnostics=tuple(diagnostics),
            )
        item_dir = context.item_directory(work_item.logical_key)
        try:
            os.makedirs(item_dir, exist_ok=True)
            staged = self._stage_checkpoints(work_item, context, item_dir)
        except DomainError as exc:
            return self._finish_error(
                work_item,
                context,
                NativeErrorCode.ARTIFACT_ERROR,
                str(exc),
                wall_start,
                monotonic_start,
                diagnostics=tuple(diagnostics),
            )
        resolved = ResolvedCalculationInputs(
            structure=driving,
            charge=effective.charge,
            multiplicity=effective.multiplicity,
            freeze=effective.freeze,
            resources=work_item.resources,
            native=context.scientific.native,
            checkpoints=tuple(staged),
            extra_structures=FrozenDict(
                {
                    port: value
                    for port, value in work_item.named_inputs.structures.items()
                    if port != DRIVING_STRUCTURE_PORT
                }
            ),
            step_id=context.step_id,
            work_item_id=work_item.id,
            logical_key=work_item.logical_key,
        )
        try:
            materialized = context.adapter.materialize_native_input(resolved)
        except (ValueError, TypeError, KeyError) as exc:
            return self._finish_error(
                work_item,
                context,
                NativeErrorCode.NATIVE_INPUT_ERROR,
                f"failed to render native input: {exc}",
                wall_start,
                monotonic_start,
                diagnostics=tuple(diagnostics),
            )
        for entry in materialized.files:
            path = os.path.join(item_dir, entry.name)
            if os.path.dirname(entry.name):
                raise DomainError("adapter input file names must be flat")  # pragma: no cover
            with open(path, "w", encoding="utf-8") as handle:
                handle.write(entry.content)
        try:
            executable = self._resolve_executable(context)
        except DomainError as exc:
            return self._finish_error(
                work_item,
                context,
                NativeErrorCode.ENVIRONMENT_ERROR,
                str(exc),
                wall_start,
                monotonic_start,
                diagnostics=tuple(diagnostics),
            )
        binding = context.execution_binding
        walltime = float(binding.walltime_seconds) if binding and binding.walltime_seconds else None
        env = dict(os.environ)
        if binding is not None:
            env.update(dict(binding.env))
        launch_info: dict[str, Any] = {
            "executable": executable,
            "env": dict(env),
            "walltime_seconds": walltime,
            "work_dir": item_dir,
        }
        try:
            request = context.adapter.build_execution_request(
                materialized,
                executable=executable,
                work_dir=item_dir,
                env=env,
                walltime_seconds=walltime,
            )
        except (ValueError, TypeError) as exc:
            return self._finish_error(
                work_item,
                context,
                NativeErrorCode.NATIVE_INPUT_ERROR,
                f"failed to build execution request: {exc}",
                wall_start,
                monotonic_start,
                diagnostics=tuple(diagnostics),
            )
        launched = self._launch_and_wait(
            supervisor, request, context.poll_interval_seconds, should_cancel=should_cancel
        )
        if launched is None:
            cancelled = should_cancel is not None and should_cancel()
            if cancelled:
                return self._finish_cancelled(
                    work_item, context, wall_start, monotonic_start, confirmed=True
                )
            return self._finish_error(
                work_item,
                context,
                NativeErrorCode.NATIVE_EXECUTION_ERROR,
                "native process handle was lost before completion",
                wall_start,
                monotonic_start,
                diagnostics=tuple(diagnostics),
            )
        execution_result, cancel_outcome = launched
        if cancel_outcome is not None:
            if cancel_outcome.confirmed:
                return self._finish_cancelled(
                    work_item, context, wall_start, monotonic_start, confirmed=True
                )
            return self._finish_error(
                work_item,
                context,
                NativeErrorCode.CANCELLATION_ERROR,
                f"cancellation could not be confirmed: {cancel_outcome.detail}",
                wall_start,
                monotonic_start,
                diagnostics=tuple(diagnostics),
                recovery=RecoveryInfo(profile=self._recovery_name(context), attempted=False),
            )
        if execution_result.timed_out:
            return self._finish_error(
                work_item,
                context,
                NativeErrorCode.NATIVE_EXECUTION_ERROR,
                f"native execution exceeded walltime of {walltime:g} seconds",
                wall_start,
                monotonic_start,
                diagnostics=tuple(diagnostics),
                details={"timed_out": True, "walltime_seconds": walltime},
            )
        log_name = (
            materialized.main_input_name.rsplit(".", 1)[0] + "." + context.adapter.log_extension
        )
        try:
            native_result = context.adapter.parse_native_result(
                work_dir=item_dir,
                log_file_name=log_name,
                materialized=materialized,
            )
        except (OSError, ValueError, TypeError, RuntimeError) as exc:
            return self._finish_error(
                work_item,
                context,
                NativeErrorCode.NATIVE_PARSE_ERROR,
                f"failed to parse native output: {exc}",
                wall_start,
                monotonic_start,
                diagnostics=tuple(diagnostics),
            )
        if not native_result.terminated_normally:
            failure = self._finish_error(
                work_item,
                context,
                NativeErrorCode.SCIENTIFIC_CHECK_ERROR,
                "native program did not terminate normally",
                wall_start,
                monotonic_start,
                diagnostics=tuple(diagnostics)
                + (
                    _diagnostic(
                        NativeErrorCode.SCIENTIFIC_CHECK_ERROR,
                        "native program did not terminate normally",
                        step_id=context.step_id,
                        work_item_id=work_item.id,
                        logical_key=work_item.logical_key,
                        details={"check": "normal_termination", "reason": "abnormal_termination"},
                    ),
                ),
            )
            return self._maybe_recover(
                work_item,
                context,
                supervisor,
                failure,
                native_result,
                should_cancel,
                wall_start,
                monotonic_start,
                tuple(diagnostics),
                launch_info,
                cancellation_confirmed=True,
            )
        return self._profile_check_recover(
            work_item,
            context,
            supervisor,
            native_result,
            materialized,
            execution_result,
            should_cancel,
            wall_start,
            monotonic_start,
            tuple(diagnostics),
            launch_info,
            cancellation_confirmed=True,
        )

    # ------------------------------------------------------------------
    # Internal pipeline stages
    # ------------------------------------------------------------------

    def _scoped(
        self, diagnostic: Diagnostic, context: ItemExecutionContext, work_item: WorkItem
    ) -> Diagnostic:
        if diagnostic.step_id is not None:
            return diagnostic
        return Diagnostic(
            code=diagnostic.code,
            message=diagnostic.message,
            severity=diagnostic.severity,
            step_id=context.step_id,
            work_item_id=work_item.id,
            logical_key=diagnostic.logical_key,
            field_path=diagnostic.field_path,
            details=diagnostic.details,
        )

    def _timing(self, wall_start: float, monotonic_start: float) -> Timing:
        wall_end = time.time()
        return Timing(
            started_at=wall_start,
            finished_at=wall_end,
            duration_seconds=max(0.0, time.monotonic() - monotonic_start),
        )

    def _finish_error(
        self,
        work_item: WorkItem,
        context: ItemExecutionContext,
        code: NativeErrorCode | str,
        message: str,
        wall_start: float,
        monotonic_start: float,
        *,
        diagnostics: tuple[Diagnostic, ...] = (),
        details: dict[str, Any] | None = None,
        recovery: RecoveryInfo | None = None,
    ) -> WorkItemResult:
        return error_result(
            work_item,
            code,
            message,
            diagnostics=diagnostics,
            details=details,
            timing=self._timing(wall_start, monotonic_start),
            recovery=(
                recovery
                if recovery is not None
                else RecoveryInfo(profile=self._recovery_name(context), attempted=False)
            ),
        )

    def _finish_cancelled(
        self,
        work_item: WorkItem,
        context: ItemExecutionContext,
        wall_start: float,
        monotonic_start: float,
        *,
        confirmed: bool,
    ) -> WorkItemResult:
        from ..domain.completion import WorkItemStatus

        message = (
            "work item cancelled"
            if confirmed
            else "cancellation could not be confirmed; item must not be retried automatically"
        )
        return error_result(
            work_item,
            NativeErrorCode.CANCELLATION_ERROR,
            message,
            diagnostics=(
                _diagnostic(
                    NativeErrorCode.CANCELLATION_ERROR,
                    message,
                    step_id=context.step_id,
                    work_item_id=work_item.id,
                    logical_key=work_item.logical_key,
                    details={"confirmed": confirmed},
                ),
            ),
            timing=self._timing(wall_start, monotonic_start),
            recovery=RecoveryInfo(profile=self._recovery_name(context), attempted=False),
            status=WorkItemStatus.CANCELLED,
        )

    @staticmethod
    def _recovery_name(context: ItemExecutionContext) -> str:
        recovery = context.recovery
        name = getattr(recovery, "name", None)
        return str(name) if name else "none"

    def _resolve_executable(self, context: ItemExecutionContext) -> str:
        binding = context.execution_binding
        candidate = None
        if binding is not None and binding.executable:
            candidate = binding.executable
        if candidate is None:
            candidate = context.adapter.default_executable
        if os.path.isabs(candidate):
            if not os.path.isfile(candidate):
                raise DomainError(f"executable not found: {candidate!r}")
            return candidate
        resolved = shutil.which(candidate)
        if resolved is None:
            raise DomainError(
                f"executable {candidate!r} was not found on PATH and no "
                "execution binding provides an absolute path"
            )
        return resolved

    def _stage_checkpoints(
        self, work_item: WorkItem, context: ItemExecutionContext, item_dir: str
    ) -> list[StagedArtifact]:
        staged: list[StagedArtifact] = []
        staged_dir = os.path.join(item_dir, "staged")
        os.makedirs(staged_dir, exist_ok=True)
        run_root = os.path.realpath(context.run_root) if context.run_root else ""
        for port in sorted(work_item.named_inputs.artifacts):
            for index, artifact in enumerate(work_item.named_inputs.artifacts[port]):
                locator = artifact.locator
                if locator.path is None:
                    continue
                if not run_root:
                    raise DomainError(
                        "run-relative artifact locators require a run root for staging"
                    )
                source = os.path.realpath(os.path.join(run_root, locator.path))
                if os.path.commonpath([source, run_root]) != run_root:
                    raise DomainError(
                        f"artifact {artifact.id!r} escapes the run root; refusing to stage"
                    )
                if not os.path.isfile(source):
                    raise DomainError(
                        f"required input artifact {artifact.id!r} is not materialized "
                        f"at {locator.path!r}"
                    )
                local_name = f"input-{port}-{index}.chk"
                destination = os.path.join(staged_dir, local_name)
                with open(source, "rb") as handle_in:
                    content = handle_in.read()
                if artifact.checksum:
                    algorithm, _, expected = artifact.checksum.partition(":")
                    if algorithm.lower() == "sha256":
                        observed = hashlib.sha256(content).hexdigest()
                        if observed != expected.lower():
                            raise DomainError(
                                f"artifact {artifact.id!r} checksum mismatch; " "refusing to stage"
                            )
                with open(destination, "wb") as handle_out:
                    handle_out.write(content)
                staged.append(
                    StagedArtifact(
                        local_name=os.path.join("staged", local_name),
                        role=artifact.role,
                        subject_structure_id=artifact.subject_structure_id,
                        checksum=artifact.checksum,
                    )
                )
        return staged

    def _launch_and_wait(
        self,
        supervisor: ProcessSupervisor,
        request: NativeExecutionRequest,
        poll_interval: float,
        *,
        should_cancel: Callable[[], bool] | None,
    ) -> tuple[NativeExecutionResult, CancelOutcome | None] | None:
        """Submit *request* and wait for a terminal outcome.

        Returns ``None`` only when the process could not be launched or the
        handle was lost.  Cancellation and walltime are converted into
        explicit outcomes, never exceptions.
        """
        try:
            handle = supervisor.submit(request)
        except Exception:
            return None
        walltime = request.walltime_seconds
        monotonic_start = time.monotonic()
        cancel_outcome: CancelOutcome | None = None
        timed_out = False
        try:
            while True:
                try:
                    status = supervisor.poll(handle)
                except Exception:
                    return None
                if status.is_terminal:
                    break
                if should_cancel is not None and should_cancel():
                    try:
                        cancel_outcome = supervisor.cancel(handle)
                    except Exception:
                        return None
                    break
                if walltime is not None and time.monotonic() - monotonic_start > walltime:
                    try:
                        supervisor.cancel(handle)
                    except Exception:
                        pass
                    timed_out = True
                    break
                time.sleep(poll_interval)
            if timed_out:
                return (
                    NativeExecutionResult(
                        exit_code=None,
                        wall_time_seconds=time.monotonic() - monotonic_start,
                        timed_out=True,
                        stdout_file=request.stdout_file,
                        stderr_file=request.stderr_file,
                    ),
                    None,
                )
            if cancel_outcome is not None:
                return (
                    NativeExecutionResult(
                        exit_code=None,
                        wall_time_seconds=time.monotonic() - monotonic_start,
                        stdout_file=request.stdout_file,
                        stderr_file=request.stderr_file,
                    ),
                    cancel_outcome,
                )
            try:
                collected = supervisor.collect(handle)
            except Exception:
                return None
            return (
                NativeExecutionResult(
                    exit_code=collected.exit_code,
                    wall_time_seconds=time.monotonic() - monotonic_start,
                    stdout_file=request.stdout_file,
                    stderr_file=request.stderr_file,
                ),
                None,
            )
        except Exception:
            try:
                supervisor.cancel(handle)
            except Exception:
                pass
            return None

    # ------------------------------------------------------------------
    # Profile / checks / recovery
    # ------------------------------------------------------------------

    def _profile_check_recover(
        self,
        work_item: WorkItem,
        context: ItemExecutionContext,
        supervisor: ProcessSupervisor,
        native_result: NativeResult,
        materialized: MaterializedNativeInput,
        execution_result: NativeExecutionResult,
        should_cancel: Callable[[], bool] | None,
        wall_start: float,
        monotonic_start: float,
        base_diagnostics: tuple[Diagnostic, ...],
        launch_info: dict[str, Any],
        *,
        cancellation_confirmed: bool,
    ) -> WorkItemResult:
        discovered = context.adapter.discover_artifacts(
            work_dir=context.item_directory(work_item.logical_key),
            run_relative_prefix=f"items/{context.step_id}/{sanitize_job_name(work_item.logical_key)}",
            native_result=native_result,
            step_id=context.step_id,
            work_item_id=work_item.id,
            subject_structure_id=self._subject_of(work_item),
        )
        profile_output = context.profile.apply(
            ProfileContext(
                work_item_id=work_item.id,
                step_id=context.step_id,
                logical_key=work_item.logical_key,
                profile_name=context.profile.name,
                profile_version=context.profile.contract_version,
                native_result=native_result,
                inputs=self._resolved_inputs(work_item, context),
                discovered_artifacts=discovered,
            )
        )
        diagnostics = (
            list(base_diagnostics)
            + list(native_result.parser_diagnostics)
            + list(profile_output.diagnostics)
        )
        failures: list[Diagnostic] = []
        for check in context.checks:
            params = context.scientific.check_params_for(check.name)
            merged = dict(CHECK_DEFAULTS.get(check.name, {}))
            merged.update(dict(params))
            outcome = check.run(
                CheckContext(
                    check_name=check.name,
                    work_item_id=work_item.id,
                    step_id=context.step_id,
                    logical_key=work_item.logical_key,
                    profile_output=profile_output,
                    inputs=self._resolved_inputs(work_item, context),
                    params=FrozenDict(merged),
                )
            )
            if not outcome.passed and outcome.diagnostic is not None:
                failures.append(self._recode_check_failure(outcome.diagnostic, context, work_item))
        if not failures:
            return WorkItemResult(
                work_item_id=work_item.id,
                status=WorkItemStatus.COMPLETED,
                structures=profile_output.structures,
                results=profile_output.results,
                artifacts=profile_output.artifacts,
                diagnostics=tuple(diagnostics),
                timing=self._timing(wall_start, monotonic_start),
                recovery=RecoveryInfo(profile=self._recovery_name(context), attempted=False),
                semantic_digest=work_item.semantic_digest,
            )
        failed = self._assemble_failed(
            work_item,
            context,
            failures,
            wall_start,
            monotonic_start,
            tuple(diagnostics),
            attempted=False,
            succeeded=None,
        )
        return self._maybe_recover(
            work_item,
            context,
            supervisor,
            failed,
            native_result,
            should_cancel,
            wall_start,
            monotonic_start,
            tuple(diagnostics),
            launch_info,
            cancellation_confirmed=cancellation_confirmed,
        )

    def _assemble_failed(
        self,
        work_item: WorkItem,
        context: ItemExecutionContext,
        failures: list[Diagnostic],
        wall_start: float,
        monotonic_start: float,
        diagnostics: tuple[Diagnostic, ...],
        *,
        attempted: bool,
        succeeded: bool | None,
        extra_diagnostics: tuple[Diagnostic, ...] = (),
    ) -> WorkItemResult:
        primary = failures[0] if failures else None
        message = primary.message if primary is not None else "work item failed checks"
        return WorkItemResult(
            work_item_id=work_item.id,
            status=WorkItemStatus.FAILED,
            structures=StructureSet(),
            results=ResultSet(),
            artifacts=ArtifactSet(),
            diagnostics=tuple(diagnostics) + tuple(failures) + tuple(extra_diagnostics),
            timing=self._timing(wall_start, monotonic_start),
            error=ResultError(
                code=check_code(NativeErrorCode.SCIENTIFIC_CHECK_ERROR),
                message=message,
                retryable=False,
                details=FrozenDict(dict(primary.details) if primary is not None else {}),
            ),
            recovery=RecoveryInfo(
                profile=self._recovery_name(context),
                attempted=attempted,
                succeeded=succeeded,
            ),
            semantic_digest=work_item.semantic_digest,
        )

    def _recode_check_failure(
        self, diagnostic: Diagnostic, context: ItemExecutionContext, work_item: WorkItem
    ) -> Diagnostic:
        details = dict(diagnostic.details)
        details.setdefault("check", details.get("check", "unknown"))
        return Diagnostic(
            code=check_code(NativeErrorCode.SCIENTIFIC_CHECK_ERROR),
            message=diagnostic.message,
            severity=DiagnosticSeverity.ERROR,
            step_id=context.step_id,
            work_item_id=work_item.id,
            logical_key=work_item.logical_key,
            field_path=diagnostic.field_path,
            details=FrozenDict(details),
        )

    def _maybe_recover(
        self,
        work_item: WorkItem,
        context: ItemExecutionContext,
        supervisor: ProcessSupervisor,
        failed: WorkItemResult,
        native_result: NativeResult | None,
        should_cancel: Callable[[], bool] | None,
        wall_start: float,
        monotonic_start: float,
        base_diagnostics: tuple[Diagnostic, ...],
        launch_info: dict[str, Any],
        *,
        cancellation_confirmed: bool,
    ) -> WorkItemResult:
        recovery = context.recovery
        if recovery is None or getattr(recovery, "name", "none") == "none":
            return failed
        check_params = context.scientific.check_params
        bond_params = check_params.get("bond_drift")
        merged_params: dict[str, Any] = dict(context.scientific.recovery_params)
        if bond_params is not None and "bond_atoms" not in merged_params:
            atoms = bond_params.get("atoms")
            if atoms is not None:
                merged_params["bond_atoms"] = list(atoms)
        # The driver owns the stage filesystem layout; the policy still needs
        # launch facts to build its requests, so they ride along as params.
        # Work-item recovery params win over launch facts on conflict.
        for key in ("executable", "env", "walltime_seconds", "work_dir"):
            merged_params.setdefault(key, launch_info[key])
        recovery_context = self._recovery_context(
            context, work_item, native_result, failed, merged_params, cancellation_confirmed
        )
        decision = recovery.evaluate(recovery_context)
        if not decision.attempt:
            return self._with_recovery_note(failed, decision, attempted=False)
        driver = _RescueDriver(
            self,
            context,
            supervisor,
            context.item_directory(work_item.logical_key),
            should_cancel,
        )
        try:
            execution = recovery.execute(recovery_context, driver)
        except Exception as exc:
            return self._with_recovery_note(
                failed,
                decision,
                attempted=True,
                succeeded=False,
                note=f"recovery execution raised: {exc}",
            )
        if execution is None:
            return self._with_recovery_note(failed, decision, attempted=True, succeeded=False)
        recovered = self._profile_check_recover(
            work_item,
            context,
            supervisor,
            execution.native_result,
            execution.materialized,
            execution.execution_result,
            should_cancel,
            wall_start,
            monotonic_start,
            base_diagnostics + tuple(execution.diagnostics),
            launch_info,
            cancellation_confirmed=True,
        )
        if recovered.is_completed:
            return self._mark_recovered(recovered, decision)
        return self._with_recovery_note(recovered, decision, attempted=True, succeeded=False)

    def _recovery_context(
        self, context, work_item, native_result, failed, merged_params, cancellation_confirmed
    ):
        from .recovery import RecoveryContext

        return RecoveryContext(
            profile_name=self._recovery_name(context),
            work_item_id=work_item.id,
            step_id=context.step_id,
            logical_key=work_item.logical_key,
            inputs=self._resolved_inputs(work_item, context),
            failed_native_result=native_result,
            failure_diagnostics=tuple(failed.diagnostics),
            attempt=0,
            params=FrozenDict(merged_params),
            cancellation_confirmed=cancellation_confirmed,
        )

    def _with_recovery_note(
        self, failed: WorkItemResult, decision, *, attempted: bool, succeeded=None, note=None
    ) -> WorkItemResult:
        details = dict(decision.details) if decision.details else {}
        details["decision_reason"] = decision.reason
        diagnostics = list(failed.diagnostics)
        if attempted:
            code = check_code(NativeErrorCode.RECOVERY_FAILED)
            severity = DiagnosticSeverity.ERROR
            message = f"recovery failed: {decision.reason}"
        else:
            code = "recovery_declined"
            severity = DiagnosticSeverity.INFO
            message = f"recovery declined: {decision.reason}"
        if note:
            message += f": {note}"
        step_id = failed.diagnostics[0].step_id if failed.diagnostics else None
        diagnostics.append(
            Diagnostic(
                code=code,
                message=message,
                severity=severity,
                step_id=step_id,
                work_item_id=failed.work_item_id,
                details=FrozenDict(details),
            )
        )
        return WorkItemResult(
            work_item_id=failed.work_item_id,
            status=failed.status,
            structures=failed.structures,
            results=failed.results,
            artifacts=failed.artifacts,
            diagnostics=tuple(diagnostics),
            timing=failed.timing,
            error=failed.error,
            recovery=RecoveryInfo(
                profile=failed.recovery.profile if failed.recovery else "none",
                attempted=attempted,
                succeeded=succeeded,
            ),
            semantic_digest=failed.semantic_digest,
            metadata=failed.metadata,
        )

    def _mark_recovered(self, recovered: WorkItemResult, decision) -> WorkItemResult:
        diagnostics = list(recovered.diagnostics)
        diagnostics.append(
            Diagnostic(
                code="recovery_succeeded",
                message=f"recovery succeeded: {decision.reason}",
                severity=DiagnosticSeverity.INFO,
                work_item_id=recovered.work_item_id,
                details=FrozenDict({"decision_reason": decision.reason}),
            )
        )
        return WorkItemResult(
            work_item_id=recovered.work_item_id,
            status=recovered.status,
            structures=recovered.structures,
            results=recovered.results,
            artifacts=recovered.artifacts,
            diagnostics=tuple(diagnostics),
            timing=recovered.timing,
            error=None,
            recovery=RecoveryInfo(
                profile=recovered.recovery.profile if recovered.recovery else "none",
                attempted=True,
                succeeded=True,
            ),
            semantic_digest=recovered.semantic_digest,
            metadata=recovered.metadata,
        )

    def _resolved_inputs(
        self, work_item: WorkItem, context: ItemExecutionContext
    ) -> ResolvedCalculationInputs:
        from ..workflow.v4.scientific import resolve_scientific_parameters

        driving = select_driving_structure(work_item)
        effective, _ = resolve_scientific_parameters(
            structure=driving,
            overrides=context.scientific.overrides,
            defaults=context.scientific_defaults,
        )
        return ResolvedCalculationInputs(
            structure=driving,
            charge=effective.charge,
            multiplicity=effective.multiplicity,
            freeze=effective.freeze,
            resources=work_item.resources,
            native=context.scientific.native,
            checkpoints=(),
            extra_structures=FrozenDict(
                {
                    port: value
                    for port, value in work_item.named_inputs.structures.items()
                    if port != DRIVING_STRUCTURE_PORT
                }
            ),
            step_id=context.step_id,
            work_item_id=work_item.id,
            logical_key=work_item.logical_key,
        )

    @staticmethod
    def _subject_of(work_item: WorkItem) -> str | None:
        try:
            return select_driving_structure(work_item).id
        except DomainError:
            return None
