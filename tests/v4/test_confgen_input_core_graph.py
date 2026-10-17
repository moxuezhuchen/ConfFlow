#!/usr/bin/env python3

"""``INPUT_SHAPE`` admission in :class:`CoordinationSpec` (input-core keep).

The input shape uses the structure's own metal-donor core as template, so it is valid at any
coordination number from ``INPUT_CN_MIN``; ideal shapes keep their CN range and admissibility.
"""

from __future__ import annotations

import pytest

from confflow.science.confgen.graph import (
    CN_RANGE,
    CN_SHAPES,
    INPUT_CN_MIN,
    INPUT_SHAPE,
    SUPPORTED_SHAPES,
    BindingSite,
    CoordinationSpec,
    UnsupportedTopologyError,
)


def _sites(n: int) -> tuple[BindingSite, ...]:
    return tuple(BindingSite(id=f"D{k}", atoms=(k + 1,)) for k in range(n))


@pytest.mark.parametrize("cn", [INPUT_CN_MIN, 4, 7, 8, 9, 12])
def test_input_shape_accepts_any_cn_from_minimum(cn: int) -> None:
    spec = CoordinationSpec(metal_center=0, binding_sites=_sites(cn), shapes=(INPUT_SHAPE,))
    assert spec.shapes == (INPUT_SHAPE,)
    assert spec.coordination_number == cn


def test_input_shape_rejects_below_minimum() -> None:
    with pytest.raises(UnsupportedTopologyError, match="at least"):
        CoordinationSpec(
            metal_center=0, binding_sites=_sites(INPUT_CN_MIN - 1), shapes=(INPUT_SHAPE,)
        )


def test_input_shape_cannot_be_mixed_with_ideal_shapes() -> None:
    with pytest.raises(UnsupportedTopologyError, match="cannot be combined"):
        CoordinationSpec(
            metal_center=0, binding_sites=_sites(6), shapes=(INPUT_SHAPE, "octahedral")
        )


def test_ideal_shapes_keep_their_cn_range() -> None:
    with pytest.raises(UnsupportedTopologyError, match="outside supported range"):
        CoordinationSpec(metal_center=0, binding_sites=_sites(CN_RANGE[1] + 1))
    with pytest.raises(UnsupportedTopologyError, match="not admissible"):
        CoordinationSpec(metal_center=0, binding_sites=_sites(4), shapes=("octahedral",))
    assert CoordinationSpec(metal_center=0, binding_sites=_sites(6)).shapes == CN_SHAPES[6]


def test_input_shape_stays_out_of_the_ideal_shape_contract() -> None:
    assert INPUT_SHAPE not in SUPPORTED_SHAPES
    assert all(INPUT_SHAPE not in shapes for shapes in CN_SHAPES.values())
