"""Shared V4.2 adapter input builders (moved verbatim from test_v42_adapters)."""

from __future__ import annotations

from confflow.domain import FrozenDict, ResourceRequest
from confflow.execution.native import ResolvedCalculationInputs
from tests.v4._builders import structure


def gaussian_inputs(**overrides: object) -> ResolvedCalculationInputs:
    """Build resolved calculation inputs for the Gaussian adapter."""
    params: dict[str, object] = {
        "structure": structure("s0"),
        "charge": 0,
        "multiplicity": 1,
        "freeze": None,
        "resources": ResourceRequest.from_values(cores_per_item=4, memory_per_item="16GB"),
        "native": FrozenDict({"keyword": "B3LYP/6-31G* Opt"}),
        "checkpoints": (),
        "step_id": "s_opt",
        "work_item_id": "wi:s_opt:item0",
        "logical_key": "s_opt:item0",
    }
    params.update(overrides)
    return ResolvedCalculationInputs(**params)  # type: ignore[arg-type]


def orca_inputs(**overrides: object) -> ResolvedCalculationInputs:
    """Build resolved calculation inputs for the ORCA adapter."""
    params: dict[str, object] = {
        "structure": structure("s0"),
        "charge": 0,
        "multiplicity": 1,
        "freeze": None,
        "resources": ResourceRequest.from_values(cores_per_item=4, memory_per_item="16GB"),
        "native": FrozenDict({"keyword": "B3LYP D3BJ def2-SVP Opt"}),
        "checkpoints": (),
        "step_id": "s_opt",
        "work_item_id": "wi:s_opt:item0",
        "logical_key": "s_opt:item0",
    }
    params.update(overrides)
    return ResolvedCalculationInputs(**params)  # type: ignore[arg-type]
