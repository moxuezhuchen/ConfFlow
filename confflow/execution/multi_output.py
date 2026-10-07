#!/usr/bin/env python3

"""V4 multi-output helpers (V4-5).

Pure presentation/bookkeeping helpers shared by every multi-output producer:
``order_item_structures`` sorts into deterministic presentation order without
changing identity; ``validate_multi_output_uniqueness`` fails closed
(``DomainError``) on duplicated structure ids;
``resolve_multi_output_restart_subjects`` keeps artifacts already bound to an
emitted output, keeps restart-role artifacts bound to the input when several
outputs emit (work-item scoped, never copied to every output), re-subjects a
restart-role input-bound artifact to the single emitted output, and passes
everything else through unchanged. Imports only ``confflow.domain`` and the
frozen ``artifact_flow`` vocabulary.
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
    """Return *structures* in deterministic presentation order."""
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
    """Reassign artifact subjects for one multi-output work item."""
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
