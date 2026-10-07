#!/usr/bin/env python3

"""V4 standard result profile.

Normalizes parser facts plus work-item inputs into one output structure,
energy/frequency results, and discovered artifacts. Frozen energy contract:
``energy`` is parsed electronic ``e`` only, never Gibbs or a sum (composite
Gibbs formation belongs to analysis, never this profile); ``gibbs_energy``
is ``g`` only; ``gibbs_correction`` is ``gc`` only, derived once as
``gc = g - e`` when ``e`` and ``g`` parse without explicit correction;
``frequencies`` when non-empty with 10 cm^-1 noise floor for
``num_imaginary_frequencies``/``lowest_frequency``. Every result binds the input structure id with producer provenance. ``terminated_normally`` is not
check-visible; the profile always appends ``native_termination``
(``{"terminated": bool}``) for ``normal_termination`` plus forwarded parser
diagnostics. No comment-metadata inheritance: results come only from parsed
output, so missing frequencies yield none (``frequencies_required`` fails closed).
"""

from __future__ import annotations

import math
from typing import Any

from ..domain._immutable import FrozenDict
from ..domain.artifact import ArtifactSet
from ..domain.diagnostics import Diagnostic, DiagnosticSeverity
from ..domain.result import Provenance, ResultSet, ScientificResult, make_result_id
from ..domain.structure import StructureRecord, StructureSet
from ..domain.units import Unit
from .native import GeometryOutput, NativeResult, ParsedGeometry, ResolvedCalculationInputs
from .profile_ensemble import EnsembleProfile
from .profiles import (
    GeometrySemantics,
    ProfileContext,
    ProfileOutput,
    ResultProfile,
    passthrough_structure_id,
    produced_structure_id,
)

__all__ = [
    "FREQUENCY_NOISE_FLOOR_CM",
    "NATIVE_TERMINATION_CODE",
    "PROFILES",
    "STANDARD_PROFILE_CONTRACT",
    "StandardResultProfile",
    "count_imaginary_frequencies",
]

#: Contract version folded into step digests; must match the registry spec.
STANDARD_PROFILE_CONTRACT = "confflow.contract.result_profile.standard.v1"

#: Diagnostic code carrying the native termination fact for the
#: ``normal_termination`` check.  The executor re-codes check failures into
#: workflow-level diagnostics; this code is the profile/check transport only.
NATIVE_TERMINATION_CODE = "native_termination"

#: Modes within this many cm^-1 of zero are numerical noise and are excluded
#: from the imaginary count and the lowest-frequency report (ported value
#: from the legacy frequency analysis).
FREQUENCY_NOISE_FLOOR_CM = 10.0


def count_imaginary_frequencies(
    frequencies: tuple[float, ...],
    *,
    noise_floor_cm: float = FREQUENCY_NOISE_FLOOR_CM,
) -> tuple[int, float | None]:
    """Count imaginary modes and report the lowest kept frequency."""
    modes: list[float] = []
    for value in frequencies:
        try:
            frequency = float(value)
        except (TypeError, ValueError):
            continue
        if not math.isfinite(frequency) or abs(frequency) <= noise_floor_cm:
            continue
        modes.append(frequency)
    num_imaginary = sum(1 for mode in modes if mode < 0.0)
    return num_imaginary, min(modes) if modes else None


def _text_or_none(value: object) -> str | None:
    """Return *value* as stripped text, or ``None`` when blank/missing."""
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _select_energies(
    native_result: NativeResult,
) -> tuple[float | None, float | None, float | None]:
    """Return ``(electronic, gibbs, correction)`` energies in Hartree."""
    raw = native_result.energies_hartree
    electronic = raw.get("electronic")
    gibbs = raw.get("gibbs")
    correction = raw.get("gibbs_correction")
    energy = float(electronic) if electronic is not None else None
    free = float(gibbs) if gibbs is not None else None
    corr = float(correction) if correction is not None else None
    if corr is None and energy is not None and free is not None:
        corr = free - energy
    return energy, free, corr


def _build_provenance(context: ProfileContext) -> Provenance:
    """Build producer provenance for every result of this profile."""
    native_map = context.inputs.native
    program_version = None
    environment = getattr(context, "inputs", None)
    # ProfileContext carries no environment; the executor stamps measured
    # program versions via adapter probe metadata when available.  Keep None
    # here rather than inventing a version.
    del environment
    return Provenance(
        program=context.native_result.program.value,
        program_version=program_version,
        method=_text_or_none(native_map.get("keyword")),
        basis=_text_or_none(native_map.get("basis")),
        adapter=STANDARD_PROFILE_CONTRACT,
        step_id=context.step_id,
        work_item_id=context.work_item_id,
    )


def _result_kwargs(
    *,
    kind: str,
    value: Any,
    unit: Any,
    subject_id: str,
    context: ProfileContext,
    provenance: Provenance,
) -> dict[str, Any]:
    """Return ScientificResult kwargs stamped with B's deterministic identity."""
    kwargs: dict[str, Any] = {
        "kind": kind,
        "value": value,
        "unit": unit,
        "subject_structure_id": subject_id,
        "source_step_id": context.step_id,
        "source_work_item_id": context.work_item_id,
        "provenance": provenance,
    }
    if context.producer_digest is not None:
        kwargs["result_id"] = make_result_id(
            step_id=context.step_id,
            work_item_id=context.work_item_id,
            kind=kind,
            subject_structure_id=subject_id,
            program=provenance.program,
            method=provenance.method,
            basis=provenance.basis,
            producer_digest=context.producer_digest,
        )
    return kwargs


def _named_parent_records(
    inputs: ResolvedCalculationInputs,
) -> tuple[StructureRecord, ...] | None:
    """Return named-slot parents in slot order, or ``None`` for standard items."""
    slots = inputs.extra_structures
    if not hasattr(slots, "get"):
        return None
    reactant = slots.get("reactant")
    product = slots.get("product")
    if reactant is None or product is None or not len(reactant) or not len(product):
        return None
    parents: list[StructureRecord] = [reactant[0], product[0]]
    guess = slots.get("guess")
    if guess is not None and len(guess):
        parents.append(guess[0])
    return tuple(parents)


def _output_structure(context: ProfileContext) -> tuple[StructureRecord, GeometrySemantics]:
    """Build the single output structure for *context*."""
    native_result = context.native_result
    inputs: ResolvedCalculationInputs = context.inputs
    source = inputs.structure
    charge = inputs.charge if inputs.charge is not None else source.charge
    multiplicity = inputs.multiplicity if inputs.multiplicity is not None else source.multiplicity
    # Intended-topology inheritance (resolved once on the pre-change source
    # geometry; explicit calculation charge/spin overrides above retained).
    from ..science.topology import resolve_and_persist_kwargs as _persist_kwargs

    topo_kwargs = _persist_kwargs(source, source.coordinates)
    # A newly captured intended graph (or a newly attached patch) is new
    # scientific state even when the coordinates did not move: retaining
    # the source entity would silently drop it downstream.
    topo_changed = False
    if topo_kwargs:
        kept_patch = topo_kwargs.get("topology_patch")
        if topo_kwargs.get("working_topology") is not None and (source.working_topology is None):
            topo_changed = True
        if (
            kept_patch is not None
            and not kept_patch.is_empty
            and (source.topology_patch is None or source.topology_patch.is_empty)
        ):
            topo_changed = True
    parent_ids: tuple[str, ...] = (source.id,)
    lineage_root_id = source.lineage_root_id
    group_key = source.group_key
    named_parents = _named_parent_records(inputs)
    if named_parents is not None:
        from .output_identity import multi_parent_lineage

        parent_ids, lineage_root_id, group_key = multi_parent_lineage(named_parents)
    if native_result.geometry_output is GeometryOutput.PRODUCED and (
        native_result.final_geometry is not None
    ):
        geometry = native_result.final_geometry
        if (
            tuple(geometry.atoms) == tuple(source.atoms)
            and _geometry_digest_of(geometry) == source.geometry_digest
            and named_parents is None
            and charge == source.charge
            and multiplicity == source.multiplicity
            and not topo_changed
        ):
            # Measurement of unchanged geometry and unchanged state:
            # retain input identity.
            return source, GeometrySemantics.PASSTHROUGH
        # Same geometry with new scientific state (explicit charge/spin or
        # newly captured topology) is a derived entity preserving the
        # transition — never the source with silently dropped state.
        record = StructureRecord(
            id=produced_structure_id(context.logical_key, 0),
            atoms=tuple(geometry.atoms),
            coordinates=tuple(geometry.coordinates),
            charge=charge,
            multiplicity=multiplicity,
            parent_ids=parent_ids,
            lineage_root_id=lineage_root_id,
            source_step_id=context.step_id,
            source_work_item_id=context.work_item_id,
            role=None,
            ordinal=0,
            group_key=group_key,
            metadata=FrozenDict({}),
            **topo_kwargs,
        )
        return record, GeometrySemantics.PRODUCED
    record = StructureRecord(
        id=passthrough_structure_id(context.logical_key),
        atoms=tuple(source.atoms),
        coordinates=tuple(source.coordinates),
        charge=charge,
        multiplicity=multiplicity,
        parent_ids=parent_ids,
        lineage_root_id=lineage_root_id,
        source_step_id=context.step_id,
        source_work_item_id=context.work_item_id,
        role=None,
        ordinal=0,
        group_key=group_key,
        metadata=FrozenDict({}),
        **topo_kwargs,
    )
    return record, GeometrySemantics.PASSTHROUGH


def _geometry_digest_of(geometry: ParsedGeometry) -> str | None:
    """Return the content digest of parsed geometry, or ``None``."""
    try:
        transient = StructureRecord(
            id="transient:digest-probe",
            atoms=tuple(geometry.atoms),
            coordinates=tuple(geometry.coordinates),
        )
    except Exception:
        return None
    return transient.geometry_digest


class StandardResultProfile:
    """The standard V4 result profile (geometries, results, artifacts)."""

    @property
    def name(self) -> str:
        """Return the profile name."""
        return "standard"

    @property
    def contract_version(self) -> str:
        """Return the profile contract version (folded into digests)."""
        return STANDARD_PROFILE_CONTRACT

    def apply(self, context: ProfileContext) -> ProfileOutput:
        """Normalize parser facts into domain collections."""
        native_result = context.native_result
        structure, semantics = _output_structure(context)
        provenance = _build_provenance(context)
        subject_id = structure.id

        chosen, gibbs, correction = _select_energies(native_result)
        results: list[ScientificResult] = []
        if chosen is not None:
            # Canonical contract: kind "energy" is electronic only; the
            # full Gibbs energy travels only as "gibbs_energy" and the
            # thermal part only as "gibbs_correction".
            results.append(
                ScientificResult(
                    **_result_kwargs(
                        kind="energy",
                        value=chosen,
                        unit=Unit.HARTREE,
                        subject_id=subject_id,
                        context=context,
                        provenance=provenance,
                    )
                )
            )
        if gibbs is not None:
            results.append(
                ScientificResult(
                    **_result_kwargs(
                        kind="gibbs_energy",
                        value=gibbs,
                        unit=Unit.HARTREE,
                        subject_id=subject_id,
                        context=context,
                        provenance=provenance,
                    )
                )
            )
        if correction is not None:
            results.append(
                ScientificResult(
                    **_result_kwargs(
                        kind="gibbs_correction",
                        value=correction,
                        unit=Unit.HARTREE,
                        subject_id=subject_id,
                        context=context,
                        provenance=provenance,
                    )
                )
            )

        frequencies = tuple(float(value) for value in native_result.frequencies_cm)
        if frequencies:
            results.append(
                ScientificResult(
                    **_result_kwargs(
                        kind="frequencies",
                        value=list(frequencies),
                        unit=Unit.CM_INVERSE,
                        subject_id=subject_id,
                        context=context,
                        provenance=provenance,
                    )
                )
            )
            num_imaginary, lowest = count_imaginary_frequencies(frequencies)
            results.append(
                ScientificResult(
                    **_result_kwargs(
                        kind="num_imaginary_frequencies",
                        value=num_imaginary,
                        unit=None,
                        subject_id=subject_id,
                        context=context,
                        provenance=provenance,
                    )
                )
            )
            if lowest is not None:
                results.append(
                    ScientificResult(
                        **_result_kwargs(
                            kind="lowest_frequency",
                            value=lowest,
                            unit=Unit.CM_INVERSE,
                            subject_id=subject_id,
                            context=context,
                            provenance=provenance,
                        )
                    )
                )

        terminated = bool(native_result.terminated_normally)
        termination = Diagnostic(
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
        diagnostics = (termination, *tuple(native_result.parser_diagnostics))

        artifacts = (
            context.discovered_artifacts
            if isinstance(context.discovered_artifacts, ArtifactSet)
            else ArtifactSet(tuple(context.discovered_artifacts))
        )
        return ProfileOutput(
            structures=StructureSet((structure,)),
            results=ResultSet(tuple(results)),
            artifacts=artifacts,
            geometry_semantics=semantics,
            diagnostics=diagnostics,
        )


#: Profile instances keyed by name, for the executor to consume and wire
#: into the capability registry.  ``ensemble`` normalizes conformer-ensemble
#: output (retained: NEB images plus the ConfGen result-profile binding)
#: into member structures.  It is a full runtime implementation, not a
#: registry name.
PROFILES: dict[str, ResultProfile] = {
    "standard": StandardResultProfile(),
    "ensemble": EnsembleProfile(),
}
