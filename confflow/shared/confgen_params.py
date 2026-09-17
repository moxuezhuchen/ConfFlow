#!/usr/bin/env python3

"""Canonical ConfGen parameter resolution shared across layers.

Execution, validation, fingerprinting, and ``config-show`` must agree on how a
confgen step's parameters are interpreted (defaults, legacy aliases, types).
This module is the single source of that interpretation.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from ..core.exceptions import ConfigurationError
from ..core.pairs import normalize_pair_list

__all__ = [
    "CONFGEN_ALIAS_GROUPS",
    "DEFAULT_CONFGEN_ANGLE_STEP",
    "DEFAULT_CONFGEN_BOND_THRESHOLD",
    "DEFAULT_CONFGEN_CLASH_THRESHOLD",
    "DEFAULT_CONFGEN_ROTATE_SIDE",
    "confgen_known_keys",
    "resolve_confgen_params",
]

DEFAULT_CONFGEN_ANGLE_STEP = 120
DEFAULT_CONFGEN_BOND_THRESHOLD = 1.15
DEFAULT_CONFGEN_CLASH_THRESHOLD = 0.65
DEFAULT_CONFGEN_ROTATE_SIDE = "left"

#: canonical key -> accepted aliases (canonical key included).
CONFGEN_ALIAS_GROUPS: dict[str, tuple[str, ...]] = {
    "bond_threshold": ("bond_threshold", "bond_multiplier"),
    "chains": ("chains", "chain"),
    "chain_steps": ("chain_steps", "steps"),
    "chain_angles": ("chain_angles", "angles"),
    "workers": ("workers", "max_workers", "max_parallel_jobs"),
}

#: canonical keys that are not aliases of another key.
CONFGEN_SIMPLE_KEYS: tuple[str, ...] = (
    "angle_step",
    "clash_threshold",
    "add_bond",
    "del_bond",
    "no_rotate",
    "force_rotate",
    "optimize",
    "rotate_side",
)

_MISSING = object()


def confgen_known_keys() -> frozenset[str]:
    """Return every parameter name recognized by the resolver."""
    keys: set[str] = set(CONFGEN_SIMPLE_KEYS)
    for aliases in CONFGEN_ALIAS_GROUPS.values():
        keys.update(aliases)
    return frozenset(keys)


def _as_list(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, list):
        return value
    return [value]


def _values_conflict(left: Any, right: Any) -> bool:
    if left is right:
        return False
    if isinstance(left, (int, float)) and isinstance(right, (int, float)):
        return float(left) != float(right)
    return left != right


def _select_alias(
    params: Mapping[str, Any], canonical: str, aliases: tuple[str, ...]
) -> Any:
    present = [key for key in aliases if key in params and params[key] is not None]
    if not present:
        return _MISSING
    first = present[0]
    for key in present[1:]:
        if _values_conflict(params[first], params[key]):
            rendered = ", ".join(f"{name}={params[name]!r}" for name in present)
            raise ConfigurationError(
                f"confgen params specify conflicting values for '{canonical}': {rendered}"
            )
    return params[first]


def _coerce_float(value: Any, name: str) -> float:
    try:
        return float(value)
    except (TypeError, ValueError) as exc:
        raise ConfigurationError(f"confgen {name} must be a number, got {value!r}") from exc


def _coerce_int(value: Any, name: str) -> int:
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise ConfigurationError(f"confgen {name} must be an integer, got {value!r}") from exc


def resolve_confgen_params(
    params: Mapping[str, Any],
    *,
    default_workers: int,
) -> dict[str, Any]:
    """Return canonical execution parameters for a confgen step.

    Handles defaults, legacy aliases, minimal type normalization, and rejects
    conflicting alias values instead of silently choosing a precedence.
    """
    params = params or {}

    angle_step = _coerce_int(
        params.get("angle_step", DEFAULT_CONFGEN_ANGLE_STEP), "angle_step"
    )

    bond_threshold_raw = _select_alias(
        params, "bond_threshold", CONFGEN_ALIAS_GROUPS["bond_threshold"]
    )
    bond_threshold = _coerce_float(
        DEFAULT_CONFGEN_BOND_THRESHOLD
        if bond_threshold_raw is _MISSING
        else bond_threshold_raw,
        "bond_threshold",
    )

    clash_threshold = _coerce_float(
        params.get("clash_threshold", DEFAULT_CONFGEN_CLASH_THRESHOLD), "clash_threshold"
    )

    workers_raw = _select_alias(params, "workers", CONFGEN_ALIAS_GROUPS["workers"])
    workers = _coerce_int(default_workers if workers_raw is _MISSING else workers_raw, "workers")
    if workers < 1:
        raise ConfigurationError(f"confgen workers must be an integer >= 1, got {workers!r}")

    chains_raw = _select_alias(params, "chains", CONFGEN_ALIAS_GROUPS["chains"])
    chain_steps_raw = _select_alias(
        params, "chain_steps", CONFGEN_ALIAS_GROUPS["chain_steps"]
    )
    chain_angles_raw = _select_alias(
        params, "chain_angles", CONFGEN_ALIAS_GROUPS["chain_angles"]
    )

    return {
        "angle_step": angle_step,
        "bond_threshold": bond_threshold,
        "clash_threshold": clash_threshold,
        "add_bond": normalize_pair_list(params.get("add_bond")),
        "del_bond": normalize_pair_list(params.get("del_bond")),
        "no_rotate": normalize_pair_list(params.get("no_rotate")),
        "force_rotate": normalize_pair_list(params.get("force_rotate")),
        "optimize": params.get("optimize", False),
        "chains": _as_list(None if chains_raw is _MISSING else chains_raw),
        "chain_steps": _as_list(None if chain_steps_raw is _MISSING else chain_steps_raw),
        "chain_angles": _as_list(None if chain_angles_raw is _MISSING else chain_angles_raw),
        "rotate_side": params.get("rotate_side", DEFAULT_CONFGEN_ROTATE_SIDE),
        "workers": workers,
    }
