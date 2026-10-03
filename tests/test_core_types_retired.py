#!/usr/bin/env python3

"""Retired-module guard that used to live next to the viz report tests."""

from __future__ import annotations


class TestCoreTypesRetired:
    """``core.types`` (V1/V2 TypedDicts) was retired by Architecture Diet PR-9."""

    def test_core_types_module_is_gone(self):
        import importlib.util

        assert importlib.util.find_spec("confflow.core.types") is None
