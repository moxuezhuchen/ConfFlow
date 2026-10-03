#!/usr/bin/env python3

"""A required input of zero structures fails; it is never a silent success (A1).

Assembly layer (A1-2): a required structure port is not satisfied by artifacts
or results that happen to sit beside an empty structure set.
"""

from __future__ import annotations

from confflow.domain import FrozenDict, StructureSet
from confflow.domain.completion import StepStatus
from confflow.workflow.v4 import MaterializedOutputs, StepOutputs, assemble_work_items
from tests.v4._builders import (
    calc_step,
    checkpoint_set,
    compile_doc,
    run_inputs,
    structure_set,
    v4_doc,
)

STRUCTURE_INPUTS = {"structures": {"kind": "structure", "cardinality": "many"}}


# -- A1-2: assembly ------------------------------------------------------------


def test_artifacts_do_not_satisfy_an_empty_required_structure_port() -> None:
    doc = v4_doc(
        [
            calc_step("s_opt", bindings={"structure": {"source": {"run": "structures"}}}),
            calc_step(
                "s_freq",
                bindings={"structure": {"source": {"step": "s_opt", "port": "structures"}}},
            ),
        ],
        inputs=STRUCTURE_INPUTS,
    )
    plan = compile_doc(doc).plan
    assert plan is not None
    producer = StepOutputs(
        step_id="s_opt",
        structures=StructureSet(),
        artifacts=checkpoint_set("s0", producer_step_id="s_opt"),
        status=StepStatus.COMPLETED,
    )
    assembly = assemble_work_items(
        plan,
        run_inputs(structures={"structures": structure_set("s0")}),
        materialized=MaterializedOutputs(steps=FrozenDict({"s_opt": producer})),
    )
    messages = [item.message for item in assembly.errors if item.step_id == "s_freq"]
    assert messages == [
        "required structure port 'structure' has no values "
        "(step 's_opt' published no structure for it)"
    ]
    assert assembly.for_step("s_freq") == ()
