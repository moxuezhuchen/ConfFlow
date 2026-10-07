#!/usr/bin/env python3

"""V4 multi-output entity identity and lineage authority (V4-5, frozen).

This module is owned by the main agent and is **frozen**: subagents read it
but never edit it.  It fixes the deterministic rules every multi-output
producer (path_endpoints, ensemble, NEB) must obey:

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
    "NEB_IMAGE_ROLE",
    "NEB_TS_CANDIDATE_ROLE",
    "PATH_ENDPOINT_FORWARD_ROLE",
    "PATH_ENDPOINT_REVERSE_ROLE",
    "conformer_output_id",
    "endpoint_lineage",
    "endpoint_output_id",
    "multi_output_structure_id",
    "multi_parent_lineage",
    "neb_image_output_id",
    "neb_ts_candidate_output_id",
    "output_ordering_key",
]

#: Semantic role of a forward IRC/NEB path endpoint.  Direction only; never
#: a reactant/product claim (chemistry assignment is V4-6 analysis work).
PATH_ENDPOINT_FORWARD_ROLE: Final[str] = "path_endpoint_forward"

#: Semantic role of a reverse IRC/NEB path endpoint.  Direction only.
PATH_ENDPOINT_REVERSE_ROLE: Final[str] = "path_endpoint_reverse"

#: Semantic role of one NEB path image (ordered ensemble member).
NEB_IMAGE_ROLE: Final[str] = "neb_image"

#: Semantic role of an NEB-TS optimized transition-state candidate.
#: Only used when the native program explicitly reports an optimized TS
#: candidate; a path maximum image must never carry this role.
NEB_TS_CANDIDATE_ROLE: Final[str] = "neb_ts_candidate"

#: Semantic role of one GOAT/ensemble conformer member.
CONFORMER_ROLE: Final[str] = "conformer"

#: Single-authority metadata key carrying the native conformer member index
#: on conformer StructureRecords.  Ensemble and confgen producers share this
#: key so the member ordinal is one typed contract, not two string literals.
CONFORMER_MEMBER_METADATA_KEY: Final[str] = "member_index"

_PATH_ROLES: Final = frozenset(
    {
        PATH_ENDPOINT_FORWARD_ROLE,
        PATH_ENDPOINT_REVERSE_ROLE,
        NEB_IMAGE_ROLE,
        NEB_TS_CANDIDATE_ROLE,
        CONFORMER_ROLE,
    }
)


def multi_output_structure_id(logical_key: str, role: str, ordinal: int = 0) -> str:
    """Return the deterministic entity id for one multi-output slot."""
    if not isinstance(logical_key, str) or not logical_key:
        raise ValueError("logical_key must be a non-empty string")
    if not isinstance(role, str) or not role:
        raise ValueError("role must be a non-empty string")
    if isinstance(ordinal, bool) or not isinstance(ordinal, int) or ordinal < 0:
        raise ValueError("ordinal must be an integer >= 0")
    return f"{logical_key}:structure:{role}:{ordinal}"


def endpoint_output_id(logical_key: str, direction: str) -> str:
    """Return the entity id for one reaction-path endpoint."""
    if direction == "forward":
        return multi_output_structure_id(logical_key, PATH_ENDPOINT_FORWARD_ROLE, 0)
    if direction == "reverse":
        return multi_output_structure_id(logical_key, PATH_ENDPOINT_REVERSE_ROLE, 0)
    raise ValueError(f"path direction must be 'forward' or 'reverse', got {direction!r}")


def conformer_output_id(logical_key: str, member_index: int) -> str:
    """Return the entity id for one ensemble conformer member."""
    return multi_output_structure_id(logical_key, CONFORMER_ROLE, member_index)


def neb_image_output_id(logical_key: str, image_index: int) -> str:
    """Return the entity id for one NEB path image."""
    return multi_output_structure_id(logical_key, NEB_IMAGE_ROLE, image_index)


def neb_ts_candidate_output_id(logical_key: str) -> str:
    """Return the entity id for the NEB-TS optimized TS candidate."""
    return multi_output_structure_id(logical_key, NEB_TS_CANDIDATE_ROLE, 0)


def endpoint_lineage(
    driving: StructureRecord,
) -> tuple[tuple[str, ...], str, str | None]:
    """Return ``(parent_ids, lineage_root_id, group_key)`` for path endpoints."""
    parent_ids = (driving.id,)
    lineage_root = driving.lineage_root_id or driving.id
    group_key = driving.group_key if driving.group_key is not None else driving.id
    return parent_ids, lineage_root, group_key


def multi_parent_lineage(
    parents_in_slot_order: tuple[StructureRecord, ...],
) -> tuple[tuple[str, ...], str | None, str | None]:
    """Return lineage for a multi-parent output (QST/NEB TS candidate)."""
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
    rank = {
        PATH_ENDPOINT_FORWARD_ROLE: 0,
        PATH_ENDPOINT_REVERSE_ROLE: 1,
        NEB_TS_CANDIDATE_ROLE: 2,
        NEB_IMAGE_ROLE: 3,
        CONFORMER_ROLE: 4,
    }.get(role, 99)
    number = ordinal if isinstance(ordinal, int) and ordinal >= 0 else 0
    return (rank, number)


def describe_output_slot(payload: dict[str, Any]) -> str:
    """Return a human-readable description of one output slot (diagnostics)."""
    return (
        f"{payload.get('role', '?')} ordinal {payload.get('ordinal', '?')} "
        f"of {payload.get('logical_key', '?')}"
    )
