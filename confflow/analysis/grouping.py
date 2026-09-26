#!/usr/bin/env python3

"""Reaction-group discovery for ConfFlow Workflow V4 (V4-6).

:func:`build_reaction_groups` pairs transition-state structures with
their native forward/reverse path endpoints into :class:`ReactionGroup`
records.  Grouping keys on ``(group_key)`` plus subject ids plus
endpoint roles only:

- structures partition by explicit ``group_key`` (never inferred);
- within a group, endpoints are the members carrying the frozen roles
  ``path_endpoint_forward`` / ``path_endpoint_reverse`` (imported from
  ``confflow.execution.output_identity``, the role authority);
- the transition state is the member that is a parent of *both*
  endpoints (the structure both endpoints were derived from);
- duplicate transition states or endpoints never resolve by
  lowest-energy-wins: the slot stays empty and the group is marked
  failed with an ambiguity diagnostic;
- missing transition states or endpoints fail the group closed with a
  missing diagnostic; a partial group is never filled from another
  group;
- results attach by ``subject_structure_id``; a result naming an
  unknown subject (or no subject) yields a subject-mismatch diagnostic.

Everything iterates in sorted id/key order, so shuffled input order
produces the identical semantic payload (see
:func:`reaction_groups_digest`).

Only ``confflow.domain`` and the frozen role authority plus stdlib are
imported here: no programs, no file names, no ordinals.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any, Final

from ..domain._immutable import FrozenDict
from ..domain.canonical import typed_digest
from ..domain.diagnostics import Diagnostic, DiagnosticSeverity
from ..domain.result import ResultSet
from ..domain.structure import StructureRecord, StructureSet
from ..execution.output_identity import (
    PATH_ENDPOINT_FORWARD_ROLE,
    PATH_ENDPOINT_REVERSE_ROLE,
)
from .models import AnalysisError, ReactionGroup

__all__ = [
    "REACTION_GROUPS_DIGEST_KIND",
    "build_reaction_groups",
    "reaction_groups_digest",
]

#: Digest domain marker for :func:`reaction_groups_digest`.
REACTION_GROUPS_DIGEST_KIND: Final[str] = "confflow.analysis.reaction_groups.v1"

_ENDPOINT_ROLES: Final[tuple[str, str]] = (
    PATH_ENDPOINT_FORWARD_ROLE,
    PATH_ENDPOINT_REVERSE_ROLE,
)


def _diagnostic(
    code: str,
    message: str,
    *,
    severity: DiagnosticSeverity = DiagnosticSeverity.ERROR,
    group_key: str | None = None,
    subject_structure_id: str | None = None,
    reason: str,
    details: dict[str, Any] | None = None,
) -> Diagnostic:
    """Build one typed grouping diagnostic."""
    payload: dict[str, Any] = {"reason": reason}
    if group_key is not None:
        payload["group_key"] = group_key
    if subject_structure_id is not None:
        payload["subject_structure_id"] = subject_structure_id
    if details:
        payload.update(details)
    return Diagnostic(
        code=code,
        message=message,
        severity=severity,
        details=FrozenDict(payload),
    )


def _resolve_endpoints(
    members: tuple[StructureRecord, ...],
    group_key: str,
    diagnostics: list[Diagnostic],
) -> tuple[str | None, str | None]:
    """Resolve forward/reverse endpoint ids, recording failures.

    Parameters
    ----------
    members : tuple[StructureRecord, ...]
        Group members in sorted id order.
    group_key : str
        Reaction pairing key (diagnostic context only).
    diagnostics : list[Diagnostic]
        Mutable per-group diagnostic sink.

    Returns
    -------
    tuple
        ``(forward_id, reverse_id)``; a slot is ``None`` when its
        endpoint is missing or ambiguously claimed.
    """
    slots: list[str | None] = []
    for role, slot in (
        (PATH_ENDPOINT_FORWARD_ROLE, "forward"),
        (PATH_ENDPOINT_REVERSE_ROLE, "reverse"),
    ):
        claimed = sorted(record.id for record in members if record.role == role)
        if not claimed:
            diagnostics.append(
                _diagnostic(
                    "analysis_endpoint_missing",
                    f"reaction group {group_key!r} has no {slot} endpoint",
                    group_key=group_key,
                    reason="missing_endpoint",
                    details={"endpoint": slot},
                )
            )
            slots.append(None)
        elif len(claimed) > 1:
            diagnostics.append(
                _diagnostic(
                    "analysis_endpoint_ambiguous",
                    f"reaction group {group_key!r} has {len(claimed)} {slot} "
                    "endpoints; refusing to guess",
                    group_key=group_key,
                    reason="ambiguous_endpoint",
                    details={"endpoint": slot, "candidate_ids": claimed},
                )
            )
            slots.append(None)
        else:
            slots.append(claimed[0])
    return (slots[0], slots[1])


def _resolve_transition_state(
    members: tuple[StructureRecord, ...],
    by_id: dict[str, StructureRecord],
    group_key: str,
    forward_id: str | None,
    reverse_id: str | None,
    diagnostics: list[Diagnostic],
) -> str | None:
    """Resolve the transition-state id, recording failures.

    The transition state is the non-endpoint member that parents both
    endpoints.  Zero candidates is missing; several is ambiguous (never
    lowest-energy-wins).  Without two resolved endpoints no candidate
    can be identified, which is itself reported as missing.

    Parameters
    ----------
    members : tuple[StructureRecord, ...]
        Group members in sorted id order.
    by_id : dict[str, StructureRecord]
        Structure index by entity id.
    group_key : str
        Reaction pairing key (diagnostic context only).
    forward_id : str or None
        Resolved forward endpoint id, if any.
    reverse_id : str or None
        Resolved reverse endpoint id, if any.
    diagnostics : list[Diagnostic]
        Mutable per-group diagnostic sink.

    Returns
    -------
    str or None
        The transition-state subject id, or ``None`` when missing or
        ambiguous.
    """
    if forward_id is None or reverse_id is None:
        diagnostics.append(
            _diagnostic(
                "analysis_ts_missing",
                f"reaction group {group_key!r} has no identifiable transition "
                "state without two resolved endpoints",
                group_key=group_key,
                reason="endpoints_unresolved",
            )
        )
        return None
    parents = set(by_id[forward_id].parent_ids) & set(by_id[reverse_id].parent_ids)
    candidates = sorted(
        record.id
        for record in members
        if record.role not in _ENDPOINT_ROLES and record.id in parents
    )
    if not candidates:
        diagnostics.append(
            _diagnostic(
                "analysis_ts_missing",
                f"reaction group {group_key!r} has no structure parenting both endpoints",
                group_key=group_key,
                subject_structure_id=None,
                reason="no_common_parent",
                details={"forward_endpoint_id": forward_id, "reverse_endpoint_id": reverse_id},
            )
        )
        return None
    if len(candidates) > 1:
        diagnostics.append(
            _diagnostic(
                "analysis_group_ambiguous",
                f"reaction group {group_key!r} has {len(candidates)} "
                "transition-state candidates; refusing to guess",
                group_key=group_key,
                reason="ambiguous_ts",
                details={"candidate_ids": candidates},
            )
        )
        return None
    return candidates[0]


def build_reaction_groups(
    structures: StructureSet, results: ResultSet
) -> tuple[tuple[ReactionGroup, ...], tuple[Diagnostic, ...]]:
    """Group structures into reaction triples and attach results.

    Parameters
    ----------
    structures : StructureSet
        Subject structures carrying explicit ``group_key`` values,
        endpoint roles, and parent links.
    results : ResultSet
        Source results attached by ``subject_structure_id``.

    Returns
    -------
    tuple
        ``(groups, diagnostics)`` where ``groups`` sorts by
        ``group_key`` and ``diagnostics`` holds the grouping-level
        records (ungrouped structures, unmatched subjects), also in
        deterministic order.  Per-group failures live on the group
        itself; failed groups are never filled from another group.

    Raises
    ------
    AnalysisError
        With code ``analysis_invalid_definition`` when the inputs are
        not a :class:`StructureSet` / :class:`ResultSet`.
    """
    if not isinstance(structures, StructureSet):
        raise AnalysisError(
            "analysis_invalid_definition",
            "grouping requires a StructureSet",
            details={"reason": "invalid_structures"},
        )
    if not isinstance(results, ResultSet):
        raise AnalysisError(
            "analysis_invalid_definition",
            "grouping requires a ResultSet",
            details={"reason": "invalid_results"},
        )
    by_id = {record.id: record for record in structures}
    group_keys = sorted({record.group_key for record in structures if record.group_key is not None})
    ungrouped = sorted(record.id for record in structures if record.group_key is None)
    global_diagnostics: list[Diagnostic] = []
    if ungrouped:
        global_diagnostics.append(
            _diagnostic(
                "analysis_group_ambiguous",
                f"{len(ungrouped)} structure(s) carry no group key and belong to no reaction group",
                severity=DiagnosticSeverity.WARNING,
                reason="missing_group_key",
                details={"structure_ids": ungrouped},
            )
        )
    unmatched: dict[str | None, int] = {}
    for result in results:
        subject = result.subject_structure_id
        if subject not in by_id:
            unmatched[subject] = unmatched.get(subject, 0) + 1
    for subject in sorted(unmatched, key=lambda item: item or ""):
        if subject is None:
            global_diagnostics.append(
                _diagnostic(
                    "analysis_subject_mismatch",
                    "a result carries no subject structure id and attaches to no reaction group",
                    reason="missing_subject",
                    details={"count": unmatched[subject]},
                )
            )
        else:
            global_diagnostics.append(
                _diagnostic(
                    "analysis_subject_mismatch",
                    f"result subject {subject!r} matches no known structure",
                    subject_structure_id=subject,
                    reason="unknown_subject",
                    details={"count": unmatched[subject]},
                )
            )
    groups: list[ReactionGroup] = []
    for group_key in group_keys:
        members = tuple(
            sorted(
                (record for record in structures if record.group_key == group_key),
                key=lambda record: record.id,
            )
        )
        diagnostics: list[Diagnostic] = []
        forward_id, reverse_id = _resolve_endpoints(members, group_key, diagnostics)
        ts_id = _resolve_transition_state(
            members, by_id, group_key, forward_id, reverse_id, diagnostics
        )
        triple = {subject for subject in (ts_id, forward_id, reverse_id) if subject is not None}
        if ts_id is not None and forward_id is not None and reverse_id is not None:
            leftovers = sorted(
                record.id
                for record in members
                if record.id not in triple and record.role not in _ENDPOINT_ROLES
            )
            if leftovers:
                diagnostics.append(
                    _diagnostic(
                        "analysis_group_ambiguous",
                        f"reaction group {group_key!r} ignores "
                        f"{len(leftovers)} non-triple member(s)",
                        severity=DiagnosticSeverity.WARNING,
                        group_key=group_key,
                        reason="unassigned_members",
                        details={"structure_ids": leftovers},
                    )
                )
        sources = sorted(
            {result.value_digest for result in results if result.subject_structure_id in triple}
        )
        groups.append(
            ReactionGroup(
                group_key=group_key,
                ts_structure_id=ts_id,
                forward_endpoint_id=forward_id,
                reverse_endpoint_id=reverse_id,
                source_result_ids=tuple(sources),
                assignment="unassigned",
                diagnostics=tuple(diagnostics),
            )
        )
    return (tuple(groups), tuple(global_diagnostics))


def reaction_groups_digest(groups: Iterable[ReactionGroup]) -> str:
    """Return the deterministic digest of a group payload.

    Parameters
    ----------
    groups : iterable of ReactionGroup
        Groups in any order; the payload sorts by ``group_key`` so
        shuffled inputs yield the identical digest.

    Returns
    -------
    str
        ``sha256:<hex>`` digest over the canonical JSON encoding.
    """
    ordered = sorted(groups, key=lambda group: group.group_key)
    return typed_digest(REACTION_GROUPS_DIGEST_KIND, [group.to_dict() for group in ordered])
