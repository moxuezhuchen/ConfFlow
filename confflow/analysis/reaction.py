#!/usr/bin/env python3

"""Per-reaction Gibbs/PES aggregation for V4-6 (no chemistry assignment).

For one reaction group — a transition state plus its two native path
endpoints — this module resolves Gibbs energies under an explicit
:class:`~confflow.analysis.thermochemistry.EnergyModel` and derives:

- ``barrier_forward_endpoint = G_TS - G_forward``,
- ``barrier_reverse_endpoint = G_TS - G_reverse``,
- ``endpoint_gibbs_delta = G_reverse - G_forward`` (reference
  ``reverse_minus_forward``),
- ``endpoint_energy_delta = E_reverse - E_forward`` (reference
  ``reverse_minus_forward``),
- a structured ``reaction_profile`` payload bundling every value,
  source, formula, and policy input.

Barrier semantics deliberately avoid chemistry assignment: ``forward``
and ``reverse`` are native path directions only.  The profile carries
``assignment: None`` and never relabels an endpoint as reactant or
product.  Any missing piece fails the group closed (no results, typed
error diagnostics); nothing is ever defaulted or substituted silently.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Final

from ..domain._immutable import FrozenDict
from ..domain.diagnostics import Diagnostic, DiagnosticSeverity
from ..domain.result import Provenance, ResultSet, ScientificResult
from ..domain.units import Unit
from .thermochemistry import (
    IMPLEMENTATION_VERSION,
    EnergyModel,
    ResolvedGibbs,
    resolve_node_gibbs,
    select_result,
)
from .units import AnalysisMathError, value_in_hartree

__all__ = [
    "DIAGNOSTIC_CODES",
    "KIND_BARRIER_FORWARD_ENDPOINT",
    "KIND_BARRIER_REVERSE_ENDPOINT",
    "KIND_ENDPOINT_ENERGY_DELTA",
    "KIND_ENDPOINT_GIBBS_DELTA",
    "KIND_REACTION_PROFILE",
    "REFERENCE_MIN_ZERO",
    "REFERENCE_REVERSE_MINUS_FORWARD",
    "ReactionAnalysis",
    "ReactionNodeGroup",
    "assemble_reaction_result",
]

#: Computed barrier of the TS above the forward endpoint.
KIND_BARRIER_FORWARD_ENDPOINT: Final[str] = "barrier_forward_endpoint"

#: Computed barrier of the TS above the reverse endpoint.
KIND_BARRIER_REVERSE_ENDPOINT: Final[str] = "barrier_reverse_endpoint"

#: Computed electronic endpoint delta (reverse minus forward).
KIND_ENDPOINT_ENERGY_DELTA: Final[str] = "endpoint_energy_delta"

#: Computed Gibbs endpoint delta (reverse minus forward).
KIND_ENDPOINT_GIBBS_DELTA: Final[str] = "endpoint_gibbs_delta"

#: Structured per-group PES payload carried as a result value mapping.
KIND_REACTION_PROFILE: Final[str] = "reaction_profile"

#: Delta reference convention: ``reverse - forward``.
REFERENCE_REVERSE_MINUS_FORWARD: Final[str] = "reverse_minus_forward"

#: Delta reference convention: each endpoint minus the minimum endpoint.
REFERENCE_MIN_ZERO: Final[str] = "min_zero"

#: Diagnostic codes emitted by :func:`assemble_reaction_result`.
DIAGNOSTIC_CODES: Final[tuple[str, ...]] = (
    "analysis_energy_missing",
    "analysis_correction_missing",
    "analysis_unit_mismatch",
    "analysis_group_incomplete",
)

_ERROR_TO_DIAGNOSTIC: Final[dict[str, str]] = {
    "energy_missing": "analysis_energy_missing",
    "correction_missing": "analysis_correction_missing",
    "unit_mismatch": "analysis_unit_mismatch",
    "non_numeric_value": "analysis_unit_mismatch",
}

_FORMULA_BARRIER_FORWARD: Final[str] = "barrier_forward_endpoint=G_TS-G_forward"
_FORMULA_BARRIER_REVERSE: Final[str] = "barrier_reverse_endpoint=G_TS-G_reverse"
_FORMULA_ENDPOINT_GIBBS: Final[str] = "endpoint_gibbs_delta=G_reverse-G_forward"
_FORMULA_ENDPOINT_ENERGY: Final[str] = "endpoint_energy_delta=E_reverse-E_forward"


@dataclass(frozen=True, slots=True)
class ReactionNodeGroup:
    """One reaction's node subjects (frozen seam for Agent A).

    ``forward``/``reverse`` are native path directions, never chemistry
    roles.  Agent A's grouping owns how these ids are discovered; this
    module only consumes them.

    Parameters
    ----------
    group_key : str
        Reaction pairing key shared by the three nodes.
    ts_structure_id : str
        Subject id of the transition-state node.
    forward_structure_id : str
        Subject id of the forward path endpoint.
    reverse_structure_id : str
        Subject id of the reverse path endpoint.
    """

    group_key: str
    ts_structure_id: str
    forward_structure_id: str
    reverse_structure_id: str

    def node_ids(self) -> dict[str, str]:
        """Return node subjects keyed by ``ts``/``forward``/``reverse``."""
        return {
            "ts": self.ts_structure_id,
            "forward": self.forward_structure_id,
            "reverse": self.reverse_structure_id,
        }


@dataclass(frozen=True, slots=True)
class ReactionAnalysis:
    """Per-group aggregation outcome: computed results plus diagnostics."""

    results: tuple[ScientificResult, ...] = ()
    diagnostics: tuple[Diagnostic, ...] = field(default_factory=tuple)

    @property
    def ok(self) -> bool:
        """Return whether the group aggregated without error diagnostics."""
        return not [item for item in self.diagnostics if item.is_error]


def _diagnostic(
    code: str,
    message: str,
    *,
    analysis_step_id: str | None,
    group_key: str | None,
    subject_structure_id: str | None,
    reason: str,
    details: dict[str, Any] | None = None,
) -> Diagnostic:
    """Build one error diagnostic for a failed group."""
    payload: dict[str, Any] = {
        "group_key": group_key,
        "subject_structure_id": subject_structure_id,
        "reason": reason,
    }
    if details:
        payload.update(details)
    return Diagnostic(
        code=code,
        message=message,
        severity=DiagnosticSeverity.ERROR,
        step_id=analysis_step_id,
        details=FrozenDict(payload),
    )


def _diagnostic_for_error(
    error: AnalysisMathError,
    *,
    analysis_step_id: str | None,
    group_key: str,
) -> Diagnostic:
    """Map a math failure onto its typed group diagnostic."""
    code = _ERROR_TO_DIAGNOSTIC.get(error.code, "analysis_unit_mismatch")
    return _diagnostic(
        code,
        str(error),
        analysis_step_id=analysis_step_id,
        group_key=group_key,
        subject_structure_id=error.subject_structure_id,
        reason=error.code,
        details=dict(error.details),
    )


def _provenance(
    *,
    analysis_step_id: str | None,
    formula: str,
    group_key: str,
    node_ids: dict[str, str],
    source_ids: dict[str, str | None],
    energy_model: EnergyModel,
    extra: dict[str, Any] | None = None,
) -> Provenance:
    """Build provenance for one computed group result."""
    metadata: dict[str, Any] = {
        "formula": formula,
        "group_key": group_key,
        "nodes": dict(node_ids),
        "electronic_source_id": source_ids.get("electronic_source_id"),
        "correction_source_id": source_ids.get("correction_source_id"),
        "gibbs_source_id": source_ids.get("gibbs_source_id"),
        "fallback_used": source_ids.get("fallback_used", False),
        "energy_model": energy_model.to_dict(),
        "implementation_version": IMPLEMENTATION_VERSION,
    }
    if extra:
        metadata.update(extra)
    return Provenance(
        program=None,
        program_version=None,
        method=None,
        basis=None,
        adapter=IMPLEMENTATION_VERSION,
        step_id=analysis_step_id,
        work_item_id=None,
        metadata=FrozenDict(metadata),
    )


def _computed_energy_result(
    *,
    kind: str,
    value_hartree: float,
    subject_structure_id: str,
    analysis_step_id: str | None,
    provenance: Provenance,
) -> ScientificResult:
    """Build one computed Hartree energy result for the group."""
    return ScientificResult(
        kind=kind,
        value=value_hartree,
        unit=Unit.HARTREE,
        subject_structure_id=subject_structure_id,
        source_step_id=analysis_step_id,
        provenance=provenance,
    )


def _energy_entry(value_hartree: float) -> dict[str, Any]:
    """Return an explicit-unit energy entry for the profile payload."""
    return {"value": value_hartree, "unit": Unit.HARTREE.value}


def assemble_reaction_result(
    group: ReactionNodeGroup,
    energy_model: EnergyModel,
    lookup: Mapping[str, ResultSet],
    *,
    analysis_step_id: str | None = None,
) -> ReactionAnalysis:
    """Aggregate one reaction group into barriers, deltas, and a profile.

    Parameters
    ----------
    group : ReactionNodeGroup
        TS plus forward/reverse endpoint subject ids and the group key.
    energy_model : EnergyModel
        Explicit direct/composite/fallback Gibbs policy applied to all
        three nodes.
    lookup : Mapping[str, ResultSet]
        Source lookup mapping each canonical node subject id to its
        merged per-node result pool (across theory levels).  Kind
        selection never guesses subjects: a subject absent from the
        lookup fails the group closed.
    analysis_step_id : str or None
        Analysis step id carried as the source of computed results.

    Returns
    -------
    ReactionAnalysis
        Five computed results (two barriers, two endpoint deltas, one
        reaction profile) on success; empty results plus one error
        diagnostic per missing piece when the group fails closed.
    """
    node_ids = group.node_ids()
    for node, subject in node_ids.items():
        if not isinstance(subject, str) or not subject.strip():
            return ReactionAnalysis(
                results=(),
                diagnostics=(
                    _diagnostic(
                        "analysis_group_incomplete",
                        f"reaction group node {node!r} has no subject structure id",
                        analysis_step_id=analysis_step_id,
                        group_key=group.group_key or None,
                        subject_structure_id=None,
                        reason="missing_node_subject",
                        details={"node": node},
                    ),
                ),
            )
    if not isinstance(group.group_key, str) or not group.group_key.strip():
        return ReactionAnalysis(
            results=(),
            diagnostics=(
                _diagnostic(
                    "analysis_group_incomplete",
                    "reaction group has no group key",
                    analysis_step_id=analysis_step_id,
                    group_key=None,
                    subject_structure_id=None,
                    reason="missing_group_key",
                ),
            ),
        )

    diagnostics: list[Diagnostic] = []
    gibbs: dict[str, ResolvedGibbs] = {}
    for node, subject in node_ids.items():
        pool = lookup.get(subject)
        if pool is None or len(pool) == 0:
            diagnostics.append(
                _diagnostic(
                    "analysis_energy_missing",
                    f"no source results for reaction node {node!r}",
                    analysis_step_id=analysis_step_id,
                    group_key=group.group_key,
                    subject_structure_id=subject,
                    reason="no_source_pool",
                    details={"node": node},
                )
            )
            continue
        try:
            gibbs[node] = resolve_node_gibbs(pool, subject, energy_model)
        except AnalysisMathError as exc:
            diagnostics.append(
                _diagnostic_for_error(
                    exc, analysis_step_id=analysis_step_id, group_key=group.group_key
                )
            )
    electronic: dict[str, float] = {}
    electronic_sources: dict[str, str] = {}
    for node, subject in node_ids.items():
        pool = lookup.get(subject)
        if pool is None or len(pool) == 0:
            continue  # Already reported above; the group fails closed below.
        selected = select_result(
            pool,
            subject,
            energy_model.electronic_selector,
            energy_model.electronic_selector,
        )
        if selected is None:
            diagnostics.append(
                _diagnostic(
                    "analysis_energy_missing",
                    f"electronic energy ({energy_model.electronic_selector!r}) is missing",
                    analysis_step_id=analysis_step_id,
                    group_key=group.group_key,
                    subject_structure_id=subject,
                    reason="energy_missing",
                    details={"node": node, "selector": energy_model.electronic_selector},
                )
            )
            continue
        try:
            electronic[node] = value_in_hartree(selected)
            electronic_sources[node] = selected.value_digest
        except AnalysisMathError as exc:
            diagnostics.append(
                _diagnostic_for_error(
                    exc, analysis_step_id=analysis_step_id, group_key=group.group_key
                )
            )
    if diagnostics or len(gibbs) != 3 or len(electronic) != 3:
        if not diagnostics:
            diagnostics.append(
                _diagnostic(
                    "analysis_group_incomplete",
                    "reaction group is missing resolved node energies",
                    analysis_step_id=analysis_step_id,
                    group_key=group.group_key,
                    subject_structure_id=None,
                    reason="incomplete_resolution",
                )
            )
        return ReactionAnalysis(results=(), diagnostics=tuple(diagnostics))

    barrier_forward = gibbs["ts"].value_hartree - gibbs["forward"].value_hartree
    barrier_reverse = gibbs["ts"].value_hartree - gibbs["reverse"].value_hartree
    endpoint_gibbs_delta = gibbs["reverse"].value_hartree - gibbs["forward"].value_hartree
    endpoint_energy_delta = electronic["reverse"] - electronic["forward"]

    source_ids: list[str] = sorted(
        {
            digest
            for resolved in gibbs.values()
            for digest in (
                resolved.electronic_source_id,
                resolved.correction_source_id,
                resolved.gibbs_source_id,
            )
            if digest is not None
        }
        | set(electronic_sources.values())
    )
    fallback_used = {node: gibbs[node].fallback_used for node in ("ts", "forward", "reverse")}

    ts_subject = node_ids["ts"]
    base_sources = {
        "electronic_source_id": gibbs["ts"].electronic_source_id,
        "correction_source_id": gibbs["ts"].correction_source_id,
        "gibbs_source_id": gibbs["ts"].gibbs_source_id,
        "fallback_used": gibbs["ts"].fallback_used,
    }
    results = (
        _computed_energy_result(
            kind=KIND_BARRIER_FORWARD_ENDPOINT,
            value_hartree=barrier_forward,
            subject_structure_id=ts_subject,
            analysis_step_id=analysis_step_id,
            provenance=_provenance(
                analysis_step_id=analysis_step_id,
                formula=_FORMULA_BARRIER_FORWARD,
                group_key=group.group_key,
                node_ids=node_ids,
                source_ids=base_sources,
                energy_model=energy_model,
            ),
        ),
        _computed_energy_result(
            kind=KIND_BARRIER_REVERSE_ENDPOINT,
            value_hartree=barrier_reverse,
            subject_structure_id=ts_subject,
            analysis_step_id=analysis_step_id,
            provenance=_provenance(
                analysis_step_id=analysis_step_id,
                formula=_FORMULA_BARRIER_REVERSE,
                group_key=group.group_key,
                node_ids=node_ids,
                source_ids=base_sources,
                energy_model=energy_model,
            ),
        ),
        _computed_energy_result(
            kind=KIND_ENDPOINT_GIBBS_DELTA,
            value_hartree=endpoint_gibbs_delta,
            subject_structure_id=ts_subject,
            analysis_step_id=analysis_step_id,
            provenance=_provenance(
                analysis_step_id=analysis_step_id,
                formula=_FORMULA_ENDPOINT_GIBBS,
                group_key=group.group_key,
                node_ids=node_ids,
                source_ids=base_sources,
                energy_model=energy_model,
                extra={"reference": REFERENCE_REVERSE_MINUS_FORWARD},
            ),
        ),
        _computed_energy_result(
            kind=KIND_ENDPOINT_ENERGY_DELTA,
            value_hartree=endpoint_energy_delta,
            subject_structure_id=ts_subject,
            analysis_step_id=analysis_step_id,
            provenance=_provenance(
                analysis_step_id=analysis_step_id,
                formula=_FORMULA_ENDPOINT_ENERGY,
                group_key=group.group_key,
                node_ids=node_ids,
                source_ids=base_sources,
                energy_model=energy_model,
                extra={"reference": REFERENCE_REVERSE_MINUS_FORWARD},
            ),
        ),
    )
    profile_payload: dict[str, Any] = {
        "group_key": group.group_key,
        "nodes": dict(node_ids),
        "electronic_energy": {
            node: _energy_entry(electronic[node]) for node in ("ts", "forward", "reverse")
        },
        "gibbs_energy": {
            node: _energy_entry(gibbs[node].value_hartree) for node in ("ts", "forward", "reverse")
        },
        "relative": {
            "endpoint_gibbs_delta": _energy_entry(endpoint_gibbs_delta),
            "endpoint_energy_delta": _energy_entry(endpoint_energy_delta),
            "reference": REFERENCE_REVERSE_MINUS_FORWARD,
        },
        "barriers": {
            "forward_endpoint": _energy_entry(barrier_forward),
            "reverse_endpoint": _energy_entry(barrier_reverse),
        },
        "source_result_ids": list(source_ids),
        "energy_model": energy_model.to_dict(),
        "fallback_used": dict(fallback_used),
        "assignment": None,
    }
    profile = ScientificResult(
        kind=KIND_REACTION_PROFILE,
        value=profile_payload,
        subject_structure_id=ts_subject,
        source_step_id=analysis_step_id,
        provenance=_provenance(
            analysis_step_id=analysis_step_id,
            formula="reaction_profile",
            group_key=group.group_key,
            node_ids=node_ids,
            source_ids=base_sources,
            energy_model=energy_model,
        ),
    )
    return ReactionAnalysis(results=(*results, profile), diagnostics=())
