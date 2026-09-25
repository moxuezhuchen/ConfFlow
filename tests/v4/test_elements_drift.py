#!/usr/bin/env python3

"""Cross-check the V4 element table against the legacy periodic table.

The V4 domain layer owns a dependency-free element table; this drift test
guards it against silent divergence from ``confflow.core.data``.  Importing
the legacy module is allowed here because this file is a test, not core code.
"""

from __future__ import annotations

import pytest

import confflow.core.data as legacy_data
from confflow.domain import ELEMENT_SYMBOLS, atomic_number, canonical_element_symbol

LEGACY_SYMBOLS: tuple[str, ...] = tuple(legacy_data.PERIODIC_SYMBOLS)

NON_EMPTY_SYMBOLS: tuple[tuple[int, str], ...] = tuple(
    (index, symbol) for index, symbol in enumerate(ELEMENT_SYMBOLS) if symbol
)


def test_v4_element_table_matches_legacy_periodic_symbols() -> None:
    """Same length and identical entries, including the index-0 placeholder."""
    assert len(ELEMENT_SYMBOLS) == len(LEGACY_SYMBOLS)
    assert ELEMENT_SYMBOLS == LEGACY_SYMBOLS


@pytest.mark.parametrize(("atomic", "symbol"), NON_EMPTY_SYMBOLS)
def test_symbol_canonicalizes_to_itself_at_its_atomic_number(atomic: int, symbol: str) -> None:
    """Every non-empty symbol is canonical and maps to its table index."""
    assert canonical_element_symbol(symbol) == symbol
    assert canonical_element_symbol(symbol.upper()) == symbol
    assert atomic_number(symbol) == atomic
    assert atomic_number(symbol.upper()) == atomic
