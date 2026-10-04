#!/usr/bin/env python3

"""Core facade and shim compatibility against the science authority.

``core/bonding.py``, ``core/data.py``, and ``core/constants.py`` are thin
forwarding shims over ``confflow.science.{bonding,data,constants}``.  These
tests pin that the historical import paths resolve to the *same objects*
(not copies) and that the documented default-perception and unknown-element
behaviour is unchanged.
"""

from __future__ import annotations

import confflow.core.bonding as core_bonding
import confflow.science.bonding as science_bonding
import confflow.science.constants as science_constants
import confflow.science.data as science_data
from confflow.core import HARTREE_TO_KCALMOL, get_atomic_number  # facade surface
from confflow.core.bonding import UnknownElementError, infer_bond_pairs
from confflow.core.data import GV_COVALENT_RADII, get_covalent_radius


def test_core_bonding_shim_is_the_science_authority() -> None:
    assert core_bonding.build_adjacency is science_bonding.build_adjacency
    assert core_bonding.infer_bond_pairs is science_bonding.infer_bond_pairs
    assert core_bonding.covalent_radius is science_bonding.covalent_radius
    assert core_bonding.has_known_covalent_radii is science_bonding.has_known_covalent_radii
    assert core_bonding.MIN_BOND_DISTANCE_ANGSTROM is science_bonding.MIN_BOND_DISTANCE_ANGSTROM
    assert core_bonding.UnknownElementError is science_bonding.UnknownElementError


def test_core_data_shim_is_the_science_authority() -> None:
    assert GV_COVALENT_RADII is science_data.GV_COVALENT_RADII
    assert get_covalent_radius is science_data.get_covalent_radius
    from confflow.core.data import PERIODIC_SYMBOLS, SYMBOL_TO_ATOMIC_NUMBER
    from confflow.core.data import get_atomic_number as core_get_atomic_number
    from confflow.core.data import get_element_symbol as core_get_element_symbol

    assert PERIODIC_SYMBOLS is science_data.PERIODIC_SYMBOLS
    assert SYMBOL_TO_ATOMIC_NUMBER is science_data.SYMBOL_TO_ATOMIC_NUMBER
    assert core_get_atomic_number is science_data.get_atomic_number
    assert core_get_element_symbol is science_data.get_element_symbol


def test_core_constants_facade_resolves_to_science_authority() -> None:
    from confflow.core.constants import HARTREE_TO_KCALMOL as core_constant

    assert core_constant is science_constants.HARTREE_TO_KCALMOL
    assert HARTREE_TO_KCALMOL is science_constants.HARTREE_TO_KCALMOL
    assert get_atomic_number is science_data.get_atomic_number


def test_default_bond_perception_unchanged() -> None:
    # Water at ~0.96 A O-H: bonded; H-H at ~1.52 A: never bonded.
    numbers = [8, 1, 1]
    coords = [
        [0.0, 0.0, 0.0],
        [0.760, 0.590, 0.000],
        [-0.760, 0.590, 0.000],
    ]
    assert infer_bond_pairs(numbers, coords, bond_scale=1.15) == [(0, 1), (0, 2)]
    assert infer_bond_pairs(numbers, coords, bond_scale=1.2) == [(0, 1), (0, 2)]


def test_unknown_element_rejection_unchanged() -> None:
    import pytest

    with pytest.raises(UnknownElementError):
        infer_bond_pairs([8, 119], [[0.0, 0.0, 0.0], [0.0, 0.0, 0.96]], bond_scale=1.2)
    # Zero-radius placeholder (Z=0) has no usable covalent radius either.
    assert science_bonding.covalent_radius(0) is None
    # Legacy tolerance table untouched: unknown Z falls back to 1.50.
    assert get_covalent_radius(999) == 1.50
