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
from collections.abc import Mapping
from typing import Any

from ..cards import CARD_TYPES, CARD_VERSION, get_card, parse_card_ref
from ..presets import PRESET_VERSION, get_preset
from .bindings import _auto_bindings, _registry_input_ports  # noqa: F401
from .common import IntentCompilationError, _fail  # noqa: F401
from .recipes import _CARD_DEF_KEYS, _require_identifier
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


def __getattr__(name: str) -> Any:
    """Lazy compat for L1-C2 capability moves (no top-level handler import).

    Old ``confflow.producer.intent.compiler`` private paths stay observable
    with identical objects (``is`` holds) and old ``__module__``.  Handlers
    load only when these attributes are accessed, so schema/catalog imports
    stay pure (no handler/solver load, no side effects).
    """
    if name in ("_resolve_program", "_wire_calculation"):
        from .capabilities.calculation import _resolve_program as _rp
        from .capabilities.calculation import _wire_calculation as _wc

        return {"_resolve_program": _rp, "_wire_calculation": _wc}[name]
    if name in (
        "_LEGACY_PATH_SCOPE_KEYS",
        "_LEGACY_PATH_KEYS",
        "_LEGACY_DEFAULT_PATH_STEP",
        "_legacy_paths_to_v3",
        "_wire_confgen",
    ):
        from .capabilities import confgen as _cg

        return getattr(_cg, name)
    if name == "_wire_transform":
        from .capabilities.transform import _wire_transform as _wt

        return _wt
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


#: Accepted simplified intent schema id.
INTENT_SCHEMA: str = "confflow.intent.v1"

#: Strict V4 wire schema id (legacy passthrough target).
_WORKFLOW_SCHEMA: str = "confflow.workflow.v4"

#: Default run input when the intent declares no inputs.
_DEFAULT_INPUTS: dict[str, Any] = {
    "structures": {"kind": "structure", "cardinality": "many", "grouping": "each_entity"}
}

_CHECKPOINT_MODES: tuple[str, ...] = ("checkpoint", "readfc")

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
                "Retired in R2.2 with the 'tspes' recipe: no recipe accepts "
                "the normal mode anymore, and declaring 'recipe_cards' "
                "fails closed. Explicit 'role_cards' is the only shortcut."
            ),
        },
        "configuration_docs": {
            "intent_schema": INTENT_SCHEMA,
            "workflow_schema": _WORKFLOW_SCHEMA,
        },
    }


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


def _extract_card_type_for_alloc(card_ref: Any, intent_registry: Any | None) -> str | None:
    """Extract a card type for id allocation via the explicit registry first.

    Returns the registry-hit key, or ``None`` when no custom hit applies so
    the caller falls back to the legacy ``parse_card_ref`` failure path
    (preserving old messages when no custom key exists).
    """
    if intent_registry is None:
        return None
    try:
        resolve = intent_registry.resolve
    except AttributeError:
        return None
    candidate: str | None = None
    if isinstance(card_ref, Mapping):
        raw_type = card_ref.get("type")
        if isinstance(raw_type, str) and raw_type.strip():
            candidate = raw_type.strip()
    elif isinstance(card_ref, str) and "@" in card_ref:
        head, _, _ = card_ref.partition("@")
        if head.strip():
            candidate = head.strip()
    if candidate is None:
        return None
    try:
        hit = resolve(candidate)
    except Exception:
        return None
    return candidate if hit is not None else None


def _allocate_ids(
    raw_steps: list[Mapping[str, Any]], intent_registry: Any | None = None
) -> list[dict[str, Any]]:
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
        custom_type = _extract_card_type_for_alloc(step.get("card"), intent_registry)
        if custom_type is not None:
            card_type = custom_type
        else:
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
    from .recipes import normalize_cards as _new

    return _new(raw)


def _resolve_named_cards(raw_cards: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
    from .recipes import resolve_named_cards as _new

    return _new(raw_cards, parse_ref_fn=parse_card_ref)


def _expand_named_step(
    step: Mapping[str, Any], resolved_cards: Mapping[str, dict[str, Any]]
) -> dict[str, Any]:
    from .recipes import expand_named_step as _new

    return _new(step, resolved_cards)


def _select_family_native(
    template: Mapping[str, Any], role: str | None, *, context: str
) -> dict[str, Any] | None:
    from .recipes import select_family_native as _new

    return _new(template, role, context=context)


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
    from .recipes import recipe_cards_to_role_cards as _new

    return _new(recipe_cards, recipe_id=recipe_id)


def _expected_purpose(step_id: str, base_role: str | None, *, recipe_id: str | None) -> str | None:
    from .recipes import expected_purpose as _new

    return _new(step_id, base_role, recipe_id=recipe_id)


def _apply_role_cards(
    wire_steps: list[dict[str, Any]],
    role_cards: Mapping[str, str],
    resolved_cards: Mapping[str, dict[str, Any]],
    *,
    skip_ids: set[str],
    recipe_id: str | None = None,
) -> set[str]:
    from .capabilities.registry import build_default_intent_registry
    from .capabilities.registry import role_block_for_executor as _role_block
    from .capabilities.registry import wire_block_key_for_executor as _wkey

    _registry = build_default_intent_registry()

    def _wire_key_of(_executor: str) -> str | None:
        return _wkey(_registry, _executor)

    def _role_block_of(_executor: str) -> Any | None:
        return _role_block(_registry, _executor)

    _card_access: dict[str, Any] = {
        "parse_ref": parse_card_ref,
        "get_card": get_card,
        "card_types": CARD_TYPES,
        "card_version": CARD_VERSION,
        "intent_schema": INTENT_SCHEMA,
    }
    from .recipes import apply_role_cards as _new

    return _new(
        wire_steps,
        role_cards,
        resolved_cards,
        skip_ids=skip_ids,
        recipe_id=recipe_id,
        role_block_of=_role_block_of,
        card_access=_card_access,
        wire_key_of=_wire_key_of,
    )


# ----------------------------------------------------------------------
# Capability card resolution (L1-C2, generic; science stays in handlers)
# ----------------------------------------------------------------------


def _parse_card_ref_with_registry(
    ref: object, intent_registry: object | None, step_id: str
) -> tuple[str, str]:
    """Parse a card ref, consulting the explicit registry before rejection.

    Custom keys hit the registry and return immediately; otherwise the
    legacy ``parse_card_ref`` path runs unchanged so default failures keep
    their old messages.  ``step_id`` is only used for error context by the
    caller (this helper raises raw ``ValueError`` like the legacy parser).
    """
    if intent_registry is not None:
        try:
            resolve = intent_registry.resolve  # type: ignore[attr-defined]
        except AttributeError:
            resolve = None
        else:
            candidate: str | None = None
            version: str | None = None
            if isinstance(ref, Mapping):
                unknown = sorted(set(ref) - {"type", "version"})
                if not unknown:
                    raw_type = ref.get("type")
                    raw_version = ref.get("version")
                    if isinstance(raw_type, str) and raw_type.strip():
                        candidate = raw_type.strip()
                    if isinstance(raw_version, str) and raw_version.strip():
                        version = raw_version.strip()
            elif isinstance(ref, str) and "@" in ref:
                head, _, tail = ref.partition("@")
                if head.strip() and tail.strip():
                    candidate = head.strip()
                    version = tail.strip()
            if candidate is not None and version is not None:
                try:
                    hit = resolve(candidate)
                except Exception:
                    hit = None
                if hit is not None:
                    if version != CARD_VERSION:
                        raise ValueError(
                            f"unsupported card version {version!r}; "
                            f"this producer serves {CARD_VERSION!r}"
                        )
                    return candidate, version
    # Legacy path (default 14 cards + all old failure messages).
    return parse_card_ref(ref)


def _resolve_card_and_entry(
    ref: object, intent_registry: object, step_id: str
) -> tuple[str, str, dict[str, object], object]:
    """Resolve ``(card_type, version, card_dict, entry)`` via the registry.

    Custom keys return the descriptor-owned thawed card; default keys return
    thawed copies equal to ``get_card`` (lists stay lists).  Unknown keys
    fall through to the legacy ``parse_card_ref``/``get_card`` failure path
    so default errors are byte-identical.
    """
    card_type, card_version = _parse_card_ref_with_registry(ref, intent_registry, step_id)
    try:
        resolve = intent_registry.resolve  # type: ignore[attr-defined]
    except AttributeError:
        resolve = None
    entry = None
    if resolve is not None:
        try:
            entry = resolve(card_type)
        except Exception:
            entry = None
    if entry is not None:
        try:
            card = entry.card_dict()  # type: ignore[attr-defined]
        except AttributeError:
            import copy as _copy

            card = _copy.deepcopy(dict(entry.card))  # type: ignore[attr-defined]
        return card_type, card_version, card, entry
    # Legacy default path (same errors as before C2).
    try:
        legacy_type, legacy_version = parse_card_ref(ref)
    except ValueError as exc:
        raise exc
    card = get_card(legacy_type, legacy_version)
    return legacy_type, legacy_version, card, None


# L1-C2: capability wire builders live in
# ``capabilities/{calculation,confgen,transform}.py`` (mechanical moves);
# re-exported lazily via ``__getattr__`` above for compatible ``compiler``
# import paths (``is`` holds, old ``__module__`` kept).  R2.3a: the
# ``capabilities/analysis.py`` test-local handler is retired with
# ``confflow.analysis``; analysis executors stay fail-closed on the
# default registry through the ``None``/unknown path below.


# Canonical R1 sources: ``capabilities/{calculation,confgen,transform}.py``
# ``REJECTED_STEP_KEYS`` (verbatim old branches).  This set is kept for
# compatible import paths and equals ``confgen REJECTED - {"preset"}`` ==
# ``transform REJECTED - {"seed"}`` (locked by test); new code reads the
# descriptor value, never branches on executor here.
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

#: Generic wire fields owned by the compiler (workflow syntax boundary).
#: Capability fragments must never contain these; the compiler merges only
#: descriptor-declared fragment keys.  ``_preset_ref`` is deliberately NOT
#: reserved: it is capability-owned private metadata declared in the
#: transform descriptor's ``fragment_keys`` and carried generically.
_RESERVED_FRAGMENT_KEYS = frozenset(
    {
        "id",
        "executor",
        "label",
        "resources",
        "scheduler",
        "bindings",
        "_from",
        "execution",
        "reuse_checkpoint",
        "_card_type",
        "_card_version",
        "_role_default",
        "_named_card",
    }
)


def _effective_wire_block_key(entry: Any) -> str | None:
    """Return the descriptor effective wire-block key (total, never raises).

    Explicit non-empty ``wire_block_key`` wins; empty derives from
    ``fragment_keys[0]`` (C2 compat).  No executor hardcoding here.
    """
    try:
        explicit = getattr(entry, "wire_block_key", "")
    except Exception:
        explicit = ""
    if isinstance(explicit, str) and explicit:
        return explicit
    try:
        frag = tuple(getattr(entry, "fragment_keys", ()) or ())
    except Exception:
        return None
    return frag[0] if frag and isinstance(frag[0], str) else None


def _adapter_from_wire(step: Mapping[str, Any], block_key: Any) -> str | None:
    """Read ``execution_adapter`` from one wire block (total, never raises)."""
    if not isinstance(block_key, str) or not block_key:
        return None
    try:
        block = step.get(block_key)
    except Exception:
        return None
    if isinstance(block, Mapping):
        try:
            return block.get("execution_adapter")  # type: ignore[return-value]
        except Exception:
            return None
    return None


def _wire_block_key_for_executor(intent_registry: Any, executor: str) -> str | None:
    """Resolve the wire-block key by executor via the assembly point.

    The compiler never hardcodes executor->block names; the mapping lives in
    ``capabilities/registry.py`` (builtin defaults + per-executor consistency).
    Unknown executors yield ``None`` (old unknown-executor empty set
    analogue for adapters). Same-executor conflicts raise fail-closed
    (never silent ``sorted-first``); assembly import failures also raise.
    """
    from .capabilities.registry import wire_block_key_for_executor as _helper

    return _helper(intent_registry, executor)


def _reject_misplaced_fields(
    step: Mapping[str, Any], card_type: str, executor: str, step_id: str, entry: Any = None
) -> None:
    """Reject scientific inputs the card's executor cannot consume.

    Silently discarding a user declaration (a seed on a transform, a
    program on a confgen) would pretend to honor science it drops.

    R1: the rejected set comes from the descriptor (``entry``) when given;
    the 4-positional-arg form delegates to the registry assembly helper
    owning the builtin table (unknown executors stay empty).  Callers pass
    the resolved ``entry``; validation stays after card resolve and before
    the handler (call-site order unchanged).  Assembly/import failures
    fail closed and never collapse into an empty set.
    """
    rejected: Any = ()
    if entry is not None:
        try:
            rejected = getattr(entry, "rejected_step_keys", ()) or ()
        except Exception:
            rejected = ()
    else:
        from .capabilities.registry import (
            rejected_step_keys_for_legacy_fallback as _legacy_rejected,
        )

        try:
            rejected = _legacy_rejected(executor)
        except IntentCompilationError:
            raise
        except Exception as exc:
            raise _fail(
                f"step {step_id!r} ({card_type}) rejected-metadata unavailable: {exc}",
                step_id=step_id,
            ) from exc
    try:
        misplaced = sorted(set(step) & set(rejected))
    except Exception:
        misplaced = []
    if misplaced:
        raise _fail(
            f"step {step_id!r} ({card_type}) cannot consume fields: {', '.join(misplaced)}",
            step_id=step_id,
        )


# L1-C1: generic bindings/resources helpers live in .bindings/.resources;
# re-exported above for compatible ``compiler`` import paths (``is`` holds).


# L1-A2a compat: the seed-scope rule lives in ``producer.seeds``;
# this wrapper preserves the old ``compiler._seed_scope`` import path
# without reintroducing executor knowledge here (generic delegation only).
def _seed_scope(step: Mapping[str, Any], source: str) -> str | None:
    """Delegate to :func:`producer.seeds.seed_scope_for_step` (compat)."""
    from ..seeds import seed_scope_for_step as _delegate

    return _delegate(step, source)


# ----------------------------------------------------------------------
# Entry point
# ----------------------------------------------------------------------


def compile_intent(
    document: Mapping[str, Any] | None = None,
    *,
    intent: Mapping[str, Any] | None = None,
    machine_profile: Mapping[str, Any] | None = None,
    registry: Any = None,
    intent_registry: Any = None,
) -> dict[str, Any]:
    """Compile a simplified intent into a strict V4 wire document.

    Accepts the mapping positionally (``document``) or as ``intent=``.
    ``registry`` keeps its existing meaning (``None`` loads the default
    execution registry once); ``intent_registry`` is the explicit capability
    registry (``None`` builds the default once and the same instance flows
    explicitly to every helper).  Legacy strict V4 is returned unchanged;
    intent v1 is expanded,
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
    if intent_registry is None:
        try:
            from .capabilities.registry import build_default_intent_registry
        except ImportError as exc:
            raise _fail(f"cannot load the intent registry: {exc}") from exc
        intent_registry = build_default_intent_registry()
    # L1-A3a explicit runtime binding: the same ExecutionRegistry instance
    # must flow through card/compile/authoring/runtime.  A bound intent
    # registry reuses its instance when ``registry`` is omitted (never a
    # silent second default); a supplied different instance fails closed.
    try:
        _bound_execution = getattr(intent_registry, "execution_registry", None)
    except Exception:
        _bound_execution = None
    if _bound_execution is not None:
        if registry is not None and registry is not _bound_execution:
            raise _fail(
                "intent registry is bound to a different execution registry instance; "
                "pass the bound instance explicitly (no silent second default)",
            )
        if registry is None:
            registry = _bound_execution
    user_steps = _allocate_ids(expanded_raw, intent_registry)

    if registry is None:
        try:
            from ...execution.registry import default_registry
        except ImportError as exc:
            raise _fail(f"cannot load the execution registry: {exc}") from exc
        registry = default_registry()

    # Patch recipe base steps with user assignments keyed by id (L1-A2b1:
    # generic hook dispatch by wire executor; no executor literal branch here.
    # The same explicit intent_registry instance flows to every helper;
    # public wrappers keep their 3-param defaults via the default registry.)
    from .recipes import missing_recipe_assignments as _missing_generic
    from .recipes import run_recipe_lane as _run_recipe_lane
    from .recipes import select_family_native as _select_new

    def _hooks_of(_executor: str) -> tuple[Any, Any] | None:
        from .capabilities.registry import recipe_hooks_for_executor as _query

        return _query(intent_registry, _executor)

    def _wire_key_of(_executor: str) -> str | None:
        return _wire_block_key_for_executor(intent_registry, _executor)

    wire_steps, base_by_id, matched, appended = _run_recipe_lane(
        base_wire_steps,
        user_steps,
        hooks_of=_hooks_of,
        wire_key_of=_wire_key_of,
        select_family_native_fn=_select_new,
    )
    if base_wire_steps and role_cards:
        from .capabilities.registry import role_block_for_executor as _role_block_new
        from .recipes import apply_role_cards as _apply_new

        _card_access: dict[str, Any] = {
            "parse_ref": parse_card_ref,
            "get_card": get_card,
            "card_types": CARD_TYPES,
            "card_version": CARD_VERSION,
            "intent_schema": INTENT_SCHEMA,
        }

        def _role_block_of(_executor: str) -> Any | None:
            return _role_block_new(intent_registry, _executor)

        covered = _apply_new(
            wire_steps,
            role_cards,
            resolved_cards,
            skip_ids=set(matched),
            recipe_id=str(recipe_id).strip() if recipe_id is not None else None,
            role_block_of=_role_block_of,
            card_access=_card_access,
            wire_key_of=_wire_key_of,
        )
        matched = set(matched) | set(covered)
    if base_wire_steps:
        missing = _missing_generic(base_wire_steps, matched, hooks_of=_hooks_of)
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
        # Adapter is read-only data via the executor-consistent block key
        # (registry query, never a guessed card type: base steps carry no
        # ``_card_type``).  Legality stays in bindings (requires_adapter).
        step_id = str(step.get("id"))
        if step_id in by_id:
            raise _fail(f"duplicate step id {step_id!r}", step_id=step_id)
        ordered_ids.append(step_id)
        by_id[step_id] = step
        wire_executors[step_id] = str(step.get("executor"))
        wire_adapters[step_id] = _adapter_from_wire(
            step, _wire_block_key_for_executor(intent_registry, str(step.get("executor")))
        )
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
                    _ft, _fv, _fc, _fe = _resolve_card_and_entry(
                        user.get("card"), intent_registry, step_id
                    )
                except (ValueError, IntentCompilationError):
                    _ft = ""
                    _fc = {}
                else:
                    _ft = _ft
                try:
                    if isinstance(_fc, Mapping):
                        role_hint = _fc.get("default_role") if _ft else None
                    else:
                        role_hint = get_card(_ft).get("default_role") if _ft else None
                except ValueError:
                    role_hint = None
            user = {
                **user,
                "native": _select_new(
                    {"native_by_role": user["native_by_role"]},
                    role_hint if isinstance(role_hint, str) else None,
                    context=f"step {step_id!r}",
                ),
            }
        try:
            card_type, card_version, card, _entry = _resolve_card_and_entry(
                user.get("card"), intent_registry, step_id
            )
        except ValueError as exc:
            raise _fail(str(exc), step_id=step_id) from exc
        executor = str(card["executor"])
        _reject_misplaced_fields(user, card_type, executor, step_id, _entry)
        # Generic dispatch: single handler call, no executor hardcoding.
        # The same explicit registry instance flows here; a second lookup of
        # the same key/executor must return the same descriptor.
        try:
            _resolve_fn = intent_registry.resolve  # type: ignore[attr-defined]
        except AttributeError:
            _resolve_fn = None
        _entry2 = None
        if _resolve_fn is not None:
            try:
                _entry2 = _resolve_fn(card_type, executor)
            except Exception:
                _entry2 = None
        if _entry is not None and _entry2 is not None and _entry2 is not _entry:
            _entry = _entry2
        elif _entry2 is not None:
            _entry = _entry2
        if _entry is None or getattr(_entry, "intent_handler", None) is None:
            raise _fail(f"step {step_id!r}: card executor {executor!r} is not servable")
        wire: dict[str, Any] = {"id": step_id, "executor": executor}
        if user.get("label") is not None:
            if not isinstance(user["label"], str):
                raise _fail(f"step {step_id!r} label must be a string", step_id=step_id)
            wire["label"] = user["label"]
        fragment = _entry.intent_handler(user, card, step_id)  # type: ignore[attr-defined]
        if not isinstance(fragment, Mapping):
            raise _fail(
                f"step {step_id!r}: capability handler must return a mapping",
                step_id=step_id,
            )
        try:
            declared_keys = tuple(getattr(_entry, "fragment_keys", ()))
        except Exception:
            declared_keys = ()
        declared_set = set(declared_keys)
        fragment_keys = set(fragment.keys())
        if not fragment_keys or not fragment_keys <= declared_set:
            raise _fail(
                f"step {step_id!r}: capability fragment carries undeclared keys: "
                f"{sorted(fragment_keys - declared_set)}",
                step_id=step_id,
            )
        illegal = sorted(fragment_keys & set(_RESERVED_FRAGMENT_KEYS))
        if illegal:
            raise _fail(
                f"step {step_id!r}: capability fragment must not overwrite "
                f"generic fields: {', '.join(illegal)}",
                step_id=step_id,
            )
        for _fk, _fv in fragment.items():
            if _fk in wire:
                raise _fail(
                    f"step {step_id!r}: capability fragment overwrites {_fk!r}",
                    step_id=step_id,
                )
            wire[_fk] = copy.deepcopy(_fv)
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
        wire_adapters[step_id] = _adapter_from_wire(wire, _effective_wire_block_key(_entry))

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
        machine_prov = _apply_machine_profile(
            wire_document["steps"], machine_profile, intent_registry=intent_registry
        )

    # Checkpoints: semantic edges after the full wire exists.  The helper
    # compiles through the strict compiler, which refuses stochastic steps
    # without seeds — but seeds assign after checkpoints because edges move
    # science.  Materialize provisional seeds to enable helper compilation,
    # then strip only the automatically derived ones and re-derive from the
    # final science; explicit seeds are never touched.
    if checkpoint_intents:
        from ..seeds import assign_seeds as _provisional_assign
        from .capabilities.registry import seed_block_keys_for_registry as _seed_blocks

        _seed_block_names = tuple(_seed_blocks(intent_registry))
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
                for _block_name in _seed_block_names:
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
                for _block_name in _seed_block_names:
                    _wire_block = _step.get(_block_name)
                    if isinstance(_wire_block, dict) and "seed" in _wire_block:
                        del _wire_block["seed"]

    # Seeds: whole-workflow scientific identity (Phase 2).
    from ..seeds import SEED_VERSION, assign_seeds, seed_identity_for_step
    from ..seeds import seed_scope_for_step as _seed_scope

    wire_document, seed_prov = assign_seeds(wire_document)

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
    """Require explicit science for a recipe assignment (L1-A2b1 compat wrapper).

    Same 3-param signature and same error order/text as before; behavior is
    identical on all real wires.  Delegates to the generic hook from the
    default registry by wire ``executor`` (never by guessed card type, no
    component literal here): legal ``None`` hooks skip (old non-block early
    analogue); unknown/no-executor wires skip (old baseline: reviewed bases
    without hooks carry no assignment blocks).  Real ``compile_intent`` uses
    the same custom registry explicitly (no default construction inside the
    lane).  The wrapper is not the same object as the capability
    implementation (no ``is`` promise); exceptions propagate unwrapped so the
    five-tuple is byte-identical.
    """
    try:
        executor = base.get("executor") if isinstance(base, Mapping) else None
    except Exception:
        executor = None
    if not isinstance(executor, str) or not executor:
        return
    from .capabilities.registry import build_default_intent_registry
    from .capabilities.registry import recipe_hooks_for_executor as _query

    hooks = _query(build_default_intent_registry(), executor)
    if hooks is None:
        return
    require_fn, _patch_fn = hooks
    if require_fn is None:
        return
    require_fn(user, base, step_id)


def _patch_recipe_step(patched: dict[str, Any], user: Mapping[str, Any], step_id: str) -> None:
    """Apply user assignments onto one recipe step (L1-A2b1 compat wrapper).

    Same 3-param signature, same in-place ``None`` return, same error
    order/text.  Delegates to the generic orchestrator
    ``intent/recipes.py::apply_recipe_assignment`` with the block hook from
    the default registry by wire ``executor`` (no component literal here).
    Legal ``None`` hooks mark only ``_expanded`` (old early analogue).
    Real ``compile_intent`` passes the same custom registry explicitly.
    Not the same object as the new implementation (no ``is`` promise).
    """
    try:
        executor = patched.get("executor") if isinstance(patched, Mapping) else None
    except Exception:
        executor = None
    wire_key: str | None = None
    patch_fn: Any = None
    if isinstance(executor, str) and executor:
        from .capabilities.registry import build_default_intent_registry
        from .capabilities.registry import recipe_hooks_for_executor as _query2
        from .capabilities.registry import wire_block_key_for_executor as _wkey

        _registry = build_default_intent_registry()
        hooks = _query2(_registry, executor)
        if hooks is not None:
            _req, patch_fn = hooks
        wire_key = _wkey(_registry, executor)
    from .recipes import apply_recipe_assignment as _apply_generic

    _apply_generic(patched, user, step_id, patch_block_fn=patch_fn, wire_block_key=wire_key)
