"""Review fix #2: a ConfGen step's reuse identity covers the xTB binary content."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from confflow.application.v4_run import V4RunApplication, V4RunRequest, import_xyz
from confflow.domain import FrozenDict
from confflow.domain.errors import DomainError
from confflow.execution.contracts import ExecutionBinding, ExecutorCapability
from confflow.execution.environment import build_pure_environment, measure_executable
from confflow.execution.process import NativeProcessSupervisor
from confflow.workflow.v4.assembly import RunInputs
from tests.v4._dg_search_helpers import _toy, _write_fake
from tests.v4.test_confgen_search_step import _SEARCH, _scope


def _document(search_starts: int = 1) -> dict[str, Any]:
    step = {
        "id": "cg",
        "executor": "confgen",
        "bindings": {"structure": {"source": {"run": "structures"}}},
        "resources": {"cores_per_item": 1, "memory_per_item": "1GB"},
        "confgen": {
            **_scope(),
            "topology": {"add_bond": [{"atoms": [2, 4], "kind": "FORMING"}]},
            "search": {**_SEARCH, "starts": search_starts},
            "seed": 11,
        },
    }
    document: dict[str, Any] = {
        "schema": "confflow.workflow.v4",
        "inputs": {
            "structures": {
                "kind": "structure",
                "cardinality": "many",
                "charge": 0,
                "multiplicity": 1,
            }
        },
        "steps": [step],
    }
    return document


def _run(document: dict[str, Any], run_root: Path, text: str, xtb: Path) -> Any:
    return V4RunApplication(supervisor=NativeProcessSupervisor()).run(
        V4RunRequest(
            workflow_document=document,
            run_inputs=RunInputs(structures=FrozenDict({"structures": import_xyz(text)})),
            import_sources=FrozenDict({"structures": text}),
            run_root=str(run_root),
            executables=FrozenDict({"xtb": str(xtb)}),
        )
    )


@pytest.fixture
def toy_text() -> str:
    elements, coords = _toy()
    rows = "".join(f"{e} {x} {y} {z}\n" for e, (x, y, z) in zip(elements, coords))
    return f"{len(elements)}\ntoy\n{rows}"


@pytest.fixture
def isolated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("CONFFLOW_SERVER_CONFIG", str(tmp_path / "absent.toml"))
    counter = tmp_path / "calls"
    monkeypatch.setenv("FAKE_XTB_COUNT", str(counter))
    return counter


def _calls(counter: Path) -> int:
    return counter.read_text().count("x") if counter.exists() else 0


def test_changed_xtb_content_recomputes_confgen(
    tmp_path: Path, toy_text: str, isolated: Path
) -> None:
    fake = _write_fake(tmp_path)
    run_root = tmp_path / "run"
    document = _document()

    first = _run(document, run_root, toy_text, fake)
    calls_before = _calls(isolated)
    assert first.status == "completed"
    assert calls_before > 0

    fake.write_text(fake.read_text().replace("e = -100.0", "e = -200.0"))
    second = _run(document, run_root, toy_text, fake)

    assert second.status == "completed"
    assert _calls(isolated) > calls_before
    energies = [dict(s.metadata).get("energy_eh") for s in second.step_results[0].structures]
    assert energies and all(e is not None and e < -150.0 for e in energies)


def test_unchanged_xtb_content_still_reuses_confgen(
    tmp_path: Path, toy_text: str, isolated: Path
) -> None:
    fake = _write_fake(tmp_path)
    run_root = tmp_path / "run"
    document = _document()

    first = _run(document, run_root, toy_text, fake)
    calls_before = _calls(isolated)
    second = _run(document, run_root, toy_text, fake)

    assert first.status == second.status == "completed"
    assert _calls(isolated) == calls_before


def test_confgen_identity_digests_xtb_content_not_metadata(tmp_path: Path) -> None:
    from confflow.application.v4_run import _pure_executable_identity

    fake = _write_fake(tmp_path)
    binding = ExecutionBinding(binding_id="xtb", executable=str(fake))
    planned = _planned_confgen()

    identity = _pure_executable_identity(planned, binding)
    assert identity is not None
    assert identity.digest == measure_executable(str(fake)).digest

    env = build_pure_environment(
        implementation="confflow.confgen",
        implementation_version="1",
        executable_digest=identity.digest,
        metadata={"resolved_path": identity.resolved_path, "mtime_ns": identity.mtime_ns},
    )
    plain = build_pure_environment(implementation="confflow.confgen", implementation_version="1")
    assert env.executable_digest == identity.digest
    assert env.digest() != plain.digest()
    twin = build_pure_environment(
        implementation="confflow.confgen",
        implementation_version="1",
        executable_digest=identity.digest,
        metadata={"resolved_path": "/elsewhere", "mtime_ns": 0},
    )
    assert twin.digest() == env.digest()


def test_non_confgen_pure_step_keeps_digest(tmp_path: Path) -> None:
    from confflow.application.v4_run import _pure_executable_identity

    fake = _write_fake(tmp_path)
    binding = ExecutionBinding(binding_id="xtb", executable=str(fake))
    transform = SimpleNamespace(executor=ExecutorCapability.STRUCTURE_TRANSFORM, step_id="t")
    assert _pure_executable_identity(transform, binding) is None
    assert _pure_executable_identity(_planned_confgen(), None) is None
    plain = build_pure_environment(implementation="confflow.transform", implementation_version="1")
    assert plain.executable_digest is None


def test_unresolvable_xtb_yields_no_identity(tmp_path: Path) -> None:
    from confflow.application.v4_run import _pure_executable_identity

    binding = ExecutionBinding(binding_id="xtb", executable=str(tmp_path / "missing-xtb"))
    assert _pure_executable_identity(_planned_confgen(), binding) is None
    with pytest.raises(DomainError):
        measure_executable(str(tmp_path / "missing-xtb"))


def _planned_confgen() -> Any:
    return SimpleNamespace(executor=ExecutorCapability.CONFGEN, step_id="cg")
