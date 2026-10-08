#!/usr/bin/env python3

"""V4 multi-output helpers (V4-5).

Pure bookkeeping helpers shared by multi-output producers:
``resolve_multi_output_restart_subjects`` keeps artifacts already bound to an
emitted output, keeps restart-role artifacts bound to the input when several
outputs emit (work-item scoped, never copied to every output), re-subjects a
restart-role input-bound artifact to the single emitted output, and passes
everything else through unchanged. Imports only ``confflow.domain`` and the
frozen ``artifact_flow`` vocabulary.
"""

from __future__ import annotations

from dataclasses import replace

from ..domain.artifact import ArtifactRef, ArtifactSet
from ..workflow.v4.artifact_flow import RESTART_ROLES

__all__ = [
    "resolve_multi_output_restart_subjects",
]


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
