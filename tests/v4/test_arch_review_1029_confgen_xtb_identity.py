"""Architecture review N2: ConfGen resolves xTB once and launches the measured file.

Control-process PATH and binding PATH deliberately prefer different fake xTB
executables (``a/xtb`` and ``b/xtb``).  Whichever file the allow list, the
identity measurement and the launch all use must be the same file.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import pytest

from confflow.application.v4_run import V4RunApplication, V4RunRequest, import_xyz
from confflow.domain import FrozenDict
from confflow.domain.errors import DomainError
from confflow.execution.contracts import ExecutionBinding
from confflow.execution.process import NativeProcessSupervisor
from confflow.workflow.v4.assembly import RunInputs
from tests.v4._dg_search_helpers import _toy, _write_fake
from tests.v4.test_confgen_search_step import _SEARCH, _scope


@pytest.fixture
def toy_text() -> str:
    elements, coords = _toy()
    rows = "".join(f"{e} {x} {y} {z}\n" for e, (x, y, z) in zip(elements, coords))
    return f"{len(elements)}\ntoy\n{rows}"


@pytest.fixture
def two_xtb(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Return two distinct fake xtb files ``a/xtb`` and ``b/xtb`` with their launch record.

    The control process PATH prefers ``a``; the binding PATH (set by each test) prefers ``b``.
    """
    monkeypatch.setenv("CONFFLOW_SERVER_CONFIG", str(tmp_path / "absent.toml"))
    monkeypatch.setenv("CONFFLOW_SERVER_STATE_DIR", str(tmp_path / "quota-state"))
    a, b = tmp_path / "a", tmp_path / "b"
    a.mkdir()
    b.mkdir()
    for directory, label in ((a, "A"), (b, "B")):
        written = _write_fake(directory)
        target = directory / "xtb"
        written.rename(target)
        target.write_text(target.read_text() + f"\n# version {label}\n")
    record = tmp_path / "xtb-launches.jsonl"
    monkeypatch.setenv("PATH", f"{a}{os.pathsep}{os.environ['PATH']}")
    return {
        "a": a / "xtb",
        "b": b / "xtb",
        "a_dir": a,
        "b_dir": b,
        "record": record,
        "ambient_path": os.environ["PATH"].split(os.pathsep, 1)[1],
    }


def _document(xtb: dict[str, Any], *, allowed: Path) -> dict[str, Any]:
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
                "execution": {
                    "binding_id": "xtb",
                    "executable": "xtb",
                    "allowed_executables": [str(allowed)],
                    "env": {
                        "PATH": f"{xtb['b_dir']}{os.pathsep}{xtb['ambient_path']}",
                        "FAKE_XTB_RECORD": str(xtb["record"]),
                    },
                },
            }
        ],
    }


def _run(document: dict[str, Any], run_root: Path, text: str) -> Any:
    return V4RunApplication(supervisor=NativeProcessSupervisor()).run(
        V4RunRequest(
            workflow_document=document,
            run_root=str(run_root),
            run_inputs=RunInputs(structures=FrozenDict({"structures": import_xyz(text)})),
            import_sources=FrozenDict({"structures": text}),
        )
    )


def _launches(record: Path) -> list[str]:
    """Return the resolved executable path of every real xTB launch."""
    if not record.exists():
        return []
    return [
        os.path.realpath(json.loads(line)["argv"][0]) for line in record.read_text().splitlines()
    ]


def test_allow_list_for_control_path_executable_rejects_binding_executable(
    tmp_path: Path, toy_text: str, two_xtb: dict[str, Any]
) -> None:
    # Only a/xtb is allowed, but the binding PATH resolves xtb to b/xtb: the run is refused
    document = _document(two_xtb, allowed=two_xtb["a"])
    run_root = tmp_path / "run"
    try:
        report = _run(document, run_root, toy_text)
    except DomainError:
        pass
    else:
        assert report.status != "completed"
    assert os.path.realpath(two_xtb["b"]) not in _launches(two_xtb["record"])
    assert _launches(two_xtb["record"]) == []


def test_changed_allowed_binding_executable_content_recomputes(
    tmp_path: Path, toy_text: str, two_xtb: dict[str, Any]
) -> None:
    # b/xtb is the allowed, measured and launched file, so editing it changes identity
    document = _document(two_xtb, allowed=two_xtb["b"])
    run_root = tmp_path / "run"
    first = _run(document, run_root, toy_text)
    assert first.status == "completed"
    launches_before = _launches(two_xtb["record"])
    assert launches_before
    assert set(launches_before) == {os.path.realpath(two_xtb["b"])}
    assert {Path(p).parent.name for p in launches_before} == {"b"}

    text = two_xtb["b"].read_text()
    assert "e = -100.0" in text
    two_xtb["b"].write_text(text.replace("e = -100.0", "e = -200.0"))
    second = _run(document, run_root, toy_text)

    assert second.status == "completed"
    assert len(_launches(two_xtb["record"])) > len(launches_before)
    energies = [dict(s.metadata).get("energy_eh") for s in second.step_results[0].structures]
    assert energies and all(e is not None and e < -150.0 for e in energies)


def test_pin_resolves_binding_path_not_control_path(
    two_xtb: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    from confflow.application.v4_run import resolve_confgen_xtb_binding

    binding = ExecutionBinding(binding_id="xtb", executable="xtb")
    snapshot = {"PATH": f"{two_xtb['b_dir']}{os.pathsep}{two_xtb['ambient_path']}"}
    pinned = resolve_confgen_xtb_binding(binding, snapshot, step_id="cg")
    assert pinned.executable == str(two_xtb["b"])
    # the ambient environment is not consulted once the snapshot is given
    monkeypatch.setenv("PATH", str(two_xtb["a_dir"]))
    assert resolve_confgen_xtb_binding(binding, snapshot, step_id="cg").executable == str(
        two_xtb["b"]
    )


def test_pin_unresolvable_or_missing_binding_fails_closed(
    tmp_path: Path, two_xtb: dict[str, Any]
) -> None:
    from confflow.application.v4_run import resolve_confgen_xtb_binding

    absent = {"PATH": str(tmp_path / "nowhere")}
    with pytest.raises(DomainError, match="did not resolve"):
        resolve_confgen_xtb_binding(
            ExecutionBinding(binding_id="xtb", executable="xtb"), absent, step_id="cg"
        )
    missing = tmp_path / "missing-xtb"
    with pytest.raises(DomainError, match="did not resolve"):
        resolve_confgen_xtb_binding(
            ExecutionBinding(binding_id="xtb", executable=str(missing)), absent, step_id="cg"
        )
    # no executable at all leaves the binding for the executor's own missing-executable error
    unset = ExecutionBinding(binding_id="xtb", executable=None)
    assert resolve_confgen_xtb_binding(unset, absent, step_id="cg") is unset
    # a relative name with a directory part resolves against the control process cwd
    rel = ExecutionBinding(binding_id="xtb", executable="a/xtb")
    cwd = os.getcwd()
    try:
        os.chdir(tmp_path)
        pinned = resolve_confgen_xtb_binding(rel, absent, step_id="cg")
    finally:
        os.chdir(cwd)
    assert pinned.executable == str(tmp_path / "a" / "xtb")


def test_worker_refuses_non_absolute_job_executable(
    tmp_path: Path, two_xtb: dict[str, Any]
) -> None:
    from confflow.execution import confgen_search_worker as worker
    from tests.v4.test_confgen_search_worker import _job, _metal_parts

    # control PATH finds a/xtb for the bare name; a bare job path must still fail closed
    job, _work, _out, _summary = _job(tmp_path, *_metal_parts(tmp_path), xtb="xtb")
    assert worker.main([job]) == 3
    assert _launches(two_xtb["record"]) == []


def test_confgen_executor_refuses_bare_xtb_name_before_launch(tmp_path: Path) -> None:
    from tests.v4.test_confgen_search_step import _driving_metal, _native, _run

    # a bare name never reaches a job file: the executor fails before any directory exists
    out, _item = _run(_native(), _driving_metal(), Path("xtb"), tmp_path)
    assert out.status.value == "failed"
    assert "absolute xTB executable path" in out.diagnostics[0].message


def test_bare_name_allow_list_completes_with_binding_path_executable(
    tmp_path: Path, toy_text: str, two_xtb: dict[str, Any]
) -> None:
    # configured name "xtb" matches the name rule; the launch is the file the binding PATH resolves
    document = _document(two_xtb, allowed=Path("xtb"))
    report = _run(document, tmp_path / "run", toy_text)
    assert report.status == "completed"
    launches = _launches(two_xtb["record"])
    assert launches
    assert set(launches) == {os.path.realpath(two_xtb["b"])}


def test_resolved_absolute_allow_list_completes(
    tmp_path: Path, toy_text: str, two_xtb: dict[str, Any]
) -> None:
    # the allow list names the file the binding PATH resolves to, as an absolute path
    document = _document(two_xtb, allowed=two_xtb["b"])
    report = _run(document, tmp_path / "run", toy_text)
    assert report.status == "completed"
    launches = _launches(two_xtb["record"])
    assert launches
    assert set(launches) == {os.path.realpath(two_xtb["b"])}


def test_other_absolute_allow_list_refuses_without_launch(
    tmp_path: Path, toy_text: str, two_xtb: dict[str, Any]
) -> None:
    # only a/xtb is allowed (absolute), the binding PATH resolves to b/xtb: refused, no launch
    document = _document(two_xtb, allowed=two_xtb["a"])
    try:
        report = _run(document, tmp_path / "run", toy_text)
    except DomainError as exc:
        assert "not allowed by allowed_executables" in str(exc)
    else:
        assert report.status != "completed"
    assert _launches(two_xtb["record"]) == []
