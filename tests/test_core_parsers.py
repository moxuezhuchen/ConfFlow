"""Regression tests for the shared iprog/itask/memory parsers."""

from __future__ import annotations

import pytest

from confflow.core.exceptions import ValidationError
from confflow.core.parsers import parse_iprog, parse_itask, parse_memory


@pytest.mark.parametrize("value", [True, False])
def test_parse_iprog_rejects_bool(value: bool) -> None:
    with pytest.raises(ValidationError, match="iprog"):
        parse_iprog(value)


@pytest.mark.parametrize("value", [True, False])
def test_parse_itask_rejects_bool(value: bool) -> None:
    with pytest.raises(ValidationError, match="itask"):
        parse_itask(value)


def test_parse_iprog_and_itask_keep_valid_inputs() -> None:
    assert parse_iprog({"iprog": "orca"}) == 2
    assert parse_iprog(1) == 1
    assert parse_itask("opt") == 0
    assert parse_itask(4) == 4


@pytest.mark.parametrize("value", ["invalidGB", "abcMB"])
def test_parse_memory_invalid_unit_value_raises_validation_error(value: str) -> None:
    with pytest.raises(ValidationError, match="memory"):
        parse_memory(value)


def test_parse_memory_valid_values_unchanged() -> None:
    assert parse_memory("4GB") == 4096
    assert parse_memory("4GB", "GB") == 4
    assert parse_memory("4000MB") == 4000
