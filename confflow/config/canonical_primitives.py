#!/usr/bin/env python3

"""Dependency-light coercion primitives owned by the typed config layer."""

from __future__ import annotations

import re
from typing import Any

__all__ = ["_coerce_freeze_indices", "_coerce_two_atom_indices"]


def _parse_index_spec(value: Any) -> list[int]:
    """Parse a 1-based atom-index specification without importing ``core``."""
    if value is None:
        return []
    if isinstance(value, (int, float)) and int(value) == 0:
        return []
    if isinstance(value, str) and value.strip().lower() in {"", "0", "none", "false"}:
        return []

    tokens: list[str] = []
    if isinstance(value, (list, tuple)):
        for item in value:
            tokens.extend(str(item).replace(",", " ").split())
    else:
        tokens = str(value).replace(",", " ").split()

    out: list[int] = []
    for token in tokens:
        token = token.strip()
        if not token:
            continue
        match = re.fullmatch(r"(\d+)\s*-\s*(\d+)", token)
        if match:
            start, end = int(match.group(1)), int(match.group(2))
            if start <= 0 or end <= 0:
                continue
            low, high = (start, end) if start <= end else (end, start)
            out.extend(range(low, high + 1))
            continue
        if token.isdigit():
            index = int(token)
            if index > 0:
                out.append(index)
            continue
        for match in re.findall(r"\d+", token):
            index = int(match)
            if index > 0:
                out.append(index)

    return sorted(set(out))


def _coerce_freeze_indices(value: Any) -> list[int]:
    """Coerce freeze indices from lists, range strings, or ``None``."""
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


def _coerce_two_atom_indices(value: Any) -> list[int] | None:
    """Coerce two-atom index input from a list or string into ``[a, b]``."""
    if value is None:
        return None
    if isinstance(value, str):
        parts = value.replace(",", " ").split()
        if len(parts) == 2:
            return [int(parts[0]), int(parts[1])]
        return None
    if isinstance(value, (list, tuple)):
        if len(value) == 2:
            return [int(value[0]), int(value[1])]
        return None
    return None
