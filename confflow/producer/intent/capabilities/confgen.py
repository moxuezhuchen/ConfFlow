#!/usr/bin/env python3

"""ConfGen capability handler (v4 search-only).

The confgen step has exactly one generation engine: the DG search declared
with ``schema_version: 4``. :func:`_wire_confgen` requires a typed v4 scope
and merges the step-level seed; ``schema_version: 3`` documents fail closed
with the engine-removal message shared with the workflow schema. Step-level
``overrides`` are rejected for confgen steps (the v4 card carries no
overrides member).
"""

from __future__ import annotations

import copy
from collections.abc import Mapping
from typing import Any

from ....workflow.v4.confgen_schema import REMOVED_ENGINES_MESSAGE
from ..common import _fail


def _wire_confgen(step: Mapping[str, Any], step_id: str) -> dict[str, Any]:
    native = step.get("native")
    if not isinstance(native, Mapping) or not native:
        raise _fail(
            f"step {step_id!r} requires an explicit non-empty native mapping",
            step_id=step_id,
        )
    native_dict = copy.deepcopy(dict(native))
    if "seed" in native_dict:
        raise _fail(
            f"step {step_id!r}: declare the seed at the step level, not inside native",
            step_id=step_id,
        )
    overrides = step.get("overrides", {})
    if overrides and not isinstance(overrides, Mapping):
        raise _fail(f"step {step_id!r} overrides must be a mapping", step_id=step_id)
    if overrides:
        raise _fail(
            f"step {step_id!r}: confgen steps do not accept overrides "
            "(the v4 confgen card carries no overrides member)",
            step_id=step_id,
        )
    version = native_dict.get("schema_version")
    if version == 3:
        raise _fail(f"step {step_id!r}: {REMOVED_ENGINES_MESSAGE}", step_id=step_id)
    if version != 4:
        raise _fail(
            f"step {step_id!r}: ConfGen intent requires a typed schema_version 4 scope; "
            f"got schema_version {version!r}",
            step_id=step_id,
        )
    block: dict[str, Any] = dict(native_dict)
    if step.get("seed") is not None:
        block["seed"] = step["seed"]
    return block


# Mechanical compat: old import path was ``confflow.producer.intent.compiler``.
_wire_confgen.__module__ = "confflow.producer.intent.compiler"

#: Step keys this executor cannot consume (R1 authority, verbatim old branch).
#: Old compiler branch was ``_CALC_ONLY_FIELDS | {"preset"}`` (9 keys).
#: Wire block: ``"confgen"`` (= ``fragment_keys[0]`` for confgen cards;
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
        "preset",
    }
)


def confgen_fragment(
    step: Mapping[str, Any], card: Mapping[str, Any] | dict[str, Any], step_id: str
) -> dict[str, Any]:
    """Complete capability-owned fragment for confgen cards (NEW adapter)."""
    _ = card
    return {"confgen": _wire_confgen(step, step_id)}
