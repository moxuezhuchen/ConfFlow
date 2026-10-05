#!/usr/bin/env python3

"""Simplified producer intent (Phases 2-4 + 7).

:func:`compile_intent` turns the documented ``confflow.intent.v1`` mapping
into a strict V4 wire document and verifies it through the REAL strict V4
parser and compiler.  There is no second runtime: cards supply
``CalculationModel`` defaults resolved against the real execution registry
and program registry, bindings obey the real port contracts, seeds fill the
strict ``calculation.seed``/``confgen.seed`` fields, presets fill the strict
transform native vocabulary, and every failure raises
:class:`IntentCompilationError` (a ``ValueError`` so root dispatch can catch
``ValueError``/``DomainError``).

Module-load imports stay light (cards/presets constants only) so
:func:`intent_catalog` is pure and safe to call from
``build_configuration_contract_v4`` without compiler/contract recursion.
Everything heavy is imported lazily inside :func:`compile_intent`.
"""

from __future__ import annotations

import copy
import re
from collections.abc import Mapping
from typing import Any

from ..cards import CARD_TYPES, CARD_VERSION, get_card, parse_card_ref
from ..presets import PRESET_VERSION, get_preset, parse_preset_ref
from .bindings import _auto_bindings, _registry_input_ports  # noqa: F401
from .common import IntentCompilationError, _fail  # noqa: F401
from .resources import (  # noqa: F401
    _apply_checkpoints,
    _apply_machine_profile,
    _wire_resources,
    _wire_scheduler,
)

__all__ = [
    "INTENT_SCHEMA",
    "IntentCompilationError",
    "compile_intent",
    "intent_catalog",
]

#: Accepted simplified intent schema id.
INTENT_SCHEMA: str = "confflow.intent.v1"

#: Strict V4 wire schema id (legacy passthrough target).
_WORKFLOW_SCHEMA: str = "confflow.workflow.v4"

#: Default run input when the intent declares no inputs.
_DEFAULT_INPUTS: dict[str, Any] = {
    "structures": {"kind": "structure", "cardinality": "many", "grouping": "each_entity"}
}

_CHECKPOINT_MODES: tuple[str, ...] = ("checkpoint", "readfc", "rcfc")

_IDENTIFIER_PATTERN = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,63}$")

_STEP_KEYS = frozenset(
    {
        "id",
        "card",
        "role",
        "program",
        "native",
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
        "from",
        "bindings",
        "reuse_checkpoint",
        "execution",
        "label",
    }
)

_TOP_KEYS = frozenset(
    {"schema", "inputs", "globals", "recipe", "steps", "cards", "role_cards", "recipe_cards"}
)

#: Allowed members of one reusable card definition (named calculation
#: template).  Identity/binding/wiring keys are forbidden here: a card
#: fixes reusable science, never per-step placement.
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


def intent_catalog() -> dict[str, Any]:
    """Return the versioned card/preset catalog (pure constants).

    No compiler, registry, or contract import happens here, so root may
    call this from ``build_configuration_contract_v4`` without recursion.
    The reusable-card and recipe role-card guides are documented constants
    here so editors can render them without importing the compiler.
    """
    from ..cards import CARD_TYPES
    from ..presets import PRESET_TYPES

    try:
        from ..recipes import RECIPE_IDS_V4
    except ImportError:
        recipe_ids: tuple[str, ...] = ()
    else:
        recipe_ids = tuple(RECIPE_IDS_V4)

    cards: list[dict[str, Any]] = []
    for name in CARD_TYPES:
        descriptor = get_card(name)
        cards.append(
            {
                "type": name,
                "version": CARD_VERSION,
                "executor": descriptor["executor"],
                "adapter": descriptor["adapter"],
                "profile": descriptor["profile"],
                "checks": list(descriptor["checks"]),
                "recovery": descriptor["recovery"],
                "default_role": descriptor.get("default_role"),
                "transform_kind": descriptor.get("transform_kind"),
                "requires_explicit_bindings": bool(
                    descriptor.get("requires_explicit_bindings", False)
                ),
            }
        )
    presets: list[dict[str, Any]] = []
    for name in PRESET_TYPES:
        descriptor = get_preset(name)
        presets.append(
            {
                "name": name,
                "version": PRESET_VERSION,
                "card": descriptor["card"],
                "native": copy.deepcopy(descriptor["native"]),
            }
        )
    return {
        "intent_schema": INTENT_SCHEMA,
        "workflow_schema": _WORKFLOW_SCHEMA,
        "card_version": CARD_VERSION,
        "cards": cards,
        "preset_version": PRESET_VERSION,
        "presets": presets,
        "schema_keys": sorted(_TOP_KEYS),
        "step_keys": sorted(_STEP_KEYS),
        "card_definition_keys": sorted(_CARD_DEF_KEYS),
        "default_seed_policy": "explicit-or-derived-once",
        "from_semantics": "linear-predecessor by default; '<step-id>' or 'run:<input>' override",
        "supported_recipes": list(recipe_ids),
        "reusable_cards": {
            "top_key": "cards",
            "description": (
                "Optional mapping of reusable calculation templates: "
                "{<name>: {card: '<type>@v1', program, native, resources, ...}}. "
                "A step with card '<name>' (no '@') expands the named template "
                "mechanically before validation; explicit step fields win "
                "(native replaces wholesale, never merges). Inline '<type>@v1' "
                "still works. Unknown refs, bad types/versions, and chained "
                "cycles fail closed."
            ),
            "ref_form": "bare name without '@' (inline keeps '<type>@<version>')",
            "native_merge": "replace (step native replaces card native verbatim)",
            "family_key": "native_by_role ({role: native}) selects per-step-role "
            "variants verbatim with no keyword editing",
        },
        "role_cards": {
            "top_key": "role_cards",
            "description": (
                "Optional recipe shortcut: {<role-or-step-id>: '<card-name>'} "
                "mapping each reviewed calculation role (or step id, which wins) "
                "to a reusable card from top-level 'cards'. Every base "
                "calculation step must be covered; demo program/native never "
                "leaks. Family cards select the step-id variant first, then "
                "the role variant, verbatim with purpose checks from the "
                "variant key (ts_freq steps need a ts_freq variant/purpose). "
                "One SP card may cover ts_sp and endpoint_sp; frequency legs "
                "may share a family card."
            ),
        },
        "recipe_cards": {
            "top_key": "recipe_cards",
            "description": (
                "Optional normal mode for reviewed recipes (currently 'tspes' "
                "only): {low_level: '<family-card>', single_point: '<sp-card>'} "
                "generates the role mapping mechanically (ts/freq/opt/irc from "
                "the low-level family with ts_freq purpose special, sp from "
                "the single-point card shared twice). Explicit 'role_cards' "
                "still wins on conflicts."
            ),
        },
        "configuration_docs": {
            "intent_schema": INTENT_SCHEMA,
            "workflow_schema": _WORKFLOW_SCHEMA,
        },
    }


def _require_identifier(value: Any, field_name: str) -> str:
    if not isinstance(value, str) or _IDENTIFIER_PATTERN.match(value) is None:
        raise _fail(
            f"{field_name} must match [A-Za-z][A-Za-z0-9_]{{0,63}}, got {value!r}",
            field_path=field_name,
        )
    return value


def _coerce_source(document: Any, intent: Any) -> Mapping[str, Any]:
    source = intent if intent is not None else document
    if source is None:
        raise _fail("compile_intent requires an intent mapping")
    if not isinstance(source, Mapping):
        raise _fail(f"intent must be a mapping, got {type(source).__name__}")
    return source


# ----------------------------------------------------------------------
# Legacy passthrough
# ----------------------------------------------------------------------


def _passthrough_legacy(source: Mapping[str, Any], registry: Any) -> dict[str, Any]:
    """Verify a strict V4 document with the real compiler; return it unchanged."""
    from ...workflow.v4.compiler import compile_workflow

    snapshot = copy.deepcopy(dict(source))
    try:
        result = compile_workflow(snapshot, registry=registry)
    except Exception as exc:
        raise _fail(f"legacy V4 document failed to compile: {exc}") from exc
    if not result.ok:
        details = (
            "; ".join(
                f"{item.code}@{item.field_path or item.step_id or '?'}: {item.message}"
                for item in result.diagnostics
                if item.is_error
            )
            or "strict V4 validation failed"
        )
        raise _fail(
            f"legacy V4 document does not compile: {details}",
            diagnostics=tuple(result.diagnostics),
        )
    return copy.deepcopy(dict(source))


# ----------------------------------------------------------------------
# Intent normalization
# ----------------------------------------------------------------------


def _normalize_inputs(raw: Any) -> dict[str, Any]:
    if raw is None:
        return copy.deepcopy(_DEFAULT_INPUTS)
    if not isinstance(raw, Mapping) or not raw:
        raise _fail("intent 'inputs' must be a non-empty mapping when declared")
    return copy.deepcopy(dict(raw))


def _normalize_globals(raw: Any) -> dict[str, Any]:
    if raw is None:
        return {}
    if not isinstance(raw, Mapping):
        raise _fail("intent 'globals' must be a mapping when declared")
    allowed = {"charge", "multiplicity", "freeze"}
    unknown = sorted(set(raw) - allowed)
    if unknown:
        raise _fail(f"intent 'globals' carries unknown members: {', '.join(unknown)}")
    out: dict[str, Any] = {}
    if raw.get("charge") is not None:
        charge = raw["charge"]
        if isinstance(charge, bool) or not isinstance(charge, int):
            raise _fail("intent globals charge must be an integer")
        out["charge"] = charge
    if raw.get("multiplicity") is not None:
        multiplicity = raw["multiplicity"]
        if isinstance(multiplicity, bool) or not isinstance(multiplicity, int):
            raise _fail("intent globals multiplicity must be an integer")
        if multiplicity < 1:
            raise _fail("intent globals multiplicity must be >= 1")
        out["multiplicity"] = multiplicity
    if raw.get("freeze") is not None:
        freeze = raw["freeze"]
        # [] is a valid explicit clear (strict V4 allows an empty freeze
        # list); only the item types are validated, never the length.
        if not isinstance(freeze, (list, tuple)):
            raise _fail("intent globals freeze must be a list when declared")
        indices: list[int] = []
        for item in freeze:
            if isinstance(item, bool) or not isinstance(item, int) or item < 1:
                raise _fail("intent globals freeze indices must be 1-based integers")
            indices.append(item)
        out["freeze"] = sorted(set(indices))
    return out


def _allocate_ids(raw_steps: list[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Assign stable ``{cardtype}_{occurrence}`` ids; preserve explicit ones."""
    counts: dict[str, int] = {}
    used: set[str] = set()
    for raw in raw_steps:
        explicit = raw.get("id")
        if explicit is not None:
            _require_identifier(explicit, "step id")
            if explicit in used:
                raise _fail(f"duplicate step id {explicit!r}", step_id=str(explicit))
            used.add(str(explicit))
    allocated: list[dict[str, Any]] = []
    for raw in raw_steps:
        step = dict(raw)
        if step.get("id") is not None:
            allocated.append(step)
            continue
        try:
            card_type, _ = parse_card_ref(step.get("card"))
        except ValueError as exc:
            raise _fail(str(exc)) from exc
        counts[card_type] = counts.get(card_type, 0) + 1
        candidate = f"{card_type}_{counts[card_type]}"
        while candidate in used:
            counts[card_type] += 1
            candidate = f"{card_type}_{counts[card_type]}"
        try:
            _require_identifier(candidate, "step id")
        except IntentCompilationError as exc:
            raise _fail(f"cannot allocate a step id for card {card_type!r}") from exc
        step["id"] = candidate
        used.add(candidate)
        allocated.append(step)
    return allocated


def _validate_step_keys(step: Mapping[str, Any]) -> None:
    step_id = step.get("id")
    unknown = sorted(set(step) - _STEP_KEYS)
    if unknown:
        raise _fail(
            f"step {step_id!r} carries unknown members: {', '.join(unknown)}",
            step_id=str(step_id) if step_id is not None else None,
        )
    if step.get("card") is None:
        raise _fail(
            f"step {step_id!r} must declare a versioned card",
            step_id=str(step_id) if step_id is not None else None,
        )


# ----------------------------------------------------------------------
# Reusable cards (Phase D) + recipe role cards (Phase E)
# ----------------------------------------------------------------------


def _normalize_cards(raw: Any) -> dict[str, dict[str, Any]]:
    """Validate top-level ``cards`` into name -> definition mappings."""
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


def _resolve_named_cards(raw_cards: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Resolve chained named cards into inline-based expanded definitions.

    A definition whose ``card`` holds ``'@'`` is inline (validated through
    the card authority); otherwise it names another card and merges
    recursively with the current definition winning (native replaces
    wholesale).  Unknown refs and cycles fail closed.
    """
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
                parse_card_ref(base_ref)
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


def _expand_named_step(
    step: Mapping[str, Any], resolved_cards: Mapping[str, dict[str, Any]]
) -> dict[str, Any]:
    """Expand one step's named card ref against resolved cards.

    Inline ``'<type>@<version>'`` (or type/version mappings) pass through
    untouched.  A bare name merges the named template with explicit step
    fields winning; ``native`` replaces wholesale per the documented rule.
    """
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


def _select_family_native(
    template: Mapping[str, Any], role: str | None, *, context: str
) -> dict[str, Any] | None:
    """Return the verbatim native variant for *role* from a family card."""
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


def _normalize_role_cards(raw: Any) -> dict[str, str]:
    """Validate top-level ``role_cards`` into role/step-id -> card-name."""
    if raw is None:
        return {}
    if not isinstance(raw, Mapping) or not raw:
        raise _fail("intent 'role_cards' must be a non-empty mapping when declared")
    out: dict[str, str] = {}
    for key, value in raw.items():
        if not isinstance(key, str) or not key.strip():
            raise _fail(f"role_cards keys must be non-empty strings, got {key!r}")
        if not isinstance(value, str) or not value.strip() or "@" in value:
            raise _fail(f"role_cards[{key!r}] must name a reusable card (bare name, no '@')")
        out[key.strip()] = value.strip()
    return out


#: Reviewed TSPES calculation purpose per step id.  The purpose card owns
#: the scientific defaults (adapter/profile/checks/check_params/recovery);
#: user templates only override advanced fields explicitly.
_TSPES_PURPOSE: dict[str, str] = {
    "ts": "ts",
    "ts_freq": "ts_freq",
    "ts_sp": "sp",
    "irc": "irc",
    "endpoint_opt": "opt",
    "endpoint_freq": "freq",
    "endpoint_sp": "sp",
}

#: Recipes supporting the ``recipe_cards`` normal mode.
_RECIPE_CARDS_RECIPES: tuple[str, ...] = ("tspes",)


def _normalize_recipe_cards(raw: Any) -> dict[str, str]:
    """Validate top-level ``recipe_cards`` into level -> card-name."""
    if raw is None:
        return {}
    if not isinstance(raw, Mapping) or not raw:
        raise _fail("intent 'recipe_cards' must be a non-empty mapping when declared")
    allowed = {"low_level", "single_point"}
    unknown = sorted(set(raw) - allowed)
    if unknown:
        raise _fail(f"intent 'recipe_cards' carries unknown members: {', '.join(unknown)}")
    out: dict[str, str] = {}
    for key, value in raw.items():
        if not isinstance(value, str) or not value.strip() or "@" in value:
            raise _fail(f"recipe_cards[{key!r}] must name a reusable card (bare name, no '@')")
        out[str(key)] = value.strip()
    return out


def _recipe_cards_to_role_cards(
    recipe_cards: Mapping[str, str], *, recipe_id: str
) -> dict[str, str]:
    """Generate the role mapping for the ``recipe_cards`` normal mode."""
    if recipe_id != "tspes":
        raise _fail(f"intent 'recipe_cards' currently supports only 'tspes', got {recipe_id!r}")
    try:
        low = recipe_cards["low_level"]
        high = recipe_cards["single_point"]
    except KeyError as exc:
        raise _fail(
            "intent 'recipe_cards' for 'tspes' needs both 'low_level' and 'single_point'"
        ) from exc
    # ts_freq purpose special rides the low-level family variant (step-id
    # key wins inside _apply_role_cards); sp covers both single points.
    return {"ts": low, "freq": low, "opt": low, "irc": low, "sp": high}


def _expected_purpose(step_id: str, base_role: str | None, *, recipe_id: str | None) -> str | None:
    """Return the expected purpose card type for one recipe base step."""
    if recipe_id == "tspes" and step_id in _TSPES_PURPOSE:
        return _TSPES_PURPOSE[step_id]
    return base_role


def _apply_role_cards(
    wire_steps: list[dict[str, Any]],
    role_cards: Mapping[str, str],
    resolved_cards: Mapping[str, dict[str, Any]],
    *,
    skip_ids: set[str],
    recipe_id: str | None = None,
) -> set[str]:
    """Apply reusable cards to uncovered recipe base steps in place.

    Coverage keys try the step id first, then the base calculation role;
    step-id mapping wins over role mapping.  Family cards
    (``native_by_role``) select the step-id variant first, then the role
    variant, verbatim with no keyword editing; the purpose card follows
    the selected variant key (``ts_freq`` steps need a ``ts_freq``
    variant/purpose even when mapped through the ``freq`` role key).
    Plain cards apply their native verbatim and their base type must match
    the expected stage purpose unless mapped by explicit step id.
    Purpose-card defaults (adapter/profile/checks/check_params/recovery)
    always replace the catalog demo values; user template fields only
    override them explicitly.  Returns the set of covered step ids.
    """
    covered: set[str] = set()
    for base in wire_steps:
        step_id = str(base.get("id"))
        if step_id in skip_ids:
            covered.add(step_id)
            continue
        calculation = base.get("calculation")
        if not isinstance(calculation, Mapping):
            continue
        base_role = calculation.get("role")
        base_role_str = base_role if isinstance(base_role, str) else None
        card_name: str | None = None
        via_step_id = False
        if step_id in role_cards:
            card_name = role_cards[step_id]
            via_step_id = True
        elif base_role_str is not None and base_role_str in role_cards:
            card_name = role_cards[base_role_str]
        if card_name is None:
            continue
        try:
            template = resolved_cards[card_name]
        except KeyError as exc:
            raise _fail(
                f"role_cards[{step_id!r}] references unknown card {card_name!r}",
                step_id=step_id,
            ) from exc
        try:
            template_type, template_version = parse_card_ref(template.get("card"))
        except ValueError as exc:
            raise _fail(f"card {card_name!r} carries a bad card reference: {exc}") from exc
        if template_version != CARD_VERSION:
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
            if variant_key in CARD_TYPES:
                purpose_type = variant_key
            else:
                purpose_type = template_type
            expected_purpose = _expected_purpose(step_id, base_role_str, recipe_id=recipe_id)
            if expected_purpose is not None and purpose_type != expected_purpose:
                raise _fail(
                    f"step {step_id!r} needs purpose {expected_purpose!r} but family "
                    f"card {card_name!r} selected variant {variant_key!r} "
                    f"(purpose {purpose_type!r}); declare the {expected_purpose!r} "
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
            expected = _expected_purpose(step_id, base_role_str, recipe_id=recipe_id)
            if expected is not None and purpose_type != expected and not via_step_id:
                raise _fail(
                    f"step {step_id!r} needs purpose {expected!r} but card "
                    f"{card_name!r} is {purpose_type!r}; map it by explicit "
                    "step id when the mismatch is intended",
                    step_id=step_id,
                )
        if purpose_type not in CARD_TYPES:
            raise _fail(f"card {card_name!r} resolves to unknown purpose {purpose_type!r}")
        try:
            purpose = get_card(purpose_type, CARD_VERSION)
        except ValueError as exc:
            raise _fail(f"card {card_name!r} resolves to a bad purpose: {exc}") from exc
        program = template.get("program")
        if not isinstance(program, str) or not program.strip():
            raise _fail(
                f"step {step_id!r}: card {card_name!r} must declare an explicit program "
                "(demo recipe science must not leak into production)",
                step_id=step_id,
            )
        if not isinstance(native, Mapping) or not native:
            raise _fail(
                f"step {step_id!r}: card {card_name!r} must declare an explicit non-empty "
                "native mapping (or native_by_role variant)",
                step_id=step_id,
            )
        patched_calc = copy.deepcopy(dict(calculation))
        patched_calc["program"] = _resolve_program(program, step_id=step_id)
        patched_calc["native"] = copy.deepcopy(dict(native))
        # Purpose-card scientific defaults replace catalog demo values;
        # explicit template fields win over the purpose defaults.
        patched_calc["execution_adapter"] = copy.deepcopy(
            template.get("adapter", purpose["adapter"])
        )
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
        base["calculation"] = patched_calc
        if template.get("resources") is not None:
            base["resources"] = copy.deepcopy(dict(template["resources"]))
        if template.get("scheduler") is not None:
            base["scheduler"] = copy.deepcopy(dict(template["scheduler"]))
        annotations = dict(base.get("annotations") or {})
        resolution = dict(annotations.get("producer_resolution") or {})
        resolution["intent_schema"] = INTENT_SCHEMA
        resolution["recipe_assignment"] = True
        resolution["role_card"] = card_name
        resolution["named_card"] = card_name
        resolution["card_type"] = purpose_type
        resolution["card_version"] = CARD_VERSION
        if variant_key is not None:
            resolution["family_variant"] = variant_key
        resolution["purpose"] = purpose_type
        annotations["producer_resolution"] = resolution
        base["annotations"] = annotations
        base["_named_card"] = card_name
        base["_card_type"] = purpose_type
        base["_card_version"] = CARD_VERSION
        covered.add(step_id)
    return covered


# ----------------------------------------------------------------------
# Wire builders
# ----------------------------------------------------------------------


def _resolve_program(program: Any, *, step_id: str) -> str:
    from ...programs.registry import get_program_adapter

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
    step: Mapping[str, Any], card: dict[str, Any], step_id: str
) -> dict[str, Any]:
    native = step.get("native")
    if not isinstance(native, Mapping) or not native:
        raise _fail(
            f"step {step_id!r} requires an explicit non-empty native mapping",
            step_id=step_id,
        )
    goat_section = native.get("goat")
    if isinstance(goat_section, Mapping) and "RANDOMSEED" in goat_section:
        raise _fail(
            f"step {step_id!r}: native goat RANDOMSEED is a second seed authority; "
            "set the step seed instead",
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
            f"step {step_id!r}: deduplicate takes no native parameters, " f"got {sorted(native)}",
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
        from ...execution.transform_executor import REFINE_NATIVE_KEYS
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


_CALC_ONLY_FIELDS = frozenset(
    {
        "program",
        "role",
        "adapter",
        "profile",
        "checks",
        "check_params",
        "recovery",
        "recovery_params",
    }
)


def _reject_misplaced_fields(
    step: Mapping[str, Any], card_type: str, executor: str, step_id: str
) -> None:
    """Reject scientific inputs the card's executor cannot consume.

    Silently discarding a user declaration (a seed on a transform, a
    program on a confgen) would pretend to honor science it drops.
    """
    if executor == "calculation":
        misplaced = sorted(set(step) & {"preset"})
    elif executor == "confgen":
        misplaced = sorted(set(step) & (_CALC_ONLY_FIELDS | {"preset"}))
    elif executor == "structure_transform":
        misplaced = sorted(set(step) & (_CALC_ONLY_FIELDS | {"program", "seed"}))
    else:
        misplaced = []
    if misplaced:
        raise _fail(
            f"step {step_id!r} ({card_type}) cannot consume fields: {', '.join(misplaced)}",
            step_id=step_id,
        )


# L1-C1: generic bindings/resources helpers live in .bindings/.resources;
# re-exported above for compatible ``compiler`` import paths (``is`` holds).


# ----------------------------------------------------------------------
# Entry point
# ----------------------------------------------------------------------


def compile_intent(
    document: Mapping[str, Any] | None = None,
    *,
    intent: Mapping[str, Any] | None = None,
    machine_profile: Mapping[str, Any] | None = None,
    registry: Any = None,
) -> dict[str, Any]:
    """Compile a simplified intent into a strict V4 wire document.

    Accepts the mapping positionally (``document``) or as ``intent=``.
    Legacy strict V4 is returned unchanged; intent v1 is expanded,
    seed-assigned, machine-resolved, checkpoint-wired, then verified with
    the real strict V4 parser and compiler before being returned.
    """
    from ...workflow.v4.compiler import compile_workflow
    from ...workflow.v4.parser import parse_workflow_document

    source = _coerce_source(document, intent)
    schema = source.get("schema")
    if schema == _WORKFLOW_SCHEMA:
        return _passthrough_legacy(source, registry)
    if schema != INTENT_SCHEMA:
        raise _fail(
            f"intent schema must be {INTENT_SCHEMA!r} (or a legacy {_WORKFLOW_SCHEMA!r} "
            f"document), got {schema!r}"
        )
    unknown_top = sorted(set(source) - _TOP_KEYS)
    if unknown_top:
        raise _fail(f"intent carries unknown members: {', '.join(unknown_top)}")

    inputs = _normalize_inputs(source.get("inputs"))
    globals_map = _normalize_globals(source.get("globals"))
    raw_steps = source.get("steps")
    recipe_id = source.get("recipe")
    named_cards = _normalize_cards(source.get("cards"))
    resolved_cards = _resolve_named_cards(named_cards)
    role_cards = _normalize_role_cards(source.get("role_cards"))
    recipe_cards_raw = _normalize_recipe_cards(source.get("recipe_cards"))
    if (role_cards or recipe_cards_raw) and recipe_id is None:
        raise _fail("intent 'role_cards'/'recipe_cards' need a 'recipe' base chain")
    generated_role_cards: dict[str, str] = {}
    if recipe_cards_raw:
        if recipe_id is None:
            raise _fail("intent 'recipe_cards' needs a 'recipe' base chain")
        generated_role_cards = _recipe_cards_to_role_cards(
            recipe_cards_raw, recipe_id=str(recipe_id).strip()
        )
        for _key, _name in generated_role_cards.items():
            if _name not in resolved_cards:
                raise _fail(
                    f"recipe_cards reference unknown card {_name!r}",
                    field_path="recipe_cards",
                )
    if generated_role_cards:
        merged_role_cards = dict(generated_role_cards)
        merged_role_cards.update(role_cards)
        role_cards = merged_role_cards
    if role_cards:
        unknown_refs = sorted({name for name in role_cards.values() if name not in resolved_cards})
        if unknown_refs:
            raise _fail(
                f"role_cards reference unknown cards: {', '.join(unknown_refs)}",
                field_path="role_cards",
            )

    # Recipe mode: reviewed base chain plus user assignments.
    base_wire_steps: list[dict[str, Any]] = []
    user_raw: list[Mapping[str, Any]] = []
    if recipe_id is not None:
        if not isinstance(recipe_id, str) or not recipe_id.strip():
            raise _fail("intent 'recipe' must be a non-empty recipe id")
        try:
            from ..recipes import get_recipe_v4
        except ImportError as exc:
            raise _fail(f"intent recipe {recipe_id!r} needs producer.recipes: {exc}") from exc
        try:
            recipe = get_recipe_v4(recipe_id.strip())
        except KeyError as exc:
            raise _fail(f"unknown intent recipe {recipe_id!r}") from exc
        base_document = recipe.get("document", {})
        for base_step in base_document.get("steps", []):
            if isinstance(base_step, Mapping):
                base_wire_steps.append(copy.deepcopy(dict(base_step)))
        if isinstance(base_document.get("inputs"), Mapping) and source.get("inputs") is None:
            inputs = copy.deepcopy(dict(base_document["inputs"]))
        # No neutral scientific fallback: the reviewed catalog ships
        # charge 0 / multiplicity 1 only so its document compiles, but a
        # new intent must declare its own state.  Guessing neutral would
        # hide validation, so an absent 'globals' stays absent here and
        # the explicit-state check below (or the strict compiler) refuses.
        if raw_steps is not None:
            if not isinstance(raw_steps, (list, tuple)):
                raise _fail("intent 'steps' must be a list")
            user_raw = [item for item in raw_steps if isinstance(item, Mapping)]
            if len(user_raw) != len(list(raw_steps)):
                raise _fail("intent 'steps' entries must be mappings")
        if source.get("globals") is None:
            has_typed_state = any(
                isinstance(declaration, Mapping)
                and (
                    declaration.get("charge") is not None
                    or declaration.get("multiplicity") is not None
                )
                for declaration in inputs.values()
            )
            if not has_typed_state and not globals_map:
                raise _fail(
                    "recipe intents require explicit 'globals' charge/multiplicity "
                    "(or typed per-input charge/multiplicity); the catalog "
                    "neutral fallback is never inherited",
                    field_path="globals",
                )
    else:
        if not isinstance(raw_steps, (list, tuple)) or not raw_steps:
            raise _fail("intent must declare a non-empty 'steps' list")
        user_raw = [item for item in raw_steps if isinstance(item, Mapping)]
        if len(user_raw) != len(list(raw_steps)):
            raise _fail("intent 'steps' entries must be mappings")

    for raw in user_raw:
        _validate_step_keys(raw)
    expanded_raw = [_expand_named_step(dict(item), resolved_cards) for item in user_raw]
    user_steps = _allocate_ids(expanded_raw)

    if registry is None:
        try:
            from ...execution.registry import default_registry
        except ImportError as exc:
            raise _fail(f"cannot load the execution registry: {exc}") from exc
        registry = default_registry()

    # Patch recipe base steps with user assignments keyed by id.
    wire_steps: list[dict[str, Any]] = [copy.deepcopy(step) for step in base_wire_steps]
    base_by_id = {str(step.get("id")): step for step in wire_steps if isinstance(step, dict)}
    appended: list[dict[str, Any]] = []
    matched: set[str] = set()
    for user in user_steps:
        explicit_id = str(user["id"])
        if explicit_id in base_by_id:
            base_calc = base_by_id[explicit_id].get("calculation")
            base_role: str | None = None
            if isinstance(base_calc, Mapping):
                raw_role = base_calc.get("role")
                base_role = raw_role if isinstance(raw_role, str) else None
            if user.get("native") is None and isinstance(user.get("native_by_role"), Mapping):
                user = {
                    **user,
                    "native": _select_family_native(
                        {"native_by_role": user["native_by_role"]},
                        base_role,
                        context=f"step {explicit_id!r}",
                    ),
                }
            _require_recipe_assignment(user, base_by_id[explicit_id], explicit_id)
            _patch_recipe_step(base_by_id[explicit_id], user, explicit_id)
            if isinstance(user.get("_named_card"), str):
                base_by_id[explicit_id]["_named_card"] = user["_named_card"]
                _annotations = dict(base_by_id[explicit_id].get("annotations") or {})
                _resolution = dict(_annotations.get("producer_resolution") or {})
                _resolution["named_card"] = user["_named_card"]
                _annotations["producer_resolution"] = _resolution
                base_by_id[explicit_id]["annotations"] = _annotations
            matched.add(explicit_id)
        else:
            appended.append(user)
    if base_wire_steps and role_cards:
        covered = _apply_role_cards(
            wire_steps,
            role_cards,
            resolved_cards,
            skip_ids=set(matched),
            recipe_id=str(recipe_id).strip() if recipe_id is not None else None,
        )
        matched = set(matched) | set(covered)
    if base_wire_steps:
        missing = sorted(
            str(step.get("id"))
            for step in base_wire_steps
            if isinstance(step.get("calculation"), Mapping) and str(step.get("id")) not in matched
        )
        if missing:
            raise _fail(
                "recipe requires explicit program/native assignments for every "
                f"calculation step (no demo science); missing: {', '.join(missing)}"
            )

    # Expand appended (and recipe-free) intent steps into wire steps.
    ordered_ids: list[str] = []
    by_id: dict[str, dict[str, Any]] = {}
    wire_executors: dict[str, str] = {}
    wire_adapters: dict[str, str | None] = {}
    checkpoint_intents: dict[str, dict[str, Any]] = {}
    for step in wire_steps:
        # Recipe base steps (patched or pristine) are already strict wire.
        step_id = str(step.get("id"))
        if step_id in by_id:
            raise _fail(f"duplicate step id {step_id!r}", step_id=step_id)
        ordered_ids.append(step_id)
        by_id[step_id] = step
        wire_executors[step_id] = str(step.get("executor"))
        calculation = step.get("calculation")
        adapter = None
        if isinstance(calculation, Mapping):
            adapter = calculation.get("execution_adapter")
        wire_adapters[step_id] = adapter
        step.setdefault("_card_type", None)
        step.setdefault("_card_version", CARD_VERSION)

    expand_list = appended if base_wire_steps else user_steps
    for user in expand_list:
        step_id = str(user["id"])
        # Family template without an explicit step native: select the
        # step-role variant verbatim (no keyword editing).
        if user.get("native") is None and isinstance(user.get("native_by_role"), Mapping):
            role_hint = user.get("role")
            if not isinstance(role_hint, str) or not role_hint.strip():
                try:
                    _ft, _fv = parse_card_ref(user.get("card"))
                except ValueError:
                    _ft = ""
                try:
                    role_hint = get_card(_ft).get("default_role") if _ft else None
                except ValueError:
                    role_hint = None
            user = {
                **user,
                "native": _select_family_native(
                    {"native_by_role": user["native_by_role"]},
                    role_hint if isinstance(role_hint, str) else None,
                    context=f"step {step_id!r}",
                ),
            }
        try:
            card_type, card_version = parse_card_ref(user.get("card"))
        except ValueError as exc:
            raise _fail(str(exc), step_id=step_id) from exc
        card = get_card(card_type, card_version)
        executor = str(card["executor"])
        _reject_misplaced_fields(user, card_type, executor, step_id)
        wire: dict[str, Any] = {"id": step_id, "executor": executor}
        if user.get("label") is not None:
            if not isinstance(user["label"], str):
                raise _fail(f"step {step_id!r} label must be a string", step_id=step_id)
            wire["label"] = user["label"]
        if executor == "calculation":
            wire["calculation"] = _wire_calculation(user, card, step_id)
        elif executor == "confgen":
            wire["confgen"] = _wire_confgen(user, step_id)
        elif executor == "structure_transform":
            transform = _wire_transform(user, card, step_id)
            preset_name = str(transform.pop("_preset"))
            wire["transform"] = transform
            wire["_preset_ref"] = preset_name
        else:
            raise _fail(f"step {step_id!r}: card executor {executor!r} is not servable")
        resources = _wire_resources(user, step_id)
        if resources is not None:
            wire["resources"] = resources
        scheduler = _wire_scheduler(user, step_id)
        if scheduler is not None:
            wire["scheduler"] = scheduler
        if "bindings" in user:
            if not isinstance(user["bindings"], Mapping):
                raise _fail(f"step {step_id!r} bindings must be a mapping", step_id=step_id)
            wire["bindings"] = copy.deepcopy(dict(user["bindings"]))
        if user.get("from") is not None:
            if not isinstance(user["from"], str) or not user["from"].strip():
                raise _fail(
                    f"step {step_id!r} 'from' must be a non-empty reference", step_id=step_id
                )
            wire["_from"] = user["from"].strip()
        if user.get("execution") is not None:
            if not isinstance(user["execution"], Mapping):
                raise _fail(f"step {step_id!r} execution must be a mapping", step_id=step_id)
            wire["execution"] = copy.deepcopy(dict(user["execution"]))
        reuse = user.get("reuse_checkpoint")
        if reuse is not None:
            if not isinstance(reuse, Mapping):
                raise _fail(f"step {step_id!r} reuse_checkpoint must be a mapping", step_id=step_id)
            source_ref = reuse.get("step")
            mode = reuse.get("mode")
            if not isinstance(source_ref, str) or not source_ref.strip():
                raise _fail(
                    f"step {step_id!r} reuse_checkpoint needs an explicit step",
                    step_id=step_id,
                )
            if mode not in _CHECKPOINT_MODES:
                raise _fail(
                    f"step {step_id!r} reuse_checkpoint mode must be one of "
                    f"{list(_CHECKPOINT_MODES)} (no route guessing)",
                    step_id=step_id,
                )
            allow_change = reuse.get("allow_method_change", False)
            if not isinstance(allow_change, bool):
                raise _fail(
                    f"step {step_id!r} reuse_checkpoint allow_method_change must be boolean",
                    step_id=step_id,
                )
            checkpoint_intents[step_id] = {
                "step": source_ref.strip(),
                "mode": mode,
                "allow_method_change": bool(allow_change),
            }
        wire["_card_type"] = card_type
        wire["_card_version"] = card_version
        wire["_role_default"] = card.get("default_role")
        if isinstance(user.get("_named_card"), str):
            wire["_named_card"] = user["_named_card"]
        by_id[step_id] = wire
        ordered_ids.append(step_id)
        wire_executors[step_id] = executor
        calculation = wire.get("calculation")
        adapter_name: str | None = None
        if isinstance(calculation, Mapping):
            adapter_name = calculation.get("execution_adapter")
        wire_adapters[step_id] = adapter_name

    _auto_bindings(ordered_ids, by_id, wire_executors, wire_adapters, inputs, registry)

    wire_document: dict[str, Any] = {
        "schema": _WORKFLOW_SCHEMA,
        "inputs": copy.deepcopy(inputs),
        "global": {"scientific_defaults": copy.deepcopy(globals_map)},
        "steps": [],
    }
    for step_id in ordered_ids:
        wire = by_id[step_id]
        public = {
            key: copy.deepcopy(value) for key, value in wire.items() if not key.startswith("_")
        }
        wire_document["steps"].append(public)

    # Machine profile: operational resolution (resources lane).
    machine_prov: dict[str, dict[str, Any]] = {}
    if machine_profile is not None:
        machine_prov = _apply_machine_profile(wire_document["steps"], machine_profile)

    # Checkpoints: semantic edges after the full wire exists.  The helper
    # compiles through the strict compiler, which refuses stochastic steps
    # without seeds — but seeds assign after checkpoints because edges move
    # science.  Materialize provisional seeds to enable helper compilation,
    # then strip only the automatically derived ones and re-derive from the
    # final science; explicit seeds are never touched.
    if checkpoint_intents:
        from ..seeds import assign_seeds as _provisional_assign

        _prov_doc, _prov_prov = _provisional_assign(wire_document)
        _auto_ids = {
            str(step_id)
            for step_id, record in _prov_prov.items()
            if isinstance(record, Mapping) and record.get("source") == "derived"
        }
        if _auto_ids:
            _prov_by_id = {
                str(item.get("id")): item
                for item in _prov_doc.get("steps", [])
                if isinstance(item, Mapping)
            }
            for _step in wire_document["steps"]:
                if not isinstance(_step, dict):
                    continue
                _sid = str(_step.get("id"))
                if _sid not in _auto_ids:
                    continue
                _prov_step = _prov_by_id.get(_sid, {})
                if not isinstance(_prov_step, Mapping):
                    continue
                for _block_name in ("calculation", "confgen"):
                    _prov_block = _prov_step.get(_block_name)
                    _wire_block = _step.get(_block_name)
                    if (
                        isinstance(_prov_block, Mapping)
                        and isinstance(_wire_block, dict)
                        and "seed" in _prov_block
                    ):
                        _wire_block["seed"] = copy.deepcopy(_prov_block["seed"])
        _apply_checkpoints(wire_document, checkpoint_intents)
        if _auto_ids:
            for _step in wire_document["steps"]:
                if not isinstance(_step, dict):
                    continue
                if str(_step.get("id")) not in _auto_ids:
                    continue
                for _block_name in ("calculation", "confgen"):
                    _wire_block = _step.get(_block_name)
                    if isinstance(_wire_block, dict) and "seed" in _wire_block:
                        del _wire_block["seed"]

    # Seeds: whole-workflow scientific identity (Phase 2).
    from ..seeds import SEED_VERSION, assign_seeds, seed_identity_for_step

    wire_document, seed_prov = assign_seeds(wire_document)

    def _seed_scope(step: Mapping[str, Any], source: str) -> str | None:
        if source == "none":
            return None
        executor = step.get("executor")
        if executor == "confgen":
            return "native_sampling"
        if executor == "calculation":
            calculation = step.get("calculation")
            native = calculation.get("native") if isinstance(calculation, Mapping) else None
            if isinstance(native, Mapping) and native.get("goat") is not None:
                return "workflow_identity_only"
        return None

    # Provenance into the valid nonsemantic container.  Existing
    # authoring keys (role_card/named_card/recipe_assignment from the
    # recipe lanes) are preserved; new fields only add.
    for step in wire_document["steps"]:
        if not isinstance(step, dict):
            continue
        step_id = str(step.get("id"))
        card_type = by_id.get(step_id, {}).get("_card_type")
        card_version = by_id.get(step_id, {}).get("_card_version", CARD_VERSION)
        preset_ref = by_id.get(step_id, {}).get("_preset_ref")
        named_ref = by_id.get(step_id, {}).get("_named_card")
        annotations = dict(step.get("annotations") or {})
        existing_resolution = dict(annotations.get("producer_resolution") or {})
        source_name = str(seed_prov.get(step_id, {}).get("source", "none"))
        try:
            import hashlib

            from ...domain.canonical import canonical_json_bytes

            _identity = seed_identity_for_step(step_id, wire_document)
            _identity_digest = (
                "sha256:" + hashlib.sha256(canonical_json_bytes(_identity)).hexdigest()
            )
        except Exception:
            _identity_digest = None
        resolution: dict[str, Any] = dict(existing_resolution)
        resolution.update(
            {
                "intent_schema": INTENT_SCHEMA,
                "card": card_type,
                "card_version": card_version,
                "preset": preset_ref,
                "preset_version": PRESET_VERSION if preset_ref else None,
                "seed_version": SEED_VERSION,
                "seed_source": source_name,
                "seed_scope": _seed_scope(step, source_name),
                "seed_identity_digest": _identity_digest,
                "machine_profile": (
                    copy.deepcopy(machine_profile.get("name"))
                    if isinstance(machine_profile, Mapping)
                    else None
                ),
            }
        )
        if named_ref is not None and resolution.get("named_card") is None:
            resolution["named_card"] = named_ref
        if step_id in machine_prov:
            resolution["machine_provenance"] = copy.deepcopy(machine_prov[step_id])
        annotations["producer_resolution"] = resolution
        step["annotations"] = annotations

    # Strict verification through the real authority (no second runtime).
    try:
        parsed = parse_workflow_document(wire_document)
    except Exception as exc:
        raise _fail(f"intent wire failed strict parsing: {exc}") from exc
    if parsed.definition is None:
        details = (
            "; ".join(
                f"{item.code}@{item.field_path or item.step_id or '?'}: {item.message}"
                for item in parsed.diagnostics
                if item.is_error
            )
            or "strict parsing failed"
        )
        raise _fail(
            f"intent wire failed strict parsing: {details}",
            diagnostics=tuple(parsed.diagnostics),
        )
    try:
        compiled = compile_workflow(parsed, registry=registry)
    except Exception as exc:
        raise _fail(f"intent wire failed strict compilation: {exc}") from exc
    if not compiled.ok:
        details = (
            "; ".join(
                f"{item.code}@{item.field_path or item.step_id or '?'}: {item.message}"
                for item in compiled.diagnostics
                if item.is_error
            )
            or "strict compilation failed"
        )
        raise _fail(
            f"intent wire failed strict compilation: {details}",
            diagnostics=tuple(compiled.diagnostics),
        )
    return copy.deepcopy(wire_document)


def _require_recipe_assignment(
    user: Mapping[str, Any], base: Mapping[str, Any], step_id: str
) -> None:
    """Require explicit science for a recipe calculation assignment.

    Reviewed recipes ship demo program/native placeholders so the catalog
    stays compilable; an intent that selects a recipe must replace them on
    every calculation step with the user's own explicit program and native
    mapping.  Keywords are never transformed or guessed from method text.
    """
    if not isinstance(base.get("calculation"), Mapping):
        return
    if user.get("program") is None or user.get("native") is None:
        raise _fail(
            f"step {step_id!r}: recipe assignments require explicit program and native "
            "(demo recipe science must not leak into production)",
            step_id=step_id,
        )


def _patch_recipe_step(patched: dict[str, Any], user: Mapping[str, Any], step_id: str) -> None:
    """Apply user card assignments onto one reviewed recipe step in place."""
    patched["_expanded"] = True
    calculation = patched.get("calculation")
    if not isinstance(calculation, Mapping):
        return
    calculation = copy.deepcopy(dict(calculation))
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
    patched["calculation"] = calculation
    if user.get("resources") is not None:
        patched["resources"] = copy.deepcopy(dict(user["resources"]))
    if user.get("scheduler") is not None:
        patched["scheduler"] = copy.deepcopy(dict(user["scheduler"]))
    annotations = dict(patched.get("annotations") or {})
    resolution = dict(annotations.get("producer_resolution") or {})
    resolution["intent_schema"] = INTENT_SCHEMA
    resolution["recipe_assignment"] = True
    annotations["producer_resolution"] = resolution
    patched["annotations"] = annotations
