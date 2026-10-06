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
    # L1-A1 (R1+R2, compat defaults): misplaced-field authority lives in
    # each capability module's ``REJECTED_STEP_KEYS``; the descriptor only
    # carries the assembled value (default empty = no placement restriction,
    # builtin entries carry the verbatim old branch sets).  ``wire_block_key``
    # defaults to ``""`` meaning "derive from ``fragment_keys[0]``" so C2-era
    # custom descriptors without the new fields still construct/compile;
    # only an explicit non-empty key outside ``fragment_keys`` is rejected.
    rejected_step_keys: tuple[str, ...] = ()
    wire_block_key: str = ""
    # L1-A2a (seed plumbing): seed-bearing wire blocks declared per
    # descriptor.  Default ``()`` means "derive the builtin default for the
    # executor" so C2/A1-era descriptors without the field stay
    # constructible with no new required field: calculation/confgen derive
    # ``(fragment_keys[0],)``, structure_transform derives ``()`` (no seed
    # slot).  An explicit non-empty tuple must live inside
    # ``fragment_keys``; the seeds authority (needs_seed/current/set) stays
    # the sole science decider.
    seed_block_keys: tuple[str, ...] = ()
    # L1-A2b1 (recipe assignment hooks): generic recipe orchestration calls
    # these by wire executor.  ``None`` is a legal capability declaration
    # ("this executor needs no explicit recipe assignment", e.g. confgen /
    # transform today) and means skip -- it is NOT a metadata-lookup failure.
    # Unknown executors (no descriptor) also resolve to ``None`` at query
    # time (old non-calculation early-return analogue); registry conflicts /
    # import errors / non-callable declarations fail closed at assembly and
    # never collapse into ``None``.
    requires_assignment: Callable[..., None] | None = None
    patch_recipe_step: Callable[..., Any] | None = None

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
        if not isinstance(self.rejected_step_keys, (tuple, list, frozenset, set)):
            raise ValueError("CapabilityDescriptor rejected_step_keys must be a tuple of strings")
        rej = tuple(self.rejected_step_keys)
        for item in rej:
            if not isinstance(item, str) or not item.strip():
                raise ValueError(
                    "CapabilityDescriptor rejected_step_keys must be non-empty strings"
                )
        if len(set(rej)) != len(rej):
            raise ValueError("CapabilityDescriptor rejected_step_keys carries duplicates")
        if not isinstance(self.wire_block_key, str):
            raise ValueError("CapabilityDescriptor wire_block_key must be a string")
        for _hook_name in ("requires_assignment", "patch_recipe_step"):
            try:
                _hook = getattr(self, _hook_name, None)
            except Exception as exc:
                raise ValueError(f"CapabilityDescriptor {_hook_name} unreadable") from exc
            if _hook is not None and not callable(_hook):
                raise ValueError(f"CapabilityDescriptor {_hook_name} must be callable or None")
        if not isinstance(self.seed_block_keys, (tuple, list, frozenset, set)):
            raise ValueError("CapabilityDescriptor seed_block_keys must be a tuple of strings")
        seed_keys = tuple(self.seed_block_keys)
        for item in seed_keys:
            if not isinstance(item, str) or not item.strip():
                raise ValueError("CapabilityDescriptor seed_block_keys must be non-empty strings")
        if len(set(seed_keys)) != len(seed_keys):
            raise ValueError("CapabilityDescriptor seed_block_keys carries duplicates")
        frozen = _freeze_mapping(dict(self.card))
        object.__setattr__(self, "card", frozen)
        object.__setattr__(self, "fragment_keys", tuple(frag))
        object.__setattr__(self, "rejected_step_keys", tuple(rej))
        object.__setattr__(self, "seed_block_keys", tuple(seed_keys))
        if self.wire_block_key and self.wire_block_key not in tuple(frag):
            raise ValueError(
                f"CapabilityDescriptor wire_block_key {self.wire_block_key!r} "
                f"not in fragment_keys {tuple(frag)!r}"
            )
        if seed_keys:
            outside = sorted(set(seed_keys) - set(tuple(frag)))
            if outside:
                raise ValueError(
                    f"CapabilityDescriptor seed_block_keys {sorted(seed_keys)!r} "
                    f"not in fragment_keys {tuple(frag)!r}"
                )

    def card_dict(self) -> dict[str, Any]:
        """Return an isolated mutable deep copy of the frozen card shape."""
        thawed = _thaw_value(self.card)
        if not isinstance(thawed, dict):
            raise ValueError("CapabilityDescriptor card did not thaw to a dict")
        return thawed

    @property
    def effective_wire_block_key(self) -> str:
        """Return the wire block key (explicit or derived default).

        Empty ``wire_block_key`` derives from ``fragment_keys[0]`` so C2-era
        descriptors stay compilable without hardcoding an executor mapping
        in the compiler.
        """
        if isinstance(self.wire_block_key, str) and self.wire_block_key:
            return self.wire_block_key
        return tuple(self.fragment_keys)[0]

    @property
    def effective_seed_block_keys(self) -> tuple[str, ...]:
        """Return the seed-bearing wire blocks (explicit or derived default).

        Empty ``seed_block_keys`` derives the builtin default for the
        executor: calculation/confgen derive ``(fragment_keys[0],)``,
        any other executor derives ``()`` (no seed slot, e.g. transform).
        Explicit non-empty values are returned verbatim.
        """
        try:
            explicit = tuple(self.seed_block_keys or ())
        except Exception:
            explicit = ()
        if explicit:
            return explicit
        try:
            executor = getattr(self, "executor", "")
        except Exception:
            executor = ""
        if executor in ("calculation", "confgen"):
            try:
                return (tuple(self.fragment_keys)[0],)
            except Exception:
                return ()
        return ()
