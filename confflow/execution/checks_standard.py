#!/usr/bin/env python3

"""V4 standard scientific checks.

Each check is a predicate over normalized ``ProfileOutput`` plus inputs;
checks never read task names, roles, keywords, or legacy config: params come
only via declared ``check_params`` with ``CHECK_DEFAULTS`` fallback. Failed
checks carry ``check_failed`` with details always containing ``check`` and
stable ``reason`` (re-coded by the executor): ``abnormal_termination``;
``geometry_missing``; ``frequencies_missing``; ``imaginary_count_mismatch``,
``frequencies_missing``, ``invalid_check_parameter``; ``rmsd_exceeded``,
``rmsd_uncomputable`` (fail closed), ``invalid_check_parameter``;
``bond_drift_exceeded``, ``bond_atoms_missing``, ``invalid_check_parameter``.
With no parsed frequency data the imaginary-count check passes only when the
expectation is zero; ``frequencies_required`` covers the requested-but-unparsed case.
"""

from __future__ import annotations

import math
from typing import Any

import numpy as np

from ..domain._immutable import FrozenDict
from ..domain.diagnostics import Diagnostic, DiagnosticSeverity
from ..domain.structure import StructureRecord
from .checks import CHECK_DEFAULTS, CheckContext, CheckOutcome, parse_bond_atom_pair
from .profile_standard import NATIVE_TERMINATION_CODE
from .profiles import GeometrySemantics

__all__ = [
    "BondDriftCheck",
    "CHECKS",
    "CHECK_FAILED_CODE",
    "FrequenciesRequiredCheck",
    "GeometryRequiredCheck",
    "ImaginaryFrequencyCountCheck",
    "MaxRmsdFromInputCheck",
    "NormalTerminationCheck",
    "bond_length_angstrom",
    "kabsch_aligned_rmsd",
    "parse_bond_atoms",
]

#: Transport diagnostic code for every check failure (see module docstring).
CHECK_FAILED_CODE = "check_failed"


def _failure(
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


def _output_structure(context: CheckContext) -> StructureRecord | None:
    """Return the single output structure, or ``None`` when absent."""
    structures = context.profile_output.structures
    if structures.is_empty:
        return None
    return structures[0]


def _coords_array(record: StructureRecord) -> np.ndarray | None:
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


def kabsch_aligned_rmsd(first: np.ndarray, second: np.ndarray) -> float | None:
    """Return the reflection-free Kabsch-aligned RMSD of two ``(N, 3)`` arrays.

    Parameters
    ----------
    first : np.ndarray
        Moving coordinate set.
    second : np.ndarray
        Fixed coordinate set.

    Returns
    -------
    float | None
        Aligned RMSD in Angstrom, or ``None`` when the inputs are
        mismatched, empty, or non-finite (fail closed).
    """
    if first.shape != second.shape or first.shape[0] == 0:
        return None
    if not np.all(np.isfinite(first)) or not np.all(np.isfinite(second)):
        return None
    first_centered = first - first.mean(axis=0)
    second_centered = second - second.mean(axis=0)
    covariance = first_centered.T @ second_centered
    left, _singular, right_transposed = np.linalg.svd(covariance)
    correction = np.eye(3)
    correction[-1, -1] = 1.0 if float(np.linalg.det(left @ right_transposed)) >= 0.0 else -1.0
    aligned = first_centered @ (left @ correction @ right_transposed)
    return float(np.sqrt(np.mean(np.sum((second_centered - aligned) ** 2, axis=1))))


def bond_length_angstrom(record: StructureRecord, atom_a: int, atom_b: int) -> float | None:
    """Return the ``atom_a``-``atom_b`` distance in Angstrom (1-based indices)."""
    count = len(record.coordinates)
    if atom_a < 1 or atom_b < 1 or atom_a > count or atom_b > count or atom_a == atom_b:
        return None
    first = record.coordinates[atom_a - 1]
    second = record.coordinates[atom_b - 1]
    delta = [float(a) - float(b) for a, b in zip(first, second)]
    distance = math.sqrt(sum(component * component for component in delta))
    if not math.isfinite(distance):
        return None
    return distance


def parse_bond_atoms(value: Any) -> tuple[int, int] | None:
    """Parse a bond atom pair (1-based indices) from check parameters."""
    return parse_bond_atom_pair(value)


def _float_param(value: Any) -> float | None:
    """Coerce a threshold parameter to a positive float, or ``None``."""
    try:
        number = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number) or number < 0.0:
        return None
    return number


class NormalTerminationCheck:
    """Require the native program to report normal termination."""

    @property
    def name(self) -> str:
        """Return the check name as declared in workflow documents."""
        return "normal_termination"

    @property
    def contract_version(self) -> str:
        """Return the check contract version (folded into digests)."""
        return "confflow.contract.check.normal_termination.v1"

    def run(self, context: CheckContext) -> CheckOutcome:
        """Pass iff the profile's termination diagnostic reports success."""
        for diagnostic in context.profile_output.diagnostics:
            if diagnostic.code != NATIVE_TERMINATION_CODE:
                continue
            if diagnostic.details.get("terminated") is True:
                return CheckOutcome(passed=True)
            return _failure(
                context,
                "abnormal_termination",
                "Native program did not terminate normally.",
                {"terminated": diagnostic.details.get("terminated")},
            )
        return _failure(
            context,
            "abnormal_termination",
            "Normal termination could not be established: no termination fact recorded.",
        )


class GeometryRequiredCheck:
    """Require at least one produced (non-passthrough) geometry."""

    @property
    def name(self) -> str:
        """Return the check name as declared in workflow documents."""
        return "geometry_required"

    @property
    def contract_version(self) -> str:
        """Return the check contract version (folded into digests)."""
        return "confflow.contract.check.geometry_required.v1"

    def run(self, context: CheckContext) -> CheckOutcome:
        """Pass iff a produced output structure is present."""
        output = context.profile_output
        if (
            not output.structures.is_empty
            and output.geometry_semantics is GeometrySemantics.PRODUCED
        ):
            return CheckOutcome(passed=True)
        return _failure(
            context,
            "geometry_missing",
            "No produced geometry available: output is passthrough or absent.",
        )


class FrequenciesRequiredCheck:
    """Require a parsed, non-empty vibrational frequency set."""

    @property
    def name(self) -> str:
        """Return the check name as declared in workflow documents."""
        return "frequencies_required"

    @property
    def contract_version(self) -> str:
        """Return the check contract version (folded into digests)."""
        return "confflow.contract.check.frequencies_required.v1"

    def run(self, context: CheckContext) -> CheckOutcome:
        """Pass iff a non-empty ``frequencies`` result is present."""
        for result in context.profile_output.results:
            if result.kind != "frequencies":
                continue
            if isinstance(result.value, (list, tuple)) and len(result.value) > 0:
                return CheckOutcome(passed=True)
        return _failure(
            context,
            "frequencies_missing",
            "No frequency information was parsed from the native output.",
        )


class ImaginaryFrequencyCountCheck:
    """Check the imaginary-frequency count against an expectation."""

    @property
    def name(self) -> str:
        """Return the check name as declared in workflow documents."""
        return "imaginary_frequency_count"

    @property
    def contract_version(self) -> str:
        """Return the check contract version (folded into digests)."""
        return "confflow.contract.check.imaginary_frequency_count.v1"

    def run(self, context: CheckContext) -> CheckOutcome:
        """Pass iff the parsed count equals ``expected`` (default 0)."""
        default_expected = CHECK_DEFAULTS["imaginary_frequency_count"]["expected"]
        try:
            expected = int(context.param("expected", default_expected))
        except (TypeError, ValueError):
            return _failure(
                context,
                "invalid_check_parameter",
                "Check parameter 'expected' must be an integer.",
                {"parameter": "expected"},
            )
        observed_result = context.profile_output.results.first("num_imaginary_frequencies")
        if observed_result is None:
            if expected == 0:
                return CheckOutcome(passed=True)
            return _failure(
                context,
                "frequencies_missing",
                f"Expected {expected} imaginary frequencies but no frequency data "
                "was parsed from the native output.",
                {"expected": expected, "observed": None},
            )
        try:
            observed = int(observed_result.value)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            return _failure(
                context,
                "imaginary_count_mismatch",
                "Parsed imaginary-frequency count is not an integer.",
                {"expected": expected, "observed": observed_result.value},
            )
        if observed == expected:
            return CheckOutcome(passed=True)
        lowest_result = context.profile_output.results.first("lowest_frequency")
        lowest = lowest_result.value if lowest_result is not None else None
        return _failure(
            context,
            "imaginary_count_mismatch",
            f"Expected {expected} imaginary frequencies, observed {observed}.",
            {"expected": expected, "observed": observed, "lowest": lowest},
        )


class MaxRmsdFromInputCheck:
    """Check the Kabsch-aligned all-atom RMSD against the input structure."""

    @property
    def name(self) -> str:
        """Return the check name as declared in workflow documents."""
        return "max_rmsd_from_input"

    @property
    def contract_version(self) -> str:
        """Return the check contract version (folded into digests)."""
        return "confflow.contract.check.max_rmsd_from_input.v1"

    def run(self, context: CheckContext) -> CheckOutcome:
        """Pass iff the aligned RMSD is computable and within threshold."""
        default_threshold = CHECK_DEFAULTS["max_rmsd_from_input"]["threshold_angstrom"]
        threshold = _float_param(context.param("threshold_angstrom", default_threshold))
        if threshold is None:
            return _failure(
                context,
                "invalid_check_parameter",
                "Check parameter 'threshold_angstrom' must be a non-negative number.",
                {"parameter": "threshold_angstrom"},
            )
        output = _output_structure(context)
        if output is None:
            return _failure(
                context,
                "rmsd_uncomputable",
                "RMSD could not be computed: no output structure present.",
            )
        initial = _coords_array(context.inputs.structure)
        final = _coords_array(output)
        if initial is None or final is None or initial.shape != final.shape:
            return _failure(
                context,
                "rmsd_uncomputable",
                "RMSD could not be computed: coordinates unparseable or mismatched.",
            )
        rmsd = kabsch_aligned_rmsd(final, initial)
        if rmsd is None:
            return _failure(
                context,
                "rmsd_uncomputable",
                "RMSD could not be computed: non-finite coordinates.",
            )
        if rmsd > threshold:
            return _failure(
                context,
                "rmsd_exceeded",
                f"Aligned RMSD {rmsd:.3f} A exceeds threshold {threshold:.3f} A.",
                {"rmsd": rmsd, "threshold": threshold},
            )
        return CheckOutcome(passed=True)


class BondDriftCheck:
    """Check drift of a declared bond length against a threshold."""

    @property
    def name(self) -> str:
        """Return the check name as declared in workflow documents."""
        return "bond_drift"

    @property
    def contract_version(self) -> str:
        """Return the check contract version (folded into digests)."""
        return "confflow.contract.check.bond_drift.v1"

    def run(self, context: CheckContext) -> CheckOutcome:
        """Pass iff the bond drift is computable and within threshold."""
        default_threshold = CHECK_DEFAULTS["bond_drift"]["threshold_angstrom"]
        threshold = _float_param(context.param("threshold_angstrom", default_threshold))
        if threshold is None:
            return _failure(
                context,
                "invalid_check_parameter",
                "Check parameter 'threshold_angstrom' must be a non-negative number.",
                {"parameter": "threshold_angstrom"},
            )
        atoms = context.param("atoms", context.param("bond_atoms", None))
        pair = parse_bond_atoms(atoms)
        if pair is None:
            return _failure(
                context,
                "bond_atoms_missing",
                "Bond drift requires a declared atom pair ('atoms': two 1-based indices).",
            )
        atom_a, atom_b = pair
        output = _output_structure(context)
        target = output if output is not None else context.inputs.structure
        initial_length = bond_length_angstrom(context.inputs.structure, atom_a, atom_b)
        final_length = bond_length_angstrom(target, atom_a, atom_b)
        if initial_length is None or final_length is None:
            return _failure(
                context,
                "bond_atoms_missing",
                f"Bond atoms {atom_a},{atom_b} do not resolve against the structures.",
                {"atoms": [atom_a, atom_b]},
            )
        drift = abs(final_length - initial_length)
        if drift > threshold:
            return _failure(
                context,
                "bond_drift_exceeded",
                f"Critical bond drift |dR|={drift:.3f} A exceeds threshold "
                f"{threshold:.3f} A (R_initial={initial_length:.3f} A, "
                f"R_final={final_length:.3f} A).",
                {
                    "atoms": [atom_a, atom_b],
                    "observed": drift,
                    "threshold": threshold,
                    "r_initial": initial_length,
                    "r_final": final_length,
                },
            )
        return CheckOutcome(passed=True)


#: Check instances keyed by declared check name, for the executor to consume.
CHECKS: dict[str, Any] = {
    "normal_termination": NormalTerminationCheck(),
    "geometry_required": GeometryRequiredCheck(),
    "frequencies_required": FrequenciesRequiredCheck(),
    "imaginary_frequency_count": ImaginaryFrequencyCountCheck(),
    "max_rmsd_from_input": MaxRmsdFromInputCheck(),
    "bond_drift": BondDriftCheck(),
}
