#!/usr/bin/env python3

"""ConfGen v3 torsion science tests (CORE lane).

Commanded==measured torsion grids, legacy adapter parity, fail-closed
validation, and perception coverage. Pure science; no energy evaluation.
"""

from __future__ import annotations

import itertools

import numpy as np
import pytest

from confflow.domain import FrozenDict, StructureRecord
from confflow.domain.completion import WorkItemStatus
from confflow.execution.confgen_executor import ConfgenExecutor
from confflow.science.bonds import covalent_radii
from confflow.science.confgen.model import WorkingRealization, build_context
from confflow.science.confgen.planner import MixedRadixGrid, normalize_executor_native
from confflow.science.confgen.torsion import (
    TorsionStage,
    legacy_cap_v1,
    legacy_grid_geometries,
    legacy_rotatable_bonds,
    measure_dihedral,
    wrap_degrees,
)
from tests.v4.test_repair_executors import _butane, _ctx, _item, _sci


def _butane_context(**overrides):
    """Build a torsion context over the minimal C4 chain (1-based spec)."""
    spec = {
        "schema_version": 3,
        "index_base": 1,
        "torsions": [
            {
                "id": "central",
                "bond": [2, 3],
                "model": "relative_rotation_grid",
                "angles": [0.0, 120.0, 240.0],
                "treatment": "enumerate",
                "rotate_side": "left",
            }
        ],
    }
    spec.update(overrides)
    return build_context(_butane("seed"), spec)


def _root(context):
    """Return the root working realization for a context."""
    return WorkingRealization(structure=context.structure, state_key=context.input_state_key)


# -- measurement -----------------------------------------------------------


def test_wrap_degrees_branches():
    """Angle wrapping lands in (-180, 180] on both branches."""
    assert wrap_degrees(190.0) == pytest.approx(-170.0)
    assert wrap_degrees(-190.0) == pytest.approx(170.0)
    assert wrap_degrees(360.0) == pytest.approx(0.0)
    assert wrap_degrees(90.0) == pytest.approx(90.0)


def test_measure_dihedral_known_right_angle():
    """A hand-built +90 degree frame measures +90."""
    coords = np.array(
        [
            [0.0, 0.0, 0.0],  # p (bond start)
            [1.0, 0.0, 0.0],  # q (bond end)
            [0.0, 1.0, 0.0],  # a attached to p
            [1.0, 0.0, 1.0],  # d attached to q
        ]
    )
    assert measure_dihedral(coords, 2, 0, 1, 3) == pytest.approx(90.0)


def test_measure_dihedral_degenerate_frame_fails_closed():
    """Collinear frames raise instead of returning a fake angle."""
    coords = np.array([[0.0, 0.0, 0.0], [1.0, 0.0, 0.0], [2.0, 0.0, 0.0], [3.0, 0.0, 0.0]])
    with pytest.raises(ValueError):
        measure_dihedral(coords, 0, 1, 2, 3)


# -- commanded == measured ---------------------------------------------------


@pytest.mark.parametrize("side", ["left", "right"])
def test_relative_commanded_equals_measured(side):
    """Every commanded relative angle is re-measured (no blind acceptance)."""
    context = _butane_context(
        torsions=[
            {
                "id": "central",
                "bond": [2, 3],
                "model": "relative_rotation_grid",
                "angles": [30.0, 120.0, -90.0],
                "treatment": "enumerate",
                "rotate_side": side,
            }
        ]
    )
    stage = TorsionStage(context.resolved_spec)
    root = _root(context)
    for target in stage.enumerate_targets(root, context):
        outcome = stage.realize(root, target, context)
        assert outcome.status == "realized", (target, outcome.reason)
        ok, measured, evidence = stage.audit_target(outcome.structure, target, root, context)
        assert ok, (target, measured, evidence)
        commanded = float(target.state_value["central"])
        assert abs(wrap_degrees(measured["central"] - commanded)) <= 1.0
        perception = stage.perceive(outcome.structure, context)
        assert set(perception.best_key) == {"central"}
        assert perception.confidence == "reported"


def test_absolute_setpoint_reaches_target():
    """Absolute four-atom grids converge to the commanded setpoint."""
    context = _butane_context(
        torsions=[
            {
                "id": "phi",
                "atoms": [1, 2, 3, 4],
                "model": "absolute_dihedral_grid",
                "angles": [60.0, 180.0],
                "treatment": "enumerate",
                "rotate_side": "left",
            }
        ]
    )
    stage = TorsionStage(context.resolved_spec)
    root = _root(context)
    for target in stage.enumerate_targets(root, context):
        outcome = stage.realize(root, target, context)
        assert outcome.status == "realized", (target, outcome.reason)
        ok, measured, evidence = stage.audit_target(outcome.structure, target, root, context)
        assert ok, (target, measured, evidence)
        assert abs(wrap_degrees(measured["phi"] - float(target.state_value["phi"]))) <= 1.0


def test_chemical_states_opt_in_and_measured():
    """Chemical states resolve by explicit map only, then measure out."""
    context = _butane_context(
        torsions=[
            {
                "id": "rot",
                "bond": [2, 3],
                "model": "chemical",
                "states": {"gauche": 60.0, "trans": 180.0},
                "treatment": "enumerate",
                "rotate_side": "left",
            }
        ]
    )
    stage = TorsionStage(context.resolved_spec)
    root = _root(context)
    targets = list(stage.enumerate_targets(root, context))
    assert [t.state_value["rot"] for t in targets] == ["gauche", "trans"]
    for target in targets:
        outcome = stage.realize(root, target, context)
        assert outcome.status == "realized", (target, outcome.reason)
        ok, measured, evidence = stage.audit_target(outcome.structure, target, root, context)
        assert ok, (target, measured, evidence)


def test_chemical_requires_explicit_states():
    """Chemical without a states map fails closed (no implicit defaults)."""
    with pytest.raises(ValueError):
        _butane_context(
            torsions=[
                {
                    "id": "rot",
                    "bond": [2, 3],
                    "model": "chemical",
                    "treatment": "enumerate",
                    "rotate_side": "left",
                }
            ]
        )


def test_stage_sign_convention_vs_legacy():
    """Stage coords equal legacy coords at negated angles (sign pinned)."""
    context = _butane_context()
    stage = TorsionStage(context.resolved_spec)
    root = _root(context)
    base = np.asarray(context.structure.coordinates, dtype=float)
    adjacency = [list(row) for row in context.adjacency]
    legacy = {
        ordinal: coords
        for ordinal, coords in legacy_grid_geometries(
            base, [[1, 2]], [[0.0, 120.0, 240.0]], adjacency, rotate_side="left"
        )
    }
    angles = [0.0, 120.0, 240.0]
    for target in stage.enumerate_targets(root, context):
        outcome = stage.realize(root, target, context)
        assert outcome.status == "realized"
        commanded = float(target.state_value["central"])
        # Legacy rotates about the chain-direction axis: equal geometry at
        # the negated angle (mod 360).
        ordinal = angles.index((-commanded) % 360.0)
        assert np.allclose(np.asarray(outcome.structure.coordinates), legacy[ordinal], atol=1e-9)


# -- legacy adapter parity ---------------------------------------------------


def test_legacy_adapter_matches_executor_coordinates_and_ordinals():
    """Legacy adapter reproduces executor geometry/ordinals bit-for-bit."""
    from confflow.science.torsion import topological_distance_matrix

    record = _butane("seed-a")
    native = {"chains": ["1-2-3-4"], "chain_angles": "0;0,120,240;0"}
    item = _item("c1:g1", "c1", [record])
    result = ConfgenExecutor().execute(item, _ctx(_sci(seed=11, native=FrozenDict(native)), "/tmp"))
    assert result.status is WorkItemStatus.COMPLETED
    base = np.asarray(record.coordinates, dtype=float)
    adjacency = legacy_adapter_adjacency(record)
    angle_lists = [[0.0], [0.0, 120.0, 240.0], [0.0]]
    chains = [[0, 1, 2, 3]]
    numbers = [6, 6, 6, 6]
    radii = covalent_radii(numbers)
    topo = topological_distance_matrix(adjacency)
    kept = {
        ordinal: coords
        for ordinal, coords in legacy_grid_geometries(
            base,
            chains,
            angle_lists,
            adjacency,
            radii=radii,
            topo=topo,
            clash_threshold=0.65,
        )
    }
    assert [m.ordinal for m in result.structures] == sorted(kept)
    for member in result.structures:
        assert np.allclose(np.asarray(member.coordinates), kept[member.ordinal], atol=0.0)


def legacy_adapter_adjacency(record: StructureRecord):
    """Perceive covalent adjacency exactly like the executor default."""
    from confflow.domain.elements import atomic_number
    from confflow.science.bonds import perceive_adjacency

    numbers = [atomic_number(symbol) for symbol in record.atoms]
    return perceive_adjacency(numbers, [tuple(p) for p in record.coordinates], bond_scale=1.15)


def test_legacy_cap_v1_matches_executor_capped_subset():
    """Versioned cap keeps the executor's survivor subset (documented)."""
    record = _butane("seed-a")
    native = {"chains": ["1-2-3-4"], "chain_angles": "0;0,60,120,180,240,300;0"}
    full = ConfgenExecutor().execute(
        _item("c1:g1", "c1", [record]), _ctx(_sci(seed=11, native=FrozenDict(native)), "/tmp")
    )
    assert full.status is WorkItemStatus.COMPLETED
    full_ordinals = sorted(m.ordinal for m in full.structures)
    capped = ConfgenExecutor().execute(
        _item("c1:g1", "c1", [record]),
        _ctx(_sci(seed=11, native=FrozenDict({**native, "max_conformers": 2})), "/tmp"),
    )
    assert capped.status is WorkItemStatus.COMPLETED
    expected = sorted(legacy_cap_v1(full_ordinals, seed=11, logical_key="c1:g1", cap=2))
    assert sorted(m.ordinal for m in capped.structures) == expected


def test_executor_native_normalization_round_trip():
    """Executor native maps to a normalized v3 spec with 0-based bonds."""
    spec = normalize_executor_native(
        {"chains": ["1-2-3-4"], "chain_angles": "0;0,120;0", "no_rotate": ["1-2"]},
        seed=7,
    )
    assert spec["index_base"] == 0
    bonds = {tuple(entry["bond"]): entry["treatment"] for entry in spec["torsions"]}
    assert bonds[(0, 1)] == "preserve_input"
    assert bonds[(1, 2)] == "enumerate"
    assert spec["torsions"][1]["angles"] == [0.0, 120.0]


def test_executor_native_rejects_max_conformers_mapping():
    """Old cap must use the versioned adapter, never silent v3 sampling."""
    with pytest.raises(ValueError, match="legacy_cap_v1"):
        normalize_executor_native({"chains": ["1-2-3-4"], "max_conformers": 2}, seed=1)


# -- fail-closed validation ----------------------------------------------------


def test_duplicate_bond_axes_fail_closed():
    """Two axes on the same bond are rejected at normalization."""
    with pytest.raises(ValueError, match="duplicate torsion bond"):
        _butane_context(
            torsions=[
                {"id": "a", "bond": [2, 3], "model": "relative_rotation_grid", "angles": [0.0]},
                {"id": "b", "bond": [3, 2], "model": "relative_rotation_grid", "angles": [0.0]},
            ]
        )


def test_ring_bond_refused_with_name():
    """Ring bonds refuse rotation, naming the bond (never silent)."""
    square = StructureRecord(
        id="square",
        atoms=("C", "C", "C", "C"),
        coordinates=((0.0, 0.0, 0.0), (1.5, 0.0, 0.0), (1.5, 1.5, 0.0), (0.0, 1.5, 0.0)),
        charge=0,
        multiplicity=1,
    )
    context = build_context(
        square,
        {
            "schema_version": 3,
            "index_base": 1,
            "torsions": [
                {
                    "id": "edge",
                    "bond": [1, 2],
                    "model": "relative_rotation_grid",
                    "angles": [0.0, 90.0],
                }
            ],
        },
    )
    stage = TorsionStage(context.resolved_spec)
    with pytest.raises(ValueError, match="ring bond"):
        stage.estimate(_root(context), context)


def test_nonfinite_angle_fails_closed():
    """NaN angles never enter a grid."""
    with pytest.raises(ValueError, match="finite"):
        _butane_context(
            torsions=[
                {
                    "id": "bad",
                    "bond": [2, 3],
                    "model": "relative_rotation_grid",
                    "angles": [float("nan")],
                }
            ]
        )


def test_clash_is_geometric_failure_not_proof():
    """Clash-dropped targets fail geometrically with explicit reason."""
    pentane = StructureRecord(
        id="pentane",
        atoms=("C", "C", "C", "C", "C"),
        coordinates=(
            (0.0, 0.0, 0.0),
            (1.5, 0.0, 0.0),
            (3.0, 0.4, 0.0),
            (4.5, 0.4, 0.0),
            (6.0, 0.8, 0.0),
        ),
        charge=0,
        multiplicity=1,
    )
    context = build_context(
        pentane,
        {
            "schema_version": 3,
            "index_base": 1,
            "torsions": [
                {
                    "id": "axis",
                    "bond": [2, 3],
                    "model": "relative_rotation_grid",
                    "angles": [0.0, 120.0],
                }
            ],
            # Absurd threshold: every non-bonded check clashes
            # deterministically (test value only; defaults untouched).
            "tolerances": {"clash_threshold": 1e9},
        },
    )
    stage = TorsionStage(context.resolved_spec)
    root = WorkingRealization(structure=context.structure, state_key=context.input_state_key)
    target = next(iter(stage.enumerate_targets(root, context)))
    outcome = stage.realize(root, target, context)
    assert outcome.status == "geometry_failure"
    assert outcome.reason == "clash"


def test_degenerate_axis_reports_numerical_failure():
    """Zero-length bond axes report instead of silently not rotating."""
    collapsed = StructureRecord(
        id="collapsed",
        atoms=("C", "C", "C", "C"),
        coordinates=(
            (0.0, 0.0, 0.0),
            (1.5, 0.0, 0.0),
            (1.5, 0.0, 0.0),  # coincident with atom 2: degenerate axis
            (3.0, 0.0, 0.0),
        ),
        charge=0,
        multiplicity=1,
    )
    context = build_context(
        collapsed,
        {
            "schema_version": 3,
            "index_base": 1,
            "torsions": [
                {"id": "axis", "bond": [2, 3], "model": "relative_rotation_grid", "angles": [90.0]}
            ],
            "topology": {"bonds": [[1, 2], [2, 3], [3, 4]]},
        },
    )
    stage = TorsionStage(context.resolved_spec)
    root = _root(context)
    target = next(iter(stage.enumerate_targets(root, context)))
    outcome = stage.realize(root, target, context)
    assert outcome.status == "numerical_failure"
    assert outcome.reason == "degenerate_axis"


def test_collinear_perception_is_ambiguous():
    """Unmeasurable frames are ambiguous, never a claimed state."""
    linear = StructureRecord(
        id="linear",
        atoms=("C", "C", "C", "C"),
        coordinates=((0.0, 0.0, 0.0), (1.5, 0.0, 0.0), (3.0, 0.0, 0.0), (4.5, 0.0, 0.0)),
        charge=0,
        multiplicity=1,
    )
    context = build_context(
        linear,
        {
            "schema_version": 3,
            "index_base": 1,
            "torsions": [
                {"id": "axis", "bond": [2, 3], "model": "relative_rotation_grid", "angles": [90.0]}
            ],
        },
    )
    stage = TorsionStage(context.resolved_spec)
    perception = stage.perceive(context.structure, context)
    assert perception.confidence == "ambiguous"


def test_preserve_input_axis_skipped_and_reported():
    """preserve_input axes contribute factor 1 and are reported, not rotated."""
    context = _butane_context(
        torsions=[
            {
                "id": "held",
                "bond": [2, 3],
                "model": "relative_rotation_grid",
                "angles": [0.0, 90.0],
                "treatment": "preserve_input",
            },
        ]
    )
    stage = TorsionStage(context.resolved_spec)
    root = _root(context)
    estimate = stage.estimate(root, context)
    assert estimate.declared_count == 1
    assert estimate.details["preserve_input"] == ("held",)
    assert [t.state_value for t in stage.enumerate_targets(root, context)] == [{}]


def test_mixed_radix_matches_product_order():
    """V3 ordinals equal itertools.product row-major ordinals."""
    grid = MixedRadixGrid([1, 3, 1])
    combos = [grid.index_to_combo(i) for i in grid.iter_indices()]
    assert combos == list(itertools.product(range(1), range(3), range(1)))


def test_legacy_bond_builder_matches_executor_sides():
    """Chain-aware side selection mirrors executor construction."""
    adjacency = [[1], [0, 2], [1, 3], [2]]
    bonds = legacy_rotatable_bonds([[0, 1, 2, 3]], adjacency, "left")
    assert [(b[0], b[1]) for b in bonds] == [(0, 1), (1, 2), (2, 3)]
    assert bonds[1][2] == [0]
