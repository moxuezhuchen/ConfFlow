#!/usr/bin/env python3

"""N3 energy-window / lowest-N / imaginary-frequency selection for filters.

Reads only the explicitly bound calculation step's ``results`` port, never
earlier steps. Missing values exclude with one note each. N4 note: the
compiler may later default-bind ``results`` to the direct predecessor.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from typing import Any

from ..domain.errors import DomainError
from ..domain.result import ResultSet
from ..domain.structure import StructureRecord

__all__ = [
    "DEFAULT_IMAGINARY_THRESHOLD_CM1",
    "ENERGY_KEY_TO_RESULT_KIND",
    "ENERGY_PARAM_KEYS",
    "HARTREE_TO_KCAL_MOL",
    "parse_energy_params",
    "select_by_energy",
    "uses_energy_params",
]

#: kcal/mol per Hartree for the energy-window comparison.
HARTREE_TO_KCAL_MOL = 627.5094740631

#: Default imaginary threshold: any negative frequency is an imaginary mode.
DEFAULT_IMAGINARY_THRESHOLD_CM1 = 0.0

#: ``energy_key`` values mapped to the bound step's result kinds.
ENERGY_KEY_TO_RESULT_KIND = {"electronic": "energy", "gibbs": "gibbs_energy"}

#: Native keys that require a bound ``results`` port on a filter step.
ENERGY_PARAM_KEYS = frozenset(
    {
        "energy_key",
        "energy_window_kcal",
        "lowest_n",
        "max_imaginary_count",
        "imaginary_threshold_cm1",
    }
)


def uses_energy_params(native: Mapping[str, Any]) -> bool:
    """Return whether *native* declares any energy/frequency selection."""
    return any(key in native for key in ENERGY_PARAM_KEYS)


def _check_param(name: str, value: Any, *, integer: bool, minimum: float | None) -> None:
    """Raise DomainError unless *value* is a finite number (int) above *minimum*."""
    ok = (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(value)
        and (not integer or isinstance(value, int))
        and (minimum is None or value >= minimum)
    )
    if not ok:
        bound = "" if minimum is None else f" >= {minimum}"
        kind = "an integer" if integer else "a finite number"
        raise DomainError(f"filter {name} must be {kind}{bound}, got {value!r}")


def parse_energy_params(native: Mapping[str, Any]) -> dict[str, Any]:
    """Parse and validate N3 selection params, raising DomainError."""
    key = native.get("energy_key")
    if key is not None and key not in ENERGY_KEY_TO_RESULT_KIND:
        raise DomainError(
            f"filter energy_key must be one of {sorted(ENERGY_KEY_TO_RESULT_KIND)}, got {key!r}"
        )
    specs = (
        ("energy_window_kcal", False, 0),
        ("lowest_n", True, 0),
        ("max_imaginary_count", True, 0),
    )
    params: dict[str, Any] = {"energy_key": key}
    for name, integer, minimum in specs:
        value = native.get(name)
        if value is not None:
            _check_param(name, value, integer=integer, minimum=minimum)
        params[name] = value
    threshold = native.get("imaginary_threshold_cm1", DEFAULT_IMAGINARY_THRESHOLD_CM1)
    if threshold is not None:
        _check_param("imaginary_threshold_cm1", threshold, integer=False, minimum=None)
    params["imaginary_threshold_cm1"] = (
        float(threshold) if threshold is not None else DEFAULT_IMAGINARY_THRESHOLD_CM1
    )
    return params


def _as_float(value: Any) -> float | None:
    """Coerce a result value to a finite float, or None when unusable."""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def select_by_energy(
    records: Sequence[StructureRecord],
    results: ResultSet,
    params: Mapping[str, Any],
) -> tuple[list[StructureRecord], list[str]]:
    """Apply N3 selection to *records* using bound-step *results*."""
    ordered = sorted(records, key=lambda record: record.id)
    notes: list[str] = []
    key = params["energy_key"] or "electronic"
    kind = ENERGY_KEY_TO_RESULT_KIND[key]
    threshold = float(params["imaginary_threshold_cm1"])
    survivors = list(ordered)
    limit = params["max_imaginary_count"]
    if limit is not None:
        kept: list[StructureRecord] = []
        for record in survivors:
            match = results.first("frequencies", record.id)
            modes = (
                [_as_float(item) for item in match.value]
                if match is not None and isinstance(match.value, (list, tuple))
                else None
            )
            if modes is None or any(mode is None for mode in modes):
                notes.append(f"dropped {record.id}: no frequencies from the bound step")
                continue
            count = sum(1 for mode in modes if mode is not None and mode < threshold)
            if count > limit:
                notes.append(f"dropped {record.id}: {count} imaginary modes exceed max {limit}")
                continue
            kept.append(record)
        survivors = kept
    if params["energy_window_kcal"] is not None or params["lowest_n"] is not None:
        ranked: list[tuple[str, float]] = []
        for record in survivors:
            match = results.first(kind, record.id)
            energy = _as_float(match.value) if match is not None else None
            if energy is None:
                notes.append(f"dropped {record.id}: no {key} energy from the bound step")
            else:
                ranked.append((record.id, energy))
        if params["energy_window_kcal"] is not None and ranked:
            floor = min(energy for _, energy in ranked)
            kept_ids = {
                struct_id
                for struct_id, energy in ranked
                if (energy - floor) * HARTREE_TO_KCAL_MOL <= params["energy_window_kcal"]
            }
            for struct_id, energy in ranked:
                if struct_id not in kept_ids:
                    above = (energy - floor) * HARTREE_TO_KCAL_MOL
                    notes.append(f"dropped {struct_id}: +{above:.4f} kcal/mol, window exceeded")
            ranked = [(i, e) for i, e in ranked if i in kept_ids]
        if params["lowest_n"] is not None:
            ranked.sort(key=lambda item: (item[1], item[0]))
            keep = {struct_id for struct_id, _ in ranked[: params["lowest_n"]]}
            for position, (struct_id, _) in enumerate(ranked):
                if struct_id not in keep:
                    notes.append(f"dropped {struct_id}: ranked #{position + 1} by {key} energy")
            ranked = [(i, e) for i, e in ranked if i in keep]
        keep_ids = {struct_id for struct_id, _ in ranked}
        survivors = [record for record in survivors if record.id in keep_ids]
    return survivors, notes
