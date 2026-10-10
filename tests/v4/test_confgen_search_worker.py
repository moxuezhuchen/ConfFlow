#!/usr/bin/env python3

"""Confgen search worker tests: job round trip, exits, resume, SIGTERM."""

from __future__ import annotations

import json
import os
import re
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import numpy as np
import pytest

from confflow.domain.structure import StructureRecord
from confflow.execution import confgen_search_run as search_run
from confflow.execution import confgen_search_worker as worker
from confflow.science.confgen import search
from confflow.science.confgen.coordination.stage import axis_spec_from_lane_spec
from tests.v4._dg_search_helpers import _SHAPE, _embed, _toy, _write_fake, build_topology

_VALID_SETTINGS = {
    "starts": 1,
    "seed": 7,
    "small_ring_torsions": "both",
    "embed_timeout_seconds": 20,
    "fragment_charges": [],
    "bond_scale": 1.25,
}


def _section(spec) -> dict[str, Any] | None:
    """Return the resolved coordination section round trip for *spec*."""
    return None if spec is None else dict(axis_spec_from_lane_spec(spec))


def _job(
    tmp: Path,
    els: list[str],
    ref: np.ndarray,
    sel: tuple[Any, ...],
    shape: str | None,
    fake: Path,
    tag: str = "",
    settings_over: dict[str, Any] | None = None,
    **over: Any,
) -> tuple[str, Path, Path, Path]:
    """Write one worker job file; return (job, workdir, structures, summary)."""
    topo, spec, _ = build_topology(els, ref, sel, shape)
    job = {
        "atoms": list(els),
        "reference": [[float(v) for v in row] for row in ref],
        "graph": topo.to_mapping(convention="internal0"),
        "coordination": _section(spec),
        "shape": shape,
        "charge": 0,
        "uhf": 0,
        "settings": {**_VALID_SETTINGS, **(settings_over or {})},
        "max_cycles": 1000,
        "xtb": str(fake),
        "cores": 2,
        "workdir": str(tmp / f"work{tag}"),
        "structures_file": str(tmp / f"structures{tag}.xyz"),
        "summary_file": str(tmp / f"summary{tag}.json"),
    }
    job.update(over)
    path = tmp / f"job{tag}.json"
    path.write_text(json.dumps(job), encoding="utf-8")
    return str(path), tmp / f"work{tag}", tmp / f"structures{tag}.xyz", tmp / f"summary{tag}.json"


def _metal_parts(tmp: Path, tag: str = "") -> tuple[Any, ...]:
    """Return _job positional args for the square-planar toy complex."""
    els, ref = _toy()
    return (els, ref, (0, [1, 2, 3, 4], [(1, 3)], [], 1.25), _SHAPE, _write_fake(tmp), tag)


def _stub(monkeypatch: pytest.MonkeyPatch, ref: np.ndarray) -> None:
    """Stub DG seeds to the reference geometry (fast, deterministic)."""

    def _fake(*args: Any, **kwargs: Any) -> SimpleNamespace:
        need = args[-1].count if args else 1
        return SimpleNamespace(coords=(np.asarray(ref),) * max(need, 1), stereo_centers=())

    monkeypatch.setattr(search, "generate_dg_seeds", _fake)


def test_exit_zero_metal(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    els, ref = _toy()
    _stub(monkeypatch, ref)
    monkeypatch.setenv("FAKE_XTB_RECORD", str(tmp_path / "record.jsonl"))
    monkeypatch.setenv("FAKE_XTB_PAD", "1")
    job, work, out, summary_path = _job(tmp_path, *_metal_parts(tmp_path))
    assert worker.main([job]) == 0
    assert (work / "t00_s00" / "xtb.out").stat().st_size > 100000
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    assert summary["totals"]["generated"] == 3
    assert summary["totals"]["passed"] >= 1
    assert [t["id"] for t in summary["targets"]] == ["t00", "t01", "t02"]
    assert summary["xtb"] == {
        "executable": str(tmp_path / "fake_xtb.py"),
        "version": "fake xtb 99.9",
    }
    assert summary["settings"]["starts"] == 1 and summary["settings"]["seed"] == 7
    energies = [r["energy_eh"] for r in summary["structures"] if r["passed"]]
    assert energies == sorted(energies)
    text = out.read_text(encoding="utf-8")
    assert [float(v) for v in re.findall(r"energy_eh=(\S+)", text)] == energies
    cinp = next(work.glob("t*_s00/c.inp")).read_text(encoding="utf-8")
    assert cinp.startswith("$constrain\n  force constant=1.0\n") and cinp.endswith("$end\n")
    for donor in (2, 3, 4, 5):
        assert (
            f"  distance: 1, {donor}, {float(np.linalg.norm(ref[0] - ref[donor - 1])):.5f}" in cinp
        )
    assert f"  distance: 2, 4, {float(np.linalg.norm(ref[1] - ref[3])):.5f}" in cinp
    rows = [json.loads(line) for line in (tmp_path / "record.jsonl").read_text().splitlines()]
    assert len(rows) == summary["totals"]["generated"]
    tail = ["in.xyz", "--gfn", "2", "--opt", "--input", "c.inp", "--chrg", "0", "--uhf", "0"]
    for row in rows:
        assert row["argv"] == [str(tmp_path / "fake_xtb.py")] + tail + ["--cycles", "1000"]
        assert ",".join(f"{k}={v}" for k, v in sorted(row["env"].items())) == (
            "MKL_NUM_THREADS=1,OMP_NUM_THREADS=1,OMP_STACKSIZE=2G,OPENBLAS_NUM_THREADS=1"
        )


def test_metal_free_no_restraint_argv(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    mol, els, ref = _embed("C[C@H]1CC[C@@H](O)C1")
    _stub(monkeypatch, ref)
    monkeypatch.setenv("FAKE_XTB_RECORD", str(tmp_path / "record.jsonl"))
    fake = _write_fake(tmp_path)
    job, work, _, summary_path = _job(tmp_path, els, ref, (None, [], [], [], 1.25), None, fake)
    assert worker.main([job]) == 0
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    assert [t["id"] for t in summary["targets"]] == ["t00"]
    assert not list(work.glob("t*_s00/c.inp"))
    rows = [json.loads(line) for line in (tmp_path / "record.jsonl").read_text().splitlines()]
    assert rows and all("--input" not in row["argv"] for row in rows)


def test_metal_free_real_dg(tmp_path: Path) -> None:
    mol, els, ref = _embed("C[C@H]1CC[C@@H](O)C1")
    fake = _write_fake(tmp_path)
    job, _, _, summary_path = _job(tmp_path, els, ref, (None, [], [(0, 1)], [], 1.25), None, fake)
    assert worker.main([job]) == 0
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    assert summary["totals"]["passed"] >= 1
    assert all(
        tuple(r["skipped_checks"])
        == ("coordination_class", "metal_donor_distance", "donor_orientation")
        for r in summary["structures"]
    )


def test_resume_skips_xtb(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    els, ref = _toy()
    _stub(monkeypatch, ref)
    monkeypatch.setenv("FAKE_XTB_COUNT", str(tmp_path / "count.txt"))
    job, work, _, _ = _job(tmp_path, *_metal_parts(tmp_path))
    assert worker.main([job]) == 0
    first = (tmp_path / "count.txt").read_text(encoding="utf-8").count("x")
    assert first == 3 and (work / "t00_s00" / "done.json").is_file()
    assert worker.main([job]) == 0
    assert (tmp_path / "count.txt").read_text(encoding="utf-8").count("x") == first
    (work / "t00_s00" / "done.json").write_text("corrupt", encoding="utf-8")
    assert worker.main([job]) == 0
    assert (tmp_path / "count.txt").read_text(encoding="utf-8").count("x") == first + 1


def test_exit_two_modes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    els, ref = _toy()
    _stub(monkeypatch, ref)
    base = _metal_parts(tmp_path)
    monkeypatch.setenv("FAKE_XTB_MODE", "noconverge")
    monkeypatch.setenv("FAKE_XTB_COUNT", str(tmp_path / "count.txt"))
    job, _, out, summary_path = _job(tmp_path, *base[:5], tag="n")
    assert worker.main([job]) == 2
    launches = (tmp_path / "count.txt").read_text(encoding="utf-8").count("x")
    assert launches == 3
    assert worker.main([job]) == 2
    assert (tmp_path / "count.txt").read_text(encoding="utf-8").count("x") == launches
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    assert not out.exists()
    assert summary["totals"] == {"generated": 3, "passed": 0, "relaxed": 0, "targets": 3}
    assert all(r["failed_checks"] == ["converged"] for r in summary["structures"])
    monkeypatch.setenv("FAKE_XTB_MODE", "crash")
    job, _, _, summary_path = _job(tmp_path, *base[:5], tag="c")
    assert worker.main([job]) == 2
    crashed = json.loads(summary_path.read_text(encoding="utf-8"))
    assert all(r["energy_eh"] is None for r in crashed["structures"])
    monkeypatch.setenv("FAKE_XTB_MODE", "break")
    job, _, _, summary_path = _job(tmp_path, *base[:5], tag="b")
    assert worker.main([job]) == 2
    broken = json.loads(summary_path.read_text(encoding="utf-8"))
    assert all("topology" in r["failed_checks"] for r in broken["structures"])


def test_fragment_charge_roundtrip(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    els, ref = _toy()
    _stub(monkeypatch, ref)
    monkeypatch.setenv("FAKE_XTB_MODE", "noconverge")
    over = {"fragment_charges": [[1, 0]]}
    job, _, _, summary_path = _job(tmp_path, *_metal_parts(tmp_path), settings_over=over)
    assert worker.main([job]) == 2
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    assert summary["fragment_charges"] == [{"atom": 2, "charge": 0}]


def test_exit_three(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    good = _job(tmp_path, *_metal_parts(tmp_path))[0]
    base = json.loads(Path(good).read_text(encoding="utf-8"))

    def _bad(tag: str, **over: Any) -> str:
        settings_over = over.pop("settings_over", None)
        bad = dict(base)
        bad.update(over)
        if settings_over is not None:
            bad["settings"] = {**_VALID_SETTINGS, **settings_over}
        path = tmp_path / f"bad{tag}.json"
        path.write_text(json.dumps(bad), encoding="utf-8")
        return str(path)

    cases = [
        str(tmp_path / "missing.json"),
        _bad("a", atoms=[]),
        _bad("b", atoms=["Xx"]),
        _bad("c", reference=[[0.0, 0.0]]),
        _bad("d", graph={"bonds": [[1, 99]]}),
        _bad("e", coordination={"metal_center": "x"}),
        _bad("f", coordination={"metal_center": 0, "binding_sites": []}),
        _bad("g", shape=None),
        _bad("h", shape="octahedral"),
        _bad("i", settings={"starts": 0}),
        _bad("j", settings_over={"starts": True}),
        _bad("k", settings_over={"small_ring_torsions": "x"}),
        _bad("l", settings_over={"bond_scale": "x"}),
        _bad("m", settings_over={"bond_scale": 0}),
        _bad("n", settings_over={"fragment_charges": [[0]]}),
        _bad("o", settings_over={"fragment_charges": [[99, 0]]}),
        _bad("p", settings=None),
        _bad("q", charge=True),
        _bad("r", xtb="/nonexistent/xtb"),
        _bad("s", xtb="definitely-not-on-path-xtb"),
        _bad("t", cores=0),
        _bad("u", workdir=""),
        _bad("v", reference=[["a", "b", "c"]] * 11),
        _bad("w", graph=[]),
        _bad("x", coordination=[]),
        _bad("y", coordination=None, shape="square_planar"),
        _bad("z", settings_over={"fragment_charges": {}}),
    ]
    for path in cases:
        assert worker.main([path]) == 3, path
        assert "error" in capsys.readouterr().err
    assert worker.main([]) == 3
    path = tmp_path / "text.json"
    path.write_text("not json", encoding="utf-8")
    assert worker.main([str(path)]) == 3
    path.write_text("[1, 2]", encoding="utf-8")
    assert worker.main([str(path)]) == 3


def test_small_helpers(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _write_fake(tmp_path)
    assert worker._resolve_xtb(str(fake)) == str(fake)
    assert worker._resolve_xtb("/nonexistent/xtb") is None
    monkeypatch.setattr(worker.shutil, "which", lambda name: "/usr/bin/xtb")
    assert worker._resolve_xtb("xtb") == "/usr/bin/xtb"
    els, ref = _toy()
    xyz = tmp_path / "r.xyz"
    worker._write_xyz(xyz, els, [(ref, "frame")])
    assert worker._first_frame(xyz, len(els)) is not None
    assert worker._first_frame(xyz, len(els) + 1) is None
    xyz.write_text("garbage", encoding="utf-8")
    assert worker._first_frame(xyz, len(els)) is None
    monkeypatch.setattr(
        worker.subprocess, "run", lambda *a, **k: (_ for _ in ()).throw(OSError("nope"))
    )
    assert worker._xtb_version(str(fake)) == ""


def test_launch_failure_is_exit_two(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    els, ref = _toy()
    _stub(monkeypatch, ref)
    job, _, out, summary_path = _job(tmp_path, *_metal_parts(tmp_path))
    monkeypatch.setattr(
        worker.subprocess, "Popen", lambda *a, **k: (_ for _ in ()).throw(OSError("nope"))
    )
    assert worker.main([job]) == 2
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    assert summary["totals"]["passed"] == 0 and not out.exists()


def test_request_stop_exits_nonzero(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    els, ref = _toy()
    _stub(monkeypatch, ref)
    monkeypatch.setenv("FAKE_XTB_MODE", "sleep")
    monkeypatch.setenv("FAKE_XTB_PIDS", str(tmp_path / "pids.txt"))
    (tmp_path / "pids.txt").write_text("", encoding="utf-8")
    job, _, _, _ = _job(tmp_path, *_metal_parts(tmp_path), cores=1)

    def _fire() -> None:
        deadline = time.time() + 120.0
        while time.time() < deadline and not (tmp_path / "pids.txt").read_text().strip():
            time.sleep(0.1)
        worker._handle_sigterm(signal.SIGTERM, None)
        worker._handle_sigterm(signal.SIGTERM, None)

    timer = threading.Thread(target=_fire, daemon=True)
    timer.start()
    code = worker.main([job])
    timer.join(timeout=60.0)
    assert code == 143
    for line in (tmp_path / "pids.txt").read_text().splitlines():
        with pytest.raises(ProcessLookupError):
            os.kill(int(line.strip()), 0)
    probe = subprocess.Popen(["true"])
    probe.wait()
    worker._LIVE.add(probe)
    worker._handle_sigterm(signal.SIGTERM, None)
    worker._LIVE.discard(probe)


def test_sigterm_subprocess(tmp_path: Path) -> None:
    job, work, _, _ = _job(tmp_path, *_metal_parts(tmp_path, tag="k"), cores=1)
    env = dict(
        os.environ,
        FAKE_XTB_SLOW_AFTER="1",
        FAKE_XTB_STATE_DIR=str(tmp_path),
        FAKE_XTB_PIDS=str(tmp_path / "pids.txt"),
    )
    proc = subprocess.Popen(
        [sys.executable, "-m", "confflow.execution.confgen_search_worker", job], env=env
    )
    try:
        deadline = time.time() + 240.0
        while time.time() < deadline:
            try:
                if len((tmp_path / "pids.txt").read_text().splitlines()) >= 2:
                    break
            except OSError:
                pass
            if proc.poll() is not None:
                break
            time.sleep(0.2)
        assert proc.poll() is None, "worker exited before SIGTERM"
        proc.send_signal(signal.SIGTERM)
        code = proc.wait(timeout=120.0)
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait()
    assert code != 0
    pids = [int(line.strip()) for line in (tmp_path / "pids.txt").read_text().splitlines()]
    assert len(pids) >= 2
    for pid in pids:
        with pytest.raises(ProcessLookupError):
            os.kill(pid, 0)
    finished = json.loads((work / "t00_s00" / "done.json").read_text(encoding="utf-8"))
    assert finished["converged"] is True
    interrupted = work / "t01_s00" / "done.json"
    if interrupted.is_file():
        assert json.loads(interrupted.read_text(encoding="utf-8"))["converged"] is False


def test_equal_energy_keeps_frame_metadata(tmp_path: Path) -> None:
    """Equal-energy passing structures keep their own target/start.

    The xyz file order is the publication order: each frame pairs with the
    passing record named by its own comment line, never by re-sorting.
    """
    driving = StructureRecord(
        id="d", atoms=("H", "H"), coordinates=((0.0, 0.0, 0.0), (0.0, 0.0, 0.74))
    )
    rows = [
        {"target": "t01", "start": 0, "energy_eh": -1.0, "passed": True, "failed_checks": []},
        {"target": "t00", "start": 1, "energy_eh": -1.0, "passed": True, "failed_checks": []},
    ]
    totals = {"targets": 2, "generated": 2, "passed": 2, "relaxed": 2}
    xyz = (
        "2\ntarget=t01 start=0 energy_eh=-1.000000 rel_kcal=0.000\nH 0 0 0\nH 0 0 0.74\n"
        "2\ntarget=t00 start=1 energy_eh=-1.000000 rel_kcal=0.000\nH 1 0 0\nH 1 0 0.74\n"
    )
    rundir = tmp_path / "tie"
    rundir.mkdir(exist_ok=True)
    summary = {"targets": [], "structures": rows, "fragment_charges": [], "totals": totals}
    (rundir / "summary.json").write_text(json.dumps(summary), encoding="utf-8")
    (rundir / "structures.xyz").write_text(xyz, encoding="utf-8")
    _, _, ordered, frames = search_run._read_validated(driving, rundir)
    assert [(r["target"], r["start"]) for r in ordered] == [("t01", 0), ("t00", 1)]
    assert frames[0].coordinates[0][0] == 0.0 and frames[1].coordinates[0][0] == 1.0
    (rundir / "structures.xyz").write_text(
        xyz.replace("target=t00", "target=t99"), encoding="utf-8"
    )
    with pytest.raises(search_run._ImportError):
        search_run._read_validated(driving, rundir)
    (rundir / "summary.json").write_text("{invalid", encoding="utf-8")
    with pytest.raises(search_run._ImportError):
        search_run._read_validated(driving, rundir)
    (rundir / "summary.json").write_text(json.dumps(summary), encoding="utf-8")
    (rundir / "structures.xyz").write_text(xyz.replace("-1.000000", "abc"), encoding="utf-8")
    with pytest.raises(search_run._ImportError):
        search_run._read_validated(driving, rundir)
    bad_row = dict(rows[0], energy_eh="abc")
    bad_summary = dict(summary, structures=[bad_row, rows[1]])
    (rundir / "summary.json").write_text(json.dumps(bad_summary), encoding="utf-8")
    (rundir / "structures.xyz").write_text(xyz, encoding="utf-8")
    with pytest.raises(search_run._ImportError):
        search_run._read_validated(driving, rundir)
