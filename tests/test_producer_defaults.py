#!/usr/bin/env python3

"""The legacy tooling's program/task defaults have exactly one source.

``GlobalOptions.iprog`` and ``.itask`` used to be string literals in the dataclass
while every other option default came from :mod:`confflow.shared.defaults`.  The
constants are what they always were, and the legacy typed model still reads them
instead of carrying its own copy.

The manifest half of this file was retired with the V1/V2 editor manifest
(Architecture Diet PR-9); the V4 manifest is the only published manifest and is
covered by ``tests/v4/test_v46_producer_contract.py``.
"""

from __future__ import annotations

from typing import get_args

import pytest

import confflow.shared.defaults as defaults
from confflow.calc.config_model import (
    CalcStepParams,
    GlobalOptions,
    ProgramName,
    TaskName,
)
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

    def test_they_are_legal_members_of_the_typed_literals(self) -> None:
        assert DEFAULT_PROGRAM in get_args(ProgramName)
        assert DEFAULT_TASK in get_args(TaskName)


class TestTheTypedModelReadsTheConstants:
    def test_direct_construction_uses_the_constants(self) -> None:
        options = GlobalOptions()

        assert options.iprog == DEFAULT_PROGRAM
        assert options.itask == DEFAULT_TASK

    @pytest.mark.parametrize("raw", [None, {}, {"charge": 0}])
    def test_mapping_resolution_uses_the_constants(self, raw) -> None:
        options = GlobalOptions.from_mapping(raw)

        assert options.iprog == DEFAULT_PROGRAM
        assert options.itask == DEFAULT_TASK

    def test_an_explicit_value_still_wins(self) -> None:
        options = GlobalOptions.from_mapping({"iprog": "g16", "itask": "sp"})

        assert options.iprog == "g16"
        assert options.itask == "sp"

    def test_a_legacy_numeric_program_aliases_to_the_same_labels(self) -> None:
        """The alias normaliser is untouched by the change to the default."""
        assert GlobalOptions.from_mapping({"iprog": 1}).iprog == "g16"
        assert GlobalOptions.from_mapping({"iprog": 2}).iprog == DEFAULT_PROGRAM

    def test_calc_step_resolution_uses_the_constants(self) -> None:
        params = CalcStepParams.from_params({"keyword": "B3LYP/6-31G(d)"}, GlobalOptions())

        assert params.program == DEFAULT_PROGRAM
        assert params.task == DEFAULT_TASK

    def test_a_step_level_value_still_wins(self) -> None:
        params = CalcStepParams.from_params(
            {"keyword": "B3LYP/6-31G(d)", "iprog": "g16", "itask": "opt"}, GlobalOptions()
        )

        assert params.program == "g16"
        assert params.task == "opt"
