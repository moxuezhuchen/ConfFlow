#!/usr/bin/env python3

"""V4-6 analysis grouping matrix (Agent A runtime).

Grouping pairs transition-state structures with native forward/reverse
path endpoints by ``(group_key)`` plus subject ids plus endpoint roles
only: complete/partial/duplicate/missing inputs, ungrouped structures,
unmatched subjects, frozen-role handling, and shuffled-order
determinism via digest comparison.
"""

from __future__ import annotations

from confflow.analysis.grouping import build_reaction_groups, reaction_groups_digest
from confflow.analysis.models import AnalysisError
from confflow.domain.diagnostics import Diagnostic
from confflow.domain.result import ResultSet, ScientificResult
from confflow.domain.structure import StructureRecord, StructureSet
from confflow.domain.units import Unit
from confflow.execution.output_identity import (
    PATH_ENDPOINT_FORWARD_ROLE,
    PATH_ENDPOINT_REVERSE_ROLE,
)

_ATOMS = ("O", "H", "H")
_BASE_COORDS = ((0.0, 0.0, 0.0), (0.76, 0.59, 0.0), (0.76, -0.59, 0.0))


def _coords(offset: float = 0.0) -> tuple[tuple[float, float, float], ...]:
    """Return deterministic test coordinates shifted by *offset*."""
    return tuple((x + offset, y + offset, z + offset) for x, y, z in _BASE_COORDS)


def _structure(
    record_id: str,
    *,
    group_key: str | None = None,
    role: str | None = None,
    parent_ids: tuple[str, ...] = (),
    offset: float = 0.0,
) -> StructureRecord:
    """Build one deterministic test structure record."""
    return StructureRecord(
        id=record_id,
        atoms=_ATOMS,
        coordinates=_coords(offset),
        group_key=group_key,
        role=role,
        parent_ids=parent_ids,
    )


def _triple(
    prefix: str, group_key: str, *, base_offset: float = 0.0
) -> tuple[StructureRecord, StructureRecord, StructureRecord]:
    """Build one transition-state plus endpoint triple."""
    transition = _structure(f"{prefix}-ts", group_key=group_key, role=None, offset=base_offset)
    forward = _structure(
        f"{prefix}-fwd",
        group_key=group_key,
        role=PATH_ENDPOINT_FORWARD_ROLE,
        parent_ids=(transition.id,),
        offset=base_offset + 1.0,
    )
    reverse = _structure(
        f"{prefix}-rev",
        group_key=group_key,
        role=PATH_ENDPOINT_REVERSE_ROLE,
        parent_ids=(transition.id,),
        offset=base_offset + 2.0,
    )
    return (transition, forward, reverse)


def _energy(value: float, subject: str | None) -> ScientificResult:
    """Build one Hartree energy result bound to *subject*."""
    return ScientificResult(
        kind="energy",
        value=value,
        unit=Unit.HARTREE,
        subject_structure_id=subject,
    )


def _codes(diagnostics: tuple[Diagnostic, ...]) -> list[str]:
    """Return diagnostic codes in order."""
    return [item.code for item in diagnostics]


def _reasons(diagnostics: tuple[Diagnostic, ...]) -> list[str]:
    """Return machine-readable diagnostic reasons in order."""
    return [str(item.details.get("reason")) for item in diagnostics]


class TestFrozenRoles:
    """Endpoint slots come from the frozen role authority, never order."""

    def test_role_constants(self) -> None:
        assert PATH_ENDPOINT_FORWARD_ROLE == "path_endpoint_forward"
        assert PATH_ENDPOINT_REVERSE_ROLE == "path_endpoint_reverse"

    def test_input_order_never_swaps_endpoints(self) -> None:
        transition, forward, reverse = _triple("t", "rxn-0")
        groups, _ = build_reaction_groups(StructureSet((reverse, transition, forward)), ResultSet())
        assert len(groups) == 1
        assert groups[0].forward_endpoint_id == forward.id
        assert groups[0].reverse_endpoint_id == reverse.id
        assert groups[0].ts_structure_id == transition.id


class TestCompleteGroup:
    """A transition state plus both endpoints resolves cleanly."""

    def test_triple_resolves(self) -> None:
        transition, forward, reverse = _triple("t", "rxn-0")
        results = ResultSet(
            (
                _energy(-100.0, transition.id),
                _energy(-100.5, forward.id),
                _energy(-100.2, reverse.id),
            )
        )
        groups, global_diagnostics = build_reaction_groups(
            StructureSet((transition, forward, reverse)), results
        )
        assert global_diagnostics == ()
        assert len(groups) == 1
        group = groups[0]
        assert group.group_key == "rxn-0"
        assert group.ts_structure_id == transition.id
        assert group.forward_endpoint_id == forward.id
        assert group.reverse_endpoint_id == reverse.id
        assert group.is_complete
        assert group.ok
        assert group.assignment == "unassigned"
        assert group.subject_ids() == tuple(sorted((transition.id, forward.id, reverse.id)))
        assert group.source_result_ids == tuple(sorted(result.value_digest for result in results))

    def test_groups_sort_by_group_key(self) -> None:
        first = _triple("b", "rxn-b", base_offset=10.0)
        second = _triple("a", "rxn-a", base_offset=20.0)
        groups, _ = build_reaction_groups(StructureSet((*first, *second)), ResultSet())
        assert [group.group_key for group in groups] == ["rxn-a", "rxn-b"]

    def test_invalid_input_types_raise(self) -> None:
        try:
            build_reaction_groups("nope", ResultSet())  # type: ignore[arg-type]
        except AnalysisError as exc:
            assert exc.code == "analysis_invalid_definition"
        else:  # pragma: no cover - must raise
            raise AssertionError("expected AnalysisError")
        try:
            build_reaction_groups(StructureSet(), "nope")  # type: ignore[arg-type]
        except AnalysisError as exc:
            assert exc.code == "analysis_invalid_definition"
        else:  # pragma: no cover - must raise
            raise AssertionError("expected AnalysisError")


class TestMissingEndpoints:
    """A partial group fails closed and is never filled from elsewhere."""

    def test_only_transition_state(self) -> None:
        transition = _structure("ts-0", group_key="rxn-0")
        groups, _ = build_reaction_groups(StructureSet((transition,)), ResultSet())
        assert len(groups) == 1
        group = groups[0]
        assert not group.is_complete
        assert not group.ok
        assert group.forward_endpoint_id is None
        assert group.reverse_endpoint_id is None
        assert group.ts_structure_id is None
        assert _codes(group.diagnostics) == [
            "analysis_endpoint_missing",
            "analysis_endpoint_missing",
            "analysis_ts_missing",
        ]
        assert _reasons(group.diagnostics) == [
            "missing_endpoint",
            "missing_endpoint",
            "endpoints_unresolved",
        ]

    def test_one_endpoint_missing(self) -> None:
        transition, forward, _ = _triple("t", "rxn-0")
        groups, _ = build_reaction_groups(StructureSet((transition, forward)), ResultSet())
        group = groups[0]
        assert group.forward_endpoint_id == forward.id
        assert group.reverse_endpoint_id is None
        assert group.ts_structure_id is None
        assert "analysis_endpoint_missing" in _codes(group.diagnostics)
        assert "analysis_ts_missing" in _codes(group.diagnostics)

    def test_partial_group_never_borrows_from_another_group(self) -> None:
        partial = _triple("p", "rxn-partial")[:2]
        complete = _triple("c", "rxn-complete", base_offset=5.0)
        groups, _ = build_reaction_groups(StructureSet((*partial, *complete)), ResultSet())
        by_key = {group.group_key: group for group in groups}
        assert not by_key["rxn-partial"].ok
        assert by_key["rxn-complete"].ok
        assert by_key["rxn-partial"].reverse_endpoint_id is None
        assert by_key["rxn-partial"].ts_structure_id is None


class TestMissingTransitionState:
    """Endpoints without a common parent leave the slot empty."""

    def test_no_common_parent(self) -> None:
        forward = _structure(
            "fwd-0", group_key="rxn-0", role=PATH_ENDPOINT_FORWARD_ROLE, parent_ids=("a",)
        )
        reverse = _structure(
            "rev-0", group_key="rxn-0", role=PATH_ENDPOINT_REVERSE_ROLE, parent_ids=("b",)
        )
        bystander = _structure("other-0", group_key="rxn-0", role=None)
        groups, _ = build_reaction_groups(StructureSet((forward, reverse, bystander)), ResultSet())
        group = groups[0]
        assert group.forward_endpoint_id == forward.id
        assert group.reverse_endpoint_id == reverse.id
        assert group.ts_structure_id is None
        assert not group.ok
        assert _codes(group.diagnostics) == ["analysis_ts_missing"]
        assert _reasons(group.diagnostics) == ["no_common_parent"]

    def test_single_parent_is_not_a_transition_state(self) -> None:
        transition, forward, _ = _triple("t", "rxn-0")
        lone_reverse = _structure(
            "lone-rev",
            group_key="rxn-0",
            role=PATH_ENDPOINT_REVERSE_ROLE,
            parent_ids=("someone-else",),
            offset=9.0,
        )
        groups, _ = build_reaction_groups(
            StructureSet((transition, forward, lone_reverse)), ResultSet()
        )
        group = groups[0]
        assert group.ts_structure_id is None
        assert _reasons(group.diagnostics) == ["no_common_parent"]


class TestDuplicateEndpoints:
    """Two structures claiming one slot fail the slot, never guess."""

    def test_duplicate_forward(self) -> None:
        transition = _structure("ts-0", group_key="rxn-0")
        first = _structure(
            "fwd-a",
            group_key="rxn-0",
            role=PATH_ENDPOINT_FORWARD_ROLE,
            parent_ids=(transition.id,),
            offset=1.0,
        )
        second = _structure(
            "fwd-b",
            group_key="rxn-0",
            role=PATH_ENDPOINT_FORWARD_ROLE,
            parent_ids=(transition.id,),
            offset=2.0,
        )
        reverse = _structure(
            "rev-0",
            group_key="rxn-0",
            role=PATH_ENDPOINT_REVERSE_ROLE,
            parent_ids=(transition.id,),
            offset=3.0,
        )
        groups, _ = build_reaction_groups(
            StructureSet((transition, first, second, reverse)), ResultSet()
        )
        group = groups[0]
        assert group.forward_endpoint_id is None
        assert not group.ok
        ambiguous = [
            item for item in group.diagnostics if item.code == "analysis_endpoint_ambiguous"
        ]
        assert len(ambiguous) == 1
        assert ambiguous[0].details.get("endpoint") == "forward"
        assert list(ambiguous[0].details.get("candidate_ids")) == ["fwd-a", "fwd-b"]

    def test_duplicate_reverse(self) -> None:
        transition, forward, reverse = _triple("t", "rxn-0")
        extra = _structure(
            "rev-extra",
            group_key="rxn-0",
            role=PATH_ENDPOINT_REVERSE_ROLE,
            parent_ids=(transition.id,),
            offset=7.0,
        )
        groups, _ = build_reaction_groups(
            StructureSet((transition, forward, reverse, extra)), ResultSet()
        )
        group = groups[0]
        assert group.reverse_endpoint_id is None
        assert "analysis_endpoint_ambiguous" in _codes(group.diagnostics)


class TestDuplicateTransitionState:
    """Two common parents fail the group, never lowest-energy-wins."""

    def test_ambiguous_transition_state(self) -> None:
        first = _structure("ts-a", group_key="rxn-0", offset=0.0)
        second = _structure("ts-b", group_key="rxn-0", offset=0.5)
        forward = _structure(
            "fwd-0",
            group_key="rxn-0",
            role=PATH_ENDPOINT_FORWARD_ROLE,
            parent_ids=(first.id, second.id),
            offset=1.0,
        )
        reverse = _structure(
            "rev-0",
            group_key="rxn-0",
            role=PATH_ENDPOINT_REVERSE_ROLE,
            parent_ids=(first.id, second.id),
            offset=2.0,
        )
        groups, _ = build_reaction_groups(
            StructureSet((first, second, forward, reverse)), ResultSet()
        )
        group = groups[0]
        assert group.ts_structure_id is None
        assert group.forward_endpoint_id == forward.id
        assert group.reverse_endpoint_id == reverse.id
        assert not group.ok
        ambiguous = [item for item in group.diagnostics if item.code == "analysis_group_ambiguous"]
        assert len(ambiguous) == 1
        assert ambiguous[0].details.get("reason") == "ambiguous_ts"
        assert list(ambiguous[0].details.get("candidate_ids")) == ["ts-a", "ts-b"]


class TestUngroupedStructures:
    """Structures without a group key belong to no group, with warning."""

    def test_ungrouped_structure_warns(self) -> None:
        transition, forward, reverse = _triple("t", "rxn-0")
        stray = _structure("stray-0", group_key=None, offset=9.0)
        groups, global_diagnostics = build_reaction_groups(
            StructureSet((transition, forward, reverse, stray)), ResultSet()
        )
        assert [group.group_key for group in groups] == ["rxn-0"]
        assert groups[0].ok
        assert len(global_diagnostics) == 1
        diagnostic = global_diagnostics[0]
        assert diagnostic.code == "analysis_group_ambiguous"
        assert diagnostic.details.get("reason") == "missing_group_key"
        assert list(diagnostic.details.get("structure_ids")) == ["stray-0"]
        assert not diagnostic.is_error


class TestSubjectMismatch:
    """Results naming unknown subjects fail closed with typed diagnostics."""

    def test_unknown_subject(self) -> None:
        transition, forward, reverse = _triple("t", "rxn-0")
        results = ResultSet(
            (
                _energy(-100.0, transition.id),
                _energy(-100.5, forward.id),
                _energy(-100.2, reverse.id),
                _energy(-99.0, "ghost-structure"),
            )
        )
        groups, global_diagnostics = build_reaction_groups(
            StructureSet((transition, forward, reverse)), results
        )
        assert groups[0].ok
        assert len(global_diagnostics) == 1
        diagnostic = global_diagnostics[0]
        assert diagnostic.code == "analysis_subject_mismatch"
        assert diagnostic.details.get("reason") == "unknown_subject"
        assert diagnostic.details.get("subject_structure_id") == "ghost-structure"
        assert diagnostic.is_error

    def test_missing_subject(self) -> None:
        transition, forward, reverse = _triple("t", "rxn-0")
        results = ResultSet(
            (
                _energy(-100.0, transition.id),
                _energy(-100.5, forward.id),
                _energy(-100.2, reverse.id),
                _energy(-99.0, None),
            )
        )
        groups, global_diagnostics = build_reaction_groups(
            StructureSet((transition, forward, reverse)), results
        )
        assert groups[0].ok
        assert [item.details.get("reason") for item in global_diagnostics] == ["missing_subject"]
        assert _codes(global_diagnostics) == ["analysis_subject_mismatch"]

    def test_attached_results_cover_only_triple_subjects(self) -> None:
        transition, forward, reverse = _triple("t", "rxn-0")
        extra = _structure("extra-0", group_key="rxn-0", offset=8.0)
        results = ResultSet(
            (
                _energy(-100.0, transition.id),
                _energy(-100.5, forward.id),
                _energy(-100.2, reverse.id),
                _energy(-98.0, extra.id),
            )
        )
        groups, global_diagnostics = build_reaction_groups(
            StructureSet((transition, forward, reverse, extra)), results
        )
        assert global_diagnostics == ()
        assert groups[0].ok
        assert groups[0].source_result_ids == tuple(
            sorted(
                result.value_digest for result in results if result.subject_structure_id != extra.id
            )
        )


class TestExtraMembers:
    """Non-triple members of a complete group warn instead of failing."""

    def test_leftover_member_warns(self) -> None:
        transition, forward, reverse = _triple("t", "rxn-0")
        leftover = _structure("image-0", group_key="rxn-0", role=None, offset=8.0)
        groups, _ = build_reaction_groups(
            StructureSet((transition, forward, reverse, leftover)), ResultSet()
        )
        group = groups[0]
        assert group.is_complete
        assert group.ok
        warnings = [item for item in group.diagnostics if not item.is_error]
        assert len(warnings) == 1
        assert warnings[0].code == "analysis_group_ambiguous"
        assert warnings[0].details.get("reason") == "unassigned_members"
        assert list(warnings[0].details.get("structure_ids")) == ["image-0"]


class TestShuffledDeterminism:
    """Shuffled input order yields the identical semantic payload."""

    def _payload(self) -> tuple[StructureSet, ResultSet]:
        triples = [
            _triple("a", "rxn-a", base_offset=0.0),
            _triple("b", "rxn-b", base_offset=10.0),
            _triple("c", "rxn-c", base_offset=20.0),
        ]
        structures = [record for triple in triples for record in triple]
        results = [_energy(-100.0 - index, record.id) for index, record in enumerate(structures)]
        return (StructureSet(tuple(structures)), ResultSet(tuple(results)))

    def test_digest_stable_across_orders(self) -> None:
        structures, results = self._payload()
        orders = [
            tuple(structures),
            tuple(reversed(tuple(structures))),
            tuple(structures)[3:] + tuple(structures)[:3],
        ]
        result_orders = [
            tuple(results),
            tuple(reversed(tuple(results))),
            tuple(results)[4:] + tuple(results)[:4],
        ]
        digests = set()
        for structure_order, result_order in zip(orders, result_orders):
            groups, global_diagnostics = build_reaction_groups(
                StructureSet(structure_order), ResultSet(result_order)
            )
            assert global_diagnostics == ()
            assert [group.group_key for group in groups] == ["rxn-a", "rxn-b", "rxn-c"]
            digests.add(reaction_groups_digest(groups))
        assert len(digests) == 1

    def test_payload_semantically_identical(self) -> None:
        structures, results = self._payload()
        first, _ = build_reaction_groups(structures, results)
        second, _ = build_reaction_groups(
            StructureSet(tuple(reversed(tuple(structures)))),
            ResultSet(tuple(reversed(tuple(results)))),
        )
        assert [group.to_dict() for group in first] == [group.to_dict() for group in second]
