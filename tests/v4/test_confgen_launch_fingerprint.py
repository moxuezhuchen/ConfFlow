"""ConfGen xTB launch fingerprint: an xtb file changed after measurement is never launched.

The application resolves xtb once and keeps the fingerprint taken at resolution.  The step
refuses a stale resolution before any job file exists, the worker checks the fingerprint
before the version probe, and ``XtbCalculator`` checks it before every xtb process starts.
A replacement program writes a marker when it runs, so a launch of it is observable.
"""

from __future__ import annotations

import dataclasses
import json
import os
import sys
from pathlib import Path
from typing import Any

import pytest

from confflow.application import v4_run
from confflow.application.v4_run import V4RunApplication, V4RunRequest, import_xyz
from confflow.domain import FrozenDict
from confflow.domain.errors import DomainError
from confflow.execution import confgen_search_worker as worker
from confflow.execution.command_resolution import take_fingerprint
from confflow.execution.confgen_xtb import ExecutableChanged, XtbCalculator
from confflow.execution.process import NativeProcessSupervisor
from confflow.science.confgen.calculator import RelaxRequest
from confflow.workflow.v4.assembly import RunInputs
from tests.v4._dg_search_helpers import _toy, _write_fake
from tests.v4.test_confgen_search_step import _SEARCH, _scope
from tests.v4.test_confgen_search_worker import _job, _metal_parts, _stub


@pytest.fixture(autouse=True)
def _isolated_server_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CONFFLOW_SERVER_CONFIG", str(tmp_path / "absent.toml"))
    monkeypatch.setenv("CONFFLOW_SERVER_STATE_DIR", str(tmp_path / "quota-state"))


def _replacement_source(marker: Path) -> str:
    """Return a program that only records that it ran; it fails any convergence check."""
    return f"#!{sys.executable}\nopen({str(marker)!r}, 'a').write('ran\\n')\n"


def _replace_file(target: Path, text: str) -> None:
    """Replace *target* in place the way an upgrade does: new file, then rename over it."""
    staged = target.with_name(target.name + ".new")
    staged.write_text(text, encoding="utf-8")
    staged.chmod(0o755)
    os.replace(staged, target)


@pytest.fixture
def xtb_exe(tmp_path: Path) -> Path:
    """Create a working fake xtb at ``tmp_path/bin/xtb``."""
    bindir = tmp_path / "bin"
    bindir.mkdir()
    written = _write_fake(bindir)
    target = bindir / "xtb"
    written.rename(target)
    return target


def test_unchanged_fingerprint_launches(tmp_path: Path, xtb_exe: Path) -> None:
    els, coords = _toy()
    fingerprint = take_fingerprint(str(xtb_exe))
    calc = XtbCalculator(
        str(xtb_exe),
        tmp_path / "work",
        els,
        charge=0,
        uhf=0,
        max_cycles=50,
        cores=1,
        base_env=dict(os.environ),
        expected_fingerprint=fingerprint,
    )
    outcomes = calc.relax_many([RelaxRequest("a", coords)])
    assert len(outcomes) == 1
    assert outcomes[0].energy_eh is not None


def test_changed_before_construction_is_refused(tmp_path: Path, xtb_exe: Path) -> None:
    els, _coords = _toy()
    fingerprint = take_fingerprint(str(xtb_exe))
    _replace_file(xtb_exe, xtb_exe.read_text(encoding="utf-8") + "\n# changed\n")
    with pytest.raises(ExecutableChanged, match="changed after it was measured"):
        XtbCalculator(
            str(xtb_exe),
            tmp_path / "work",
            els,
            charge=0,
            uhf=0,
            max_cycles=50,
            cores=1,
            base_env=dict(os.environ),
            expected_fingerprint=fingerprint,
        )


def test_replaced_after_construction_is_refused_before_launch(
    tmp_path: Path, xtb_exe: Path
) -> None:
    els, coords = _toy()
    marker = tmp_path / "replacement-ran.txt"
    calc = XtbCalculator(
        str(xtb_exe),
        tmp_path / "work",
        els,
        charge=0,
        uhf=0,
        max_cycles=50,
        cores=1,
        base_env=dict(os.environ),
        expected_fingerprint=take_fingerprint(str(xtb_exe)),
    )
    _replace_file(xtb_exe, _replacement_source(marker))
    with pytest.raises(ExecutableChanged):
        calc.relax_many([RelaxRequest("a", coords)])
    with pytest.raises(ExecutableChanged):
        calc.hessian(RelaxRequest("h", coords))
    assert not marker.exists()


def test_removed_executable_is_refused(tmp_path: Path, xtb_exe: Path) -> None:
    els, coords = _toy()
    calc = XtbCalculator(
        str(xtb_exe),
        tmp_path / "work",
        els,
        charge=0,
        uhf=0,
        max_cycles=50,
        cores=1,
        base_env=dict(os.environ),
        expected_fingerprint=take_fingerprint(str(xtb_exe)),
    )
    xtb_exe.unlink()
    with pytest.raises(ExecutableChanged):
        calc.relax_many([RelaxRequest("a", coords)])


def test_no_fingerprint_means_no_check(tmp_path: Path, xtb_exe: Path) -> None:
    els, coords = _toy()
    marker = tmp_path / "replacement-ran.txt"
    calc = XtbCalculator(
        str(xtb_exe),
        tmp_path / "work",
        els,
        charge=0,
        uhf=0,
        max_cycles=50,
        cores=1,
        base_env=dict(os.environ),
    )
    _replace_file(xtb_exe, _replacement_source(marker))
    outcomes = calc.relax_many([RelaxRequest("a", coords)])
    assert len(outcomes) == 1
    assert marker.exists()


def test_worker_refuses_stale_fingerprint(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _els, ref = _toy()
    _stub(monkeypatch, ref)
    record = tmp_path / "record.jsonl"
    monkeypatch.setenv("FAKE_XTB_RECORD", str(record))
    parts = _metal_parts(tmp_path)
    fingerprint = dataclasses.asdict(take_fingerprint(str(parts[4])))
    fingerprint["size"] += 1
    job, work, _out, _summary = _job(tmp_path, *parts, xtb_fingerprint=fingerprint)
    assert worker.main([job]) == 3
    baseline = json.loads((work / "input_baseline.json").read_text(encoding="utf-8"))
    assert baseline["execution_status"] == "failed"
    assert not record.exists()


def test_worker_accepts_matching_fingerprint(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _els, ref = _toy()
    _stub(monkeypatch, ref)
    monkeypatch.setenv("FAKE_XTB_RECORD", str(tmp_path / "record.jsonl"))
    parts = _metal_parts(tmp_path)
    fingerprint = dataclasses.asdict(take_fingerprint(str(parts[4])))
    job, work, _out, _summary = _job(tmp_path, *parts, xtb_fingerprint=fingerprint)
    assert worker.main([job]) == 0
    baseline = json.loads((work / "input_baseline.json").read_text(encoding="utf-8"))
    assert baseline["execution_status"] == "completed"


def test_worker_rejects_malformed_fingerprint(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _els, ref = _toy()
    _stub(monkeypatch, ref)
    parts = _metal_parts(tmp_path)
    bad = {"device": "1", "inode": 1, "size": 1, "mtime_ns": 1, "ctime_ns": 1, "real_path": "x"}
    job, _work, _out, _summary = _job(tmp_path, *parts, xtb_fingerprint=bad)
    assert worker.main([job]) == 3


# --- end to end through the public V4 application -------------------------------------------


def _toy_text() -> str:
    elements, coords = _toy()
    rows = "".join(f"{e} {x} {y} {z}\n" for e, (x, y, z) in zip(elements, coords))
    return f"{len(elements)}\ntoy\n{rows}"


def _document(exe: Path, record: Path, *, extra_env: dict[str, str]) -> dict[str, Any]:
    ambient = os.environ["PATH"]
    return {
        "schema": "confflow.workflow.v4",
        "inputs": {
            "structures": {
                "kind": "structure",
                "cardinality": "many",
                "charge": 0,
                "multiplicity": 1,
            }
        },
        "steps": [
            {
                "id": "cg",
                "executor": "confgen",
                "bindings": {"structure": {"source": {"run": "structures"}}},
                "confgen": _scope(search={**_SEARCH, "starts": 1}, seed=11),
                "resources": {"cores_per_item": 1},
                "execution": {
                    "binding_id": "xtb",
                    "executable": "xtb",
                    "allowed_executables": [str(exe)],
                    "env": {
                        "PATH": f"{exe.parent}{os.pathsep}{ambient}",
                        "FAKE_XTB_RECORD": str(record),
                        **extra_env,
                    },
                },
            }
        ],
    }


def _run(document: dict[str, Any], run_root: Path) -> Any:
    text = _toy_text()
    return V4RunApplication(supervisor=NativeProcessSupervisor()).run(
        V4RunRequest(
            workflow_document=document,
            run_root=str(run_root),
            run_inputs=RunInputs(structures=FrozenDict({"structures": import_xyz(text)})),
            import_sources=FrozenDict({"structures": text}),
        )
    )


def _launch_count(record: Path) -> int:
    return len(record.read_text().splitlines()) if record.exists() else 0


def _published(report: Any) -> int:
    return sum(len(step.structures) for step in report.step_results)


def test_e2e_unchanged_xtb_completes(tmp_path: Path, xtb_exe: Path) -> None:
    record = tmp_path / "launches.jsonl"
    report = _run(_document(xtb_exe, record, extra_env={}), tmp_path / "run")
    assert report.status == "completed"
    assert _launch_count(record) > 0
    assert _published(report) > 0


def test_e2e_replaced_after_resolution_is_not_completed(
    tmp_path: Path, xtb_exe: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # the file is replaced after the step resolved it (and took its fingerprint) but before
    # the measurement and the launch: the stale resolution must be refused
    record = tmp_path / "launches.jsonl"
    marker = tmp_path / "replacement-ran.txt"
    original = v4_run.resolve_confgen_xtb_binding

    def resolve_then_replace(binding: Any, env: Any, *, step_id: str) -> Any:
        result = original(binding, env, step_id=step_id)
        _replace_file(xtb_exe, _replacement_source(marker))
        return result

    monkeypatch.setattr(v4_run, "resolve_confgen_xtb_binding", resolve_then_replace)
    run_root = tmp_path / "run"
    try:
        report = _run(_document(xtb_exe, record, extra_env={}), run_root)
    except DomainError as exc:
        messages = [str(exc)]
    else:
        assert report.status != "completed"
        assert _published(report) == 0
        messages = [d.message for s in report.step_results for d in s.diagnostics]
    # refused by the step itself: no job file was written and nothing was launched
    assert any("changed after it was resolved and measured" in m for m in messages)
    assert not list(run_root.rglob("search_job.json"))
    assert not marker.exists()
    assert _launch_count(record) == 0


def test_e2e_replaced_during_search_is_not_completed(tmp_path: Path, xtb_exe: Path) -> None:
    # the first xtb run replaces its own file; the next launch must be refused
    record = tmp_path / "launches.jsonl"
    marker = tmp_path / "replacement-ran.txt"
    staged = tmp_path / "replacement.py"
    staged.write_text(_replacement_source(marker), encoding="utf-8")
    staged.chmod(0o755)
    xtb_exe.write_text(
        xtb_exe.read_text(encoding="utf-8").replace(
            "els, xyz = read_xyz(sys.argv[1])",
            "if '--opt' in sys.argv and os.path.exists(os.environ['FAKE_XTB_SWAP_SRC']):\n"
            "    os.replace(os.environ['FAKE_XTB_SWAP_SRC'], sys.argv[0])\n"
            "els, xyz = read_xyz(sys.argv[1])",
        ),
        encoding="utf-8",
    )
    document = _document(xtb_exe, record, extra_env={"FAKE_XTB_SWAP_SRC": str(staged)})
    report = _run(document, tmp_path / "run")
    assert report.status != "completed"
    assert _published(report) == 0
    assert _launch_count(record) >= 1
