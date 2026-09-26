#!/usr/bin/env python3

"""V4 execution-adapter runtime (V4-5, frozen, main-owned).

An execution adapter resolves *input shape* — which named structure slots
fill a work item and how they are presented to the program adapter.  It
never parses program output (program adapter's job), never judges
acceptance (checks' job), and never dispatches on task names (there are
no task types in V4: ``calculation``/``confgen`` capabilities plus
adapter/profile/native-definition express IRC, QST, NEB, and GOAT).

Two adapters exist, matching the registry specs byte-for-byte:

- ``standard``: exactly one structure on the driving ``structure`` port.
- ``named_structures``: ``reactant``/``product``/optional ``guess`` slots
  paired by explicit group key at assembly time; structural resolution
  only — semantic validation (cardinality, compatibility, atom mapping)
  lives in :mod:`confflow.execution.named_structures` and
  :mod:`confflow.execution.atom_mapping`.

Dependency rule: ``confflow.domain`` plus stdlib only.
"""

from __future__ import annotations

from typing import Final

from ..domain._immutable import FrozenDict
from ..domain.errors import DomainError
from ..domain.structure import StructureRecord, StructureSet
from ..domain.work_item import WorkItem

__all__ = [
    "DRIVING_STRUCTURE_PORT",
    "GUESS_SLOT",
    "NAMED_STRUCTURES_ADAPTER",
    "NATIVE_TEMPLATE_ADAPTER",
    "PRODUCT_SLOT",
    "REACTANT_SLOT",
    "STANDARD_ADAPTER",
    "resolve_named_slot_sets",
    "resolve_standard_structure",
]

#: Adapter name for single-structure calculations (registry-identical).
STANDARD_ADAPTER: Final[str] = "standard"

#: Adapter name for named-slot calculations (registry-identical).
NAMED_STRUCTURES_ADAPTER: Final[str] = "named_structures"

#: Adapter name for native-template calculations (registry-identical).
NATIVE_TEMPLATE_ADAPTER: Final[str] = "native_template"

#: Port carrying the single driving structure under the standard adapter.
DRIVING_STRUCTURE_PORT: Final[str] = "structure"

#: Named slot holding the reactant structure.
REACTANT_SLOT: Final[str] = "reactant"

#: Named slot holding the product structure.
PRODUCT_SLOT: Final[str] = "product"

#: Named slot holding the optional transition-state guess (QST3).
GUESS_SLOT: Final[str] = "guess"

#: Slots in semantic order (reactant, product, guess) — the only order
#: multi-parent lineage and materialization may use.
NAMED_SLOT_ORDER: Final = (REACTANT_SLOT, PRODUCT_SLOT, GUESS_SLOT)


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


def resolve_named_slot_sets(work_item: WorkItem) -> FrozenDict:
    """Return the named structure slots of *work_item*, keyed by slot name.

    Structural resolution only: every structure port present on the item
    is returned as-is, in slot order.  Cardinality (exactly one reactant,
    exactly one product, zero-or-one guess), group-key agreement, and
    atom compatibility are enforced by
    :mod:`confflow.execution.named_structures`, not here.
    """
    slots: dict[str, StructureSet] = {}
    for port in sorted(work_item.named_inputs.structures):
        value = work_item.named_inputs.structures[port]
        if isinstance(value, StructureSet) and len(value):
            slots[port] = value
    ordered = {slot: slots[slot] for slot in NAMED_SLOT_ORDER if slot in slots}
    ordered.update({port: sets for port, sets in sorted(slots.items()) if port not in ordered})
    return FrozenDict(ordered)
