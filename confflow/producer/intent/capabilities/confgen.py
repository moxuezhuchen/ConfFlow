#!/usr/bin/env python3

"""ConfGen capability handler (L1-C2 mechanical move).

Moved verbatim from :mod:`confflow.producer.intent.compiler`:
``_LEGACY_*`` constants, :func:`_legacy_paths_to_v3`, and
:func:`_wire_confgen` (2-arg form preserved verbatim for AST equality).
Only the shared light error :func:`_fail` comes from :mod:`..common`
(no back-import of ``compiler``).  Top-level imports stay light.

NEW (explicit, recorded): :func:`confgen_fragment` returns the complete
capability-owned fragment ``{"confgen": payload}``; the card shape is
accepted for signature uniformity and ignored.  Default bytes unchanged
while all dispatches flow through the descriptor.
"""

from __future__ import annotations

import copy
from collections.abc import Mapping
from typing import Any

from ..common import _fail

#: Legacy ConfGen ``native`` keys that describe a paths scope and can be
#: expressed as a typed schema_version 3 block (IS.1 equivalence golden).
_LEGACY_PATH_SCOPE_KEYS = frozenset({"paths", "angle_step", "bond_scale", "strict_path_bond_check"})

#: Keys a legacy path declaration may carry; anything else cannot be mapped.
_LEGACY_PATH_KEYS = frozenset({"start", "end", "move", "id", "angles", "step"})

#: The legacy default scan step in degrees (Q2b: bare declarations get it explicitly).
_LEGACY_DEFAULT_PATH_STEP = 120


def _legacy_paths_to_v3(native: Mapping[str, Any], step_id: str) -> dict[str, Any] | None:
    """Express a legacy paths scope as a typed ``schema_version: 3`` block.

    The mapping is the one recorded by the IS.1 equivalence golden
    (``tests/fixtures/paths_equivalence/run_equivalence.py::map_native``): a bare
    declaration gets ``step`` from ``angle_step`` or the legacy default 120,
    ``bond_scale`` becomes ``tolerances.bond_scale`` and
    ``strict_path_bond_check`` becomes the v3 top-level flag.  (Step-level
    ``paths`` / ``strict_path_bond_check`` are not intent step members, so they
    never reach this function.)

    Returns ``None`` when the native carries anything outside the paths scope
    (for example ``chains``); such a scope has no v3 form.  No endpoint is
    inspected or rewritten: a terminal-atom endpoint is compiled as written
    and refused at run time by v3.
    """
    scope = dict(native)
    if not set(scope) <= _LEGACY_PATH_SCOPE_KEYS or "paths" not in scope:
        return None
    declarations = scope["paths"]
    if not isinstance(declarations, list) or not declarations:
        raise _fail(f"step {step_id!r}: paths must be a non-empty list", step_id=step_id)
    v3: dict[str, Any] = {"schema_version": 3, "index_base": 1, "paths": []}
    if "strict_path_bond_check" in scope:
        v3["strict_path_bond_check"] = scope["strict_path_bond_check"]
    if "bond_scale" in scope:
        v3["tolerances"] = {"bond_scale": scope["bond_scale"]}
    default_step = scope.get("angle_step", _LEGACY_DEFAULT_PATH_STEP)
    for index, declaration in enumerate(declarations):
        if not isinstance(declaration, Mapping):
            raise _fail(f"step {step_id!r}: paths[{index}] must be a mapping", step_id=step_id)
        unknown = sorted(set(declaration) - _LEGACY_PATH_KEYS)
        if unknown:
            hint = (
                " (waypoint paths have no typed v3 form; use explicit torsions declarations instead)"
                if "waypoint" in unknown
                else ""
            )
            raise _fail(
                f"step {step_id!r}: paths[{index}] carries unsupported keys: "
                f"{', '.join(unknown)}{hint}",
                step_id=step_id,
            )
        entry: dict[str, Any] = {
            key: copy.deepcopy(declaration[key])
            for key in ("start", "end", "move", "id")
            if key in declaration
        }
        if "angles" in declaration:
            entry["angles"] = copy.deepcopy(declaration["angles"])
        elif "step" in declaration:
            entry["step"] = declaration["step"]
        else:
            entry["step"] = default_step
        v3["paths"].append(entry)
    return v3


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
    if (
        isinstance(native_dict.get("schema_version"), int)
        and native_dict.get("schema_version") == 3
    ):
        block: dict[str, Any] = dict(native_dict)
        if step.get("seed") is not None:
            block["seed"] = step["seed"]
        if overrides:
            block["overrides"] = copy.deepcopy(dict(overrides))
        return block
    mapped = _legacy_paths_to_v3(native_dict, step_id)
    if mapped is not None:
        if step.get("seed") is not None:
            mapped["seed"] = step["seed"]
        if overrides:
            mapped["overrides"] = copy.deepcopy(dict(overrides))
        return mapped
    raise _fail(
        f"step {step_id!r}: ConfGen intent requires a typed schema_version 3 scope; "
        "the legacy native vocabulary "
        f"({', '.join(sorted(native_dict))}) has no typed form here",
        step_id=step_id,
    )


# Mechanical compat: old import path was ``confflow.producer.intent.compiler``.
_legacy_paths_to_v3.__module__ = "confflow.producer.intent.compiler"
_wire_confgen.__module__ = "confflow.producer.intent.compiler"


def confgen_fragment(
    step: Mapping[str, Any], card: Mapping[str, Any] | dict[str, Any], step_id: str
) -> dict[str, Any]:
    """Complete capability-owned fragment for confgen cards (NEW adapter).

    Wraps :func:`_wire_confgen` as ``{"confgen": payload}``; the card shape
    is accepted for signature uniformity and ignored.  Declared fragment
    keys: ``("confgen",)``.
    """
    _ = card
    return {"confgen": _wire_confgen(step, step_id)}
