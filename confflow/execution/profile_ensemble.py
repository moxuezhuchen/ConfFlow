#!/usr/bin/env python3

"""V4 ensemble result profile (V4-5).

The ensemble profile normalizes parsed conformer members
(:class:`NativeEnsembleMember`) plus the seed work-item input into domain
collections: one :class:`StructureRecord` per native member, one ``"energy"``
result per member that parsed an energy, and the executor-discovered
artifacts passed through untouched.

Identity is delegated to the frozen
:mod:`confflow.execution.output_identity` authority: member ids derive from
the work-item logical key plus the frozen conformer role plus the native
member index — never the parser encounter order — so member ordering is
deterministic by ``member_index``.  Lineage follows the single-parent rule
shared with path endpoints.  Identical geometries under different member
indexes are never deduped: a :class:`StructureSet` allows shared content
under distinct ids, and each member is a distinct scientific entity.

A duplicated ``member_index`` fails closed with no structures and a
``duplicate_ensemble_member`` error diagnostic (never dedupe, never
renumber); an empty member list yields an ``empty_ensemble`` error
diagnostic.
"""

from __future__ import annotations

from ..domain._immutable import FrozenDict
from ..domain.artifact import ArtifactSet
from ..domain.diagnostics import Diagnostic, DiagnosticSeverity
from ..domain.result import Provenance, ResultSet, ScientificResult
from ..domain.structure import StructureRecord, StructureSet
from ..domain.units import Unit
from .native import NativeEnsembleMember
from .output_identity import (
    NEB_IMAGE_ROLE,
    NEB_TS_CANDIDATE_ROLE,
    conformer_output_id,
    endpoint_lineage,
    neb_image_output_id,
    neb_ts_candidate_output_id,
    output_ordering_key,
)
from .profiles import GeometrySemantics, ProfileContext, ProfileOutput

__all__ = [
    "DUPLICATE_ENSEMBLE_MEMBER_CODE",
    "EMPTY_ENSEMBLE_CODE",
    "ENSEMBLE_PROFILE_CONTRACT",
    "NATIVE_TERMINATION_CODE",
    "PROFILES",
    "EnsembleProfile",
]

#: Contract version folded into step digests; must match the registry spec.
ENSEMBLE_PROFILE_CONTRACT = "confflow.contract.result_profile.ensemble.v1"

#: Diagnostic code carrying the native termination fact for the
#: ``normal_termination`` check (same transport as the standard profile).
NATIVE_TERMINATION_CODE = "native_termination"

#: Diagnostic code emitted when two members share one native member index.
DUPLICATE_ENSEMBLE_MEMBER_CODE = "duplicate_ensemble_member"

#: Diagnostic code emitted when the parser reported no ensemble members.
EMPTY_ENSEMBLE_CODE = "empty_ensemble"


def _text_or_none(value: object) -> str | None:
    """Return *value* as stripped text, or ``None`` when blank/missing."""
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _build_provenance(context: ProfileContext) -> Provenance:
    """Build producer provenance for every result of this profile.

    Parameters
    ----------
    context : ProfileContext
        Profile inputs carrying the native program and declared keywords.

    Returns
    -------
    Provenance
        Producer provenance naming this profile contract as the adapter.
    """
    native_map = context.inputs.native
    return Provenance(
        program=context.native_result.program.value,
        program_version=None,
        method=_text_or_none(native_map.get("keyword")),
        basis=_text_or_none(native_map.get("basis")),
        adapter=ENSEMBLE_PROFILE_CONTRACT,
        step_id=context.step_id,
        work_item_id=context.work_item_id,
    )


def _termination_diagnostic(context: ProfileContext) -> Diagnostic:
    """Build the native-termination transport diagnostic.

    Parameters
    ----------
    context : ProfileContext
        Profile inputs carrying the native termination fact.

    Returns
    -------
    Diagnostic
        ``INFO`` when the native program terminated normally, else ``ERROR``.
    """
    terminated = bool(context.native_result.terminated_normally)
    return Diagnostic(
        code=NATIVE_TERMINATION_CODE,
        message=(
            "Native program reported normal termination."
            if terminated
            else "Native program did not report normal termination."
        ),
        severity=DiagnosticSeverity.INFO if terminated else DiagnosticSeverity.ERROR,
        step_id=context.step_id,
        work_item_id=context.work_item_id,
        logical_key=context.logical_key,
        details=FrozenDict({"terminated": terminated}),
    )


def _passthrough_artifacts(context: ProfileContext) -> ArtifactSet:
    """Return the discovered artifacts unchanged.

    Parameters
    ----------
    context : ProfileContext
        Profile inputs carrying the executor-discovered artifacts.

    Returns
    -------
    ArtifactSet
        The discovered set, passed through untouched.
    """
    if isinstance(context.discovered_artifacts, ArtifactSet):
        return context.discovered_artifacts
    return ArtifactSet(tuple(context.discovered_artifacts))


def _member_output_id(logical_key: str, member: NativeEnsembleMember) -> str:
    """Return the deterministic entity id for one ensemble/path member."""
    if member.role == NEB_IMAGE_ROLE:
        return neb_image_output_id(logical_key, member.member_index)
    if member.role == NEB_TS_CANDIDATE_ROLE:
        return neb_ts_candidate_output_id(logical_key)
    return conformer_output_id(logical_key, member.member_index)


class EnsembleProfile:
    """The V4 ensemble result profile (conformer members)."""

    @property
    def name(self) -> str:
        """Return the profile name."""
        return "ensemble"

    @property
    def contract_version(self) -> str:
        """Return the profile contract version (folded into digests)."""
        return ENSEMBLE_PROFILE_CONTRACT

    def apply(self, context: ProfileContext) -> ProfileOutput:
        """Normalize parsed ensemble members into domain collections.

        Parameters
        ----------
        context : ProfileContext
            Profile inputs: the seed structure plus parser facts.

        Returns
        -------
        ProfileOutput
            Member structures ordered by native member index, per-member
            energy results, untouched artifacts, ``PRODUCED`` semantics, and
            the termination plus ensemble-shape diagnostics.
        """
        seed = context.inputs.structure
        if context.inputs.charge is not None:
            charge = context.inputs.charge
        else:
            charge = seed.charge
        if context.inputs.multiplicity is not None:
            multiplicity = context.inputs.multiplicity
        else:
            multiplicity = seed.multiplicity
        provenance = _build_provenance(context)

        members = tuple(context.native_result.ensemble_members)
        diagnostics: list[Diagnostic] = [
            _termination_diagnostic(context),
            *context.native_result.parser_diagnostics,
        ]
        if not members:
            diagnostics.append(
                Diagnostic(
                    code=EMPTY_ENSEMBLE_CODE,
                    message="Ensemble run reported no conformer members.",
                    severity=DiagnosticSeverity.ERROR,
                    step_id=context.step_id,
                    work_item_id=context.work_item_id,
                    logical_key=context.logical_key,
                    details=FrozenDict({}),
                )
            )
            return ProfileOutput(
                structures=StructureSet(),
                results=ResultSet(),
                artifacts=_passthrough_artifacts(context),
                geometry_semantics=GeometrySemantics.PRODUCED,
                diagnostics=tuple(diagnostics),
            )
        indexes = tuple(member.member_index for member in members)
        if len(set(indexes)) != len(indexes):
            # Colliding native member indexes would share one deterministic
            # id, so fail closed with no structures rather than deduping or
            # renumbering silently.
            diagnostics.append(
                Diagnostic(
                    code=DUPLICATE_ENSEMBLE_MEMBER_CODE,
                    message=(
                        "Ensemble members carry duplicated member indexes; "
                        f"observed {sorted(indexes)}."
                    ),
                    severity=DiagnosticSeverity.ERROR,
                    step_id=context.step_id,
                    work_item_id=context.work_item_id,
                    logical_key=context.logical_key,
                    details=FrozenDict({"observed": tuple(sorted(indexes))}),
                )
            )
            return ProfileOutput(
                structures=StructureSet(),
                results=ResultSet(),
                artifacts=_passthrough_artifacts(context),
                geometry_semantics=GeometrySemantics.PRODUCED,
                diagnostics=tuple(diagnostics),
            )

        # Single-parent lineage: the seed is the parent, its root propagates
        # (or its own id), and the group key falls back to the seed id — the
        # same rule the path-endpoints profile applies to its driving input.
        parent_ids, lineage_root, group_key = endpoint_lineage(seed)
        ordered = tuple(sorted(members, key=lambda member: member.member_index))
        ranked: list[tuple[tuple[int, int], StructureRecord, NativeEnsembleMember]] = []
        for member in ordered:
            record = StructureRecord(
                id=_member_output_id(context.logical_key, member),
                atoms=tuple(member.geometry.atoms),
                coordinates=tuple(member.geometry.coordinates),
                charge=charge,
                multiplicity=multiplicity,
                parent_ids=parent_ids,
                lineage_root_id=lineage_root,
                source_step_id=context.step_id,
                source_work_item_id=context.work_item_id,
                role=member.role,
                ordinal=member.member_index,
                group_key=group_key,
                metadata=FrozenDict({"member_index": member.member_index}),
            )
            ranked.append((output_ordering_key(member.role, member.member_index), record, member))
        ranked.sort(key=lambda item: item[0])

        results: list[ScientificResult] = []
        for _, record, member in ranked:
            if member.energy_hartree is None:
                continue
            results.append(
                ScientificResult(
                    kind="energy",
                    value=float(member.energy_hartree),
                    unit=Unit.HARTREE,
                    subject_structure_id=record.id,
                    source_step_id=context.step_id,
                    source_work_item_id=context.work_item_id,
                    provenance=provenance,
                )
            )
        return ProfileOutput(
            structures=StructureSet(tuple(record for _, record, _ in ranked)),
            results=ResultSet(tuple(results)),
            artifacts=_passthrough_artifacts(context),
            geometry_semantics=GeometrySemantics.PRODUCED,
            diagnostics=tuple(diagnostics),
        )


#: Profile instances keyed by name, for the executor to consume and wire
#: into the capability registry.
PROFILES: dict[str, EnsembleProfile] = {"ensemble": EnsembleProfile()}
