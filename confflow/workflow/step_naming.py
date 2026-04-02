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
    used: dict[str, int] = {}
    dirnames: list[str] = []
    by_name: dict[str, str] = {}

    for idx, step in enumerate(steps, start=1):
        step_name = str(step.get("name", "")).strip()
        base = sanitize_step_dir_name(step_name, fallback=f"step_{idx:02d}")

        n = used.get(base, 0)
        dirname = base if n == 0 else f"{base}_{n + 1}"
        used[base] = n + 1

        dirnames.append(dirname)
        if step_name and step_name not in by_name:
            by_name[step_name] = dirname

    return dirnames, by_name
