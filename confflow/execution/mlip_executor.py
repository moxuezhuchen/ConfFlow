#!/usr/bin/env python3

"""MLIP (UMA) batch executor: one whole-set work item per step.

Pure executor for the ``mlip`` step.  It writes ``job.json`` for the standalone
runner ``mlip_runner.py``, launches it under the process supervisor (walltime,
cancellation) with the execution binding's interpreter, and turns the runner's
``mlip_results.jsonl`` into structures, electronic-energy results, artifacts and
diagnostics.  Never raises: every failure is a typed FAILED result.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import shutil
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from ..domain._immutable import FrozenDict
from ..domain.artifact import ArtifactLocator, ArtifactRef, ArtifactSet
from ..domain.completion import WorkItemStatus
from ..domain.diagnostics import Diagnostic, DiagnosticSeverity
from ..domain.errors import DomainError
from ..domain.result import Provenance, ResultSet, ScientificResult, make_result_id
from ..domain.structure import StructureRecord, StructureSet
from ..domain.units import Unit
from ..domain.work_item import RecoveryInfo, ResultError, Timing, WorkItem, WorkItemResult
from ..science.topology import resolve_and_persist_kwargs
from .command_resolution import launch_environment, resolve_and_enforce_command
from .mlip_analysis import (
    MlipOutput,
    calibration,
    fix_deviation,
    mean,
    stretch_check,
    xyz_text,
)
from .native import NativeExecutionRequest
from .output_identity import endpoint_lineage
from .quota import QuotaCancelled, QuotaError
from .work_item_executor import (
    ItemExecutionContext,
    WorkItemExecutor,
    pure_cancelled_result,
    pure_fail_result,
)

__all__ = ["MlipExecutor", "RUNNER_PATH"]

#: Runner source copied into each attempt directory (tests point this elsewhere).
RUNNER_PATH = Path(__file__).with_name("mlip_runner.py")

#: Program name recorded in every MLIP energy result's provenance (UMA runs through fairchem).
PROGRAM_NAME = "fairchem"
#: Adapter contract recorded in MLIP result provenance.
ADAPTER_CONTRACT = "confflow.mlip.executor.v1"

MODEL_ENV = "CONFFLOW_MLIP_MODEL_PATH"
REFS_ENV = "CONFFLOW_MLIP_REFS_PATH"
DEVICE_ENV = "CONFFLOW_MLIP_DEVICE"
THREADS_ENV = "CONFFLOW_MLIP_THREADS"
DEFAULT_THREADS = 4
MODES = ("sp", "relax", "sp_then_relax")
OPTIMIZERS = ("lbfgs", "precon_lbfgs", "fire")
#: Stretch ratio above which a kept structure gets a WARNING.
STRETCH_WARN_RATIO = 1.25
#: Items listed in a diagnostic message before truncation.
_LIST_LIMIT = 10
_STDERR_TAIL_LINES = 20
_STDERR_TAIL_CHARS = 4000
_JOB_NAME = "job.json"
_RESULTS_NAME = "mlip_results.jsonl"
_DONE_NAME = "mlip_done.json"
#: Runner exit status and done-file status of a job whose backend failed to load (see
#: ``mlip_runner.BACKEND_EXIT_CODE`` / ``BACKEND_FAILED``; a test keeps them equal).
_BACKEND_EXIT_CODE = 75
_BACKEND_FAILED = "backend_failed"
_LOG_NAME = "mlip_runner.log"
_XYZ_NAME = "mlip_structures.xyz"
_ARTIFACT_FILES = (
    ("mlip_job", _JOB_NAME),
    ("mlip_results", _RESULTS_NAME),
    ("mlip_done", _DONE_NAME),
    ("mlip_log", _LOG_NAME),
    ("mlip_structures", _XYZ_NAME),
)
#: Failure codes: a task-level backend failure, and a run where no structure survived errors.
BACKEND_FAILURE_CODE = "mlip_backend_failed"
ALL_ERRORED_CODE = "mlip_all_errored"


@dataclass(frozen=True)
class _Plan:
    """Validated native parameters of one mlip step."""

    model: str
    task: str
    mode: str
    index_base: int
    optimizer: str
    fmax: float
    max_steps: int
    fix_pairs: tuple[tuple[int, int], ...]
    fix_targets: tuple[float | None, ...]  # per pair: target distance in Angstrom, None = input
    pick: dict[str, Any] | None
    reject_stretch: float | None


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _parse_native(native: Mapping[str, Any], records: Sequence[StructureRecord]) -> _Plan:
    """Validate the native mapping; raise DomainError with a clear message."""
    model = native.get("model")
    if not isinstance(model, str) or not model:
        raise DomainError("mlip step requires a non-empty native 'model' name")
    mode = native.get("mode")
    if mode not in MODES:
        raise DomainError(f"mlip mode must be one of {list(MODES)}, got {mode!r}")
    index_base = native.get("index_base", 1)
    if not _is_int(index_base) or index_base not in (0, 1):
        raise DomainError(f"mlip index_base must be 0 or 1, got {index_base!r}")
    relax = dict(native.get("relax") or {})
    optimizer = relax.get("optimizer", "lbfgs")
    if optimizer not in OPTIMIZERS:
        raise DomainError(f"mlip optimizer must be one of {list(OPTIMIZERS)}, got {optimizer!r}")
    fmax = float(relax.get("fmax", 0.02))
    if not math.isfinite(fmax) or fmax <= 0:
        raise DomainError(f"mlip fmax must be a positive number, got {fmax!r}")
    max_steps = relax.get("max_steps", 600)
    if not _is_int(max_steps) or max_steps < 1:
        raise DomainError(f"mlip max_steps must be an integer >= 1, got {max_steps!r}")
    fixed = _fix_pairs(relax.get("fix_distances") or (), index_base, records)
    pick = _parse_pick(native.get("pick"), mode)
    reject = native.get("reject_bond_stretch")
    if reject is not None:
        reject = float(reject)
        if not math.isfinite(reject) or reject <= 1.0:
            raise DomainError(f"mlip reject_bond_stretch must be a ratio > 1, got {reject!r}")
    return _Plan(
        model=model,
        task=str(native.get("task", "omol")),
        mode=str(mode),
        index_base=int(index_base),
        optimizer=str(optimizer),
        fmax=fmax,
        max_steps=int(max_steps),
        fix_pairs=tuple(pair for pair, _ in fixed),
        fix_targets=tuple(target for _, target in fixed),
        pick=pick,
        reject_stretch=reject,
    )


def _parse_pick(raw: Any, mode: str) -> dict[str, Any] | None:
    """Return the normalised pick mapping, required for sp_then_relax."""
    if raw is None:
        if mode == "sp_then_relax":
            raise DomainError("mlip sp_then_relax requires a 'pick' selection")
        return None
    pick = dict(raw)
    lowest = pick.get("lowest")
    per_label = pick.get("per_label")
    for name, value in (("lowest", lowest), ("per_label", per_label)):
        if value is not None and (not _is_int(value) or value < 0):
            raise DomainError(f"mlip pick.{name} must be an integer >= 0, got {value!r}")
    return {"lowest": lowest, "per_label": per_label}


def _fix_pairs(
    raw: Any, index_base: int, records: Sequence[StructureRecord]
) -> tuple[tuple[tuple[int, int], float | None], ...]:
    """Convert fix_distances to 0-based ``((i, j), target)``, checking every record's atom range.

    Entries are ``[i, j]`` (target None: hold the input distance) or ``[i, j, target]``.
    """
    pairs: list[tuple[tuple[int, int], float | None]] = []
    for item in raw:
        if not (
            isinstance(item, (list, tuple))
            and len(item) in (2, 3)
            and all(_is_int(v) for v in item[:2])
        ):
            raise DomainError(
                f"mlip fix_distances entries must be [i, j] or [i, j, target] "
                f"with integer indices: {item!r}"
            )
        target: float | None = None
        if len(item) == 3:
            value = item[2]
            if not (isinstance(value, (int, float)) and not isinstance(value, bool)):
                raise DomainError(f"mlip fix_distances target must be a number: {list(item)}")
            target = float(value)
            if not math.isfinite(target) or target <= 0:
                raise DomainError(
                    f"mlip fix_distances target must be a finite distance > 0: {list(item)}"
                )
        first, second = int(item[0]) - index_base, int(item[1]) - index_base
        if first == second:
            raise DomainError(f"mlip fix_distances pair {list(item)} repeats one atom")
        for record in records:
            count = len(record.atoms)
            if not (0 <= first < count and 0 <= second < count):
                raise DomainError(
                    f"mlip fix_distances pair {list(item)} is out of range for {record.id} "
                    f"({count} atoms, index_base {index_base})"
                )
        pairs.append(((first, second), target))
    return tuple(pairs)


def _unresolved_interpreter_message(interpreter: str) -> str:
    """Return the failure text for an MLIP interpreter that does not resolve."""
    if os.path.isabs(interpreter):
        return f"mlip interpreter not found: {interpreter!r}"
    return f"mlip interpreter {interpreter!r} was not found on PATH"


def _launch_env(context: ItemExecutionContext) -> dict[str, str]:
    """Effective environment: the bound native snapshot, else the binding over os.environ."""
    return dict(launch_environment(context.native_env, context.execution_binding))


def _runner_settings(
    env: Mapping[str, str], cores: int | None
) -> tuple[str, str | None, str, int, int]:
    """Read model/refs/device/threads from env; return workers from cores."""
    model_path = env.get(MODEL_ENV, "").strip()
    if not model_path:
        raise DomainError(f"mlip steps need {MODEL_ENV} (model file path) in the execution env")
    refs_path = env.get(REFS_ENV, "").strip() or None
    device = env.get(DEVICE_ENV, "").strip() or "cpu"
    threads_text = env.get(THREADS_ENV, "").strip() or str(DEFAULT_THREADS)
    try:
        threads = int(threads_text)
    except ValueError as exc:
        raise DomainError(f"{THREADS_ENV} must be an integer, got {threads_text!r}") from exc
    if threads < 1:
        raise DomainError(f"{THREADS_ENV} must be >= 1, got {threads}")
    workers = max(1, (cores or 1) // threads)
    return model_path, refs_path, device, threads, workers


def _stable_keys(records: Sequence[StructureRecord]) -> dict[str, str]:
    """Map record id to a short key ``s00001`` in sorted record-id order."""
    ordered = sorted(record.id for record in records)
    return {record_id: f"s{index + 1:05d}" for index, record_id in enumerate(ordered)}


@dataclass(frozen=True)
class _Science:
    """Effective charge/multiplicity of the whole batch plus the resolver's warnings."""

    charge: int
    multiplicity: int
    diagnostics: tuple[Diagnostic, ...]


def _batch_scientific(
    records: Sequence[StructureRecord],
    context: ItemExecutionContext,
    work_item: WorkItem,
) -> _Science:
    """Resolve every structure with the step-wide scientific rules; the runner takes one pair.

    Mirrors the calculation executor: ERROR diagnostics and missing values fail, and a batch whose
    effective (charge, multiplicity) differ is rejected because one job carries one pair.
    """
    from ..workflow.v4.scientific import resolve_scientific_parameters

    pairs: set[tuple[int, int]] = set()
    missing: list[str] = []
    diagnostics: list[Diagnostic] = []
    for record in records:
        effective, found = resolve_scientific_parameters(
            structure=record,
            overrides=context.scientific.overrides,
            defaults=context.scientific_defaults,
        )
        errors = [item.message for item in found if item.severity is DiagnosticSeverity.ERROR]
        if errors:
            raise DomainError(
                f"mlip scientific parameters are inconsistent for {record.id}: " + "; ".join(errors)
            )
        diagnostics.extend(
            replace(
                item,
                step_id=context.step_id,
                work_item_id=work_item.id,
                logical_key=work_item.logical_key,
            )
            for item in found
        )
        if effective.charge is None or effective.multiplicity is None:
            missing.append(record.id)
        else:
            pairs.add((effective.charge, effective.multiplicity))
    if missing:
        raise DomainError(
            "mlip needs charge and multiplicity on every structure or in scientific_defaults; "
            f"missing for {_short(missing)}"
        )
    if len(pairs) != 1:
        raise DomainError(
            "mlip batch mixes (charge, multiplicity) pairs "
            f"{sorted(pairs)}; the runner takes one pair per job, split the batch by charge/spin"
        )
    charge, multiplicity = pairs.pop()
    return _Science(charge, multiplicity, tuple(diagnostics))


def _input_energy(record: StructureRecord) -> float | None:
    """Input electronic energy (Eh) carried by the record, when numeric."""
    value = record.metadata.get("energy_eh")
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    return None


def _inherited_metadata(record: StructureRecord) -> dict[str, Any]:
    """Input metadata minus the input energy ranking (rel_kcal is stale after relaxation)."""
    return {key: value for key, value in record.metadata.thaw().items() if key != "rel_kcal"}


def _calibration_pairs(
    plan: _Plan,
    records: Sequence[StructureRecord],
    keys: Mapping[str, str],
    rows: Mapping[tuple[str, str], Mapping[str, Any]],
    outputs: Sequence[MlipOutput],
) -> tuple[list[tuple[str, float, float]], str]:
    """(id, input Eh, MLIP Eh) pairs: sp energies of all inputs when sp rows exist, else outputs."""
    pairs: list[tuple[str, float, float]] = []
    if plan.mode in ("sp", "sp_then_relax"):
        for record in sorted(records, key=lambda item: item.id):
            gfn2 = _input_energy(record)
            sp_row = rows.get((keys[record.id], "sp"))
            if gfn2 is None or sp_row is None or sp_row.get("error"):
                continue
            try:
                pairs.append((record.id, gfn2, _finite_energy(sp_row)))
            except DomainError:
                continue
        return pairs, "sp_all_inputs"
    for out in outputs:
        gfn2 = _input_energy(out.record)
        if gfn2 is not None:
            pairs.append((out.record.id, gfn2, out.energy))
    return pairs, "relaxed_outputs"


def _provenance(plan: _Plan, work_item: WorkItem, done: Mapping[str, Any]) -> Provenance:
    """Return the producer provenance of the MLIP energies: program, model and measured version.

    The fairchem version is the one the runner measured in its own interpreter; it stays
    None when the runner did not report it, rather than inventing a version.
    """
    raw = done.get("fairchem_version")
    version = raw.strip() if isinstance(raw, str) else ""
    return Provenance(
        program=PROGRAM_NAME,
        program_version=version or None,
        method=plan.model,
        adapter=ADAPTER_CONTRACT,
        step_id=work_item.step_id,
        work_item_id=work_item.id,
        metadata=FrozenDict({"task": plan.task}),
    )


def _label(record: StructureRecord) -> str | None:
    value = record.metadata.get("search_state_flips")
    return None if value in (None, "") else str(value)


def _read_rows(path: Path) -> dict[tuple[str, str], dict[str, Any]]:
    """Index mlip_results.jsonl by (key, stage); later lines win on resume."""
    if not path.is_file():
        return {}
    rows: dict[tuple[str, str], dict[str, Any]] = {}
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except ValueError as exc:
            raise DomainError(f"mlip_results.jsonl line {number} is not JSON") from exc
        if isinstance(row, dict):
            rows[(str(row.get("key")), str(row.get("stage")))] = row
    return rows


def _read_done(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except ValueError:
        return {}
    return payload if isinstance(payload, dict) else {}


def _backend_error(done: Mapping[str, Any], exit_code: int | None) -> str | None:
    """Reason of a backend failure recorded by the runner, or None when it reported none.

    The done file and the exit status must agree; a marker with any other exit status is
    still a failure, and the disagreement is named in the reason.
    """
    if done.get("status") != _BACKEND_FAILED:
        return None
    reason = str(done.get("backend_error") or "reason not recorded by the runner")
    if exit_code == _BACKEND_EXIT_CODE:
        return reason
    return f"{reason} (runner exit code {exit_code} contradicts its backend_failed marker)"


def _coords_of(row: Mapping[str, Any], count: int) -> list[list[float]]:
    """Validate and return a relax row's coordinates for *count* atoms."""
    coords = row.get("coords")
    if not isinstance(coords, list) or len(coords) != count:
        raise DomainError("relaxed coordinates are missing or have the wrong atom count")
    return [[float(x), float(y), float(z)] for x, y, z in coords]


def _finite_energy(row: Mapping[str, Any]) -> float:
    energy = row.get("energy_eh")
    if not isinstance(energy, (int, float)) or isinstance(energy, bool):
        raise DomainError("result row has no electronic energy")
    if not math.isfinite(float(energy)):
        raise DomainError("result row energy is not finite")
    return float(energy)


def _short(items: Sequence[str]) -> str:
    shown = ", ".join(items[:_LIST_LIMIT])
    extra = len(items) - _LIST_LIMIT
    return shown + (f" (+{extra} more)" if extra > 0 else "")


def _stderr_tail(item_dir: str) -> str:
    """Last lines of stderr.log or mlip_runner.log, whichever has content."""
    for name in ("stderr.log", _LOG_NAME):
        try:
            text = (Path(item_dir) / name).read_text(encoding="utf-8")
        except OSError:
            continue
        tail = "\n".join(text.splitlines()[-_STDERR_TAIL_LINES:])[-_STDERR_TAIL_CHARS:]
        if tail.strip():
            return tail
    return ""


def _diagnostic(
    code: str,
    message: str,
    work_item: WorkItem,
    context: ItemExecutionContext,
    severity: DiagnosticSeverity,
    details: Mapping[str, Any],
) -> Diagnostic:
    return Diagnostic(
        code=code,
        message=message,
        severity=severity,
        step_id=context.step_id,
        work_item_id=work_item.id,
        logical_key=work_item.logical_key,
        details=FrozenDict(dict(details)),
    )


class MlipExecutor:
    """Run one whole-set UMA (MLIP) work item through the standalone runner."""

    def __init__(self) -> None:
        self._launcher = WorkItemExecutor()

    def execute(
        self,
        work_item: WorkItem,
        context: ItemExecutionContext,
        *,
        should_cancel: Callable[[], bool] | None = None,
    ) -> WorkItemResult:
        """Execute one mlip work item (never raises)."""
        wall_start = time.time()
        monotonic_start = time.monotonic()
        try:
            return self._run(work_item, context, wall_start, monotonic_start, should_cancel)
        except QuotaCancelled:
            return self._cancelled(work_item, context, wall_start, monotonic_start)
        except QuotaError as exc:
            return self._fail(work_item, context, wall_start, monotonic_start, str(exc))
        except DomainError as exc:
            return self._fail(work_item, context, wall_start, monotonic_start, str(exc))
        except Exception as exc:  # fail closed; executors never raise into batch
            return self._fail(
                work_item, context, wall_start, monotonic_start, f"mlip internal failure: {exc}"
            )

    def _run(
        self,
        work_item: WorkItem,
        context: ItemExecutionContext,
        wall_start: float,
        monotonic_start: float,
        should_cancel: Callable[[], bool] | None,
    ) -> WorkItemResult:
        if should_cancel is not None and should_cancel():
            return self._cancelled(work_item, context, wall_start, monotonic_start)
        records = list(work_item.named_inputs.structures.get("structure", StructureSet()))
        if not records:
            raise DomainError("mlip requires a non-empty 'structure' input set")
        if not context.run_root:
            raise DomainError("mlip execution requires a run root for the attempt directory")
        if context.supervisor is None:
            raise DomainError("no process supervisor is configured")
        plan = _parse_native(dict(context.scientific.native), records)
        env = _launch_env(context)
        cores = work_item.resources.cores_per_item
        model_path, refs_path, device, threads, workers = _runner_settings(env, cores)
        science = _batch_scientific(records, context, work_item)
        keys = _stable_keys(records)
        ordered = sorted(records, key=lambda record: record.id)
        binding = context.execution_binding
        interpreter = binding.executable if binding and binding.executable else "python3"
        work_dir = context.attempt_dir(work_item)
        # The one resolution: the application's pinned resolution is reused when present (the
        # file that was measured), else the interpreter is resolved on the launch environment.
        # The policy is checked before any file is written, and the launch names that file.
        resolved = resolve_and_enforce_command(
            binding,
            interpreter,
            env,
            label="mlip interpreter",
            write_paths=(work_dir,),
            unresolved_message=_unresolved_interpreter_message(interpreter),
            pinned=context.resolved_command,
        )
        executable = resolved.absolute
        if not RUNNER_PATH.is_file():
            raise DomainError(f"mlip runner source is missing: {RUNNER_PATH}")
        os.makedirs(work_dir, exist_ok=True)
        shutil.copyfile(RUNNER_PATH, os.path.join(work_dir, "mlip_runner.py"))
        job = {
            "model_path": model_path,
            "refs_path": refs_path,
            "task": plan.task,
            "device": device,
            "charge": science.charge,
            "multiplicity": science.multiplicity,
            "mode": plan.mode,
            "optimizer": plan.optimizer,
            "fmax": plan.fmax,
            "max_steps": plan.max_steps,
            "fix_pairs": [
                [i, j] if target is None else [i, j, target]
                for (i, j), target in zip(plan.fix_pairs, plan.fix_targets, strict=True)
            ],
            "pick": plan.pick,
            "workers": workers,
            "threads": threads,
            "structures": [
                {
                    "key": keys[record.id],
                    "label": _label(record),
                    "symbols": list(record.atoms),
                    "coords": [list(map(float, xyz)) for xyz in record.coordinates],
                }
                for record in ordered
            ],
        }
        with open(os.path.join(work_dir, _JOB_NAME), "w", encoding="utf-8") as handle:
            json.dump(job, handle, indent=2)
        walltime = float(binding.walltime_seconds) if binding and binding.walltime_seconds else None
        request = NativeExecutionRequest(
            executable=executable,
            argv=(executable, "mlip_runner.py", _JOB_NAME),
            work_dir=work_dir,
            env=FrozenDict(env),
            walltime_seconds=walltime,
            stdout_file="stdout.log",
            stderr_file="stderr.log",
            metadata=FrozenDict({"mlip_model": plan.model}),
        )
        launched = self._launcher.launch_and_wait(
            context.supervisor,
            request,
            context.poll_interval_seconds,
            should_cancel=should_cancel,
            quota=(work_item.resources, str(context.run_root), work_item.id),
        )
        if launched is None:
            return self._fail(
                work_item,
                context,
                wall_start,
                monotonic_start,
                "mlip process handle was lost before completion",
            )
        execution_result, cancel_outcome = launched
        if cancel_outcome is not None:
            if cancel_outcome.confirmed:
                return self._cancelled(work_item, context, wall_start, monotonic_start)
            return self._fail(
                work_item,
                context,
                wall_start,
                monotonic_start,
                f"cancellation could not be confirmed: {cancel_outcome.detail}",
            )
        if execution_result.timed_out:
            return self._fail(
                work_item,
                context,
                wall_start,
                monotonic_start,
                f"mlip execution exceeded walltime of {walltime:g} seconds",
            )
        backend_error = _backend_error(
            _read_done(Path(work_dir) / _DONE_NAME), execution_result.exit_code
        )
        if backend_error is not None:
            return self._failed(
                work_item,
                context,
                wall_start,
                monotonic_start,
                code=BACKEND_FAILURE_CODE,
                message=f"mlip model backend failed to load: {backend_error}",
                retryable=True,
            )
        if execution_result.exit_code != 0:
            tail = _stderr_tail(work_dir)
            return self._fail(
                work_item,
                context,
                wall_start,
                monotonic_start,
                f"mlip runner exited with code {execution_result.exit_code}"
                + (f":\n{tail}" if tail else ""),
            )
        return self._assemble(
            work_item, context, wall_start, monotonic_start, plan, records, keys, work_dir, science
        )

    def _assemble(
        self,
        work_item: WorkItem,
        context: ItemExecutionContext,
        wall_start: float,
        monotonic_start: float,
        plan: _Plan,
        records: Sequence[StructureRecord],
        keys: Mapping[str, str],
        work_dir: str,
        science: _Science,
    ) -> WorkItemResult:
        """Classify every input, then fail only when all were errors and none was filtered.

        A structure is *dropped by error* (an error row, or a required row missing), *dropped
        by a filter* (bond stretch, or not selected by ``pick``), or *kept*.  Filters are
        legitimate; errors are not.  No kept output with at least one error and no filter is
        a FAILED item; anything else is COMPLETED.
        """
        rows = _read_rows(Path(work_dir) / _RESULTS_NAME)
        done = _read_done(Path(work_dir) / _DONE_NAME)
        stage = "sp" if plan.mode == "sp" else "relax"
        outputs: list[MlipOutput] = []
        error_rows: list[dict[str, str]] = []
        dropped_error: list[str] = []
        dropped_stretch: list[str] = []
        not_selected: list[str] = []
        errored: set[str] = set()
        sp_count = 0
        for record in sorted(records, key=lambda item: item.id):
            key = keys[record.id]
            sp_row = rows.get((key, "sp"))
            if sp_row is not None and not sp_row.get("error"):
                sp_count += 1
            for row_stage in ("sp", "relax"):
                row = rows.get((key, row_stage))
                if row is not None and row.get("error"):
                    error_rows.append(
                        {"id": record.id, "stage": row_stage, "error": str(row["error"])}
                    )
                    errored.add(record.id)
            row = rows.get((key, stage))
            if row is None:
                if plan.mode != "sp_then_relax":
                    dropped_error.append(record.id)
                    error_rows.append({"id": record.id, "stage": stage, "error": "no result row"})
                    errored.add(record.id)
                elif sp_row is None or sp_row.get("error"):
                    errored.add(record.id)
                else:
                    not_selected.append(record.id)
                continue
            if row.get("error"):
                dropped_error.append(record.id)
                errored.add(record.id)
                continue
            try:
                out = self._output(record, stage, row, sp_row, plan)
            except DomainError as exc:
                dropped_error.append(record.id)
                error_rows.append({"id": record.id, "stage": stage, "error": str(exc)})
                errored.add(record.id)
                continue
            if out is None:
                dropped_stretch.append(record.id)
                continue
            outputs.append(out)
        failure = None
        legitimately_filtered = bool(dropped_stretch or not_selected)
        if not outputs and errored and not legitimately_filtered:
            first = error_rows[0] if error_rows else None
            where = (
                f"; first error: {first['id']} {first['stage']}: {first['error']}" if first else ""
            )
            failure = (
                f"all {len(records)} input structures failed with errors and none was "
                f"filtered out; no structure was produced{where}"
            )
        pairs, basis = _calibration_pairs(plan, records, keys, rows, outputs)
        return self._result(
            work_item,
            context,
            wall_start,
            monotonic_start,
            plan,
            keys,
            work_dir,
            outputs,
            sp_count,
            error_rows,
            dropped_error,
            dropped_stretch,
            done,
            calibration(pairs, basis),
            science,
            failure=failure,
        )

    def stored_result_reusable(self, stored_result: WorkItemResult) -> bool:
        """Whether a stored COMPLETED result may be reused (hook consulted by the batch executor).

        A COMPLETED result without structures in which every input was dropped by an error was
        written before an all-errored run became FAILED; it is refused so the item runs again.
        Results whose emptiness comes from a legitimate filter stay reusable.
        """
        if len(stored_result.structures) > 0:
            return True
        summary = next(
            (item for item in stored_result.diagnostics if item.code == "mlip_completed"), None
        )
        if summary is None:
            return True
        inputs = summary.details.get("inputs")
        if not isinstance(inputs, int) or isinstance(inputs, bool) or inputs < 1:
            return True
        rows = summary.details.get("error_rows") or ()
        errored = {
            str(row.get("id"))
            for row in rows
            if isinstance(row, Mapping) and row.get("id") is not None
        }
        return len(errored) < inputs

    def _output(
        self,
        record: StructureRecord,
        stage: str,
        row: Mapping[str, Any],
        sp_row: Mapping[str, Any] | None,
        plan: _Plan,
    ) -> MlipOutput | None:
        """Build one output (None when rejected by reject_bond_stretch)."""
        energy = _finite_energy(row)
        if stage == "sp":
            coords: Sequence[Sequence[float]] = record.coordinates
            max_ratio, pair, changed, fix_dev = None, None, False, 0.0
        else:
            coords = _coords_of(row, len(record.atoms))
            max_ratio, pair, changed = stretch_check(record, coords, plan.fix_pairs)
            fix_dev = fix_deviation(plan.fix_pairs, record.coordinates, coords, plan.fix_targets)
            if (
                plan.reject_stretch is not None
                and max_ratio is not None
                and max_ratio > plan.reject_stretch
            ):
                return None
        meta: dict[str, Any] = {
            "energy_eh": energy,
            "mlip_energy_eh": energy,
            "mlip_model": plan.model,
            "mlip_stage": stage,
            "mlip_optimizer": plan.optimizer if stage == "relax" else None,
            "mlip_converged": row.get("converged") if stage == "relax" else None,
            "mlip_n_steps": row.get("n_steps"),
            "mlip_n_evals": row.get("n_evals"),
            "mlip_wall_s": row.get("wall_s"),
            "mlip_fix_max_dev": fix_dev,
            "mlip_input_energy_eh": _input_energy(record),
            "mlip_sp_energy_eh": (
                _finite_energy(sp_row) if sp_row and not sp_row.get("error") else None
            ),
            "mlip_max_bond_stretch": max_ratio,
            "mlip_stretched_bond": None if pair is None else f"{pair[0] + 1}-{pair[1] + 1}",
            "mlip_graph_changed": changed,
        }
        return MlipOutput(record, stage, energy, coords, meta, row, max_ratio, pair, changed)

    def _result(
        self,
        work_item: WorkItem,
        context: ItemExecutionContext,
        wall_start: float,
        monotonic_start: float,
        plan: _Plan,
        keys: Mapping[str, str],
        work_dir: str,
        outputs: list[MlipOutput],
        sp_count: int,
        error_rows: list[dict[str, str]],
        dropped_error: list[str],
        dropped_stretch: list[str],
        done: Mapping[str, Any],
        calibration_info: dict[str, Any] | None,
        science: _Science,
        failure: str | None = None,
    ) -> WorkItemResult:
        """Build structures, energies, artifacts and diagnostics for the kept outputs.

        With *failure* set (every input errored, none filtered) the result is FAILED and carries
        the summary diagnostics and no structures, energies or artifacts.
        """
        ordered = sorted(outputs, key=lambda out: out.record.id)
        provenance = _provenance(plan, work_item, done)
        structures: list[StructureRecord] = []
        energies: list[ScientificResult] = []
        for out in ordered:
            record = out.record
            parents, root, group = endpoint_lineage(record)
            out_id = f"{record.id}:mlip-{out.stage}"
            coords = tuple((float(x), float(y), float(z)) for x, y, z in out.coords)
            # The intended graph is resolved once on the input geometry, as the standard
            # profile does; post-relax bond changes stay in the mlip_graph_changed diagnostic.
            topology = resolve_and_persist_kwargs(record, record.coordinates)
            structures.append(
                StructureRecord(
                    id=out_id,
                    atoms=tuple(record.atoms),
                    coordinates=coords,
                    charge=science.charge,
                    multiplicity=science.multiplicity,
                    parent_ids=parents,
                    lineage_root_id=root,
                    source_step_id=work_item.step_id,
                    source_work_item_id=work_item.id,
                    role=record.role,
                    ordinal=None,
                    group_key=group,
                    metadata=FrozenDict({**_inherited_metadata(record), **out.meta}),
                    **topology,
                )
            )
            energies.append(
                ScientificResult(
                    kind="energy",
                    value=out.energy,
                    unit=Unit.HARTREE,
                    subject_structure_id=out_id,
                    source_step_id=work_item.step_id,
                    source_work_item_id=work_item.id,
                    provenance=provenance,
                    result_id=make_result_id(
                        step_id=work_item.step_id,
                        work_item_id=None,
                        kind="energy",
                        subject_structure_id=out_id,
                        producer_digest=work_item.semantic_digest,
                    ),
                )
            )
        if failure is None:
            xyz_path = Path(work_dir) / _XYZ_NAME
            xyz_path.write_text(xyz_text(ordered, keys), encoding="utf-8")
            artifacts = self._artifacts(work_item, context, work_dir)
        else:
            artifacts = ArtifactSet.of()
        relaxed = [out for out in ordered if out.stage == "relax"]
        not_converged = [out.record.id for out in relaxed if out.row.get("converged") is False]
        stretched = [
            out.record.id
            for out in ordered
            if out.max_ratio is not None and out.max_ratio > STRETCH_WARN_RATIO
        ]
        changed = [out.record.id for out in ordered if out.changed]
        evals = [
            float(out.row["n_evals"])
            for out in relaxed
            if isinstance(out.row.get("n_evals"), (int, float))
        ]
        walls = [
            float(out.row["wall_s"])
            for out in relaxed
            if isinstance(out.row.get("wall_s"), (int, float))
        ]
        input_count = len(keys)
        details: dict[str, Any] = {
            "inputs": input_count,
            "mode": plan.mode,
            "model": plan.model,
            "optimizer": plan.optimizer,
            "model_sha256": done.get("model_sha256"),
            "sp_count": sp_count,
            "relaxed_count": len(relaxed),
            "not_converged": len(not_converged),
            "dropped_error": len(dropped_error),
            "dropped_stretch": len(dropped_stretch),
            "error_rows": error_rows,
            "wall_s": done.get("wall_s"),
            "mean_n_evals": mean(evals),
            "mean_wall_s": mean(walls),
        }
        message = (
            f"mlip {plan.mode} ({plan.model}, {plan.optimizer}) kept {len(ordered)} of {input_count}: "
            f"sp {sp_count}, relaxed {len(relaxed)}, not converged {len(not_converged)}, "
            f"dropped error {len(dropped_error)}, dropped stretch {len(dropped_stretch)}"
        )
        diagnostics = [
            _diagnostic(
                "mlip_completed", message, work_item, context, DiagnosticSeverity.INFO, details
            )
        ]
        if not_converged:
            diagnostics.append(
                _diagnostic(
                    "mlip_not_converged",
                    f"{len(not_converged)} structures did not converge: {_short(not_converged)}",
                    work_item,
                    context,
                    DiagnosticSeverity.WARNING,
                    {"structures": not_converged},
                )
            )
        if stretched:
            diagnostics.append(
                _diagnostic(
                    "mlip_bond_stretched",
                    f"{len(stretched)} structures stretch a bond above {STRETCH_WARN_RATIO}x: {_short(stretched)}",
                    work_item,
                    context,
                    DiagnosticSeverity.WARNING,
                    {"structures": stretched},
                )
            )
        if changed:
            diagnostics.append(
                _diagnostic(
                    "mlip_graph_changed",
                    f"{len(changed)} structures changed bonding graph: {_short(changed)}",
                    work_item,
                    context,
                    DiagnosticSeverity.WARNING,
                    {"structures": changed},
                )
            )
        if dropped_error or dropped_stretch:
            diagnostics.append(
                _diagnostic(
                    "mlip_dropped",
                    f"dropped {len(dropped_error)} with errors and {len(dropped_stretch)} for bond stretch",
                    work_item,
                    context,
                    DiagnosticSeverity.WARNING,
                    {"error": dropped_error, "stretch": dropped_stretch},
                )
            )
        if calibration_info is not None:
            spearman = calibration_info["spearman"]
            text = "n/a" if spearman is None else f"{spearman:.3f}"
            diagnostics.append(
                _diagnostic(
                    "mlip_calibration",
                    f"GFN2 vs MLIP over {calibration_info['n']} structures ({calibration_info['basis']}): "
                    f"Spearman {text}; input-lowest {calibration_info['input_lowest']} is "
                    f"{calibration_info['kcal_above_mlip_lowest']:.2f} kcal/mol above the MLIP lowest",
                    work_item,
                    context,
                    DiagnosticSeverity.INFO,
                    calibration_info,
                )
            )
        diagnostics.extend(science.diagnostics)
        if failure is not None:
            diagnostics.append(
                _diagnostic(
                    ALL_ERRORED_CODE,
                    failure,
                    work_item,
                    context,
                    DiagnosticSeverity.ERROR,
                    {"error_rows": error_rows, "inputs": input_count},
                )
            )
            return WorkItemResult(
                work_item_id=work_item.id,
                status=WorkItemStatus.FAILED,
                diagnostics=tuple(diagnostics),
                timing=self._timing(wall_start, monotonic_start),
                error=ResultError(
                    code=ALL_ERRORED_CODE,
                    message=failure,
                    retryable=True,
                    details=FrozenDict({"inputs": input_count, "error_rows": error_rows}),
                ),
                recovery=RecoveryInfo(profile="none", attempted=False),
                semantic_digest=work_item.semantic_digest,
            )
        return WorkItemResult(
            work_item_id=work_item.id,
            status=WorkItemStatus.COMPLETED,
            structures=StructureSet(tuple(structures)),
            results=ResultSet(tuple(energies)),
            artifacts=artifacts,
            diagnostics=tuple(diagnostics),
            timing=self._timing(wall_start, monotonic_start),
            recovery=RecoveryInfo(profile="none", attempted=False),
            semantic_digest=work_item.semantic_digest,
        )

    def _artifacts(
        self, work_item: WorkItem, context: ItemExecutionContext, work_dir: str
    ) -> ArtifactSet:
        """Checksummed run-relative artifacts for every file the attempt produced."""
        run_root = os.path.realpath(str(context.run_root))
        token = hashlib.blake2b(work_item.id.encode("utf-8"), digest_size=8).hexdigest()
        refs: list[ArtifactRef] = []
        for role, name in _ARTIFACT_FILES:
            path = Path(work_dir) / name
            if not path.is_file():
                continue
            relative = os.path.relpath(os.path.realpath(path), run_root).replace(os.sep, "/")
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            refs.append(
                ArtifactRef(
                    id=f"mlip-{work_item.step_id}-{role}-{token}",
                    role=role,
                    locator=ArtifactLocator.run_relative(relative),
                    checksum="sha256:" + digest,
                    subject_structure_id=None,
                    producer_step_id=work_item.step_id,
                    producer_work_item_id=work_item.id,
                )
            )
        return ArtifactSet.of(*refs)

    @staticmethod
    def _timing(wall_start: float, monotonic_start: float) -> Timing:
        return Timing(
            started_at=wall_start,
            finished_at=max(time.time(), wall_start),
            duration_seconds=max(0.0, time.monotonic() - monotonic_start),
        )

    def _fail(
        self,
        work_item: WorkItem,
        context: ItemExecutionContext,
        wall_start: float,
        monotonic_start: float,
        message: str,
    ) -> WorkItemResult:
        return pure_fail_result(
            work_item,
            step_id=context.step_id,
            wall_start=wall_start,
            monotonic_start=monotonic_start,
            message=message,
        )

    def _failed(
        self,
        work_item: WorkItem,
        context: ItemExecutionContext,
        wall_start: float,
        monotonic_start: float,
        *,
        code: str,
        message: str,
        retryable: bool,
    ) -> WorkItemResult:
        """FAILED result with a structured, retryable-aware error and a matching diagnostic."""
        return WorkItemResult(
            work_item_id=work_item.id,
            status=WorkItemStatus.FAILED,
            diagnostics=(
                _diagnostic(
                    code,
                    message,
                    work_item,
                    context,
                    DiagnosticSeverity.ERROR,
                    {"retryable": retryable},
                ),
            ),
            timing=self._timing(wall_start, monotonic_start),
            error=ResultError(code=code, message=message, retryable=retryable),
            recovery=RecoveryInfo(profile="none", attempted=False),
            semantic_digest=work_item.semantic_digest,
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
