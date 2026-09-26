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
from .execution_adapters import (
    DRIVING_STRUCTURE_PORT,
    NAMED_STRUCTURES_ADAPTER,
    resolve_named_slot_sets,
)
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
from .profiles import ProfileContext, ProfileOutput, ResultProfile

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

#: The driving structure port, re-exported from the execution-adapter
#: authority so every seam agrees on the port name.
#: (Defined in :mod:`confflow.execution.execution_adapters`.)

_POLL_INTERVAL_SECONDS = 0.2

#: Maximum recovery executions per work item.  V4-2 runs a single recovery
#: round: policies may decline earlier, but the executor never runs more.
_MAX_RECOVERY_ROUNDS = 1


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
    from .execution_adapters import resolve_standard_structure

    return resolve_standard_structure(work_item)


def _is_named_execution(context: ItemExecutionContext) -> bool:
    """Return whether *context* runs through the named-structures adapter."""
    scientific = context.scientific
    return scientific is not None and scientific.execution_adapter == NAMED_STRUCTURES_ADAPTER


@dataclass(frozen=True, slots=True)
class _NamedExecution:
    """Validated named-slot execution inputs for one work item."""

    reference: StructureRecord
    slots: FrozenDict
    mapping: Any
    named: Any


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

    @staticmethod
    def _run_relative_prefix(context: ItemExecutionContext, work_item: WorkItem) -> str:
        """Return the run-relative locator prefix for one item directory.

        The prefix is the item directory's path relative to the run root,
        so artifact locators stay portable under any work-base layout.  When
        the item directory escapes the run root (or no run root is set),
        fail closed: a locator that cannot be resolved durably must never
        be emitted.
        """
        item_dir = context.item_directory(work_item.logical_key)
        legacy = f"items/{context.step_id}/{sanitize_job_name(work_item.logical_key)}"
        if not context.run_root:
            return legacy
        run_root = os.path.realpath(context.run_root)
        relative = os.path.relpath(os.path.abspath(item_dir), run_root)
        if relative == ".." or relative.startswith(f"..{os.sep}"):
            raise DomainError(
                f"item work directory {item_dir!r} escapes run root {run_root!r};"
                " durable artifact locators require containment"
            )
        return relative.replace(os.sep, "/")

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
        named_execution: _NamedExecution | None = None
        if _is_named_execution(context):
            try:
                named_execution = self._resolve_named_execution(work_item, context)
                driving = named_execution.reference
            except DomainError as exc:
                return self._finish_error(
                    work_item,
                    context,
                    NativeErrorCode.NATIVE_INPUT_ERROR,
                    str(exc),
                    wall_start,
                    monotonic_start,
                )
        else:
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
        if named_execution is not None:
            # Named slots already agreed on (charge, multiplicity) inside
            # _resolve_named_execution; the agreement must match the
            # single-precedence effective values, never override them.
            try:
                self._check_named_effective(named_execution, effective)
            except DomainError as exc:
                return self._finish_error(
                    work_item,
                    context,
                    NativeErrorCode.NATIVE_INPUT_ERROR,
                    str(exc),
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
        if named_execution is not None:
            extra_structures = named_execution.slots
        else:
            extra_structures = FrozenDict(
                {
                    port: value
                    for port, value in work_item.named_inputs.structures.items()
                    if port != DRIVING_STRUCTURE_PORT
                }
            )
        resolved = ResolvedCalculationInputs(
            structure=driving,
            charge=effective.charge,
            multiplicity=effective.multiplicity,
            freeze=effective.freeze,
            resources=work_item.resources,
            native=context.scientific.native,
            checkpoints=tuple(staged),
            extra_structures=extra_structures,
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
                recovery_attempt=0,
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
            recovery_attempt=0,
        )

    # ------------------------------------------------------------------
    # Internal pipeline stages
    # ------------------------------------------------------------------

    def _resubject_restart_artifacts(
        self, profile_output: ProfileOutput, work_item: WorkItem
    ) -> ProfileOutput:
        """Re-subject restart artifacts to the profile's output structures.

        Single-output items keep the V4-4 rule (restart artifacts redirect
        to the sole output id).  Multi-output items follow the V4-5 rule:
        artifacts already bound to one output keep it; input-bound
        checkpoints stay work-item scoped and are never fanned out to all
        outputs.  Anything else passes through untouched.
        """
        from .multi_output import resolve_multi_output_restart_subjects

        output_ids = tuple(record.id for record in profile_output.structures)
        fixed = resolve_multi_output_restart_subjects(
            artifacts=profile_output.artifacts,
            output_ids=output_ids,
            input_subject=self._subject_of(work_item),
        )
        return ProfileOutput(
            structures=profile_output.structures,
            results=profile_output.results,
            artifacts=fixed,
            geometry_semantics=profile_output.geometry_semantics,
            diagnostics=profile_output.diagnostics,
        )

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
            # Wall clocks may step backward (container clock sync); clamp so
            # the finished/started invariant always holds.  The authoritative
            # interval remains the monotonic duration.
            finished_at=max(wall_end, wall_start),
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
        allowed_subjects = self._allowed_artifact_subjects(work_item, context)
        for port in sorted(work_item.named_inputs.artifacts):
            for index, artifact in enumerate(work_item.named_inputs.artifacts[port]):
                locator = artifact.locator
                if locator.path is None:
                    continue
                if (
                    artifact.subject_structure_id is not None
                    and artifact.subject_structure_id not in allowed_subjects
                ):
                    raise DomainError(
                        f"artifact {artifact.id!r} is bound to subject "
                        f"{artifact.subject_structure_id!r}, not one of the "
                        f"item input structures {sorted(allowed_subjects)!r}; "
                        "refusing to stage"
                    )
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
                    except Exception as exc:
                        # The stop was requested but the supervisor failed to
                        # act: the process may still run, so report an
                        # unconfirmed cancellation instead of a clean cancel.
                        return (
                            NativeExecutionResult(
                                exit_code=None,
                                wall_time_seconds=time.monotonic() - monotonic_start,
                                stdout_file=request.stdout_file,
                                stderr_file=request.stderr_file,
                            ),
                            CancelOutcome(
                                confirmed=False,
                                detail=f"cancel request failed: {exc}",
                            ),
                        )
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
        recovery_attempt: int = 0,
    ) -> WorkItemResult:
        item_dir = context.item_directory(work_item.logical_key)
        discovered = context.adapter.discover_artifacts(
            work_dir=item_dir,
            run_relative_prefix=self._run_relative_prefix(context, work_item),
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
        profile_output = self._resubject_restart_artifacts(profile_output, work_item)
        if context.profile.name == "path_endpoints" and len(profile_output.structures) != 2:
            return self._finish_error(
                work_item,
                context,
                "incomplete_path",
                "reaction-path profile did not yield exactly two endpoints; "
                "refusing to emit a partial path",
                wall_start,
                monotonic_start,
                diagnostics=tuple(
                    list(base_diagnostics)
                    + list(native_result.parser_diagnostics)
                    + list(profile_output.diagnostics)
                ),
                details={
                    "emitted_structures": len(profile_output.structures),
                    "profile": context.profile.name,
                },
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
            try:
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
            except Exception as exc:
                # A check that cannot even be evaluated fails closed; it must
                # never propagate and kill the batch.
                failures.append(
                    _diagnostic(
                        NativeErrorCode.SCIENTIFIC_CHECK_ERROR,
                        f"check {check.name!r} raised during evaluation: {exc}",
                        step_id=context.step_id,
                        work_item_id=work_item.id,
                        logical_key=work_item.logical_key,
                        details={"check": check.name, "reason": "check_raised"},
                    )
                )
                continue
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
            recovery_attempt=recovery_attempt,
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
        recovery_attempt: int = 0,
    ) -> WorkItemResult:
        recovery = context.recovery
        if recovery is None or getattr(recovery, "name", "none") == "none":
            return failed
        if recovery_attempt >= _MAX_RECOVERY_ROUNDS:
            # Single-round semantics: a previous recovery execution already
            # ran and the item still fails.  Stop even if the policy would
            # attempt again; policies own stricter caps for their own callers.
            from .recovery import RecoveryDecision

            return self._with_recovery_note(
                failed,
                RecoveryDecision(attempt=False, reason="max_attempts_reached"),
                attempted=True,
                succeeded=False,
            )
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
            context,
            work_item,
            native_result,
            failed,
            merged_params,
            cancellation_confirmed,
            recovery_attempt,
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
            recovery_attempt=recovery_attempt + 1,
        )
        if recovered.is_completed:
            return self._mark_recovered(recovered, decision)
        return self._with_recovery_note(recovered, decision, attempted=True, succeeded=False)

    def _recovery_context(
        self,
        context,
        work_item,
        native_result,
        failed,
        merged_params,
        cancellation_confirmed,
        recovery_attempt: int = 0,
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
            attempt=recovery_attempt,
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

    def _resolve_named_execution(
        self, work_item: WorkItem, context: ItemExecutionContext
    ) -> _NamedExecution:
        """Validate named-slot inputs plus atom mapping before any launch.

        Structural slots resolve first; semantic cardinality/group rules
        come from :mod:`confflow.execution.named_structures`; atom
        correspondence comes from :mod:`confflow.execution.atom_mapping`.
        Every failure raises a :class:`DomainError` so callers report a
        typed ``native_input_error`` with zero native invocations.  The
        executor never understands QST2/QST3/NEB — only slots, mapping,
        and compatibility.
        """
        from .atom_mapping import parse_atom_mapping, validate_atom_mapping
        from .execution_adapters import GUESS_SLOT
        from .named_structures import resolve_named_inputs, validate_named_compatibility

        slots = resolve_named_slot_sets(work_item)
        guess_sets = slots.get(GUESS_SLOT)
        require_guess = guess_sets is not None and len(guess_sets) > 0
        named = resolve_named_inputs(work_item, require_guess=require_guess)
        validate_named_compatibility(named)
        mapping = parse_atom_mapping(context.scientific.native.get("atom_mapping"))
        slot_atoms = {
            port: tuple(value[0].atoms) for port, value in slots.items() if len(value) > 0
        }
        validate_atom_mapping(mapping, slot_atoms)
        return _NamedExecution(reference=named.reactant, slots=slots, mapping=mapping, named=named)

    @staticmethod
    def _check_named_effective(named_execution: _NamedExecution, effective: Any) -> None:
        """Require slot-agreed charge/mult to match the effective values."""
        from .named_structures import validate_named_compatibility

        charge, multiplicity = validate_named_compatibility(named_execution.named)
        if charge != effective.charge or multiplicity != effective.multiplicity:
            raise DomainError(
                "named-slot charge/multiplicity "
                f"({charge!r}, {multiplicity!r}) disagree with the resolved "
                f"effective values ({effective.charge!r}, {effective.multiplicity!r})"
            )

    def _resolved_inputs(
        self, work_item: WorkItem, context: ItemExecutionContext
    ) -> ResolvedCalculationInputs:
        from ..workflow.v4.scientific import resolve_scientific_parameters

        if _is_named_execution(context):
            named_execution = self._resolve_named_execution(work_item, context)
            driving = named_execution.reference
            extra = named_execution.slots
        else:
            driving = select_driving_structure(work_item)
            extra = FrozenDict(
                {
                    port: value
                    for port, value in work_item.named_inputs.structures.items()
                    if port != DRIVING_STRUCTURE_PORT
                }
            )
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
            extra_structures=extra,
            step_id=context.step_id,
            work_item_id=work_item.id,
            logical_key=work_item.logical_key,
        )

    @staticmethod
    def _allowed_artifact_subjects(work_item: WorkItem, context: ItemExecutionContext) -> set[str]:
        """Return the input structure ids artifacts may be bound to.

        Standard items allow exactly the driving structure; named items
        allow every named slot structure.  Anything else fails closed at
        staging, before any native launch.
        """
        if _is_named_execution(context):
            return {
                record.id for sets in work_item.named_inputs.structures.values() for record in sets
            }
        return {select_driving_structure(work_item).id}

    @staticmethod
    def _subject_of(work_item: WorkItem) -> str | None:
        try:
            return select_driving_structure(work_item).id
        except DomainError:
            pass
        reactant = work_item.named_inputs.structures.get("reactant")
        if reactant is not None and len(reactant) == 1:
            record = reactant[0]
            return str(record.id) if isinstance(record, StructureRecord) else None
        return None
