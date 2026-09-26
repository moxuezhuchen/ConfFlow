#!/usr/bin/env python3

"""V4 multi-output helpers (V4-5).

Pure, presentation- and bookkeeping-level helpers shared by every
multi-output producer (path endpoints, ensembles):

- :func:`order_item_structures` sorts one work item's structures into the
  deterministic presentation order without changing identity;
- :func:`validate_multi_output_uniqueness` fails closed on duplicated
  structure ids;
- :func:`resolve_multi_output_restart_subjects` applies the restart-subject
  rule for multi-output items: an artifact already bound to one of the
  emitted outputs keeps that subject; a restart-role artifact still bound to
  the consumed input keeps the input subject when several outputs were
  emitted (work-item scoped, never copied to every output); a restart-role
  artifact bound to the input re-subjects to the single emitted output;
  everything else passes through unchanged.

Only ``confflow.domain`` and the frozen
:mod:`confflow.workflow.v4.artifact_flow` vocabulary are imported here.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import replace

from ..domain.artifact import ArtifactRef, ArtifactSet
from ..domain.errors import DomainError
from ..domain.structure import StructureRecord, StructureSet
from ..workflow.v4.artifact_flow import RESTART_ROLES
from .output_identity import output_ordering_key

__all__ = [
    "order_item_structures",
    "resolve_multi_output_restart_subjects",
    "validate_multi_output_uniqueness",
]


def order_item_structures(structures: StructureSet) -> StructureSet:
    """Return *structures* in deterministic presentation order.

    Parameters
    ----------
    structures : StructureSet
        Structures emitted by one work item, in any order.

    Returns
    -------
    StructureSet
        The same records sorted by ``output_ordering_key(role, ordinal)``
        (stable: records with equal keys keep their input order).  Ordering
        is presentation only; downstream pairing must use identity, group,
        and subject, never this order.
    """
    ordered = tuple(
        sorted(
            structures.structures,
            key=lambda record: output_ordering_key(record.role or "", record.ordinal),
        )
    )
    return StructureSet(ordered)


def validate_multi_output_uniqueness(
    structures: StructureSet | Iterable[StructureRecord],
) -> None:
    """Fail closed when two records share one structure id.

    Parameters
    ----------
    structures : StructureSet | Iterable[StructureRecord]
        Records emitted by one multi-output work item.

    Returns
    -------
    None

    Raises
    ------
    DomainError
        Raised when any structure id appears more than once.
    """
    if isinstance(structures, StructureSet):
        records = structures.structures
    else:
        records = tuple(structures)
    seen: set[str] = set()
    for record in records:
        if record.id in seen:
            raise DomainError(f"duplicate multi-output structure id: {record.id!r}")
        seen.add(record.id)


def resolve_multi_output_restart_subjects(
    *,
    artifacts: ArtifactSet,
    output_ids: tuple[str, ...],
    input_subject: str | None,
) -> ArtifactSet:
    """Reassign artifact subjects for one multi-output work item.

    Parameters
    ----------
    artifacts : ArtifactSet
        Executor-discovered artifacts bound (or not) to the consumed input.
    output_ids : tuple[str, ...]
        Ids of the structures the profile emitted for this work item.
    input_subject : str | None
        Id of the structure the work item consumed, if known.

    Returns
    -------
    ArtifactSet
        The reassigned set, in the original order:

        - an artifact already bound to one of *output_ids* keeps it;
        - a restart-role artifact bound to *input_subject* keeps the input
          subject when more than one output was emitted (work-item scoped;
          it is never copied to every output), and re-subjects to the
          single output when exactly one was emitted;
        - every other artifact passes through unchanged (including
          restart-role artifacts with an unknown or unbound subject).
    """
    outputs = tuple(output_ids)
    reassigned: list[ArtifactRef] = []
    for artifact in artifacts:
        subject = artifact.subject_structure_id
        if subject is not None and subject in outputs:
            reassigned.append(artifact)
            continue
        if (
            artifact.role in RESTART_ROLES
            and input_subject is not None
            and subject == input_subject
        ):
            if len(outputs) == 1:
                reassigned.append(replace(artifact, subject_structure_id=outputs[0]))
            else:
                reassigned.append(artifact)
            continue
        reassigned.append(artifact)
    return ArtifactSet(tuple(reassigned))
