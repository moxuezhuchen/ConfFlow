#!/usr/bin/env python3

"""V4 domain error hierarchy.

The V4 domain layer is deliberately dependency-free: it must not import legacy
``confflow.core``/``confflow.config``/``confflow.workflow`` packages, whose
package initialisers pull in V2/V3 runtime models.  A dedicated error base is
therefore defined here instead of reusing ``confflow.core.exceptions``.

All V4 domain failures derive from :class:`DomainError` so callers can catch
them uniformly.  The workflow compiler converts them into typed diagnostics.
"""

from __future__ import annotations

__all__ = [
    "DomainError",
    "CanonicalizationError",
    "ElementSymbolError",
    "InvalidStructureError",
    "InvalidArtifactError",
    "InvalidResultError",
    "InvalidBindingError",
    "InvalidWorkItemError",
    "InvalidCompletionPolicyError",
    "InvalidResourceError",
    "PublicationError",
]


class DomainError(Exception):
    """Base class for every V4 domain failure."""


class CanonicalizationError(DomainError, ValueError):
    """A value cannot be encoded into the canonical JSON contract."""


class ElementSymbolError(DomainError, ValueError):
    """An atom symbol is not a known chemical element symbol."""


class InvalidStructureError(DomainError, ValueError):
    """A structure record violates the V4 structure invariants."""


class InvalidArtifactError(DomainError, ValueError):
    """An artifact reference violates the V4 artifact invariants."""


class InvalidResultError(DomainError, ValueError):
    """A scientific result or result set violates the V4 result invariants."""


class InvalidBindingError(DomainError, ValueError):
    """A binding violates the V4 dataflow invariants."""


class InvalidWorkItemError(DomainError, ValueError):
    """A work item or work item result violates the V4 invariants."""


class InvalidCompletionPolicyError(DomainError, ValueError):
    """A completion policy violates the V4 failure-semantics invariants."""


class InvalidResourceError(DomainError, ValueError):
    """A resource request or scheduler policy violates the V4 invariants."""


class PublicationError(DomainError, RuntimeError):
    """The crash-consistency publication protocol was violated."""
