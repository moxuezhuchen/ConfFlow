#!/usr/bin/env python3

"""Explicit component registry for ConfGen (FIX-1A A1).

The engine discovers generation components through an explicitly
constructed, immutable :class:`ComponentRegistry` instead of hard-coded
axis branches. The default instance is built by
:func:`build_default_registry`, which imports the three built-in
``descriptor()`` functions locally -- never at module top level and never
via import side effects.

This module is intentionally light: it imports no component ``stage``
modules at top level (only under ``TYPE_CHECKING`` for annotations), and
it must never be imported by ``model.py`` (which would close a
``model -> registry -> component -> model`` cycle).
"""

from __future__ import annotations

import functools
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Literal

if TYPE_CHECKING:  # Annotations only; runtime stays free of stage imports.
    from confflow.science.confgen.model import GenerationStage

__all__ = [
    "ComponentDescriptor",
    "ComponentRegistry",
    "build_default_registry",
    "default_registry",
    "resolve_registry",
]


@dataclass(frozen=True)
class ComponentDescriptor:
    """Static description of one generation component."""

    id: str  # Component id (built-ins: coordination / rings / torsions).
    order: int  # Execution order (coordination=10, rings=20, torsions=30).
    spec_keys: tuple[str, ...]  # Resolved-spec top-level keys owned by this component.
    state_merge: Literal["replace", "merge"]  # coordination=replace, others merge.
    is_active: Callable[[Mapping[str, Any]], bool]
    factory: Callable[[Mapping[str, Any]], GenerationStage]
    # A3 optional hooks (compatible defaults; the engine reads the stage
    # hook first, then this descriptor fallback, for legacy explicit
    # stages without hooks; it never compares axis strings itself).
    check_bond_integrity: bool = False
    carries_inherited_locks: bool = False
    fallback_lock: Callable[..., Any] | None = None
    preserved_entries: Callable[[Mapping[str, Any]], list[dict[str, Any]]] | None = None
    report_section: Callable[[Mapping[str, Any]], dict[str, Any] | None] | None = None
    describe_scope: Callable[[Mapping[str, Any]], Mapping[str, Any] | None] | None = None
    # A4b spec hooks (optional for compat; built-ins always set them).
    # normalize_spec(raw, *, index_base) sees the whole raw spec and returns
    # ONLY owned keys present in the input; missing builtin defaults are
    # filled by the planner (coordination->None, rings/torsions/paths->[],
    # strict->False) so default bytes never change. Custom components without
    # a hook pass values through unchanged; a hook returning an unowned key
    # fails closed in the planner. validate_context(resolved, context) runs
    # after the typed graph/context exists; only checks that already ran at
    # context stage may live here (none at A4b; always passes).
    normalize_spec: Callable[..., Mapping[str, Any]] | None = None
    validate_context: Callable[..., None] | None = None
    # A4c topology hook (optional for compat; only coordination sets it).
    # contribute_topology(resolved, build) mutates the shared
    # TopologyBuildContext in place (same adjacency/typed objects, edge order
    # preserved). Runs in registry order at the pre-A4c overlay site (after
    # structure patch, before graph assembly). Built-ins without a hook are
    # no-ops.
    contribute_topology: Callable[..., None] | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "spec_keys", tuple(self.spec_keys))


@dataclass(frozen=True)
class ComponentRegistry:
    """Immutable, explicitly constructed component collection."""

    descriptors: tuple[ComponentDescriptor, ...] = field(default=())

    def __post_init__(self) -> None:
        object.__setattr__(self, "descriptors", tuple(self.descriptors))
        ids = [descriptor.id for descriptor in self.descriptors]
        if len(set(ids)) != len(ids):
            raise ValueError(f"duplicate component id in registry: {ids!r}")
        orders = [descriptor.order for descriptor in self.descriptors]
        if len(set(orders)) != len(orders):
            raise ValueError(f"duplicate component order in registry: {orders!r}")
        seen: dict[str, str] = {}
        for descriptor in self.descriptors:
            for key in descriptor.spec_keys:
                if key in seen:
                    raise ValueError(
                        f"overlapping spec_keys {key!r}: "
                        f"owned by both {seen[key]!r} and {descriptor.id!r}"
                    )
                seen[key] = descriptor.id

    def _ordered(self) -> list[ComponentDescriptor]:
        return sorted(self.descriptors, key=lambda descriptor: descriptor.order)

    def resolve(self, resolved: Mapping[str, Any]) -> list[tuple[str, GenerationStage]]:
        """Build active ``(component id, stage)`` levels in ``order`` sequence."""
        return [
            (descriptor.id, descriptor.factory(resolved))
            for descriptor in self._ordered()
            if descriptor.is_active(resolved)
        ]

    def ids(self) -> tuple[str, ...]:
        """Return component ids in execution order."""
        return tuple(descriptor.id for descriptor in self._ordered())

    def with_component(self, descriptor: ComponentDescriptor) -> ComponentRegistry:
        """Return a new registry with ``descriptor`` added (self unchanged)."""
        return ComponentRegistry(descriptors=tuple(self.descriptors) + (descriptor,))

    def owner_of(self, spec_key: str) -> str | None:
        """Return the id of the component owning ``spec_key``, if any."""
        for descriptor in self.descriptors:
            if spec_key in descriptor.spec_keys:
                return descriptor.id
        return None


def build_default_registry() -> ComponentRegistry:
    """Build the registry with the three built-in components.

    The three ``descriptor()`` functions are imported here, explicitly
    listed, so merely importing this module never loads solver code and
    no import-time self-registration exists.
    """
    from confflow.science.confgen.coordination.component import descriptor as coordination
    from confflow.science.confgen.ring.component import descriptor as rings
    from confflow.science.confgen.torsion.component import descriptor as torsions

    return ComponentRegistry(
        descriptors=(coordination(), rings(), torsions()),
    )


@functools.cache
def default_registry() -> ComponentRegistry:
    """Return the shared immutable default registry instance."""
    return build_default_registry()


def resolve_registry(registry: ComponentRegistry | None) -> ComponentRegistry:
    """Return ``registry``, or the shared default when ``None``."""
    return default_registry() if registry is None else registry
