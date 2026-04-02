#!/usr/bin/env python3

"""Hotspot tests for calculation input helpers."""

from __future__ import annotations

from unittest.mock import patch

from confflow.calc.components import input_helpers


def test_compute_gaussian_mem_has_1gb_floor():
    config = {"total_memory": "512MB", "max_parallel_jobs": 4}
    assert input_helpers.compute_gaussian_mem(config) == "1GB"


def test_compute_orca_maxcore_has_100mb_floor():
    config = {
        "total_memory": "500MB",
        "max_parallel_jobs": 2,
        "cores_per_task": 8,
    }
    assert input_helpers.compute_orca_maxcore(config) == "100"


def test_normalize_gaussian_keyword_handles_non_string_and_repeated_prefixes():
    assert input_helpers.normalize_gaussian_keyword(123) == "123"
    assert input_helpers.normalize_gaussian_keyword("  #T   #P   opt freq") == "opt freq"


def test_normalize_blocks_appends_newlines_and_handles_none():
    assert input_helpers.normalize_blocks("smd", "foo") == ("smd\n", "foo\n")
    assert input_helpers.normalize_blocks(None, "") == ("", "")


def test_parse_freeze_indices_uses_comma_fallback_without_parse_index_spec():
    with patch.object(input_helpers, "parse_index_spec", None):
        assert input_helpers.parse_freeze_indices("1, 3,5") == [1, 3, 5]


def test_gaussian_apply_freeze_formats_cartesian_lines():
    coords = ["C 0 1 2", "H 3 4 5"]
    frozen = input_helpers.gaussian_apply_freeze(coords, [2])
    assert frozen.splitlines() == [
        "C  0     0.000000     1.000000     2.000000",
        "H  -1     3.000000     4.000000     5.000000",
    ]


def test_orca_constraint_block_renders_indices():
    assert input_helpers.orca_constraint_block([1, 3]) == (
        "%geom Constraints\n  { C 0 C }\n  { C 2 C }\n  end\nend\n"
    )


def test_format_orca_blocks_renders_nested_dict_list_string_and_bool():
    blocks = {
        "geom": {
            "UseSymmetry": False,
            "Constraints": ["{ C 0 C }", "{ C 2 C }"],
            "Scan": "B 0 1 = 1.0, 2.0, 5",
            "Nested": {"Flag": True},
        },
        "pal": {"nprocs": 4},
    }

    formatted = input_helpers.format_orca_blocks(blocks)

    assert "%geom" in formatted
    assert "  UseSymmetry false" in formatted
    assert "    { C 0 C }" in formatted
    assert "    { C 2 C }" in formatted
    assert "  Scan B 0 1 = 1.0, 2.0, 5" in formatted
    assert "  Nested" in formatted
    assert "    Flag true" in formatted
    assert "%pal" in formatted
    assert "  nprocs 4" in formatted


def test_format_orca_blocks_handles_blank_strings_and_scalar_content():
    assert input_helpers.format_orca_blocks("   ") == ""
    assert input_helpers.format_orca_blocks({"method": 5}) == "%method\n  5\nend\n"
