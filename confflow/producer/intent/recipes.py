#!/usr/bin/env python3

"""Generic recipe assignment orchestration (L1-A2b1).

Light boundary: top-level stdlib + ``.common`` only.  Card/program/native
knowledge arrives via parameters (context/callbacks/registry handles), never
via top-level capability imports, so there is no ``compiler`` back-import and
no cycle.  All executor-specific science lives in capability hooks
(``requires_assignment``/``patch_recipe_step``); this module only does outer
merges, ordering, and the missing gate derived from hook presence.
"""

from __future__ import annotations

import copy
from collections.abc import Callable, Mapping
from typing import Any

from .common import _fail  # noqa: F401  (re-exported for thin wrappers if needed)

INTENT_SCHEMA: str = "confflow.intent.v1"


def apply_recipe_assignment(
    patched_step: dict[str, Any],
    user: Mapping[str, Any],
    step_id: str,
    *,
    patch_block_fn: Callable[[Any, Mapping[str, Any], str], Any] | None,
    wire_block_key: str | None,
) -> None:
    """Apply user assignments onto one reviewed recipe step in place (generic).

    Sets ``_expanded`` first (verbatim old timing), then patches the wire
    block named by ``wire_block_key`` via ``patch_block_fn`` (capability hook,
    e.g. calculation block mapping).  ``patch_block_fn`` receives the block
    mapping and returns the new block mapping (or mutates in place and returns
    ``None``); ``None`` hook means "no block patch" (legal no-hook skip).
    Resources/scheduler/annotations outer merge is verbatim old order.
    No executor/program/native literals here: block name and science arrive
    via parameters.
    """
    patched_step["_expanded"] = True
    block: Any = None
    has_block = False
    if isinstance(wire_block_key, str) and wire_block_key:
        try:
            block = patched_step.get(wire_block_key)
        except Exception:
            block = None
        has_block = isinstance(block, Mapping)
    if patch_block_fn is None:
        # Legal no-hook executor: old require skipped and old patch marked
        # only ``_expanded`` (early return before outer).  Preserve verbatim.
        return
    if not has_block:
        # Old early return analogue: non-block steps only get ``_expanded``.
        # Preserve verbatim: skip resources/scheduler/annotations outer.
        return
    updated = patch_block_fn(copy.deepcopy(dict(block)), user, step_id)
    if updated is not None:
        patched_step[wire_block_key] = updated  # type: ignore[index]
    if user.get("resources") is not None:
        patched_step["resources"] = copy.deepcopy(dict(user["resources"]))
    if user.get("scheduler") is not None:
        patched_step["scheduler"] = copy.deepcopy(dict(user["scheduler"]))
    annotations = dict(patched_step.get("annotations") or {})
    resolution = dict(annotations.get("producer_resolution") or {})
    resolution["intent_schema"] = INTENT_SCHEMA
    resolution["recipe_assignment"] = True
    annotations["producer_resolution"] = resolution
    patched_step["annotations"] = annotations


def missing_recipe_assignments(
    base_wire_steps: list[dict[str, Any]],
    matched: set[str],
    *,
    hooks_of: Callable[[str], tuple[Any, Any] | None],
) -> list[str]:
    """Return sorted ids needing explicit assignments (generic missing gate).

    Derived from descriptor hook presence (``hooks_of(executor) is not None``),
    not from a wire-block literal: steps whose executor declares recipe hooks
    and are unmatched are missing.  Message/sorting/timing preserved by the
    caller (old text: "recipe requires explicit program/native assignments
    for every calculation step (no demo science); missing: ...").
    Unknown executors (``hooks_of`` returns ``None``) are excluded (old
    non-block early analogue); lookup/conflict failures raise fail-closed via
    ``hooks_of`` and never collapse into silent skip.
    """
    missing: list[str] = []
    for step in base_wire_steps:
        if not isinstance(step, dict):
            continue
        step_id = str(step.get("id"))
        if step_id in matched:
            continue
        try:
            executor = step.get("executor")
        except Exception:
            continue
        if not isinstance(executor, str) or not executor:
            continue
        hooks = hooks_of(executor)
        if hooks is None:
            continue
        missing.append(step_id)
    return sorted(missing)


def run_recipe_lane(
    base_wire_steps: list[dict[str, Any]],
    user_steps: list[dict[str, Any]],
    *,
    hooks_of: Callable[[str], tuple[Any, Any] | None],
    wire_key_of: Callable[[str], str | None],
    select_family_native_fn: Callable[..., dict[str, Any] | None],
) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]], set[str], list[dict[str, Any]]]:
    """Run the b1 recipe assignment lane generically (assignment channel only).

    ``hooks_of(executor)`` returns ``(require_fn, patch_fn) | None``;
    ``wire_key_of(executor)`` returns the wire block key (generic descriptor
    metadata); ``select_family_native_fn(template, role, *, context=...)`` is
    the family selector (b1 still delegates to the compiler original; b2 moves
    the body).  Role extraction reads ``base[wire_key].role`` generically via
    ``wire_key_of`` -- no executor literal branch here.  Error order preserved:
    family selection, then require gate, then patch mapping.  Returns
    ``(wire_steps, base_by_id, matched, appended)``.
    """
    wire_steps: list[dict[str, Any]] = [copy.deepcopy(step) for step in base_wire_steps]
    base_by_id = {str(step.get("id")): step for step in wire_steps if isinstance(step, dict)}
    appended: list[dict[str, Any]] = []
    matched: set[str] = set()
    for user in user_steps:
        explicit_id = str(user["id"])
        if explicit_id in base_by_id:
            base_step = base_by_id[explicit_id]
            try:
                executor = base_step.get("executor")
            except Exception:
                executor = None
            executor_str = executor if isinstance(executor, str) else ""
            wire_key = wire_key_of(executor_str) if executor_str else None
            base_role: str | None = None
            if isinstance(wire_key, str) and wire_key:
                try:
                    block = base_step.get(wire_key)
                except Exception:
                    block = None
                if isinstance(block, Mapping):
                    raw_role = block.get("role")
                    base_role = raw_role if isinstance(raw_role, str) else None
            if user.get("native") is None and isinstance(user.get("native_by_role"), Mapping):
                user = {
                    **user,
                    "native": select_family_native_fn(
                        {"native_by_role": user["native_by_role"]},
                        base_role,
                        context=f"step {explicit_id!r}",
                    ),
                }
            hooks = hooks_of(executor_str) if executor_str else None
            if hooks is None:
                # Legal no-hook executor (today: non-hooked bases): old
                # require skipped silently; old patch still marked _expanded.
                # Preserve via generic outer with no block hook.
                apply_recipe_assignment(
                    base_step,
                    user,
                    explicit_id,
                    patch_block_fn=None,
                    wire_block_key=wire_key,
                )
            else:
                require_fn, patch_fn = hooks
                if require_fn is not None:
                    require_fn(user, base_step, explicit_id)
                apply_recipe_assignment(
                    base_step,
                    user,
                    explicit_id,
                    patch_block_fn=patch_fn,
                    wire_block_key=wire_key,
                )
            if isinstance(user.get("_named_card"), str):
                _annotations = dict(base_step.get("annotations") or {})
                _resolution = dict(_annotations.get("producer_resolution") or {})
                _resolution["named_card"] = user["_named_card"]
                _annotations["producer_resolution"] = _resolution
                base_step["annotations"] = _annotations
                base_step["_named_card"] = user["_named_card"]
            matched.add(explicit_id)
        else:
            appended.append(user)
    return wire_steps, base_by_id, matched, appended
