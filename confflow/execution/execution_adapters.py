#!/usr/bin/env python3

"""V4 execution-adapter runtime (V4-5, frozen, main-owned).

An execution adapter resolves *input shape* — which structure slots fill
a work item and how they are presented to the program adapter.  It never
parses program output (program adapter's job), never judges acceptance
(checks' job), and never dispatches on task names (there are no task
types in V4).

One adapter exists, matching the registry spec byte-for-byte:

- ``standard``: exactly one structure on the driving ``structure`` port.

Dependency rule: ``confflow.domain`` plus stdlib only.
"""

from __future__ import annotations

from typing import Final

from ..domain.errors import DomainError
from ..domain.structure import StructureRecord
from ..domain.work_item import WorkItem

__all__ = [
    "DRIVING_STRUCTURE_PORT",
    "NATIVE_TEMPLATE_ADAPTER",
    "STANDARD_ADAPTER",
    "resolve_standard_structure",
]

#: Adapter name for single-structure calculations (registry-identical).
STANDARD_ADAPTER: Final[str] = "standard"

#: Adapter name for native-template calculations (registry-identical).
NATIVE_TEMPLATE_ADAPTER: Final[str] = "native_template"

#: Port carrying the single driving structure under the standard adapter.
DRIVING_STRUCTURE_PORT: Final[str] = "structure"


def resolve_standard_structure(work_item: WorkItem) -> StructureRecord:
    """Return the single driving structure of *work_item*.

    Requires exactly one structure on the ``structure`` port; anything
    else fails closed (multi-structure execution is never positional
    guessing).
    """
    structures = work_item.named_inputs.structures.get(DRIVING_STRUCTURE_PORT)
    if structures is None or len(structures) != 1:
        raise DomainError(
            f"work item {work_item.logical_key!r} must carry exactly one structure "
            f"on port {DRIVING_STRUCTURE_PORT!r} for standard execution"
        )
    record = structures[0]
    if not isinstance(record, StructureRecord):  # pragma: no cover - guarded by model
        raise DomainError(f"port {DRIVING_STRUCTURE_PORT!r} holds a non-structure value")
    return record
