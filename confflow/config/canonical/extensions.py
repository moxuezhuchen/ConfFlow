#!/usr/bin/env python3

"""Producer-owned extension registry and namespace grammar.

Semantic producer data lives under a *namespaced* ``extensions`` key so it can
never be confused with a core parameter:

* a namespace is two or more dot-separated lowercase segments
  (``vendor.example.basis``);
* the JSON Schema can only check the namespace *grammar*;
* whether a producer actually *recognises* a namespace is a runtime fact, so the
  registry answers that question and the runnable validator consults it.

The registry starts **empty**: nothing is recognised until a producer registers
it, so an unknown-but-valid namespace parses and round-trips but is rejected at
runnable validation (that check lands in R3.4). No namespace string-matching is
allowed to leak into the validator — it asks the registry.
"""

from __future__ import annotations

import copy
import re
from typing import Any

__all__ = [
    "DEFAULT_EXTENSION_REGISTRY",
    "EXTENSION_NAMESPACE_PATTERN",
    "ExtensionRegistry",
    "is_valid_namespace",
]

#: A namespaced extension key: ``vendor``, ``vendor.example``,
#: ``org.example.feature_1`` … at least two segments, lowercase only.
EXTENSION_NAMESPACE_PATTERN = r"^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)+$"

_NAMESPACE_RE = re.compile(EXTENSION_NAMESPACE_PATTERN)


def is_valid_namespace(namespace: str) -> bool:
    """Return whether ``namespace`` matches the extension namespace grammar."""
    return isinstance(namespace, str) and _NAMESPACE_RE.fullmatch(namespace) is not None


class ExtensionRegistry:
    """A producer-owned set of recognised semantic extension namespaces."""

    def __init__(self) -> None:
        self._schemas: dict[str, dict[str, Any] | None] = {}

    def register(self, namespace: str, *, schema: dict[str, Any] | None = None) -> None:
        """Register ``namespace``; a malformed or duplicate namespace fails loudly."""
        if not is_valid_namespace(namespace):
            raise ValueError(f"invalid extension namespace: {namespace!r}")
        if namespace in self._schemas:
            raise ValueError(f"extension namespace already registered: {namespace!r}")
        self._schemas[namespace] = None if schema is None else copy.deepcopy(schema)

    def is_known(self, namespace: str) -> bool:
        """Return whether this registry recognises ``namespace``."""
        return namespace in self._schemas

    def namespaces(self) -> frozenset[str]:
        """Return the recognised namespaces."""
        return frozenset(self._schemas)

    def schema_for(self, namespace: str) -> dict[str, Any] | None:
        """Return a copy of the namespace's structural schema, if one was given."""
        schema = self._schemas.get(namespace)
        return None if schema is None else copy.deepcopy(schema)


#: The default registry. Empty until a producer registers a namespace.
DEFAULT_EXTENSION_REGISTRY = ExtensionRegistry()
