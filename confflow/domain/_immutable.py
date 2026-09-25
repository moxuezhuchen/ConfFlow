#!/usr/bin/env python3

"""Immutable container helpers for V4 domain models.

Metadata and extension maps stored on domain records use :class:`FrozenDict`,
a read-only mapping that recursively freezes nested mappings, sequences, and
enums.  Freezing is *strict*: unsupported leaf types are rejected at
construction so a record can never hide a mutable object or blow up later at
hash/digest time.

Accepted leaves are ``None``, ``bool``, ``int``, finite ``float``, ``str``, and
``Enum`` members; nested ``Mapping`` and list/tuple containers are frozen
recursively.  Sets are rejected because their serialization order would make
payloads nondeterministic; use tuples for ordered or repeated values.
"""

from __future__ import annotations

import dataclasses
import math
from collections.abc import Iterator, Mapping
from enum import Enum
from typing import Any

from .canonical import CanonicalizationError, canonical_json_bytes
from .errors import DomainError

__all__ = [
    "FrozenDict",
    "freeze_value",
    "thaw_value",
]


def freeze_value(value: Any) -> Any:
    """Recursively freeze *value* into canonical immutable data.

    Parameters
    ----------
    value : Any
        Value to freeze.

    Returns
    -------
    Any
        Mapping values become :class:`FrozenDict`, list/tuple values become
        tuples, enums become their values, and scalars are normalized to
        plain Python types.

    Raises
    ------
    DomainError
        Raised for unsupported leaf types, non-finite floats, or sets.
    """
    if isinstance(value, Enum):
        return freeze_value(value.value)
    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, str):
        return str(value)
    if isinstance(value, int):
        return int(value)
    if isinstance(value, float):
        number = float(value)
        if not math.isfinite(number):
            raise DomainError("frozen floats must be finite")
        return number
    if isinstance(value, Mapping):
        return FrozenDict(value)
    if isinstance(value, (list, tuple)):
        return tuple(freeze_value(item) for item in value)
    if isinstance(value, (set, frozenset)):
        raise DomainError(
            "sets are not canonical frozen values; use a tuple to keep order explicit"
        )
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        params = getattr(value, "__dataclass_params__", None)
        if params is None or not params.frozen:
            raise DomainError(
                f"mutable dataclass {type(value).__name__} is not a canonical frozen value"
            )
        return value
    raise DomainError(
        f"unsupported frozen value type {type(value).__name__}; "
        "only JSON-compatible scalars, mappings, sequences, and frozen dataclasses are allowed"
    )


def thaw_value(value: Any) -> Any:
    """Convert frozen containers back into plain JSON-compatible data.

    Parameters
    ----------
    value : Any
        Value to convert.

    Returns
    -------
    Any
        Mappings become plain dicts and tuples become lists; sets, if ever
        encountered, are emitted in canonical-byte order so payloads stay
        deterministic across processes.  Other values are returned unchanged.
    """
    if isinstance(value, Mapping):
        return {str(key): thaw_value(item) for key, item in value.items()}
    if isinstance(value, (set, frozenset)):
        return sorted((thaw_value(item) for item in value), key=canonical_json_bytes)
    if isinstance(value, tuple):
        return [thaw_value(item) for item in value]
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        to_dict = getattr(value, "to_dict", None)
        if callable(to_dict):
            return to_dict()
        raise DomainError(f"cannot thaw frozen dataclass {type(value).__name__} without to_dict()")
    return value


class FrozenDict(Mapping[str, Any]):
    """Read-only string-keyed mapping with recursively frozen values.

    Parameters
    ----------
    data : Mapping[str, Any] | None
        Mapping to copy and freeze.  ``None`` produces an empty mapping.

    Raises
    ------
    DomainError
        Raised when a key is not a string, or a value fails strict freezing.
    """

    __slots__ = ("_data",)

    def __init__(self, data: Mapping[str, Any] | None = None) -> None:
        frozen: dict[str, Any] = {}
        for key, value in (data or {}).items():
            if not isinstance(key, str):
                raise DomainError(f"FrozenDict keys must be strings, got {type(key).__name__}")
            frozen[key] = freeze_value(value)
        self._data = frozen

    def __getitem__(self, key: str) -> Any:
        return self._data[key]

    def __iter__(self) -> Iterator[str]:
        return iter(self._data)

    def __len__(self) -> int:
        return len(self._data)

    def __repr__(self) -> str:
        return f"FrozenDict({self._data!r})"

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, Mapping):
            return NotImplemented
        try:
            return canonical_json_bytes(self._data) == canonical_json_bytes(dict(other))
        except (CanonicalizationError, DomainError, TypeError):
            return False

    def __hash__(self) -> int:
        return hash(canonical_json_bytes(self._data))

    def __reduce__(self) -> tuple[Any, ...]:
        return (FrozenDict, (self._data,))

    def thaw(self) -> dict[str, Any]:
        """Return a fresh plain mutable copy for serialization."""
        return {key: thaw_value(value) for key, value in self._data.items()}
