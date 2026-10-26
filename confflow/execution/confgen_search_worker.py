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
import time
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import replace
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
from confflow.science.confgen.dg_seed import detached_donors_of
from confflow.science.confgen.explore import ExploreSettings, ParallelMap, explore
from confflow.science.confgen.graph import TypedGraph
from confflow.science.confgen.identity import Identity, MinimaPool
from confflow.science.confgen.search_spec import CoordinationSpec, resolve_search_coordination

HARTREE_TO_KCAL = 627.5094740631

#: one donor state of the job: (sub-search label, its coordination spec); no optional donors
#: means one state labelled ``""`` with the job's own spec
_DonorState = tuple[str, CoordinationSpec | None]

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


def _finite_float(value: Any, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not np.isfinite(value):
        raise _JobError(f"job field {name!r} must be a finite number, got {value!r}")
    return float(value)


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
    force_raw = section.get("restraint_force_constant")
    force = (
        1.0 if force_raw is None else _finite_float(force_raw, "settings.restraint_force_constant")
    )
    if force <= 0.0:
        raise _JobError(f"job field 'settings.restraint_force_constant' must be > 0, got {force!r}")
    stretch_raw = section.get("max_donor_stretch")
    stretch = (
        None if stretch_raw is None else _finite_float(stretch_raw, "settings.max_donor_stretch")
    )
    if stretch is not None and stretch <= 1.0:
        raise _JobError(f"job field 'settings.max_donor_stretch' must be > 1, got {stretch!r}")
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
        max_donor_stretch=stretch,
    )
    donor_states, donor_sites = _load_donor_states(raw, spec, len(els))
    job = dict(raw)
    job["donor_states"] = donor_states
    job["donor_sites"] = donor_sites
    job["restraint_force_constant"] = force
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


def _load_donor_states(
    raw: Mapping[str, Any], spec: CoordinationSpec | None, natoms: int
) -> tuple[list[_DonorState], list[tuple[str, int]]]:
    """Read the optional-donor state table (absent: one state, the job's own spec).

    ``donor_sites`` lists ``[site id, 0-based atom]`` of the optional binding sites in
    binding-site order; ``donor_states`` lists ``{"label", "coordination"}`` per donor state.
    """
    raw_states = raw.get("donor_states")
    if raw_states is None:
        return [("", spec)], []
    if not isinstance(raw_states, list) or not raw_states:
        raise _JobError("job field 'donor_states' must be a non-empty list")
    raw_sites = raw.get("donor_sites")
    if not isinstance(raw_sites, list) or not raw_sites:
        raise _JobError("job field 'donor_sites' must list the optional binding sites")
    for entry in raw_sites:
        if not isinstance(entry, list) or len(entry) != 2 or not isinstance(entry[0], str):
            raise _JobError(f"job donor site {entry!r} must be [site id, atom]")
        if _int(entry[1], "donor_sites atom", 0) >= natoms:
            raise _JobError(f"job donor site atom {entry[1]!r} is out of range")
    states: list[_DonorState] = []
    for entry in raw_states:
        if not isinstance(entry, dict) or not isinstance(entry.get("label"), str):
            raise _JobError("job donor state must hold a 'label' string")
        coord_raw = entry.get("coordination")
        if coord_raw is None:
            states.append((entry["label"], None))
            continue
        if not isinstance(coord_raw, dict):
            raise _JobError("job donor state 'coordination' must be a mapping or null")
        try:
            states.append((entry["label"], resolve_search_coordination(coord_raw)))
        except ValueError as exc:
            raise _JobError(f"job donor state coordination is malformed: {exc}") from exc
    return states, [(str(sid), int(atom)) for sid, atom in raw_sites]


def donor_state_label(sites: Sequence[tuple[str, int]], bound: Mapping[int, bool]) -> str:
    """Join ``<site id>+`` (bound) / ``<site id>-`` (detached) per optional site in order."""
    return ",".join(f"{sid}{'+' if bound[atom] else '-'}" for sid, atom in sites if atom in bound)


def _comment(r: search.SearchRecord, low: float, sites: Sequence[tuple[str, int]] = ()) -> str:
    energy = float(r.energy_eh or 0.0)
    text = f"target={r.target} start={r.start} energy_eh={energy:.6f} rel_kcal={(energy - low) * HARTREE_TO_KCAL:.3f}"
    if r.actual_target is not None:
        text = f"{text} actual={r.actual_target}"
    arrangement = getattr(r, "arrangement", None)
    if arrangement is not None:
        text = f"{text} arrangement={arrangement}"
    distances = getattr(r, "reaction_distances", ())
    if distances:
        text = f"{text} rxn=" + ",".join(f"{d:.3f}" for d in distances)
    stretch = getattr(r, "donor_stretch", None)
    if stretch is not None:
        text = f"{text} donor_stretch={stretch:.3f}"
    flips = getattr(r, "state_flips", None)
    if flips is not None:
        text = f"{text} flips=" + (",".join(str(int(k)) for k in flips) if flips else "-")
    bound = getattr(r, "optional_bound", None)
    if bound is not None and sites:
        text = f"{text} donors={donor_state_label(sites, bound)}"
    return text


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


def _explore_states(
    job: dict[str, Any], calc: XtbCalculator, pmap: Any, publish: Any
) -> search.SearchRun:
    """Run one search, or one search per donor state merged into one published run.

    Each state gets an equal share of the budget and starts (at least 1), seed ``seed + index``,
    and the wall limit left over divided among the states still to run.
    """
    settings: ExploreSettings = job["settings"]
    states: list[_DonorState] = job["donor_states"]
    ref: np.ndarray = job["reference"]
    if len(states) == 1:
        return explore(
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
    count = len(states)
    started = time.monotonic()
    runs: list[tuple[str, ExploreSettings, search.SearchRun]] = []
    for index, (label, spec) in enumerate(states):
        limit = settings.time_limit_seconds
        left = (
            None
            if limit is None
            else max(1e-3, (limit - (time.monotonic() - started)) / (count - index))
        )
        sub = replace(
            settings,
            budget=max(1, settings.budget // count),
            min_budget=max(1, settings.min_budget // count),
            starts=None if settings.starts is None else max(1, settings.starts // count),
            seed=settings.seed + index,
            time_limit_seconds=left,
        )
        run = explore(
            job["graph"],
            spec,
            ref,
            job["shape"],
            sub,
            calc,
            pmap,
            should_cancel=_STOP.is_set,
            checkpoint=publish,
        )
        runs.append((label, sub, run))
    return _merge_donor_runs(job, runs)


def _donor_measure(job: dict[str, Any]) -> Any:
    """Return a function that stamps a structure's measured optional-donor state.

    Every merged structure is measured the same way (bound iff its donor is within the slack
    of the input's metal-donor distance), whichever donor state's search produced it.
    """
    spec: CoordinationSpec = job["spec"]
    ref: np.ndarray = job["reference"]
    metal = int(spec.metal_center)
    refs = {atom: float(np.linalg.norm(ref[atom] - ref[metal])) for _, atom in job["donor_sites"]}

    def measure(rec: search.SearchRecord) -> search.SearchRecord:
        if rec.coords is None or not refs:
            return rec
        bound = search.optional_bound_state(rec.coords, metal, refs)
        return replace(rec, optional_bound=bound)

    return measure


def _measured_state(rec: search.SearchRecord) -> tuple[tuple[int, bool], ...]:
    """Donor state a structure actually has (measured distances, not its sub-search)."""
    return tuple(sorted((int(k), bool(v)) for k, v in (rec.optional_bound or {}).items()))


def _measured_label(sites: Sequence[tuple[str, int]], rec: search.SearchRecord) -> str:
    return donor_state_label(sites, rec.optional_bound or {})


def _measured_rows(
    declared: Sequence[str], published: Sequence[tuple[str, float]], lowest: float | None
) -> list[dict[str, Any]]:
    """Count published structures per measured donor state; declared states come first."""
    labels = list(dict.fromkeys([*declared, *(lab for lab, _ in published)]))
    rows: list[dict[str, Any]] = []
    for label in labels:
        energies = [e for lab, e in published if lab == label]
        rows.append(
            {
                "label": label,
                "published": len(energies),
                "lowest_rel_kcal": (
                    (min(energies) - lowest) * HARTREE_TO_KCAL
                    if energies and lowest is not None
                    else None
                ),
            }
        )
    return rows


def _merge_donor_runs(
    job: dict[str, Any], runs: list[tuple[str, ExploreSettings, search.SearchRun]]
) -> search.SearchRun:
    """Merge per-state runs: dedupe across states, one energy window, each measured state's lowest."""
    settings: ExploreSettings = job["settings"]
    states: list[_DonorState] = job["donor_states"]
    sites = job["donor_sites"]
    pool = MinimaPool(Identity(job["graph"], fixed_atoms=settings.freeze_core))
    owner: dict[int, int] = {}
    kept: dict[int, search.SearchRecord] = {}
    measure = _donor_measure(job)
    ordered = sorted(
        (
            (float(rec.energy_eh or 0.0), index, measure(rec))
            for index, (_, _, run) in enumerate(runs)
            for rec in run.passing
        ),
        key=lambda item: (item[0], item[1]),
    )
    for energy, index, rec in ordered:
        mid = pool.add(energy, rec.coords, rec.target, f"donor_state{index}")
        if mid is not None:
            owner[mid], kept[mid] = index, rec
    lowest = pool.lowest() if len(pool) else None
    window = settings.window_kcal
    chosen: list[int] = []
    seen: set[tuple[tuple[int, bool], ...]] = set()
    for minimum in pool.ordered():
        state = _measured_state(kept[minimum.id])
        in_window = (
            (minimum.energy - lowest) * HARTREE_TO_KCAL <= window if lowest is not None else False
        )
        if in_window or state not in seen:
            chosen.append(minimum.id)
        seen.add(state)
    final = tuple(replace(kept[mid], start=mid) for mid in chosen)
    chosen_set = set(chosen)

    stats: dict[str, Any] = {}
    rows: list[dict[str, Any]] = []
    sites = job["donor_sites"]
    for index, (label, sub, run) in enumerate(runs):
        # the sub-search declares its donors bound or detached; a passing structure may end in
        # another measured state (a detached donor re-bound the metal, or the reverse). Counted
        # over every passing structure of the sub-search, before the merge and the window.
        declared_off = set(detached_donors_of(states[index][1]))
        rebound: Counter[str] = Counter()
        detached: Counter[str] = Counter()
        for rec in run.passing:
            bound = measure(rec).optional_bound or {}
            for sid, atom in sites:
                if atom not in bound or (atom in declared_off) != bound[atom]:
                    continue  # matches the declared state (declared off <=> measured off)
                (rebound if bound[atom] else detached)[sid] += 1
        for key, value in (run.stats or {}).items():
            if key not in stats:
                stats[key] = value
            elif all(
                isinstance(v, (int, float)) and not isinstance(v, bool) for v in (stats[key], value)
            ):
                stats[key] = stats[key] + value
        lows = [float(rec.energy_eh or 0.0) for rec in run.passing]
        rows.append(
            {
                "label": label,
                "budget": sub.budget,
                "published": sum(1 for mid in chosen if owner[mid] == index),
                "passed": len(run.passing),
                "lowest_rel_kcal": (
                    (min(lows) - lowest) * HARTREE_TO_KCAL if lows and lowest is not None else None
                ),
                "stop": (run.stats or {}).get("stop"),
                "release": (run.stats or {}).get("release"),
                "rebound": dict(sorted(rebound.items())),
                "detached": dict(sorted(detached.items())),
            }
        )
    stats["donor_states"] = rows
    measured_published = [
        (_measured_label(sites, kept[minimum.id]), minimum.energy)
        for minimum in pool.ordered()
        if minimum.id in chosen_set
    ]
    stats["donor_measured"] = _measured_rows(
        [label for label, _, _ in runs], measured_published, lowest
    )

    targets: dict[str, search.SearchTarget] = {}
    generated: Counter[str] = Counter()
    relaxed: Counter[str] = Counter()
    for _, _, run in runs:
        for target in run.targets:
            targets.setdefault(target.id, target)
            generated[target.id] += target.generated
            relaxed[target.id] += target.relaxed
    per_target = Counter(rec.target for rec in final)
    merged_targets = tuple(
        replace(
            t,
            generated=0,
            relaxed=0,
            passed=per_target.get(tid, 0),
            realized=per_target.get(tid, 0),
        )
        for tid, t in targets.items()
    )
    totals = {
        "targets": len(merged_targets),
        "generated": sum((run.totals or {}).get("generated", 0) for _, _, run in runs),
        "relaxed": sum((run.totals or {}).get("relaxed", 0) for _, _, run in runs),
        "passed": len(final),
    }

    # the input's own donor state (all bound) carries the independent input baseline
    base_index = next((i for i, (label, _, _) in enumerate(runs) if "-" not in label), 0)
    baseline = runs[base_index][2].input_baseline
    record = None if baseline is None else baseline.final_record
    if (
        baseline is not None
        and record is not None
        and record.coords is not None
        and record.energy_eh is not None
    ):
        pid = pool.match(float(record.energy_eh), record.coords)
        published = pid in chosen_set
        baseline = replace(
            baseline,
            final_pool_id=pid,
            published=published,
            disposition="published" if published else "outside_energy_window",
            lowest_final_delta_kcal=(
                (lowest - float(record.energy_eh)) * HARTREE_TO_KCAL
                if final and baseline.comparable_to_final and lowest is not None
                else None
            ),
        )
    return search.SearchRun(
        merged_targets, final, final, runs[0][2].fragment_charges, totals, stats, baseline
    )


def _run(job_path: str) -> int:
    """Run one job file; return the worker exit code."""
    job = _load_job(job_path)
    els: list[str] = job["atoms"]
    work = Path(job["workdir"])
    work.mkdir(parents=True, exist_ok=True)
    baseline_path = Path(job.get("input_baseline_file", work / "input_baseline.json"))
    _write_input_baseline(baseline_path, job, None)
    if (exe := resolve_xtb(job["xtb"])) is None:
        _write_input_baseline(baseline_path, job, None, execution_status="failed")
        raise _JobError(f"xTB executable not found: {job['xtb']!r}")
    version = xtb_version(exe)
    _write_input_baseline(baseline_path, job, version)
    calc = XtbCalculator(
        exe,
        work,
        els,
        job["charge"],
        job["uhf"],
        job["max_cycles"],
        job["cores"],
        version,
        restraint_force_constant=job["restraint_force_constant"],
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

            run = _explore_states(job, calc, pmap, publish)
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
    frames = [(r.coords, _comment(r, low, job["donor_sites"])) for r in run.passing]
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
