#!/usr/bin/env python3

"""ConfGen search mode: DG search as a mode of the confgen step.

Native scope carries ``search`` instead of rings/torsions/paths. Graph,
coordination and shape come from ``build_context``; one supervised worker
runs restrained xTB; passing structures import as plain members.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sys
import time
from collections.abc import Callable, Mapping
from typing import Any

from confflow.science.confgen.coordination.stage import resolve_axis_spec
from confflow.science.confgen.model import build_context
from confflow.science.topology import should_persist_working_graph

from ..domain._immutable import FrozenDict, thaw_value
from ..domain.artifact import ArtifactLocator, ArtifactRef, ArtifactSet
from ..domain.completion import WorkItemStatus
from ..domain.diagnostics import Diagnostic, DiagnosticSeverity
from ..domain.errors import DomainError
from ..domain.result import ResultSet
from ..domain.structure import StructureRecord, StructureSet
from ..domain.work_item import RecoveryInfo, Timing, WorkItem, WorkItemResult
from .binding_resolution import effective_native_env
from .confgen_executor import ConfgenExecutor
from .native import NativeExecutionRequest
from .output_identity import CONFORMER_MEMBER_METADATA_KEY, conformer_output_id, endpoint_lineage
from .quota import QuotaCancelled, QuotaError
from .work_item_executor import ItemExecutionContext, WorkItemExecutor
from .xyz_import import import_xyz

__all__ = ["run_search_item"]

_HARTREE_TO_KCAL = 627.5094740631


class _ImportError(DomainError):
    pass


def _histogram(rows: Any) -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in rows if isinstance(rows, list) else []:
        if not isinstance(row, Mapping):
            continue
        for check in row.get("failed_checks", []) or []:
            counts[str(check)] = counts.get(str(check), 0) + 1
    return dict(sorted(counts.items()))


def _read_validated(
    driving: StructureRecord, rundir: str | os.PathLike[str]
) -> tuple[dict[str, Any], list[Any], list[Any], StructureSet]:
    try:
        payload = json.loads(open(os.path.join(rundir, "summary.json"), encoding="utf-8").read())
    except (OSError, ValueError) as exc:
        raise _ImportError(f"confgen search summary is unreadable: {exc}") from exc
    if not isinstance(payload, dict):
        raise _ImportError("confgen search summary must hold a JSON object")
    totals, rows = payload.get("totals"), payload.get("structures")
    if not isinstance(totals, dict) or not isinstance(rows, list):
        raise _ImportError("confgen search summary lacks totals/structures")
    passing = [r for r in rows if isinstance(r, dict) and r.get("passed") is True]
    if totals.get("passed") != len(passing):
        raise _ImportError("confgen search summary totals disagree with passing records")
    try:
        xyz_text = open(os.path.join(rundir, "structures.xyz"), encoding="utf-8").read()
    except OSError as exc:
        raise _ImportError(f"confgen search structures are unreadable: {exc}") from exc
    try:
        frames = import_xyz(xyz_text, source_name="confgen-search")
    except DomainError as exc:
        raise _ImportError(f"confgen search structures are invalid: {exc}") from exc
    if len(frames) != len(passing):
        raise _ImportError("confgen search frame count disagrees with passed totals")
    if any(tuple(r.atoms) != tuple(driving.atoms) for r in frames):
        raise _ImportError("confgen search frame breaks the atomic invariant")
    tags = re.findall(r"target=(\S+) start=(\d+) energy_eh=(\S+)", xyz_text)
    if len(tags) != len(frames):
        raise _ImportError("confgen search xyz comments are malformed")
    try:
        framed = [(str(target), int(start), float(energy)) for target, start, energy in tags]
    except (TypeError, ValueError) as exc:
        raise _ImportError("confgen search xyz comments are malformed") from exc
    keyed: dict[tuple[str, int], Any] = {}
    for row in passing:
        target, start = row.get("target"), row.get("start")
        if not isinstance(target, str) or isinstance(start, bool) or not isinstance(start, int):
            raise _ImportError("confgen search summary records are malformed")
        if (target, start) in keyed:
            raise _ImportError("confgen search summary holds duplicate passing records")
        keyed[(target, start)] = row
    if sorted(keyed) != sorted((target, start) for target, start, _ in framed):
        raise _ImportError("confgen search xyz comments disagree with passing records")
    ordered: list[Any] = []
    for target, start, energy in framed:
        record = keyed[(target, start)]
        try:
            want = float(record.get("energy_eh") or 0.0)
        except (TypeError, ValueError) as exc:
            raise _ImportError("confgen search summary energies are malformed") from exc
        if round(energy, 6) != round(want, 6):
            raise _ImportError("confgen search frame energies disagree with passing records")
        ordered.append(record)
    return totals, rows, ordered, frames


def run_search_item(
    executor: ConfgenExecutor,
    work_item: WorkItem,
    context: ItemExecutionContext,
    wall_start: float,
    monotonic_start: float,
    should_cancel: Callable[[], bool] | None = None,
) -> WorkItemResult:
    """Run one confgen work item in DG-search mode (never raises)."""
    try:
        return _run(executor, work_item, context, wall_start, monotonic_start, should_cancel)
    except QuotaCancelled:
        return executor._cancelled(work_item, context, wall_start, monotonic_start)
    except (DomainError, ValueError) as exc:
        return executor._fail(work_item, context, wall_start, monotonic_start, str(exc))
    except Exception as exc:
        msg = f"confgen internal failure: {exc}"
        return executor._fail(work_item, context, wall_start, monotonic_start, msg)


def _run(
    executor: ConfgenExecutor,
    work_item: WorkItem,
    context: ItemExecutionContext,
    wall_start: float,
    monotonic_start: float,
    should_cancel: Callable[[], bool] | None,
) -> WorkItemResult:
    scientific = context.scientific
    native = dict(scientific.native)
    section = native.get("search")
    if not isinstance(section, Mapping):
        raise DomainError("confgen search scope is missing its search section")
    seed, raw_seed = scientific.seed, native.get("seed")
    if seed is not None and raw_seed is not None and int(raw_seed) != int(seed):
        raise DomainError(
            "confgen v3 seed conflict: step seed "
            f"{seed!r} disagrees with native seed {raw_seed!r}; "
            "the top-level step seed is the sole authority"
        )
    if seed is None:
        seed = raw_seed
    if seed is None:
        raise DomainError(
            "confgen v3 search requires an explicit top-level seed "
            "(the seed is the sole stochastic authority)"
        )
    if isinstance(seed, bool) or not isinstance(seed, int):
        raise DomainError(f"confgen v3 seed must be an integer or null, got {seed!r}")
    executor._reject_freeze(work_item, context)
    driving = executor._driving(work_item)
    input_key, upstream = executor._chained_input_state(work_item, driving)
    spec = {k: v for k, v in native.items() if k != "search"}
    spec["seed"] = int(seed)
    scope = upstream.get("inherited_scope")
    try:
        science_context = build_context(driving, spec, input_key, inherited_scope=scope)
    except ValueError as exc:
        raise DomainError(f"confgen v3 spec rejected: {exc}") from exc
    coord_section = science_context.resolved_spec.get("coordination")
    coord, shape = None, None
    if isinstance(coord_section, Mapping):
        coord = resolve_axis_spec(dict(coord_section))
        if len(coord.shapes) != 1:
            raise DomainError(
                "confgen search requires exactly one coordination shape "
                f"(resolved {list(coord.shapes)!r}); declare a single shape"
            )
        shape = coord.shapes[0]
    base, natoms = int(native.get("index_base", 1)), len(driving.atoms)
    raw_charges = section.get("fragment_charges", [])
    frag = tuple((int(e["atom"]) - base, int(e["charge"])) for e in raw_charges)
    for atom, _ in frag:
        if atom < 0 or atom >= natoms:
            raise DomainError(
                f"confgen search fragment charge atom {atom + base} is out of range for {natoms} atoms"
            )
    charge, multiplicity = driving.charge, driving.multiplicity
    overrides = scientific.overrides
    if overrides.get("charge") is not None:
        charge = int(overrides["charge"])
    if overrides.get("multiplicity") is not None:
        multiplicity = int(overrides["multiplicity"])
    if charge is None or multiplicity is None:
        raise DomainError("confgen search requires a resolved charge and multiplicity")
    binding = context.execution_binding
    xtb = binding.executable if binding is not None else None
    if not xtb:
        raise DomainError(
            "confgen search requires an xTB executable: set step execution.executable "
            "or provide the request xtb executable"
        )
    cores = work_item.resources.cores_per_item
    if cores is None or context.supervisor is None or not context.run_root:
        raise DomainError(
            "confgen search requires resolved cores, a process supervisor, "
            "and a run root for item directories"
        )
    attempt = context.attempt_dir(work_item)
    os.makedirs(attempt, exist_ok=True)
    rundir = os.path.join(attempt, "search_run")
    os.makedirs(rundir, exist_ok=True)
    job_path = os.path.join(attempt, "search_job.json")
    job_doc = {
        "atoms": list(driving.atoms),
        "reference": [[float(x), float(y), float(z)] for x, y, z in driving.coordinates],
        "graph": science_context.graph.to_mapping(convention="internal0"),
        "coordination": thaw_value(coord_section) if isinstance(coord_section, Mapping) else None,
        "shape": shape,
        "charge": int(charge),
        "uhf": int(multiplicity) - 1,
        "settings": {**section, "seed": int(seed), "fragment_charges": [[a, q] for a, q in frag]},
        "max_cycles": int(section.get("max_cycles", 1000)),
        "xtb": xtb,
        "cores": cores,
        "workdir": os.path.join(rundir, "starts"),
        "structures_file": os.path.join(rundir, "structures.xyz"),
        "summary_file": os.path.join(rundir, "summary.json"),
    }
    with open(job_path, "w", encoding="utf-8") as handle:
        handle.write(json.dumps(job_doc, sort_keys=True, indent=2) + "\n")
    walltime: float | None = binding.walltime_seconds if binding is not None else None
    walltime = float(walltime) if walltime else None
    request = NativeExecutionRequest(
        sys.executable,
        (sys.executable, "-m", "confflow.execution.confgen_search_worker", job_path),
        rundir,
        FrozenDict(effective_native_env(binding, inherit=os.environ)),
        walltime,
        "stdout.log",
        "stderr.log",
    )
    try:
        launched = WorkItemExecutor().launch_and_wait(
            context.supervisor,
            request,
            context.poll_interval_seconds,
            should_cancel=should_cancel,
            quota=(work_item.resources, str(context.run_root), work_item.id),
        )
    except QuotaCancelled:
        raise
    except QuotaError as exc:
        raise DomainError(f"confgen search quota refused: {exc}") from exc

    def _result(
        status: WorkItemStatus,
        structures: StructureSet,
        diagnostics: tuple[Diagnostic, ...],
        artifacts: ArtifactSet,
    ) -> WorkItemResult:
        return WorkItemResult(
            work_item.id,
            status,
            structures,
            ResultSet(),
            artifacts,
            diagnostics,
            Timing(
                started_at=wall_start,
                finished_at=max(time.time(), wall_start),
                duration_seconds=max(0.0, time.monotonic() - monotonic_start),
            ),
            None,
            RecoveryInfo(profile="none", attempted=False),
            work_item.semantic_digest,
        )

    def _failed(
        msg: str, code: str, artifacts: ArtifactSet, details: dict[str, Any] | None = None
    ) -> WorkItemResult:
        diagnostic = Diagnostic(
            code,
            msg,
            DiagnosticSeverity.ERROR,
            context.step_id,
            work_item.id,
            work_item.logical_key,
            None,
            FrozenDict(dict(details or {})),
        )
        return _result(WorkItemStatus.FAILED, StructureSet(), (diagnostic,), artifacts)

    def _artifacts() -> ArtifactSet:
        run_root = os.path.realpath(context.run_root)
        refs: list[ArtifactRef] = []
        roles = (
            ("summary.json", "search_summary"),
            ("structures.xyz", "search_structures"),
            ("stdout.log", "search_log"),
            ("stderr.log", "search_log"),
        )
        for name, role in roles:
            path = os.path.join(rundir, name)
            if not os.path.isfile(path):
                continue
            with open(path, "rb") as handle:
                checksum = "sha256:" + hashlib.sha256(handle.read()).hexdigest()
            relative = os.path.relpath(os.path.realpath(path), run_root).replace(os.sep, "/")
            metadata = {"seed": int(seed)}
            if upstream.get("result_id") is not None:
                metadata["input_confgen_state"] = upstream["result_id"]
            refs.append(
                ArtifactRef(
                    id=f"{work_item.id}/{name}",
                    role=role,
                    locator=ArtifactLocator.run_relative(relative),
                    checksum=checksum,
                    producer_step_id=work_item.step_id,
                    producer_work_item_id=work_item.id,
                    subject_structure_id=driving.id,
                    metadata=FrozenDict(metadata),
                )
            )
        return ArtifactSet(tuple(refs))

    def _completed(artifacts: ArtifactSet, charge: int, multiplicity: int) -> WorkItemResult:
        try:
            totals, rows, ordered, frames = _read_validated(driving, rundir)
        except _ImportError as exc:
            return _failed(str(exc), "confgen_search_failed", artifacts)
        parent_ids, lineage_root, group_key = endpoint_lineage(driving)
        persist = should_persist_working_graph(driving)
        inherit_patch = driving.topology_patch if persist else None
        inherit_graph = (
            tuple(tuple(int(v) for v in row) for row in science_context.adjacency)
            if persist
            else None
        )
        low = min(float(r.get("energy_eh") or 0.0) for r in ordered)
        members: list[StructureRecord] = []
        for rank, rec in enumerate(ordered):
            energy = rec.get("energy_eh")
            coords = tuple((float(x), float(y), float(z)) for x, y, z in frames[rank].coordinates)
            metadata = {
                CONFORMER_MEMBER_METADATA_KEY: rank,
                "seed": int(seed),
                "energy_eh": energy,
                "rel_kcal": (float(energy or 0.0) - low) * _HARTREE_TO_KCAL,
                "search_target": rec.get("target"),
                "search_start": rec.get("start"),
            }
            if upstream.get("result_id") is not None:
                metadata["input_confgen_state"] = upstream["result_id"]
            members.append(
                StructureRecord(
                    conformer_output_id(work_item.logical_key, rank),
                    tuple(driving.atoms),
                    coords,
                    charge,
                    multiplicity,
                    parent_ids,
                    lineage_root,
                    work_item.step_id,
                    work_item.id,
                    "conformer",
                    rank,
                    group_key,
                    FrozenDict(metadata),
                    inherit_patch,
                    inherit_graph,
                )
            )
        counts = _histogram(rows)
        diagnostic = Diagnostic(
            "confgen_completed",
            f"confgen search published {len(members)} conformers "
            f"({totals.get('generated')} generated, {totals.get('relaxed')} relaxed, "
            f"{totals.get('passed')} passed across {totals.get('targets')} targets; "
            f"failed checks: {', '.join(f'{k}={v}' for k, v in counts.items()) or 'none'})",
            DiagnosticSeverity.INFO,
            context.step_id,
            work_item.id,
            work_item.logical_key,
            None,
            FrozenDict(
                {
                    "targets": totals.get("targets"),
                    "generated": totals.get("generated"),
                    "relaxed": totals.get("relaxed"),
                    "passed": totals.get("passed"),
                    "failed_checks": counts,
                }
            ),
        )
        structures = StructureSet(tuple(members))
        return _result(WorkItemStatus.COMPLETED, structures, (diagnostic,), artifacts)

    if launched is None:
        if should_cancel is not None and should_cancel():
            return executor._cancelled(work_item, context, wall_start, monotonic_start)
        raise DomainError("confgen search process handle was lost before completion")
    execution_result, cancel_outcome = launched
    if cancel_outcome is not None:
        if cancel_outcome.confirmed:
            return executor._cancelled(work_item, context, wall_start, monotonic_start)
        return _failed(
            f"cancellation could not be confirmed: {cancel_outcome.detail}",
            "cancellation_error",
            _artifacts(),
        )
    if execution_result.timed_out or execution_result.exit_code is None:
        return _failed(
            "confgen search worker did not exit cleanly",
            "confgen_search_failed",
            _artifacts(),
            {"timed_out": bool(execution_result.timed_out)},
        )
    code = execution_result.exit_code
    artifacts = _artifacts()
    if code == 0:
        return _completed(artifacts, int(charge), int(multiplicity))
    if code == 2:
        payload = json.loads(open(os.path.join(rundir, "summary.json"), encoding="utf-8").read())
        rows, totals = payload.get("structures", []), payload.get("totals", {})
        counts = _histogram(rows)
        generated = totals.get("generated", "?") if isinstance(totals, dict) else "?"
        return _failed(
            f"confgen search relaxed {generated} starts but none passed all audit checks "
            f"(failed checks: {', '.join(f'{k}={v}' for k, v in counts.items()) or 'none'})",
            "confgen_no_passing_structures",
            artifacts,
            {"failed_checks": counts, "generated": generated},
        )
    try:
        tail = open(os.path.join(rundir, "stderr.log"), encoding="utf-8").read()
    except OSError:
        tail = ""
    tail = "\n".join(tail.splitlines()[-20:])[-4000:]
    return _failed(
        f"confgen search worker failed with exit code {code}: {tail}",
        "confgen_search_failed",
        artifacts,
    )
