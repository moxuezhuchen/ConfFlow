#!/usr/bin/env python3

"""Typed cross-step artifact flow for ConfFlow Workflow V4 (V4-4).

Frozen restart vocabulary: :data:`RESTART_ROLES` pins the single
restart role; :func:`resolve_restart_subject` re-subjects outputs;
:func:`select_restart_artifact` binds one artifact by exact subject
and role, never by order, filename, or recency; :func:`filter_by_role`
narrows by exact role; :func:`verify_binding_cardinality` enforces
per-port cardinality; :func:`subject_for_output` applies the no-guessing
rule for passthrough chains.

Assembly (:mod:`confflow.workflow.v4.assembly`) matches ``BY_SUBJECT``,
excludes locators from digests, and reports cardinality diagnostics;
this module extends that for the executor-owned restart path.
"""

from __future__ import annotations

from typing import Final

from ...domain.artifact import ArtifactRef, ArtifactSet
from ...domain.binding import Cardinality
from ...domain.errors import DomainError

__all__ = [
    "ARTIFACT_CARDINALITY_INVALID",
    "ARTIFACT_SUBJECT_AMBIGUOUS",
    "ARTIFACT_SUBJECT_MISSING",
    "RESTART_ROLES",
    "ArtifactFlowError",
    "filter_by_role",
    "resolve_restart_subject",
    "select_restart_artifact",
    "subject_for_output",
    "verify_binding_cardinality",
]

#: Machine-readable code for a subject with no matching artifact.
ARTIFACT_SUBJECT_MISSING: Final = "artifact_subject_missing"

#: Machine-readable code for a subject with more than one matching artifact.
ARTIFACT_SUBJECT_AMBIGUOUS: Final = "artifact_subject_ambiguous"

#: Machine-readable code for an unrecognized cardinality contract name.
ARTIFACT_CARDINALITY_INVALID: Final = "artifact_cardinality_invalid"

#: The single restart semantic role.
#:
#: Program-native restart files are normalized to this role by their program
#: adapter (for example the ORCA adapter maps its ``checkpoint_wavefunction``
#: file to ``checkpoint`` and keeps the native format in
#: ``metadata["program_format"]``); roles are semantic, never extension
#: aliases, so no other role carries restart semantics.
RESTART_ROLES: Final = frozenset({"checkpoint"})


class ArtifactFlowError(DomainError):
    """Typed failure of cross-step artifact flow."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        step_id: str = "",
        logical_key: str = "",
        port: str = "",
    ) -> None:
        """Store the machine-readable code and build a context-rich message."""
        self.code = code
        self.step_id = step_id
        self.logical_key = logical_key
        self.port = port
        super().__init__(
            f"{message} "
            f"(code={code}, step_id={step_id!r}, "
            f"logical_key={logical_key!r}, port={port!r})"
        )


def resolve_restart_subject(
    *,
    native_subject: str | None,
    output_structure_id: str,
    geometry_semantics: str,
) -> str:
    """Return the subject id restart artifacts bind to after a profile step."""
    del geometry_semantics
    if output_structure_id:
        return output_structure_id
    return native_subject or ""


def select_restart_artifact(
    *,
    artifacts: ArtifactSet,
    subject_structure_id: str,
    role: str = "checkpoint",
    step_id: str = "",
    logical_key: str = "",
    port: str = "",
) -> ArtifactRef:
    """Return the single artifact bound to *subject_structure_id* with *role*.

    Parameters
    ----------
    artifacts : ArtifactSet
        Candidate artifacts, typically already narrowed to one producer.
    subject_structure_id : str
        Structure the selected artifact must be bound to.
    role : str
        Required semantic role (default ``"checkpoint"``); matched exactly.
    step_id : str
        Step context carried into the error message.
    logical_key : str
        Work-item context carried into the error message.
    port : str
        Port context carried into the error message.

    Returns
    -------
    ArtifactRef
        The unique artifact whose ``subject_structure_id`` and ``role``
        both match exactly.

    Raises
    ------
    ArtifactFlowError
        With code ``artifact_subject_missing`` when no artifact matches, or
        ``artifact_subject_ambiguous`` when more than one matches.  Ambiguity
        is never resolved by order, recency, or filename.
    """
    matches = tuple(
        record
        for record in artifacts
        if record.subject_structure_id == subject_structure_id and record.role == role
    )
    if not matches:
        raise ArtifactFlowError(
            ARTIFACT_SUBJECT_MISSING,
            f"no artifact with role {role!r} bound to subject "
            f"{subject_structure_id!r} among {len(artifacts)} candidates",
            step_id=step_id,
            logical_key=logical_key,
            port=port,
        )
    if len(matches) > 1:
        raise ArtifactFlowError(
            ARTIFACT_SUBJECT_AMBIGUOUS,
            f"{len(matches)} artifacts with role {role!r} bound to subject "
            f"{subject_structure_id!r}; refusing to guess",
            step_id=step_id,
            logical_key=logical_key,
            port=port,
        )
    return matches[0]


def filter_by_role(artifacts: ArtifactSet, roles: frozenset[str]) -> ArtifactSet:
    """Return the sub-set of *artifacts* whose role is a member of *roles*."""
    wanted = frozenset(roles)
    return ArtifactSet(tuple(record for record in artifacts if record.role in wanted))


def _normalize_cardinality(cardinality: str | Cardinality) -> str:
    """Return the lowercase cardinality name for *cardinality*."""
    if isinstance(cardinality, Cardinality):
        return cardinality.value
    return str(cardinality).strip().lower()


def verify_binding_cardinality(
    *,
    artifacts: ArtifactSet,
    port_name: str,
    cardinality: str | Cardinality,
    subject_structure_id: str | None,
    step_id: str = "",
    logical_key: str = "",
    port: str = "",
) -> None:
    """Enforce *cardinality* on an already subject-selected artifact set.

    Parameters
    ----------
    artifacts : ArtifactSet
        Artifacts already selected for one subject; only the count is
        checked here, never the membership.
    port_name : str
        Target port name used in messages when ``port`` is empty.
    cardinality : str | Cardinality
        Required contract: ``"one"`` needs exactly one artifact,
        ``"one_or_more"`` needs at least one, while ``"many"`` and
        ``"optional"`` pass through (empty is allowed for ``"optional"``).
    subject_structure_id : str | None
        Subject the set was selected for, named in error messages.
    step_id : str
        Step context carried into the error message.
    logical_key : str
        Work-item context carried into the error message.
    port : str
        Port context carried into the error message; defaults to
        ``port_name`` in the message when empty.

    Raises
    ------
    ArtifactFlowError
        With code ``artifact_subject_missing`` when a required artifact is
        absent, ``artifact_subject_ambiguous`` when ``"one"`` matches more
        than one, or ``artifact_cardinality_invalid`` for an unknown
        cardinality name (fail closed, never guess).
    """
    label = port or port_name
    count = len(artifacts)
    key = _normalize_cardinality(cardinality)
    if key == Cardinality.ONE.value:
        if count == 0:
            raise ArtifactFlowError(
                ARTIFACT_SUBJECT_MISSING,
                f"port {label!r} requires exactly one artifact for subject "
                f"{subject_structure_id!r} but matched 0",
                step_id=step_id,
                logical_key=logical_key,
                port=label,
            )
        if count > 1:
            raise ArtifactFlowError(
                ARTIFACT_SUBJECT_AMBIGUOUS,
                f"port {label!r} requires exactly one artifact for subject "
                f"{subject_structure_id!r} but matched {count}; refusing to guess",
                step_id=step_id,
                logical_key=logical_key,
                port=label,
            )
        return None
    if key == Cardinality.ONE_OR_MORE.value:
        if count == 0:
            raise ArtifactFlowError(
                ARTIFACT_SUBJECT_MISSING,
                f"port {label!r} requires at least one artifact for subject "
                f"{subject_structure_id!r} but matched 0",
                step_id=step_id,
                logical_key=logical_key,
                port=label,
            )
        return None
    if key in (Cardinality.MANY.value, Cardinality.OPTIONAL.value):
        return None
    raise ArtifactFlowError(
        ARTIFACT_CARDINALITY_INVALID,
        f"port {label!r} declares unknown cardinality {cardinality!r} "
        f"for subject {subject_structure_id!r}",
        step_id=step_id,
        logical_key=logical_key,
        port=label,
    )


def subject_for_output(
    *,
    input_structure_id: str,
    output_structure_id: str | None,
) -> str:
    """Return the subject id downstream bindings must use for an output."""
    if output_structure_id:
        return output_structure_id
    return input_structure_id
