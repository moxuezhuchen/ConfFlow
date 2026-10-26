"""Standalone UMA (fairchem + ASE) runner used by the ``mlip`` executor.

The executor copies this file into a job directory and runs
``python mlip_runner.py job.json`` with the Python that has fairchem installed.
It is deliberately standalone: it imports only the standard library at module
level and must never import anything from ``confflow``, because that Python
usually has no ConfFlow installed. ASE, torch and fairchem are imported lazily
inside the real backend, so selection, resume and scheduling are testable
in-process with a fake backend.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import math
import multiprocessing as mp
import os
import sys
import time
from collections.abc import Callable, Iterable, Iterator
from importlib import metadata
from typing import Any

EV_PER_EH = 27.211386245988
RESULTS, DONE, LOG = "mlip_results.jsonl", "mlip_done.json", "mlip_runner.log"
MODES = ("sp", "relax", "sp_then_relax")
OPTIMIZERS = ("lbfgs", "precon_lbfgs", "fire")

_BACKEND: Any = None  # per-process backend, set by _init_worker in pool workers


def _check(ok: bool, name: str) -> None:
    if not ok:
        raise ValueError(f"invalid job field: {name}")


def _is_int(v: Any) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


def _is_num(v: Any) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _valid_structure(s: Any) -> bool:
    if not isinstance(s, dict) or not isinstance(s.get("key"), str):
        return False
    if s.get("label") is not None and not isinstance(s["label"], str):
        return False
    syms, coords = s.get("symbols"), s.get("coords")
    if not isinstance(syms, list) or not all(isinstance(x, str) for x in syms):
        return False
    if not isinstance(coords, list) or len(coords) != len(syms):
        return False
    return all(isinstance(c, list) and len(c) == 3 and all(_is_num(x) for x in c) for c in coords)


def validate_job(job: Any) -> None:
    """Raise ValueError when job.json does not match the executor contract."""
    _check(isinstance(job, dict), "job object")
    _check(isinstance(job.get("model_path"), str), "model_path")
    _check(job.get("refs_path") is None or isinstance(job["refs_path"], str), "refs_path")
    _check(isinstance(job.get("task"), str) and isinstance(job.get("device"), str), "task/device")
    for name in ("charge", "multiplicity", "max_steps", "workers", "threads"):
        _check(_is_int(job.get(name)), name)
    _check(job["workers"] >= 1 and job["threads"] >= 1 and job["max_steps"] >= 1, "limits")
    _check(job.get("mode") in MODES, "mode")
    _check(job.get("optimizer") in OPTIMIZERS, "optimizer")
    _check(_is_num(job.get("fmax")), "fmax")
    pairs = job.get("fix_pairs")
    _check(
        isinstance(pairs, list)
        and all(
            isinstance(p, list) and len(p) == 2 and all(_is_int(i) and i >= 0 for i in p)
            for p in pairs
        ),
        "fix_pairs",
    )
    pick = job.get("pick")
    if pick is not None:
        _check(
            isinstance(pick, dict)
            and all(_is_int(pick.get(k)) and pick[k] >= 0 for k in ("lowest", "per_label")),
            "pick",
        )
    _check(not (job["mode"] == "sp_then_relax" and pick is None), "pick required for sp_then_relax")
    structures = job.get("structures")
    _check(
        isinstance(structures, list) and all(_valid_structure(s) for s in structures), "structures"
    )
    keys = [s["key"] for s in structures]
    _check(len(set(keys)) == len(keys), "unique structure keys")


def load_job(path: str) -> dict[str, Any]:
    """Read and validate job.json; OSError or ValueError on failure."""
    with open(path, encoding="utf-8") as fh:
        job: dict[str, Any] = json.load(fh)
    validate_job(job)
    return job


def _read_rows(path: str) -> list[dict[str, Any]]:
    """Existing result rows; a torn last line from an interrupted run is ignored."""
    rows: list[dict[str, Any]] = []
    with contextlib.suppress(FileNotFoundError), open(path, encoding="utf-8") as fh:
        for line in fh:
            with contextlib.suppress(ValueError):
                row = json.loads(line)
                if isinstance(row, dict):
                    rows.append(row)
    return rows


def _terminate_torn_line(path: str) -> None:
    """Start appends on a fresh line when an interrupted run left a partial last line."""
    with contextlib.suppress(FileNotFoundError), open(path, "rb+") as fh:
        size = fh.seek(0, os.SEEK_END)
        if size:
            fh.seek(size - 1)
            if fh.read(1) != b"\n":
                fh.write(b"\n")


def _log(workdir: str, msg: str) -> None:
    with open(os.path.join(workdir, LOG), "a", encoding="utf-8") as fh:
        fh.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {msg}\n")


def pick_structures(
    sp_energy: dict[str, tuple[float, str | None]], pick: dict[str, int]
) -> list[str]:
    """Keys to relax, lowest energy first.

    Union of the ``lowest`` lowest structures overall and the ``per_label`` lowest
    within each label group (``None`` is one group). Ties are broken by key.
    """
    ranked = sorted(sp_energy, key=lambda k: (sp_energy[k][0], k))
    chosen = set(ranked[: pick["lowest"]])
    groups: dict[str | None, list[str]] = {}
    for key in ranked:
        groups.setdefault(sp_energy[key][1], []).append(key)
    for members in groups.values():
        chosen.update(members[: pick["per_label"]])
    return [k for k in ranked if k in chosen]


def _dist(a: list[float], b: list[float]) -> float:
    return math.dist(a, b)


def _fix_dev(
    before: list[list[float]], after: list[list[float]], pairs: list[list[int]]
) -> float | None:
    if not pairs:
        return None
    return max(abs(_dist(after[i], after[j]) - _dist(before[i], before[j])) for i, j in pairs)


def _compute(backend: Any, task: dict[str, Any]) -> dict[str, Any]:
    """One structure through the backend; any exception becomes an error row."""
    t0 = time.time()
    stage = task["stage"]
    row: dict[str, Any] = dict(
        key=task["key"],
        stage=stage,
        energy_eh=None,
        coords=None,
        converged=None,
        n_steps=0,
        n_evals=0,
        wall_s=0.0,
        optimizer=None if stage == "sp" else task["optimizer"],
        fix_max_dev=None,
        error=None,
    )
    try:
        if stage == "sp":
            energy, row["n_evals"] = backend.energy(task["symbols"], task["coords"])
        else:
            res = backend.relax(
                task["symbols"],
                task["coords"],
                task["fix_pairs"],
                task["optimizer"],
                task["fmax"],
                task["max_steps"],
            )
            energy = res["energy_eV"]
            row.update(
                coords=res["coords"],
                converged=bool(res["converged"]),
                n_steps=int(res["n_steps"]),
                n_evals=int(res["n_evals"]),
                fix_max_dev=_fix_dev(task["coords"], res["coords"], task["fix_pairs"]),
            )
        if not math.isfinite(energy):
            raise ValueError(f"non-finite energy {energy}")
        row["energy_eh"] = float(energy) / EV_PER_EH
    except Exception as exc:  # one bad structure must not stop the batch
        row.update(
            energy_eh=None, coords=None, converged=None, error=f"{type(exc).__name__}: {exc}"
        )
    row["wall_s"] = round(time.time() - t0, 3)
    return row


class _BrokenBackend:
    """Stands in for a backend that failed to load, so each structure gets an error row."""

    def __init__(self, reason: str) -> None:
        self.reason = reason

    def energy(self, *args: Any) -> Any:
        raise RuntimeError(f"backend failed to load: {self.reason}")

    relax = energy


def _load_backend(factory: Callable[[dict[str, Any]], Any], job: dict[str, Any]) -> Any:
    try:
        return factory(job)
    except Exception as exc:
        return _BrokenBackend(f"{type(exc).__name__}: {exc}")


def _init_worker(factory: Callable[[dict[str, Any]], Any], job: dict[str, Any]) -> None:
    global _BACKEND
    _BACKEND = _load_backend(factory, job)


def _worker_task(task: dict[str, Any]) -> dict[str, Any]:
    return _compute(_BACKEND, task)


@contextlib.contextmanager
def _scheduler(
    job: dict[str, Any], factory: Callable[[dict[str, Any]], Any]
) -> Iterator[Callable[[list[dict[str, Any]]], Iterable[dict[str, Any]]]]:
    """Yield a function that runs tasks and yields rows as they finish."""
    if job["workers"] == 1:
        backend = _load_backend(factory, job)
        yield lambda tasks: (_compute(backend, t) for t in tasks)
        return
    method = "fork" if "fork" in mp.get_all_start_methods() else "spawn"
    size = min(job["workers"], max(1, len(job["structures"])))
    with mp.get_context(method).Pool(size, _init_worker, (factory, job)) as pool:
        yield lambda tasks: pool.imap_unordered(_worker_task, tasks, chunksize=1)


def _sha256(path: str) -> str | None:
    digest = hashlib.sha256()
    try:
        with open(path, "rb") as fh:
            for block in iter(lambda: fh.read(1 << 20), b""):
                digest.update(block)
    except OSError:
        return None
    return digest.hexdigest()


def _pkg_version(name: str) -> str | None:
    try:
        return metadata.version(name)
    except metadata.PackageNotFoundError:
        return None


def run_job(
    job: dict[str, Any], workdir: str, backend_factory: Callable[[dict[str, Any]], Any]
) -> int:
    """Run the job in ``workdir``; returns 0 once the job ran (errors are rows)."""
    validate_job(job)
    t0 = time.time()
    results = os.path.join(workdir, RESULTS)
    rows = _read_rows(results)
    done = {(r.get("key"), r.get("stage")) for r in rows if r.get("error") is None}
    structures = job["structures"]
    labels = {s["key"]: s["label"] for s in structures}
    _log(
        workdir,
        f"start: {len(structures)} structures mode={job['mode']} workers={job['workers']} "
        f"threads={job['threads']} resumed_rows={len(rows)}",
    )

    def task(s: dict[str, Any], stage: str) -> dict[str, Any]:
        return dict(
            key=s["key"],
            stage=stage,
            symbols=s["symbols"],
            coords=s["coords"],
            fix_pairs=job["fix_pairs"],
            optimizer=job["optimizer"],
            fmax=job["fmax"],
            max_steps=job["max_steps"],
        )

    sp_energy = {
        r["key"]: (r["energy_eh"], labels[r["key"]])
        for r in rows
        if r.get("stage") == "sp" and r.get("error") is None and r.get("key") in labels
    }
    relax_keys = [s["key"] for s in structures]
    _terminate_torn_line(results)
    with open(results, "a", encoding="utf-8") as out, _scheduler(job, backend_factory) as run:

        def emit(row: dict[str, Any]) -> None:
            out.write(json.dumps(row) + "\n")
            out.flush()
            status = row["error"] or f"energy_eh={row['energy_eh']}"
            _log(workdir, f"done {row['key']} {row['stage']} wall_s={row['wall_s']} {status}")
            if row["stage"] == "sp" and row["error"] is None:
                sp_energy[row["key"]] = (row["energy_eh"], labels[row["key"]])

        if job["mode"] in ("sp", "sp_then_relax"):
            todo = [task(s, "sp") for s in structures if (s["key"], "sp") not in done]
            for row in run(todo):
                emit(row)
        if job["mode"] == "sp_then_relax":
            relax_keys = pick_structures(sp_energy, job["pick"])
        if job["mode"] != "sp":
            by_key = {s["key"]: s for s in structures}
            todo = [task(by_key[k], "relax") for k in relax_keys if (k, "relax") not in done]
            for row in run(todo):
                emit(row)

    info = dict(
        model_sha256=_sha256(job["model_path"]),
        fairchem_version=_pkg_version("fairchem-core"),
        ase_version=_pkg_version("ase"),
        torch_threads=job["threads"],
        workers=job["workers"],
        wall_s=round(time.time() - t0, 3),
        picked=relax_keys if job["mode"] != "sp" else [],
    )
    with open(os.path.join(workdir, DONE), "w", encoding="utf-8") as fh:
        json.dump(info, fh, indent=1)
    _log(workdir, f"end: wall_s={info['wall_s']}")
    return 0


class _FairchemBackend:  # pragma: no cover - needs fairchem, ASE and torch
    """Real UMA backend; loads the model once per process (same settings as uma_reopt.py)."""

    def __init__(self, job: dict[str, Any]) -> None:
        import torch
        from fairchem.core import FAIRChemCalculator
        from fairchem.core.calculate.pretrained_mlip import load_predict_unit
        from fairchem.core.units.mlip_unit.api.inference import InferenceSettings
        from omegaconf import OmegaConf

        torch.set_num_threads(job["threads"])
        try:
            torch.set_num_interop_threads(1)
        except RuntimeError:  # already set in this process
            pass
        settings = InferenceSettings(
            tf32=False,
            activation_checkpointing=False,
            merge_mole=False,
            external_graph_gen=False,
            internal_graph_gen_version=2,
        )
        refs = OmegaConf.load(job["refs_path"]) if job["refs_path"] else None
        predict = load_predict_unit(job["model_path"], settings, {}, job["device"], refs)
        self.calc = FAIRChemCalculator(predict, task_name=job["task"])
        self.charge, self.spin = job["charge"], job["multiplicity"]
        self.n_calls = 0
        plain_calculate = self.calc.calculate

        def counting_calculate(*args: Any, **kwargs: Any) -> Any:
            self.n_calls += 1
            return plain_calculate(*args, **kwargs)

        self.calc.calculate = counting_calculate

    def _atoms(
        self, symbols: list[str], coords: list[list[float]], centred: bool
    ) -> tuple[Any, list[float] | None]:
        """Atoms with calculator attached, plus the shift applied (centred mode only)."""
        from ase import Atoms

        pos, shift, extra = coords, None, {}
        if centred:  # PreconLBFGS needs a cell for its neighbour list; molecule centred in 50 A box
            mean = [sum(c[i] for c in coords) / len(coords) for i in range(3)]
            shift = [m - 25.0 for m in mean]
            pos = [[c[i] - shift[i] for i in range(3)] for c in coords]
            extra = dict(cell=[50.0, 50.0, 50.0], pbc=False)
        atoms = Atoms(symbols=symbols, positions=pos, **extra)
        atoms.info.update(charge=self.charge, spin=self.spin)
        atoms.calc = self.calc
        return atoms, shift

    def energy(self, symbols: list[str], coords: list[list[float]]) -> tuple[float, int]:
        atoms, _ = self._atoms(symbols, coords, centred=False)
        start = self.n_calls
        energy = atoms.get_potential_energy()
        return float(energy), self.n_calls - start

    def relax(
        self,
        symbols: list[str],
        coords: list[list[float]],
        fix_pairs: list[list[int]],
        optimizer: str,
        fmax: float,
        max_steps: int,
    ) -> dict[str, Any]:
        from ase.constraints import FixBondLengths
        from ase.optimize import FIRE, LBFGS

        atoms, shift = self._atoms(symbols, coords, centred=optimizer == "precon_lbfgs")
        if fix_pairs:  # tolerance 1e-8: float32 UMA forces cannot meet the 1e-13 default
            atoms.set_constraint(FixBondLengths(fix_pairs, tolerance=1e-8))
        if optimizer == "precon_lbfgs":
            from ase.optimize.precon import Exp, PreconLBFGS

            opt: Any = PreconLBFGS(
                atoms, precon=Exp(r_cut=3.0, A=3.0), use_armijo=True, logfile=None
            )
        elif optimizer == "fire":
            opt = FIRE(atoms, logfile=None)
        else:
            opt = LBFGS(atoms, logfile=None)
        start = self.n_calls
        converged = bool(opt.run(fmax=fmax, steps=max_steps))
        n_evals = self.n_calls - start
        pos = atoms.get_positions()
        if shift is not None:
            pos = [[p[i] + shift[i] for i in range(3)] for p in pos]
        return dict(
            coords=[[float(x) for x in p] for p in pos],
            energy_eV=float(atoms.get_potential_energy()),
            converged=converged,
            n_steps=int(opt.nsteps),
            n_evals=n_evals,
        )


def _fairchem_backend(job: dict[str, Any]) -> _FairchemBackend:  # pragma: no cover
    return _FairchemBackend(job)


def main(argv: list[str]) -> int:
    """``python mlip_runner.py job.json``: exit 0 when the job ran, 2 for a bad job file."""
    if len(argv) != 2:
        print("usage: python mlip_runner.py job.json", file=sys.stderr)
        return 2
    try:
        job = load_job(argv[1])
    except (OSError, ValueError) as exc:
        print(f"mlip_runner: cannot read job {argv[1]}: {exc}", file=sys.stderr)
        return 2
    return run_job(job, os.getcwd(), _fairchem_backend)


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main(sys.argv))
