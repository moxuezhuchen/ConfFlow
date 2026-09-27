#!/usr/bin/env python3

"""V4 standard result profile.

The standard profile normalizes parser facts (:class:`NativeResult`) plus the
original work-item inputs into domain collections: one output structure, an
energy/frequency result set, and the executor-discovered artifacts.

Energy selection (canonical scientific-energy contract, frozen)
---------------------------------------------------------------
Result kinds name disjoint physical quantities; a kind never changes
meaning with context:

- ``"energy"`` (Hartree): the parsed electronic energy ``e`` only,
  always when parsed.  It is never the Gibbs free energy and never a
  sum: composite Gibbs formation (``E_high + correction``) belongs to
  the analysis layer, never to this profile.
- ``"gibbs_energy"`` (Hartree): the parsed full Gibbs free energy
  ``g`` only, when parsed.
- ``"gibbs_correction"`` (Hartree): the parsed thermal Gibbs
  correction ``gc`` only; when the parser yields ``e`` and ``g`` but
  no explicit correction, it is derived once as ``gc = g - e``.
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
from typing import Any

from ..domain._immutable import FrozenDict
from ..domain.artifact import ArtifactSet
from ..domain.diagnostics import Diagnostic, DiagnosticSeverity
from ..domain.result import Provenance, ResultSet, ScientificResult, make_result_id
from ..domain.structure import StructureRecord, StructureSet
from ..domain.units import Unit
from .native import GeometryOutput, NativeResult, ParsedGeometry, ResolvedCalculationInputs
from .profile_ensemble import EnsembleProfile
from .profile_path_endpoints import PathEndpointsProfile
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
    """Return ``(electronic, gibbs, correction)`` energies in Hartree.

    Canonical scientific-energy contract (frozen): ``energy`` is the
    parsed electronic energy only, ``gibbs_energy`` the parsed full
    Gibbs energy only, ``gibbs_correction`` the parsed thermal
    correction only.  A missing correction is derived once as
    ``g - e`` when both are parsed; sums are never formed here, so a
    downstream composite (``E_high + correction``) can never double
    count Gibbs content already folded into ``energy``.
    """
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
    """Return ScientificResult kwargs stamped with B's deterministic identity.

    ``result_id`` comes from :func:`make_result_id` over complete
    producer/subject/kind data with ``producer_digest`` passed verbatim
    from the producing work item's ``semantic_digest`` (coordinated keyword
    ``producer_digest``; B owns the helper).  Retries or reordering of the
    same semantic item retain refs; changed science moves the producer
    digest and mints new refs.  Value digests, paths, and ordinals never
    substitute.

    When the context carries no producer digest (pre-contract unit
    fixtures), results are emitted without ``result_id``; production
    execution always supplies it via the executor.
    """
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
    """Return named-slot parents in slot order, or ``None`` for standard items.

    When the inputs carry reactant/product slots (QST/NEB shapes), the
    output structure descends from every present slot in semantic order
    (reactant, product, guess) — never dict or random order.
    """
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
    """Build the single output structure for *context*.

    Native facts decide, never task names or roles:

    - ``PRODUCED`` with content differing from the input is a
      transformation: a new entity with parent linkage, results bound to it.
    - ``PRODUCED`` whose content is identical to the input (single-point
      measurement parsed back verbatim) retains the input entity: the
      input record itself is returned, results bind the input id.
    - ``NONE`` (no geometry parsed) is the passthrough record with
      identical content and a parent link; the executor, not the profile,
      decides whether passthrough is acceptable from declared checks.
    """
    native_result = context.native_result
    inputs: ResolvedCalculationInputs = context.inputs
    source = inputs.structure
    charge = inputs.charge if inputs.charge is not None else source.charge
    multiplicity = inputs.multiplicity if inputs.multiplicity is not None else source.multiplicity
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
        ):
            # Measurement of unchanged geometry: retain input identity.
            return source, GeometrySemantics.PASSTHROUGH
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
        """Normalize parser facts into domain collections.

        Transformation/measurement rule (frozen): output results bind the
        output geometry entity.  A produced native geometry mints a new
        entity and results bind it; a missing geometry (measurement /
        passthrough) retains the input entity as the subject.
        """
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
                        kind="energy", value=chosen, unit=Unit.HARTREE,
                        subject_id=subject_id, context=context, provenance=provenance,
                    )
                )
            )
        if gibbs is not None:
            results.append(
                ScientificResult(
                    **_result_kwargs(
                        kind="gibbs_energy", value=gibbs, unit=Unit.HARTREE,
                        subject_id=subject_id, context=context, provenance=provenance,
                    )
                )
            )
        if correction is not None:
            results.append(
                ScientificResult(
                    **_result_kwargs(
                        kind="gibbs_correction", value=correction, unit=Unit.HARTREE,
                        subject_id=subject_id, context=context, provenance=provenance,
                    )
                )
            )

        frequencies = tuple(float(value) for value in native_result.frequencies_cm)
        if frequencies:
            results.append(
                ScientificResult(
                    **_result_kwargs(
                        kind="frequencies", value=list(frequencies), unit=Unit.CM_INVERSE,
                        subject_id=subject_id, context=context, provenance=provenance,
                    )
                )
            )
            num_imaginary, lowest = count_imaginary_frequencies(frequencies)
            results.append(
                ScientificResult(
                    **_result_kwargs(
                        kind="num_imaginary_frequencies", value=num_imaginary, unit=None,
                        subject_id=subject_id, context=context, provenance=provenance,
                    )
                )
            )
            if lowest is not None:
                results.append(
                    ScientificResult(
                        **_result_kwargs(
                            kind="lowest_frequency", value=lowest, unit=Unit.CM_INVERSE,
                            subject_id=subject_id, context=context, provenance=provenance,
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
#: into the capability registry.  ``path_endpoints`` normalizes bidirectional
#: reaction-path output (IRC/NEB) into forward/reverse endpoint structures;
#: ``ensemble`` normalizes conformer-ensemble output (GOAT) into member
#: structures.  Both are full runtime implementations, not registry names.
PROFILES: dict[str, ResultProfile] = {
    "standard": StandardResultProfile(),
    "path_endpoints": PathEndpointsProfile(),
    "ensemble": EnsembleProfile(),
}
