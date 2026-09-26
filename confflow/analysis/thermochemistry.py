#!/usr/bin/env python3

"""Gibbs-energy resolution for V4-6 reaction aggregation.

Two explicit policies, never auto-selected:

- ``direct``: use the parsed ``gibbs_energy`` result as-is.
- ``composite``: ``G_high = E_high + (G_low - E_low)``, where the
  high-level electronic energy and the low-level Gibbs correction are
  picked by exact-kind selectors from the caller's per-subject pool.

Selectors match exact result kinds only (``"energy"``,
``"gibbs_energy"``, ``"gibbs_correction"``, ...); cross-kind guessing
never happens.  A missing high-level electronic energy fails closed
unless the model opts into ``fallback="low_level"``, in which case the
low-level Gibbs energy is used and explicitly marked with formula
``"fallback_low_level"`` in both provenance and the reaction-profile
digest payload.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from numbers import Real
from typing import Any, Final

from ..domain._immutable import FrozenDict
from ..domain.result import Provenance, ResultSet, ScientificResult
from ..domain.units import Unit
from .units import (
    CODE_CORRECTION_MISSING,
    CODE_ENERGY_MISSING,
    AnalysisMathError,
    value_in_hartree,
)

__all__ = [
    "CODE_CORRECTION_MISSING",
    "CODE_ENERGY_MISSING",
    "DIRECT_GIBBS_KIND",
    "FORMULA_COMPOSITE",
    "FORMULA_DIRECT",
    "FORMULA_FALLBACK_LOW_LEVEL",
    "IMPLEMENTATION_VERSION",
    "AnalysisMathError",
    "EnergyModel",
    "ResolvedGibbs",
    "composite_gibbs",
    "direct_gibbs",
    "gibbs_correction_value",
    "gibbs_result",
    "resolve_node_gibbs",
    "select_result",
]

#: Implementation version stamped into every computed result's provenance.
IMPLEMENTATION_VERSION: Final[str] = "confflow.analysis.thermochemistry.v1"

#: Result kind consumed by the ``direct`` policy (an actually emitted kind).
DIRECT_GIBBS_KIND: Final[str] = "gibbs_energy"

#: Provenance formula for directly consumed Gibbs energies.
FORMULA_DIRECT: Final[str] = "direct_gibbs"

#: Provenance formula for composite high-level Gibbs energies.
FORMULA_COMPOSITE: Final[str] = "G_high=E_high+(G_low-E_low)"

#: Provenance formula when the low-level Gibbs fallback is used.
FORMULA_FALLBACK_LOW_LEVEL: Final[str] = "fallback_low_level"

_DIRECT_MODES: Final[frozenset[str]] = frozenset({"direct", "composite"})
_FALLBACK_MODES: Final[frozenset[str]] = frozenset({"none", "low_level"})


@dataclass(frozen=True, slots=True)
class EnergyModel:
    """Explicit Gibbs-energy policy (frozen seam for Agent A).

    Parameters
    ----------
    mode : str
        ``"direct"`` (consume ``gibbs_energy`` as-is) or ``"composite"``
        (``G_high = E_high + correction``).
    electronic_selector : str
        Exact result kind carrying the electronic energy (composite
        mode), e.g. ``"energy"``.  Ignored by the ``direct`` policy.
    correction_selector : str
        Exact result kind carrying the Gibbs correction (composite
        mode), e.g. ``"gibbs_correction"``.  Ignored by ``direct``.
    fallback : str
        ``"none"`` (default: missing high-level energy fails closed) or
        ``"low_level"`` (opt-in: use the low-level Gibbs energy,
        explicitly marked).

    Raises
    ------
    AnalysisMathError
        With code ``invalid_energy_model`` when any field is invalid.
        Selectors must name actually emitted result kinds; invented
        aliases are never resolved.
    """

    mode: str
    electronic_selector: str
    correction_selector: str
    fallback: str = "none"

    def __post_init__(self) -> None:
        """Validate every policy field explicitly."""
        if self.mode not in _DIRECT_MODES:
            raise AnalysisMathError(
                "invalid_energy_model",
                f"mode must be one of {sorted(_DIRECT_MODES)}, got {self.mode!r}",
            )
        for name in ("electronic_selector", "correction_selector"):
            selector = getattr(self, name)
            if not isinstance(selector, str) or not selector.strip():
                raise AnalysisMathError(
                    "invalid_energy_model",
                    f"{name} must be a non-empty result kind",
                )
        if self.fallback not in _FALLBACK_MODES:
            raise AnalysisMathError(
                "invalid_energy_model",
                f"fallback must be one of {sorted(_FALLBACK_MODES)}, got {self.fallback!r}",
            )

    def to_dict(self) -> dict[str, Any]:
        """Return the policy payload folded into profile digests."""
        return {
            "mode": self.mode,
            "electronic_selector": self.electronic_selector,
            "correction_selector": self.correction_selector,
            "fallback": self.fallback,
        }


@dataclass(frozen=True, slots=True)
class ResolvedGibbs:
    """One subject's Gibbs energy resolved under an :class:`EnergyModel`.

    Source ids are result ``value_digest`` strings (results carry no
    entity ids); absent legs are ``None`` (direct resolutions have no
    electronic/correction legs, fallbacks have no electronic leg).
    """

    subject_structure_id: str
    value_hartree: float
    formula: str
    electronic_source_id: str | None
    correction_source_id: str | None
    gibbs_source_id: str | None
    fallback_used: bool
    energy_model: EnergyModel


def select_result(
    results: ResultSet | Iterable[ScientificResult],
    subject_structure_id: str | None,
    kind: str,
    selector: str,
) -> ScientificResult | None:
    """Return the first result of exactly *selector* kind for *subject*.

    Parameters
    ----------
    results : ResultSet or iterable
        Candidate source results (the caller's per-subject pool).
    subject_structure_id : str or None
        Subject to match; ``None`` matches any subject.
    kind : str
        Requested kind (error/lookup context only).
    selector : str
        Exact kind to match.  When ``selector != kind`` no result is
        returned: selection never guesses across kinds.

    Returns
    -------
    ScientificResult or None
        The first matching result in pool order, or ``None``.
    """
    if not selector or selector != kind:
        return None
    for result in results:
        if result.kind != selector:
            continue
        if subject_structure_id is not None and (
            result.subject_structure_id != subject_structure_id
        ):
            continue
        return result
    return None


def gibbs_correction_value(
    gibbs_low: ScientificResult | None,
    electronic_low: ScientificResult | None,
    *,
    subject_structure_id: str | None = None,
) -> float:
    """Return the low-level Gibbs correction ``G_low - E_low`` in Hartree.

    Parameters
    ----------
    gibbs_low : ScientificResult or None
        Low-level Gibbs free energy (both legs required).
    electronic_low : ScientificResult or None
        Low-level electronic energy (both legs required).
    subject_structure_id : str or None
        Subject context for error reports.

    Returns
    -------
    float
        The correction in Hartree.  Both inputs are unit-checked before
        any arithmetic.

    Raises
    ------
    AnalysisMathError
        With code ``energy_missing`` when either leg is absent, or
        ``unit_mismatch`` when either leg is not a compatible energy.
        Nothing is ever substituted silently for a missing theory level.
    """
    subject = subject_structure_id
    if subject is None:
        subject = gibbs_low.subject_structure_id if gibbs_low is not None else None
    if gibbs_low is None:
        raise AnalysisMathError(
            CODE_ENERGY_MISSING,
            "low-level Gibbs energy is missing; cannot form the correction",
            subject_structure_id=subject,
        )
    if electronic_low is None:
        raise AnalysisMathError(
            CODE_ENERGY_MISSING,
            "low-level electronic energy is missing; cannot form the correction",
            subject_structure_id=subject,
        )
    return value_in_hartree(gibbs_low) - value_in_hartree(electronic_low)


def composite_gibbs(
    electronic_high: ScientificResult,
    correction_hartree: float,
    *,
    subject_structure_id: str | None = None,
) -> float:
    """Return ``E_high + correction`` in Hartree.

    Parameters
    ----------
    electronic_high : ScientificResult
        High-level electronic energy (unit-checked).
    correction_hartree : float
        Gibbs correction already expressed in Hartree.
    subject_structure_id : str or None
        Subject context for error reports (defaults to the result's).

    Returns
    -------
    float
        The composite Gibbs energy in Hartree.

    Raises
    ------
    AnalysisMathError
        With code ``unit_mismatch`` or ``non_numeric_value`` on bad input.
    """
    subject = subject_structure_id or electronic_high.subject_structure_id
    if isinstance(correction_hartree, bool) or not isinstance(correction_hartree, Real):
        raise AnalysisMathError(
            "non_numeric_value",
            "correction must be a real number in Hartree",
            subject_structure_id=subject,
        )
    correction = float(correction_hartree)
    if not math.isfinite(correction):
        raise AnalysisMathError(
            "non_numeric_value",
            "correction must be finite",
            subject_structure_id=subject,
        )
    return value_in_hartree(electronic_high) + correction


def direct_gibbs(
    gibbs_result_value: ScientificResult,
    *,
    subject_structure_id: str | None = None,
) -> float:
    """Return a directly consumed Gibbs energy in Hartree.

    Parameters
    ----------
    gibbs_result_value : ScientificResult
        Parsed ``gibbs_energy`` result (unit-checked).
    subject_structure_id : str or None
        Subject context for error reports (defaults to the result's).

    Returns
    -------
    float
        The Gibbs energy in Hartree.

    Raises
    ------
    AnalysisMathError
        With code ``unit_mismatch`` or ``non_numeric_value`` on bad input.
    """
    value = value_in_hartree(gibbs_result_value)
    if subject_structure_id is not None and (
        gibbs_result_value.subject_structure_id != subject_structure_id
    ):
        raise AnalysisMathError(
            "unit_mismatch",
            "Gibbs result subject does not match the requested subject",
            subject_structure_id=subject_structure_id,
            details={"result_subject": gibbs_result_value.subject_structure_id},
        )
    return value


def resolve_node_gibbs(
    pool: ResultSet | Iterable[ScientificResult],
    subject_structure_id: str,
    energy_model: EnergyModel,
) -> ResolvedGibbs:
    """Resolve one subject's Gibbs energy under *energy_model*.

    Parameters
    ----------
    pool : ResultSet or iterable
        Per-node merged source pool.  The caller (executor/grouping)
        merges every candidate source result for this node under its
        canonical subject id across theory levels; kind selection here
        never guesses subjects.
    subject_structure_id : str
        Canonical node subject id.
    energy_model : EnergyModel
        Explicit direct/composite/fallback policy.

    Returns
    -------
    ResolvedGibbs
        Value, formula, source digests, and fallback flag.

    Raises
    ------
    AnalysisMathError
        With code ``energy_missing`` (absent Gibbs/electronic leg),
        ``correction_missing`` (absent correction leg), or
        ``unit_mismatch`` (incompatible units).  The group fails closed
        on any of these; no theory level is ever substituted silently.
    """
    subject = subject_structure_id
    if energy_model.mode == "direct":
        gibbs = select_result(pool, subject, DIRECT_GIBBS_KIND, DIRECT_GIBBS_KIND)
        if gibbs is None:
            raise AnalysisMathError(
                CODE_ENERGY_MISSING,
                f"direct Gibbs energy ({DIRECT_GIBBS_KIND!r}) is missing",
                subject_structure_id=subject,
                details={"selector": DIRECT_GIBBS_KIND},
            )
        return ResolvedGibbs(
            subject_structure_id=subject,
            value_hartree=direct_gibbs(gibbs, subject_structure_id=subject),
            formula=FORMULA_DIRECT,
            electronic_source_id=None,
            correction_source_id=None,
            gibbs_source_id=gibbs.value_digest,
            fallback_used=False,
            energy_model=energy_model,
        )
    electronic = select_result(
        pool, subject, energy_model.electronic_selector, energy_model.electronic_selector
    )
    if electronic is None:
        if energy_model.fallback == "low_level":
            gibbs_low = select_result(pool, subject, DIRECT_GIBBS_KIND, DIRECT_GIBBS_KIND)
            if gibbs_low is None:
                raise AnalysisMathError(
                    CODE_ENERGY_MISSING,
                    "high-level electronic energy is missing and no low-level "
                    "Gibbs fallback is available",
                    subject_structure_id=subject,
                    details={"selector": energy_model.electronic_selector},
                )
            return ResolvedGibbs(
                subject_structure_id=subject,
                value_hartree=direct_gibbs(gibbs_low, subject_structure_id=subject),
                formula=FORMULA_FALLBACK_LOW_LEVEL,
                electronic_source_id=None,
                correction_source_id=None,
                gibbs_source_id=gibbs_low.value_digest,
                fallback_used=True,
                energy_model=energy_model,
            )
        raise AnalysisMathError(
            CODE_ENERGY_MISSING,
            "high-level electronic energy is missing and fallback is disabled",
            subject_structure_id=subject,
            details={"selector": energy_model.electronic_selector},
        )
    correction_result = select_result(
        pool, subject, energy_model.correction_selector, energy_model.correction_selector
    )
    if correction_result is None:
        raise AnalysisMathError(
            CODE_CORRECTION_MISSING,
            "Gibbs correction is missing; cannot form the composite energy",
            subject_structure_id=subject,
            details={"selector": energy_model.correction_selector},
        )
    correction = value_in_hartree(correction_result)
    return ResolvedGibbs(
        subject_structure_id=subject,
        value_hartree=composite_gibbs(electronic, correction, subject_structure_id=subject),
        formula=FORMULA_COMPOSITE,
        electronic_source_id=electronic.value_digest,
        correction_source_id=correction_result.value_digest,
        gibbs_source_id=None,
        fallback_used=False,
        energy_model=energy_model,
    )


def gibbs_result(
    resolved: ResolvedGibbs,
    *,
    analysis_step_id: str | None = None,
) -> ScientificResult:
    """Build the ``gibbs_energy`` result for a resolved node.

    Parameters
    ----------
    resolved : ResolvedGibbs
        Resolution to materialize (Hartree internally).
    analysis_step_id : str or None
        Analysis step id carried as the result source.

    Returns
    -------
    ScientificResult
        Kind ``"gibbs_energy"`` in Hartree with source digests, formula,
        policy, and implementation version in provenance.
    """
    return ScientificResult(
        kind="gibbs_energy",
        value=resolved.value_hartree,
        unit=Unit.HARTREE,
        subject_structure_id=resolved.subject_structure_id,
        source_step_id=analysis_step_id,
        provenance=Provenance(
            program=None,
            program_version=None,
            method=None,
            basis=None,
            adapter=IMPLEMENTATION_VERSION,
            step_id=analysis_step_id,
            work_item_id=None,
            metadata=FrozenDict(
                {
                    "formula": resolved.formula,
                    "electronic_source_id": resolved.electronic_source_id,
                    "correction_source_id": resolved.correction_source_id,
                    "gibbs_source_id": resolved.gibbs_source_id,
                    "fallback_used": resolved.fallback_used,
                    "energy_model": resolved.energy_model.to_dict(),
                    "implementation_version": IMPLEMENTATION_VERSION,
                }
            ),
        ),
    )


def lookup_to_mapping(
    lookup: Mapping[str, ResultSet] | Mapping[str, tuple[ScientificResult, ...]],
) -> Mapping[str, ResultSet]:
    """Normalize a source lookup into subject-id to :class:`ResultSet` form."""
    normalized: dict[str, ResultSet] = {}
    for subject, pool in lookup.items():
        normalized[subject] = pool if isinstance(pool, ResultSet) else ResultSet(tuple(pool))
    return normalized
