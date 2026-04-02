#!/usr/bin/env python3

"""ConfFlow public utilities module.

This module contains two categories:

1. **Backward-compatible re-exports** — re-exports public symbols from sub-modules
   so that callers do not need to update their import paths.  Actual implementations
   have been split into:

   - ``core.exceptions``: exception class hierarchy
   - ``core.logging``: ConfFlowLogger + get_logger
   - ``core.parsers``: parse_iprog / itask / memory, parse_index_spec, format_*

2. **Input validation utilities** — ``validate_xyz_file``, ``validate_yaml_config``,
   etc., used for CLI pre-flight checks.  They live here rather than in
   ``validation.py`` to avoid circular imports.
"""

from __future__ import annotations

import logging
import os
import re
from typing import Any

# ==============================================================================
# Backward-compatible re-exports
# ==============================================================================
# Exception classes (now defined in exceptions.py)
from .exceptions import ConfFlowError, InputFileError, XYZFormatError  # noqa: F401

# Logging system (now defined in logging.py)
from .logging import (  # noqa: F401
    ConfFlowLogger,
    get_logger,
    redirect_logging_streams,
)

# Parsing functions (now defined in parsers.py)
from .parsers import (  # noqa: F401
    ITASK_NAME_MAP,
    PROG_NAME_MAP,
    format_duration_hms,
    format_index_ranges,
    parse_index_spec,
    parse_iprog,
    parse_itask,
    parse_memory,
)

# Module availability flag (for detection by other modules)
UTILS_AVAILABLE = True

# Public API (all exported symbols)
__all__ = [
    # re-exports
    "ConfFlowError",
    "InputFileError",
    "XYZFormatError",
    "ConfFlowLogger",
    "get_logger",
    "redirect_logging_streams",
    "ITASK_NAME_MAP",
    "PROG_NAME_MAP",
    "format_duration_hms",
    "format_index_ranges",
    "parse_index_spec",
    "parse_iprog",
    "parse_itask",
    "parse_memory",
    # local
    "UTILS_AVAILABLE",
    "get_numba_jit",
    "index_to_letter_prefix",
    "validate_xyz_file",
    "validate_yaml_config",
]

# ==============================================================================
# CID letter-prefix helper
# ==============================================================================


def index_to_letter_prefix(idx: int) -> str:
    """Convert a 0-based index to an uppercase letter prefix.

    Examples: 0 → "A", 1 → "B", … 25 → "Z", 26 → "AA", 27 → "AB".
    """
    letters = ""
    n = idx
    while True:
        letters = chr(ord("A") + (n % 26)) + letters
        n = n // 26 - 1
        if n < 0:
            break
    return letters


# ==============================================================================
# Numba fallback support
# ==============================================================================


def get_numba_jit(logger_name: str = "confflow"):
    """Return ``numba.njit``; falls back to a no-op decorator if Numba is unavailable."""
    try:
        import numba

        return numba
    except ImportError:
        log = logging.getLogger(logger_name)
        log.warning("Numba not found. Performance will be impacted. Consider: pip install numba")

        class FakeNumba:
            __name__ = "FakeNumba"

            def njit(self, *args, **kwargs):
                def decorator(func):
                    return func

                return decorator if not args else args[0]

            def jit(self, *args, **kwargs):
                def decorator(func):
                    return func

                return decorator if not args else args[0]

        return FakeNumba()


# ==============================================================================
# Input validation functions
# ==============================================================================


def validate_xyz_file(filepath: str, strict: bool = False) -> tuple[bool, list[dict[str, Any]]]:
    """
    Validate an XYZ file format and return parsed results.

    Parameters
    ----------
    filepath : str
        Path to the XYZ file.
    strict : bool
        Whether to enable strict mode (stricter format checks).

    Returns
    -------
    tuple[bool, list[dict[str, Any]]]
        ``(is_valid, geometries)`` — validation result and parsed geometry list.

    Raises
    ------
    InputFileError
        If the file does not exist or cannot be read.
    XYZFormatError
        On format errors (strict mode only).
    """
    from .io import read_xyz_file as _read_xyz

    if not os.path.exists(filepath):
        raise InputFileError(f"File does not exist: {filepath}", filepath)
    if not os.path.isfile(filepath):
        raise InputFileError(f"Path is not a file: {filepath}", filepath)
    if os.path.getsize(filepath) == 0:
        raise InputFileError(f"File is empty: {filepath}", filepath)

    try:
        frames = _read_xyz(filepath, parse_metadata=False)
    except (OSError, ValueError) as exc:
        if strict:
            raise XYZFormatError(str(exc), filepath) from exc
        return False, []

    errors: list[str] = []
    geometries: list[dict[str, Any]] = []

    for fr in frames:
        atoms = fr.get("atoms", [])
        coords = fr.get("coords", [])
        # Validate atom symbols
        atom_errors: list[str] = []
        for idx, sym in enumerate(atoms):
            if not re.match(r"^[A-Za-z]{1,2}$", sym):
                atom_errors.append(
                    f"Frame {fr.get('frame_index', '?')}, atom {idx+1}: invalid symbol '{sym}'"
                )
        if atom_errors:
            errors.extend(atom_errors)
            if strict:
                continue

        geometries.append(
            {
                "num_atoms": fr.get("natoms", len(atoms)),
                "comment": fr.get("comment", ""),
                "atoms": atoms,
                "coords": [tuple(c) for c in coords],
                "frame_index": fr.get("frame_index", 0),
            }
        )

    if strict and errors:
        raise XYZFormatError("\n".join(errors), filepath)

    is_valid = len(geometries) > 0 and len(errors) == 0
    return is_valid, geometries


# ---------------------------------------------------------------------------
# Backward compat: validate_yaml_config / _validate_step_config moved to config.schema
# ---------------------------------------------------------------------------


def validate_yaml_config(
    config: dict[str, Any], required_sections: list[str] | None = None
) -> list[str]:
    from ..config.schema import validate_yaml_config as _impl

    return _impl(config, required_sections)


def _validate_step_config(step: dict[str, Any], index: int) -> list[str]:
    from ..config.schema import _validate_step_config as _impl

    return _impl(step, index)
