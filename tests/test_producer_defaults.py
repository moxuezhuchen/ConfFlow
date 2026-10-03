#!/usr/bin/env python3

"""The program/task default constants are what they always were.

The manifest half of this file was retired with the V1/V2 editor manifest
(Architecture Diet PR-9); the V4 manifest is the only published manifest and is
covered by ``tests/v4/test_v46_producer_contract.py``.
"""

from __future__ import annotations

import confflow.shared.defaults as defaults
from confflow.shared.defaults import DEFAULT_PROGRAM, DEFAULT_TASK


class TestTheConstantsAreWhatTheyAlwaysWere:
    def test_the_values_are_unchanged(self) -> None:
        """These two strings are behaviour, not preferences.

        Changing them changes what every workflow without an explicit
        ``iprog``/``itask`` runs.
        """
        assert DEFAULT_PROGRAM == "orca"
        assert DEFAULT_TASK == "opt_freq"

    def test_they_are_exported_from_the_defaults_module(self) -> None:
        assert "DEFAULT_PROGRAM" in defaults.__all__
        assert "DEFAULT_TASK" in defaults.__all__
