#!/usr/bin/env python3
"""Registered DG-search script: fake-xTB runs, CLI errors, resume, step integration."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import re
import stat
import sys
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from confflow.execution.xyz_import import import_xyz
from confflow.science.confgen import search

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "confgen_dg_search.py"
_SHAPE = "square_planar"


def _load():
    spec = importlib.util.spec_from_file_location("confgen_dg_search", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


DG = _load()

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
if mode == "crash":
    sys.stderr.write("fake xtb exploded\\n")
    sys.exit(3)
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
    path = tmp_path / "fake_xtb.py"
    path.write_text(_FAKE.replace("@EXE@", sys.executable), encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP)
    return path


def _toy() -> tuple[list[str], np.ndarray]:
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
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(f"{len(els)}\ntoy\n")
        for sym, (x, y, z) in zip(els, xyz):
            handle.write(f"{sym} {x:.6f} {y:.6f} {z:.6f}\n")


def _embed(smiles):
    from rdkit import Chem
    from rdkit.Chem import rdDistGeom

    mol = Chem.AddHs(Chem.MolFromSmiles(smiles))
    params = rdDistGeom.ETKDGv3()
    params.randomSeed = 42
    assert rdDistGeom.EmbedMolecule(mol, params) == 0
    els = [atom.GetSymbol() for atom in mol.GetAtoms()]
    return mol, els, np.array(mol.GetConformer().GetPositions(), dtype=float)


def _free_args(tmp: Path, xyz: Path, fake: Path, *extra: str, tag: str = "") -> list[str]:
    return (
        ["--input", str(xyz), "--count", "2", "--seed", "1", "--cores", "2", "--xtb", str(fake)]
        + ["--workdir", str(tmp / f"work{tag}"), "--out", str(tmp / f"structures{tag}.xyz")]
        + ["--summary", str(tmp / f"summary{tag}.json"), *extra]
    )


def _base_args(tmp: Path, xyz: Path, fake: Path, *extra: str, tag: str = "") -> list[str]:
    return (
        ["--input", str(xyz), "--metal", "1", "--donors", "2,3,4,5"]
        + ["--shape", _SHAPE, "--forming", "2-4", "--count", "1"]
        + ["--seed", "1", "--cores", "2", "--xtb", str(fake)]
        + ["--workdir", str(tmp / f"work{tag}"), "--out", str(tmp / f"structures{tag}.xyz")]
        + ["--summary", str(tmp / f"summary{tag}.json"), *extra]
    )


def _stub(monkeypatch: pytest.MonkeyPatch, ref: np.ndarray) -> None:
    monkeypatch.setattr(
        search, "generate_dg_seeds", lambda *a, **k: SimpleNamespace(coords=(np.asarray(ref),))
    )


def test_end_to_end(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    els, ref = _toy()
    xyz = tmp_path / "toy.xyz"
    _write_xyz(xyz, els, ref)
    fake = _write_fake(tmp_path)
    monkeypatch.setenv("FAKE_XTB_RECORD", str(tmp_path / "record.jsonl"))
    assert DG.main(_base_args(tmp_path, xyz, fake)) == 0
    out, summary_path = tmp_path / "structures.xyz", tmp_path / "summary.json"
    found = import_xyz(out.read_text(encoding="utf-8"), source_name="dg")
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    assert summary["totals"]["passed"] == len(found) >= 1
    assert summary["totals"]["generated"] == len(summary["structures"])
    assert summary["totals"]["passed"] <= summary["totals"]["relaxed"]
    assert summary["totals"]["relaxed"] == sum(t["relaxed"] for t in summary["targets"])
    energies = [r["energy_eh"] for r in summary["structures"] if r["passed"]]
    assert energies == sorted(energies)
    text = out.read_text(encoding="utf-8")
    assert [float(m) for m in re.findall(r"energy_eh=(\S+)", text)] == energies
    assert summary["xtb"] == {"executable": str(fake), "version": "fake xtb 99.9"}
    assert [t["id"] for t in summary["targets"]] == ["t00", "t01", "t02"]
    cinp = next((tmp_path / "work").glob("t*_s00/c.inp")).read_text(encoding="utf-8")
    assert cinp.startswith("$constrain\n  force constant=1.0\n") and cinp.endswith("$end\n")
    for donor in (2, 3, 4, 5):
        want = float(np.linalg.norm(ref[0] - ref[donor - 1]))
        assert f"  distance: 1, {donor}, {want:.5f}" in cinp
    assert f"  distance: 2, 4, {float(np.linalg.norm(ref[1] - ref[3])):.5f}" in cinp
    rec_text = (tmp_path / "record.jsonl").read_text(encoding="utf-8")
    rows = [json.loads(line) for line in rec_text.splitlines()]
    assert len(rows) == summary["totals"]["generated"]
    tail = ["in.xyz", "--gfn", "2", "--opt", "--input", "c.inp"]
    tail += ["--chrg", "0", "--uhf", "0", "--cycles", "1000"]
    for row in rows:
        got_env = sorted(f"{k}={v}" for k, v in row["env"].items())
        assert row["argv"] == [str(fake)] + tail
        assert ",".join(got_env) == (
            "MKL_NUM_THREADS=1,OMP_NUM_THREADS=1," "OMP_STACKSIZE=2G,OPENBLAS_NUM_THREADS=1"
        )


def test_resume_skips_xtb(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    els, ref = _toy()
    xyz = tmp_path / "toy.xyz"
    _write_xyz(xyz, els, ref)
    fake = _write_fake(tmp_path)
    monkeypatch.setenv("FAKE_XTB_COUNT", str(tmp_path / "count.txt"))
    _stub(monkeypatch, ref)
    args = _base_args(tmp_path, xyz, fake)
    assert DG.main(args) == 0
    first = (tmp_path / "count.txt").read_text(encoding="utf-8").count("x")
    assert first == 3 and (tmp_path / "work" / "t00_s00" / "done.json").is_file()
    assert DG.main(args) == 0
    assert (tmp_path / "count.txt").read_text(encoding="utf-8").count("x") == first


def test_xtb_failures(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    els, ref = _toy()
    xyz = tmp_path / "toy.xyz"
    _write_xyz(xyz, els, ref)
    fake = _write_fake(tmp_path)
    _stub(monkeypatch, ref)
    monkeypatch.setenv("FAKE_XTB_MODE", "noconverge")
    assert DG.main(_base_args(tmp_path, xyz, fake)) == 2
    summary = json.loads((tmp_path / "summary.json").read_text(encoding="utf-8"))
    assert not (tmp_path / "structures.xyz").exists()
    assert summary["totals"] == {"generated": 3, "passed": 0, "relaxed": 0, "targets": 3}
    assert all(r["failed_checks"] == ["converged"] for r in summary["structures"])
    monkeypatch.setenv("FAKE_XTB_MODE", "crash")
    assert DG.main(_base_args(tmp_path, xyz, fake, "--workdir", str(tmp_path / "wc"))) == 2
    crashed = json.loads((tmp_path / "summary.json").read_text(encoding="utf-8"))
    assert all(r["energy_eh"] is None for r in crashed["structures"])
    assert all(r["failed_checks"] == ["converged"] for r in crashed["structures"])
    monkeypatch.setenv("FAKE_XTB_MODE", "break")
    assert DG.main(_base_args(tmp_path, xyz, fake, "--workdir", str(tmp_path / "wb"))) == 2
    broken = json.loads((tmp_path / "summary.json").read_text(encoding="utf-8"))
    assert all("topology" in r["failed_checks"] for r in broken["structures"])


def test_cli_errors(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    els, ref = _toy()
    xyz = tmp_path / "toy.xyz"
    _write_xyz(xyz, els, ref)
    fake = _write_fake(tmp_path)
    assert DG.main(_base_args(tmp_path, xyz, fake, "--xtb", "/nonexistent/xtb")) == 1
    assert "not found" in capsys.readouterr().err
    for extra in (
        ("--metal", "99"),
        ("--shape", "nope"),
        ("--shape", "octahedral"),
        ("--donors", ""),
        ("--donors", "1,2,3,4"),
        ("--donors", "2,2,3,4"),
        ("--forbid-trans", "2-6"),
        ("--count", "0"),
        ("--bond-scale", "0"),
        ("--embed-timeout", "-1"),
        ("--fragment-charge", "2"),
        ("--fragment-charge", "2:x"),
    ):
        with pytest.raises(SystemExit) as exc:
            DG.main(_base_args(tmp_path, xyz, fake, *extra))
        assert exc.value.code == 2
    two = tmp_path / "two.xyz"
    two.write_bytes(xyz.read_bytes() + xyz.read_bytes())
    with pytest.raises(ValueError, match="one frame"):
        DG._read_xyz(str(two))
    with pytest.raises(ValueError, match="cannot read"):
        DG._read_xyz(str(tmp_path / "missing.xyz"))
    with pytest.raises(ValueError, match="covalent radius"):
        DG.perceive_bond_set(np.zeros((1, 3)), ["Xx"], 0, [], 1.25)
    assert DG._xtb("/nonexistent/xtb") is None
    assert DG._xtb(str(fake)) == str(fake)
    assert DG._xtb("definitely-not-on-path-xyz") is None
    assert DG._first_frame(xyz, 11) is not None
    assert DG._first_frame(xyz, 5) is None


def test_step_integration(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from confflow.domain import FrozenDict, StructureSet
    from confflow.domain.resources import ResourceRequest
    from confflow.domain.structure import StructureRecord
    from confflow.domain.work_item import WorkItem, WorkItemInputs, make_work_item_id
    from confflow.execution.process import NativeProcessSupervisor
    from confflow.execution.script_executor import ScriptExecutor
    from confflow.execution.work_item_executor import ItemExecutionContext
    from confflow.workflow.v4.document import ScientificDefaults, ScientificDefinition

    els, ref = _toy()
    fake = _write_fake(tmp_path)
    cmd = json.dumps([sys.executable, str(SCRIPT)])
    server = tmp_path / "server.toml"
    text = f"total_cores = 8\ntotal_memory = '8GB'\n\n[scripts.dg]\ncommand = {cmd}\n"
    server.write_text(text, encoding="utf-8")
    monkeypatch.setenv("CONFFLOW_SERVER_CONFIG", str(server))
    monkeypatch.setenv("CONFFLOW_SERVER_STATE_DIR", str(tmp_path / "state"))
    xyz = tuple(tuple(map(float, row)) for row in ref)
    record = StructureRecord(id="s-toy", atoms=tuple(els), coordinates=xyz)
    named = WorkItemInputs(structures=FrozenDict({"structure": StructureSet.of(record)}))
    res = ResourceRequest(cores_per_item=1, memory_per_item_bytes=1024**3)
    item = WorkItem(
        id=make_work_item_id("dg-item"),
        logical_key="dg-item",
        step_id="s1",
        named_inputs=named,
        resources=res,
        semantic_digest="sha256:" + hashlib.sha256(b"dg-item").hexdigest(),
        ordinal=0,
    )
    sargs = ("{input}", "--metal", "1", "--donors", "2,3,4,5", "--shape", _SHAPE, "--xtb")
    sargs += (str(fake), "--count", "1", "--workdir", "dgwork", "--out")
    sargs += ("structures.xyz", "--summary", "summary.json")
    sout = FrozenDict({"structures": "structures.xyz", "summary": "summary.json"})
    sci = ScientificDefinition(script_id="dg", script_args=sargs, script_outputs=sout)
    context = ItemExecutionContext(
        step_id="s1",
        scientific=sci,
        scientific_defaults=ScientificDefaults(),
        adapter=None,
        profile=None,
        supervisor=NativeProcessSupervisor(),
        run_root=str(tmp_path / "run"),
    )
    result = ScriptExecutor().execute(item, context)
    assert result.status.value == "completed", [str(d.message) for d in result.diagnostics]
    assert len(result.structures) >= 1 and len(result.results) == 1
    assert result.results[0].value["totals"]["passed"] >= 1


def test_metal_free_end_to_end(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    mol, els, ref = _embed("C[C@H]1CC[C@@H](O)C1")
    xyz = tmp_path / "org.xyz"
    _write_xyz(xyz, els, ref)
    fake = _write_fake(tmp_path)
    assert DG.main(_free_args(tmp_path, xyz, fake, "--forming", "1-2")) == 0
    summary = json.loads((tmp_path / "summary.json").read_text(encoding="utf-8"))
    assert [t["id"] for t in summary["targets"]] == ["t00"]
    want_skipped = ("coordination_class", "metal_donor_distance", "donor_orientation")
    for row in summary["structures"]:
        assert tuple(row["skipped_checks"]) == want_skipped
        assert not {"coordination_class", "metal_donor_distance"} & set(row["failed_checks"])
    cinp = (tmp_path / "work" / "t00_s00" / "c.inp").read_text(encoding="utf-8")
    assert cinp.count("distance:") == 1 and "distance: 1, 2," in cinp
    monkeypatch.setenv("FAKE_XTB_RECORD", str(tmp_path / "record.jsonl"))
    assert DG.main(_free_args(tmp_path, xyz, fake, tag="2")) == 0
    text = (tmp_path / "record.jsonl").read_text(encoding="utf-8")
    rows = [json.loads(line) for line in text.splitlines()]
    assert rows and all("--input" not in row["argv"] for row in rows)
    for extra in ("--metal 1", "--donors 1,2", "--shape x", "--forbid-trans 1-2"):
        pytest.raises(SystemExit, DG.main, _free_args(tmp_path, xyz, fake, *extra.split()))
