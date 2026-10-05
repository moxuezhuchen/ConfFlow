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

from confflow.domain._immutable import freeze_value

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
    # A4d inherited-state hooks (optional for compat; built-ins always set
    # them; root-authorized interface addition, PLAN omitted the pairing).
    # serialize_inherited_state(resolved, state_value, context) sees the full
    # resolved spec plus this component's state slice (from ComponentStateKey,
    # never context.input_state_key) and returns the public JSON wire payload
    # for downstream chaining (byte-identical to the legacy executor slices).
    # verify_inherited_state(structure, state_value, payload, context) checks
    # the carried slice against the downstream resolved spec/adjacency (from
    # context, never input_state_key) plus the given geometry, raising
    # InheritedScopeError verbatim on scope failures and returning
    # VerificationResult(ok, evidence) for geometry drift (empty when ok).
    serialize_inherited_state: Callable[..., Any] | None = None
    verify_inherited_state: Callable[..., Any] | None = None
    # A5 lightweight-boundary hooks (optional for compat; built-ins always
    # set them). schema_constants() returns only lightweight vocabulary
    # (shapes, template sizes, models/treatments) read from the component's
    # stdlib-only constants.py (plus the lane-B graph for coordination
    # shapes, which is light and needs no new authority); contract_options()
    # returns the component-owned slice of the producer contract section.
    # Both must never import stage/realization/enumeration/hgeom; they are
    # called lazily by schema/contract validators, never at registry build.
    schema_constants: Callable[[], Mapping[str, Any]] | None = None
    contract_options: Callable[[], Mapping[str, Any]] | None = None
    # AG1 generic hooks (optional for compat; built-ins set them, customs
    # default to no behavior). Kernel loops only; never reads component
    # key strings itself.
    # spec_defaults: ordered builtin defaults owned by this component
    # (e.g. coordination (("coordination", None),)). Custom components
    # leave it empty so missing custom keys stay missing (no growth).
    spec_defaults: tuple[tuple[str, Any], ...] = ()
    # has_indices(raw): True when the raw spec carries index-bearing
    # content owned by this component (never raises; illegal shapes are
    # False so the later normalize dispatch raises the first error).
    has_indices: Callable[[Mapping[str, Any]], bool] | None = None
    # index_detection_order: probe order for the missing-base gate.
    # Topology (generic) runs first in the planner; then descriptors in
    # this order. Builtins preserve the historical T->C->R probe order:
    # torsions=10, coordination=20, rings=30. Customs default last (1000).
    index_detection_order: int = 1000
    # expand_context_spec(resolved, structure, adjacency): mutating hook
    # running after the typed graph exists and before the context is
    # built. Only torsions sets it (verbatim moved body).
    expand_context_spec: Callable[..., None] | None = None
    # graph_metadata(resolved, n_atoms): return a generic mapping of
    # typed-graph metadata (e.g. {"metal_center": int|None}) or None for
    # no contribution. Called at the pre-AG1 _graph_metal_center site;
    # duplicate keys across components fail closed.
    graph_metadata: Callable[..., Mapping[str, Any] | None] | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "spec_keys", tuple(self.spec_keys))
        # AG1 v2: deep-freeze every default value via the single domain
        # authority (freeze_value). The outer tuple alone left mutable
        # builtin [] objects shared in the cached default_registry; a caller
        # could append via spec_defaults[0][1] and change the next
        # normalize run (root repro). Frozen form (tuple/FrozenDict) is
        # immutable; the planner thaws a fresh deep copy per run.
        # Fail closed: default keys must belong to this descriptor's own
        # spec_keys (customs cannot hijack another owner's key) and must
        # not repeat within one descriptor.
        owned = set(self.spec_keys)
        seen: set[str] = set()
        frozen: list[tuple[str, Any]] = []
        for _pair in self.spec_defaults:
            try:
                _key, _value = _pair
            except (TypeError, ValueError) as exc:
                raise ValueError(
                    f"component {self.id!r} has malformed spec default {_pair!r}; "
                    "expected (key, value)"
                ) from exc
            if _key not in owned:
                raise ValueError(
                    f"component {self.id!r} declares default for unowned spec key "
                    f"{_key!r}; owned {sorted(owned)}"
                )
            if _key in seen:
                raise ValueError(f"duplicate spec default {_key!r} in component {self.id!r}")
            seen.add(_key)
            try:
                _frozen = freeze_value(_value)
            except Exception as exc:
                raise ValueError(
                    f"component {self.id!r} has unsupported spec default for key "
                    f"{_key!r}: {exc}"
                ) from exc
            frozen.append((_key, _frozen))
        object.__setattr__(self, "spec_defaults", tuple(frozen))


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
        # AG1 v2: defaults must not escape their owner and must not repeat
        # across descriptors (a custom default overwriting another owner's
        # key would silently change insertion order/output bytes).
        seen_defaults: dict[str, str] = {}
        for descriptor in self.descriptors:
            owned_keys = set(descriptor.spec_keys)
            for key, _ in descriptor.spec_defaults:
                if key not in owned_keys:
                    raise ValueError(
                        f"component {descriptor.id!r} declares default for unowned "
                        f"spec key {key!r}; owned {sorted(owned_keys)}"
                    )
                if key in seen_defaults:
                    raise ValueError(
                        f"duplicate spec default {key!r}: declared by both "
                        f"{seen_defaults[key]!r} and {descriptor.id!r}"
                    )
                seen_defaults[key] = descriptor.id

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
