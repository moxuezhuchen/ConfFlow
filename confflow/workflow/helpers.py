#!/usr/bin/env python3

"""Workflow helper utility functions."""

from __future__ import annotations

import contextlib
import os
from typing import Any

__all__ = [
    "pushd",
    "as_list",
    "resolve_step_output",
    "count_conformers_in_xyz",
    "count_conformers_any",
    "is_multi_frame_xyz",
    "is_multi_frame_any",
]


@contextlib.contextmanager
def pushd(path: str):
    """Context manager to temporarily change the working directory."""
    prev = os.getcwd()
    os.chdir(path)
    try:
        yield
    finally:
        os.chdir(prev)


def as_list(value: Any) -> Any:
    """Ensure the value is a list, or return None."""
    if value is None:
        return None
    if isinstance(value, list):
        return value
    return [value]


def resolve_step_output(step_dir: str, step_type: str | None = None) -> str | None:
    """Resolve an existing standard output file by step type."""
    st = (step_type or "").lower()
    if st in {"confgen", "gen"}:
        path = os.path.join(step_dir, "search.xyz")
        return path if os.path.exists(path) else None

    candidates = ["output.xyz", "result.xyz", "search.xyz"]
    for name in candidates:
        path = os.path.join(step_dir, name)
        if os.path.exists(path):
            return path
    return None


def count_conformers_in_xyz(filepath: str) -> int:
    """Count the number of conformers in a single XYZ file."""
    if not os.path.exists(filepath):
        return 0
    from ..core.utils import validate_xyz_file

    ok, geoms = validate_xyz_file(filepath)
    if not ok:
        return 0
    return len(geoms)


def count_conformers_any(src: str | list[str]) -> int:
    """Count total conformers across one or more XYZ files."""
    if isinstance(src, (list, tuple)):
        return sum(count_conformers_in_xyz(str(p)) for p in src)
    return count_conformers_in_xyz(str(src))


def is_multi_frame_xyz(filepath: str) -> bool:
    """Check whether the file is a multi-frame XYZ file."""
    return count_conformers_in_xyz(filepath) >= 2


def is_multi_frame_any(src: str | list[str]) -> bool:
    """Check whether the given input(s) contain multiple frames."""
    if isinstance(src, list):
        return any(is_multi_frame_xyz(str(p)) for p in src)
    return is_multi_frame_xyz(str(src))
