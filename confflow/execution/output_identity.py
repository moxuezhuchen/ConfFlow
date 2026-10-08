#!/usr/bin/env python3

"""V4 multi-output entity identity and lineage authority (V4-5, frozen).

This module is owned by the main agent and is **frozen**: subagents read it
but never edit it.  It fixes the deterministic rules every ensemble
producer must obey:

- entity ids derive from the producer work-item logical identity plus a
  semantic output role plus a stable ordinal — never from parser order,
  wall-clock time, randomness, or list position;
- lineage and group keys propagate by rule, never by guessing;
- output ordering is a presentation rule; downstream pairing always uses
  identity, group, and subject, never order.

Dependency rule: ``confflow.domain`` plus stdlib only.
"""

from __future__ import annotations

from typing import Any, Final

from ..domain.structure import StructureRecord

__all__ = [
    "CONFORMER_MEMBER_METADATA_KEY",
    "CONFORMER_ROLE",
    "conformer_output_id",
    "endpoint_lineage",
    "multi_output_structure_id",
    "multi_parent_lineage",
    "output_ordering_key",
]

#: Semantic role of one ensemble conformer member.
CONFORMER_ROLE: Final[str] = "conformer"

#: Single-authority metadata key carrying the native conformer member index
#: on conformer StructureRecords.  Ensemble and confgen producers share this
#: key so the member ordinal is one typed contract, not two string literals.
CONFORMER_MEMBER_METADATA_KEY: Final[str] = "member_index"


def multi_output_structure_id(logical_key: str, role: str, ordinal: int = 0) -> str:
    """Return the deterministic entity id for one multi-output slot."""
    if not isinstance(logical_key, str) or not logical_key:
        raise ValueError("logical_key must be a non-empty string")
    if not isinstance(role, str) or not role:
        raise ValueError("role must be a non-empty string")
    if isinstance(ordinal, bool) or not isinstance(ordinal, int) or ordinal < 0:
        raise ValueError("ordinal must be an integer >= 0")
    return f"{logical_key}:structure:{role}:{ordinal}"


def conformer_output_id(logical_key: str, member_index: int) -> str:
    """Return the entity id for one ensemble conformer member."""
    return multi_output_structure_id(logical_key, CONFORMER_ROLE, member_index)


def endpoint_lineage(
    driving: StructureRecord,
) -> tuple[tuple[str, ...], str, str | None]:
    """Return ``(parent_ids, lineage_root_id, group_key)`` for ensemble members."""
    parent_ids = (driving.id,)
    lineage_root = driving.lineage_root_id or driving.id
    group_key = driving.group_key if driving.group_key is not None else driving.id
    return parent_ids, lineage_root, group_key


def multi_parent_lineage(
    parents_in_slot_order: tuple[StructureRecord, ...],
) -> tuple[tuple[str, ...], str | None, str | None]:
    """Return lineage for a multi-parent output."""
    if not parents_in_slot_order:
        raise ValueError("multi-parent lineage requires at least one parent")
    parent_ids = tuple(record.id for record in parents_in_slot_order)
    roots = {record.lineage_root_id for record in parents_in_slot_order}
    lineage_root: str | None = None
    if len(roots) == 1:
        sole = next(iter(roots))
        lineage_root = sole if sole else None
    keys = {record.group_key for record in parents_in_slot_order}
    group_key: str | None = None
    if len(keys) == 1:
        sole_key = next(iter(keys))
        group_key = sole_key if sole_key else None
    return parent_ids, lineage_root, group_key


def output_ordering_key(role: str, ordinal: int | None) -> tuple[int, int]:
    """Return the deterministic presentation order key for one output."""
    rank = {CONFORMER_ROLE: 4}.get(role, 99)
    number = ordinal if isinstance(ordinal, int) and ordinal >= 0 else 0
    return (rank, number)


def describe_output_slot(payload: dict[str, Any]) -> str:
    """Return a human-readable description of one output slot (diagnostics)."""
    return (
        f"{payload.get('role', '?')} ordinal {payload.get('ordinal', '?')} "
        f"of {payload.get('logical_key', '?')}"
    )
