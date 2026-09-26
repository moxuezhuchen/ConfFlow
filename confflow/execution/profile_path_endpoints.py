#!/usr/bin/env python3

"""V4 path-endpoints result profile (V4-5).

The path-endpoints profile normalizes parsed reaction-path endpoints
(:class:`NativePathEndpoint`) plus the driving work-item input into domain
collections: one :class:`StructureRecord` per native direction (``forward`` /
``reverse``), one ``"energy"`` result per endpoint that parsed an energy, and
the executor-discovered artifacts passed through untouched.

Identity and lineage are delegated to the frozen
:mod:`confflow.execution.output_identity` authority: endpoint ids derive from
the work-item logical key plus the frozen direction role, so a shuffled parser
order can never swap the two endpoints.  ``forward`` / ``reverse`` is native
path direction only — this profile performs no reactant/product chemistry
assignment anywhere.

An endpoint set that is not exactly ``{forward, reverse}`` (missing endpoint,
empty set, or a duplicated direction) always yields an ``incomplete_path``
error diagnostic.  A missing endpoint still emits the endpoints that parsed;
a duplicated direction fails closed with no structures, because the two
endpoints would share one deterministic id and must never be deduped
silently.  The executor (not this profile) turns that diagnostic into the
work-item failure.
"""

from __future__ import annotations

from ..domain._immutable import FrozenDict
from ..domain.artifact import ArtifactSet
from ..domain.diagnostics import Diagnostic, DiagnosticSeverity
from ..domain.result import Provenance, ResultSet, ScientificResult
from ..domain.structure import StructureRecord, StructureSet
from ..domain.units import Unit
from .native import NativePathEndpoint
from .output_identity import (
    PATH_ENDPOINT_FORWARD_ROLE,
    PATH_ENDPOINT_REVERSE_ROLE,
    endpoint_lineage,
    endpoint_output_id,
    output_ordering_key,
)
from .profiles import GeometrySemantics, ProfileContext, ProfileOutput

__all__ = [
    "INCOMPLETE_PATH_CODE",
    "NATIVE_TERMINATION_CODE",
    "PATH_ENDPOINTS_PROFILE_CONTRACT",
    "PROFILES",
    "PathEndpointsProfile",
]

#: Contract version folded into step digests; must match the registry spec.
PATH_ENDPOINTS_PROFILE_CONTRACT = "confflow.contract.result_profile.path_endpoints.v1"

#: Diagnostic code carrying the native termination fact for the
#: ``normal_termination`` check (same transport as the standard profile).
NATIVE_TERMINATION_CODE = "native_termination"

#: Diagnostic code emitted when the parsed endpoint set is not exactly one
#: forward plus one reverse endpoint.
INCOMPLETE_PATH_CODE = "incomplete_path"

#: Native path directions normalized by this profile.  Direction only; never
#: a reactant/product claim.
_PATH_DIRECTIONS: tuple[str, str] = ("forward", "reverse")


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
        adapter=PATH_ENDPOINTS_PROFILE_CONTRACT,
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


def _endpoint_role(direction: str) -> str:
    """Return the frozen semantic role for one native path direction.

    Parameters
    ----------
    direction : str
        ``"forward"`` or ``"reverse"`` (native direction only).

    Returns
    -------
    str
        The frozen endpoint role constant for *direction*.
    """
    if direction == "forward":
        return PATH_ENDPOINT_FORWARD_ROLE
    return PATH_ENDPOINT_REVERSE_ROLE


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


class PathEndpointsProfile:
    """The V4 path-endpoints result profile (IRC/NEB directions)."""

    @property
    def name(self) -> str:
        """Return the profile name."""
        return "path_endpoints"

    @property
    def contract_version(self) -> str:
        """Return the profile contract version (folded into digests)."""
        return PATH_ENDPOINTS_PROFILE_CONTRACT

    def apply(self, context: ProfileContext) -> ProfileOutput:
        """Normalize parsed path endpoints into domain collections.

        Parameters
        ----------
        context : ProfileContext
            Profile inputs: the driving structure plus parser facts.

        Returns
        -------
        ProfileOutput
            Endpoint structures sorted forward-then-reverse, per-endpoint
            energy results, untouched artifacts, ``PRODUCED`` semantics, and
            the termination plus completeness diagnostics.
        """
        driving = context.inputs.structure
        if context.inputs.charge is not None:
            charge = context.inputs.charge
        else:
            charge = driving.charge
        if context.inputs.multiplicity is not None:
            multiplicity = context.inputs.multiplicity
        else:
            multiplicity = driving.multiplicity
        provenance = _build_provenance(context)

        endpoints = tuple(
            endpoint
            for endpoint in context.native_result.path_endpoints
            if endpoint.direction in _PATH_DIRECTIONS
        )
        directions = tuple(endpoint.direction for endpoint in endpoints)
        observed = set(directions)
        complete = len(endpoints) == 2 and observed == set(_PATH_DIRECTIONS)

        diagnostics: list[Diagnostic] = [
            _termination_diagnostic(context),
            *context.native_result.parser_diagnostics,
        ]
        if not complete:
            diagnostics.append(
                Diagnostic(
                    code=INCOMPLETE_PATH_CODE,
                    message=(
                        "Reaction path is incomplete: expected one forward and "
                        f"one reverse endpoint, observed {sorted(observed)}."
                    ),
                    severity=DiagnosticSeverity.ERROR,
                    step_id=context.step_id,
                    work_item_id=context.work_item_id,
                    logical_key=context.logical_key,
                    details=FrozenDict(
                        {
                            "expected": _PATH_DIRECTIONS,
                            "observed": tuple(sorted(observed)),
                        }
                    ),
                )
            )
        if len(endpoints) != len(observed):
            # Duplicated native direction: both endpoints map to one
            # deterministic id, so fail closed with no structures rather
            # than silently dropping one of them.
            return ProfileOutput(
                structures=StructureSet(),
                results=ResultSet(),
                artifacts=_passthrough_artifacts(context),
                geometry_semantics=GeometrySemantics.PRODUCED,
                diagnostics=tuple(diagnostics),
            )

        parent_ids, lineage_root, group_key = endpoint_lineage(driving)
        ranked: list[tuple[tuple[int, int], StructureRecord, NativePathEndpoint]] = []
        for endpoint in endpoints:
            role = _endpoint_role(endpoint.direction)
            record = StructureRecord(
                id=endpoint_output_id(context.logical_key, endpoint.direction),
                atoms=tuple(endpoint.geometry.atoms),
                coordinates=tuple(endpoint.geometry.coordinates),
                charge=charge,
                multiplicity=multiplicity,
                parent_ids=parent_ids,
                lineage_root_id=lineage_root,
                source_step_id=context.step_id,
                source_work_item_id=context.work_item_id,
                role=role,
                ordinal=0,
                group_key=group_key,
                metadata=FrozenDict(
                    {
                        "direction": endpoint.direction,
                        "point_ordinal": endpoint.point_ordinal,
                        "converged": endpoint.converged,
                    }
                ),
            )
            ranked.append((output_ordering_key(role, 0), record, endpoint))
        ranked.sort(key=lambda item: item[0])

        results: list[ScientificResult] = []
        for _, record, endpoint in ranked:
            if endpoint.energy_hartree is None:
                continue
            results.append(
                ScientificResult(
                    kind="energy",
                    value=float(endpoint.energy_hartree),
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
PROFILES: dict[str, PathEndpointsProfile] = {"path_endpoints": PathEndpointsProfile()}
