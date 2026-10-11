#!/usr/bin/env python3

"""Shared DG-search fixtures: toy complex, writers, fake xTB, topology builder.

Moved verbatim from the retired ``tests/v4/test_confgen_dg_search_script.py``
so the pure-module search tests keep their fixtures; the worker and step
tests reuse the fake-xTB harness and builders.
"""

from __future__ import annotations

import stat
import sys
from pathlib import Path
from typing import Any

import numpy as np

from confflow.science.bonding import covalent_radius
from confflow.science.confgen import graph
from confflow.science.confgen.graph import ForbiddenTrans
from confflow.science.data import get_atomic_number

_SHAPE = "square_planar"
_POL = "REJECTED_BY_POLICY"

_FAKE = """\
#!@EXE@
import math, os, re, sys
def read_xyz(path):
    lines = open(path).read().splitlines()
    n = int(lines[0].strip())
    rows = [r.split() for r in lines[2:2 + n]]
    return [r[0] for r in rows], [[float(v) for v in r[1:4]] for r in rows]
if "--version" in sys.argv:
    print("fake xtb 99.9")
    sys.exit(0)
mode = os.environ.get("FAKE_XTB_MODE", "ok")
if os.environ.get("FAKE_XTB_RECORD"):
    import json
    data = (json.dumps({"argv": sys.argv, "env": {k: os.environ.get(k) for k in (
        "OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS", "OMP_STACKSIZE")}})
        + "\\n").encode()
    fd = os.open(os.environ["FAKE_XTB_RECORD"], os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o644)
    os.write(fd, data)
    os.close(fd)
if os.environ.get("FAKE_XTB_COUNT"):
    open(os.environ["FAKE_XTB_COUNT"], "a").write("x\\n")
if os.environ.get("FAKE_XTB_PIDS"):
    open(os.environ["FAKE_XTB_PIDS"], "a").write(str(os.getpid()) + "\\n")
slow_after = os.environ.get("FAKE_XTB_SLOW_AFTER")
if slow_after is not None:
    marker = os.path.join(os.environ.get("FAKE_XTB_STATE_DIR", "."), "launches.txt")
    open(marker, "a").write("x\\n")
    if open(marker).read().count("x") > int(slow_after):
        import time
        time.sleep(120)
if mode == "crash":
    sys.stderr.write("fake xtb exploded\\n")
    sys.exit(3)
if mode == "sleep":
    import time
    time.sleep(60)
if os.environ.get("FAKE_XTB_PAD"):
    print("PAD " + "x" * 200000)
els, xyz = read_xyz(sys.argv[1])
limits = []
try:
    cinp_lines = open("c.inp")
except OSError:
    cinp_lines = []
for line in cinp_lines:
    m = re.match(r"\\s*distance:\\s*(\\d+),\\s*(\\d+),\\s*([\\d.]+)", line)
    if m:
        limits.append((int(m.group(1)) - 1, int(m.group(2)) - 1, float(m.group(3))))
for _ in range(50):
    for i, j, d in reversed(limits):
        dx = [xyz[j][k] - xyz[i][k] for k in range(3)]
        cur = math.sqrt(sum(v * v for v in dx)) or 1.0
        for k in range(3):
            xyz[j][k] = xyz[i][k] + dx[k] / cur * d
if mode == "break":
    xyz[5][0] += 2.0
with open("xtbopt.xyz", "w") as f:
    f.write(f"{len(els)}\\nrelaxed\\n")
    for e, p in zip(els, xyz):
        f.write(f"{e} {p[0]:.6f} {p[1]:.6f} {p[2]:.6f}\\n")
e = -100.0 + (sum(p[0] for p in xyz) % 1.0)
print(f"cycle... TOTAL ENERGY {e:.6f} Eh ...")
if mode != "noconverge":
    print("GEOMETRY OPTIMIZATION CONVERGED")
"""


def _write_fake(tmp_path: Path) -> Path:
    """Write the fake-xTB executable into *tmp_path* and return its path."""
    path = tmp_path / "fake_xtb.py"
    path.write_text(_FAKE.replace("@EXE@", sys.executable), encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP)
    return path


def _toy() -> tuple[list[str], np.ndarray]:
    """Return the square-planar Pt toy complex elements and reference."""
    els, pos = ["Pt"], [np.zeros(3)]
    donors = [("N", (2.0, 0.0, 0.0)), ("N", (-2.0, 0.0, 0.0))]
    donors += [("Cl", (0.0, 2.0, 0.0)), ("Cl", (0.0, -2.0, 0.0))]
    for el, plas in donors:
        els.append(el)
        pos.append(np.array(plas))
    for slot in (1, 2):
        axis = pos[slot] / np.linalg.norm(pos[slot])
        e1 = np.cross(axis, (0.0, 0.0, 1.0))
        e1 /= np.linalg.norm(e1)
        e2 = np.cross(axis, e1)
        for k in range(3):
            ang = 2.0 * np.pi * k / 3.0
            hdir = 0.5 * axis + 0.866 * (np.cos(ang) * e1 + np.sin(ang) * e2)
            els.append("H")
            pos.append(pos[slot] + 1.02 * hdir / np.linalg.norm(hdir))
    return els, np.array(pos)


def _write_xyz(path: Path, els: list[str], xyz: np.ndarray) -> None:
    """Write one xyz frame."""
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(f"{len(els)}\ntoy\n")
        for sym, (x, y, z) in zip(els, xyz):
            handle.write(f"{sym} {x:.6f} {y:.6f} {z:.6f}\n")


def _embed(smiles):
    """Embed one RDKit molecule; return (mol, elements, reference)."""
    from rdkit import Chem
    from rdkit.Chem import rdDistGeom

    mol = Chem.AddHs(Chem.MolFromSmiles(smiles))
    params = rdDistGeom.ETKDGv3()
    params.randomSeed = 42
    assert rdDistGeom.EmbedMolecule(mol, params) == 0
    els = [atom.GetSymbol() for atom in mol.GetAtoms()]
    return mol, els, np.array(mol.GetConformer().GetPositions(), dtype=float)


def perceive_bond_set(xyz, els, metal, fp, scale) -> set[tuple[int, int]]:
    """Perceive covalent bonds skipping the metal and forbidden pairs."""
    radii = []
    for sym in els:
        radius = covalent_radius(get_atomic_number(sym))
        if radius is None:
            raise ValueError(f"element {sym!r} has no usable covalent radius")
        radii.append(float(radius))
    skip = {(min(a, b), max(a, b)) for a, b in fp}
    bonds: set[tuple[int, int]] = set()
    for i in range(len(els)):
        if i == metal:
            continue
        for j in range(i + 1, len(els)):
            if j == metal or (i, j) in skip:
                continue
            if float(np.linalg.norm(xyz[i] - xyz[j])) < scale * (radii[i] + radii[j]):
                bonds.add((i, j))
    return bonds


def build_topology(els, ref, sel, shape) -> tuple[Any, ...]:
    """Build the script-style typed graph, spec, and reference bond set."""
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
    site_of = {d: f"{els[d]}{d + 1}" for d in donors}
    cons = []
    for k, (a, b) in enumerate(forbid):
        cons.append(ForbiddenTrans(f"F{k:02d}", (site_of[a], site_of[b]), _POL, "cli:forbid-trans"))
    order = [graph.BindingSite(site_of[d], "atom", (d,), 1) for d in donors]
    spec = graph.CoordinationSpec(metal, tuple(order), (shape,), "enumerate", tuple(cons))
    return topo, spec, ref_bonds
