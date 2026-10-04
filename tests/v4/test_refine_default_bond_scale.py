#!/usr/bin/env python3

"""The refine perception default equals the ConfGen scale (C1-a).

A C-C pair at 1.81 A sits between the two candidate scales: it is perceived
at ``1.2 * (r_C + r_C) = 1.848 A`` but not at ``1.15 * (r_C + r_C) = 1.771 A``.
Two near-identical two-carbon structures whose bond sets differ only through
such a pair are duplicates at 1.15 and not collapsible at 1.2, which makes the
effective scale observable through refine's dedup verdict.
"""

from __future__ import annotations

from typing import Any

from confflow.domain.structure import StructureRecord
from confflow.execution.transform_executor import REFINE_DEFAULT_BOND_SCALE, TransformExecutor
from confflow.workflow.v4.confgen_schema import ConfgenToleranceModel

THRESHOLD = 0.25
#: The discriminating C-C distance: unbonded at 1.15, bonded at 1.2.
MARGINAL_DISTANCE = 1.81
#: The stretched partner: unbonded at every scale in play.
STRETCHED_DISTANCE = 1.95


def _record(identifier: str, distance: float, working_topology: Any = None) -> StructureRecord:
    return StructureRecord(
        id=identifier,
        atoms=("C", "C"),
        coordinates=((0.0, 0.0, 0.0), (distance, 0.0, 0.0)),
        charge=0,
        multiplicity=1,
        working_topology=working_topology,
    )


def _pair(working_topology: Any = None) -> list[StructureRecord]:
    return [
        _record("a", MARGINAL_DISTANCE, working_topology),
        _record("b", STRETCHED_DISTANCE, working_topology),
    ]


def test_the_refine_default_matches_the_confgen_perception_scale() -> None:
    assert REFINE_DEFAULT_BOND_SCALE == 1.15
    assert REFINE_DEFAULT_BOND_SCALE == ConfgenToleranceModel().bond_scale
    retained, notes = TransformExecutor()._refine(_pair(), {})
    assert [record.id for record in retained] == ["a"]
    assert notes and notes[0].startswith("dropped b as duplicate of a")


def test_an_explicit_bond_scale_still_overrides_the_default() -> None:
    retained, _notes = TransformExecutor()._refine(_pair(), {"bond_scale": 1.2})
    assert [record.id for record in retained] == ["a", "b"]


def test_a_persisted_working_topology_wins_verbatim_at_any_scale() -> None:
    # Both records persist the empty graph although the geometry of "a" would
    # be perceived as bonded at 1.2: the persisted graph wins verbatim, so the
    # pair collapses at the default *and* at the explicit 1.2 (where mere
    # perception would have kept both).
    no_bonds: Any = ((), ())
    retained, _notes = TransformExecutor()._refine(_pair(no_bonds), {})
    assert [record.id for record in retained] == ["a"]
    retained, _notes = TransformExecutor()._refine(_pair(no_bonds), {"bond_scale": 1.2})
    assert [record.id for record in retained] == ["a"]
