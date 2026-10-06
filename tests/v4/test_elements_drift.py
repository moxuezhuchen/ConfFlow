#!/usr/bin/env python3

"""Pin the V4 element table as the single authority.

The V4 domain layer owns a dependency-free element table;
``confflow.science.data`` re-exports the same object.  This test pins that
the two import paths resolve to the identical object, so no duplicate table
can silently drift.  (The historical ``confflow.core.data`` forwarding shim
was retired by DIET-2 R1.5; its leg of this pin was removed with it.)
Importing the implementation modules is allowed here because this file is a
test, not core code.
"""

from __future__ import annotations

import pytest

import confflow.science.data as science_data
from confflow.domain import ELEMENT_SYMBOLS, atomic_number, canonical_element_symbol

NON_EMPTY_SYMBOLS: tuple[tuple[int, str], ...] = tuple(
    (index, symbol) for index, symbol in enumerate(ELEMENT_SYMBOLS) if symbol
)


def test_v4_element_table_matches_legacy_periodic_symbols() -> None:
    """The science/domain import paths yield the SAME table object."""
    assert ELEMENT_SYMBOLS is science_data.PERIODIC_SYMBOLS


@pytest.mark.parametrize(("atomic", "symbol"), NON_EMPTY_SYMBOLS)
def test_symbol_canonicalizes_to_itself_at_its_atomic_number(atomic: int, symbol: str) -> None:
    """Every non-empty symbol is canonical and maps to its table index."""
    assert canonical_element_symbol(symbol) == symbol
    assert canonical_element_symbol(symbol.upper()) == symbol
    assert atomic_number(symbol) == atomic
    assert atomic_number(symbol.upper()) == atomic
