"""Shared V4.3 coverage fixtures (moved verbatim from test_v43_coverage)."""

from __future__ import annotations

from typing import Any

from confflow.domain import FrozenDict, StructureSet
from confflow.domain.completion import WorkItemStatus
from confflow.domain.diagnostics import Diagnostic
from confflow.domain.work_item import WorkItemResult
from confflow.execution import ExecutionBinding
from confflow.execution.batch import StepExecutionRequest
from confflow.persistence import wall_now
from confflow.persistence.work_items import SqliteWorkItemStore
from tests.v4._builders import assemble, calc_step, compile_doc, run_inputs, structure, v4_doc
from tests.v4._helpers.v42_executors import FAKE_ORCA

STEP_ID = "s_opt"


def _compile(document: dict[str, Any]) -> Any:
    """Compile *document*, asserting a clean compile."""
    compiled = compile_doc(document)
    assert compiled.ok, [(item.code, item.message) for item in compiled.errors]
    assert compiled.plan is not None
    return compiled.plan


def _document(**overrides: Any) -> dict[str, Any]:
    """Build the single-calculation coverage document."""
    step = calc_step(
        STEP_ID,
        program="orca",
        bindings={"structure": {"source": {"run": "structures"}}},
        native={"keyword": "B3LYP D3BJ def2-SVP Opt"},
        checks=["normal_termination"],
        scheduler={"max_parallel_items": 4},
        resources={"cores_per_item": 1, "memory_per_item": "1GB"},
        execution={"binding_id": "test", "executable": str(FAKE_ORCA)},
    )
    return v4_doc([step], inputs={"structures": {"kind": "structure", "cardinality": "many"}})


def _items(plan: Any, count: int) -> tuple[Any, ...]:
    """Assemble *count* distinct work items."""
    structures = StructureSet.of(
        *(structure(f"c{i:03d}", offset=float(i) * 0.002) for i in range(count))
    )
    assembly = assemble(plan, run_inputs(structures={"structures": structures}))
    assert assembly.ok, [item.message for item in assembly.errors]
    return tuple(assembly.items)


def _request(plan: Any, items: tuple[Any, ...], run_root: str) -> StepExecutionRequest:
    """Build a durable step request wired to the fake ORCA executable."""
    from confflow.execution.environment import EnvironmentMeasurer
    from confflow.execution.registry import default_registry

    planned = plan.steps[0]
    registry = default_registry()
    adapter = registry.resolve_program("orca")
    # Final contract (freeze §1/A + §7/D-11): single registry authority,
    # measured environment (never None).
    return StepExecutionRequest(
        step=planned,
        items=tuple(items),
        scientific=planned.scientific,
        scientific_defaults=plan.scientific_defaults,
        adapter=adapter,
        profile=registry.profile_implementation("standard"),
        checks=(registry.check_implementation("normal_termination"),),
        recovery=registry.recovery_implementation("none", adapter=adapter),
        execution_binding=ExecutionBinding(
            binding_id="test", executable=str(FAKE_ORCA), env=FrozenDict({})
        ),
        run_root=run_root,
        environment=EnvironmentMeasurer().build_environment(str(FAKE_ORCA), adapter=adapter),
        definition_digest=plan.definition_digest,
    )


def _result(item: Any, *, status: WorkItemStatus) -> WorkItemResult:
    """Build a minimal terminal result for *item*."""
    from confflow.domain.work_item import RecoveryInfo, ResultError, Timing

    error = None
    diagnostics: tuple[Diagnostic, ...] = ()
    if status is not WorkItemStatus.COMPLETED:
        error = ResultError(code="native_execution_error", message="boom", retryable=True)
        diagnostics = (Diagnostic(code="native_execution_error", message="boom"),)
    return WorkItemResult(
        work_item_id=item.id,
        status=status,
        diagnostics=diagnostics,
        timing=Timing(finished_at=wall_now(), duration_seconds=0.1),
        error=error,
        recovery=RecoveryInfo(profile="none", attempted=False),
        semantic_digest=item.semantic_digest,
    )


def _register(store: SqliteWorkItemStore, item: Any) -> None:
    """Register *item* with its live digests."""
    store.register_item(
        work_item_id=item.id,
        logical_key=item.logical_key,
        step_id=item.step_id,
        work_item_digest=item.semantic_digest,
        step_semantic_digest="sha256:" + "a" * 64,
        environment_digest=None,
        producer_provenance={},
    )
