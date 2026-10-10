#!/usr/bin/env python3

"""ConfGen DG-search worker (<job.json> runner): DG starts, restrained xTB, audit.

Takes over the retired script's xTB handling unchanged; graph from the job.
Exit 0 passing / 2 none pass / 3 bad job; SIGTERM stops launches, exits non-zero.
"""

from __future__ import annotations

import concurrent.futures as futures
import hashlib
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any, Literal

import numpy as np

import confflow.science.confgen.search as search
from confflow.science.confgen.coordination.stage import resolve_axis_spec
from confflow.science.confgen.graph import TypedGraph

HARTREE_TO_KCAL = 627.5094740631
_CONV = "GEOMETRY OPTIMIZATION CONVERGED"

_STOP = threading.Event()
_LIVE: set[subprocess.Popen[str]] = set()
_LIVE_LOCK = threading.Lock()


class _JobError(ValueError):
    pass


def _handle_sigterm(signum: int, frame: Any) -> None:
    _STOP.set()
    with _LIVE_LOCK:
        running = list(_LIVE)
    for proc in running:
        if proc.poll() is None:
            proc.terminate()


def _resolve_xtb(path: str) -> str | None:
    if os.path.dirname(path):
        return path if Path(path).is_file() and os.access(path, os.X_OK) else None
    return shutil.which(path)


def _write_xyz(path: Path, els: list[str], frames: list[tuple[np.ndarray, str]]) -> None:
    with open(path, "w", encoding="utf-8") as handle:
        for xyz, comment in frames:
            handle.write(f"{len(els)}\n{comment}\n")
            for sym, (x, y, z) in zip(els, xyz):
                handle.write(f"{sym} {x:.6f} {y:.6f} {z:.6f}\n")


def _first_frame(path: Path, n: int) -> np.ndarray | None:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
        if int(lines[0].strip()) != n:
            return None
        vals = [[float(v) for v in row.split()[1:4]] for row in lines[2 : 2 + n]]
        xyz = np.asarray(vals, dtype=float)
    except (OSError, IndexError, ValueError):
        return None
    return xyz if xyz.shape == (n, 3) and bool(np.all(np.isfinite(xyz))) else None


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
            spec = resolve_axis_spec(coord_raw)
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
    settings = search.SearchSettings(
        _int(section.get("starts"), "settings.starts", 1),
        _int(section.get("seed"), "settings.seed"),
        typed_mode,
        _int(section.get("embed_timeout_seconds"), "settings.embed_timeout_seconds", 0),
        tuple(frag),
        float(scale),
    )
    job = dict(raw)
    job["reference"] = ref
    job["graph"] = graph
    job["spec"] = spec
    job["settings"] = settings
    job["settings_doc"] = section
    job["charge"] = _int(raw.get("charge"), "charge")
    job["uhf"] = _int(raw.get("uhf"), "uhf")
    job["max_cycles"] = _int(raw.get("max_cycles"), "max_cycles", 1)
    job["cores"] = _int(raw.get("cores"), "cores", 1)
    for key in ("xtb", "workdir", "structures_file", "summary_file"):
        job[key] = _text(raw.get(key), key)
    return job


def _xtb_version(exe: str) -> str:
    try:
        proc = subprocess.run([exe, "--version"], capture_output=True, text=True, timeout=60)
        out = proc.stdout + proc.stderr
    except (OSError, subprocess.SubprocessError):
        return ""
    return next((line.strip() for line in out.splitlines() if line.strip()), "")


def _comment(r: search.SearchRecord, low: float) -> str:
    energy = float(r.energy_eh or 0.0)
    return f"target={r.target} start={r.start} energy_eh={energy:.6f} rel_kcal={(energy - low) * HARTREE_TO_KCAL:.3f}"


def _relax_one(exe: str, here: Path, xyz: np.ndarray, cfg: tuple[Any, ...]) -> search.RelaxOutcome:
    els, cinp, chrg, uhf, cyc = cfg
    here.mkdir(parents=True, exist_ok=True)
    _write_xyz(here / "in.xyz", els, [(xyz, "frame")])
    if cinp is not None:
        (here / "c.inp").write_text(cinp, encoding="utf-8")
    digest = hashlib.sha256((here / "in.xyz").read_bytes()).hexdigest()
    done = here / "done.json"
    if done.is_file():
        try:
            prev = json.loads(done.read_text(encoding="utf-8"))
            if prev.get("in_sha256") == digest:
                converged = bool(prev["converged"])
                opt = _first_frame(here / "xtbopt.xyz", len(els)) if converged else None
                return search.RelaxOutcome(opt, prev["energy_eh"], converged, float(prev["wall_s"]))
        except (OSError, ValueError, KeyError, TypeError):
            pass
    env = dict(os.environ, OMP_NUM_THREADS="1", OPENBLAS_NUM_THREADS="1")
    env.update(MKL_NUM_THREADS="1", OMP_STACKSIZE="2G")
    argv = [exe, "in.xyz", "--gfn", "2", "--opt"]
    argv += ["--input", "c.inp"] if cinp is not None else []
    argv += ["--chrg", str(chrg), "--uhf", str(uhf), "--cycles", str(cyc)]
    tick, proc, out = time.monotonic(), None, ""
    try:
        with open(here / "xtb.out", "w", encoding="utf-8") as log:
            proc = subprocess.Popen(
                argv, cwd=here, stdout=log, stderr=subprocess.STDOUT, env=env, text=True
            )
    except OSError as exc:
        out = f"xTB launch failed: {exc}"
        (here / "xtb.out").write_text(out, encoding="utf-8")
    if proc is not None:
        with _LIVE_LOCK:
            _LIVE.add(proc)
        try:
            while proc.poll() is None:
                if _STOP.is_set():
                    proc.terminate()
                    break
                time.sleep(0.02)
            proc.wait(timeout=30)
        except subprocess.TimeoutExpired:
            proc.kill()
        finally:
            with _LIVE_LOCK:
                _LIVE.discard(proc)
        try:
            out = (here / "xtb.out").read_text(encoding="utf-8")
        except OSError:
            out = ""
    wall = time.monotonic() - tick
    found = re.findall(r"TOTAL ENERGY\s+([+-]?\d+(?:\.\d+)?)\s*Eh", out)
    ok = proc is not None and proc.returncode == 0 and _CONV in out
    conv = bool(ok) and (here / "xtbopt.xyz").is_file()
    energy = float(found[-1]) if found else None
    payload = {"in_sha256": digest, "energy_eh": energy, "converged": conv, "wall_s": wall}
    done.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")
    opt = _first_frame(here / "xtbopt.xyz", len(els)) if conv else None
    return search.RelaxOutcome(opt, energy, conv, wall)


def _run(job_path: str) -> int:
    """Run one job file; return the worker exit code."""
    job = _load_job(job_path)
    els: list[str] = job["atoms"]
    ref: np.ndarray = job["reference"]
    if (exe := _resolve_xtb(job["xtb"])) is None:
        raise _JobError(f"xTB executable not found: {job['xtb']!r}")
    version = _xtb_version(exe)
    work = Path(job["workdir"])
    work.mkdir(parents=True, exist_ok=True)
    settings: search.SearchSettings = job["settings"]
    cores: int = job["cores"]

    def _relax_many(
        starts: tuple[search.SearchStart, ...], restraints: tuple[tuple[int, int, float], ...]
    ) -> list[search.RelaxOutcome]:
        lines = ["$constrain", "  force constant=1.0"]
        for i, j, d in restraints:
            lines.append(f"  distance: {i + 1}, {j + 1}, {d:.5f}")
        cinp = "\n".join(lines + ["$end"]) + "\n" if restraints else None
        cfg = (els, cinp, job["charge"], job["uhf"], job["max_cycles"])

        def _one(s: search.SearchStart) -> search.RelaxOutcome:
            if _STOP.is_set():
                raise search.SearchCancelled("search cancelled")
            return _relax_one(exe, work / f"{s.target}_s{s.index:02d}", s.coords, cfg)

        with futures.ThreadPoolExecutor(max_workers=cores) as pool:
            outcomes = list(pool.map(_one, starts))
        if _STOP.is_set():
            raise search.SearchCancelled("search cancelled")
        return outcomes

    try:
        run = search.run_search(
            job["graph"],
            job["spec"],
            ref,
            job["shape"],
            settings,
            _relax_many,
            should_cancel=_STOP.is_set,
        )
    except search.SearchCancelled:
        raise
    except search.SearchError as exc:
        raise _JobError(f"search failed: {exc}") from exc
    settings_doc = {"max_cycles": job["max_cycles"], "xtb": job["xtb"], **job["settings_doc"]}
    summary = {"settings": settings_doc, **run.summary()}
    summary["xtb"] = {"executable": exe, "version": version}
    Path(job["summary_file"]).write_text(json.dumps(summary, sort_keys=True, indent=2) + "\n")
    if not run.passing:
        sys.stderr.write("error: no structure passed all audit checks\n")
        return 2
    low = float(run.passing[0].energy_eh or 0.0)
    frames = [(r.coords, _comment(r, low)) for r in run.passing]
    _write_xyz(Path(job["structures_file"]), els, frames)
    return 0


def main(argv: list[str] | None = None) -> int:
    """Run the worker for one job file; return the process exit code."""
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) != 1:
        sys.stderr.write("usage: confgen_search_worker <job.json>\n")
        return 3
    _STOP.clear()
    prev = signal.signal(signal.SIGTERM, _handle_sigterm)
    try:
        return _run(args[0])
    except _JobError as exc:
        sys.stderr.write(f"error: {exc}\n")
        return 3
    except search.SearchCancelled:
        return 143
    finally:
        signal.signal(signal.SIGTERM, prev)


if __name__ == "__main__":
    sys.exit(main())
