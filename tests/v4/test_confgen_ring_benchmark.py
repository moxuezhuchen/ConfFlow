#!/usr/bin/env python3
"""R7 benchmark CLI + published-source node tests (DRAFT, test-only).

CLI tests invoke docs/confgen-fix/tools/ring_benchmark.py as a
subprocess (no mirror CP implementation here). Published-source tests
import benchmark_case and inject mock engines: empty leaves must yield
0 seeds / 0 recall (root counterexample), and dropping one leaf must
shrink the seed set (proving seeds come from run.leaves, not stage
candidates).
"""

from __future__ import annotations

import csv
import importlib.util
import json
import subprocess
import sys
import types
from pathlib import Path

import numpy as np

TOOL = Path(__file__).resolve().parents[2] / "docs" / "confgen-fix" / "tools" / "ring_benchmark.py"
MATCHER = (
    Path(__file__).resolve().parents[2] / "docs" / "confgen-fix" / "tools" / "match_after_opt.py"
)
FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "confgen" / "ring"


def _run_case(tmp: Path, system: str, input_mode: str, forms_mode: str) -> dict:
    outdir = tmp / f"{system}-{input_mode}-{forms_mode}"
    proc = subprocess.run(
        [
            sys.executable,
            str(TOOL),
            "--system",
            system,
            "--input-mode",
            input_mode,
            "--forms-mode",
            forms_mode,
            "--fixtures",
            str(FIXTURES),
            "--outdir",
            str(outdir),
        ],
        capture_output=True,
        text=True,
        timeout=600,
    )
    assert proc.returncode == 0, f"tool failed: {proc.stdout}\n{proc.stderr}"
    for name in [
        "seeds.xyz",
        "cp_table.csv",
        "reference_match.csv",
        "failures.json",
        "run_meta.json",
        "MANIFEST.json",
    ]:
        assert (outdir / name).is_file(), f"missing {name}"
    summary = json.loads(proc.stdout.strip().splitlines()[-1])
    return {"outdir": outdir, "summary": summary, "stderr": proc.stderr}


def _load_tool_module():
    spec = importlib.util.spec_from_file_location("ring_benchmark_v2", str(TOOL))
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_benchmark_cli_mch_crest_first_explicit_recall_6of6(tmp_path: Path) -> None:
    """CLI node: mch crest-first explicit must recall 6/6 (root verified)."""
    res = _run_case(tmp_path, "methylcyclohexane", "crest-first", "explicit")
    assert res["summary"]["published"] == 38
    assert res["summary"]["recall"] == "6/6"
    with open(res["outdir"] / "reference_match.csv") as f:
        rows = list(csv.DictReader(f))
    assert len(rows) == 6
    assert all(int(r["hit_lt15"]) == 1 for r in rows)
    meta = json.loads((res["outdir"] / "run_meta.json").read_text())
    assert meta["verdict_qgate"] in ("PASS", "REVIEW-nearband-advisory")
    assert meta["energy_unit"].startswith("Hartree")
    assert meta["seeds_from"].startswith("ConfgenEngine.run().leaves")


def test_benchmark_cli_mch_crest_first_default_limit_5of6(tmp_path: Path) -> None:
    """CLI node: mch crest-first default must report the B_4 limit (5/6)."""
    res = _run_case(tmp_path, "methylcyclohexane", "crest-first", "default")
    assert res["summary"]["recall"] == "5/6"


def test_tool_has_single_published_source(tmp_path: Path) -> None:
    """Static guard: no second solving pass; seeds only from run.leaves."""
    src = TOOL.read_text()
    assert "realize(" not in src, "tool must not call stage.realize (root counterexample)"
    assert "ConfgenEngine" in src and ".run(" in src


def test_empty_leaves_yields_zero_seeds_zero_recall(tmp_path: Path) -> None:
    """Root counterexample: published=0 must give 0 seeds / 0 recall."""
    mod = _load_tool_module()
    from confflow.science.confgen.engine import ConfgenEngine

    class _Empty(ConfgenEngine):
        def run(self, context, should_cancel=None):
            run = super().run(context, should_cancel=should_cancel)
            return types.SimpleNamespace(
                leaves=(), target_records=run.target_records, report_json=run.report_json
            )

    outdir = tmp_path / "empty"
    summary = mod.benchmark_case(
        "thf", "crest-first", "explicit", str(FIXTURES), str(outdir), engine_factory=_Empty
    )
    assert summary["published"] == 0
    assert summary["recall"] == "0/1"
    text = (outdir / "seeds.xyz").read_text().strip()
    assert text == "", "no published leaves must mean an empty seed file"
    with open(outdir / "reference_match.csv") as f:
        rows = list(csv.DictReader(f))
    assert rows and all(int(r["hit_lt15"]) == 0 for r in rows)


def test_observer_restores_after_exception(tmp_path: Path) -> None:
    """Observer finally: a raising engine run still restores the entry point."""
    mod = _load_tool_module()
    from confflow.core.io import read_xyz_file
    from confflow.domain.structure import StructureRecord
    from confflow.science.confgen.model import build_context
    from confflow.science.confgen.ring.stage import RingStage

    fr = read_xyz_file(str(FIXTURES / "thf" / "input.xyz"), strict=True)[0]
    rec = StructureRecord(
        id="obs-neg",
        atoms=tuple(fr["atoms"]),
        coordinates=tuple(map(tuple, fr["coords"])),
        charge=0,
        multiplicity=1,
    )
    ctx = build_context(rec, {"index_base": 0, "rings": [{"id": "r1", "atoms": [0, 1, 2, 3, 4]}]})
    orig = RingStage.realize

    class _Boom:
        def run(self, _ctx):
            raise RuntimeError("boom")

    import pytest

    with pytest.raises(RuntimeError):
        mod._run_with_audit_capture(_Boom(), ctx)
    assert RingStage.realize is orig


def test_observer_preserves_report_bytes(tmp_path: Path) -> None:
    """Small real comparison: report bytes identical with/without observer."""
    mod = _load_tool_module()
    from confflow.core.io import read_xyz_file
    from confflow.domain.structure import StructureRecord
    from confflow.science.confgen.engine import ConfgenEngine
    from confflow.science.confgen.model import build_context
    from confflow.science.confgen.ring.stage import RingStage

    fr = read_xyz_file(str(FIXTURES / "thf" / "input.xyz"), strict=True)[0]
    rec = StructureRecord(
        id="obs-bytes",
        atoms=tuple(fr["atoms"]),
        coordinates=tuple(map(tuple, fr["coords"])),
        charge=0,
        multiplicity=1,
    )
    ctx = build_context(rec, {"index_base": 0, "rings": [{"id": "r1", "atoms": [0, 1, 2, 3, 4]}]})
    plain_run = ConfgenEngine().run(ctx)
    observed_run, captured = mod._run_with_audit_capture(ConfgenEngine(), ctx)
    assert RingStage.realize is not None
    import json as _json

    assert _json.dumps(plain_run.report_json(), sort_keys=True, default=str) == _json.dumps(
        observed_run.report_json(), sort_keys=True, default=str
    )
    assert len(captured) == len(plain_run.target_records) == 20
    published_ids = {
        t.target_id
        for t in observed_run.target_records
        if str(getattr(t, "status", "")).endswith("PUBLISHED_LEAF")
    }
    assert published_ids and all(tid in captured for tid in published_ids)
    sample = next(iter(captured.values())).evidence[0]
    assert "conjugation_planarity" in dict(sample) and "ring_angle_drift" in dict(sample)


def test_drop_one_leaf_shrinks_seed_set(tmp_path: Path) -> None:
    """Seeds track run.leaves exactly: dropping one leaf drops one seed."""
    mod = _load_tool_module()
    from confflow.science.confgen.engine import ConfgenEngine

    class _DropOne(ConfgenEngine):
        def run(self, context, should_cancel=None):
            run = super().run(context, should_cancel=should_cancel)
            assert len(run.leaves) > 1
            return types.SimpleNamespace(
                leaves=tuple(run.leaves[1:]),
                target_records=run.target_records,
                report_json=run.report_json,
            )

    outdir = tmp_path / "drop"
    summary = mod.benchmark_case(
        "thf", "crest-first", "explicit", str(FIXTURES), str(outdir), engine_factory=_DropOne
    )
    assert summary["published"] == 19
    assert summary["recall"] == "1/1"


def test_benchmark_cli_rpdd_recall_3of3(tmp_path: Path) -> None:
    """CLI node: rpdd crest-first default recalls 3/3 (2 published seeds)."""
    res = _run_case(tmp_path, "rpdd", "crest-first", "default")
    assert res["summary"]["recall"] == "3/3"
    assert res["summary"]["audit_complete"] is True
    fails = json.loads((res["outdir"] / "failures.json").read_text())
    assert fails["failed"], "failed targets must keep concrete status/reason evidence"
    assert any("FAILED_GEOMETRY" in f["status"] for f in fails["failed"])
    assert any(f["reason"] for f in fails["failed"])
    audits = json.loads((res["outdir"] / "seed_audits.json").read_text())
    assert len(audits["audits"]) == res["summary"]["published"] == 2
    assert audits["audit_gaps"] == []
    for entry in audits["audits"]:
        audit = entry["solver_audit"]
        assert audit["conjugation_planarity"]["passed"] is True
        assert audit["ring_angle_drift"]["passed"] is True
        assert audit["puckering_amplitude"]["passed"] is True
        assert entry["audit_source"].startswith("same-run observer")


def test_benchmark_cli_synthetic_cyclohexane_2of2(tmp_path: Path) -> None:
    """CLI node: synthetic cyclohexane (NOT CREST) recalls its 2 chairs."""
    res = _run_case(tmp_path, "synthetic-cyclohexane", "crest-first", "default")
    assert res["summary"]["recall"] == "2/2"
    assert res["summary"]["audit_complete"] is True
    meta = json.loads((res["outdir"] / "run_meta.json").read_text())
    assert meta["reference_source"].startswith("synthetic")
    assert meta["driving_src"].startswith("synthetic")


def _write_xyz(path: Path, atoms: list[str], frames: list[np.ndarray], comment: str = "c") -> None:
    with open(path, "w") as f:
        for xyz in frames:
            f.write(f"{len(atoms)}\n{comment}\n")
            for el, (x, y, z) in zip(atoms, xyz):
                f.write(f"{el} {x:.6f} {y:.6f} {z:.6f}\n")


def _load_matcher():
    spec = importlib.util.spec_from_file_location("match_after_opt_v2", str(MATCHER))
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_matcher_rigid_transform_recalls_and_mirror_does_not(tmp_path: Path) -> None:
    """Proper Kabsch: rigid motion matches, mirror does not."""
    mod = _load_matcher()
    rng = np.random.default_rng(0)
    base = rng.normal(size=(6, 3))
    theta = np.array([[0.0, -1.0, 0.0], [1.0, 0.0, 0.0], [0.0, 0.0, 1.0]])
    moved = base @ theta.T + np.array([5.0, -3.0, 2.0])
    mirrored = base * np.array([-1.0, 1.0, 1.0])
    atoms = ["C"] * 6
    rp, sp, mp = tmp_path / "r.xyz", tmp_path / "s.xyz", tmp_path / "m.xyz"
    _write_xyz(rp, atoms, [base])
    _write_xyz(sp, atoms, [moved])
    _write_xyz(mp, atoms, [mirrored])
    res = mod.match(mod.read_xyz_frames(str(sp)), mod.read_xyz_frames(str(rp)), 0.5)
    assert res["recall"] == "1/1"
    res_m = mod.match(mod.read_xyz_frames(str(mp)), mod.read_xyz_frames(str(rp)), 0.01)
    assert res_m["recall"] == "0/1"


def test_matcher_redundancy_and_fail_closed(tmp_path: Path) -> None:
    """Redundancy counts extras; bad inputs refuse."""
    mod = _load_matcher()
    rng = np.random.default_rng(1)
    base = rng.normal(size=(4, 3))
    atoms = ["C"] * 4
    rp, sp = tmp_path / "r.xyz", tmp_path / "s.xyz"
    _write_xyz(rp, atoms, [base])
    _write_xyz(sp, atoms, [base, base + 1e-4, base * 2.0])
    res = mod.match(mod.read_xyz_frames(str(sp)), mod.read_xyz_frames(str(rp)), 0.5)
    assert res["recall"] == "1/1"
    assert res["refs"][0]["redundant_extra"] == 1
    assert res["unmatched_seeds"] == 1
    bad = tmp_path / "bad.xyz"
    bad.write_text("2\nc\nC 0 0 0\nH nan 0 0\n")
    try:
        mod.read_xyz_frames(str(bad))
    except ValueError:
        pass
    else:
        raise AssertionError("non-finite coords must fail closed")
    proc = subprocess.run(
        [
            sys.executable,
            str(MATCHER),
            "--seeds",
            str(sp),
            "--refs",
            str(rp),
            "--rmsd-cutoff",
            "-1",
            "-o",
            str(tmp_path / "o.csv"),
        ],
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert proc.returncode == 2
