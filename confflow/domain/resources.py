#!/usr/bin/env python3

"""V4 resource and scheduler models.

Resources and scheduling are deliberately separate:

- :class:`ResourceRequest` is scientific: ``cores_per_item`` and
  ``memory_per_item`` change what a native program computes (``%nproc``,
  ``%mem``) and therefore participate in semantic digests.
- :class:`SchedulerPolicy` is operational: ``max_parallel_items`` and
  ``on_failure`` never change scientific inputs or reuse identity.

Memory is stored canonically as an integer number of bytes.  The textual form
accepts binary suffixes (``KB``/``KiB`` = 1024 bytes, ``GB``/``GiB`` = 1024^3
bytes) and is documented here as the single authority.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from typing import Any

from .errors import InvalidResourceError

__all__ = [
    "OnFailure",
    "ResourceRequest",
    "SchedulerPolicy",
    "parse_memory_bytes",
]

_MEMORY_PATTERN = re.compile(r"^(?P<value>\d+(?:\.\d+)?)\s*(?P<unit>[KMGTPE]i?B?|B)?$", re.I)

_MEMORY_MULTIPLIERS: dict[str, int] = {
    "": 1,
    "B": 1,
    "KB": 1024,
    "KIB": 1024,
    "MB": 1024**2,
    "MIB": 1024**2,
    "GB": 1024**3,
    "GIB": 1024**3,
    "TB": 1024**4,
    "TIB": 1024**4,
    "PB": 1024**5,
    "PIB": 1024**5,
    "EB": 1024**6,
    "EIB": 1024**6,
}


class OnFailure(str, Enum):
    """Scheduler behavior when a work item fails.

    Attributes
    ----------
    CONTINUE
        Remaining eligible items keep running.
    FAIL_FAST
        Not-yet-started items are cancelled; acceptance policy still applies
        to the items that did run.
    """

    CONTINUE = "continue"
    FAIL_FAST = "fail_fast"


def parse_memory_bytes(value: str | int | float) -> int:
    """Parse a memory amount into bytes.

    Parameters
    ----------
    value : str | int | float
        Integer/float byte counts, or a string such as ``"16GB"`` or
        ``"512MiB"``.  Suffixes are binary (``1GB`` = 1024^3 bytes).

    Returns
    -------
    int
        Non-negative byte count.

    Raises
    ------
    InvalidResourceError
        Raised when *value* is malformed or negative.
    """
    if isinstance(value, bool):
        raise InvalidResourceError("memory amount must not be a boolean")
    if isinstance(value, int):
        if value < 0:
            raise InvalidResourceError("memory amount must be non-negative")
        return value
    if isinstance(value, float):
        if value < 0:
            raise InvalidResourceError("memory amount must be non-negative")
        return int(value)
    if not isinstance(value, str):
        raise InvalidResourceError(f"unsupported memory amount: {value!r}")
    match = _MEMORY_PATTERN.match(value.strip())
    if match is None:
        raise InvalidResourceError(
            f"invalid memory amount {value!r}; expected forms such as '16GB' or '512MiB'"
        )
    unit = (match.group("unit") or "").upper()
    multiplier = _MEMORY_MULTIPLIERS.get(unit)
    if multiplier is None:
        raise InvalidResourceError(
            f"unknown memory unit {match.group('unit')!r} in {value!r}; "
            "use B, KB/KiB, MB/MiB, GB/GiB, TB/TiB, PB/PiB, or EB/EiB"
        )
    amount = float(match.group("value")) * multiplier
    return int(amount)


@dataclass(frozen=True, slots=True)
class ResourceRequest:
    """Per-item resource request for a step.

    Parameters
    ----------
    cores_per_item : int | None
        CPU cores reserved per work item; ``>= 1``.
    memory_per_item_bytes : int | None
        Memory reserved per work item in bytes; ``> 0``.

    Raises
    ------
    InvalidResourceError
        Raised when a declared value is out of range.
    """

    cores_per_item: int | None = None
    memory_per_item_bytes: int | None = None

    def __post_init__(self) -> None:
        if self.cores_per_item is not None:
            if isinstance(self.cores_per_item, bool) or not isinstance(self.cores_per_item, int):
                raise InvalidResourceError("cores_per_item must be an integer or None")
            if self.cores_per_item < 1:
                raise InvalidResourceError("cores_per_item must be >= 1")
        if self.memory_per_item_bytes is not None:
            if isinstance(self.memory_per_item_bytes, bool) or not isinstance(
                self.memory_per_item_bytes, int
            ):
                raise InvalidResourceError("memory_per_item must be an integer byte count or None")
            if self.memory_per_item_bytes <= 0:
                raise InvalidResourceError("memory_per_item must be > 0")

    @classmethod
    def from_values(
        cls, *, cores_per_item: int | None, memory_per_item: str | int | float | None
    ) -> ResourceRequest:
        """Build a request from a textual ``memory_per_item`` value."""
        memory = None if memory_per_item is None else parse_memory_bytes(memory_per_item)
        return cls(cores_per_item=cores_per_item, memory_per_item_bytes=memory)

    @property
    def memory_per_item(self) -> int | None:
        """Return the memory request in bytes (explicit alias)."""
        return self.memory_per_item_bytes

    @property
    def is_resolved(self) -> bool:
        """Return whether both resource dimensions are declared."""
        return self.cores_per_item is not None and self.memory_per_item_bytes is not None

    def with_defaults(self, defaults: ResourceRequest) -> ResourceRequest:
        """Fill unset fields from *defaults* without mutating either request."""
        return ResourceRequest(
            cores_per_item=(
                self.cores_per_item if self.cores_per_item is not None else defaults.cores_per_item
            ),
            memory_per_item_bytes=(
                self.memory_per_item_bytes
                if self.memory_per_item_bytes is not None
                else defaults.memory_per_item_bytes
            ),
        )

    def to_dict(self) -> dict[str, Any]:
        """Return a canonical, JSON-compatible representation."""
        return {
            "cores_per_item": self.cores_per_item,
            "memory_per_item_bytes": self.memory_per_item_bytes,
        }


@dataclass(frozen=True, slots=True)
class SchedulerPolicy:
    """Scheduler-only policy for a run or a step.

    Parameters
    ----------
    max_parallel_items : int | None
        Maximum concurrent work items; ``>= 1``.  Digest-inert by contract.
    on_failure : OnFailure | None
        Failure behavior; ``None`` defers to the run-level policy.

    Raises
    ------
    InvalidResourceError
        Raised when the concurrency limit is out of range.
    """

    max_parallel_items: int | None = None
    on_failure: OnFailure | None = None

    def __post_init__(self) -> None:
        if self.max_parallel_items is not None:
            if isinstance(self.max_parallel_items, bool) or not isinstance(
                self.max_parallel_items, int
            ):
                raise InvalidResourceError("max_parallel_items must be an integer or None")
            if self.max_parallel_items < 1:
                raise InvalidResourceError("max_parallel_items must be >= 1")
        if self.on_failure is not None and not isinstance(self.on_failure, OnFailure):
            raise InvalidResourceError("on_failure must be an OnFailure or None")

    @property
    def is_resolved(self) -> bool:
        """Return whether a concurrency limit is declared."""
        return self.max_parallel_items is not None

    def with_defaults(self, defaults: SchedulerPolicy) -> SchedulerPolicy:
        """Fill unset fields from *defaults* without mutating either policy."""
        return SchedulerPolicy(
            max_parallel_items=(
                self.max_parallel_items
                if self.max_parallel_items is not None
                else defaults.max_parallel_items
            ),
            on_failure=self.on_failure if self.on_failure is not None else defaults.on_failure,
        )

    def to_dict(self) -> dict[str, Any]:
        """Return a canonical, JSON-compatible representation."""
        return {
            "max_parallel_items": self.max_parallel_items,
            "on_failure": self.on_failure.value if self.on_failure is not None else None,
        }
