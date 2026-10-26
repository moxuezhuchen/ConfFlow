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
    CAP_FIXED_ATOMS,
    CAP_HESSIAN,
    CAP_RESTRAINED_RELAX,
    FIXED_ATOM_TOLERANCE_ANGSTROM,
    CalculatorInfo,
    FixedAtom,
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
_VERSION_LINE = re.compile(r"xtb version\s+\S.*", re.IGNORECASE)
_HAS_WORD = re.compile(r"[A-Za-z0-9]")


class Cancelled(RuntimeError):
    """Raised by ``relax_many`` when the batch was cancelled."""


def resolve_xtb(path: str) -> str | None:
    """Return the executable for ``path`` (a file path or a name on ``PATH``), else ``None``."""
    if os.path.dirname(path):
        return path if Path(path).is_file() and os.access(path, os.X_OK) else None
    return shutil.which(path)


def xtb_version(exe: str) -> str:
    """Return the ``xtb version ... (commit) ...`` line of ``exe --version``.

    Falls back to the first line with any letter or digit (xtb opens with a banner of
    dashes); empty when the executable cannot be run.
    """
    try:
        proc = subprocess.run([exe, "--version"], capture_output=True, text=True, timeout=60)
        out = proc.stdout + proc.stderr
    except (OSError, subprocess.SubprocessError):
        return ""
    lines = [line.strip(" *|\t") for line in out.splitlines()]
    hit = next((m.group(0) for line in lines if (m := _VERSION_LINE.search(line))), None)
    return hit if hit is not None else next((x for x in lines if _HAS_WORD.search(x)), "")


def _file_sha256(path: str) -> str:
    """Return the sha256 of the file at ``path`` (the path itself when it cannot be read)."""
    digest = hashlib.sha256()
    try:
        with open(path, "rb") as handle:
            for block in iter(lambda: handle.read(1 << 20), b""):
                digest.update(block)
    except OSError:
        return f"path:{path}"
    return digest.hexdigest()


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


def _constrain_block(restraints: Sequence[Restraint], force_constant: float = 1.0) -> str | None:
    if not restraints:
        return None
    lines = ["$constrain", f"  force constant={force_constant!r}"]
    lines += [f"  distance: {r.i + 1}, {r.j + 1}, {r.target:.5f}" for r in restraints]
    return "\n".join(lines + ["$end"]) + "\n"


def _fixed_block(fixed_atoms: Sequence[FixedAtom]) -> str | None:
    """Render xTB's exact atom-freezing input block, distinct from distance restraints."""
    if not fixed_atoms:
        return None
    indices = ", ".join(str(atom.index + 1) for atom in fixed_atoms)
    return f"$fix\n  atoms: {indices}\n$end\n"


def _input_blocks(
    restraints: Sequence[Restraint],
    fixed_atoms: Sequence[FixedAtom],
    force_constant: float = 1.0,
) -> str | None:
    blocks = [
        block.strip()
        for block in (_constrain_block(restraints, force_constant), _fixed_block(fixed_atoms))
        if block is not None
    ]
    return "\n".join(blocks) + "\n" if blocks else None


class XtbCalculator:
    """GFN2-xTB optimiser: one xtb process per request, ``cores`` at a time, under ``workdir``.

    Each request runs in ``workdir/<key>``; a finished run is reused only when its coordinates,
    restraints, charge, spin, cycle limit and xtb binary (by content) are all unchanged.
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
        restraint_force_constant: float = 1.0,
    ) -> None:
        self._exe, self._work, self._els = exe, workdir, list(elements)
        self._charge, self._uhf, self._cycles, self._cores = charge, uhf, max_cycles, cores
        self._force = restraint_force_constant
        self._info = CalculatorInfo(
            "xtb",
            "GFN2-xTB",
            version,
            frozenset({CAP_RESTRAINED_RELAX, CAP_HESSIAN, CAP_FIXED_ATOMS}),
        )
        self._exe_sha = _file_sha256(shutil.which(exe) or exe)
        self._stop = threading.Event()
        self._live: set[subprocess.Popen[str]] = set()
        self._lock = threading.Lock()
        self._pool: futures.ThreadPoolExecutor | None = None

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
                self._work / req.key,
                req.coords,
                _input_blocks(req.restraints, req.fixed_atoms, self._force),
                req.fixed_atoms,
            )

        outcomes = [f.result() for f in self.submit_many(requests)]
        if self._stop.is_set():
            raise Cancelled("search cancelled")
        return outcomes

    def submit_many(self, requests: Sequence[RelaxRequest]) -> list[futures.Future[RelaxOutcome]]:
        """Queue every request on the shared ``cores``-wide pool; return one future each."""

        def _one(req: RelaxRequest) -> RelaxOutcome:
            if self._stop.is_set():
                raise Cancelled("search cancelled")
            return self._relax_one(
                self._work / req.key,
                req.coords,
                _input_blocks(req.restraints, req.fixed_atoms, self._force),
                req.fixed_atoms,
            )

        if self._pool is None:
            self._pool = futures.ThreadPoolExecutor(max_workers=self._cores)
        return [self._pool.submit(_one, req) for req in requests]

    def hessian(self, request: RelaxRequest) -> np.ndarray | None:
        """Return the xTB Hessian (3N x 3N, Eh/bohr^2) under the request's restraints."""
        if self._stop.is_set():
            raise Cancelled("search cancelled")
        n3 = 3 * len(self._els)
        here = self._work / request.key
        here.mkdir(parents=True, exist_ok=True)
        write_xyz_frames(here / "in.xyz", self._els, [(request.coords, "hessian")])
        argv = [self._exe, "in.xyz", "--gfn", "2", "--hess"]
        cinp = _input_blocks(request.restraints, request.fixed_atoms, self._force)
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
            proc.wait()
        finally:
            with self._lock:
                self._live.discard(proc)
        if self._stop.is_set():
            raise Cancelled("search cancelled")
        return proc.returncode == 0

    def _run_key(
        self, xyz_path: Path, cinp: str | None, fixed_atoms: Sequence[FixedAtom] = ()
    ) -> str:
        """Return the cache key of one optimisation: every input that can change its result."""
        doc = {
            "xyz": hashlib.sha256(xyz_path.read_bytes()).hexdigest(),
            "constrain": cinp,
            "fixed_atoms": [
                {"index": atom.index, "coordinates": list(atom.coordinates)} for atom in fixed_atoms
            ],
            "charge": self._charge,
            "uhf": self._uhf,
            "cycles": self._cycles,
            "xtb_sha256": self._exe_sha,
        }
        return hashlib.sha256(json.dumps(doc, sort_keys=True).encode("utf-8")).hexdigest()

    def _relax_one(
        self,
        here: Path,
        xyz: np.ndarray,
        cinp: str | None,
        fixed_atoms: Sequence[FixedAtom] = (),
    ) -> RelaxOutcome:
        els = self._els
        here.mkdir(parents=True, exist_ok=True)
        write_xyz_frames(here / "in.xyz", els, [(xyz, "frame")])
        if cinp is not None:
            (here / "c.inp").write_text(cinp, encoding="utf-8")
        else:
            (here / "c.inp").unlink(missing_ok=True)
        digest = self._run_key(here / "in.xyz", cinp, fixed_atoms)
        done = here / "done.json"
        if done.is_file():
            try:
                prev = json.loads(done.read_text(encoding="utf-8"))
                if prev.get("key_sha256") == digest:
                    converged = bool(prev["converged"])
                    opt = read_first_frame(here / "xtbopt.xyz", len(els)) if converged else None
                    if opt is not None and fixed_atoms:
                        displacement = max(
                            float(np.linalg.norm(opt[atom.index] - atom.coordinates))
                            for atom in fixed_atoms
                        )
                        if displacement > FIXED_ATOM_TOLERANCE_ANGSTROM:
                            return RelaxOutcome(
                                None,
                                prev.get("energy_eh"),
                                False,
                                float(prev["wall_s"]),
                                f"frozen core displaced by {displacement:.6g} A",
                                "core_displacement",
                            )
                    return RelaxOutcome(
                        opt,
                        prev.get("energy_eh"),
                        converged,
                        float(prev["wall_s"]),
                        str(prev.get("detail", "")),
                        prev.get("failure_kind"),
                    )
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
                proc.wait()
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
        opt = read_first_frame(here / "xtbopt.xyz", len(els)) if conv else None
        failure_kind = None
        detail = ""
        if not conv or opt is None:
            conv = False
            failure_kind = "backend_failed"
        elif fixed_atoms:
            displacement = max(
                float(np.linalg.norm(opt[atom.index] - atom.coordinates)) for atom in fixed_atoms
            )
            if displacement > FIXED_ATOM_TOLERANCE_ANGSTROM:
                conv = False
                opt = None
                failure_kind = "core_displacement"
                detail = f"frozen core displaced by {displacement:.6g} A"
        if not self._stop.is_set():  # a cancelled run is not a result: resume must relaunch it
            payload = {
                "key_sha256": digest,
                "energy_eh": energy,
                "converged": conv,
                "wall_s": wall,
                "detail": detail,
                "failure_kind": failure_kind,
            }
            done.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")
        return RelaxOutcome(opt, energy, conv, wall, detail, failure_kind)
