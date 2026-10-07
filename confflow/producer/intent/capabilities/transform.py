#!/usr/bin/env python3

"""Transform capability handler (L1-C2 mechanical move).

Moved verbatim from :mod:`confflow.producer.intent.compiler`:
:func:`_wire_transform`.  Body, exception order, messages, and wiring
semantics are unchanged.  Only the shared light error :func:`_fail` comes
from :mod:`..common` (no back-import of ``compiler``).  Top-level imports
stay light (``copy``/``collections.abc``/``typing`` + ``..common`` +
``..presets`` constants); the heavy ``execution.transform_executor``
knowledge stays function-local lazy, exactly as before.
"""

from __future__ import annotations

import copy
from collections.abc import Mapping
from typing import Any

from ...presets import get_preset, parse_preset_ref
from ..common import _fail


def _wire_transform(step: Mapping[str, Any], card: dict[str, Any], step_id: str) -> dict[str, Any]:
    kind = card.get("transform_kind")
    if not kind:
        raise _fail(f"step {step_id!r}: transform card lacks a kind", step_id=step_id)
    preset_native: dict[str, Any] = {}
    preset_name: str | None = None
    if step.get("preset") is not None:
        try:
            preset_name, _ = parse_preset_ref(step["preset"])
        except ValueError as exc:
            raise _fail(str(exc), step_id=step_id) from exc
        preset = get_preset(preset_name)
        if preset["card"] != kind:
            raise _fail(
                f"step {step_id!r}: preset {preset_name!r} serves {preset['card']!r}, "
                f"not card kind {kind!r}",
                step_id=step_id,
            )
        preset_native = copy.deepcopy(preset["native"])
    else:
        preset_name = "refine_default" if kind == "refine" else "dedup_default"
        preset_native = copy.deepcopy(get_preset(preset_name)["native"])
    native = step.get("native", {})
    if native is None:
        native = {}
    if not isinstance(native, Mapping):
        raise _fail(f"step {step_id!r} native must be a mapping", step_id=step_id)
    if kind == "deduplicate" and native:
        raise _fail(
            f"step {step_id!r}: deduplicate takes no native parameters, got {sorted(native)}",
            step_id=step_id,
        )
    merged = dict(preset_native)
    for key, value in dict(native).items():
        merged[key] = copy.deepcopy(value)
    if "energy_window" in merged:
        raise _fail(
            f"step {step_id!r}: 'energy_window' is not a V4 transform member and is omitted",
            step_id=step_id,
        )
    try:
        from ....execution.transform_executor import REFINE_NATIVE_KEYS
    except ImportError:
        allowed_keys = frozenset(
            {"rmsd_threshold_angstrom", "bond_scale", "heavy_only", "max_structures"}
        )
    else:
        allowed_keys = REFINE_NATIVE_KEYS
    if kind == "refine":
        unknown = sorted(set(merged) - set(allowed_keys))
        if unknown:
            raise _fail(
                f"step {step_id!r} carries unknown refine native keys: {', '.join(unknown)}",
                step_id=step_id,
            )
    return {"kind": kind, "native": merged, "_preset": preset_name}


# Mechanical compat: old import path was ``confflow.producer.intent.compiler``.
_wire_transform.__module__ = "confflow.producer.intent.compiler"

#: Step keys this executor cannot consume (R1 authority, verbatim old branch).
#: Old compiler branch was ``_CALC_ONLY_FIELDS | {"program", "seed"}`` (9 keys,
#: note no ``preset`` -- preset is a transform-owned key).  Equals the 8
#: calc-only fields plus ``seed`` (``program`` already in the 8).
#: Wire block: ``"transform"`` (= ``fragment_keys[0]`` for transform cards;
#: compiler derives via the descriptor effective key, no executor branch here).
REJECTED_STEP_KEYS: frozenset = frozenset(
    {
        "program",
        "role",
        "adapter",
        "profile",
        "checks",
        "check_params",
        "recovery",
        "recovery_params",
        "seed",
    }
)


def transform_fragment(
    step: Mapping[str, Any], card: dict[str, Any] | Mapping[str, Any], step_id: str
) -> dict[str, Any]:
    """Complete capability-owned fragment for transform cards (NEW adapter)."""
    raw = _wire_transform(step, card, step_id)
    preset_name = str(raw.pop("_preset"))
    return {
        "transform": {"kind": raw["kind"], "native": raw["native"]},
        "_preset_ref": preset_name,
    }
