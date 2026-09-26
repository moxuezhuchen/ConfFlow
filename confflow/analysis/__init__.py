#!/usr/bin/env python3

"""V4 analysis package: typed reaction/PES aggregation (V4-6, main-owned).

This package turns executed calculation results into structured scientific
analysis.  The analysis executor is pure: no native subprocesses, no
program adapters, no filenames — only typed :class:`StructureSet` /
:class:`ResultSet` inputs plus an explicit analysis definition.

Submodules
----------
- :mod:`confflow.analysis.models` — ``ReactionGroup``, ``AnalysisDefinition``,
  ``AnalysisStepResult``, ``AnalysisError``.
- :mod:`confflow.analysis.grouping` — deterministic reaction grouping.
- :mod:`confflow.analysis.executor` — the pure analysis executor.
- :mod:`confflow.analysis.registry` — analysis capability registry.
- :mod:`confflow.analysis.thermochemistry` — Gibbs selection/composition.
- :mod:`confflow.analysis.reaction` — per-group barrier/delta/profile math.
- :mod:`confflow.analysis.pes` — multi-group profile assembly + digests.
- :mod:`confflow.analysis.units` — hartree-based unit projections.
"""

from __future__ import annotations

from typing import Any

from ..domain.result import ResultSet
from ..domain.structure import StructureSet

__all__ = [
    "compute_reaction_profile",
]


def compute_reaction_profile(
    structures: StructureSet,
    results: ResultSet,
    energy_model: Any,
    *,
    analysis_step_id: str | None = None,
) -> Any:
    """Group structures and aggregate every complete reaction group.

    Parameters
    ----------
    structures : StructureSet
        Subject structures with explicit group keys, endpoint roles, and
        parent links.
    results : ResultSet
        Source results attached by subject id.
    energy_model : EnergyModel
        Explicit direct/composite/fallback Gibbs policy from
        :mod:`confflow.analysis.thermochemistry`.
    analysis_step_id : str or None
        Analysis step id carried as the source of computed results.

    Returns
    -------
    list
        One ``ReactionAnalysis`` per reaction group, in group-key order.
    """
    from .grouping import build_reaction_groups
    from .reaction import ReactionNodeGroup, assemble_reaction_result

    groups, _group_diagnostics = build_reaction_groups(structures, results)
    by_subject: dict[str, list[Any]] = {}
    for record in results:
        by_subject.setdefault(record.subject_structure_id or "", []).append(record)
    analyses = []
    for group in sorted(groups, key=lambda item: item.group_key):
        node = ReactionNodeGroup(
            group_key=group.group_key,
            ts_structure_id=group.ts_structure_id or "",
            forward_structure_id=group.forward_endpoint_id or "",
            reverse_structure_id=group.reverse_endpoint_id or "",
        )
        lookup = {subject: ResultSet(tuple(pool)) for subject, pool in by_subject.items()}
        analyses.append(
            assemble_reaction_result(node, energy_model, lookup, analysis_step_id=analysis_step_id)
        )
    return analyses
