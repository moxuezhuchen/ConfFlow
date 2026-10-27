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

import re
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

import numpy as np

from ..domain._immutable import FrozenDict
from ..domain.diagnostics import Diagnostic, DiagnosticSeverity
from ..domain.structure import StructureRecord
from .native import ResolvedCalculationInputs
from .profiles import ProfileOutput

__all__ = [
    "CHECK_DEFAULTS",
    "CHECK_FAILED_CODE",
    "CheckContext",
    "CheckOutcome",
    "ScientificCheck",
    "check_failure",
    "coords_array",
    "output_structure",
    "parse_bond_atom_pair",
    "strict_pair",
    "strict_pairs",
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
    "distance_window": {},
    "graph_unchanged": {"bond_scale": 1.15, "ignore_pairs": []},
    "ts_mode": {"min_overlap": 0.2},
}


def parse_bond_atom_pair(value: Any) -> tuple[int, int] | None:
    """Parse a bond atom pair (1-based indices) from step parameters."""
    if value is None:
        return None
    numbers: list[int] = []
    if isinstance(value, (list, tuple)):
        for item in value:
            try:
                numbers.append(int(item))
            except (TypeError, ValueError):
                continue
    else:
        for match in re.findall(r"\d+", str(value)):
            try:
                numbers.append(int(match))
            except (TypeError, ValueError):
                continue
    if len(numbers) < 2:
        return None
    first, second = numbers[0], numbers[1]
    if first <= 0 or second <= 0 or first == second:
        return None
    return first, second


#: Transport diagnostic code for every scientific check failure.
CHECK_FAILED_CODE = "check_failed"


def check_failure(
    context: CheckContext,
    reason: str,
    message: str,
    extra: dict[str, Any] | None = None,
) -> CheckOutcome:
    """Build a failed outcome with the executor mapping contract."""
    details: dict[str, Any] = {"check": context.check_name, "reason": reason}
    if extra:
        details.update(extra)
    return CheckOutcome(
        passed=False,
        diagnostic=Diagnostic(
            code=CHECK_FAILED_CODE,
            message=message,
            severity=DiagnosticSeverity.ERROR,
            step_id=context.step_id,
            work_item_id=context.work_item_id,
            logical_key=context.logical_key,
            details=FrozenDict(details),
        ),
    )


def output_structure(context: CheckContext) -> StructureRecord | None:
    """Return the single output structure, or ``None`` when absent."""
    structures = context.profile_output.structures
    if structures.is_empty:
        return None
    return structures[0]


def coords_array(record: StructureRecord) -> np.ndarray | None:
    """Return an ``(N, 3)`` coordinate array, or ``None`` when unusable."""
    try:
        array = np.array(record.coordinates, dtype=float)
    except (TypeError, ValueError):
        return None
    if array.ndim != 2 or array.shape[1] != 3 or array.shape[0] == 0:
        return None
    if not np.all(np.isfinite(array)):
        return None
    return array


def strict_pair(value: Any) -> tuple[int, int] | None:
    """Parse a strict ``[a, b]`` pair of distinct positive 1-based integers."""
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        return None
    if any(isinstance(item, bool) or not isinstance(item, int) for item in value):
        return None
    first, second = int(value[0]), int(value[1])
    if first <= 0 or second <= 0 or first == second:
        return None
    return first, second


def strict_pairs(value: Any) -> list[tuple[int, int]] | None:
    """Parse a non-empty list of strict ``[a, b]`` pairs, or ``None``."""
    if not isinstance(value, (list, tuple)) or not value:
        return None
    pairs: list[tuple[int, int]] = []
    for item in value:
        pair = strict_pair(item)
        if pair is None:
            return None
        pairs.append(pair)
    return pairs
