#!/usr/bin/env python3

"""Workflow step naming helpers."""

from __future__ import annotations

import os
import re
from typing import Any

__all__ = [
    "sanitize_step_dir_name",
    "build_step_dir_name_map",
]


def sanitize_step_dir_name(name: Any, fallback: str) -> str:
    """Sanitize a step name into a safe directory name."""
    raw = str(name).strip() if name is not None else ""
    if not raw:
        raw = fallback

    raw = raw.replace(os.sep, "_")
    if os.altsep:
        raw = raw.replace(os.altsep, "_")

    safe = re.sub(r"[^A-Za-z0-9_.-]+", "_", raw)
    safe = re.sub(r"_+", "_", safe).strip("._-")
    return safe or fallback


def build_step_dir_name_map(steps: list[dict[str, Any]]) -> tuple[list[str], dict[str, str]]:
    """Build deterministic, unique directory names for workflow steps."""
    # Reserve every sanitized base before allocating any suffixes.  Otherwise
    # an early duplicate can consume the natural name of a later step, e.g.
    # ``A!``, ``A?``, ``A_2`` used to become ``A``, ``A_2``, ``A_2``.  Keeping
    # the later base available makes the allocation deterministic and keeps
    # names which are already collision-free stable across ordering changes.
    raw_names = [
        "" if step.get("name") is None else str(step.get("name")).strip() for step in steps
    ]
    bases = [
        sanitize_step_dir_name(raw_name, fallback=f"step_{idx:02d}")
        for idx, raw_name in enumerate(raw_names, start=1)
    ]
    reserved = set(bases)
    used: set[str] = set()
    dirnames: list[str] = []
    by_name: dict[str, str] = {}

    for step_name, base in zip(raw_names, bases, strict=True):
        dirname = base
        suffix = 2
        # A suffix must not steal a base that belongs to a later step.  The
        # base itself is allowed on its first occurrence, even though it is in
        # ``reserved`` for that same step.
        while dirname in used or (dirname in reserved and dirname != base):
            dirname = f"{base}_{suffix}"
            suffix += 1

        used.add(dirname)

        dirnames.append(dirname)
        if step_name and step_name not in by_name:
            by_name[step_name] = dirname

    return dirnames, by_name
