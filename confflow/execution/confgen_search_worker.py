#!/usr/bin/env python3

"""ConfGen DG-search worker (<job.json> runner): DG starts, restrained relaxation, audit.

Builds the energy engine for the job (GFN2-xTB, ``confgen_xtb``) and hands it to the search
as a backend-neutral ``Calculator``; graph from the job.
Exit 0 passing / 2 none pass / 3 bad job; SIGTERM stops launches, exits non-zero.
"""

from __future__ import annotations

import concurrent.futures as futures
import contextlib
import json
import multiprocessing
import os
import signal
import sys
import threading
from pathlib import Path
from typing import Any, Literal

import numpy as np

import confflow.science.confgen.search as search
from confflow.execution.confgen_xtb import (
    Cancelled,
    XtbCalculator,
    resolve_xtb,
    write_xyz_frames,
    xtb_version,
)
from confflow.science.confgen.explore import ExploreSettings, ParallelMap, explore
from confflow.science.confgen.graph import TypedGraph
from confflow.science.confgen.search_spec import resolve_search_coordination

HARTREE_TO_KCAL = 627.5094740631

_STOP = threading.Event()
_ACTIVE: list[XtbCalculator] = []


class _JobError(ValueError):
    pass


def _handle_sigterm(signum: int, frame: Any) -> None:
    _STOP.set()
    for calc in list(_ACTIVE):
        calc.cancel()


def _int(value: Any, name: str, minimum: int | None = None) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise _JobError(f"job field {name!r} must be an integer, got {value!r}")
    if minimum is not None and value < minimum:
        raise _JobError(f"job field {name!r} must be >= {minimum}, got {value!r}")
    return value


def _text(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value:
        raise _JobError(f"job field {name!r} must be a non-empty string")
    return value


def _load_job(path: str) -> dict[str, Any]:
    try:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise _JobError(f"cannot read job file {path!r}: {exc}") from exc
    if not isinstance(raw, dict):
        raise _JobError(f"job file {path!r} must hold a JSON object")
    els = raw.get("atoms")
    if not isinstance(els, list) or not els or any(not isinstance(e, str) for e in els):
        raise _JobError("job field 'atoms' must be a non-empty list of element symbols")
    try:
        ref = np.asarray(raw.get("reference"), dtype=float)
    except (ValueError, TypeError) as exc:
        raise _JobError(f"job field 'reference' must be an Nx3 float array: {exc}") from exc
    if ref.shape != (len(els), 3) or not bool(np.all(np.isfinite(ref))):
        raise _JobError("job field 'reference' must hold one finite triple per atom")
    if not isinstance(raw.get("graph"), dict):
        raise _JobError("job field 'graph' must hold a typed-graph mapping")
    try:
        graph = TypedGraph.from_mapping(
            raw["graph"], els, convention="internal0", source="confgen-search-job"
        )
    except (ValueError, KeyError, TypeError) as exc:
        raise _JobError(f"job graph is malformed: {exc}") from exc
    coord_raw, spec, shape = raw.get("coordination"), None, raw.get("shape")
    if coord_raw is not None:
        if not isinstance(coord_raw, dict):
            raise _JobError("job field 'coordination' must be a mapping or null")
        try:
            spec = resolve_search_coordination(coord_raw)
        except ValueError as exc:
            raise _JobError(f"job coordination is malformed: {exc}") from exc
        if not isinstance(shape, str) or not shape:
            raise _JobError("job coordination needs a non-empty shape name")
    elif shape is not None:
        raise _JobError("job carries a shape without a coordination section")
    section = raw.get("settings")
    if not isinstance(section, dict):
        raise _JobError("job field 'settings' must hold a mapping")
    mode = section.get("small_ring_torsions")
    if mode not in ("both", "on", "off"):
        raise _JobError(f"job field 'settings.small_ring_torsions' is invalid: {mode!r}")
    typed_mode: Literal["both", "on", "off"] = mode
    scale = section.get("bond_scale")
    if isinstance(scale, bool) or not isinstance(scale, (int, float)):
        raise _JobError(f"job field 'settings.bond_scale' must be a number, got {scale!r}")
    if not np.isfinite(scale) or float(scale) <= 0.0:
        raise _JobError(f"job field 'settings.bond_scale' must be positive, got {scale!r}")
    frag_raw = section.get("fragment_charges", [])
    if not isinstance(frag_raw, list):
        raise _JobError("job field 'settings.fragment_charges' must be a list")
    frag: list[tuple[int, int]] = []
    for entry in frag_raw:
        if not isinstance(entry, (list, tuple)) or len(entry) != 2:
            raise _JobError(f"job fragment charge {entry!r} must be [ATOM, Q] in range")
        atom = _int(entry[0], "settings.fragment_charges atom", 0)
        if atom >= len(els):
            raise _JobError(f"job fragment charge atom {atom!r} is out of range")
        frag.append((atom, _int(entry[1], "settings.fragment_charges charge")))
    freeze_raw = section.get("freeze_core", [])
    if not isinstance(freeze_raw, list):
        raise _JobError("job field 'settings.freeze_core' must be a list")
    freeze_core: list[int] = []
    for value in freeze_raw:
        atom = _int(value, "settings.freeze_core atom", 0)
        if atom >= len(els):
            raise _JobError(f"job freeze_core atom {atom!r} is out of range")
        if atom in freeze_core:
            raise _JobError(f"job freeze_core repeats atom {atom!r}")
        freeze_core.append(atom)
    fixed_raw = section.get("fixed_states", [])
    if not isinstance(fixed_raw, list):
        raise _JobError("job field 'settings.fixed_states' must be a list")
    fixed_states: list[tuple[int, int, int, int]] = []
    for quad in fixed_raw:
        if not isinstance(quad, list) or len(quad) != 4:
            raise _JobError(f"job fixed state {quad!r} must hold 4 atom indices")
        a, b, c, d = (_int(value, "settings.fixed_states atom", 0) for value in quad)
        for atom in (a, b, c, d):
            if atom >= len(els):
                raise _JobError(f"job fixed_states atom {atom!r} is out of range")
        if len({a, b, c, d}) != 4:
            raise _JobError(f"job fixed state {quad!r} repeats an atom")
        if (a, b, c, d) in fixed_states or (d, c, b, a) in fixed_states:
            raise _JobError(f"job fixed state {quad!r} is duplicated")
        fixed_states.append((a, b, c, d))
    starts = section.get("starts")
    window = section.get("window_kcal")
    if isinstance(window, bool) or not isinstance(window, (int, float)) or not window > 0:
        raise _JobError(f"job field 'settings.window_kcal' must be positive, got {window!r}")
    arrangement = section.get("arrangement")
    if arrangement not in ("keep", "enumerate", "search"):
        raise _JobError(f"job field 'settings.arrangement' is invalid: {arrangement!r}")
    typed_arr: Literal["keep", "enumerate", "search"] = arrangement
    release = section.get("release")
    if not isinstance(release, bool):
        raise _JobError(f"job field 'settings.release' must be a boolean, got {release!r}")
    cores = _int(raw.get("cores"), "cores", 1)
    adaptive = section.get("adaptive", False)
    if not isinstance(adaptive, bool):
        raise _JobError(f"job field 'settings.adaptive' must be a boolean, got {adaptive!r}")
    budget = _int(section.get("budget"), "settings.budget", 1)
    cap = _int(section.get("budget_cap", budget), "settings.budget_cap", 1) if adaptive else budget
    time_limit = raw.get("time_limit_seconds")
    if time_limit is not None and (
        isinstance(time_limit, bool)
        or not isinstance(time_limit, (int, float))
        or not np.isfinite(time_limit)
        or time_limit <= 0
    ):
        raise _JobError("job field 'time_limit_seconds' must be a finite positive number")
    ez_policy = section.get("ez_policy", "preserve")
    if ez_policy != "preserve":
        raise _JobError(
            "settings.ez_policy supports 'preserve' only; E/Z enumeration is not implemented"
        )
    settings = ExploreSettings(
        budget=max(cap, budget),
        adaptive=adaptive,
        min_budget=budget,
        window_kcal=float(window),
        seed=_int(section.get("seed"), "settings.seed"),
        starts=None if starts is None else _int(starts, "settings.starts", 1),
        arrangement=typed_arr,
        batch=8 * cores,
        # one relaxation per core per backend call: a fixed 4 left most of a many-core job idle
        relaxation_batch=max(4, cores),
        release=release,
        small_ring_torsions=typed_mode,
        embed_timeout=_int(
            section.get("embed_timeout_seconds"), "settings.embed_timeout_seconds", 0
        ),
        fragment_charges=tuple(frag),
        freeze_core=tuple(freeze_core),
        fixed_states=tuple(fixed_states),
        ez_policy=ez_policy,
        bond_scale=float(scale),
        time_limit_seconds=None if time_limit is None else float(time_limit),
    )
    job = dict(raw)
    job["reference"] = ref
    job["graph"] = graph
    job["graph_doc"] = raw["graph"]
    job["spec"] = spec
    job["settings"] = settings
    job["settings_doc"] = section
    job["charge"] = _int(raw.get("charge"), "charge")
    job["uhf"] = _int(raw.get("uhf"), "uhf")
    job["max_cycles"] = _int(raw.get("max_cycles"), "max_cycles", 1)
    job["cores"] = cores
    for key in ("xtb", "workdir", "structures_file", "summary_file"):
        job[key] = _text(raw.get(key), key)
    return job


def _comment(r: search.SearchRecord, low: float) -> str:
    energy = float(r.energy_eh or 0.0)
    text = f"target={r.target} start={r.start} energy_eh={energy:.6f} rel_kcal={(energy - low) * HARTREE_TO_KCAL:.3f}"
    if r.actual_target is not None:
        text = f"{text} actual={r.actual_target}"
    arrangement = getattr(r, "arrangement", None)
    return text if arrangement is None else f"{text} arrangement={arrangement}"


def _write_json(path: Path, document: dict[str, Any]) -> None:
    """Atomically replace one coherent JSON artifact."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    try:
        with temporary.open("w", encoding="utf-8") as handle:
            json.dump(document, handle, sort_keys=True, allow_nan=False)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def _write_checkpoint(path: Path, run: search.SearchRun, atoms: list[str]) -> None:
    """Atomically publish audited search minima, separately from final released output."""
    _write_json(
        path,
        {
            "schema_version": 1,
            "kind": "confgen_search_checkpoint",
            "final": False,
            "atoms": atoms,
            **run.summary(),
            "coordinates": [
                r.coords.tolist() if r.coords is not None else None for r in run.records
            ],
        },
    )


def _write_input_baseline(
    path: Path,
    job: dict[str, Any],
    version: str | None,
    run: search.SearchRun | None = None,
    execution_status: str = "running",
) -> None:
    """Preserve the original input and independently evaluated baseline, outside energy windows."""
    _write_json(
        path,
        {
            "schema_version": 1,
            "kind": "confgen_input_baseline",
            "execution_status": execution_status,
            "atoms": job["atoms"],
            "reference": {"coordinates": job["reference"].tolist(), "energy_eh": None},
            "graph": job["graph_doc"],
            "coordination": job.get("coordination"),
            "shape": job["shape"],
            "parameters": {
                "charge": job["charge"],
                "uhf": job["uhf"],
                "max_cycles": job["max_cycles"],
                "settings": job["settings_doc"],
            },
            "backend": {"executable": job["xtb"], "version": version, "method": "GFN2-xTB"},
            "baseline": (
                None if run is None or run.input_baseline is None else run.input_baseline.to_dict()
            ),
            "input_seed": None if run is None else (run.stats or {}).get("input_seed"),
        },
    )


def _run(job_path: str) -> int:
    """Run one job file; return the worker exit code."""
    job = _load_job(job_path)
    els: list[str] = job["atoms"]
    ref: np.ndarray = job["reference"]
    work = Path(job["workdir"])
    work.mkdir(parents=True, exist_ok=True)
    baseline_path = Path(job.get("input_baseline_file", work / "input_baseline.json"))
    _write_input_baseline(baseline_path, job, None)
    if (exe := resolve_xtb(job["xtb"])) is None:
        _write_input_baseline(baseline_path, job, None, execution_status="failed")
        raise _JobError(f"xTB executable not found: {job['xtb']!r}")
    version = xtb_version(exe)
    _write_input_baseline(baseline_path, job, version)
    settings: ExploreSettings = job["settings"]
    calc = XtbCalculator(
        exe, work, els, job["charge"], job["uhf"], job["max_cycles"], job["cores"], version
    )
    _ACTIVE[:] = [calc]
    if _STOP.is_set():
        calc.cancel()

    last_snapshot: search.SearchRun | None = None
    try:
        # spawn, not fork: the xTB calculator runs threads, and forking a threaded process can
        # deadlock the children; one core needs no pool at all
        ctx = multiprocessing.get_context("spawn")
        with contextlib.ExitStack() as stack:
            pmap: ParallelMap = map  # type: ignore[assignment]
            if job["cores"] > 1:
                pool = futures.ProcessPoolExecutor(max_workers=job["cores"], mp_context=ctx)
                # On SIGTERM/cancel, drop queued tasks instead of draining them before exit;
                # a normal completion still waits for every task, so its results are unchanged.
                stack.callback(lambda: pool.shutdown(wait=True, cancel_futures=_STOP.is_set()))

                # one task per worker call: the tasks are coarse (one DG start, one re-embedding,
                # one RMSD batch) and pairing them lets one slow task hold back another
                def pmap(fn: Any, items: Any) -> Any:
                    return pool.map(fn, items, chunksize=1)

            def publish(snapshot: search.SearchRun) -> None:
                nonlocal last_snapshot
                last_snapshot = snapshot
                _write_input_baseline(baseline_path, job, version, snapshot)
                _write_checkpoint(
                    Path(job.get("checkpoint_file", work / "checkpoint.json")), snapshot, els
                )

            run = explore(
                job["graph"],
                job["spec"],
                ref,
                job["shape"],
                settings,
                calc,
                pmap,
                should_cancel=_STOP.is_set,
                checkpoint=publish,
            )
    except Cancelled as exc:
        _write_input_baseline(baseline_path, job, version, last_snapshot, "cancelled")
        raise search.SearchCancelled(str(exc)) from exc
    except search.SearchCancelled:
        _write_input_baseline(baseline_path, job, version, last_snapshot, "cancelled")
        raise
    except search.SearchError as exc:
        _write_input_baseline(baseline_path, job, version, last_snapshot, "failed")
        raise _JobError(f"search failed: {exc}") from exc
    settings_doc = {"max_cycles": job["max_cycles"], "xtb": job["xtb"], **job["settings_doc"]}
    _write_input_baseline(baseline_path, job, version, run, "completed")
    summary = {"settings": settings_doc, **run.summary()}
    summary["xtb"] = {"executable": exe, "version": version}
    Path(job["summary_file"]).write_text(json.dumps(summary, sort_keys=True, indent=2) + "\n")
    if (run.stats or {}).get("release") == "failed":
        sys.stderr.write(
            "warning: every release relaxation failed; the published structures keep all search restraints\n"
        )
    elif (run.stats or {}).get("release") in {"deferred", "partial"}:
        sys.stderr.write(
            "warning: time budget limited release; inspect search.release and checkpoint_stage "
            "before using the published structures\n"
        )
    if not run.passing:
        sys.stderr.write("error: no structure passed all audit checks\n")
        return 2
    low = float(run.passing[0].energy_eh or 0.0)
    frames = [(r.coords, _comment(r, low)) for r in run.passing]
    write_xyz_frames(Path(job["structures_file"]), els, frames)
    return 0


def main(argv: list[str] | None = None) -> int:
    """Run the worker for one job file; return the process exit code."""
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) != 1:
        sys.stderr.write("usage: confgen_search_worker <job.json>\n")
        return 3
    _STOP.clear()
    _ACTIVE.clear()
    prev = signal.signal(signal.SIGTERM, _handle_sigterm)
    try:
        return _run(args[0])
    except _JobError as exc:
        sys.stderr.write(f"error: {exc}\n")
        return 3
    except search.SearchCancelled:
        return 143
    finally:
        _ACTIVE.clear()
        signal.signal(signal.SIGTERM, prev)


if __name__ == "__main__":
    sys.exit(main())
