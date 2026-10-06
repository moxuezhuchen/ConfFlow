#!/usr/bin/env python3

"""Explicit immutable intent registry (L1-C2).

Light boundary: only ``.descriptor`` types plus stdlib at top level.
Handler implementations are never imported here at top level; callables
are injected as parameters (explicit construction, no import-time
self-registration, no global mutation, no production probe reference).
``build_default_intent_registry()`` assembles the 14 builtin cards
explicitly with function-local lazy imports.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any

from .descriptor import CapabilityDescriptor

#: Existing runtime executor names only; no new enum is introduced here.
ALLOWED_EXECUTORS: frozenset[str] = frozenset({"calculation", "confgen", "structure_transform"})


@dataclass(frozen=True, slots=True)
class IntentRegistry:
    """Locally immutable card-key -> descriptor map (deeply frozen)."""

    entries: Mapping[str, CapabilityDescriptor]
    # L1-A3a: optional explicit runtime registry binding.  ``None`` is the
    # production default (no binding); a test-local registry built with
    # ``execution_registry=<same ExecutionRegistry>`` reuses that instance
    # in :func:`compile_intent` instead of silently building another
    # default.  Stored verbatim, never copied.
    execution_registry: Any = None

    def __post_init__(self) -> None:
        if not isinstance(self.entries, Mapping):
            raise ValueError("IntentRegistry entries must be a mapping")
        snapshot = dict(self.entries)
        for key, descriptor in snapshot.items():
            if not isinstance(key, str) or not key.strip():
                raise ValueError("IntentRegistry entry keys must be non-empty strings")
            if not isinstance(descriptor, CapabilityDescriptor):
                raise ValueError(f"IntentRegistry entry {key!r} must be a CapabilityDescriptor")
            if key != descriptor.key:
                raise ValueError(
                    f"IntentRegistry entry key {key!r} != descriptor.key {descriptor.key!r}"
                )
        object.__setattr__(self, "entries", MappingProxyType(dict(snapshot)))

    def resolve(self, card_type: str, executor: str | None = None) -> CapabilityDescriptor | None:
        """Return the descriptor for *card_type* (and *executor* if given)."""
        try:
            entry = self.entries[card_type]
        except KeyError:
            return None
        if executor is not None and entry.executor != executor:
            return None
        return entry

    def card_of(self, card_type: str) -> dict[str, Any] | None:
        """Return an isolated mutable copy of the card shape, if present."""
        entry = self.resolve(card_type)
        if entry is None:
            return None
        return entry.card_dict()


def build_intent_registry(
    descriptors: Iterable[CapabilityDescriptor],
    *,
    execution_registry: Any = None,
) -> IntentRegistry:
    """Build a locally immutable registry from explicit descriptors.

    Rejects duplicate keys (conflict), unknown executors, entries
    without a handler, and empty/duplicate fragment-key declarations.
    Only an explicit non-empty ``wire_block_key`` outside ``fragment_keys``
    is rejected (empty derives from ``fragment_keys[0]`` so C2-era custom
    descriptors stay constructible); per-executor effective wire-block keys
    must agree (fail-closed at assembly).  Rejected-key entries must be
    non-empty strings (empty tuple = no placement restriction).  No import
    side effects, no global state.

    L1-A3a explicit channel: when ``execution_registry`` is given, each
    descriptor executor is verified against that same
    :class:`ExecutionRegistry`'s actually registered contract (including
    the already-registered ``analysis`` capability) instead of the
    builtin 3-item table; no second hardcoded table is built here.
    When omitted, the legacy 3-allowed check and unknown-executor
    message bytes are preserved verbatim.  The given instance (or
    ``None``) is stored verbatim on the returned registry.
    """
    seen: dict[str, CapabilityDescriptor] = {}
    for descriptor in descriptors:
        if not isinstance(descriptor, CapabilityDescriptor):
            raise ValueError("build_intent_registry needs CapabilityDescriptor items")
        if descriptor.key in seen:
            raise ValueError(f"conflicting CapabilityDescriptor key {descriptor.key!r}")
        if execution_registry is None:
            if descriptor.executor not in ALLOWED_EXECUTORS:
                raise ValueError(
                    f"unknown executor {descriptor.executor!r} for key "
                    f"{descriptor.key!r}; expected one of {sorted(ALLOWED_EXECUTORS)}"
                )
        else:
            try:
                _resolve = getattr(execution_registry, "resolve_executor", None)
            except Exception as exc:
                raise ValueError(
                    f"CapabilityDescriptor {descriptor.key!r} runtime registry unreadable"
                ) from exc
            if not callable(_resolve):
                raise ValueError(
                    f"CapabilityDescriptor {descriptor.key!r} runtime registry unreadable"
                )
            try:
                _resolve(descriptor.executor)
            except Exception as exc:
                try:
                    _names = sorted(execution_registry.capability_names)
                except Exception:
                    _names = []
                raise ValueError(
                    f"unknown executor {descriptor.executor!r} for key "
                    f"{descriptor.key!r}; expected one of {_names}"
                ) from exc
        if descriptor.intent_handler is None:
            raise ValueError(f"CapabilityDescriptor {descriptor.key!r} carries no intent handler")
        if not descriptor.fragment_keys:
            raise ValueError(f"CapabilityDescriptor {descriptor.key!r} declares no fragment keys")
        for item in tuple(descriptor.rejected_step_keys or ()):
            if not isinstance(item, str) or not item.strip():
                raise ValueError(
                    f"CapabilityDescriptor {descriptor.key!r} carries bad rejected keys"
                )
        explicit = getattr(descriptor, "wire_block_key", "")
        if (
            isinstance(explicit, str)
            and explicit
            and explicit not in tuple(descriptor.fragment_keys)
        ):
            raise ValueError(
                f"CapabilityDescriptor {descriptor.key!r} wire_block_key "
                f"{explicit!r} not in fragment_keys {tuple(descriptor.fragment_keys)!r}"
            )
        seed_keys = tuple(getattr(descriptor, "seed_block_keys", ()) or ())
        for item in seed_keys:
            if not isinstance(item, str) or not item.strip():
                raise ValueError(
                    f"CapabilityDescriptor {descriptor.key!r} carries bad seed block keys"
                )
        if seed_keys:
            outside = sorted(set(seed_keys) - set(tuple(descriptor.fragment_keys)))
            if outside:
                raise ValueError(
                    f"CapabilityDescriptor {descriptor.key!r} seed_block_keys "
                    f"{sorted(seed_keys)!r} not in fragment_keys "
                    f"{tuple(descriptor.fragment_keys)!r}"
                )
        # L1-A2b1: recipe hooks must be callable-or-None (fail-closed, never
        # silent None).  Missing attribute derives None for C2-era compat.
        # L1-A2b2: role-card block hook follows the same rule (single hook).
        for _hook_name in ("requires_assignment", "patch_recipe_step", "apply_role_card_block"):
            try:
                _has = hasattr(descriptor, _hook_name)
            except Exception as exc:
                raise ValueError(
                    f"CapabilityDescriptor {descriptor.key!r} recipe hook unreadable"
                ) from exc
            if not _has:
                continue
            try:
                _hook = getattr(descriptor, _hook_name)
            except Exception as exc:
                raise ValueError(
                    f"CapabilityDescriptor {descriptor.key!r} recipe hook unreadable"
                ) from exc
            if _hook is not None and not callable(_hook):
                raise ValueError(
                    f"CapabilityDescriptor {descriptor.key!r} {_hook_name} must be callable or None"
                )
        seen[descriptor.key] = descriptor
    _check_executor_wire_consistency(seen)
    _check_executor_seed_consistency(seen)
    _check_executor_recipe_hooks_consistency(seen)
    _check_executor_role_block_consistency(seen)
    return IntentRegistry(entries=seen, execution_registry=execution_registry)


def _effective_wire_block_key(descriptor: CapabilityDescriptor) -> str:
    """Return the effective wire block key (explicit or ``fragment_keys[0]``).

    Strict: missing ``wire_block_key`` derives compat ``fragment_keys[0]``;
    present-but-non-string or explicit-outside-``fragment_keys`` raises
    fail-closed (never silently derived). Missing/invalid ``fragment_keys``
    also raises.
    """
    try:
        explicit = getattr(descriptor, "wire_block_key", "")
    except Exception as exc:
        raise ValueError(
            f"CapabilityDescriptor {getattr(descriptor, 'key', '?')!r} wire_block_key unreadable"
        ) from exc
    if not isinstance(explicit, str):
        raise ValueError(
            f"CapabilityDescriptor {getattr(descriptor, 'key', '?')!r} "
            f"wire_block_key must be a string, got {type(explicit).__name__}"
        )
    if explicit:
        try:
            frag = tuple(descriptor.fragment_keys)
        except Exception as exc:
            raise ValueError(
                f"CapabilityDescriptor {getattr(descriptor, 'key', '?')!r} fragment_keys unreadable"
            ) from exc
        if explicit not in frag:
            raise ValueError(
                f"CapabilityDescriptor {getattr(descriptor, 'key', '?')!r} "
                f"wire_block_key {explicit!r} not in fragment_keys {frag!r}"
            )
        return explicit
    try:
        frag = tuple(descriptor.fragment_keys)
    except Exception as exc:
        raise ValueError(
            f"CapabilityDescriptor {getattr(descriptor, 'key', '?')!r} fragment_keys unreadable"
        ) from exc
    if not frag or not isinstance(frag[0], str) or not frag[0]:
        raise ValueError(
            f"CapabilityDescriptor {getattr(descriptor, 'key', '?')!r} "
            "fragment_keys must start with a non-empty string"
        )
    return frag[0]


def _check_executor_wire_consistency(entries: Mapping[str, CapabilityDescriptor]) -> None:
    """Fail closed when one executor maps to divergent wire-block keys.

    Declared strategy: same-executor entries must share one effective key
    (explicit or derived).  Divergent metadata is an assembly error, never a
    silent compiler choice.
    """
    by_executor: dict[str, set[str]] = {}
    for descriptor in entries.values():
        by_executor.setdefault(descriptor.executor, set()).add(
            _effective_wire_block_key(descriptor)
        )
    for executor, keys in by_executor.items():
        if len(keys) != 1:
            raise ValueError(
                f"conflicting wire_block_key for executor {executor!r}: {sorted(keys)}"
            )


def _effective_seed_block_keys(descriptor: Any) -> tuple[str, ...]:
    """Return the effective seed block keys for one descriptor (strict).

    Explicit non-empty ``seed_block_keys`` wins verbatim after validation
    (non-string/duplicate/outside-``fragment_keys`` raises fail-closed).
    Missing attribute derives the builtin compat default
    (calculation/confgen ``(fragment_keys[0],)``, others ``()``).
    Present-but-invalid (non-iterable, non-string items) raises instead of
    pretending undeclared.
    """
    try:
        has_attr = hasattr(descriptor, "seed_block_keys")
    except Exception as exc:
        raise ValueError("seed_block_keys unreadable") from exc
    if not has_attr:
        try:
            executor = getattr(descriptor, "executor", "")
            frag = tuple(getattr(descriptor, "fragment_keys", ()) or ())
        except Exception as exc:
            raise ValueError("seed descriptor metadata unreadable") from exc
        if executor in ("calculation", "confgen") and frag:
            first = frag[0]
            if not isinstance(first, str) or not first:
                raise ValueError("seed fragment_keys[0] must be a non-empty string")
            return (first,)
        return ()
    try:
        raw = descriptor.seed_block_keys
    except Exception as exc:
        raise ValueError("seed_block_keys unreadable") from exc
    if raw is None:
        try:
            executor = getattr(descriptor, "executor", "")
            frag = tuple(getattr(descriptor, "fragment_keys", ()) or ())
        except Exception as exc:
            raise ValueError("seed descriptor metadata unreadable") from exc
        if executor in ("calculation", "confgen") and frag:
            first = frag[0]
            if not isinstance(first, str) or not first:
                raise ValueError("seed fragment_keys[0] must be a non-empty string")
            return (first,)
        return ()
    try:
        explicit = tuple(raw or ())
    except Exception as exc:
        raise ValueError("seed_block_keys must be an iterable of strings") from exc
    for item in explicit:
        if not isinstance(item, str) or not item.strip():
            raise ValueError("seed_block_keys must be non-empty strings")
    if len(set(explicit)) != len(explicit):
        raise ValueError("seed_block_keys carries duplicates")
    if explicit:
        try:
            frag = tuple(getattr(descriptor, "fragment_keys", ()) or ())
        except Exception as exc:
            raise ValueError("seed descriptor fragment_keys unreadable") from exc
        outside = sorted(set(explicit) - set(frag))
        if outside:
            raise ValueError(f"seed_block_keys {sorted(explicit)!r} not in fragment_keys {frag!r}")
        return explicit
    try:
        executor = getattr(descriptor, "executor", "")
        frag = tuple(getattr(descriptor, "fragment_keys", ()) or ())
    except Exception as exc:
        raise ValueError("seed descriptor metadata unreadable") from exc
    if executor in ("calculation", "confgen") and frag:
        first = frag[0]
        if not isinstance(first, str) or not first:
            raise ValueError("seed fragment_keys[0] must be a non-empty string")
        return (first,)
    return ()


def _check_executor_seed_consistency(entries: Mapping[str, CapabilityDescriptor]) -> None:
    """Fail closed when one executor maps to divergent seed block keys.

    Same-executor entries must share one effective seed key tuple.
    Divergent metadata is an assembly error, never a silent sorted-first
    compiler choice.
    """
    by_executor: dict[str, set[tuple[str, ...]]] = {}
    for descriptor in entries.values():
        by_executor.setdefault(descriptor.executor, set()).add(
            _effective_seed_block_keys(descriptor)
        )
    for executor, keys in by_executor.items():
        if len(keys) != 1:
            raise ValueError(
                f"conflicting seed_block_keys for executor {executor!r}: "
                f"{sorted(sorted(k) for k in keys)}"
            )


def _recipe_hook_pair(descriptor: Any) -> tuple[Any, Any]:
    """Return the (requires_assignment, patch_recipe_step) pair (strict).

    Missing attributes derive ``None`` for C2-era compat; present-but-invalid
    (non-callable, non-None) raises fail-closed and never collapses into a
    silent skip.  Unreadable attributes also raise.
    """
    try:
        has_req = hasattr(descriptor, "requires_assignment")
        has_patch = hasattr(descriptor, "patch_recipe_step")
    except Exception as exc:
        raise ValueError("recipe hook metadata unreadable") from exc
    try:
        req = getattr(descriptor, "requires_assignment", None) if has_req else None
    except Exception as exc:
        raise ValueError("recipe hook requires_assignment unreadable") from exc
    try:
        patch = getattr(descriptor, "patch_recipe_step", None) if has_patch else None
    except Exception as exc:
        raise ValueError("recipe hook patch_recipe_step unreadable") from exc
    if req is not None and not callable(req):
        raise ValueError("recipe hook requires_assignment must be callable or None")
    if patch is not None and not callable(patch):
        raise ValueError("recipe hook patch_recipe_step must be callable or None")
    return req, patch


def _check_executor_recipe_hooks_consistency(
    entries: Mapping[str, CapabilityDescriptor],
) -> None:
    """Fail closed on divergent non-None recipe hook pairs (b1, ROOT-relaxed).

    ``None``/``None`` is a legal C2-era declaration ("no hooks for this card")
    and coexists with set pairs for the same executor (old custom probes stay
    constructible; query returns the set pair).  Failure only when two
    distinct non-None pairs exist for one executor, or when a single
    descriptor sets exactly one of the two hooks (must be set together).
    """
    by_executor: dict[str, set[tuple[int, int]]] = {}
    for descriptor in entries.values():
        req, patch = _recipe_hook_pair(descriptor)
        if (req is None) != (patch is None):
            raise ValueError(
                f"conflicting recipe hooks for executor {descriptor.executor!r}: "
                "requires_assignment/patch_recipe_step must be set together"
            )
        if req is None and patch is None:
            continue
        key = (id(req), id(patch))
        by_executor.setdefault(descriptor.executor, set()).add(key)
    for executor, keys in by_executor.items():
        if len(keys) != 1:
            raise ValueError(f"conflicting recipe hooks for executor {executor!r}")


def _role_block_of(descriptor: Any) -> Any:
    """Return the role-card block hook (strict, callable-or-None).

    Missing attribute derives ``None`` for C2-era compat; present-but-invalid
    (non-callable, non-None) raises fail-closed and never collapses into a
    silent skip.  Unreadable attributes also raise.
    """
    try:
        has_attr = hasattr(descriptor, "apply_role_card_block")
    except Exception as exc:
        raise ValueError("role-card hook metadata unreadable") from exc
    try:
        hook = getattr(descriptor, "apply_role_card_block", None) if has_attr else None
    except Exception as exc:
        raise ValueError("role-card hook apply_role_card_block unreadable") from exc
    if hook is not None and not callable(hook):
        raise ValueError("role-card hook apply_role_card_block must be callable or None")
    return hook


def _check_executor_role_block_consistency(
    entries: Mapping[str, CapabilityDescriptor],
) -> None:
    """Fail closed on divergent non-None role-card block hooks.

    ``None`` is a legal declaration ("no role-card science for this card")
    and coexists with a set hook for the same executor (C2-era custom cards
    stay constructible; query returns the set hook).  Failure only when two
    distinct non-None hooks exist for one executor.
    """
    by_executor: dict[str, set[int]] = {}
    for descriptor in entries.values():
        hook = _role_block_of(descriptor)
        if hook is None:
            continue
        by_executor.setdefault(descriptor.executor, set()).add(id(hook))
    for executor, keys in by_executor.items():
        if len(keys) != 1:
            raise ValueError(f"conflicting role-card hooks for executor {executor!r}")


def role_block_for_executor(registry: Any, executor: str) -> Any | None:
    """Return the consistent role-card block hook for *executor* (generic).

    Queries by wire ``executor`` (never by guessed card type).  ``None``
    means "no non-None hook declared for this executor" (today
    confgen/transform all-None; C2-era ``None`` coexisting with a set
    default) or "no entry for this executor" (unknown-executor analogue of
    the old non-calculation early return) -- all skip.  Divergent non-None
    hooks and present-but-non-callable declarations raise fail-closed and
    never collapse into ``None``.
    """
    try:
        entries = getattr(registry, "entries", None)
    except Exception as exc:
        raise ValueError("intent registry unreadable") from exc
    if not isinstance(entries, Mapping):
        if registry is None or entries is None:
            return None
        raise ValueError("intent registry entries must be a mapping")
    found: list[Any] = []
    for descriptor in entries.values():
        try:
            desc_executor = getattr(descriptor, "executor", None)
        except Exception as exc:
            raise ValueError("descriptor executor unreadable") from exc
        if desc_executor != executor:
            continue
        hook = _role_block_of(descriptor)
        if hook is None:
            continue
        found.append(hook)
    if not found:
        return None
    first = found[0]
    for hook in found[1:]:
        if hook is not first:
            raise ValueError(f"conflicting role-card hooks for executor {executor!r}")
    return first


def recipe_hooks_for_executor(registry: Any, executor: str) -> tuple[Any, Any] | None:
    """Return the consistent recipe hook pair for *executor* (generic query).

    Queries by wire ``executor`` (never by guessed card type, so recipe base
    steps without ``_card_type`` resolve).  ``None`` means "no non-None hooks
    declared for this executor" (today confgen/transform all-None; C2-era
    custom cards with ``None`` coexisting with set defaults) or "no entry
    for this executor" (unknown-executor analogue of the old non-block early
    return) -- all skip, preserving old bytes.  Divergent non-None pairs and
    partial (one-set/one-None) descriptors raise fail-closed and never
    collapse into ``None``.
    """
    try:
        entries = getattr(registry, "entries", None)
    except Exception as exc:
        raise ValueError("intent registry unreadable") from exc
    if not isinstance(entries, Mapping):
        if registry is None or entries is None:
            return None
        raise ValueError("intent registry entries must be a mapping")
    found: list[tuple[Any, Any]] = []
    for descriptor in entries.values():
        try:
            desc_executor = getattr(descriptor, "executor", None)
        except Exception as exc:
            raise ValueError("descriptor executor unreadable") from exc
        if desc_executor != executor:
            continue
        req, patch = _recipe_hook_pair(descriptor)
        if (req is None) != (patch is None):
            raise ValueError(
                f"conflicting recipe hooks for executor {executor!r}: "
                "requires_assignment/patch_recipe_step must be set together"
            )
        if req is None and patch is None:
            continue
        found.append((req, patch))
    if not found:
        return None
    first_req, first_patch = found[0]
    for req, patch in found[1:]:
        if not (req is first_req and patch is first_patch):
            raise ValueError(f"conflicting recipe hooks for executor {executor!r}")
    return first_req, first_patch


def rejected_step_keys_for_legacy_fallback(executor: str) -> frozenset[str]:
    """Return the builtin rejected set for the legacy 4-arg fallback.

    Explicit assembly point owning the old compiler branches: the three
    builtin ``REJECTED_STEP_KEYS`` constants are read here, never in the
    compiler.  Unknown executors yield an empty frozenset (old empty-set
    analogue).  Internal module import errors and invalid declared metadata
    propagate fail-closed and are never swallowed into an empty set (which
    would silently drop science).
    """
    from .calculation import REJECTED_STEP_KEYS as _CALC_REJ
    from .confgen import REJECTED_STEP_KEYS as _CONF_REJ
    from .transform import REJECTED_STEP_KEYS as _TR_REJ

    table: dict[str, Any] = {
        "calculation": _CALC_REJ,
        "confgen": _CONF_REJ,
        "structure_transform": _TR_REJ,
    }
    raw = table.get(executor, ())
    items = tuple(raw or ())
    for item in items:
        if not isinstance(item, str) or not item.strip():
            raise ValueError(f"builtin rejected metadata for executor {executor!r} is invalid")
    if len(set(items)) != len(items):
        raise ValueError(f"builtin rejected metadata for executor {executor!r} carries duplicates")
    return frozenset(items)


def seed_block_keys_for_registry(registry: Any) -> tuple[str, ...]:
    """Return the union seed block names declared by *registry* (ordered).

    Strict: each descriptor's effective seed keys are read fail-closed
    (declared-bad raises, old missing-field derives compat). Per-executor
    divergence raises instead of silently merging. Empty registry (or
    ``None``) falls back to the builtin default union so the compiler stays
    usable without extra context. Order is the builtin declaration order
    (``calculation`` then ``confgen``) to preserve the historical loop
    order; unknown keys append sorted.
    """
    try:
        entries = getattr(registry, "entries", None)
    except Exception as exc:
        raise ValueError("intent registry unreadable") from exc
    if entries is None and registry is not None:
        # Duck registry without entries: treat as empty (legal default path
        # keeps old None behavior); only None and Mapping are expected.
        # Non-mapping entries raises below via isinstance check.
        pass
    if not isinstance(entries, Mapping):
        if registry is None or entries is None:
            entries = {}
        else:
            raise ValueError("intent registry entries must be a mapping")
    per_executor: dict[str, set[tuple[str, ...]]] = {}
    collected: set[str] = set()
    for descriptor in entries.values():
        keys = _effective_seed_block_keys(descriptor)
        try:
            executor = getattr(descriptor, "executor", "")
        except Exception as exc:
            raise ValueError("seed descriptor executor unreadable") from exc
        per_executor.setdefault(executor, set()).add(keys)
        for key in keys:
            if isinstance(key, str) and key:
                collected.add(key)
    for executor, keys in per_executor.items():
        if len(keys) != 1:
            raise ValueError(
                f"conflicting seed_block_keys for executor {executor!r}: "
                f"{sorted(sorted(k) for k in keys)}"
            )
    if not collected:
        fallback: list[str] = []
        for keys in SEED_BLOCK_KEYS_BY_EXECUTOR.values():
            for key in keys:
                if key not in fallback:
                    fallback.append(key)
        return tuple(fallback)
    builtin_order = ["calculation", "confgen"]
    ordered = [k for k in builtin_order if k in collected]
    ordered.extend(sorted(k for k in collected if k not in set(builtin_order)))
    return tuple(ordered)


def wire_block_key_for_executor(registry: Any, executor: str) -> str | None:
    """Return the consistent wire-block key for *executor* (fail-closed).

    Queries the registry by executor (never by guessed card type, so recipe
    base steps without ``_card_type`` resolve). No entry for *executor*
    falls back to the builtin default (``FRAGMENT_KEYS_BY_EXECUTOR`` first
    item, owned by this assembly point); unknown executors yield ``None``.
    Same-executor divergence raises instead of silently picking
    ``sorted-first`` or the builtin default.
    """
    try:
        entries = getattr(registry, "entries", None)
    except Exception as exc:
        raise ValueError("intent registry unreadable") from exc
    if not isinstance(entries, Mapping):
        if registry is None or entries is None:
            entries = {}
        else:
            raise ValueError("intent registry entries must be a mapping")
    found: set[str] = set()
    for descriptor in entries.values():
        try:
            desc_executor = getattr(descriptor, "executor", None)
        except Exception as exc:
            raise ValueError("descriptor executor unreadable") from exc
        if desc_executor != executor:
            continue
        found.add(_effective_wire_block_key(descriptor))
    if len(found) == 1:
        only = next(iter(found))
        return only if isinstance(only, str) and only else None
    if len(found) > 1:
        raise ValueError(f"conflicting wire_block_key for executor {executor!r}: {sorted(found)}")
    default = FRAGMENT_KEYS_BY_EXECUTOR.get(executor)
    if default:
        return tuple(default)[0]
    return None


#: Capability-owned fragment keys per runtime executor (single source).
FRAGMENT_KEYS_BY_EXECUTOR: dict[str, tuple[str, ...]] = {
    "calculation": ("calculation",),
    "confgen": ("confgen",),
    "structure_transform": ("transform", "_preset_ref"),
}

#: Seed-bearing wire blocks per runtime executor (single source).
#: Only calculation/confgen carry seed slots; transform carries none.
SEED_BLOCK_KEYS_BY_EXECUTOR: dict[str, tuple[str, ...]] = {
    "calculation": ("calculation",),
    "confgen": ("confgen",),
    "structure_transform": (),
}


def build_default_intent_registry() -> IntentRegistry:
    """Assemble the production default registry (14 builtin cards, explicit).

    Three legacy payload builders stay verbatim (``_wire_*`` keep their old
    return shape and ``is``/``__module__`` compat); the registry wires the
    new fragment adapters (``*_fragment``) that return the complete
    capability-owned fragment.  Function-local lazy imports only, no
    top-level handler import, no self-registration.
    No test-local keys are referenced here.
    """
    from ...cards import CARD_TYPES, get_card
    from .calculation import REJECTED_STEP_KEYS as _CALC_REJECTED
    from .calculation import apply_role_card_block as _calc_role_block
    from .calculation import calculation_fragment
    from .calculation import patch_recipe_block as _calc_patch_block
    from .calculation import require_recipe_assignment as _calc_require
    from .confgen import REJECTED_STEP_KEYS as _CONFGEN_REJECTED
    from .confgen import confgen_fragment
    from .transform import REJECTED_STEP_KEYS as _TRANSFORM_REJECTED
    from .transform import transform_fragment

    descriptors: list[CapabilityDescriptor] = []
    for card_type in CARD_TYPES:
        card = get_card(card_type)
        executor = str(card["executor"])
        try:
            fragment_keys = FRAGMENT_KEYS_BY_EXECUTOR[executor]
        except KeyError as exc:
            raise ValueError(f"unknown executor {executor!r} for card {card_type!r}") from exc
        if executor == "calculation":
            handler = calculation_fragment
            rejected = tuple(sorted(set(_CALC_REJECTED)))
            wire_block_key = str(fragment_keys[0])
            seed_block_keys = tuple(SEED_BLOCK_KEYS_BY_EXECUTOR["calculation"])
            requires_assignment = _calc_require
            patch_recipe_step = _calc_patch_block
            role_block = _calc_role_block
        elif executor == "confgen":
            handler = confgen_fragment
            rejected = tuple(sorted(set(_CONFGEN_REJECTED)))
            wire_block_key = str(fragment_keys[0])
            seed_block_keys = tuple(SEED_BLOCK_KEYS_BY_EXECUTOR["confgen"])
            requires_assignment = None
            patch_recipe_step = None
            role_block = None
        elif executor == "structure_transform":
            handler = transform_fragment
            rejected = tuple(sorted(set(_TRANSFORM_REJECTED)))
            wire_block_key = str(fragment_keys[0])
            seed_block_keys = tuple(SEED_BLOCK_KEYS_BY_EXECUTOR["structure_transform"])
            requires_assignment = None
            patch_recipe_step = None
            role_block = None
        else:  # pragma: no cover - current cards only use the three above
            raise ValueError(f"unknown executor {executor!r} for card {card_type!r}")
        descriptors.append(
            CapabilityDescriptor(
                key=card_type,
                executor=executor,
                intent_handler=handler,
                card=card,
                fragment_keys=fragment_keys,
                description=str(card.get("description", "")),
                rejected_step_keys=rejected,
                wire_block_key=wire_block_key,
                seed_block_keys=seed_block_keys,
                requires_assignment=requires_assignment,
                patch_recipe_step=patch_recipe_step,
                apply_role_card_block=role_block,
            )
        )
    return build_intent_registry(descriptors)
