#!/usr/bin/env python3

"""Review post-#198 V1: MLIP backend and per-structure failures are task-level outcomes.

Through the public application entry (``V4RunApplication.run``):

* a backend that fails to build is a retryable FAILED item, never a completed empty result,
  and a resume after recovery re-executes the item (RETRY_FAILED);
* every structure failing with an error is FAILED; partial errors and legitimately empty
  filters stay COMPLETED;
* a stored COMPLETED result whose inputs were all dropped by errors is not reused; a new
  attempt runs and the old attempt stays in history; legitimately empty results are reused.

The scientific program is a harness runner that loads the real ``mlip_runner.py`` protocol
(``run_job``) with a controllable fake backend.  No model is loaded.
"""

from __future__ import annotations

import dataclasses
import json
import sys
from pathlib import Path
from typing import Any

import pytest

from confflow.application.v4_run import V4RunApplication, V4RunRequest, import_xyz
from confflow.domain import FrozenDict
from confflow.domain.completion import WorkItemStatus
from confflow.domain.diagnostics import Diagnostic, DiagnosticSeverity
from confflow.domain.result import ResultSet
from confflow.domain.structure import StructureSet
from confflow.execution import mlip_executor
from confflow.execution.mlip_executor import MODEL_ENV, THREADS_ENV
from confflow.execution.process import NativeProcessSupervisor
from confflow.persistence.contracts import StoredWorkItemStatus, store_path
from confflow.persistence.recovery import owner_identity_current
from confflow.persistence.work_items import SqliteWorkItemStore
from confflow.workflow.v4.assembly import RunInputs

STEP = "uma"
REAL_RUNNER = Path(mlip_executor.__file__).with_name("mlip_runner.py")
OK_FRAME = "3\nwater\nO 0 0 0\nH 0.96 0 0\nH -0.24 0.93 0\n"
#: The fake backend raises for any structure whose first atom sits beyond x = 5 A.
BAD_FRAME = "3\nwater\nO 9 0 0\nH 9.96 0 0\nH 8.76 0.93 0\n"
ENERGY_EH = -1.0

_HARNESS = """\
import importlib.util
import os
import sys
from pathlib import Path

REAL = __REAL__
FLAG = __FLAG__
LOG = __LOG__
BEHAVIOUR = __BEHAVIOUR__
import json
BEH = json.loads(Path(BEHAVIOUR).read_text(encoding="utf-8"))


def _load_real():
    spec = importlib.util.spec_from_file_location("real_mlip_runner", REAL)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module  # pool tasks pickle functions by module name
    spec.loader.exec_module(module)
    return module


real = _load_real()


class Backend:
    def energy(self, symbols, coords):
        if coords[0][0] > 5.0:
            raise ValueError("bad structure")
        return -27.211386245988, 1

    def relax(self, symbols, coords, fix_pairs, optimizer, fmax, max_steps):
        if coords[0][0] > 5.0:
            raise ValueError("bad structure")
        scale = 2.0 if BEH.get("stretch") else 1.0
        moved = [[x * scale, y * scale, z * scale] for x, y, z in coords]
        return {
            "energy_eV": -27.211386245988,
            "coords": moved,
            "converged": True,
            "n_steps": 1,
            "n_evals": 1,
        }


def _factory(job):
    if Path(FLAG).exists():
        raise RuntimeError("model backend unavailable: transient outage")
    return Backend()


with open(LOG, "a", encoding="utf-8") as handle:
    handle.write("launch\\n")
raise SystemExit(real.run_job(real.load_job("job.json"), os.getcwd(), _factory))
"""


@pytest.fixture(autouse=True)
def _isolated_server_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep server config and quota state inside tmp_path, with enough cores for two workers."""
    config = tmp_path / "server.toml"
    config.write_text("total_cores = 96\ntotal_memory = '192GB'\n", encoding="utf-8")
    monkeypatch.setenv("CONFFLOW_SERVER_CONFIG", str(config))
    monkeypatch.setenv("CONFFLOW_SERVER_STATE_DIR", str(tmp_path / "quota-state"))


@pytest.fixture
def harness(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Path]:
    """Install the harness runner; the flag file simulates a backend outage."""
    work = tmp_path / "harness"
    work.mkdir()
    flag = work / "backend-unavailable"
    log = work / "launches.log"
    runner = work / "harness_runner.py"
    (work / "behaviour.json").write_text("{}", encoding="utf-8")
    text = (
        _HARNESS.replace("__REAL__", repr(str(REAL_RUNNER)))
        .replace("__FLAG__", repr(str(flag)))
        .replace("__LOG__", repr(str(log)))
        .replace("__BEHAVIOUR__", repr(str(work / "behaviour.json")))
    )
    runner.write_text(text, encoding="utf-8")
    monkeypatch.setattr(mlip_executor, "RUNNER_PATH", runner)
    model = work / "model.pt"
    model.write_text("constant model bytes", encoding="utf-8")
    return {"flag": flag, "log": log, "model": model, "dir": work}


def _behave(harness: dict[str, Path], **behaviour: Any) -> None:
    """Set the harness behaviour (read by the runner at each launch)."""
    (harness["dir"] / "behaviour.json").write_text(json.dumps(behaviour), encoding="utf-8")


def _document(model: Path, *, cores: int = 1, mode: str = "sp", **mlip: Any) -> dict[str, Any]:
    return {
        "schema": "confflow.workflow.v4",
        "inputs": {"structures": {"kind": "structure", "cardinality": "many"}},
        "global": {"scientific_defaults": {"charge": 0, "multiplicity": 1}},
        "steps": [
            {
                "id": STEP,
                "executor": "mlip",
                "bindings": {"structure": {"source": {"run": "structures"}}},
                "mlip": {"model": "uma-s-1p2p1", "mode": mode, **mlip},
                "resources": {"cores_per_item": cores, "memory_per_item": "1GB"},
                "execution": {
                    "binding_id": "uma",
                    "executable": sys.executable,
                    "env": {MODEL_ENV: str(model), THREADS_ENV: "1"},
                },
            }
        ],
    }


def _run(run_root: Path, document: dict[str, Any], xyz: str) -> Any:
    return V4RunApplication(supervisor=NativeProcessSupervisor()).run(
        V4RunRequest(
            workflow_document=document,
            run_inputs=RunInputs(structures=FrozenDict({"structures": import_xyz(xyz)})),
            import_sources=FrozenDict({"structures": xyz}),
            run_root=str(run_root),
            executables=FrozenDict(),
        )
    )


def _items(report: Any) -> tuple[Any, ...]:
    return tuple(report.step_results[0].item_results)


def _step_codes(report: Any) -> list[str]:
    return [diagnostic.code for diagnostic in report.step_results[0].diagnostics]


def _launches(harness: dict[str, Path]) -> int:
    log = harness["log"]
    return log.read_text(encoding="utf-8").count("launch") if log.exists() else 0


def _energy(report: Any) -> float:
    return float(report.step_results[0].results[0].value)


# ---------------------------------------------------------------------------
# Backend initialisation failure is a task-level, retryable FAILED item
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("cores", [1, 2], ids=["single-worker", "pool-workers"])
def test_backend_outage_is_failed_and_retryable(
    tmp_path: Path, harness: dict[str, Path], cores: int
) -> None:
    harness["flag"].write_text("outage", encoding="utf-8")
    report = _run(
        tmp_path / "outage",
        _document(harness["model"], cores=cores),
        OK_FRAME,
    )
    assert report.status != "completed"
    (item,) = _items(report)
    assert item.status is WorkItemStatus.FAILED
    assert item.error is not None and item.error.retryable is True
    assert "model backend unavailable" in item.error.message
    assert not report.step_results[0].results


@pytest.mark.parametrize("cores", [1, 2], ids=["single-worker", "pool-workers"])
def test_backend_outage_resume_after_recovery_recomputes(
    tmp_path: Path, harness: dict[str, Path], cores: int
) -> None:
    document = _document(harness["model"], cores=cores)
    root = tmp_path / "outage"
    harness["flag"].write_text("outage", encoding="utf-8")
    failed = _run(root, document, OK_FRAME)
    assert failed.status != "completed"
    assert _launches(harness) == 1

    harness["flag"].unlink()
    resumed = _run(root, document, OK_FRAME)
    assert resumed.status == "completed", resumed.step_results[0].diagnostics
    assert _launches(harness) == 2, "the failed item must be re-executed, not reused"
    assert "reuse_hit" not in _step_codes(resumed)
    assert len(resumed.step_results[0].structures) == 1
    assert _energy(resumed) == pytest.approx(ENERGY_EH)

    control = _run(tmp_path / "healthy", document, OK_FRAME)
    assert control.status == "completed"
    assert len(control.step_results[0].structures) == 1
    assert _energy(control) == pytest.approx(ENERGY_EH)


# ---------------------------------------------------------------------------
# Per-structure errors: all errored fails, partial and legitimate empty complete
# ---------------------------------------------------------------------------


def test_every_structure_errored_fails_retryable(tmp_path: Path, harness: dict[str, Path]) -> None:
    report = _run(tmp_path / "all-bad", _document(harness["model"]), BAD_FRAME + BAD_FRAME)
    assert report.status != "completed"
    (item,) = _items(report)
    assert item.status is WorkItemStatus.FAILED
    assert item.error is not None and item.error.code == "mlip_all_errored"
    assert item.error.retryable is True
    assert "bad structure" in item.error.message
    assert not report.step_results[0].structures


def test_partial_errors_complete_with_surviving_output(
    tmp_path: Path, harness: dict[str, Path]
) -> None:
    report = _run(tmp_path / "partial", _document(harness["model"]), OK_FRAME + BAD_FRAME)
    assert report.status == "completed", report.step_results[0].diagnostics
    (item,) = _items(report)
    assert item.status is WorkItemStatus.COMPLETED
    assert len(report.step_results[0].structures) == 1
    assert _energy(report) == pytest.approx(ENERGY_EH)


def test_legitimate_empty_filter_completes_and_is_reused(
    tmp_path: Path, harness: dict[str, Path]
) -> None:
    _behave(harness, stretch=True)
    document = _document(harness["model"], mode="relax", reject_bond_stretch=1.25)
    root = tmp_path / "filtered"
    first = _run(root, document, OK_FRAME + OK_FRAME)
    assert first.status == "completed", first.step_results[0].diagnostics
    assert not first.step_results[0].structures
    assert _launches(harness) == 1

    second = _run(root, document, OK_FRAME + OK_FRAME)
    assert second.status == "completed"
    assert any(d.code == "reuse_hit" for item in _items(second) for d in item.diagnostics)
    assert _launches(harness) == 1


# ---------------------------------------------------------------------------
# Stored all-errored COMPLETED results from before the fix are not reused
# ---------------------------------------------------------------------------


def _legacy_all_errored(root: Path) -> str:
    """Replace the stored COMPLETED result of the single item with an all-errored empty one.

    The store's own transitions are used: COMPLETED -> INTERRUPTED (generation refresh with
    unchanged axes) -> claim (new attempt) -> record the legacy result.  Attempt history stays.
    """
    store_file = store_path(str(root), STEP)
    with SqliteWorkItemStore.open(store_file) as store:
        (item_id,) = store.list_items()
        registered = store.get_registered(item_id)
        stored = store.get_result(item_id)
        assert stored is not None and stored.status is WorkItemStatus.COMPLETED
        store.refresh_for_new_environment(
            item_id,
            work_item_digest=registered["work_item_digest"],
            step_semantic_digest=registered["step_semantic_digest"],
            environment_digest=registered["environment_digest"],
            producer_provenance=dict(registered["producer_provenance"]),
            reason="legacy fixture: all inputs dropped by backend errors",
        )
        assert store.claim(item_id, owner=owner_identity_current(owner_token="legacy-fixture"))
        legacy_diagnostic = Diagnostic(
            code="mlip_completed",
            message="mlip sp (legacy) kept 0 of 1: dropped error 1",
            severity=DiagnosticSeverity.INFO,
            details=FrozenDict(
                {
                    "inputs": 1,
                    "dropped_error": 1,
                    "dropped_stretch": 0,
                    "error_rows": [{"id": "legacy", "stage": "sp", "error": "backend down"}],
                }
            ),
        )
        legacy = dataclasses.replace(
            stored,
            structures=StructureSet(()),
            results=ResultSet(()),
            diagnostics=(legacy_diagnostic,),
            error=None,
        )
        store.record_finished(legacy)
        return item_id


def test_stored_all_errored_completed_result_is_reexecuted(
    tmp_path: Path, harness: dict[str, Path]
) -> None:
    document = _document(harness["model"])
    root = tmp_path / "legacy"
    first = _run(root, document, OK_FRAME)
    assert first.status == "completed"
    item_id = _items(first)[0].work_item_id
    assert _launches(harness) == 1

    _legacy_all_errored(root)
    second = _run(root, document, OK_FRAME)
    assert all(d.code != "reuse_hit" for item in _items(second) for d in item.diagnostics)
    assert _launches(harness) == 2
    assert second.status == "completed"
    assert len(second.step_results[0].structures) == 1
    assert _energy(second) == pytest.approx(ENERGY_EH)

    with SqliteWorkItemStore.open(store_path(str(root), STEP)) as store:
        attempts = store.get_attempts(item_id)
    assert len(attempts) >= 3
    assert attempts[0].status is StoredWorkItemStatus.COMPLETED
    assert attempts[1].status is StoredWorkItemStatus.COMPLETED


def test_runner_and_executor_agree_on_the_backend_failure_protocol() -> None:
    from confflow.execution import mlip_runner

    assert mlip_executor._BACKEND_EXIT_CODE == mlip_runner.BACKEND_EXIT_CODE
    assert mlip_executor._BACKEND_FAILED == mlip_runner.BACKEND_FAILED


def test_backend_marker_with_zero_exit_is_a_retryable_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A done file that reports a backend failure while the runner exits 0 is contradictory."""
    runner = tmp_path / "contradictory_runner.py"
    runner.write_text(
        "import json\n"
        "json.dump({'status': 'backend_failed', 'backend_error': 'OSError: no model'},\n"
        "          open('mlip_done.json', 'w'))\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(mlip_executor, "RUNNER_PATH", runner)
    model = tmp_path / "model.pt"
    model.write_text("constant model bytes", encoding="utf-8")
    report = _run(tmp_path / "contradictory", _document(model), OK_FRAME)
    assert report.status != "completed"
    (item,) = _items(report)
    assert item.status is WorkItemStatus.FAILED
    assert item.error is not None and item.error.retryable is True
    assert "OSError: no model" in item.error.message
    assert "contradicts" in item.error.message


def test_in_memory_reuse_refuses_stored_all_errored_result(
    tmp_path: Path, harness: dict[str, Path]
) -> None:
    """The non-durable reuse path consults the same executor hook and re-executes."""
    from confflow.domain.step_result import StepStatus
    from confflow.execution.batch import BatchStepExecutor, InMemoryReuseStore, StepExecutionRequest
    from tests.v4._builders import structure_set
    from tests.v4._helpers.v42_executors import assemble_items, binding_for, compile_plan

    plan = compile_plan(_document(harness["model"]))
    items = assemble_items(plan, structure_set("s0"))
    native_env = FrozenDict({MODEL_ENV: str(harness["model"]), THREADS_ENV: "1"})
    store = InMemoryReuseStore()

    def execute(root: Path) -> Any:
        request = StepExecutionRequest(
            step=plan.steps[0],
            items=tuple(items),
            scientific=plan.steps[0].scientific,
            scientific_defaults=plan.scientific_defaults,
            execution_binding=binding_for(sys.executable),
            native_env=native_env,
            run_root=str(root),
            work_base=str(root / "work"),
            producer_provenance=FrozenDict({}),
        )
        executor = BatchStepExecutor(mlip_executor.MlipExecutor(), reuse_store=store)
        return executor.with_supervisor(NativeProcessSupervisor()).execute_step(request)

    first = execute(tmp_path / "first")
    assert first.status is StepStatus.COMPLETED
    assert _launches(harness) == 1

    stored = store.lookup(items[0].semantic_digest)
    assert stored is not None and len(stored.structures) == 1
    legacy_summary = Diagnostic(
        code="mlip_completed",
        message="legacy: kept 0 of 1",
        severity=DiagnosticSeverity.INFO,
        details=FrozenDict(
            {
                "inputs": 1,
                "error_rows": [{"id": "legacy", "stage": "sp", "error": "backend down"}],
            }
        ),
    )
    store.store(
        dataclasses.replace(
            stored,
            structures=StructureSet(()),
            results=ResultSet(()),
            diagnostics=(legacy_summary,),
            error=None,
        )
    )

    second = execute(tmp_path / "second")
    assert second.status is StepStatus.COMPLETED
    assert _launches(harness) == 2, "a refused in-memory result must be re-executed"
    assert len(second.structures) == 1
