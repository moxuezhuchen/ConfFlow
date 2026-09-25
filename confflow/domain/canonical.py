#!/usr/bin/env python3

"""Canonical JSON encoding and identity digests for the V4 domain.

V4 digests are domain-separated SHA-256 digests over RFC 8785 (JCS) canonical
JSON bytes.  Every digest payload is wrapped by :func:`typed_digest` with an
explicit ``kind`` marker, so values of different semantic types can never share
a digest and the digest axis is visible at the call site.

Canonicalization contract
-------------------------
- Only JSON data types, enums, and (frozen) dataclass instances are accepted.
- Mapping keys must be strings; key order never affects the result.
- Non-finite floats are rejected; ``-0.0`` is canonicalized to ``0`` by JCS.
- Sets are emitted as canonical-byte-sorted lists, never in iteration order.
"""

from __future__ import annotations

import dataclasses
import hashlib
import math
from collections.abc import Mapping, Sequence, Set
from enum import Enum
from typing import Any, Final, cast

import rfc8785

from .errors import CanonicalizationError

__all__ = [
    "CANONICALIZATION_ID",
    "canonical_json_bytes",
    "canonical_sha256",
    "normalize",
    "typed_digest",
]

CANONICALIZATION_ID: Final[str] = "confflow.v4.jcs.v1"

_DIGEST_PREFIX: Final[str] = "sha256:"


def normalize(value: Any, *, path: str = "$") -> Any:
    """Normalize *value* into the single canonical JSON data form.

    Parameters
    ----------
    value : Any
        Value to normalize.  Supported inputs are ``None``, ``bool``, ``int``,
        finite ``float``, ``str``, ``Enum``, ``Mapping`` with string keys,
        sequences (list/tuple), sets/frozensets, and dataclass instances.
    path : str
        Diagnostic path prefix used in error messages.

    Returns
    -------
    Any
        JSON-compatible value with deterministic collection ordering.

    Raises
    ------
    CanonicalizationError
        Raised when *value* cannot be represented canonically.
    """
    if value is None or isinstance(value, (bool, str)):
        return value
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise CanonicalizationError(f"non-finite number at {path}")
        return value
    if isinstance(value, Enum):
        return normalize(value.value, path=path)
    if isinstance(value, Mapping):
        result: dict[str, Any] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise CanonicalizationError(f"non-string mapping key at {path}: {key!r}")
            result[key] = normalize(item, path=f"{path}.{key}")
        return result
    if isinstance(value, (bytes, bytearray)):
        raise CanonicalizationError(f"bytes are not canonical JSON at {path}")
    if isinstance(value, (Sequence, Set)):
        items = [normalize(item, path=f"{path}[{index}]") for index, item in enumerate(value)]
        if isinstance(value, Set):
            items = sorted(items, key=canonical_json_bytes)
        return items
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        payload: dict[str, Any] = {}
        for field in dataclasses.fields(value):
            payload[field.name] = normalize(getattr(value, field.name), path=f"{path}.{field.name}")
        return payload
    raise CanonicalizationError(f"unsupported canonical value at {path}: {type(value).__name__}")


def canonical_json_bytes(value: Any) -> bytes:
    """Encode *value* as RFC 8785 (JCS) canonical JSON bytes.

    Parameters
    ----------
    value : Any
        Value accepted by :func:`normalize`.

    Returns
    -------
    bytes
        UTF-8 JCS encoding of the normalized value.

    Raises
    ------
    CanonicalizationError
        Raised when *value* cannot be encoded canonically.
    """
    normalized = normalize(value)
    try:
        return cast(bytes, rfc8785.dumps(normalized))
    except Exception as exc:  # rfc8785 raises several unrelated exception types
        raise CanonicalizationError(f"cannot encode canonical JSON: {exc}") from exc


def canonical_sha256(value: Any) -> str:
    """Return the bare SHA-256 hex digest of the canonical JSON encoding."""
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def typed_digest(kind: str, payload: Any) -> str:
    """Return the domain-separated ``sha256:<hex>`` digest of *payload*.

    Parameters
    ----------
    kind : str
        Digest domain marker such as ``"confflow.structure.geometry.v1"``.
        Separating digests by kind makes cross-type digest confusion
        impossible even when payloads happen to have the same shape.
    payload : Any
        Value accepted by :func:`normalize`.

    Returns
    -------
    str
        Digest string of the form ``sha256:<64 lowercase hex>``.

    Raises
    ------
    CanonicalizationError
        Raised when *kind* is empty or *payload* cannot be encoded.
    """
    if not str(kind).strip():
        raise CanonicalizationError("digest kind must not be empty")
    envelope = {"canonicalization": CANONICALIZATION_ID, "kind": str(kind), "payload": payload}
    return _DIGEST_PREFIX + canonical_sha256(envelope)
