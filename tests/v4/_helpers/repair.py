"""Shared V4 executor/repair fixtures (moved verbatim from test_repair_executors)."""

from __future__ import annotations

import hashlib
from types import SimpleNamespace
from typing import Any

from confflow.domain import FrozenDict, StructureSet
from confflow.domain.resources import ResourceRequest
from confflow.domain.structure import StructureRecord
from confflow.domain.work_item import WorkItem, WorkItemInputs, make_work_item_id
from confflow.execution.work_item_executor import ItemExecutionContext


def _digest(seed: str = "x") -> str:
    return "sha256:" + hashlib.sha256(seed.encode()).hexdigest()


def _butane(struct_id: str) -> StructureRecord:
    """Linear C4 chain (C-C 1.5 A) with per-carbon hydrogens omitted.

    Four carbons are enough for a 4-atom torsion chain; hydrogens are
    unnecessary for rotation/clash mechanics and are left out so the
    fixture stays minimal.
    """
    atoms = ("C", "C", "C", "C")
    coords = ((0.0, 0.0, 0.0), (1.5, 0.0, 0.0), (3.0, 0.4, 0.0), (4.5, 0.4, 0.0))
    return StructureRecord(id=struct_id, atoms=atoms, coordinates=coords, charge=0, multiplicity=1)


def _resources() -> ResourceRequest:
    return ResourceRequest(cores_per_item=2, memory_per_item_bytes=2**30)


def _item(
    logical_key: str, step_id: str, records: list[StructureRecord], port: str = "structure"
) -> WorkItem:
    return WorkItem(
        id=make_work_item_id(logical_key),
        logical_key=logical_key,
        step_id=step_id,
        named_inputs=WorkItemInputs(
            structures=FrozenDict({port: StructureSet(tuple(records))}),
        ),
        resources=_resources(),
        semantic_digest=_digest(logical_key),
    )


def _sci(**kwargs: Any) -> SimpleNamespace:
    base: dict[str, Any] = {
        "seed": None,
        "native": FrozenDict({}),
        "overrides": FrozenDict({}),
        "transform": None,
        "recovery_params": FrozenDict({}),
        "check_params": FrozenDict({}),
        "checks": (),
        "execution_adapter": "standard",
        "program": "orca",
    }
    base.update(kwargs)
    return SimpleNamespace(**base)


def _ctx(sci: SimpleNamespace, run_root: str, attempt: int = 0) -> ItemExecutionContext:
    return ItemExecutionContext(
        step_id="s",
        scientific=sci,
        scientific_defaults=SimpleNamespace(),
        adapter=None,
        profile=None,
        checks=(),
        recovery=None,  # type: ignore[arg-type]
        run_root=run_root,
        attempt=attempt,
    )
