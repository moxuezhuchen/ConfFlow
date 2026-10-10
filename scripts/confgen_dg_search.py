#!/usr/bin/env python3
"""DG seeds, constrained xTB relaxation, and audit for one complex.

Registered script: stdlib plus NumPy plus ``confflow`` only.
"""

from __future__ import annotations

import argparse
import concurrent.futures as futures
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import numpy as np

from confflow.science.bonding import covalent_radius
from confflow.science.confgen import coordination, graph
from confflow.science.confgen.dg_seed import DGSeedError, DGSeedSettings, generate_dg_seeds
from confflow.science.confgen.graph import ForbiddenTrans
from confflow.science.data import get_atomic_number

Pairs = list[tuple[int, int]]
Bonds = set[tuple[int, int]]
Sel = tuple[Any, list[int], Pairs, Pairs, float]
Ctx = tuple[np.ndarray, list[str], Any, list[int], list[str], Any, Pairs, float]
CHECKS = (
    "converged",
    "topology",
    "coordination_class",
    "stereo",
    "reaction_distance",
    "metal_donor_distance",
    "donor_orientation",
    "contacts",
)
R_TOL, MD_TOL, C_SCALE, D_TOL = 0.02, 0.03, 0.70, 30.0
HARTREE_TO_KCAL = 627.5094740631
_CONV = "GEOMETRY OPTIMIZATION CONVERGED"
_POL = "REJECTED_BY_POLICY"


def _idx(value: str, name: str, natoms: int) -> int:
    try:
        number = int(value)
    except ValueError:
        raise ValueError(f"{name} {value!r} is not an integer") from None
    if isinstance(number, bool) or not 1 <= number <= natoms:
        raise ValueError(f"{name} {value!r} is out of range for {natoms} atoms")
    return number - 1


def _pairs(text: str, name: str, natoms: int) -> Pairs:
    out = []
    for chunk in [p.strip() for p in text.split(",") if p.strip()]:
        sides = chunk.split("-")
        if len(sides) != 2:
            raise ValueError(f"{name} entry {chunk!r} must look like A-B")
        pair = (_idx(sides[0].strip(), name, natoms), _idx(sides[1].strip(), name, natoms))
        if pair[0] == pair[1]:
            raise ValueError(f"{name} entry {chunk!r} must join distinct atoms")
        out.append(pair)
    if not out:
        raise ValueError(f"{name} must hold at least one pair")
    return out


def _dist(xyz: np.ndarray, i: int, j: int) -> float:
    return float(np.linalg.norm(xyz[i] - xyz[j]))


def _read_xyz(path: str) -> tuple[list[str], np.ndarray]:
    try:
        lines = Path(path).read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise ValueError(f"cannot read input xyz {path!r}: {exc}") from exc
    cur = next((i for i, line in enumerate(lines) if line.strip()), len(lines))
    try:
        count = int(lines[cur].strip())
    except (IndexError, ValueError):
        raise ValueError(f"input xyz {path!r} has no atom-count first line") from None
    if count < 1:
        raise ValueError(f"input xyz {path!r} declares {count} atoms")
    rows = lines[cur + 2 : cur + 2 + count]
    if len(rows) != count:
        raise ValueError(f"input xyz {path!r} is truncated: needs {count} atom rows")
    if any(line.strip() for line in lines[cur + 2 + count :]):
        raise ValueError(f"input xyz {path!r} must hold exactly one frame")
    els, xyz = [], np.zeros((count, 3), dtype=float)
    for i, row in enumerate(rows):
        parts = row.split()
        if len(parts) < 4:
            raise ValueError(f"input xyz {path!r} row {i + 1} is malformed: {row!r}")
        try:
            xyz[i] = [float(parts[1]), float(parts[2]), float(parts[3])]
        except ValueError:
            raise ValueError(f"input xyz {path!r} row {i + 1} is malformed") from None
        els.append(parts[0])
    if not bool(np.all(np.isfinite(xyz))):
        raise ValueError(f"input xyz {path!r} holds non-finite coordinates")
    return els, xyz


def _write_xyz(path: Path, els: list[str], frames: list[tuple[np.ndarray, str]]) -> None:
    with open(path, "w", encoding="utf-8") as handle:
        for xyz, comment in frames:
            handle.write(f"{len(els)}\n{comment}\n")
            for sym, (x, y, z) in zip(els, xyz):
                handle.write(f"{sym} {x:.6f} {y:.6f} {z:.6f}\n")


def _rad(symbol: str) -> float:
    radius = covalent_radius(get_atomic_number(symbol))
    if radius is None:
        raise ValueError(f"element {symbol!r} has no usable covalent radius")
    return float(radius)


def perceive_bond_set(
    xyz: np.ndarray, els: list[str], metal: int | None, fp: Pairs, scale: float
) -> Bonds:
    """Perceive covalent bonds: non-metal pairs under the scaled radii sum."""
    radii = [_rad(s) for s in els]
    skip = {(min(a, b), max(a, b)) for a, b in fp}
    bonds: Bonds = set()
    for i in range(len(els)):
        if i == metal:
            continue
        for j in range(i + 1, len(els)):
            if j == metal or (i, j) in skip:
                continue
            if _dist(xyz, i, j) < scale * (radii[i] + radii[j]):
                bonds.add((i, j))
    return bonds


def _adj(n: int, bonds: Bonds, metal: int | None) -> list[list[int]]:
    adj: list[list[int]] = [[] for _ in range(n)]
    for i, j in bonds:
        adj[i].append(j)
        adj[j].append(i)
    for i, row in enumerate(adj):
        adj[i] = sorted(k for k in row if k != metal)
    if metal is not None:
        adj[metal] = []
    return adj


def _sep(adj: list[list[int]], src: int) -> list[float]:
    dist = [float("inf")] * len(adj)
    dist[src] = 0.0
    queue = [src]
    while queue:
        node = queue.pop(0)
        for peer in adj[node]:
            if dist[peer] == float("inf"):
                dist[peer] = dist[node] + 1.0
                queue.append(peer)
    return dist


def build_topology(els: list[str], ref: np.ndarray, sel: Sel, shape: str | None) -> tuple[Any, ...]:
    """Build the typed graph and coordination spec from the reference geometry."""
    metal, donors, pairs, forbid, scale = sel
    ref_bonds = perceive_bond_set(ref, els, metal, pairs, scale)
    atoms = tuple(graph.AtomRef(index=i, element=e) for i, e in enumerate(els))
    edges = [graph.TypedEdge(a=a, b=b, type=graph.EdgeType.COVALENT) for a, b in sorted(ref_bonds)]
    if metal is not None:
        edges += [graph.TypedEdge(a=metal, b=d, type=graph.EdgeType.COORDINATION) for d in donors]
    edges += [graph.TypedEdge(a=a, b=b, type=graph.EdgeType.FORMING) for a, b in sorted(pairs)]
    topo = graph.TypedGraph(atoms, tuple(edges), metal, tuple(sorted(pairs)), "confgen_dg_search")
    if metal is None:
        return topo, None, ref_bonds
    assert shape is not None
    site_of = {d: f"{els[d]}{d + 1}" for d in donors}
    cons = []
    for k, (a, b) in enumerate(forbid):
        cons.append(ForbiddenTrans(f"F{k:02d}", (site_of[a], site_of[b]), _POL, "cli:forbid-trans"))
    order = [graph.BindingSite(site_of[d], "atom", (d,), 1) for d in donors]
    spec = graph.CoordinationSpec(metal, tuple(order), (shape,), "enumerate", tuple(cons))
    return topo, spec, ref_bonds


def audit_checks(
    new: np.ndarray, ctx: Ctx, cmd: tuple[int, ...], conv: bool, centers: tuple[int, ...] = ()
) -> list[str]:
    """Audit relaxed *new* against the reference; return failed check names."""
    ref, els, metal, donors, sites, shape, pairs, scale = ctx
    failed = [] if conv else ["converged"]
    ref_bonds = perceive_bond_set(ref, els, metal, pairs, scale)
    try:
        new_bonds = perceive_bond_set(new, els, metal, pairs, scale)
    except ValueError:
        new_bonds = set()
        failed.append("topology")
    if "topology" not in failed and new_bonds != ref_bonds:
        failed.append("topology")
    if metal is not None:
        group = coordination.shapes.proper_rotation_group(shape)
        try:
            seen = coordination.perception.perceive_donors(
                np.asarray(new, dtype=float), metal, donors, sites, shape
            )
            got = coordination.enumeration.canonical_representative(tuple(seen.best_class), group)
            ok = bool(seen.unambiguous) and got == tuple(cmd)
        except ValueError:
            ok = False
        if not ok:
            failed.append("coordination_class")
    adj = _adj(len(els), ref_bonds, metal)
    stereo = True
    for i in centers:
        if not 0 <= i < len(els) or len(adj[i]) != 4:
            continue
        pick = tuple(adj[i][:4])
        before = coordination.realization.signed_volume(np.asarray(ref, dtype=float), i, pick)
        after = coordination.realization.signed_volume(np.asarray(new, dtype=float), i, pick)
        same = (before > 0.0) == (after > 0.0) if before and after else before == after == 0.0
        stereo = stereo and same
    if not stereo:
        failed.append("stereo")
    for i, j in pairs:
        if abs(_dist(new, i, j) - _dist(ref, i, j)) > R_TOL:
            failed.append("reaction_distance")
            break
    for d in donors:
        if abs(_dist(new, metal, d) - _dist(ref, metal, d)) > MD_TOL:
            failed.append("metal_donor_distance")
            break
    if metal is not None:
        worst = 0.0
        for d in donors:
            for x in adj[d]:
                v_ref, w_ref = ref[metal] - ref[d], ref[x] - ref[d]
                v_new, w_new = new[metal] - new[d], new[x] - new[d]
                n_ref = float(np.linalg.norm(v_ref) * np.linalg.norm(w_ref))
                n_new = float(np.linalg.norm(v_new) * np.linalg.norm(w_new))
                if n_ref < 1e-12 or n_new < 1e-12:
                    continue
                a_ref = float(
                    np.degrees(np.arccos(np.clip(float(np.dot(v_ref, w_ref)) / n_ref, -1.0, 1.0)))
                )
                a_new = float(
                    np.degrees(np.arccos(np.clip(float(np.dot(v_new, w_new)) / n_new, -1.0, 1.0)))
                )
                worst = max(worst, abs(a_new - a_ref))
        if worst > D_TOL:
            failed.append("donor_orientation")
    radii, clash = [_rad(s) for s in els], False
    for i in range(len(els)):
        if i == metal:
            continue
        sep = _sep(adj, i)
        far = [j for j in range(i + 1, len(els)) if j != metal and sep[j] > 3.0]
        clash = any(_dist(new, i, j) < C_SCALE * (radii[i] + radii[j]) for j in far)
        if clash:
            break
    if clash:
        failed.append("contacts")
    return [name for name in CHECKS if name in failed]


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


def _xtb(path: str) -> str | None:
    if os.path.dirname(path):
        return path if Path(path).is_file() and os.access(path, os.X_OK) else None
    return shutil.which(path)


def build_parser() -> argparse.ArgumentParser:
    """Build the script command-line parser (no braces in any option)."""
    p = argparse.ArgumentParser(description="DG seeds, constrained xTB relaxation, and audit.")
    p.add_argument("frame", nargs="?", default="", help="input xyz frame ({input} in steps)")
    p.add_argument("--input", required=False, help="input xyz (one frame)")
    p.add_argument("--metal", type=int, default=None, help="1-based metal atom index")
    p.add_argument("--donors", default=None, help="1-based donor indices in site order")
    p.add_argument("--shape", default=None, help="coordination shape name")
    p.add_argument("--forming", default="", help="1-based reaction pairs A-B[,C-D]")
    p.add_argument("--forbid-trans", default="", help="donor atom pairs never trans")
    p.add_argument("--charge", type=int, default=0, help="total charge for xTB")
    p.add_argument("--uhf", type=int, default=0, help="unpaired electrons for xTB")
    p.add_argument("--count", type=int, default=8, help="DG starts per class")
    p.add_argument("--seed", type=int, default=1, help="base random seed")
    p.add_argument("--cores", type=int, default=1, help="parallel xTB processes")
    p.add_argument("--xtb", default="", help="xTB executable path")
    p.add_argument("--bond-scale", type=float, default=1.25, help="bond perception factor")
    p.add_argument("--embed-timeout", type=int, default=120, help="DG timeout seconds")
    p.add_argument("--max-cycles", type=int, default=1000, help="xTB optimisation cycles")
    p.add_argument("--small-ring-torsions", default="both", choices=("both", "on", "off"))
    p.add_argument("--fragment-charge", action="append", default=[], help="ATOM:Q override")
    p.add_argument("--workdir", default="./dg_search_work", help="per-start directories")
    p.add_argument("--out", default="structures.xyz", help="passing structures xyz")
    p.add_argument("--summary", default="summary.json", help="JSON summary path")
    return p


def _validated(args: argparse.Namespace) -> tuple[Any, ...]:
    src = args.frame or args.input
    if not src:
        raise ValueError("an input xyz is required (--input PATH or the frame path)")
    els, ref = _read_xyz(src)
    n = len(els)
    trio = (args.metal is not None, args.donors is not None, args.shape is not None)
    if any(trio) and not all(trio):
        raise ValueError("--metal, --donors and --shape must be given together or all omitted")
    pairs = _pairs(args.forming, "--forming", n) if args.forming else []
    forbid = _pairs(args.forbid_trans, "--forbid-trans", n) if args.forbid_trans else []
    metal, donors, shape = None, [], None
    if trio[0]:
        assert args.donors is not None and args.shape is not None
        metal = _idx(str(args.metal), "--metal", n)
        parts = [p.strip() for p in args.donors.split(",") if p.strip()]
        if not parts:
            raise ValueError("--donors must hold at least one index")
        donors = [_idx(p, "--donors", n) for p in parts]
        shape = args.shape
        if metal in donors:
            raise ValueError("--metal must not be one of --donors")
        if len(set(donors)) != len(donors):
            raise ValueError("--donors must hold distinct atoms")
        if any(i not in donors or j not in donors for i, j in forbid):
            raise ValueError("--forbid-trans pairs must both be donor atoms")
        need = coordination.shapes.get_shape(shape).coordination_number
        if need != len(donors):
            raise ValueError(f"--shape {shape!r} needs {need} donors")
    elif forbid:
        raise ValueError("--forbid-trans needs --metal, --donors and --shape")
    if min(args.count, args.cores, args.max_cycles) < 1:
        raise ValueError("--count, --cores and --max-cycles must each be at least 1")
    if not np.isfinite(args.bond_scale) or args.bond_scale <= 0:
        raise ValueError("--bond-scale must be positive")
    if args.embed_timeout < 0:
        raise ValueError("--embed-timeout must be non-negative")
    frag = []
    for entry in args.fragment_charge:
        sides = str(entry).split(":")
        if len(sides) != 2:
            raise ValueError(f"--fragment-charge {entry!r} must look like ATOM:Q")
        try:
            frag.append((_idx(sides[0].strip(), "--fragment-charge", n), int(sides[1].strip())))
        except ValueError:
            raise ValueError(f"--fragment-charge {entry!r} must look like ATOM:Q") from None
    return els, ref, metal, donors, pairs, forbid, frag, src


def _relax(exe, work, xyz, cfg):
    els, cinp, chrg, uhf, cyc = cfg
    work.mkdir(parents=True, exist_ok=True)
    _write_xyz(work / "in.xyz", els, [(xyz, "frame")])
    if cinp is not None:
        (work / "c.inp").write_text(cinp, encoding="utf-8")
    digest = hashlib.sha256((work / "in.xyz").read_bytes()).hexdigest()
    done = work / "done.json"
    if done.is_file():
        try:
            prev = json.loads(done.read_text(encoding="utf-8"))
            if prev.get("in_sha256") == digest:
                return prev["energy_eh"], bool(prev["converged"]), float(prev["wall_s"])
        except (OSError, ValueError, KeyError, TypeError):
            pass
    env = dict(os.environ, OMP_NUM_THREADS="1", OPENBLAS_NUM_THREADS="1")
    env.update(MKL_NUM_THREADS="1", OMP_STACKSIZE="2G")
    argv = [exe, "in.xyz", "--gfn", "2", "--opt"]
    if cinp is not None:
        argv += ["--input", "c.inp"]
    argv += ["--chrg", str(chrg), "--uhf", str(uhf), "--cycles", str(cyc)]
    proc = None
    tick = time.monotonic()
    try:
        proc = subprocess.run(argv, cwd=work, capture_output=True, text=True, env=env)
        out = (proc.stdout or "") + (proc.stderr or "")
    except (OSError, subprocess.SubprocessError) as exc:
        out = f"xTB launch failed: {exc}"
    wall = time.monotonic() - tick
    (work / "xtb.out").write_text(out, encoding="utf-8")
    found = re.findall(r"TOTAL ENERGY\s+([+-]?\d+(?:\.\d+)?)\s*Eh", out)
    conv = (
        proc is not None
        and proc.returncode == 0
        and _CONV in out
        and (work / "xtbopt.xyz").is_file()
    )
    energy = float(found[-1]) if found else None
    payload = {"in_sha256": digest, "energy_eh": energy, "converged": conv, "wall_s": wall}
    done.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")
    return energy, conv, wall


def main(argv: list[str] | None = None) -> int:
    """Run the DG search pipeline; return a process exit code."""
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        els, ref, metal, donors, pairs, forbid, frag, src = _validated(args)
    except ValueError as exc:
        parser.error(str(exc))
    n = len(els)
    scale, shape = args.bond_scale, args.shape
    xtp = args.xtb or os.environ.get("CONFFLOW_XTB", "") or "xtb"
    exe = _xtb(xtp)
    if exe is None:
        sys.stderr.write(f"error: xTB executable not found: {xtp!r}\n")
        return 1
    try:
        proc = subprocess.run([exe, "--version"], capture_output=True, text=True, timeout=60)
        out = proc.stdout + proc.stderr
    except (OSError, subprocess.SubprocessError):
        out = ""
    version = next((line.strip() for line in out.splitlines() if line.strip()), "")
    try:
        graph, spec, _ = build_topology(els, ref, (metal, donors, pairs, forbid, scale), shape)
    except ValueError as exc:
        sys.stderr.write(f"error: invalid topology: {exc}\n")
        return 1
    sites = [s.id for s in spec.binding_sites] if spec is not None else []
    classes: list[Any] = [None]
    group: Any = None
    if metal is not None:
        try:
            pipe = coordination.enumeration.enumerate_targets(spec, shape)
        except ValueError as exc:
            sys.stderr.write(f"error: enumeration failed: {exc}\n")
            return 1
        classes = list(pipe["shape_classes"])
        group = coordination.shapes.proper_rotation_group(shape)
    ctx: Ctx = (ref, els, metal, donors, sites, shape, pairs, scale)
    work = Path(args.workdir)
    work.mkdir(parents=True, exist_ok=True)
    restrained = ([(metal, d) for d in donors] if metal is not None else []) + list(pairs)
    cinp: str | None = None
    if restrained:
        lines = ["$constrain", "  force constant=1.0"]
        for i, j in restrained:
            lines.append(f"  distance: {i + 1}, {j + 1}, {_dist(ref, i, j):.5f}")
        cinp = "\n".join(lines + ["$end"]) + "\n"
    cfg = (els, cinp, args.charge, args.uhf, args.max_cycles)
    targets: list[dict[str, Any]] = []
    structures: list[dict[str, Any]] = []
    cmds: dict[str, tuple[int, ...]] = {}
    jobs: list[tuple[str, int, np.ndarray, Path, bool]] = []
    centers: tuple[int, ...] = ()
    skipped = (
        []
        if metal is not None
        else ["coordination_class", "metal_donor_distance", "donor_orientation"]
    )
    frag_charges = tuple(frag)
    for t, cls in enumerate(classes):
        tid = f"t{t:02d}"
        placement = [] if cls is None else [int(v) for v in cls.representative]
        if cls is not None:
            cmds[tid] = coordination.enumeration.canonical_representative(placement, group)
        mode = args.small_ring_torsions
        n_on = (args.count + 1) // 2 if mode == "both" else (args.count if mode == "on" else 0)
        # Seed formula: on-call seed = seed * 1000 + 2 * target + 1, off-call
        # seed = seed * 1000 + 2 * target + 2; start numbering is contiguous.
        calls: list[tuple[int, int, bool]] = []
        if n_on:
            calls.append((n_on, args.seed * 1000 + 2 * t + 1, True))
        if args.count - n_on:
            calls.append((args.count - n_on, args.seed * 1000 + 2 * t + 2, False))
        starts: list[np.ndarray] = []
        flags: list[bool] = []
        err: str | None = None
        for need, rseed, on in calls:
            setting = DGSeedSettings(
                need, rseed, on, timeout_seconds=args.embed_timeout, fragment_charges=frag_charges
            )
            try:
                made = generate_dg_seeds(graph, spec, ref, placement, shape or "", setting)
            except DGSeedError as exc:
                if err is None:
                    err = str(exc)
                continue
            if not centers and getattr(made, "stereo_centers", ()):
                centers = tuple(int(c) for c in made.stereo_centers)
            starts += [np.asarray(s, dtype=float) for s in made.coords]
            flags += [on] * len(made.coords)
        rec: dict[str, Any] = {"id": tid, "placement": placement, "generated": len(starts)}
        tp = []
        if cls is not None:
            at = {v: s for s, v in enumerate(placement)}
            for ends in coordination.shapes.get_shape(shape).trans_pairs:
                i, j = sorted(ends)
                tp.append(sorted((sites[at[i]], sites[at[j]])))
            tp.sort()
        rec.update(trans_pairs=tp, relaxed=0, passed=0, error=err)
        targets.append(rec)
        jobs += [
            (tid, k, s, work / f"{tid}_s{k:02d}", on)
            for k, (s, on) in enumerate(zip(starts, flags))
        ]
    with futures.ThreadPoolExecutor(max_workers=args.cores) as pool:
        outs = list(pool.map(lambda j: _relax(exe, j[3], j[2], cfg), jobs))
    passing: list[tuple[float, str, int, np.ndarray]] = []
    for (tid, k, _xyz, here, on), (e, c, w) in zip(jobs, outs):
        opt = _first_frame(here / "xtbopt.xyz", n) if c else None
        bad = (
            ["converged"] if opt is None else audit_checks(opt, ctx, cmds.get(tid, ()), c, centers)
        )
        item = targets[int(tid[1:])]
        item["relaxed"] = int(item["relaxed"]) + (opt is not None)
        item["passed"] = int(item["passed"]) + (not bad)
        if not bad:
            assert opt is not None
            passing.append((float(e or 0.0), tid, k, opt))
        entry = {"target": tid, "start": k, "energy_eh": e, "passed": not bad}
        entry.update(failed_checks=bad, wall_s=w, small_ring_torsions=on, skipped_checks=skipped)
        structures.append(entry)
    passing.sort(key=lambda row: (row[0], row[1], row[2]))
    settings = dict(vars(args), xtb=xtp, input=src)
    summary = {"settings": settings, "targets": targets, "structures": structures}
    summary["xtb"] = {"executable": exe, "version": version}
    summary["fragment_charges"] = [{"atom": a + 1, "charge": q} for a, q in frag]
    totals = {"targets": len(targets), "generated": len(structures), "passed": len(passing)}
    totals["relaxed"] = sum(int(t["relaxed"]) for t in targets)
    summary["totals"] = totals
    text = json.dumps(summary, sort_keys=True, indent=2) + "\n"
    Path(args.summary).write_text(text, encoding="utf-8")
    if not passing:
        sys.stderr.write("error: no structure passed all audit checks\n")
        return 2
    low = passing[0][0]
    frames = []
    for e, tid, k, xyz in passing:
        tag = f"target={tid} start={k} energy_eh={e:.6f} rel_kcal={(e - low) * HARTREE_TO_KCAL:.3f}"
        frames.append((xyz, tag))
    _write_xyz(Path(args.out), els, frames)
    return 0


if __name__ == "__main__":
    sys.exit(main())
