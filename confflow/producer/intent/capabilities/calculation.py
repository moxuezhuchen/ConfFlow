#!/usr/bin/env python3

"""Calculation capability handler (L1-C2 mechanical move).

Moved verbatim from :mod:`confflow.producer.intent.compiler`:
:func:`_resolve_program` and :func:`_wire_calculation`.  Bodies, exception
order, messages, and wiring semantics are unchanged.  Only the shared light
error :func:`_fail` is imported from :mod:`..common` (no back-import of
``compiler``, so no cycle).  Top-level imports stay light
(``copy``/``collections.abc``/``typing`` + ``..common``); program-registry
knowledge arrives only via the function-local lazy import inside
:func:`_resolve_program`, exactly as before.
"""

from __future__ import annotations

import copy
from collections.abc import Mapping
from typing import Any

from ..common import _fail


def _resolve_program(program: Any, *, step_id: str) -> str:
    from ....programs.registry import get_program_adapter

    if not isinstance(program, str) or not program.strip():
        raise _fail(
            f"step {step_id!r} requires an explicit program",
            step_id=step_id,
        )
    try:
        adapter = get_program_adapter(program)
    except Exception as exc:
        raise _fail(
            f"step {step_id!r} names unknown program {program!r}: {exc}",
            step_id=step_id,
        ) from exc
    canonical = adapter.program_name.value
    return canonical if isinstance(canonical, str) else str(program).strip().lower()


def _wire_calculation(
    step: Mapping[str, Any], card: Mapping[str, Any], step_id: str
) -> dict[str, Any]:
    native = step.get("native")
    if not isinstance(native, Mapping) or not native:
        raise _fail(
            f"step {step_id!r} requires an explicit non-empty native mapping",
            step_id=step_id,
        )
    program_raw = step.get("program")
    if program_raw is None:
        raise _fail(f"step {step_id!r} requires an explicit program", step_id=step_id)
    program = _resolve_program(program_raw, step_id=step_id)
    role = step.get("role", card.get("default_role"))
    if role is not None and (not isinstance(role, str) or not role.strip()):
        raise _fail(f"step {step_id!r} role must be a non-empty string", step_id=step_id)
    calculation: dict[str, Any] = {
        "program": program,
        "execution_adapter": step.get("adapter", card["adapter"]),
        "result_profile": step.get("profile", card["profile"]),
        "native": copy.deepcopy(dict(native)),
        "checks": copy.deepcopy(step.get("checks", card["checks"])),
        "recovery": {"profile": step.get("recovery", card["recovery"])},
    }
    if role is not None:
        calculation["role"] = role
    check_params = step.get("check_params", card["check_params"])
    if check_params:
        if not isinstance(check_params, Mapping):
            raise _fail(f"step {step_id!r} check_params must be a mapping", step_id=step_id)
        calculation["check_params"] = copy.deepcopy(
            {name: dict(params) for name, params in check_params.items()}
        )
    recovery_params = step.get("recovery_params")
    if recovery_params:
        if not isinstance(recovery_params, Mapping):
            raise _fail(f"step {step_id!r} recovery_params must be a mapping", step_id=step_id)
        calculation["recovery"]["params"] = copy.deepcopy(dict(recovery_params))
    if step.get("seed") is not None:
        seed = step["seed"]
        if isinstance(seed, bool) or not isinstance(seed, int):
            raise _fail(f"step {step_id!r} seed must be an integer", step_id=step_id)
        calculation["seed"] = seed
    overrides = step.get("overrides", {})
    if overrides:
        if not isinstance(overrides, Mapping):
            raise _fail(f"step {step_id!r} overrides must be a mapping", step_id=step_id)
        unknown = sorted(set(overrides) - {"charge", "multiplicity", "freeze"})
        if unknown:
            raise _fail(
                f"step {step_id!r} carries unsupported overrides: {', '.join(unknown)}",
                step_id=step_id,
            )
        calculation["overrides"] = copy.deepcopy(dict(overrides))
    return calculation


# Mechanical compat: old import path was ``confflow.producer.intent.compiler``.
# Same objects are re-exported there (``is`` holds); keep the old module name
# so ``__module__``/pickle/monkeypatch paths observe the pre-move location.
_resolve_program.__module__ = "confflow.producer.intent.compiler"
_wire_calculation.__module__ = "confflow.producer.intent.compiler"


def require_recipe_assignment(
    user: Mapping[str, Any], base: Mapping[str, Any], step_id: str
) -> None:
    """Require explicit science for a recipe calculation assignment (L1-A2b1)."""
    if not isinstance(base.get("calculation"), Mapping):
        return
    if user.get("program") is None or user.get("native") is None:
        raise _fail(
            f"step {step_id!r}: recipe assignments require explicit program and native "
            "(demo recipe science must not leak into production)",
            step_id=step_id,
        )


def patch_recipe_block(
    calculation_block: Mapping[str, Any], user: Mapping[str, Any], step_id: str
) -> dict[str, Any]:
    """Patch one calculation wire block with user assignments (L1-A2b1, pure)."""
    calculation = copy.deepcopy(dict(calculation_block))
    if user.get("program") is not None:
        calculation["program"] = _resolve_program(user["program"], step_id=step_id)
    if user.get("native") is not None:
        if not isinstance(user["native"], Mapping):
            raise _fail(f"step {step_id!r} native must be a mapping", step_id=step_id)
        calculation["native"] = copy.deepcopy(dict(user["native"]))
    if user.get("role") is not None:
        calculation["role"] = user["role"]
    if user.get("adapter") is not None:
        calculation["execution_adapter"] = copy.deepcopy(user["adapter"])
    if user.get("profile") is not None:
        calculation["result_profile"] = copy.deepcopy(user["profile"])
    if user.get("checks") is not None:
        calculation["checks"] = copy.deepcopy(list(user["checks"]))
    if user.get("check_params") is not None:
        calculation["check_params"] = copy.deepcopy(dict(user["check_params"]))
    if user.get("recovery") is not None:
        recovery = dict(calculation.get("recovery", {}))
        recovery["profile"] = user["recovery"]
        calculation["recovery"] = recovery
    if user.get("seed") is not None:
        calculation["seed"] = user["seed"]
    if user.get("overrides") is not None:
        calculation["overrides"] = copy.deepcopy(dict(user["overrides"]))
    return calculation


#: Step keys this executor cannot consume (R1 authority, verbatim old branch).
#: Old compiler branch was ``{"preset"}`` for ``calculation``.
#: Wire block: ``"calculation"`` (= ``fragment_keys[0]`` for calculation cards;
#: compiler derives via the descriptor effective key, no executor branch here).
REJECTED_STEP_KEYS: frozenset = frozenset({"preset"})


def calculation_fragment(
    step: Mapping[str, Any], card: dict[str, Any] | Mapping[str, Any], step_id: str
) -> dict[str, Any]:
    """Complete capability-owned fragment for calculation cards (NEW adapter)."""
    return {"calculation": _wire_calculation(step, card, step_id)}


def apply_role_card_block(
    base_calculation: Mapping[str, Any],
    template: Mapping[str, Any],
    purpose: Mapping[str, Any],
    selected_native: Any,
    step_id: str,
    *,
    card_name: str,
) -> dict[str, Any]:
    program = template.get("program")
    if not isinstance(program, str) or not program.strip():
        raise _fail(
            f"step {step_id!r}: card {card_name!r} must declare an explicit program "
            "(demo recipe science must not leak into production)",
            step_id=step_id,
        )
    if not isinstance(selected_native, Mapping) or not selected_native:
        raise _fail(
            f"step {step_id!r}: card {card_name!r} must declare an explicit non-empty "
            "native mapping (or native_by_role variant)",
            step_id=step_id,
        )
    patched_calc = copy.deepcopy(dict(base_calculation))
    patched_calc["program"] = _resolve_program(program, step_id=step_id)
    patched_calc["native"] = copy.deepcopy(dict(selected_native))
    patched_calc["execution_adapter"] = copy.deepcopy(template.get("adapter", purpose["adapter"]))
    patched_calc["result_profile"] = copy.deepcopy(template.get("profile", purpose["profile"]))
    if template.get("checks") is not None:
        patched_calc["checks"] = copy.deepcopy(list(template["checks"]))
    else:
        patched_calc["checks"] = copy.deepcopy(list(purpose["checks"]))
    if template.get("check_params") is not None:
        patched_calc["check_params"] = copy.deepcopy(dict(template["check_params"]))
    else:
        patched_calc["check_params"] = copy.deepcopy(dict(purpose.get("check_params", {})))
    if template.get("recovery") is not None:
        recovery = dict(patched_calc.get("recovery", {}))
        recovery["profile"] = copy.deepcopy(template["recovery"])
        patched_calc["recovery"] = recovery
    else:
        patched_calc["recovery"] = {"profile": copy.deepcopy(purpose["recovery"])}
    if template.get("recovery_params") is not None:
        recovery = dict(patched_calc.get("recovery", {}))
        recovery["params"] = copy.deepcopy(dict(template["recovery_params"]))
        patched_calc["recovery"] = recovery
    if template.get("seed") is not None:
        patched_calc["seed"] = copy.deepcopy(template["seed"])
    if template.get("overrides") is not None:
        patched_calc["overrides"] = copy.deepcopy(dict(template["overrides"]))
    return patched_calc
