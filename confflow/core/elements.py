#!/usr/bin/env python3

"""Element symbol helpers."""

from __future__ import annotations

from ..science.data import PERIODIC_SYMBOLS, SYMBOL_TO_ATOMIC_NUMBER

__all__ = [
    "canonicalize_element_symbol",
]


def canonicalize_element_symbol(symbol: str) -> str:
    """Return a valid element symbol with standard capitalization."""
    raw = str(symbol).strip()
    if not raw:
        raise ValueError("Invalid element symbol: empty value")

    atomic_number = SYMBOL_TO_ATOMIC_NUMBER.get(raw.upper())
    if atomic_number is None:
        raise ValueError(f"Invalid element symbol: {symbol!r}")
    return PERIODIC_SYMBOLS[atomic_number]
