#!/usr/bin/env python3
"""External-script executor: one registered script per input structure.

Each work item drives a single structure through a server-registered
script (``[scripts.<id>]`` in server.toml). The item directory
``<run_root>/script/<step_id>/<item_key>/`` derives from the script
content hash, the expanded arguments, and the input structure, so an
interrupted run re-invokes the script in the same directory while a
changed script becomes a new task. Launch and quota reuse the native
work-item integration point; success needs exit 0 plus every declared
output file. Declared ``structures`` xyz is imported with the same
``import_xyz`` run inputs use; ``summary`` JSON is stored verbatim.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import shutil
import stat
import time
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from ..domain._immutable import FrozenDict
from ..domain.artifact import ArtifactLocator, ArtifactRef, ArtifactSet
from ..domain.completion import WorkItemStatus
from ..domain.diagnostics import Diagnostic, DiagnosticSeverity
from ..domain.errors import DomainError
from ..domain.result import ResultSet, ScientificResult, make_result_id
from ..domain.structure import StructureRecord, StructureSet
from ..domain.work_item import RecoveryInfo, Timing, WorkItem, WorkItemResult
from .execution_policy import enforce_execution_policy
from .native import NativeExecutionRequest
from .quota import QuotaCancelled, QuotaError
from .script_registry import (
    ScriptEntry,
    expand_script_args,
    format_mem_gb,
    load_script_registry,
    script_identity,
    validate_script_args_template,
)
from .work_item_executor import (
    ItemExecutionContext,
    WorkItemExecutor,
    error_result,
    pure_cancelled_result,
    select_driving_structure,
)
from .xyz_import import import_xyz

__all__ = ["ScriptExecutor", "script_item_key", "serialize_script_input"]

#: Declared-summary size cap (N2.4).
SUMMARY_MAX_BYTES = 1 << 20

#: stderr lines attached to failure diagnostics.
_STDERR_TAIL_LINES = 20
_STDERR_TAIL_CHARS = 4000

_LOG = logging.getLogger(__name__)


def _file_state(path: Path) -> tuple[int, ...] | None:
    """Return a regular file's identity and write state, or ``None`` for anything else.

    Any write or truncation moves ``st_mtime_ns``/``st_ctime_ns``; a replaced file gets a
    new inode, so an unchanged tuple means the file was not touched.
    """
    try:
        info = path.stat()
    except OSError:
        return None
    if not stat.S_ISREG(info.st_mode):
        return None
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def serialize_script_input(record: StructureRecord) -> str:
    """Render *record* as single-frame xyz text for ``{input}``."""
    rows = [
        f"{symbol} {x:.10f} {y:.10f} {z:.10f}"
        for symbol, (x, y, z) in zip(record.atoms, record.coordinates)
    ]
    return f"{len(rows)}\ninput {record.id}\n" + "\n".join(rows) + "\n"


def script_item_key(
    *, identity: Mapping[str, Any], expanded_args: tuple[str, ...], structure: StructureRecord
) -> str:
    """Derive the item directory key from the script identity, args, and input.

    *identity* is :func:`script_identity` of the registered entry: script hash,
    registered command (script element replaced by its hash), and interpreter
    content hash, so a changed registration gets a fresh item directory.
    """
    payload = {
        "script": dict(identity),
        "args": list(expanded_args),
        "structure": structure.reuse_payload(),
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.blake2b(canonical.encode("utf-8"), digest_size=16).hexdigest()


def _diagnostic(
    code: str,
    message: str,
    *,
    step_id: str | None,
    work_item: WorkItem,
    details: dict[str, Any] | None = None,
    severity: DiagnosticSeverity = DiagnosticSeverity.ERROR,
) -> Diagnostic:
    return Diagnostic(
        code=code,
        message=message,
        severity=severity,
        step_id=step_id,
        work_item_id=work_item.id,
        logical_key=work_item.logical_key,
        details=FrozenDict(details or {}),
    )


def _resolved_script_command(entry: ScriptEntry) -> tuple[str, ...]:
    """Return *entry.command* with the script element as an absolute path."""
    parts = list(entry.command)
    parts[entry.script_arg_index] = str(entry.script_path)
    return tuple(parts)


class ScriptExecutor:
    """Execute one work item through a registered external script."""

    def __init__(self) -> None:
        self._launcher = WorkItemExecutor()

    def execute(
        self,
        work_item: WorkItem,
        context: ItemExecutionContext,
        *,
        should_cancel: Callable[[], bool] | None = None,
    ) -> WorkItemResult:
        """Execute one script work item (never raises)."""
        wall_start = time.time()
        monotonic_start = time.monotonic()
        try:
            return self._run(work_item, context, wall_start, monotonic_start, should_cancel)
        except QuotaCancelled:
            return pure_cancelled_result(
                work_item,
                step_id=context.step_id,
                wall_start=wall_start,
                monotonic_start=monotonic_start,
            )
        except QuotaError as exc:
            return self._fail(
                work_item,
                context,
                wall_start,
                monotonic_start,
                "quota_exceeded",
                str(exc),
            )
        except DomainError as exc:
            return self._fail(
                work_item, context, wall_start, monotonic_start, "script_failed", str(exc)
            )
        except Exception as exc:  # fail closed; executors never raise into batch
            return self._fail(
                work_item,
                context,
                wall_start,
                monotonic_start,
                "script_failed",
                f"script internal failure: {exc}",
            )

    def _run(
        self,
        work_item: WorkItem,
        context: ItemExecutionContext,
        wall_start: float,
        monotonic_start: float,
        should_cancel: Callable[[], bool] | None,
    ) -> WorkItemResult:
        scientific = context.scientific
        script_id = getattr(scientific, "script_id", None)
        if not script_id:
            raise DomainError("script steps require a registered script id")
        template = validate_script_args_template(tuple(getattr(scientific, "script_args", ())))
        outputs = dict(getattr(scientific, "script_outputs", {}) or {})
        if context.supervisor is None:
            raise DomainError("no process supervisor is configured")
        if not context.run_root:
            raise DomainError("script execution requires a run root for item directories")
        driving = select_driving_structure(work_item)
        try:
            registry = load_script_registry()
        except QuotaError as exc:
            raise DomainError(str(exc)) from exc
        entry = registry.get(script_id)
        if entry is None:
            raise DomainError(f"unknown script {script_id!r}; expected one of {sorted(registry)}")
        resources = work_item.resources
        cores = resources.cores_per_item
        memory = resources.memory_per_item_bytes
        if cores is None or memory is None:
            raise DomainError("script work items require resolved cores and memory")
        mem_gb = format_mem_gb(memory)
        # {input} is a pure function of the key below, so the key hashes the
        # args with {input} in relative form; the launch itself uses the
        # absolute path. Distinct parameters still hash distinctly.
        key_args = expand_script_args(
            template, input_path="input.xyz", cores=cores, mem_gb=mem_gb, item_id=work_item.id
        )
        item_key = script_item_key(
            identity=script_identity(entry), expanded_args=key_args, structure=driving
        )
        item_dir = os.path.join(str(context.run_root), "script", context.step_id, item_key)
        resolved = _resolved_script_command(entry)
        if len(resolved) == 1:
            interpreter = str(entry.script_path)
        else:
            interpreter = self._resolve_interpreter(resolved[0])
        binding = context.execution_binding
        # Policy before any directory or input file is written (direct batch
        # callers bypass the step-level check in the application layer).
        enforce_execution_policy(
            binding,
            executable=interpreter,
            resolved_executable=interpreter,
            write_paths=(item_dir,),
            label="script interpreter",
        )
        os.makedirs(item_dir, exist_ok=True)
        input_path = os.path.join(item_dir, "input.xyz")
        # The item dir is keyed by inputs, so a retry reuses it. Declared
        # structures/summary files of a failed attempt are removed here;
        # checkpoints and other script files stay in place for resume.
        self._clear_declared_outputs(item_dir, outputs, protected=input_path)
        # Artifacts are matched by glob, so files left by an earlier attempt would match
        # too. Record their state now; only files this attempt creates or rewrites are kept.
        artifact_state_before = self._artifact_file_states(outputs, item_dir)
        with open(input_path, "w", encoding="utf-8") as handle:
            handle.write(serialize_script_input(driving))
        full_args = expand_script_args(
            template, input_path=input_path, cores=cores, mem_gb=mem_gb, item_id=work_item.id
        )
        if len(resolved) == 1:
            argv = (interpreter,) + tuple(full_args)
        else:
            argv = (interpreter,) + tuple(resolved[1:]) + tuple(full_args)
        walltime = float(binding.walltime_seconds) if binding and binding.walltime_seconds else None
        # Same environment rule as native steps: the snapshot hashed into the
        # environment digest (ambient + declared), else the ambient process env.
        if context.native_env is not None:
            launch_env = dict(context.native_env)
        else:
            launch_env = dict(os.environ)
        request = NativeExecutionRequest(
            executable=interpreter,
            argv=argv,
            work_dir=item_dir,
            env=FrozenDict(launch_env),
            walltime_seconds=walltime,
            stdout_file="stdout.log",
            stderr_file="stderr.log",
            metadata=FrozenDict({"script_id": script_id, "item_key": item_key}),
        )
        launched = self._launcher.launch_and_wait(
            context.supervisor,
            request,
            context.poll_interval_seconds,
            should_cancel=should_cancel,
            quota=(work_item.resources, str(context.run_root), work_item.id),
        )
        provenance = {
            "script_id": script_id,
            "script_path": entry.script_path,
            "script_sha256": entry.sha256,
            "interpreter": interpreter,
            "argv": list(argv),
            "item_key": item_key,
        }
        if launched is None:
            if should_cancel is not None and should_cancel():
                return self._fail(
                    work_item,
                    context,
                    wall_start,
                    monotonic_start,
                    "cancellation_error",
                    "script process handle was lost before completion; "
                    "cancellation could not be confirmed",
                    details={**provenance, "confirmed": False},
                )
            return self._fail(
                work_item,
                context,
                wall_start,
                monotonic_start,
                "script_failed",
                "script process handle was lost before completion",
                details=provenance,
            )
        execution_result, cancel_outcome = launched
        if cancel_outcome is not None:
            if cancel_outcome.confirmed:
                return self._cancelled(work_item, context, wall_start, monotonic_start)
            details = dict(provenance)
            if execution_result.timed_out:
                details["timed_out"] = True
            return self._fail(
                work_item,
                context,
                wall_start,
                monotonic_start,
                "cancellation_error",
                f"cancellation could not be confirmed: {cancel_outcome.detail}",
                details=details,
            )
        if execution_result.timed_out:
            # Reported like native walltime overruns: same details keys, the
            # script failure code, and the configured limit in the message.
            return self._fail(
                work_item,
                context,
                wall_start,
                monotonic_start,
                "script_failed",
                f"script execution exceeded walltime of {walltime:g} seconds",
                details={**provenance, "timed_out": True, "walltime_seconds": walltime},
            )
        if execution_result.exit_code is None:
            return self._fail(
                work_item,
                context,
                wall_start,
                monotonic_start,
                "script_failed",
                "script execution did not exit cleanly",
                details=provenance,
            )
        provenance["exit_code"] = execution_result.exit_code
        provenance["elapsed_seconds"] = execution_result.wall_time_seconds
        stderr_tail = self._stderr_tail(item_dir)
        if execution_result.exit_code != 0:
            return self._fail(
                work_item,
                context,
                wall_start,
                monotonic_start,
                "script_failed",
                f"script {script_id!r} exited with code {execution_result.exit_code}",
                details={**provenance, "stderr_tail": stderr_tail},
            )
        try:
            structures = self._collect_structures(outputs, item_dir, work_item, context, item_key)
            results = self._collect_summary(outputs, item_dir, work_item, driving)
            artifacts = self._collect_artifacts(
                outputs, item_dir, work_item, context, item_key, before=artifact_state_before
            )
        except DomainError as exc:
            return self._fail(
                work_item,
                context,
                wall_start,
                monotonic_start,
                "script_failed",
                str(exc),
                details={**provenance, "stderr_tail": stderr_tail},
            )
        timing = Timing(
            started_at=wall_start,
            finished_at=max(time.time(), wall_start),
            duration_seconds=max(0.0, time.monotonic() - monotonic_start),
        )
        completed = Diagnostic(
            code="script_completed",
            message=f"script {script_id!r} completed for {driving.id}",
            severity=DiagnosticSeverity.INFO,
            step_id=context.step_id,
            work_item_id=work_item.id,
            logical_key=work_item.logical_key,
            details=FrozenDict(provenance),
        )
        return WorkItemResult(
            work_item_id=work_item.id,
            status=WorkItemStatus.COMPLETED,
            structures=structures,
            results=results,
            artifacts=artifacts,
            diagnostics=(completed,),
            timing=timing,
            recovery=RecoveryInfo(profile="none", attempted=False),
            semantic_digest=work_item.semantic_digest,
        )

    @staticmethod
    def _resolve_interpreter(interpreter: str) -> str:
        """Resolve the registered interpreter to an executable path."""
        if os.path.isabs(interpreter):
            if not os.path.isfile(interpreter):
                raise DomainError(f"script interpreter not found: {interpreter!r}")
            return interpreter
        resolved = shutil.which(interpreter)
        if resolved is None:
            raise DomainError(f"script interpreter {interpreter!r} was not found on PATH")
        return resolved

    @staticmethod
    def _stderr_tail(item_dir: str) -> str:
        """Return the last lines of the script stderr log, possibly empty."""
        try:
            with open(os.path.join(item_dir, "stderr.log"), encoding="utf-8") as handle:
                text = handle.read()
        except OSError:
            return ""
        lines = text.splitlines()[-_STDERR_TAIL_LINES:]
        tail = "\n".join(lines)
        return tail[-_STDERR_TAIL_CHARS:]

    def _collect_structures(
        self,
        outputs: dict[str, Any],
        item_dir: str,
        work_item: WorkItem,
        context: ItemExecutionContext,
        item_key: str,
    ) -> StructureSet:
        """Import the declared multi-frame xyz as the structures output."""
        declared = outputs.get("structures")
        if declared is None:
            return StructureSet()
        path = self._channel_path(item_dir, declared, "structures")
        if not path.is_file():
            raise DomainError(f"script declared structures file {declared!r} is missing")
        try:
            text = path.read_text(encoding="utf-8")
        except OSError as exc:
            raise DomainError(f"script structures file {declared!r} is unreadable: {exc}") from exc
        try:
            first_pass = import_xyz(text, source_name=f"{context.step_id}/{work_item.id}")
        except DomainError as exc:
            raise DomainError(f"script structures file {declared!r} is invalid: {exc}") from exc
        entity_ids = tuple(f"{item_key}-{index:04d}" for index in range(len(first_pass)))
        try:
            return import_xyz(
                text,
                source_name=f"{context.step_id}/{work_item.id}",
                entity_ids=entity_ids,
            )
        except DomainError as exc:  # pragma: no cover - same text parsed above
            raise DomainError(f"script structures file {declared!r} is invalid: {exc}") from exc

    def _collect_summary(
        self,
        outputs: dict[str, Any],
        item_dir: str,
        work_item: WorkItem,
        driving: StructureRecord,
    ) -> ResultSet:
        """Read the declared summary JSON verbatim into the results output."""
        declared = outputs.get("summary")
        if declared is None:
            return ResultSet()
        path = self._channel_path(item_dir, declared, "summary")
        if not path.is_file():
            raise DomainError(f"script declared summary file {declared!r} is missing")
        try:
            size = path.stat().st_size
        except OSError as exc:
            raise DomainError(f"script summary file {declared!r} is unreadable: {exc}") from exc
        if size > SUMMARY_MAX_BYTES:
            raise DomainError(
                f"script summary file {declared!r} is {size} bytes; limit is {SUMMARY_MAX_BYTES}"
            )
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise DomainError(f"script summary file {declared!r} is not JSON: {exc}") from exc
        if not isinstance(payload, dict):
            raise DomainError(f"script summary file {declared!r} must hold a JSON object")
        record = ScientificResult(
            kind="script_summary",
            value=payload,
            subject_structure_id=driving.id,
            source_step_id=work_item.step_id,
            source_work_item_id=work_item.id,
            result_id=make_result_id(
                step_id=work_item.step_id,
                work_item_id=None,
                kind="script_summary",
                subject_structure_id=driving.id,
                producer_digest=work_item.semantic_digest,
            ),
        )
        return ResultSet.of(record)

    def _collect_artifacts(
        self,
        outputs: dict[str, Any],
        item_dir: str,
        work_item: WorkItem,
        context: ItemExecutionContext,
        item_key: str,
        *,
        before: Mapping[str, tuple[int, ...]] | None = None,
    ) -> ArtifactSet:
        """Save files matching the declared artifact patterns.

        With *before* (a pre-launch :meth:`_artifact_file_states` snapshot), files whose
        state is unchanged since the snapshot are left out: they belong to an earlier attempt.
        """
        patterns = outputs.get("artifacts")
        if not patterns:
            return ArtifactSet()
        if not isinstance(patterns, (list, tuple)) or not all(
            isinstance(item, str) and item for item in patterns
        ):
            raise DomainError("script artifacts must be a list of non-empty glob patterns")
        base = Path(item_dir)
        item_real = os.path.realpath(item_dir)
        run_root = os.path.realpath(str(context.run_root))
        matched: list[Path] = []
        for pattern in patterns:
            for candidate in sorted(base.glob(pattern)):
                if not candidate.is_file() or candidate in matched:
                    continue
                if before is not None and before.get(str(candidate)) == _file_state(candidate):
                    continue
                matched.append(candidate)
        refs: list[ArtifactRef] = []
        for index, candidate in enumerate(matched):
            candidate_real = os.path.realpath(candidate)
            if candidate_real != item_real and not candidate_real.startswith(item_real + os.sep):
                raise DomainError(f"script artifact {candidate.name!r} escapes the item directory")
            relative = os.path.relpath(candidate_real, run_root).replace(os.sep, "/")
            if relative == ".." or relative.startswith("../"):
                raise DomainError(f"script artifact {candidate.name!r} escapes the run root")
            digest = hashlib.sha256(candidate.read_bytes()).hexdigest()
            refs.append(
                ArtifactRef(
                    id=f"script-{context.step_id}-{item_key}-{index}",
                    role="script_artifact",
                    locator=ArtifactLocator.run_relative(relative),
                    checksum="sha256:" + digest,
                    subject_structure_id=None,
                    producer_step_id=work_item.step_id,
                    producer_work_item_id=None,
                    metadata=FrozenDict({"script_item_key": item_key}),
                )
            )
        return ArtifactSet.of(*refs)

    @staticmethod
    def _artifact_file_states(outputs: dict[str, Any], item_dir: str) -> dict[str, tuple[int, ...]]:
        """Return the state of every file the artifact patterns match right now.

        Invalid pattern declarations are skipped here and reported after the run.
        """
        patterns = outputs.get("artifacts")
        if not isinstance(patterns, (list, tuple)):
            return {}
        base = Path(item_dir)
        states: dict[str, tuple[int, ...]] = {}
        for pattern in patterns:
            if not isinstance(pattern, str) or not pattern:
                continue
            for candidate in base.glob(pattern):
                state = _file_state(candidate)
                if state is not None:
                    states[str(candidate)] = state
        return states

    def _clear_declared_outputs(
        self, item_dir: str, outputs: dict[str, Any], *, protected: str
    ) -> None:
        """Delete declared structures/summary files left by an earlier attempt.

        Only the declared channel files are removed; artifact globs are not
        (they may match inputs or checkpoints). *protected* is never removed.
        Invalid declarations are skipped here and reported after the run.
        """
        protected_real = os.path.realpath(protected)
        for channel in ("structures", "summary"):
            declared = outputs.get(channel)
            if declared is None:
                continue
            try:
                path = self._channel_path(item_dir, declared, channel)
            except DomainError:
                continue
            if os.path.realpath(path) == protected_real:
                continue
            if path.is_file() or path.is_symlink():
                path.unlink()

    @staticmethod
    def _channel_path(item_dir: str, declared: Any, channel: str) -> Path:
        """Resolve a declared structures/summary filename inside *item_dir*."""
        if not isinstance(declared, str) or not declared or os.path.isabs(declared):
            raise DomainError(f"script {channel} output must be a relative file name")
        path = Path(item_dir) / declared
        if Path(os.path.realpath(path)).parent != Path(os.path.realpath(item_dir)):
            raise DomainError(f"script {channel} output {declared!r} escapes the item directory")
        return path

    def _fail(
        self,
        work_item: WorkItem,
        context: ItemExecutionContext,
        wall_start: float,
        monotonic_start: float,
        code: str,
        message: str,
        *,
        details: dict[str, Any] | None = None,
    ) -> WorkItemResult:
        timing = Timing(
            started_at=wall_start,
            finished_at=max(time.time(), wall_start),
            duration_seconds=max(0.0, time.monotonic() - monotonic_start),
        )
        return error_result(
            work_item,
            code,
            message,
            diagnostics=(
                _diagnostic(
                    code, message, step_id=context.step_id, work_item=work_item, details=details
                ),
            ),
            details=details,
            timing=timing,
            recovery=RecoveryInfo(profile="none", attempted=False),
        )

    def _cancelled(
        self,
        work_item: WorkItem,
        context: ItemExecutionContext,
        wall_start: float,
        monotonic_start: float,
    ) -> WorkItemResult:
        return pure_cancelled_result(
            work_item,
            step_id=context.step_id,
            wall_start=wall_start,
            monotonic_start=monotonic_start,
        )
