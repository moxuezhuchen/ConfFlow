"""Fixed-core requests reject ambiguous targets before invoking a backend."""

import numpy as np
import pytest

from confflow.science.confgen.calculator import FixedAtom, RelaxRequest


@pytest.mark.parametrize("index", [-1, True, 0.5])
def test_fixed_target_requires_a_nonnegative_integer_index(index) -> None:
    with pytest.raises(ValueError, match="index"):
        FixedAtom(index, (0.0, 0.0, 0.0))


@pytest.mark.parametrize("point", [(0.0, 0.0), (np.nan, 0.0, 0.0), (np.inf, 0.0, 0.0)])
def test_fixed_target_requires_a_finite_coordinate_triple(point) -> None:
    with pytest.raises(ValueError, match="coordinate"):
        FixedAtom(0, point)


def test_request_refuses_duplicate_and_out_of_range_fixed_atoms() -> None:
    coords = np.zeros((2, 3))
    target = FixedAtom(0, (0.0, 0.0, 0.0))
    with pytest.raises(ValueError, match="duplicate"):
        RelaxRequest("duplicate", coords, fixed_atoms=(target, target))
    with pytest.raises(ValueError, match="out of range"):
        RelaxRequest("outside", coords, fixed_atoms=(FixedAtom(2, (0.0, 0.0, 0.0)),))


def test_request_refuses_a_start_that_disagrees_with_the_fixed_reference() -> None:
    coords = np.zeros((2, 3))
    with pytest.raises(ValueError, match="differs from its reference"):
        RelaxRequest("mismatch", coords, fixed_atoms=(FixedAtom(0, (0.001, 0.0, 0.0)),))


@pytest.mark.parametrize("coords", [np.zeros((2, 2)), np.array([[np.nan, 0.0, 0.0]])])
def test_request_refuses_invalid_geometry_before_a_backend_runs(coords) -> None:
    with pytest.raises(ValueError, match="finite Nx3"):
        RelaxRequest("invalid", coords)
