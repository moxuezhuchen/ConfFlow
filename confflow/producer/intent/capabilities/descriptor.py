#!/usr/bin/env python3

"""Frozen capability descriptor (L1-C2).

Light boundary: stdlib only (``copy``/``dataclasses``/``collections.abc`` /
``types`` / ``typing``).  No ``producer``/``workflow``/``science`` /
``programs`` / ``execution`` imports at top level, so importing this module
never loads solvers or handlers and has no side effects.

A descriptor owns the intent-side card shape for one card key
(``key``) together with the runtime executor name and the intent
handler callable ``(user, card, step_id) -> wire fragment``.  The stored
``card`` mapping is deeply frozen (``MappingProxyType`` + ``tuple`` +
``frozenset``); mutating the outer mapping or any nested list/dict fails.
Use :meth:`CapabilityDescriptor.card_dict` to obtain an isolated mutable
deep copy (thawed back to ``dict``/``list``) for wiring.
"""

from __future__ import annotations

import copy
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any


def _freeze_value(value: Any) -> Any:
    if isinstance(value, Mapping):
        return MappingProxyType({k: _freeze_value(v) for k, v in value.items()})
    if isinstance(value, list):
        return tuple(_freeze_value(v) for v in value)
    if isinstance(value, tuple):
        return tuple(_freeze_value(v) for v in value)
    if isinstance(value, set):
        return frozenset(_freeze_value(v) for v in value)
    if isinstance(value, frozenset):
        return frozenset(_freeze_value(v) for v in value)
    if isinstance(value, dict):
        return MappingProxyType({k: _freeze_value(v) for k, v in value.items()})
    return value


def _freeze_mapping(mapping: Mapping[str, Any]) -> Mapping[str, Any]:
    return MappingProxyType({k: _freeze_value(v) for k, v in dict(mapping).items()})


def _thaw_value(value: Any) -> Any:
    if isinstance(value, MappingProxyType):
        return {k: _thaw_value(v) for k, v in value.items()}
    if isinstance(value, Mapping):
        return {k: _thaw_value(v) for k, v in dict(value).items()}
    if isinstance(value, tuple):
        return [_thaw_value(v) for v in value]
    if isinstance(value, frozenset):
        # Cards never carry sets; thaw to a sorted list for determinism
        # when possible, else a plain set copy.  Builder rejects unknown
        # shapes before this path matters for defaults.
        try:
            return sorted((_thaw_value(v) for v in value), key=repr)
        except Exception:
            return set(value)
    if isinstance(value, list):
        return [_thaw_value(v) for v in value]
    if isinstance(value, dict):
        return {k: _thaw_value(v) for k, v in value.items()}
    return copy.deepcopy(value)


@dataclass(frozen=True, slots=True)
class CapabilityDescriptor:
    """One card key's intent capability (deeply immutable).

    ``key`` is the card type string (e.g. ``"sp"`` or a test-local custom
    key); ``executor`` reuses an existing runtime executor name
    (``"calculation"``/``"confgen"``/``"structure_transform"``, no new enum);
    ``intent_handler`` is ``(user, card, step_id) -> dict`` returning the
    complete capability-owned wire fragment (e.g. ``{"calculation": ...}``)
    or ``None`` (``None`` means unservable; the default builder rejects
    ``None`` so no fake analysis handler is registered); ``card`` is the
    deeply frozen card shape owned by this descriptor; ``fragment_keys``
    declares the exact fragment keys this handler may return (compiler
    validates the returned mapping against it and never lets it overwrite
    generic ``id``/``executor``/``label`` etc.).
    """

    key: str
    executor: str
    intent_handler: Callable[..., dict[str, Any]] | None
    card: Mapping[str, Any]
    fragment_keys: tuple[str, ...] = ()
    description: str = ""

    def __post_init__(self) -> None:
        if not isinstance(self.key, str) or not self.key.strip():
            raise ValueError("CapabilityDescriptor key must be a non-empty string")
        if not isinstance(self.executor, str) or not self.executor.strip():
            raise ValueError("CapabilityDescriptor executor must be a non-empty string")
        if self.intent_handler is not None and not callable(self.intent_handler):
            raise ValueError("CapabilityDescriptor intent_handler must be callable or None")
        if not isinstance(self.card, Mapping):
            raise ValueError("CapabilityDescriptor card must be a mapping")
        if not isinstance(self.fragment_keys, (tuple, list, frozenset)):
            raise ValueError("CapabilityDescriptor fragment_keys must be a tuple of strings")
        frag = tuple(self.fragment_keys)
        if not frag:
            raise ValueError("CapabilityDescriptor fragment_keys must be non-empty")
        for item in frag:
            if not isinstance(item, str) or not item.strip():
                raise ValueError("CapabilityDescriptor fragment_keys must be non-empty strings")
        if len(set(frag)) != len(frag):
            raise ValueError("CapabilityDescriptor fragment_keys carries duplicates")
        if not isinstance(self.description, str):
            raise ValueError("CapabilityDescriptor description must be a string")
        frozen = _freeze_mapping(dict(self.card))
        object.__setattr__(self, "card", frozen)
        object.__setattr__(self, "fragment_keys", tuple(frag))

    def card_dict(self) -> dict[str, Any]:
        """Return an isolated mutable deep copy of the frozen card shape."""
        thawed = _thaw_value(self.card)
        if not isinstance(thawed, dict):
            raise ValueError("CapabilityDescriptor card did not thaw to a dict")
        return thawed
