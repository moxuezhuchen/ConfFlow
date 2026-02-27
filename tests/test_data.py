#!/usr/bin/env python3

"""Tests for core.data module."""

from __future__ import annotations


class TestData:
    """Tests for core.data module."""

    def test_get_covalent_radius(self):
        """Test getting covalent radius."""
        from confflow.core.data import get_covalent_radius

        assert get_covalent_radius(1) == 0.30  # H
        assert get_covalent_radius(6) == 0.77  # C
        assert get_covalent_radius(150) == 1.50  # Unknown

    def test_get_element_symbol(self):
        """Test getting element symbol."""
        from confflow.core.data import get_element_symbol

        assert get_element_symbol(1) == "H"
        assert get_element_symbol(6) == "C"
        assert get_element_symbol(0) == "X"
        assert get_element_symbol(400) == "X"

    def test_get_atomic_number(self):
        """Test getting atomic number."""
        from confflow.core.data import get_atomic_number

        assert get_atomic_number("H") == 1
        assert get_atomic_number("c") == 6
        assert get_atomic_number("Unknown") == 0

    def test_radii_sanity(self):
        from confflow.core.data import GV_COVALENT_RADII

        assert len(GV_COVALENT_RADII) >= 100
        assert abs(GV_COVALENT_RADII[1] - 0.30) < 1e-12
        assert abs(GV_COVALENT_RADII[6] - 0.77) < 1e-12
