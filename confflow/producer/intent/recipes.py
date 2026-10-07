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
import re
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
    """Apply user assignments onto one reviewed recipe step in place (generic)."""
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
    """Return sorted ids needing explicit assignments (generic missing gate)."""
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
    """Run the b1 recipe assignment lane generically (assignment channel only)."""
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


_IDENTIFIER_PATTERN = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,63}$")


_CARD_DEF_KEYS = frozenset(
    {
        "card",
        "role",
        "program",
        "native",
        "native_by_role",
        "resources",
        "scheduler",
        "overrides",
        "seed",
        "adapter",
        "profile",
        "checks",
        "check_params",
        "recovery",
        "recovery_params",
        "preset",
    }
)


def _require_identifier(value: Any, field_name: str) -> str:
    if not isinstance(value, str) or _IDENTIFIER_PATTERN.match(value) is None:
        raise _fail(
            f"{field_name} must match [A-Za-z][A-Za-z0-9_]{{0,63}}, got {value!r}",
            field_path=field_name,
        )
    return value


def normalize_cards(raw: Any) -> dict[str, dict[str, Any]]:
    if raw is None:
        return {}
    if not isinstance(raw, Mapping):
        raise _fail("intent 'cards' must be a mapping when declared")
    out: dict[str, dict[str, Any]] = {}
    for name, definition in raw.items():
        _require_identifier(name, "card name")
        if "@" in str(name):
            raise _fail(f"card name {name!r} must be a bare identifier (no '@')")
        if not isinstance(definition, Mapping):
            raise _fail(f"card {name!r} must be a mapping", field_path=f"cards.{name}")
        unknown = sorted(set(definition) - _CARD_DEF_KEYS)
        if unknown:
            raise _fail(
                f"card {name!r} carries unknown members: {', '.join(unknown)}",
                field_path=f"cards.{name}",
            )
        if definition.get("card") is None:
            raise _fail(
                f"card {name!r} must declare a base card '<type>@<version>' or another card name",
                field_path=f"cards.{name}.card",
            )
        native = definition.get("native")
        native_by_role = definition.get("native_by_role")
        if native is not None and native_by_role is not None:
            raise _fail(
                f"card {name!r} declares both 'native' and 'native_by_role'; declare one",
                field_path=f"cards.{name}",
            )
        if native is not None and not isinstance(native, Mapping):
            raise _fail(f"card {name!r} native must be a mapping", field_path=f"cards.{name}")
        if native_by_role is not None:
            if not isinstance(native_by_role, Mapping) or not native_by_role:
                raise _fail(
                    f"card {name!r} native_by_role must be a non-empty mapping",
                    field_path=f"cards.{name}",
                )
            for role_key, variant in native_by_role.items():
                if not isinstance(role_key, str) or not role_key.strip():
                    raise _fail(
                        f"card {name!r} native_by_role keys must be non-empty strings",
                        field_path=f"cards.{name}.native_by_role",
                    )
                if not isinstance(variant, Mapping) or not variant:
                    raise _fail(
                        f"card {name!r} native_by_role[{role_key!r}] must be a non-empty mapping",
                        field_path=f"cards.{name}.native_by_role",
                    )
        out[str(name)] = copy.deepcopy(dict(definition))
    return out


def resolve_named_cards(
    raw_cards: dict[str, dict[str, Any]],
    *,
    parse_ref_fn: Callable[[Any], tuple[str, str]],
) -> dict[str, dict[str, Any]]:
    resolved: dict[str, dict[str, Any]] = {}

    def _resolve(name: str, stack: tuple[str, ...]) -> dict[str, Any]:
        if name in resolved:
            return copy.deepcopy(resolved[name])
        if name in stack:
            raise _fail(f"card chain cycle: {' -> '.join([*stack, name])}")
        try:
            definition = raw_cards[name]
        except KeyError as exc:
            raise _fail(f"unknown card reference {name!r}") from exc
        base_ref = definition.get("card")
        if not isinstance(base_ref, str) or not base_ref.strip():
            raise _fail(f"card {name!r} base reference must be a non-empty string")
        base_ref = base_ref.strip()
        if "@" in base_ref:
            try:
                parse_ref_fn(base_ref)
            except ValueError as exc:
                raise _fail(f"card {name!r} carries a bad card reference: {exc}") from exc
            merged: dict[str, Any] = {"card": base_ref}
            for key, value in definition.items():
                if key == "card":
                    continue
                merged[key] = copy.deepcopy(value)
            resolved[name] = copy.deepcopy(merged)
            return copy.deepcopy(merged)
        parent = _resolve(base_ref, (*stack, name))
        merged = copy.deepcopy(parent)
        for key, value in definition.items():
            if key == "card":
                continue
            merged[key] = copy.deepcopy(value)
        merged["card"] = parent["card"]
        resolved[name] = copy.deepcopy(merged)
        return copy.deepcopy(merged)

    for card_name in raw_cards:
        _resolve(card_name, ())
    return resolved


def expand_named_step(
    step: Mapping[str, Any], resolved_cards: Mapping[str, dict[str, Any]]
) -> dict[str, Any]:
    ref = step.get("card")
    if isinstance(ref, Mapping):
        return dict(step)
    if not isinstance(ref, str):
        return dict(step)
    if "@" in ref:
        return dict(step)
    name = ref.strip()
    if not name:
        return dict(step)
    try:
        template = resolved_cards[name]
    except KeyError as exc:
        raise _fail(
            f"step {step.get('id')!r} references unknown card {ref!r}",
            step_id=str(step.get("id")) if step.get("id") is not None else None,
        ) from exc
    expanded: dict[str, Any] = {}
    for key, value in template.items():
        if key == "card":
            expanded["card"] = copy.deepcopy(value)
        else:
            expanded[key] = copy.deepcopy(value)
    for key, value in step.items():
        if key == "card":
            continue
        expanded[key] = copy.deepcopy(value)
    expanded["card"] = copy.deepcopy(template["card"])
    expanded["_named_card"] = name
    return expanded


def select_family_native(
    template: Mapping[str, Any], role: str | None, *, context: str
) -> dict[str, Any] | None:
    native_by_role = template.get("native_by_role")
    if native_by_role is None:
        native = template.get("native")
        return copy.deepcopy(dict(native)) if isinstance(native, Mapping) else None
    if not isinstance(native_by_role, Mapping):
        raise _fail(f"{context}: family card native_by_role must be a mapping")
    if role is None or role not in native_by_role:
        raise _fail(
            f"{context}: family card needs an explicit native_by_role entry for "
            f"role {role!r} (no keyword guessing)"
        )
    variant = native_by_role[role]
    if not isinstance(variant, Mapping):
        raise _fail(f"{context}: native_by_role[{role!r}] must be a mapping")
    return copy.deepcopy(dict(variant))


def recipe_cards_to_role_cards(
    recipe_cards: Mapping[str, str], *, recipe_id: str
) -> dict[str, str]:
    # R2.2: the only supported recipe family ('tspes') is retired, so no
    # recipe accepts the 'recipe_cards' normal mode anymore.  The lane
    # stays fail-closed (never silently ignored) until R2.3 removes it.
    raise _fail(
        f"intent 'recipe_cards' is retired with recipe {recipe_id!r}: "
        "no recipe accepts the normal mode anymore"
    )


def expected_purpose(step_id: str, base_role: str | None, *, recipe_id: str | None) -> str | None:
    return base_role


def apply_role_cards(
    wire_steps: list[dict[str, Any]],
    role_cards: Mapping[str, str],
    resolved_cards: Mapping[str, dict[str, Any]],
    *,
    skip_ids: set[str],
    recipe_id: str | None,
    role_block_of: Callable[[str], Callable[..., dict[str, Any]] | None],
    card_access: Mapping[str, Any],
    wire_key_of: Callable[[str], str | None],
) -> set[str]:
    parse_ref_fn = card_access["parse_ref"]
    get_card_fn = card_access["get_card"]
    card_types = card_access["card_types"]
    card_version = card_access["card_version"]
    intent_schema = card_access["intent_schema"]
    covered: set[str] = set()
    for base in wire_steps:
        step_id = str(base.get("id"))
        if step_id in skip_ids:
            covered.add(step_id)
            continue
        try:
            executor = base.get("executor")
        except Exception:
            executor = None
        executor_str = executor if isinstance(executor, str) else ""
        wire_key = wire_key_of(executor_str) if executor_str else None
        calculation: Any = None
        if isinstance(wire_key, str) and wire_key:
            try:
                calculation = base.get(wire_key)
            except Exception:
                calculation = None
        if not isinstance(calculation, Mapping):
            continue
        raw_role = calculation.get("role")
        base_role_str = raw_role if isinstance(raw_role, str) else None
        card_name: str | None = None
        via_step_id = False
        if step_id in role_cards:
            card_name = role_cards[step_id]
            via_step_id = True
        elif base_role_str is not None and base_role_str in role_cards:
            card_name = role_cards[base_role_str]
        if card_name is None:
            continue
        # Generic dispatch (L1-A2b2): hook by actual wire executor via the
        # shared descriptor/registry -- never a uniform calc hook.  ``None``
        # is the legal no-hook skip (default non-calculation analogue);
        # unknown/conflict/non-callable fail closed; no science here.
        hook = role_block_of(executor_str) if executor_str else None
        if hook is None:
            continue
        if not callable(hook):
            raise _fail(
                f"step {step_id!r}: role-card hook for executor {executor_str!r} "
                "is not callable",
                step_id=step_id,
            )
        try:
            template = resolved_cards[card_name]
        except KeyError as exc:
            raise _fail(
                f"role_cards[{step_id!r}] references unknown card {card_name!r}",
                step_id=step_id,
            ) from exc
        try:
            template_type, template_version = parse_ref_fn(template.get("card"))
        except ValueError as exc:
            raise _fail(f"card {card_name!r} carries a bad card reference: {exc}") from exc
        if template_version != card_version:
            raise _fail(f"card {card_name!r} carries an unsupported version")
        native_by_role = template.get("native_by_role")
        is_family = isinstance(native_by_role, Mapping)
        variant_key: str | None = None
        purpose_type: str
        native: dict[str, Any] | None = None
        if is_family:
            assert isinstance(native_by_role, Mapping)
            if step_id in native_by_role:
                variant_key = step_id
            else:
                variant_key = base_role_str
            if variant_key is None or variant_key not in native_by_role:
                raise _fail(
                    f"step {step_id!r}: family card {card_name!r} declares no "
                    f"native_by_role variant for "
                    f"{variant_key!r} (step-id key wins, else role key; no guessing)",
                    step_id=step_id,
                )
            variant = native_by_role[variant_key]
            if not isinstance(variant, Mapping) or not variant:
                raise _fail(
                    f"step {step_id!r}: family card {card_name!r} variant "
                    f"{variant_key!r} must be a non-empty mapping",
                    step_id=step_id,
                )
            native = copy.deepcopy(dict(variant))
            if variant_key in card_types:
                purpose_type = variant_key
            else:
                purpose_type = template_type
            expected = expected_purpose(step_id, base_role_str, recipe_id=recipe_id)
            if expected is not None and purpose_type != expected:
                raise _fail(
                    f"step {step_id!r} needs purpose {expected!r} but family "
                    f"card {card_name!r} selected variant {variant_key!r} "
                    f"(purpose {purpose_type!r}); declare the {expected!r} "
                    "variant explicitly",
                    step_id=step_id,
                )
        else:
            native_raw = template.get("native")
            if not isinstance(native_raw, Mapping) or not native_raw:
                raise _fail(
                    f"step {step_id!r}: card {card_name!r} must declare an explicit "
                    "non-empty native mapping (or native_by_role variant)",
                    step_id=step_id,
                )
            native = copy.deepcopy(dict(native_raw))
            purpose_type = template_type
            expected = expected_purpose(step_id, base_role_str, recipe_id=recipe_id)
            if expected is not None and purpose_type != expected and not via_step_id:
                raise _fail(
                    f"step {step_id!r} needs purpose {expected!r} but card "
                    f"{card_name!r} is {purpose_type!r}; map it by explicit "
                    "step id when the mismatch is intended",
                    step_id=step_id,
                )
        if purpose_type not in card_types:
            raise _fail(f"card {card_name!r} resolves to unknown purpose {purpose_type!r}")
        try:
            purpose = get_card_fn(purpose_type, card_version)
        except ValueError as exc:
            raise _fail(f"card {card_name!r} resolves to a bad purpose: {exc}") from exc
        patched_calc = hook(
            copy.deepcopy(dict(calculation)),
            template,
            purpose,
            copy.deepcopy(dict(native)) if isinstance(native, Mapping) else native,
            step_id,
            card_name=card_name,
        )
        assert isinstance(wire_key, str) and wire_key
        base[wire_key] = patched_calc
        if template.get("resources") is not None:
            base["resources"] = copy.deepcopy(dict(template["resources"]))
        if template.get("scheduler") is not None:
            base["scheduler"] = copy.deepcopy(dict(template["scheduler"]))
        annotations = dict(base.get("annotations") or {})
        resolution = dict(annotations.get("producer_resolution") or {})
        resolution["intent_schema"] = intent_schema
        resolution["recipe_assignment"] = True
        resolution["role_card"] = card_name
        resolution["named_card"] = card_name
        resolution["card_type"] = purpose_type
        resolution["card_version"] = card_version
        if variant_key is not None:
            resolution["family_variant"] = variant_key
        resolution["purpose"] = purpose_type
        annotations["producer_resolution"] = resolution
        base["annotations"] = annotations
        base["_named_card"] = card_name
        base["_card_type"] = purpose_type
        base["_card_version"] = card_version
        covered.add(step_id)
    return covered
