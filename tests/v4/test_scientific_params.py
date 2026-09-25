#!/usr/bin/env python3

"""Scientific parameter precedence and electron-parity tests.

Pins the authority order between inherent structure properties, explicit step
overrides, and run-level defaults, plus the conflict and parity diagnostics
that make silent parameter changes impossible.
"""

from __future__ import annotations

import pytest

from confflow.domain import FrozenDict
from confflow.workflow.v4.document import ScientificDefaults
from confflow.workflow.v4.scientific import (
    check_electron_parity,
    resolve_scientific_parameters,
)
from tests.v4._builders import codes, reasons, structure
from tests.v4._builders import warnings as non_errors


def test_structure_value_wins_over_run_default() -> None:
    """An explicit inherent property outranks the run-level default."""
    effective, diagnostics = resolve_scientific_parameters(
        structure=structure("s1", charge=1, multiplicity=None),
        overrides={},
        defaults=ScientificDefaults(charge=0),
    )
    assert effective.charge == 1
    assert effective.charge_source == "structure"
    assert effective.multiplicity is None
    assert effective.multiplicity_source is None
    assert diagnostics == ()


def test_step_override_applies_when_structure_is_silent() -> None:
    """Overrides outrank run defaults when the structure declares nothing."""
    effective, diagnostics = resolve_scientific_parameters(
        structure=structure("s1", charge=None, multiplicity=None),
        overrides=FrozenDict({"charge": 2, "multiplicity": 1}),
        defaults=ScientificDefaults(charge=0, multiplicity=3),
    )
    assert (effective.charge, effective.multiplicity) == (2, 1)
    assert effective.charge_source == "step_override"
    assert effective.multiplicity_source == "step_override"
    assert diagnostics == ()


def test_run_default_applies_only_when_structure_and_override_are_silent() -> None:
    """Run defaults are the lowest-priority source."""
    effective, diagnostics = resolve_scientific_parameters(
        structure=structure("s1", charge=None, multiplicity=None),
        overrides={},
        defaults=ScientificDefaults(charge=0, multiplicity=1),
    )
    assert (effective.charge, effective.multiplicity) == (0, 1)
    assert effective.charge_source == "run_default"
    assert effective.multiplicity_source == "run_default"
    assert diagnostics == ()


def test_nothing_declared_yields_none_effective_values() -> None:
    """No declared values stays unknown rather than defaulting silently."""
    effective, diagnostics = resolve_scientific_parameters(
        structure=structure("s1", charge=None, multiplicity=None),
        overrides={},
        defaults=ScientificDefaults(),
    )
    assert effective.charge is None
    assert effective.multiplicity is None
    assert effective.charge_source is None
    assert effective.multiplicity_source is None
    assert diagnostics == ()


@pytest.mark.parametrize(
    ("overrides", "field_path", "structure_value", "override_value"),
    [
        pytest.param(
            {"charge": 2},
            "calculation.overrides.charge",
            0,
            2,
            id="charge",
        ),
        pytest.param(
            {"multiplicity": 3},
            "calculation.overrides.multiplicity",
            1,
            3,
            id="multiplicity",
        ),
    ],
)
def test_conflicting_override_emits_conflict_warning(
    overrides: dict[str, int],
    field_path: str,
    structure_value: int,
    override_value: int,
) -> None:
    """A divergent override is never silent: it is recorded as a warning."""
    effective, diagnostics = resolve_scientific_parameters(
        structure=structure("s1", charge=0, multiplicity=1),
        overrides=overrides,
        defaults=ScientificDefaults(),
    )
    assert codes(diagnostics) == ["scientific_parameter_conflict"]
    assert reasons(diagnostics) == ["structure_value_overridden"]
    conflict = non_errors(diagnostics)
    assert len(conflict) == 1
    assert conflict[0].is_error is False
    assert conflict[0].field_path == field_path
    assert conflict[0].details["structure_id"] == "s1"
    assert conflict[0].details["structure_value"] == structure_value
    assert conflict[0].details["override_value"] == override_value


@pytest.mark.parametrize(
    ("field", "override", "structure_value", "override_value"),
    [
        pytest.param("charge", 2, 0, 2, id="charge"),
        pytest.param("multiplicity", 3, 1, 3, id="multiplicity"),
    ],
)
def test_conflicting_override_takes_effect_with_matching_source(
    field: str,
    override: int,
    structure_value: int,
    override_value: int,
) -> None:
    """An explicit override wins, and value/source never contradict each other."""
    effective, _ = resolve_scientific_parameters(
        structure=structure("s1", charge=0, multiplicity=1),
        overrides={field: override},
        defaults=ScientificDefaults(),
    )
    assert getattr(effective, field) == override_value
    assert getattr(effective, f"{field}_source") == "step_override"


def test_matching_override_emits_no_diagnostic() -> None:
    """An override equal to the structure value is not a conflict."""
    effective, diagnostics = resolve_scientific_parameters(
        structure=structure("s1", charge=0, multiplicity=1),
        overrides={"charge": 0, "multiplicity": 1},
        defaults=ScientificDefaults(charge=2, multiplicity=3),
    )
    assert diagnostics == ()
    assert (effective.charge, effective.multiplicity) == (0, 1)


def test_electron_parity_accepts_water_singlet() -> None:
    """H2O with charge 0 and multiplicity 1 is chemically consistent."""
    assert check_electron_parity(structure("s1", charge=0, multiplicity=1), 0, 1) is None


def test_electron_parity_flags_wrong_spin_parity() -> None:
    """An even electron count cannot carry an even multiplicity."""
    diagnostic = check_electron_parity(structure("s1", charge=0, multiplicity=1), 0, 2)
    assert diagnostic is not None
    assert diagnostic.is_error is True
    assert reasons([diagnostic]) == ["electron_parity_mismatch"]
    assert diagnostic.details["electrons"] == 10


def test_electron_parity_flags_negative_electron_count() -> None:
    """A charge that removes more electrons than exist is impossible."""
    diagnostic = check_electron_parity(structure("s1", charge=0, multiplicity=1), 11, 1)
    assert diagnostic is not None
    assert diagnostic.is_error is True
    assert reasons([diagnostic]) == ["electron_parity_mismatch"]
    assert diagnostic.details["electrons"] == -1


def test_electron_parity_returns_none_for_unknown_element() -> None:
    """Unknown symbols short-circuit instead of crashing."""
    record = structure("s1")
    object.__setattr__(record, "atoms", ("Xx", "H", "H"))
    assert check_electron_parity(record, 0, 1) is None


def test_electron_parity_needs_both_values() -> None:
    """Parity is only checkable when both charge and multiplicity are known."""
    record = structure("s1", charge=0, multiplicity=1)
    assert check_electron_parity(record, None, 1) is None
    assert check_electron_parity(record, 0, None) is None


def test_resolve_propagates_parity_diagnostics() -> None:
    """Resolved effective values are parity-checked before reuse."""
    _, diagnostics = resolve_scientific_parameters(
        structure=structure("s1", charge=None, multiplicity=1),
        overrides={"charge": 11},
        defaults=ScientificDefaults(),
    )
    assert codes(diagnostics) == ["scientific_parameter_conflict"]
    assert reasons(diagnostics) == ["electron_parity_mismatch"]
