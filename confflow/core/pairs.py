#!/usr/bin/env python3
"""Atom pair list normalization utilities."""

from __future__ import annotations

import re
from typing import Any

__all__ = [
    "normalize_pair_list",
]


def normalize_pair_list(value: Any) -> list[list[int]] | None:
    """Normalize pair-like input into [[a, b], ...] (1-based)."""
    if value is None:
        return None

    if isinstance(value, list):
        if len(value) == 0:
            return []
        if len(value) == 2 and all(isinstance(x, (int, float)) for x in value):
            return [[int(value[0]), int(value[1])]]
        if all(isinstance(x, (list, tuple)) and len(x) == 2 for x in value):
            return [[int(a), int(b)] for a, b in value]
        if all(isinstance(x, str) for x in value):
            out = []
            for item in value:
                parts = re.split(r"[\s,\-]+", item.strip())
                parts = [p for p in parts if p]
                if len(parts) != 2:
                    raise ValueError(f"pair format error: {item}, expected 'a b' or 'a,b' or 'a-b'")
                out.append([int(parts[0]), int(parts[1])])
            return out

    if isinstance(value, str):
        parts = re.split(r"[\s,\-]+", value.strip())
        parts = [p for p in parts if p]
        if len(parts) != 2:
            raise ValueError(f"pair format error: {value}, expected 'a b' or 'a,b' or 'a-b'")
        return [[int(parts[0]), int(parts[1])]]

    raise ValueError(f"unsupported pair format: {type(value)}")
