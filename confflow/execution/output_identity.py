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
    """Return the deterministic entity id for one multi-output slot.

    Parameters
    ----------
    logical_key : str
        Logical key of the producing work item (``<step id>:<group>``).
    role : str
        Semantic output role (one of the frozen role constants above).
    ordinal : int
        Stable ordinal within ``(logical_key, role)``.  For single-slot
        roles (each path direction) this is always ``0``; for ensemble
        roles it is the native member/image index, never the parser
        position.

    Returns
    -------
    str
        ``"<logical_key>:structure:<role>:<ordinal>"`` — stable across
        retry, resume, local, and remote execution of the same slot.
    """
    if not isinstance(logical_key, str) or not logical_key:
        raise ValueError("logical_key must be a non-empty string")
    if not isinstance(role, str) or not role:
        raise ValueError("role must be a non-empty string")
    if isinstance(ordinal, bool) or not isinstance(ordinal, int) or ordinal < 0:
        raise ValueError("ordinal must be an integer >= 0")
    return f"{logical_key}:structure:{role}:{ordinal}"


def endpoint_output_id(logical_key: str, direction: str) -> str:
    """Return the entity id for one reaction-path endpoint.

    Parameters
    ----------
    logical_key : str
        Logical key of the producing work item.
    direction : str
        ``"forward"`` or ``"reverse"`` (native path direction only).

    Returns
    -------
    str
        Deterministic endpoint id; the direction selects the role, so a
        shuffled parser order can never swap the two endpoints.
    """
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
    """Return ``(parent_ids, lineage_root_id, group_key)`` for path endpoints.

    Both endpoints of one IRC/NEB item share the same lineage: the single
    driving (TS) structure is the parent, its lineage root propagates (or
    its own id when it has none beyond itself), and the group key is the
    driving group key when present, else the deterministic driving id.
    """
    parent_ids = (driving.id,)
    lineage_root = driving.lineage_root_id or driving.id
    group_key = driving.group_key if driving.group_key is not None else driving.id
    return parent_ids, lineage_root, group_key


def multi_parent_lineage(
    parents_in_slot_order: tuple[StructureRecord, ...],
) -> tuple[tuple[str, ...], str | None, str | None]:
    """Return lineage for a multi-parent output (QST/NEB TS candidate).

    Parameters
    ----------
    parents_in_slot_order : tuple[StructureRecord, ...]
        Parent structures in semantic slot order
        (reactant, product, guess) — never dict or random order.

    Returns
    -------
    tuple
        ``(parent_ids, lineage_root_id, group_key)``.  The lineage root
        propagates only when every parent shares one non-empty root;
        otherwise ``None`` (never an arbitrary pick).  The group key
        propagates only when every parent shares one non-empty key.
    """
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
    """Return the deterministic presentation order key for one output.

    Within an item, ``path_endpoint_forward`` sorts before
    ``path_endpoint_reverse``; NEB images and conformers sort by stable
    ordinal; anything else sorts last by ordinal.  Correctness must never
    depend on this order — it only makes serialized sets reproducible.
    """
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
