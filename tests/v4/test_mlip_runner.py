"""Tests for the standalone UMA runner (confflow/execution/mlip_runner.py).

Selection, resume, scheduling and file output run in-process against a fake
backend whose energy is the first atom's x coordinate (eV). The real fairchem
backend is only exercised by the smoke test, which needs CONFFLOW_MLIP_PYTHON
and CONFFLOW_MLIP_MODEL_PATH and is skipped otherwise.
"""

from __future__ import annotations

import hashlib
import json
import multiprocessing as mp
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest

from confflow.execution import mlip_runner as mr

EV = mr.EV_PER_EH


class FakeBackend:
    """Energy = first x coordinate (eV). ``bad`` energies raise, ``nan`` energies are non-finite."""

    def __init__(self, bad: tuple[float, ...] = (), nan: tuple[float, ...] = ()) -> None:
        self.bad = set(bad)
        self.nan = set(nan)
        self.calls: list[tuple[str, float]] = []

    def energy(self, symbols: list[str], coords: list[list[float]]) -> tuple[float, int]:
        x = coords[0][0]
        self.calls.append(("sp", x))
        if x in self.bad:
            raise ValueError("boom")
        return (float("nan") if x in self.nan else x), 1

    def relax(
        self,
        symbols: list[str],
        coords: list[list[float]],
        fix_pairs: Any,
        optimizer: str,
        fmax: float,
        max_steps: int,
    ) -> dict[str, Any]:
        x = coords[0][0]
        self.calls.append(("relax", x))
        if x in self.bad:
            raise ValueError("relax boom")
        out = [[float(v) for v in c] for c in coords]
        out[-1][0] += 0.01  # moves the last atom, so pairs touching it change length
        return dict(coords=out, energy_eV=x, converged=True, n_steps=3, n_evals=5)


def structs(*specs: tuple[str, str | None, float]) -> list[dict[str, Any]]:
    """(key, label, energy_eV) -> two-atom structures with energy encoded in x."""
    return [
        dict(key=k, label=lab, symbols=["H", "H"], coords=[[e, 0.0, 0.0], [e + 1.0, 0.0, 0.0]])
        for k, lab, e in specs
    ]


def make_job(items: list[dict[str, Any]], **over: Any) -> dict[str, Any]:
    job: dict[str, Any] = dict(
        model_path="model.pt",
        refs_path=None,
        task="omol",
        device="cpu",
        charge=0,
        multiplicity=1,
        mode="sp",
        optimizer="lbfgs",
        fmax=0.02,
        max_steps=600,
        fix_pairs=[],
        pick=None,
        workers=1,
        threads=1,
        structures=items,
    )
    job.update(over)
    return job


def run(
    tmp_path: Path, job: dict[str, Any], backend: FakeBackend | None = None
) -> tuple[int, FakeBackend]:
    fake = backend or FakeBackend()
    return mr.run_job(job, str(tmp_path), lambda _job: fake), fake


def rows(tmp_path: Path) -> list[dict[str, Any]]:
    with open(tmp_path / mr.RESULTS, encoding="utf-8") as fh:
        return [json.loads(line) for line in fh if line.strip()]


def done(tmp_path: Path) -> dict[str, Any]:
    with open(tmp_path / mr.DONE, encoding="utf-8") as fh:
        return json.load(fh)


def test_sp_mode_rows(tmp_path: Path) -> None:
    rc, fake = run(tmp_path, make_job(structs(("a", None, 1.0), ("b", "x", -2.0))))
    assert rc == 0
    got = rows(tmp_path)
    assert [(r["key"], r["stage"]) for r in got] == [("a", "sp"), ("b", "sp")]
    assert got[1]["energy_eh"] == pytest.approx(-2.0 / EV)
    assert got[0]["coords"] is None and got[0]["converged"] is None
    assert got[0]["n_evals"] == 1 and got[0]["error"] is None and got[0]["optimizer"] is None
    assert len(fake.calls) == 2
    info = done(tmp_path)
    assert info["picked"] == [] and info["torch_threads"] == 1 and info["workers"] == 1
    assert info["model_sha256"] is None  # model.pt does not exist in tmp_path


def test_relax_mode_rows_with_fix_dev(tmp_path: Path) -> None:
    job = make_job(structs(("a", None, 1.0)), mode="relax", fix_pairs=[[0, 1]], optimizer="fire")
    rc, _ = run(tmp_path, job)
    assert rc == 0
    (row,) = rows(tmp_path)
    assert row["stage"] == "relax" and row["converged"] is True and row["n_steps"] == 3
    assert row["n_evals"] == 5 and row["optimizer"] == "fire"
    assert row["fix_max_dev"] == pytest.approx(0.01, abs=1e-9)
    assert row["energy_eh"] == pytest.approx(1.0 / EV)
    assert row["coords"][1][0] == pytest.approx(2.01)
    assert done(tmp_path)["picked"] == ["a"]


def test_relax_without_fix_pairs_has_no_dev(tmp_path: Path) -> None:
    run(tmp_path, make_job(structs(("a", None, 1.0)), mode="relax"))
    assert rows(tmp_path)[0]["fix_max_dev"] is None


def test_sp_then_relax_union_of_lowest_and_per_label(tmp_path: Path) -> None:
    items = structs(
        ("A", "a", 1.0),
        ("B", "a", 2.0),
        ("C", "a", 3.0),
        ("D", "b", 0.5),
        ("E", None, 4.0),
        ("F", None, 2.5),
    )
    job = make_job(items, mode="sp_then_relax", pick=dict(lowest=1, per_label=1))
    rc, fake = run(tmp_path, job)
    assert rc == 0
    # lowest overall: D; per label: a -> A, b -> D, None -> F
    assert done(tmp_path)["picked"] == ["D", "A", "F"]
    relaxed = sorted({r["key"] for r in rows(tmp_path) if r["stage"] == "relax"})
    assert relaxed == ["A", "D", "F"]
    assert sum(1 for c in fake.calls if c[0] == "relax") == 3


def test_sp_then_relax_ties_broken_by_key(tmp_path: Path) -> None:
    items = structs(("t2", "z", 9.0), ("t1", "z", 9.0), ("t3", "z", 9.5))
    run(tmp_path, make_job(items, mode="sp_then_relax", pick=dict(lowest=1, per_label=0)))
    assert done(tmp_path)["picked"] == ["t1"]


def test_sp_then_relax_skips_errored_sp(tmp_path: Path) -> None:
    items = structs(("bad", "a", -10.0), ("ok1", "a", 1.0), ("ok2", None, 2.0))
    job = make_job(items, mode="sp_then_relax", pick=dict(lowest=1, per_label=1))
    rc, _ = run(tmp_path, job, FakeBackend(bad=(-10.0,)))
    assert rc == 0
    assert done(tmp_path)["picked"] == ["ok1", "ok2"]
    errs = [r for r in rows(tmp_path) if r["error"]]
    assert [(r["key"], r["stage"]) for r in errs] == [("bad", "sp")]
    assert errs[0]["error"] == "ValueError: boom" and errs[0]["energy_eh"] is None


def test_pick_structures_zero_counts_and_missing_label_group() -> None:
    energies = {"p": (1.0, None), "q": (0.5, "x")}
    assert mr.pick_structures(energies, dict(lowest=0, per_label=0)) == []
    assert mr.pick_structures(energies, dict(lowest=0, per_label=1)) == ["q", "p"]


def test_resume_skips_existing_rows(tmp_path: Path) -> None:
    items = structs(("a", "x", 1.0), ("b", "x", 2.0))
    job = make_job(items, mode="sp_then_relax", pick=dict(lowest=1, per_label=0))
    run(tmp_path, job)
    first = rows(tmp_path)
    # a torn trailing line from an interrupted run must be ignored, and not glue onto new rows
    with open(tmp_path / mr.RESULTS, "a", encoding="utf-8") as fh:
        fh.write('5\n{"key": "a", "sta')
    rc, fake = run(
        tmp_path,
        make_job(
            structs(("a", "x", 1.0), ("b", "x", 2.0), ("c", "x", 3.0)),
            mode="sp_then_relax",
            pick=dict(lowest=1, per_label=0),
        ),
    )
    assert rc == 0 and fake.calls == [("sp", 3.0)]  # only c is new; a's relax already exists
    got = mr._read_rows(str(tmp_path / mr.RESULTS))
    assert got[: len(first)] == first and [r["key"] for r in got[len(first) :]] == ["c"]


def test_resume_recomputes_errored_rows(tmp_path: Path) -> None:
    job = make_job(structs(("a", None, -5.0), ("b", None, 1.0)))
    run(tmp_path, job, FakeBackend(bad=(-5.0,)))
    rc, fake = run(tmp_path, job, FakeBackend())
    assert rc == 0 and fake.calls == [("sp", -5.0)]
    ok = [r for r in rows(tmp_path) if r["key"] == "a"]
    assert ok[0]["error"] is not None and ok[1]["error"] is None


def test_non_finite_energy_is_error_row(tmp_path: Path) -> None:
    rc, _ = run(tmp_path, make_job(structs(("a", None, 3.0))), FakeBackend(nan=(3.0,)))
    assert rc == 0
    (row,) = rows(tmp_path)
    assert row["error"].startswith("ValueError: non-finite energy") and row["energy_eh"] is None


def test_relax_error_row_keeps_optimizer(tmp_path: Path) -> None:
    job = make_job(structs(("a", None, 7.0)), mode="relax", optimizer="precon_lbfgs")
    rc, _ = run(tmp_path, job, FakeBackend(bad=(7.0,)))
    assert rc == 0
    (row,) = rows(tmp_path)
    assert row["error"] == "ValueError: relax boom"
    assert row["optimizer"] == "precon_lbfgs" and row["converged"] is None


def test_backend_load_failure_gives_error_rows(tmp_path: Path) -> None:
    def broken(_job: dict[str, Any]) -> Any:
        raise OSError("no such model")

    rc = mr.run_job(make_job(structs(("a", None, 1.0), ("b", None, 2.0))), str(tmp_path), broken)
    assert rc == 0
    got = rows(tmp_path)
    assert len(got) == 2
    assert all("backend failed to load: OSError: no such model" in r["error"] for r in got)
    assert "start" in (tmp_path / mr.LOG).read_text(encoding="utf-8")


def test_log_has_start_done_and_end(tmp_path: Path) -> None:
    run(tmp_path, make_job(structs(("a", None, 1.0))))
    text = (tmp_path / mr.LOG).read_text(encoding="utf-8")
    assert "start: 1 structures" in text and "done a sp" in text and "end: wall_s=" in text


@pytest.mark.skipif("fork" not in mp.get_all_start_methods(), reason="needs fork start method")
def test_pool_workers_schedule_all_structures(tmp_path: Path) -> None:
    items = structs(*[(f"s{i}", "x" if i % 2 else None, float(i)) for i in range(6)])
    job = make_job(items, mode="sp_then_relax", pick=dict(lowest=1, per_label=2), workers=2)
    rc = mr.run_job(job, str(tmp_path), lambda _job: FakeBackend())
    assert rc == 0
    got = rows(tmp_path)
    assert len([r for r in got if r["stage"] == "sp"]) == 6
    assert all(r["error"] is None for r in got)
    assert done(tmp_path)["workers"] == 2 and done(tmp_path)["picked"] == ["s0", "s1", "s2", "s3"]


@pytest.mark.skipif("fork" not in mp.get_all_start_methods(), reason="needs fork start method")
def test_pool_load_failure_gives_error_rows(tmp_path: Path) -> None:
    def broken(_job: dict[str, Any]) -> Any:
        raise RuntimeError("cuda missing")

    job = make_job(structs(("a", None, 1.0), ("b", None, 2.0)), workers=2)
    assert mr.run_job(job, str(tmp_path), broken) == 0
    assert all("cuda missing" in r["error"] for r in rows(tmp_path))


def test_sha256_and_package_version(tmp_path: Path) -> None:
    model = tmp_path / "m.pt"
    model.write_bytes(b"weights")
    assert mr._sha256(str(model)) == hashlib.sha256(b"weights").hexdigest()
    assert mr._sha256(str(tmp_path / "missing.pt")) is None
    assert isinstance(mr._pkg_version("pytest"), str)
    assert mr._pkg_version("no-such-package-for-mlip-runner") is None


@pytest.mark.parametrize(
    "mutate",
    [
        lambda j: j.update(mode="bogus"),
        lambda j: j.update(optimizer="adam"),
        lambda j: j.update(workers=0),
        lambda j: j.update(threads=0),
        lambda j: j.update(charge=True),
        lambda j: j.update(fmax="0.02"),
        lambda j: j.update(model_path=3),
        lambda j: j.update(refs_path=5),
        lambda j: j.update(fix_pairs=[[0]]),
        lambda j: j.update(fix_pairs=[[-1, 0]]),
        lambda j: j.update(mode="sp_then_relax", pick=None),
        lambda j: j.update(pick={"lowest": -1}),
        lambda j: j.update(structures={"key": "a"}),
        lambda j: j["structures"][0].update(key=7),
        lambda j: j["structures"][0].update(symbols=["H"]),
        lambda j: j["structures"][0].update(symbols=[1, 2]),
        lambda j: j["structures"][0].update(coords=[[0.0, 0.0]] * 2),
        lambda j: j["structures"].append(dict(j["structures"][0])),
        lambda j: j["structures"][0].update(label=3),
        lambda j: j.update(structures=[{"key": "a", "symbols": ["H"], "coords": [[0, 0, 0]]}, 5]),
    ],
)
def test_invalid_job_rejected(mutate: Any) -> None:
    job = make_job(structs(("a", None, 1.0), ("b", "x", 2.0)))
    job["pick"] = dict(lowest=1, per_label=1)
    mutate(job)
    with pytest.raises(ValueError):
        mr.validate_job(job)


def test_main_rejects_bad_job_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: Any
) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "bad.json").write_text('{"mode": "bogus"}', encoding="utf-8")
    (tmp_path / "broken.json").write_text("{not json", encoding="utf-8")
    assert mr.main(["mlip_runner.py", "bad.json"]) == 2
    assert mr.main(["mlip_runner.py", "broken.json"]) == 2
    assert mr.main(["mlip_runner.py", "missing.json"]) == 2
    assert mr.main(["mlip_runner.py"]) == 2
    assert "cannot read job" in capsys.readouterr().err


def test_main_runs_valid_job(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(mr, "_fairchem_backend", lambda _job: FakeBackend())
    (tmp_path / "job.json").write_text(
        json.dumps(make_job(structs(("a", None, 1.0)))), encoding="utf-8"
    )
    assert mr.main(["mlip_runner.py", "job.json"]) == 0
    assert rows(tmp_path)[0]["key"] == "a"


def test_load_job_reads_valid_file(tmp_path: Path) -> None:
    path = tmp_path / "job.json"
    path.write_text(json.dumps(make_job(structs(("a", None, 1.0)))), encoding="utf-8")
    assert mr.load_job(str(path))["structures"][0]["key"] == "a"


WATER = dict(
    symbols=["O", "H", "H"],
    coords=[[0.0, 0.0, 0.1173], [0.0, 0.7572, -0.4692], [0.0, -0.7572, -0.4692]],
)


@pytest.mark.skipif(
    not (os.environ.get("CONFFLOW_MLIP_PYTHON") and os.environ.get("CONFFLOW_MLIP_MODEL_PATH")),
    reason="set CONFFLOW_MLIP_PYTHON and CONFFLOW_MLIP_MODEL_PATH to run the real UMA smoke test",
)
def test_real_backend_smoke_water_relax(tmp_path: Path) -> None:
    shutil.copy(mr.__file__, tmp_path / "mlip_runner.py")  # production runs the copy in the job dir
    job = make_job(
        [dict(key="w", label=None, **WATER)],
        mode="relax",
        optimizer="lbfgs",
        fmax=0.05,
        max_steps=200,
        threads=2,
        workers=1,
        model_path=os.environ["CONFFLOW_MLIP_MODEL_PATH"],
        refs_path=os.environ.get("CONFFLOW_MLIP_REFS_PATH") or None,
    )
    (tmp_path / "job.json").write_text(json.dumps(job), encoding="utf-8")
    proc = subprocess.run(  # noqa: S603 - fixed argv, local test interpreter
        [os.environ["CONFFLOW_MLIP_PYTHON"], "mlip_runner.py", "job.json"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=900,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    (row,) = rows(tmp_path)
    assert row["error"] is None and row["converged"] is True
    assert row["energy_eh"] is not None and abs(row["energy_eh"]) > 1.0
    assert row["n_evals"] > 0 and row["coords"] is not None
    assert done(tmp_path)["model_sha256"] is not None
