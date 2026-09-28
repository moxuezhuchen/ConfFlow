#!/usr/bin/env python3

"""Behavior tests for the retained workflow helpers (no execution runtime)."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from confflow.workflow.helpers import (
    as_list,
    count_conformers_any,
    count_conformers_in_xyz,
    is_multi_frame_any,
    is_multi_frame_xyz,
    pushd,
    resolve_step_output,
)


def _one_frame(path: Path) -> Path:
    path.write_text("1\nframe\nH 0 0 0\n", encoding="utf-8")
    return path


def _two_frames(path: Path) -> Path:
    path.write_text(
        "1\nframe a\nH 0 0 0\n1\nframe b\nH 0 0 0\n",
        encoding="utf-8",
    )
    return path


def test_pushd_restores_working_directory(tmp_path: Path) -> None:
    before = os.getcwd()
    with pushd(str(tmp_path)):
        assert os.getcwd() == str(tmp_path)
    assert os.getcwd() == before


def test_pushd_restores_working_directory_on_error(tmp_path: Path) -> None:
    before = os.getcwd()
    with pytest.raises(RuntimeError, match="boom"):
        with pushd(str(tmp_path)):
            raise RuntimeError("boom")
    assert os.getcwd() == before


def test_as_list_normalizes_only_non_lists() -> None:
    assert as_list(None) is None
    values = [1, 2]
    assert as_list(values) is values
    assert as_list("x") == ["x"]
    assert as_list(("a", "b")) == [("a", "b")]


def test_resolve_step_output_by_step_type(tmp_path: Path) -> None:
    assert resolve_step_output(str(tmp_path), "confgen") is None

    search = tmp_path / "search.xyz"
    _one_frame(search)
    assert resolve_step_output(str(tmp_path), "confgen") == str(search)

    calc_dir = tmp_path / "calc"
    calc_dir.mkdir()
    result = calc_dir / "result.xyz"
    _one_frame(result)
    assert resolve_step_output(str(calc_dir), "calc") == str(result)
    # Unknown type falls back to the standard candidate order.
    assert resolve_step_output(str(calc_dir)) == str(result)

    output = calc_dir / "output.xyz"
    _one_frame(output)
    assert resolve_step_output(str(calc_dir), "calc") == str(output)


def test_conformer_counting_and_multi_frame_detection(tmp_path: Path) -> None:
    missing = tmp_path / "missing.xyz"
    assert count_conformers_in_xyz(str(missing)) == 0
    invalid = tmp_path / "invalid.xyz"
    invalid.write_text("not an xyz\n", encoding="utf-8")
    assert count_conformers_in_xyz(str(invalid)) == 0

    one = _one_frame(tmp_path / "one.xyz")
    two = _two_frames(tmp_path / "two.xyz")

    assert count_conformers_in_xyz(str(one)) == 1
    assert count_conformers_in_xyz(str(two)) == 2
    assert count_conformers_any(str(one)) == 1
    assert count_conformers_any([str(one), str(two)]) == 3

    assert is_multi_frame_xyz(str(one)) is False
    assert is_multi_frame_xyz(str(two)) is True
    assert is_multi_frame_any(str(one)) is False
    assert is_multi_frame_any([str(one), str(two)]) is True
