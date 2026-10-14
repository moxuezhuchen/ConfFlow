"""GFN2-xTB engine for the ConfGen search, behind the backend-neutral ``Calculator`` protocol.

Owns everything xTB-specific: locating and versioning the executable, rendering restraints
as an ``$constrain`` block, launching one optimisation per request (parallel, resumable via
``done.json``, cancellable), and parsing energy and geometry.  Also owns the plain-xyz
helpers the worker reuses for its output.

Does not decide what to relax or how to judge results: that is the search's job.  Another
engine is added as a sibling module implementing the same protocol.
"""

from __future__ import annotations

import concurrent.futures as futures
import hashlib
import json
import os
import re
import shutil
import subprocess
import threading
import time
from collections.abc import Sequence
from pathlib import Path

import numpy as np

from confflow.science.confgen.calculator import (
    CAP_HESSIAN,
    CAP_RESTRAINED_RELAX,
    CalculatorInfo,
    RelaxOutcome,
    RelaxRequest,
    Restraint,
)

__all__ = [
    "Cancelled",
    "XtbCalculator",
    "read_first_frame",
    "resolve_xtb",
    "write_xyz_frames",
    "xtb_version",
]

_CONV = "GEOMETRY OPTIMIZATION CONVERGED"


class Cancelled(RuntimeError):
    """Raised by ``relax_many`` when the batch was cancelled."""


def resolve_xtb(path: str) -> str | None:
    """Return the executable for ``path`` (a file path or a name on ``PATH``), else ``None``."""
    if os.path.dirname(path):
        return path if Path(path).is_file() and os.access(path, os.X_OK) else None
    return shutil.which(path)


def xtb_version(exe: str) -> str:
    """Return the first non-empty line of ``exe --version`` (empty when unavailable)."""
    try:
        proc = subprocess.run([exe, "--version"], capture_output=True, text=True, timeout=60)
        out = proc.stdout + proc.stderr
    except (OSError, subprocess.SubprocessError):
        return ""
    return next((line.strip() for line in out.splitlines() if line.strip()), "")


def write_xyz_frames(path: Path, els: list[str], frames: list[tuple[np.ndarray, str]]) -> None:
    """Write ``frames`` (coordinates, comment) as a multi-frame xyz file."""
    with open(path, "w", encoding="utf-8") as handle:
        for xyz, comment in frames:
            handle.write(f"{len(els)}\n{comment}\n")
            for sym, (x, y, z) in zip(els, xyz):
                handle.write(f"{sym} {x:.6f} {y:.6f} {z:.6f}\n")


def read_first_frame(path: Path, n: int) -> np.ndarray | None:
    """Return the first frame of an ``n``-atom xyz file, or ``None`` if unreadable."""
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
        if int(lines[0].strip()) != n:
            return None
        vals = [[float(v) for v in row.split()[1:4]] for row in lines[2 : 2 + n]]
        xyz = np.asarray(vals, dtype=float)
    except (OSError, IndexError, ValueError):
        return None
    return xyz if xyz.shape == (n, 3) and bool(np.all(np.isfinite(xyz))) else None


def _constrain_block(restraints: Sequence[Restraint]) -> str | None:
    if not restraints:
        return None
    lines = ["$constrain", "  force constant=1.0"]
    lines += [f"  distance: {r.i + 1}, {r.j + 1}, {r.target:.5f}" for r in restraints]
    return "\n".join(lines + ["$end"]) + "\n"


class XtbCalculator:
    """GFN2-xTB optimiser: one xtb process per request, ``cores`` at a time, under ``workdir``.

    Each request runs in ``workdir/<key>``; a finished run whose input is unchanged is reused.
    ``cancel()`` (safe from a signal handler) stops new launches and terminates running ones.
    """

    def __init__(
        self,
        exe: str,
        workdir: Path,
        elements: list[str],
        charge: int,
        uhf: int,
        max_cycles: int,
        cores: int,
        version: str = "",
    ) -> None:
        self._exe, self._work, self._els = exe, workdir, list(elements)
        self._charge, self._uhf, self._cycles, self._cores = charge, uhf, max_cycles, cores
        self._info = CalculatorInfo(
            "xtb", "GFN2-xTB", version, frozenset({CAP_RESTRAINED_RELAX, CAP_HESSIAN})
        )
        self._stop = threading.Event()
        self._live: set[subprocess.Popen[str]] = set()
        self._lock = threading.Lock()

    @property
    def info(self) -> CalculatorInfo:
        """Return the engine identity and capabilities."""
        return self._info

    @property
    def cancelled(self) -> bool:
        """Return whether ``cancel()`` was called."""
        return self._stop.is_set()

    def cancel(self) -> None:
        """Stop launching optimisations and terminate the running ones."""
        self._stop.set()
        with self._lock:
            running = list(self._live)
        for proc in running:
            if proc.poll() is None:
                proc.terminate()

    def relax_many(self, requests: Sequence[RelaxRequest]) -> list[RelaxOutcome]:
        """Relax every request (``cores`` in parallel); raise ``Cancelled`` if cancelled."""

        def _one(req: RelaxRequest) -> RelaxOutcome:
            if self._stop.is_set():
                raise Cancelled("search cancelled")
            return self._relax_one(
                self._work / req.key, req.coords, _constrain_block(req.restraints)
            )

        with futures.ThreadPoolExecutor(max_workers=self._cores) as pool:
            outcomes = list(pool.map(_one, requests))
        if self._stop.is_set():
            raise Cancelled("search cancelled")
        return outcomes

    def hessian(self, request: RelaxRequest) -> np.ndarray | None:
        """Return the xTB Hessian (3N x 3N, Eh/bohr^2) under the request's restraints."""
        if self._stop.is_set():
            raise Cancelled("search cancelled")
        n3 = 3 * len(self._els)
        here = self._work / request.key
        here.mkdir(parents=True, exist_ok=True)
        write_xyz_frames(here / "in.xyz", self._els, [(request.coords, "hessian")])
        argv = [self._exe, "in.xyz", "--gfn", "2", "--hess"]
        cinp = _constrain_block(request.restraints)
        if cinp is not None:
            (here / "c.inp").write_text(cinp, encoding="utf-8")
            argv += ["--input", "c.inp"]
        argv += ["--chrg", str(self._charge), "--uhf", str(self._uhf)]
        env = dict(os.environ, OMP_NUM_THREADS=str(self._cores), OPENBLAS_NUM_THREADS="1")
        env.update(MKL_NUM_THREADS="1", OMP_STACKSIZE="2G")
        if not self._launch(argv, here, env):
            return None
        try:
            rows = (here / "hessian").read_text(encoding="utf-8").splitlines()[1:]
            vals = np.array([float(v) for line in rows for v in line.split()], dtype=float)
        except (OSError, ValueError):
            return None
        if vals.size != n3 * n3 or not bool(np.all(np.isfinite(vals))):
            return None
        return vals.reshape(n3, n3)

    def _launch(self, argv: list[str], here: Path, env: dict[str, str]) -> bool:
        """Run one xtb process in ``here`` (cancellable); return whether it exited with 0."""
        proc = None
        try:
            with open(here / "xtb.out", "w", encoding="utf-8") as log:
                proc = subprocess.Popen(
                    argv, cwd=here, stdout=log, stderr=subprocess.STDOUT, env=env, text=True
                )
        except OSError as exc:
            (here / "xtb.out").write_text(f"xTB launch failed: {exc}", encoding="utf-8")
            return False
        with self._lock:
            self._live.add(proc)
        try:
            while proc.poll() is None:
                if self._stop.is_set():
                    proc.terminate()
                    break
                time.sleep(0.02)
            proc.wait(timeout=30)
        except subprocess.TimeoutExpired:
            proc.kill()
        finally:
            with self._lock:
                self._live.discard(proc)
        if self._stop.is_set():
            raise Cancelled("search cancelled")
        return proc.returncode == 0

    def _relax_one(self, here: Path, xyz: np.ndarray, cinp: str | None) -> RelaxOutcome:
        els = self._els
        here.mkdir(parents=True, exist_ok=True)
        write_xyz_frames(here / "in.xyz", els, [(xyz, "frame")])
        if cinp is not None:
            (here / "c.inp").write_text(cinp, encoding="utf-8")
        digest = hashlib.sha256((here / "in.xyz").read_bytes()).hexdigest()
        done = here / "done.json"
        if done.is_file():
            try:
                prev = json.loads(done.read_text(encoding="utf-8"))
                if prev.get("in_sha256") == digest:
                    converged = bool(prev["converged"])
                    opt = read_first_frame(here / "xtbopt.xyz", len(els)) if converged else None
                    return RelaxOutcome(opt, prev["energy_eh"], converged, float(prev["wall_s"]))
            except (OSError, ValueError, KeyError, TypeError):
                pass
        env = dict(os.environ, OMP_NUM_THREADS="1", OPENBLAS_NUM_THREADS="1")
        env.update(MKL_NUM_THREADS="1", OMP_STACKSIZE="2G")
        argv = [self._exe, "in.xyz", "--gfn", "2", "--opt"]
        argv += ["--input", "c.inp"] if cinp is not None else []
        argv += [
            "--chrg",
            str(self._charge),
            "--uhf",
            str(self._uhf),
            "--cycles",
            str(self._cycles),
        ]
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
            with self._lock:
                self._live.add(proc)
            try:
                while proc.poll() is None:
                    if self._stop.is_set():
                        proc.terminate()
                        break
                    time.sleep(0.02)
                proc.wait(timeout=30)
            except subprocess.TimeoutExpired:
                proc.kill()
            finally:
                with self._lock:
                    self._live.discard(proc)
            try:
                out = (here / "xtb.out").read_text(encoding="utf-8")
            except OSError:
                out = ""
        wall = time.monotonic() - tick
        found = re.findall(r"TOTAL ENERGY\s+([+-]?\d+(?:\.\d+)?)\s*Eh", out)
        ok = proc is not None and proc.returncode == 0 and _CONV in out
        conv = bool(ok) and (here / "xtbopt.xyz").is_file()
        energy = float(found[-1]) if found else None
        if not self._stop.is_set():  # a cancelled run is not a result: resume must relaunch it
            payload = {"in_sha256": digest, "energy_eh": energy, "converged": conv, "wall_s": wall}
            done.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")
        opt = read_first_frame(here / "xtbopt.xyz", len(els)) if conv else None
        return RelaxOutcome(opt, energy, conv, wall)
