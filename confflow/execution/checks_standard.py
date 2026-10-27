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
``bond_drift_exceeded``, ``bond_atoms_missing``, ``invalid_check_parameter``;
``distance_out_of_window``, ``bond_atoms_missing``, ``invalid_check_parameter``;
``graph_changed``, ``graph_uncomputable``, ``invalid_check_parameter``; ``ts_mode``
failures are documented in :mod:`checks_ts`.
With no parsed frequency data the imaginary-count check passes only when the
expectation is zero; ``frequencies_required`` covers the requested-but-unparsed case.
"""

from __future__ import annotations

import math
from typing import Any

import numpy as np

from ..domain.elements import atomic_number
from ..domain.errors import DomainError
from ..domain.structure import StructureRecord
from ..science.bonds import perceive_adjacency
from .checks import (
    CHECK_DEFAULTS,
    CHECK_FAILED_CODE,
    CheckContext,
    CheckOutcome,
    check_failure,
    coords_array,
    output_structure,
    parse_bond_atom_pair,
    strict_pair,
)
from .checks_ts import TsModeCheck
from .profile_standard import NATIVE_TERMINATION_CODE
from .profiles import GeometrySemantics

__all__ = [
    "BondDriftCheck",
    "CHECKS",
    "DistanceWindowCheck",
    "GraphUnchangedCheck",
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
            return check_failure(
                context,
                "abnormal_termination",
                "Native program did not terminate normally.",
                {"terminated": diagnostic.details.get("terminated")},
            )
        return check_failure(
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
        return check_failure(
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
        return check_failure(
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
            return check_failure(
                context,
                "invalid_check_parameter",
                "Check parameter 'expected' must be an integer.",
                {"parameter": "expected"},
            )
        observed_result = context.profile_output.results.first("num_imaginary_frequencies")
        if observed_result is None:
            if expected == 0:
                return CheckOutcome(passed=True)
            return check_failure(
                context,
                "frequencies_missing",
                f"Expected {expected} imaginary frequencies but no frequency data "
                "was parsed from the native output.",
                {"expected": expected, "observed": None},
            )
        try:
            observed = int(observed_result.value)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            return check_failure(
                context,
                "imaginary_count_mismatch",
                "Parsed imaginary-frequency count is not an integer.",
                {"expected": expected, "observed": observed_result.value},
            )
        if observed == expected:
            return CheckOutcome(passed=True)
        lowest_result = context.profile_output.results.first("lowest_frequency")
        lowest = lowest_result.value if lowest_result is not None else None
        return check_failure(
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
            return check_failure(
                context,
                "invalid_check_parameter",
                "Check parameter 'threshold_angstrom' must be a non-negative number.",
                {"parameter": "threshold_angstrom"},
            )
        output = output_structure(context)
        if output is None:
            return check_failure(
                context,
                "rmsd_uncomputable",
                "RMSD could not be computed: no output structure present.",
            )
        initial = coords_array(context.inputs.structure)
        final = coords_array(output)
        if initial is None or final is None or initial.shape != final.shape:
            return check_failure(
                context,
                "rmsd_uncomputable",
                "RMSD could not be computed: coordinates unparseable or mismatched.",
            )
        rmsd = kabsch_aligned_rmsd(final, initial)
        if rmsd is None:
            return check_failure(
                context,
                "rmsd_uncomputable",
                "RMSD could not be computed: non-finite coordinates.",
            )
        if rmsd > threshold:
            return check_failure(
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
            return check_failure(
                context,
                "invalid_check_parameter",
                "Check parameter 'threshold_angstrom' must be a non-negative number.",
                {"parameter": "threshold_angstrom"},
            )
        atoms = context.param("atoms", context.param("bond_atoms", None))
        pair = parse_bond_atoms(atoms)
        if pair is None:
            return check_failure(
                context,
                "bond_atoms_missing",
                "Bond drift requires a declared atom pair ('atoms': two 1-based indices).",
            )
        atom_a, atom_b = pair
        output = output_structure(context)
        target = output if output is not None else context.inputs.structure
        initial_length = bond_length_angstrom(context.inputs.structure, atom_a, atom_b)
        final_length = bond_length_angstrom(target, atom_a, atom_b)
        if initial_length is None or final_length is None:
            return check_failure(
                context,
                "bond_atoms_missing",
                f"Bond atoms {atom_a},{atom_b} do not resolve against the structures.",
                {"atoms": [atom_a, atom_b]},
            )
        drift = abs(final_length - initial_length)
        if drift > threshold:
            return check_failure(
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


def _finite_float(value: Any) -> float | None:
    """Coerce a plain (non-bool) numeric parameter to a finite float, or ``None``."""
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _parse_distance_windows(value: Any) -> list[tuple[int, int, float, float]] | None:
    """Parse ``distances`` as a non-empty list of ``[a, b, lo, hi]`` entries."""
    if not isinstance(value, (list, tuple)) or len(value) == 0:
        return None
    windows: list[tuple[int, int, float, float]] = []
    for entry in value:
        if not isinstance(entry, (list, tuple)) or len(entry) != 4:
            return None
        pair = strict_pair(list(entry[:2]))
        lo = _finite_float(entry[2])
        hi = _finite_float(entry[3])
        if pair is None or lo is None or hi is None or lo < 0.0 or hi < lo:
            return None
        windows.append((pair[0], pair[1], lo, hi))
    return windows


class DistanceWindowCheck:
    """Require declared interatomic distances to lie inside closed windows."""

    @property
    def name(self) -> str:
        """Return the check name as declared in workflow documents."""
        return "distance_window"

    @property
    def contract_version(self) -> str:
        """Return the check contract version (folded into digests)."""
        return "confflow.contract.check.distance_window.v1"

    def run(self, context: CheckContext) -> CheckOutcome:
        """Pass iff every declared distance is computable and inside its window."""
        windows = _parse_distance_windows(context.param("distances", None))
        if windows is None:
            return check_failure(
                context,
                "invalid_check_parameter",
                "Check parameter 'distances' must be a non-empty list of "
                "[a, b, lo, hi] with distinct 1-based atoms and 0 <= lo <= hi (Angstrom).",
                {"parameter": "distances"},
            )
        output = output_structure(context)
        target = output if output is not None else context.inputs.structure
        entries: list[dict[str, Any]] = []
        unresolved: list[list[int]] = []
        for atom_a, atom_b, lo, hi in windows:
            observed = bond_length_angstrom(target, atom_a, atom_b)
            if observed is None:
                unresolved.append([atom_a, atom_b])
                continue
            entries.append(
                {
                    "atoms": [atom_a, atom_b],
                    "observed": observed,
                    "window": [lo, hi],
                    "within": lo <= observed <= hi,
                }
            )
        if unresolved:
            return check_failure(
                context,
                "bond_atoms_missing",
                f"Distance atoms {unresolved} do not resolve against the structure.",
                {"atoms": unresolved},
            )
        violations = [entry for entry in entries if not entry["within"]]
        if violations:
            listing = "; ".join(
                f"{entry['atoms'][0]}-{entry['atoms'][1]} = {entry['observed']:.3f} A "
                f"(window [{entry['window'][0]:.3f}, {entry['window'][1]:.3f}] A)"
                for entry in entries
            )
            return check_failure(
                context,
                "distance_out_of_window",
                f"Distance(s) outside declared window: {listing}.",
                {"distances": entries, "violations": violations},
            )
        return CheckOutcome(passed=True)


def _perceived_pairs(record: StructureRecord, bond_scale: float) -> frozenset[tuple[int, int]]:
    """Covalent bonds (0-based sorted pairs) of *record*, as ``perceive_bonds`` does."""
    numbers = [atomic_number(symbol) for symbol in record.atoms]
    adjacency = perceive_adjacency(numbers, record.coordinates, bond_scale=bond_scale)
    return frozenset(
        (min(i, j), max(i, j)) for i, row in enumerate(adjacency) for j in row if i != j
    )


class GraphUnchangedCheck:
    """Require the perceived covalent graph to match the input, bar declared pairs."""

    @property
    def name(self) -> str:
        """Return the check name as declared in workflow documents."""
        return "graph_unchanged"

    @property
    def contract_version(self) -> str:
        """Return the check contract version (folded into digests)."""
        return "confflow.contract.check.graph_unchanged.v1"

    def run(self, context: CheckContext) -> CheckOutcome:
        """Pass iff input and output graphs agree after ignoring declared pairs."""
        defaults = CHECK_DEFAULTS["graph_unchanged"]
        bond_scale = _finite_float(context.param("bond_scale", defaults["bond_scale"]))
        if bond_scale is None or bond_scale <= 0.0:
            return check_failure(
                context,
                "invalid_check_parameter",
                "Check parameter 'bond_scale' must be a positive number.",
                {"parameter": "bond_scale"},
            )
        raw_ignore = context.param("ignore_pairs", defaults["ignore_pairs"])
        if not isinstance(raw_ignore, (list, tuple)):
            return check_failure(
                context,
                "invalid_check_parameter",
                "Check parameter 'ignore_pairs' must be a list of [a, b] pairs.",
                {"parameter": "ignore_pairs"},
            )
        ignored: set[tuple[int, int]] = set()
        for entry in raw_ignore:
            pair = strict_pair(entry)
            if pair is None:
                return check_failure(
                    context,
                    "invalid_check_parameter",
                    "Check parameter 'ignore_pairs' entries must be [a, b] of "
                    "distinct positive 1-based integers.",
                    {"parameter": "ignore_pairs"},
                )
            ignored.add((min(pair) - 1, max(pair) - 1))
        output = output_structure(context)
        initial_record = context.inputs.structure
        target = output if output is not None else initial_record
        if len(initial_record.atoms) != len(target.atoms):
            return check_failure(
                context,
                "graph_uncomputable",
                "Covalent graph could not be compared: atom counts differ.",
            )
        try:
            initial = _perceived_pairs(initial_record, bond_scale) - ignored
            final = _perceived_pairs(target, bond_scale) - ignored
        except (DomainError, ValueError) as exc:
            return check_failure(
                context,
                "graph_uncomputable",
                f"Covalent graph could not be perceived: {exc}",
            )
        formed = sorted(final - initial)
        broken = sorted(initial - final)
        if not formed and not broken:
            return CheckOutcome(passed=True)
        formed_text = [f"{i + 1}-{j + 1}" for i, j in formed]
        broken_text = [f"{i + 1}-{j + 1}" for i, j in broken]
        return check_failure(
            context,
            "graph_changed",
            f"Covalent graph changed: formed {formed_text}, broken {broken_text}.",
            {
                "formed": formed_text,
                "broken": broken_text,
                "bond_scale": bond_scale,
                "ignored": sorted(f"{i + 1}-{j + 1}" for i, j in ignored),
            },
        )


#: Check instances keyed by declared check name, for the executor to consume.
CHECKS: dict[str, Any] = {
    "normal_termination": NormalTerminationCheck(),
    "geometry_required": GeometryRequiredCheck(),
    "frequencies_required": FrequenciesRequiredCheck(),
    "imaginary_frequency_count": ImaginaryFrequencyCountCheck(),
    "max_rmsd_from_input": MaxRmsdFromInputCheck(),
    "bond_drift": BondDriftCheck(),
    "distance_window": DistanceWindowCheck(),
    "graph_unchanged": GraphUnchangedCheck(),
    "ts_mode": TsModeCheck(),
}
