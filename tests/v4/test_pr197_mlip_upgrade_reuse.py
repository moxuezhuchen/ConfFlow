"""PR #197 F2: an MLIP result recorded under contract v1 is never reused by v2 code.

Before the contract bump, MLIP results written by the previous commit (empty
``topology_patch`` / ``working_topology``, no energy provenance) matched the new
identity and were reported as ``reuse_hit``.  The contract version feeds the
producer provenance (``profile_version``), the environment digest
(``implementation_version``) and the step semantic digest (executor contract),
and the producer-provenance axis is the first in the frozen compatibility chain.
A v1 COMPLETED result therefore becomes ``invalidate_provenance``: a non-retryable
refusal that re-executes nothing and keeps the history.

The scientific program is the fake MLIP runner from the launch-identity tests.
"""

from __future__ import annotations

import dataclasses
import sys
from pathlib import Path
from typing import Any

import pytest

from confflow.application.v4_run import V4RunApplication, V4RunRequest, import_xyz
from confflow.domain import FrozenDict
from confflow.domain.errors import DomainError
from confflow.execution.contracts import ExecutorCapability
from confflow.execution.mlip_executor import MlipExecutor
from confflow.execution.process import NativeProcessSupervisor
from confflow.execution.registry import build_default_registry
from confflow.workflow.v4.assembly import RunInputs
from tests.v4 import test_arch_review_launch_identity as launch

V1 = "confflow.contract.executor.mlip.v1"
V2 = "confflow.contract.executor.mlip.v2"
WATER = "3\nwater\nO 0 0 0\nH 0.96 0 0\nH -0.24 0.93 0\n"


def _registry_with_version(version: str) -> Any:
    """Return a registry whose MLIP contract carries *version* (old or new code)."""
    registry = build_default_registry()
    contract = registry.resolve_executor(ExecutorCapability.MLIP)
    registry.register_executor(
        dataclasses.replace(contract, contract_version=version), MlipExecutor
    )
    return registry


def _water_document(model: Path) -> dict[str, Any]:
    document = launch._mlip_doc(model, sys.executable)
    document["inputs"]["structures"]["topology"] = {"delete": [[1, 3]]}
    return document


def _run(run_root: Path, model: Path, registry: Any = None) -> Any:
    return V4RunApplication(supervisor=NativeProcessSupervisor(), registry=registry).run(
        V4RunRequest(
            workflow_document=_water_document(model),
            run_root=str(run_root),
            run_inputs=RunInputs(structures=FrozenDict({"structures": import_xyz(WATER)})),
            import_sources=FrozenDict({"structures": WATER}),
        )
    )


def _rows(graph: Any) -> tuple[tuple[int, ...], ...] | None:
    return None if graph is None else tuple(tuple(int(v) for v in row) for row in graph)


def _codes(report: Any) -> list[str]:
    return [diagnostic.code for diagnostic in report.step_results[0].diagnostics]


@pytest.fixture
def model(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    launch._install_mlip_runner(monkeypatch, tmp_path)
    path = tmp_path / "uma.pt"
    path.write_text("-76.0\n", encoding="utf-8")
    return path


def test_contract_version_is_v2() -> None:
    contract = build_default_registry().resolve_executor(ExecutorCapability.MLIP)
    assert contract.contract_version == V2


def test_same_contract_resume_is_a_reuse_hit(tmp_path: Path, model: Path) -> None:
    """Positive control: the harness really reuses when nothing changed."""
    root = tmp_path / "run"
    first = _run(root, model, registry=_registry_with_version(V2))
    assert first.status == "completed", first.diagnostics
    second = _run(root, model, registry=_registry_with_version(V2))
    assert "reuse_hit" in _codes(second)


def test_v1_completed_result_is_refused_at_run_root_by_v2(tmp_path: Path, model: Path) -> None:
    """Old commit generates the result; new code resumes the same run root.

    The contract version is part of the run definition digest, so the run root
    refuses the whole resume with an explicit diagnostic before any step reuse
    is evaluated.  Nothing is reused and nothing is executed.
    """
    root = tmp_path / "run"
    old = _run(root, model, registry=_registry_with_version(V1))
    assert old.status == "completed", old.diagnostics

    with pytest.raises(DomainError, match="use a fresh run root"):
        _run(root, model)


def test_v1_result_is_not_silently_completed_for_the_new_code(tmp_path: Path, model: Path) -> None:
    """A fresh run root under v2 produces a real result, not the stale v1 record."""
    _run(tmp_path / "old", model, registry=_registry_with_version(V1))
    fresh = _run(tmp_path / "new", model)
    assert fresh.status == "completed", fresh.diagnostics
    assert "reuse_hit" not in _codes(fresh)
    assert "mlip_completed" in _codes(fresh)
    structure = fresh.step_results[0].structures[0]
    assert _rows(structure.working_topology) == ((1,), (0,), ())
