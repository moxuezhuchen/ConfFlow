#!/usr/bin/env python3

"""Shared helper functions for task configuration assembly."""

from __future__ import annotations

import shlex
from typing import Any

from ..core.models import _coerce_freeze_indices, _coerce_two_atom_indices

__all__ = [
    "_normalize_iprog_label",
    "_itask_label",
    "_format_freeze_value",
    "_format_ts_bond_pair",
    "_coerce_bool_flag",
    "_parse_clean_opts_like_string",
]


def _normalize_iprog_label(iprog: Any) -> str:
    s = str(iprog).strip().lower()
    if s in {"1", "g16", "gaussian", "gau", "g09", "g03"}:
        return "g16"
    if s in {"2", "orca"}:
        return "orca"
    return str(iprog).strip()


def _itask_label(itask: Any) -> str:
    s = str(itask).strip().lower()
    mapping = {
        "0": "opt",
        "1": "sp",
        "2": "freq",
        "3": "opt_freq",
        "4": "ts",
        "opt": "opt",
        "sp": "sp",
        "freq": "freq",
        "opt_freq": "opt_freq",
        "optfreq": "opt_freq",
        "ts": "ts",
    }
    return mapping.get(s, str(itask).strip())


def _format_freeze_value(value: Any) -> str:
    """Format freeze indices into the flat CSV string expected by calc config."""
    indices = _coerce_freeze_indices(value)
    return ",".join(str(x) for x in indices) if indices else "0"


def _format_ts_bond_pair(value: Any) -> str | None:
    """Format a two-atom index value into ``a,b`` when possible."""
    try:
        pair = _coerce_two_atom_indices(value)
    except (TypeError, ValueError):
        pair = None
    if pair is not None:
        a, b = pair
        if a > 0 and b > 0 and a != b:
            return f"{a},{b}"
        return None

    # Preserve the legacy behavior of reusing the first freeze pair.
    indices = _coerce_freeze_indices(value)
    if len(indices) >= 2:
        a, b = indices[0], indices[1]
        if a > 0 and b > 0 and a != b:
            return f"{a},{b}"
    return None


def _coerce_bool_flag(value: Any) -> bool:
    """Interpret common YAML/INI boolean spellings without Python truthiness surprises."""
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "yes", "on"}:
            return True
        if normalized in {"0", "false", "no", "off", ""}:
            return False
    if isinstance(value, (int, float)):
        return bool(value)
    return bool(value)


def _parse_clean_opts_like_string(opts_str: str) -> tuple[float | None, float | None, float | None]:
    """Parse threshold-like cleanup values from a clean_opts/clean_params style string."""
    thresh: float | None = None
    ewin: float | None = None
    etol: float | None = None

    try:
        tokens = shlex.split(opts_str)
    except ValueError:
        tokens = opts_str.split()

    i = 0
    while i < len(tokens):
        tok = tokens[i]
        if tok == "-t" and i + 1 < len(tokens):
            try:
                thresh = float(tokens[i + 1])
            except (TypeError, ValueError):
                pass
            i += 2
        elif tok == "-ewin" and i + 1 < len(tokens):
            try:
                ewin = float(tokens[i + 1])
            except (TypeError, ValueError):
                pass
            i += 2
        elif tok == "--energy-tolerance" and i + 1 < len(tokens):
            try:
                etol = float(tokens[i + 1])
            except (TypeError, ValueError):
                pass
            i += 2
        elif tok.startswith("-t="):
            try:
                thresh = float(tok.split("=", 1)[1])
            except (IndexError, TypeError, ValueError):
                pass
            i += 1
        elif tok.startswith("-ewin="):
            try:
                ewin = float(tok.split("=", 1)[1])
            except (IndexError, TypeError, ValueError):
                pass
            i += 1
        elif tok.startswith("--energy-tolerance="):
            try:
                etol = float(tok.split("=", 1)[1])
            except (IndexError, TypeError, ValueError):
                pass
            i += 1
        else:
            i += 1

    return thresh, ewin, etol
