"""Version-stable coercion helpers shared by legacy and typed config models.

This module deliberately has no dependency on ``confflow.core.models``.  The
legacy Pydantic entry points and the v2 typed factories both delegate here so
their accepted wire forms cannot drift independently.
"""

from __future__ import annotations

import re
from typing import Any

__all__ = [
    "coerce_freeze_indices",
    "coerce_positive_int",
    "coerce_two_atom_indices",
]


def _parse_index_spec(value: Any) -> list[int]:
    if value is None:
        return []
    if isinstance(value, (int, float)) and int(value) == 0:
        return []
    if isinstance(value, str) and value.strip().lower() in {"", "0", "none", "false"}:
        return []

    if isinstance(value, (list, tuple)):
        tokens = [part for item in value for part in str(item).replace(",", " ").split()]
    else:
        tokens = str(value).replace(",", " ").split()

    indices: list[int] = []
    for token in tokens:
        match = re.fullmatch(r"(\d+)\s*-\s*(\d+)", token.strip())
        if match:
            start, end = int(match.group(1)), int(match.group(2))
            if start > 0 and end > 0:
                low, high = sorted((start, end))
                indices.extend(range(low, high + 1))
            continue
        if token.isdigit():
            index = int(token)
            if index > 0:
                indices.append(index)
            continue
        indices.extend(int(item) for item in re.findall(r"\d+", token) if int(item) > 0)
    return sorted(set(indices))


def coerce_freeze_indices(value: Any) -> list[int]:
    """Coerce the v2 freeze wire formats into a list of atom indices."""
    if value is None:
        return []
    if isinstance(value, str):
        return _parse_index_spec(value)
    if isinstance(value, (list, tuple)):
        result: list[int] = []
        for item in value:
            if isinstance(item, str):
                result.extend(_parse_index_spec(item))
            else:
                result.append(int(item))
        return result
    return []


def coerce_two_atom_indices(value: Any) -> list[int] | None:
    """Coerce the v2 two-atom wire formats into ``[a, b]``."""
    if value is None:
        return None
    if isinstance(value, str):
        parts = value.replace(",", " ").split()
        if len(parts) == 2:
            return [int(parts[0]), int(parts[1])]
        return None
    if isinstance(value, (list, tuple)) and len(value) == 2:
        return [int(value[0]), int(value[1])]
    return None


def coerce_positive_int(value: Any, *, field: str) -> int:
    """Coerce a positive integer field while retaining its public field name."""
    coerced = int(value)
    if coerced < 1:
        raise ValueError(f"{field} must be >= 1, got {coerced}")
    return coerced
