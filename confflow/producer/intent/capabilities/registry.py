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
) -> IntentRegistry:
    """Build a locally immutable registry from explicit descriptors.

    Rejects duplicate keys (conflict), unknown executors, entries
    without a handler, and empty/duplicate fragment-key declarations.
    No import side effects, no global state.
    """
    seen: dict[str, CapabilityDescriptor] = {}
    for descriptor in descriptors:
        if not isinstance(descriptor, CapabilityDescriptor):
            raise ValueError("build_intent_registry needs CapabilityDescriptor items")
        if descriptor.key in seen:
            raise ValueError(f"conflicting CapabilityDescriptor key {descriptor.key!r}")
        if descriptor.executor not in ALLOWED_EXECUTORS:
            raise ValueError(
                f"unknown executor {descriptor.executor!r} for key "
                f"{descriptor.key!r}; expected one of {sorted(ALLOWED_EXECUTORS)}"
            )
        if descriptor.intent_handler is None:
            raise ValueError(f"CapabilityDescriptor {descriptor.key!r} carries no intent handler")
        if not descriptor.fragment_keys:
            raise ValueError(f"CapabilityDescriptor {descriptor.key!r} declares no fragment keys")
        seen[descriptor.key] = descriptor
    return IntentRegistry(entries=seen)


#: Capability-owned fragment keys per runtime executor (single source).
FRAGMENT_KEYS_BY_EXECUTOR: dict[str, tuple[str, ...]] = {
    "calculation": ("calculation",),
    "confgen": ("confgen",),
    "structure_transform": ("transform", "_preset_ref"),
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
    from .calculation import calculation_fragment
    from .confgen import confgen_fragment
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
        elif executor == "confgen":
            handler = confgen_fragment
        elif executor == "structure_transform":
            handler = transform_fragment
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
            )
        )
    return build_intent_registry(descriptors)
