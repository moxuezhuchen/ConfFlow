#!/usr/bin/env python3

"""Compute-seam bridge between grouping and energy math (V4-6, main-owned).

:class:`ReactionEnergyModel` satisfies the structural
``EnergyModel`` Protocol in :mod:`confflow.analysis.executor` by
delegating to :mod:`confflow.analysis.reaction` with the policy object
from :mod:`confflow.analysis.thermochemistry`.  Neither side imports the
other; this bridge is the only place that knows both.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from ..domain.result import ResultSet
from .models import ComputedGroup, ReactionGroup
from .reaction import ReactionNodeGroup, assemble_reaction_result
from .thermochemistry import EnergyModel as EnergyPolicy

__all__ = [
    "ReactionEnergyModel",
    "policy_from_native",
]


class ReactionEnergyModel:
    """Compute seam backed by explicit Gibbs policy math."""

    def __init__(self, policy: EnergyPolicy) -> None:
        self._policy = policy

    def compute(
        self,
        group: ReactionGroup,
        lookup: Mapping[str, ResultSet],
        *,
        analysis_step_id: str | None = None,
        endpoint_assignment: Mapping[str, str] | None = None,
    ) -> ComputedGroup:
        """Aggregate one reaction group into computed results."""
        del endpoint_assignment
        node = ReactionNodeGroup(
            group_key=group.group_key,
            ts_structure_id=group.ts_structure_id or "",
            forward_structure_id=group.forward_endpoint_id or "",
            reverse_structure_id=group.reverse_endpoint_id or "",
        )
        analysis = assemble_reaction_result(
            node, self._policy, lookup, analysis_step_id=analysis_step_id
        )
        return ComputedGroup(results=analysis.results, diagnostics=analysis.diagnostics)

    def to_dict(self) -> dict[str, Any]:
        """Return the canonical policy payload for digest folding."""
        return self._policy.to_dict()


def policy_from_native(native: Mapping[str, Any]) -> EnergyPolicy:
    """Build the Gibbs policy from analysis-step native params.

    Expected keys: ``energy_mode`` (``"direct"`` default), selectors
    ``electronic_result_kind`` (default ``"energy"``) and
    ``correction_result_kind`` (default ``"gibbs_correction"``),
    ``energy_fallback`` (``"none"`` default).  Step-level keys
    (``method``, ``analysis_kind``) are accepted and ignored here.
    Anything else fails closed so a misspelled policy key can never
    silently mean something else.
    """
    step_level = frozenset({"method", "analysis_kind"})
    allowed = frozenset(
        {"energy_mode", "electronic_result_kind", "correction_result_kind", "energy_fallback"}
    )
    unknown = sorted(set(native) - allowed - step_level)
    if unknown:
        raise ValueError(f"unknown analysis energy-model keys: {unknown}")
    return EnergyPolicy(
        mode=str(native.get("energy_mode", "direct")),
        electronic_selector=str(native.get("electronic_result_kind", "energy")),
        correction_selector=str(native.get("correction_result_kind", "gibbs_correction")),
        fallback=str(native.get("energy_fallback", "none")),
    )
