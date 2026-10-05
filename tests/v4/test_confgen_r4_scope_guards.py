#!/usr/bin/env python3
"""R4G ring scope guards (science card, Stage-level regression + controls).

The CP-constrained ``RingStage`` must enforce the same fail-closed scope the
legacy realizer always enforced (via ``validate_ring_system``): chord /
fused / spiro / overlapping systems, non-bonded traversal, metal chelate,
coordination-lane overlap, and the same checks on ``preserve_input`` systems.
Before R4G the Stage skipped that validation, so scope-violating inputs were
silently REALIZED (or failed with geometry-dependent accidents instead of an
explicit scope reason).

Sections:
- ``TestR4GScopeRegression``: illegal inputs that were REALIZED (or
  accidentally failed) on the base tree; they must be ``unsupported`` with an
  explicit reason after R4G and publish nothing end-to-end.
- ``TestR4GAlreadyGuarded``: guards that already worked on the base tree
  (kept here so nobody mislabels them as new).
- ``TestR4GLegalControls``: legal ring systems that must still realize
  (single, linked with matching forms, disconnected) and publish end-to-end.
"""

from __future__ import annotations

import numpy as np
import pytest

from confflow.domain import StructureRecord
from confflow.science.confgen.engine import ConfgenEngine
from confflow.science.confgen.model import (
    ConfgenStateKey,
    WorkingRealization,
    build_context,
)
from confflow.science.confgen.ring import RingStage
from confflow.science.confgen.ring.templates import get_template, template_coords


def _ring_adjacency(size: int) -> list[list[int]]:
    graph: list[list[int]] = [[] for _ in range(size)]
    for position in range(size):
        first = position
        second = (position + 1) % size
        graph[first].append(second)
        graph[second].append(first)
    return graph


def _bonds_of(graph: list[list[int]]) -> list[list[int]]:
    seen: set[tuple[int, int]] = set()
    bonds: list[list[int]] = []
    for first, row in enumerate(graph):
        for second in row:
            key = (min(first, second), max(first, second))
            if key not in seen:
                seen.add(key)
                bonds.append([first, second])
    return bonds


def _record(record_id: str, elements: list[str], coords: np.ndarray) -> StructureRecord:
    return StructureRecord(
        id=record_id,
        atoms=tuple(elements),
        coordinates=tuple(tuple(float(v) for v in row) for row in coords),
    )


def _context_for(record: StructureRecord, graph: list[list[int]], extra: dict | None = None):
    spec: dict = {
        "index_base": 1,
        "topology": {"bonds": [[a + 1, b + 1] for a, b in _bonds_of(graph)]},
    }
    if extra:
        spec.update(extra)
    return build_context(record, spec)


def _parent_of(record: StructureRecord) -> WorkingRealization:
    return WorkingRealization(structure=record, state_key=ConfgenStateKey(), provenance={})


def _realize_first(
    coords: np.ndarray,
    elements: list[str],
    graph: list[list[int]],
    rings: list[dict],
    extra: dict | None = None,
    tolerances: dict | None = None,
):
    record = _record("seed", elements, np.asarray(coords, dtype=float))
    context = _context_for(record, graph, extra)
    axis: dict = {"rings": rings}
    if tolerances:
        axis["tolerances"] = tolerances
    stage = RingStage(axis)
    parent = _parent_of(record)
    target = next(stage.enumerate_targets(parent, context))
    return stage.realize(parent, target, context)


class TestR4GScopeRegression:
    """Illegal inputs the base Stage REALIZED (or mis-failed)."""

    def test_chord_extra_bond_is_fused(self) -> None:
        from confflow.science.confgen.ring.puckering import canonical_forms, cp_to_coords

        forms = {f"{f.family}_{f.index}": f for f in canonical_forms(6)}
        coords = cp_to_coords(forms["C_0"].cp_target)
        graph = _ring_adjacency(6)
        graph[0].append(2)
        graph[2].append(0)
        result = _realize_first(
            coords, ["C"] * 6, graph, [{"id": "r1", "atoms": [0, 1, 2, 3, 4, 5]}]
        )
        assert result.status == "unsupported"
        assert "fused_or_bridged" in result.reason
        assert result.structure is None

    def test_overlapping_identical_rings_rejected(self) -> None:
        from confflow.science.confgen.ring.puckering import canonical_forms, cp_to_coords

        forms = {f"{f.family}_{f.index}": f for f in canonical_forms(6)}
        coords = cp_to_coords(forms["C_0"].cp_target)
        graph = _ring_adjacency(6)
        result = _realize_first(
            coords,
            ["C"] * 6,
            graph,
            [
                {"id": "r1", "atoms": [0, 1, 2, 3, 4, 5]},
                {"id": "r2", "atoms": [0, 1, 2, 3, 4, 5]},
            ],
        )
        assert result.status == "unsupported"
        assert "overlapping_systems" in result.reason
        assert result.structure is None

    def test_spiro_shared_atom_rejected(self) -> None:
        chair = template_coords(get_template("chair_A_6"))
        other = template_coords(get_template("chair_A_6")) + np.array([3.0, 0.0, 0.0])
        other[0] = chair[5]
        coords = np.vstack([chair, other[1:]])
        graph: list[list[int]] = [[] for _ in range(11)]
        for first, second in [
            (0, 1),
            (1, 2),
            (2, 3),
            (3, 4),
            (4, 5),
            (5, 0),
            (5, 6),
            (6, 7),
            (7, 8),
            (8, 9),
            (9, 10),
            (10, 5),
        ]:
            graph[first].append(second)
            graph[second].append(first)
        result = _realize_first(
            coords,
            ["C"] * 11,
            graph,
            [
                {"id": "a", "atoms": [0, 1, 2, 3, 4, 5]},
                {"id": "b", "atoms": [5, 6, 7, 8, 9, 10]},
            ],
        )
        assert result.status == "unsupported"
        assert "overlapping_systems" in result.reason
        assert result.structure is None

    def test_chelate_metal_in_ring_rejected(self) -> None:
        coords = template_coords(get_template("planar_4"))
        result = _realize_first(
            coords,
            ["C", "C", "FE", "C"],
            _ring_adjacency(4),
            [{"id": "r1", "atoms": [0, 1, 2, 3]}],
        )
        assert result.status == "unsupported"
        assert "chelate" in result.reason
        assert result.structure is None

    def test_preserve_chelate_rejected(self) -> None:
        coords = template_coords(get_template("planar_4"))
        result = _realize_first(
            coords,
            ["C", "C", "FE", "C"],
            _ring_adjacency(4),
            [{"id": "r1", "atoms": [0, 1, 2, 3], "treatment": "preserve_input"}],
        )
        assert result.status == "unsupported"
        assert "chelate" in result.reason
        assert result.structure is None

    def test_preserve_coordination_overlap_rejected(self) -> None:
        coords = template_coords(get_template("chair_A_6"))
        result = _realize_first(
            coords,
            ["C"] * 6,
            _ring_adjacency(6),
            [{"id": "r1", "atoms": [0, 1, 2, 3, 4, 5], "treatment": "preserve_input"}],
            extra={"coordination": {"metal_center": 1, "binding_sites": []}},
        )
        assert result.status == "unsupported"
        assert "coordination_overlap" in result.reason
        assert result.structure is None

    def test_engine_chord_publishes_nothing(self) -> None:
        from confflow.science.confgen.ring.puckering import canonical_forms, cp_to_coords

        forms = {f"{f.family}_{f.index}": f for f in canonical_forms(6)}
        coords = cp_to_coords(forms["C_0"].cp_target)
        graph = _ring_adjacency(6)
        graph[0].append(2)
        graph[2].append(0)
        record = _record("seed", ["C"] * 6, coords)
        context = build_context(
            record,
            {
                "index_base": 1,
                "topology": {"bonds": [[a + 1, b + 1] for a, b in _bonds_of(graph)]},
                "rings": [{"id": "r1", "atoms": [1, 2, 3, 4, 5, 6]}],
            },
        )
        run = ConfgenEngine(stages=[RingStage(context.resolved_spec.thaw())]).run(context)
        report = run.report.thaw()
        assert report["counts"]["published"] == 0


class TestR4GAlreadyGuarded:
    """Guards that already worked before R4G (not claimed as new)."""

    def test_nonbonded_traversal_unsupported(self) -> None:
        result = _realize_first(
            np.zeros((6, 3)),
            ["C"] * 6,
            _ring_adjacency(6),
            [{"id": "r1", "atoms": [0, 1, 2, 4]}],
        )
        assert result.status == "unsupported"
        assert "nonbonded_traversal" in result.reason
        assert result.structure is None

    def test_macrocycle_size_unsupported(self) -> None:
        graph: list[list[int]] = [[] for _ in range(8)]
        for position in range(8):
            graph[position].append((position + 1) % 8)
            graph[position].append((position - 1) % 8)
        record = _record("seed", ["C"] * 8, np.zeros((8, 3)))
        context = _context_for(record, graph)
        stage = RingStage({"rings": [{"id": "big", "atoms": list(range(8))}]})
        parent = _parent_of(record)
        with pytest.raises(ValueError, match="unsupported_ring_size"):
            list(stage.enumerate_targets(parent, context))

    def test_unknown_template_unsupported(self) -> None:
        record = _record("seed", ["C"] * 4, np.zeros((4, 3)))
        context = _context_for(record, _ring_adjacency(4))
        stage = RingStage({"rings": [{"id": "r1", "atoms": [0, 1, 2, 3], "templates": ["sofa_4"]}]})
        parent = _parent_of(record)
        with pytest.raises(ValueError, match="sofa_4"):
            list(stage.enumerate_targets(parent, context))

    def test_enumerate_coordination_overlap_unsupported(self) -> None:
        coords = template_coords(get_template("chair_A_6"))
        record = _record("seed", ["C"] * 6, coords)
        context = _context_for(
            record,
            _ring_adjacency(6),
            extra={"coordination": {"metal_center": 1, "binding_sites": []}},
        )
        stage = RingStage({"rings": [{"id": "r1", "atoms": [0, 1, 2, 3, 4, 5]}]})
        parent = _parent_of(record)
        result = stage.realize(parent, next(stage.enumerate_targets(parent, context)), context)
        assert result.status == "unsupported"
        assert result.reason == "coordination_overlap"


class TestR4GLegalControls:
    """Legal systems that must still realize and publish."""

    def test_single_ring_realizes(self) -> None:
        coords = template_coords(get_template("chair_A_6"))
        result = _realize_first(
            coords, ["C"] * 6, _ring_adjacency(6), [{"id": "r1", "atoms": [0, 1, 2, 3, 4, 5]}]
        )
        assert result.status == "realized"
        assert result.structure is not None

    def test_linked_rings_with_matching_forms_realize(self) -> None:
        chair = template_coords(get_template("chair_A_6"))
        centroid = chair.mean(axis=0)
        outward = (chair[0] - centroid) / float(np.linalg.norm(chair[0] - centroid))
        joint = chair[0] + 1.54 * outward
        axis = np.cross(outward, np.array([0.0, 0.0, 1.0]))
        axis = axis / float(np.linalg.norm(axis))
        turn = -np.eye(3) + 2.0 * np.outer(axis, axis)
        ring_b = (chair - chair[0]) @ turn.T + joint
        coords = np.vstack([chair, ring_b])
        graph: list[list[int]] = [[] for _ in range(12)]
        for offset in (0, 6):
            for position in range(6):
                first = offset + position
                second = offset + (position + 1) % 6
                graph[first].append(second)
                graph[second].append(first)
        graph[0].append(6)
        graph[6].append(0)
        result = _realize_first(
            coords,
            ["C"] * 12,
            graph,
            [
                {"id": "a", "atoms": [0, 1, 2, 3, 4, 5], "forms": ["C_1"]},
                {"id": "b", "atoms": [6, 7, 8, 9, 10, 11], "forms": ["C_1"]},
            ],
        )
        assert result.status == "realized"
        assert result.structure is not None

    def test_disconnected_rings_realize(self) -> None:
        chair = template_coords(get_template("chair_A_6"))
        coords = np.vstack([chair, chair + np.array([20.0, 0.0, 0.0])])
        graph: list[list[int]] = [[] for _ in range(12)]
        for offset in (0, 6):
            for position in range(6):
                first = offset + position
                second = offset + (position + 1) % 6
                graph[first].append(second)
                graph[second].append(first)
        result = _realize_first(
            coords,
            ["C"] * 12,
            graph,
            [
                {"id": "a", "atoms": [0, 1, 2, 3, 4, 5]},
                {"id": "b", "atoms": [6, 7, 8, 9, 10, 11]},
            ],
        )
        assert result.status == "realized"
        assert result.structure is not None

    def test_engine_legal_publishes(self) -> None:
        coords = template_coords(get_template("chair_A_6"))
        graph = _ring_adjacency(6)
        record = _record("seed", ["C"] * 6, coords)
        context = build_context(
            record,
            {
                "index_base": 1,
                "topology": {"bonds": [[a + 1, b + 1] for a, b in _bonds_of(graph)]},
                "rings": [{"id": "r1", "atoms": [1, 2, 3, 4, 5, 6]}],
            },
        )
        run = ConfgenEngine(stages=[RingStage(context.resolved_spec.thaw())]).run(context)
        report = run.report.thaw()
        assert report["counts"]["published"] >= 1
