"""Two different workflow definitions racing on one run root.

The late invocation loads run state before the early one installs its
generation, so its pre-lock digest check passes.  The generation lock must
still refuse it: exactly one run completes and the other is rejected with the
definition error, without changing the run root's ledger.
"""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Any

import pytest

from confflow.application.v4_run import V4RunApplication, V4RunRequest
from confflow.domain import FrozenDict, StructureSet
from confflow.domain.errors import DomainError
from confflow.execution.process import NativeProcessSupervisor
from confflow.persistence import arbitration
from confflow.workflow.v4.assembly import RunInputs
from tests.v4._builders import structure

TIMEOUT = 20.0


def _doc(step_id: str) -> dict[str, Any]:
    return {
        "schema": "confflow.workflow.v4",
        "inputs": {"structures": {"kind": "structure", "cardinality": "many"}},
        "global": {"scientific_defaults": {"charge": 0, "multiplicity": 1}},
        "steps": [
            {
                "id": step_id,
                "executor": "structure_transform",
                "transform": {"kind": "filter", "native": {"min_atoms": 1}},
                "bindings": {"structure": {"source": {"run": "structures"}}},
            }
        ],
    }


def _inputs() -> RunInputs:
    return RunInputs(structures=FrozenDict({"structures": StructureSet.of(structure("water"))}))


def test_different_definitions_on_one_root_admit_exactly_one_run(tmp_path: Path) -> None:
    root = str(tmp_path / "run")
    loaded = threading.Event()
    proceed = threading.Event()

    class DelayedApplication(V4RunApplication):
        @staticmethod
        def _load_or_init_run_state(**kwargs: Any) -> Any:
            result = V4RunApplication._load_or_init_run_state(**kwargs)
            loaded.set()
            assert proceed.wait(TIMEOUT), "early run never released the late run"
            return result

    late_outcome: list[object] = []

    def late() -> None:
        try:
            late_outcome.append(
                DelayedApplication(supervisor=NativeProcessSupervisor()).run(
                    V4RunRequest(workflow_document=_doc("b"), run_root=root, run_inputs=_inputs())
                )
            )
        except Exception as exc:  # captured for the main thread to assert on
            late_outcome.append(exc)

    thread = threading.Thread(target=late, daemon=True)
    try:
        thread.start()
        assert loaded.wait(TIMEOUT), "late run never reached its pre-lock state load"
        first = V4RunApplication(supervisor=NativeProcessSupervisor()).run(
            V4RunRequest(workflow_document=_doc("a"), run_root=root, run_inputs=_inputs())
        )
    finally:
        proceed.set()
        thread.join(TIMEOUT)

    assert not thread.is_alive(), "late run did not finish"
    assert first.status == "completed"
    assert len(late_outcome) == 1
    second = late_outcome[0]
    assert isinstance(second, DomainError), f"late run was not rejected: {second!r}"
    assert "different workflow definition" in str(second)

    ledger = arbitration.load_ledger(root)
    assert ledger is not None
    assert ledger.definition_digest == first.definition_digest
    assert ledger.current_generation_id == first.generation_id


def test_begin_generation_refuses_a_different_digest_without_writing(tmp_path: Path) -> None:
    root = str(tmp_path / "root")
    digest_a = "sha256:" + "a" * 64
    digest_b = "sha256:" + "b" * 64
    arbitration.begin_generation(
        root, generation_id="gen-1", run_id="r", definition_digest=digest_a
    )
    before = arbitration.load_ledger(root)

    with pytest.raises(arbitration.DefinitionMismatchError):
        arbitration.begin_generation(
            root, generation_id="gen-2", run_id="r", definition_digest=digest_b
        )

    assert arbitration.load_ledger(root) == before
    # Same-definition reruns still take over the root.
    arbitration.begin_generation(
        root, generation_id="gen-3", run_id="r", definition_digest=digest_a
    )
    assert arbitration.load_ledger(root).current_generation_id == "gen-3"  # type: ignore[union-attr]
