#!/usr/bin/env python3

"""ConfFlow shared parsing functions.

Unified configuration parsers: iprog/itask/memory/index-range parsing and
formatting utilities.
"""

from __future__ import annotations

import re
from typing import Any

__all__ = [
    "PROG_NAME_MAP",
    "ITASK_NAME_MAP",
    "parse_iprog",
    "parse_itask",
    "parse_memory",
    "parse_index_spec",
    "format_duration_hms",
    "format_index_ranges",
]

# ==============================================================================
# Shared parsing functions (eliminate duplicate code)
# ==============================================================================

# Program name mapping constants
PROG_NAME_MAP = {"gaussian": 1, "g16": 1, "orca": 2}
ITASK_NAME_MAP = {
    "opt": 0,  # geometry optimisation
    "sp": 1,  # single-point energy
    "freq": 2,  # frequency
    "opt_freq": 3,  # optimisation + frequency
    "ts": 4,  # transition-state optimisation + frequency
}


def parse_iprog(config_or_value: Any, default: int = 2) -> int:
    """Parse the ``iprog`` parameter uniformly, accepting both int and str formats.

    Parameters
    ----------
    config_or_value : Any
        Either a config dict or a raw value.
    default : int
        Default value (2 = ORCA).

    Returns
    -------
    int
        Program ID (1 = Gaussian, 2 = ORCA).

    Examples
    --------
    >>> parse_iprog({'iprog': 'orca'})
    2
    >>> parse_iprog({'iprog': 1})
    1
    >>> parse_iprog('gaussian')
    1
    """
    # If it's a dict, extract the iprog value
    if isinstance(config_or_value, dict):
        iprog_val = config_or_value.get("iprog", default)
    else:
        iprog_val = config_or_value

    # If it's a string, map to integer
    if isinstance(iprog_val, str):
        return PROG_NAME_MAP.get(iprog_val.lower(), default)

    # Try converting to integer
    try:
        return int(iprog_val)
    except (ValueError, TypeError):
        return default


def parse_itask(config_or_value: Any, default: int = 3) -> int:
    """Parse the ``itask`` parameter uniformly, accepting both int and str formats.

    Parameters
    ----------
    config_or_value : Any
        Either a config dict or a raw value.
    default : int
        Default value (3 = opt_freq).

    Returns
    -------
    int
        Task type ID (0=opt, 1=sp, 2=freq, 3=opt_freq, 4=ts).

    Examples
    --------
    >>> parse_itask({'itask': 'opt'})
    0
    >>> parse_itask('sp')
    1
    """
    # If it's a dict, extract the itask value
    if isinstance(config_or_value, dict):
        val = config_or_value.get("itask", default)
    else:
        val = config_or_value

    # If it's an integer, return directly
    if isinstance(val, int):
        return val

    # If it's a numeric string
    if str(val).isdigit():
        return int(val)

    # String mapping
    return ITASK_NAME_MAP.get(str(val).lower(), default)


def parse_memory(mem_str: Any, unit: str = "MB") -> int:
    """Parse a memory string to an integer in the specified unit (binary: 1 GB = 1024 MB).

    Parameters
    ----------
    mem_str : Any
        Memory string, e.g. ``'120GB'``, ``'4000MB'``, or ``'4000'``.
    unit : str
        Target unit (``'MB'`` or ``'GB'``).

    Returns
    -------
    int
        Converted integer value.

    Examples
    --------
    >>> parse_memory('4GB')
    4096
    >>> parse_memory('4GB', 'GB')
    4
    """
    from ..shared.defaults import DEFAULT_TOTAL_MEMORY

    mem_str = str(mem_str).strip().upper()

    # Extract numeric value and unit
    if "GB" in mem_str:
        value = float(mem_str.replace("GB", ""))
        value_mb = int(value * 1024)  # Binary: 1 GB = 1024 MB
    elif "MB" in mem_str:
        value_mb = int(float(mem_str.replace("MB", "")))
    else:
        # Assume MB when no unit is given
        try:
            value_mb = int(float(mem_str))
        except ValueError:
            value_mb = parse_memory(DEFAULT_TOTAL_MEMORY, "MB")

    if unit.upper() == "GB":
        return value_mb // 1024
    return value_mb


def parse_index_spec(value: Any) -> list[int]:
    """Parse a 1-based index specification (supports list / string / ranges).

    Used for ``freeze`` and other atom-index configuration fields.

    Accepted formats:

    - ``0`` / ``None`` / ``"0"`` → empty list
    - ``"1,2,5-7"`` / ``"1 2 5-7"`` / ``[1, 2, "5-7"]``

    Parameters
    ----------
    value : Any
        Index specification.

    Returns
    -------
    list[int]
        Sorted, deduplicated list of 1-based indices.
    """
    if value is None:
        return []
    if isinstance(value, (int, float)) and int(value) == 0:
        return []
    if isinstance(value, str) and value.strip().lower() in {"", "0", "none", "false"}:
        return []

    tokens: list[str] = []
    if isinstance(value, (list, tuple)):
        for item in value:
            tokens.extend(str(item).replace(",", " ").split())
    else:
        tokens = str(value).replace(",", " ").split()

    out: list[int] = []
    for tok in tokens:
        tok = tok.strip()
        if not tok:
            continue
        m = re.fullmatch(r"(\d+)\s*-\s*(\d+)", tok)
        if m:
            a, b = int(m.group(1)), int(m.group(2))
            if a <= 0 or b <= 0:
                continue
            lo, hi = (a, b) if a <= b else (b, a)
            out.extend(list(range(lo, hi + 1)))
            continue
        if tok.isdigit():
            v = int(tok)
            if v > 0:
                out.append(v)
            continue
        for m2 in re.findall(r"\d+", tok):
            v = int(m2)
            if v > 0:
                out.append(v)

    return sorted(set(out))


def format_duration_hms(seconds: float) -> str:
    """Format elapsed time as H:MM:SS or M:SS (for console summaries)."""
    try:
        s = int(round(float(seconds)))
    except (TypeError, ValueError):
        return str(seconds)
    h = s // 3600
    m = (s % 3600) // 60
    ss = s % 60
    if h > 0:
        return f"{h:d}:{m:02d}:{ss:02d}"
    return f"{m:d}:{ss:02d}"


def format_index_ranges(indices: list[int]) -> str:
    """Compress an index list into a range string, e.g. ``[1,2,3,8,10,11]`` → ``'1-3,8,10-11'``."""
    if not indices:
        return "none"
    sorted_idx = sorted(indices)
    parts: list[str] = []
    start = prev = sorted_idx[0]
    for v in sorted_idx[1:]:
        if v == prev + 1:
            prev = v
            continue
        parts.append(f"{start}-{prev}" if start != prev else f"{start}")
        start = prev = v
    parts.append(f"{start}-{prev}" if start != prev else f"{start}")
    return ",".join(parts)
