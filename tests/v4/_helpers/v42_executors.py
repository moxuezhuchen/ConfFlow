"""Shared V4.2 adapter/executor fixtures (moved verbatim)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from confflow.domain import FrozenDict, StructureSet
from confflow.execution import ExecutionBinding
from confflow.execution.checks_standard import CHECKS
from confflow.execution.profile_standard import PROFILES
from confflow.execution.recovery_standard import RECOVERIES
from confflow.execution.work_item_executor import ItemExecutionContext
from confflow.programs.registry import get_program_adapter
from tests.v4._builders import assemble, calc_step, compile_doc, run_inputs, v4_doc

FAKES_DIR = (
    Path(__file__).resolve().parents[1] / "fakes"
)  # L0.7: tests/v4/fakes (was parent / "fakes" in the test module)

FAKE_G16 = FAKES_DIR / "fake_g16.py"


FAKE_ORCA = FAKES_DIR / "fake_orca.py"


ORCA_NATIVE = {"keyword": "B3LYP D3BJ def2-SVP Opt"}


GAUSSIAN_NATIVE = {"keyword": "B3LYP/6-31G* Opt"}


STRUCTURE_INPUTS = {"structures": {"kind": "structure", "cardinality": "many"}}


def calculation_doc(
    program: str,
    native: dict[str, Any],
    executable: str,
    *,
    checks: list[str] | None = None,
    check_params: dict[str, dict[str, Any]] | None = None,
    step_id: str = "s_opt",
    scheduler: dict[str, Any] | None = None,
    completion: dict[str, Any] | None = None,
    resources: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a single-calculation document bound to *executable*."""
    step = calc_step(
        step_id,
        program="g16" if program == "gaussian" else "orca",
        bindings={"structure": {"source": {"run": "structures"}}},
        native=native,
        checks=checks if checks is not None else ["normal_termination"],
        scheduler=scheduler,
        completion=completion,
        resources=(
            resources if resources is not None else {"cores_per_item": 4, "memory_per_item": "16GB"}
        ),
        execution={"binding_id": "test", "executable": executable},
    )
    if check_params:
        step["calculation"]["check_params"] = check_params
    return v4_doc([step], inputs=STRUCTURE_INPUTS)


def compile_plan(document: dict[str, Any]) -> Any:
    """Compile *document*, asserting a clean compile."""
    compiled = compile_doc(document)
    assert compiled.ok, [(item.code, item.message) for item in compiled.errors]
    assert compiled.plan is not None
    return compiled.plan


def assemble_items(plan: Any, structures: StructureSet) -> Any:
    """Assemble work items for *structures*, asserting success."""
    assembly = assemble(plan, run_inputs(structures={"structures": structures}))
    assert assembly.ok, [item.message for item in assembly.errors]
    return assembly.items


def binding_for(executable: str) -> ExecutionBinding:
    """Build a test execution binding for a fake executable."""
    return ExecutionBinding(binding_id="test", executable=executable, env=FrozenDict({}))


def item_context(
    plan: Any,
    program: str,
    executable: str,
    run_root: str,
    work_base: str,
    supervisor: Any,
    checks: list[str],
    *,
    recovery: Any = None,
    scientific_defaults: Any = None,
) -> ItemExecutionContext:
    """Build an item execution context wired to real adapters and profiles.

    ``scientific_defaults`` overrides the plan's run-level defaults; tests use
    the empty ``ScientificDefaults()`` to reproduce a request that bypassed
    document validation (the executor keeps its own fail-closed check).
    """
    planned = plan.steps[0]
    return ItemExecutionContext(
        step_id=planned.step_id,
        scientific=planned.scientific,
        scientific_defaults=(
            plan.scientific_defaults if scientific_defaults is None else scientific_defaults
        ),
        adapter=get_program_adapter(program),
        profile=PROFILES["standard"],
        checks=tuple(CHECKS[name] for name in checks),
        recovery=recovery if recovery is not None else RECOVERIES["none"],
        execution_binding=binding_for(executable),
        run_root=run_root,
        work_base=work_base,
        supervisor=supervisor,
        environment=None,
        poll_interval_seconds=0.05,
    )
