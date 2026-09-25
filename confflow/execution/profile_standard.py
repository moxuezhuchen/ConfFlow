#!/usr/bin/env python3

"""V4 standard result profile.

The standard profile normalizes parser facts (:class:`NativeResult`) plus the
original work-item inputs into domain collections: one output structure, an
energy/frequency result set, and the executor-discovered artifacts.

Energy selection (ported from the legacy task runner)
-----------------------------------------------------
Gibbs free energy ``g`` is preferred; when only the electronic energy ``e``
and a Gibbs correction ``gc`` are present the chosen value is ``e + gc``;
otherwise the electronic energy stands alone.  A missing correction is
derived as ``gc = g - e`` when both are parsed.  Result kinds emitted:

- ``"energy"`` (Hartree): the chosen value, always when any energy parsed.
- ``"gibbs_energy"`` (Hartree): when ``g`` was parsed.
- ``"gibbs_correction"`` (Hartree): when ``gc`` was parsed or derived.
- ``"frequencies"`` (cm^-1): the parsed frequency list, when non-empty.
- ``"num_imaginary_frequencies"`` (dimensionless count): derived from the
  parsed list with the legacy 10 cm^-1 noise floor.
- ``"lowest_frequency"`` (cm^-1): the lowest kept mode, when one survives
  the noise floor.

Every result is bound to the input structure id and carries producer
provenance (program, native keyword as method, this profile contract as
adapter, step/work-item ids).

Profile/check contract
----------------------
The native ``terminated_normally`` fact is not part of the check-visible
:class:`ProfileOutput` structures/results, so the profile always appends a
``native_termination`` diagnostic (details ``{"terminated": bool}``) that the
``normal_termination`` check reads.  Parser diagnostics are forwarded
unchanged after it.

Deliberate parity break
-----------------------
The legacy single point inherited ``Imag``/``LowestFreq``/``TSBond`` values
from XYZ comment metadata when the parser produced none.  V4 has no
comment-metadata result transport, so frequency inheritance is dropped
entirely: V4 results come only from parsed output.  A single point with no
parsed frequencies therefore yields no frequency results (and the
``frequencies_required`` check fails closed on it).
"""

from __future__ import annotations

import math

from ..domain._immutable import FrozenDict
from ..domain.artifact import ArtifactSet
from ..domain.diagnostics import Diagnostic, DiagnosticSeverity
from ..domain.result import Provenance, ResultSet, ScientificResult
from ..domain.structure import StructureRecord, StructureSet
from ..domain.units import Unit
from .native import GeometryOutput, NativeResult, ResolvedCalculationInputs
from .profiles import (
    GeometrySemantics,
    ProfileContext,
    ProfileOutput,
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
    """Count imaginary modes and report the lowest kept frequency.

    Parameters
    ----------
    frequencies : tuple[float, ...]
        Parsed frequencies in cm^-1 (negative values are imaginary modes).
    noise_floor_cm : float
        Modes within this distance of zero are treated as numerical noise.

    Returns
    -------
    tuple[int, float | None]
        The imaginary-mode count and the lowest kept mode, or ``None`` when
        no mode survives the noise floor.
    """
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
    """Return ``(chosen, gibbs, correction)`` energies in Hartree.

    Gibbs free energy is preferred; otherwise the electronic energy plus the
    correction; otherwise the electronic energy alone.  A missing correction
    is derived as ``g - e`` when both are parsed.
    """
    raw = native_result.energies_hartree
    electronic = raw.get("electronic")
    gibbs = raw.get("gibbs")
    correction = raw.get("gibbs_correction")
    energy = float(electronic) if electronic is not None else None
    free = float(gibbs) if gibbs is not None else None
    corr = float(correction) if correction is not None else None
    if free is not None:
        if corr is None and energy is not None:
            corr = free - energy
        return free, free, corr
    if energy is not None and corr is not None:
        return energy + corr, None, corr
    return energy, None, corr


def _build_provenance(context: ProfileContext) -> Provenance:
    """Build producer provenance for every result of this profile."""
    native_map = context.inputs.native
    return Provenance(
        program=context.native_result.program.value,
        program_version=None,
        method=_text_or_none(native_map.get("keyword")),
        basis=_text_or_none(native_map.get("basis")),
        adapter=STANDARD_PROFILE_CONTRACT,
        step_id=context.step_id,
        work_item_id=context.work_item_id,
    )


def _output_structure(context: ProfileContext) -> tuple[StructureRecord, GeometrySemantics]:
    """Build the single output structure for *context*.

    A produced native geometry becomes a new ``PRODUCED`` record; a missing
    geometry always becomes a ``PASSTHROUGH`` record with geometry content
    identical to the input (the executor, not the profile, decides whether
    passthrough is acceptable from the declared checks).
    """
    native_result = context.native_result
    inputs: ResolvedCalculationInputs = context.inputs
    source = inputs.structure
    charge = inputs.charge if inputs.charge is not None else source.charge
    multiplicity = inputs.multiplicity if inputs.multiplicity is not None else source.multiplicity
    if native_result.geometry_output is GeometryOutput.PRODUCED and (
        native_result.final_geometry is not None
    ):
        geometry = native_result.final_geometry
        record = StructureRecord(
            id=produced_structure_id(context.logical_key, 0),
            atoms=tuple(geometry.atoms),
            coordinates=tuple(geometry.coordinates),
            charge=charge,
            multiplicity=multiplicity,
            parent_ids=(source.id,),
            lineage_root_id=source.lineage_root_id,
            source_step_id=context.step_id,
            source_work_item_id=context.work_item_id,
            role=None,
            ordinal=0,
            group_key=source.group_key,
            metadata=FrozenDict({}),
        )
        return record, GeometrySemantics.PRODUCED
    record = StructureRecord(
        id=passthrough_structure_id(context.logical_key),
        atoms=tuple(source.atoms),
        coordinates=tuple(source.coordinates),
        charge=charge,
        multiplicity=multiplicity,
        parent_ids=(source.id,),
        lineage_root_id=source.lineage_root_id,
        source_step_id=context.step_id,
        source_work_item_id=context.work_item_id,
        role=None,
        ordinal=0,
        group_key=source.group_key,
        metadata=FrozenDict({}),
    )
    return record, GeometrySemantics.PASSTHROUGH


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
        subject_id = context.inputs.structure.id

        chosen, gibbs, correction = _select_energies(native_result)
        results: list[ScientificResult] = []
        if chosen is not None:
            results.append(
                ScientificResult(
                    kind="energy",
                    value=chosen,
                    unit=Unit.HARTREE,
                    subject_structure_id=subject_id,
                    source_step_id=context.step_id,
                    source_work_item_id=context.work_item_id,
                    provenance=provenance,
                )
            )
        if gibbs is not None:
            results.append(
                ScientificResult(
                    kind="gibbs_energy",
                    value=gibbs,
                    unit=Unit.HARTREE,
                    subject_structure_id=subject_id,
                    source_step_id=context.step_id,
                    source_work_item_id=context.work_item_id,
                    provenance=provenance,
                )
            )
        if correction is not None:
            results.append(
                ScientificResult(
                    kind="gibbs_correction",
                    value=correction,
                    unit=Unit.HARTREE,
                    subject_structure_id=subject_id,
                    source_step_id=context.step_id,
                    source_work_item_id=context.work_item_id,
                    provenance=provenance,
                )
            )

        frequencies = tuple(float(value) for value in native_result.frequencies_cm)
        if frequencies:
            results.append(
                ScientificResult(
                    kind="frequencies",
                    value=list(frequencies),
                    unit=Unit.CM_INVERSE,
                    subject_structure_id=subject_id,
                    source_step_id=context.step_id,
                    source_work_item_id=context.work_item_id,
                    provenance=provenance,
                )
            )
            num_imaginary, lowest = count_imaginary_frequencies(frequencies)
            results.append(
                ScientificResult(
                    kind="num_imaginary_frequencies",
                    value=num_imaginary,
                    subject_structure_id=subject_id,
                    source_step_id=context.step_id,
                    source_work_item_id=context.work_item_id,
                    provenance=provenance,
                )
            )
            if lowest is not None:
                results.append(
                    ScientificResult(
                        kind="lowest_frequency",
                        value=lowest,
                        unit=Unit.CM_INVERSE,
                        subject_structure_id=subject_id,
                        source_step_id=context.step_id,
                        source_work_item_id=context.work_item_id,
                        provenance=provenance,
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
#: into the capability registry.
PROFILES: dict[str, StandardResultProfile] = {"standard": StandardResultProfile()}
