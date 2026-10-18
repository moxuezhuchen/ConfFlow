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
from confflow.execution import confgen_xtb as xtbmod
from confflow.science.confgen import search
from tests.v4._dg_search_helpers import _SHAPE, _embed, _toy, _write_fake, build_topology

_VALID_SETTINGS = {
    "budget": 12,
    "window_kcal": 6.0,
    "arrangement": "keep",
    "release": True,
    "starts": 1,
    "seed": 7,
    "small_ring_torsions": "both",
    "embed_timeout_seconds": 20,
    "fragment_charges": [],
    "bond_scale": 1.25,
}


def _section(spec) -> dict[str, Any] | None:
    """Return the trimmed v4 coordination section for *spec*."""
    if spec is None:
        return None
    return {
        "metal_center": int(spec.metal_center),
        "binding_sites": [
            {
                "id": site.id,
                "kind": site.kind,
                "atoms": [int(a) for a in site.atoms],
                "hapticity": int(site.hapticity),
            }
            for site in spec.binding_sites
        ],
        "shapes": [str(name) for name in spec.shapes],
        "constraints": [
            {
                "id": item.id,
                "kind": "FORBIDDEN_TRANS",
                "sites": [str(item.sites[0]), str(item.sites[1])],
                "classification": str(item.classification),
                "provenance": str(item.provenance),
            }
            for item in spec.constraints
        ],
    }


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
    over = {"arrangement": "enumerate"}
    job, work, out, summary_path = _job(tmp_path, *_metal_parts(tmp_path), settings_over=over)
    assert worker.main([job]) == 0
    assert any(p.stat().st_size > 100000 for p in work.rglob("xtb.out"))
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    assert 1 <= summary["search"]["relaxations"] <= 12  # the budget caps the search phase
    assert summary["totals"]["passed"] >= 1
    assert [t["id"] for t in summary["targets"]] == ["t00", "t01", "t02"]
    assert (
        summary["search"]["relaxations"] + summary["search"]["release_relaxations"]
        == summary["totals"]["generated"]
    )
    assert summary["xtb"] == {
        "executable": str(tmp_path / "fake_xtb.py"),
        "version": "fake xtb 99.9",
    }
    assert summary["settings"]["starts"] == 1 and summary["settings"]["seed"] == 7
    baseline = json.loads((work / "input_baseline.json").read_text())
    assert baseline["reference"] == {"coordinates": ref.tolist(), "energy_eh": None}
    assert baseline["baseline"]["status"] == "released"
    assert baseline["baseline"]["search_record"]["passed"]
    assert baseline["baseline"]["final_record"]["passed"]
    assert baseline["baseline"]["comparable_to_final"] is True
    assert baseline["baseline"]["lowest_final_delta_kcal"] <= 1e-8
    assert baseline["backend"]["version"] == "fake xtb 99.9"
    energies = [r["energy_eh"] for r in summary["structures"] if r["passed"]]
    assert energies == sorted(energies)
    text = out.read_text(encoding="utf-8")
    assert [float(v) for v in re.findall(r"energy_eh=(\S+)", text)] == energies
    tags = re.findall(r"target=(\S+) start=(\d+) ", text)
    assert {target for target, _ in tags} <= {"t00", "t01", "t02", "unclassified"}
    starts = [int(start) for _, start in tags]
    assert len(set(starts)) == len(starts) == summary["totals"]["passed"]
    assert len(re.findall(r" actual=(t\d\d|unclassified)\n", text)) == len(tags)
    cinps = "".join(p.read_text(encoding="utf-8") for p in work.rglob("c.inp"))
    assert cinps.startswith("$constrain\n  force constant=1.0\n") and "$end\n" in cinps
    for donor in (2, 3, 4, 5):
        assert (
            f"  distance: 1, {donor}, {float(np.linalg.norm(ref[0] - ref[donor - 1])):.5f}" in cinps
        )
    assert f"  distance: 2, 4, {float(np.linalg.norm(ref[1] - ref[3])):.5f}" in cinps
    rows = [json.loads(line) for line in (tmp_path / "record.jsonl").read_text().splitlines()]
    assert len(rows) >= summary["totals"]["generated"]
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
    sel = (None, [], [(0, 1)], [], 1.25)
    job, _, _, summary_path = _job(tmp_path, els, ref, sel, None, fake, settings_over={"starts": 4})
    assert worker.main([job]) == 0
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    assert summary["totals"]["passed"] >= 1
    assert summary["structures"] and all(r["target"] == "t00" for r in summary["structures"])
    assert all(r["passed"] is True and r["failed_checks"] == [] for r in summary["structures"])


def test_one_call_budget_optimizes_input_and_persists_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A seeded worker retains the submitted input even when DG has no budget."""
    els, ref = _toy()

    def no_dg(*args: Any, **kwargs: Any) -> Any:
        pytest.fail("a one-call search must spend its budget on the input")

    monkeypatch.setattr(search, "generate_starts", no_dg)
    job, work, out, summary_path = _job(
        tmp_path,
        *_metal_parts(tmp_path),
        settings_over={"budget": 1, "release": False},
        cores=1,
    )
    assert worker.main([job]) == 0
    summary = json.loads(summary_path.read_text())
    checkpoint = json.loads((work / "checkpoint.json").read_text())
    assert summary["search"]["relaxations"] == 1
    assert summary["totals"]["passed"] == 1
    assert checkpoint["search"]["checkpoint_stage"] == "restrained"
    assert checkpoint["structures"][0]["passed"]
    assert np.allclose(checkpoint["coordinates"][0], ref, atol=2e-5)
    assert out.exists()
    inputs = list(work.rglob("in.xyz"))
    assert len(inputs) == 1
    rows = inputs[0].read_text().splitlines()[2:]
    submitted = np.array([[float(v) for v in row.split()[1:4]] for row in rows])
    assert np.allclose(submitted, ref, atol=1e-6)
    baseline = json.loads((work / "input_baseline.json").read_text())["baseline"]
    assert baseline["comparable_to_final"] is True
    assert baseline["search_record"]["coordinates"] == baseline["final_record"]["coordinates"]


def test_resume_skips_xtb(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    els, ref = _toy()
    _stub(monkeypatch, ref)
    monkeypatch.setenv("FAKE_XTB_COUNT", str(tmp_path / "count.txt"))
    job, work, _, _ = _job(tmp_path, *_metal_parts(tmp_path))
    assert worker.main([job]) == 0
    first = (tmp_path / "count.txt").read_text(encoding="utf-8").count("x")
    dones = sorted(work.rglob("done.json"))
    assert first >= 1 and len(dones) == first
    assert worker.main([job]) == 0
    assert (tmp_path / "count.txt").read_text(encoding="utf-8").count("x") == first
    dones[0].write_text("corrupt", encoding="utf-8")
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
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    assert 1 <= launches <= 12 and summary["totals"]["generated"] == launches
    assert (
        summary["totals"]["passed"] == 0 and summary["totals"]["relaxed"] == 0 and not out.exists()
    )
    assert summary["structures"] == []
    assert summary["search"]["failures"] == {"backend_failed": launches}
    assert worker.main([job]) == 2
    assert (tmp_path / "count.txt").read_text(encoding="utf-8").count("x") == launches
    monkeypatch.setenv("FAKE_XTB_MODE", "crash")
    job, _, _, summary_path = _job(tmp_path, *base[:5], tag="c")
    assert worker.main([job]) == 2
    crashed = json.loads(summary_path.read_text(encoding="utf-8"))
    assert crashed["totals"]["passed"] == 0 and crashed["structures"] == []
    assert crashed["search"]["failures"].get("backend_failed", 0) == crashed["totals"]["generated"]
    monkeypatch.setenv("FAKE_XTB_MODE", "break")
    job, _, _, summary_path = _job(tmp_path, *base[:5], tag="b")
    assert worker.main([job]) == 2
    broken = json.loads(summary_path.read_text(encoding="utf-8"))
    assert broken["totals"]["passed"] == 0
    assert broken["search"]["failures"].get("topology", 0) >= 1


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
    monkeypatch.setattr(xtbmod.shutil, "which", lambda name: "/usr/bin/xtb")
    assert xtbmod.resolve_xtb("xtb") == "/usr/bin/xtb"
    els, ref = _toy()
    xyz = tmp_path / "r.xyz"
    xtbmod.write_xyz_frames(xyz, els, [(ref, "frame")])
    xyz.write_text("garbage", encoding="utf-8")
    assert xtbmod.read_first_frame(xyz, len(els)) is None
    monkeypatch.setattr(
        xtbmod.subprocess, "run", lambda *a, **k: (_ for _ in ()).throw(OSError("nope"))
    )
    assert xtbmod.xtb_version(str(fake)) == ""


def test_launch_failure_is_exit_two(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    els, ref = _toy()
    _stub(monkeypatch, ref)
    job, _, out, summary_path = _job(tmp_path, *_metal_parts(tmp_path))
    monkeypatch.setattr(
        xtbmod.subprocess, "Popen", lambda *a, **k: (_ for _ in ()).throw(OSError("nope"))
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
    cancelled: list[bool] = []
    worker._ACTIVE.append(SimpleNamespace(cancel=lambda: cancelled.append(True)))  # type: ignore[arg-type]
    try:
        worker._handle_sigterm(signal.SIGTERM, None)
    finally:
        worker._ACTIVE.clear()
    assert cancelled == [True]


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
    finished = [json.loads(p.read_text(encoding="utf-8")) for p in work.rglob("done.json")]
    assert any(item["converged"] is True for item in finished)


def test_broken_generated_starts_preserve_valid_input(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Broken DG starts are rejected while the valid optimized input remains publishable."""
    els, ref = _toy()
    broken = np.array(ref, copy=True)
    broken[5] += (3.0, 0.0, 0.0)

    def _broken(*args: Any, **kwargs: Any) -> SimpleNamespace:
        return SimpleNamespace(coords=(broken,) * args[-1].count, stereo_centers=())

    monkeypatch.setattr(search, "generate_dg_seeds", _broken)
    # one core: DG runs in this process, so the patched seeder is the one used
    job, _, out, summary_path = _job(tmp_path, *_metal_parts(tmp_path), cores=1)
    assert worker.main([job]) == 0
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    assert summary["totals"]["passed"] >= 1 and out.exists()
    assert summary["search"]["input_seed"]["status"] == "accepted"
    assert summary["totals"]["generated"] >= 1
    assert summary["search"]["failures"].get("topology", 0) >= 1


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("budget", 0),
        ("window_kcal", 0),
        ("window_kcal", 0.0),
        ("arrangement", "x"),
        ("release", "yes"),
        ("adaptive", "yes"),
    ],
)
def test_new_search_settings_exit_three(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], key: str, value: Any
) -> None:
    job, _, _, _ = _job(tmp_path, *_metal_parts(tmp_path), settings_over={key: value})
    assert worker.main([job]) == 3
    assert key in capsys.readouterr().err


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


def test_failed_release_is_warned(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    els, ref = _toy()
    _stub(monkeypatch, ref)
    real = worker.explore

    def _failed(*args: Any, **kwargs: Any) -> Any:
        run = real(*args, **kwargs)
        run.stats["release"] = "failed"
        return run

    monkeypatch.setattr(worker, "explore", _failed)
    job, _, _, summary_path = _job(tmp_path, *_metal_parts(tmp_path))
    assert worker.main([job]) == 0
    assert json.loads(summary_path.read_text(encoding="utf-8"))["search"]["release"] == "failed"
    assert "warning: every release relaxation failed" in capsys.readouterr().err


def test_checkpoint_survives_interrupted_search(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A cancelled search retains one coherent audited snapshot, without publishing a final set."""
    els, ref = _toy()
    record = search.SearchRecord("t00", 1, False, -1.0, True, coords=ref)
    snapshot = search.SearchRun(
        records=(record,),
        passing=(record,),
        stats={"checkpoint": True, "release": "pending", "relaxations": 1},
    )

    def interrupted(*args: Any, **kwargs: Any) -> Any:
        kwargs["checkpoint"](snapshot)
        raise search.SearchCancelled("test interruption")

    monkeypatch.setattr(worker, "explore", interrupted)
    job, work, out, summary = _job(tmp_path, *_metal_parts(tmp_path))
    assert worker.main([job]) == 143
    saved = json.loads((work / "checkpoint.json").read_text())
    assert saved["final"] is False
    assert saved["search"]["release"] == "pending"
    assert saved["structures"][0]["passed"] is True
    assert saved["coordinates"] == [ref.tolist()]
    assert saved["atoms"] == els
    assert not out.exists() and not summary.exists()
    assert not (work / "checkpoint.json.tmp").exists()


def test_raw_input_artifact_survives_cancel_before_optimization(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def cancel(*args: Any, **kwargs: Any) -> Any:
        raise search.SearchCancelled("before first relaxation")

    monkeypatch.setattr(worker, "explore", cancel)
    job, work, out, summary = _job(tmp_path, *_metal_parts(tmp_path))
    assert worker.main([job]) == 143
    artifact = json.loads((work / "input_baseline.json").read_text())
    raw = json.loads(Path(job).read_text())
    assert artifact["reference"] == {"coordinates": raw["reference"], "energy_eh": None}
    assert artifact["graph"] == raw["graph"]
    assert artifact["parameters"]["settings"] == raw["settings"]
    assert artifact["baseline"] is None
    assert artifact["execution_status"] == "cancelled"
    assert not out.exists() and not summary.exists()


def test_valid_input_baseline_survives_cancel_after_first_checkpoint(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    real = worker.explore

    def cancel_after_input(*args: Any, **kwargs: Any) -> Any:
        publish = kwargs["checkpoint"]

        def checkpoint(snapshot: search.SearchRun) -> None:
            publish(snapshot)
            raise search.SearchCancelled("after evaluated input baseline")

        kwargs["checkpoint"] = checkpoint
        return real(*args, **kwargs)

    monkeypatch.setattr(worker, "explore", cancel_after_input)
    job, work, out, summary = _job(tmp_path, *_metal_parts(tmp_path), cores=1)
    assert worker.main([job]) == 143
    artifact = json.loads((work / "input_baseline.json").read_text())
    assert artifact["execution_status"] == "cancelled"
    saved = artifact["baseline"]
    assert saved["search_record"]["passed"] is True
    assert saved["search_record"]["energy_eh"] is not None
    assert saved["search_record"]["coordinates"]
    assert saved["final_record"] is None
    checkpoint = json.loads((work / "checkpoint.json").read_text())
    assert checkpoint["input_baseline"]["search_record"] == saved["search_record"]
    assert not out.exists() and not summary.exists()


@pytest.mark.parametrize("limit", [0, -1, True, float("nan"), "240"])
def test_worker_rejects_invalid_time_limit(tmp_path: Path, limit: Any) -> None:
    job, *_ = _job(tmp_path, *_metal_parts(tmp_path), time_limit_seconds=limit)
    with pytest.raises(worker._JobError, match="time_limit_seconds"):
        worker._load_job(job)
