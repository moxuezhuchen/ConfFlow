#!/usr/bin/env python3

"""V4 scientific-check contracts.

Checks are producer-owned, explicitly declared predicates over a normalized
profile output plus the original inputs.  Each check receives its parameters
from the step's declared ``check_params``; undeclared parameters fall back to
the check descriptor's single-source defaults.  Checks never read task names,
roles, keywords, or legacy config.

Check parameter defaults live in :data:`CHECK_DEFAULTS`, the single authority
consumed by validation, documentation, and the checks themselves.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from ..domain._immutable import FrozenDict
from ..domain.diagnostics import Diagnostic
from .native import ResolvedCalculationInputs
from .profiles import ProfileOutput

__all__ = [
    "CHECK_DEFAULTS",
    "CheckContext",
    "CheckOutcome",
    "ScientificCheck",
]


@dataclass(frozen=True, slots=True)
class CheckContext:
    """Everything a scientific check may read."""

    check_name: str
    work_item_id: str
    step_id: str
    logical_key: str
    profile_output: ProfileOutput
    inputs: ResolvedCalculationInputs
    params: FrozenDict = field(default_factory=FrozenDict)

    def __post_init__(self) -> None:
        if not isinstance(self.profile_output, ProfileOutput):
            raise TypeError("profile_output must be a ProfileOutput")
        if not isinstance(self.inputs, ResolvedCalculationInputs):
            raise TypeError("inputs must be ResolvedCalculationInputs")
        if not isinstance(self.params, FrozenDict):
            object.__setattr__(self, "params", FrozenDict(self.params))

    def param(self, name: str, default: Any = None) -> Any:
        """Return the declared parameter *name*, or *default*."""
        return self.params.get(name, default)


@dataclass(frozen=True, slots=True)
class CheckOutcome:
    """The verdict of one scientific check."""

    passed: bool
    diagnostic: Diagnostic | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.passed, bool):
            raise TypeError("passed must be a boolean")
        if self.diagnostic is not None and not isinstance(self.diagnostic, Diagnostic):
            raise TypeError("diagnostic must be a Diagnostic or None")
        if not self.passed and self.diagnostic is None:
            raise ValueError("a failed check must carry a diagnostic")


@runtime_checkable
class ScientificCheck(Protocol):
    """One explicitly declared scientific acceptance predicate."""

    @property
    def name(self) -> str:
        """Return the check name as declared in workflow documents."""
        ...

    @property
    def contract_version(self) -> str:
        """Return the check contract version (folded into digests)."""
        ...

    def run(self, context: CheckContext) -> CheckOutcome:
        """Evaluate the check against normalized profile output."""
        ...


#: Single-source default parameters for the built-in checks.  Validation,
#: documentation, and check implementations all read these values; no second
#: copy may exist in production code.  Threshold defaults preserve the legacy
#: scientific policy (bond drift 0.4 A, RMSD 1.0 A); they are explicit step
#: parameters in V4, never hidden globals.
CHECK_DEFAULTS: dict[str, dict[str, Any]] = {
    "normal_termination": {},
    "geometry_required": {},
    "frequencies_required": {},
    "imaginary_frequency_count": {"expected": 0},
    "max_rmsd_from_input": {"threshold_angstrom": 1.0},
    "bond_drift": {"threshold_angstrom": 0.4},
}
