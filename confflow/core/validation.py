#!/usr/bin/env python3

"""ConfFlow input validation module.

Provides unified parameter validation utilities for checking argument
legality at function entry points.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Callable
from functools import wraps
from typing import Any, TypeVar

import numpy as np

from .exceptions import ValidationError  # noqa: F401 — re-export for compatibility

logger = logging.getLogger("confflow.validation")

F = TypeVar("F", bound=Callable[..., Any])


def validate_positive(value: Any, name: str) -> None:
    """Validate that a value is positive."""
    try:
        num = float(value)
    except (ValueError, TypeError) as e:
        raise ValidationError(name, "must be a numeric type", value) from e

    if num <= 0:
        raise ValidationError(name, "must be positive", value)


def validate_non_negative(value: Any, name: str) -> None:
    """Validate that a value is non-negative."""
    try:
        num = float(value)
    except (ValueError, TypeError) as e:
        raise ValidationError(name, "must be a numeric type", value) from e

    if num < 0:
        raise ValidationError(name, "must be non-negative", value)


def validate_integer(
    value: Any, name: str, min_val: int | None = None, max_val: int | None = None
) -> int:
    """Validate that a value is an integer, with optional range checking.

    Parameters
    ----------
    value : Any
        Value to validate.
    name : str
        Parameter name (used in error messages).
    min_val : int or None
        Minimum allowed value.
    max_val : int or None
        Maximum allowed value.

    Returns
    -------
    int
        The validated integer.
    """
    try:
        num = int(value)
    except (ValueError, TypeError) as e:
        raise ValidationError(name, "must be an integer", value) from e

    if min_val is not None and num < min_val:
        raise ValidationError(name, f"must be >= {min_val}", value)
    if max_val is not None and num > max_val:
        raise ValidationError(name, f"must be <= {max_val}", value)

    return num


def validate_float_range(
    value: Any, name: str, min_val: float | None = None, max_val: float | None = None
) -> float:
    """Validate that a value is a float, with optional range checking.

    Parameters
    ----------
    value : Any
        Value to validate.
    name : str
        Parameter name (used in error messages).
    min_val : float or None
        Minimum allowed value.
    max_val : float or None
        Maximum allowed value.

    Returns
    -------
    float
        The validated float.
    """
    try:
        num = float(value)
    except (ValueError, TypeError) as e:
        raise ValidationError(name, "must be a float", value) from e

    if min_val is not None and num < min_val:
        raise ValidationError(name, f"must be >= {min_val}", value)
    if max_val is not None and num > max_val:
        raise ValidationError(name, f"must be <= {max_val}", value)

    return num


def validate_not_empty(value: Any, name: str) -> None:
    """Validate that a value is not empty (None or zero-length)."""
    if value is None:
        raise ValidationError(name, "must not be None")
    if isinstance(value, (str, list, tuple, dict)) and len(value) == 0:
        raise ValidationError(name, "must not be empty")


def validate_file_exists(filepath: str, name: str) -> None:
    """Validate that a file exists."""
    if not filepath:
        raise ValidationError(name, "file path must not be empty")
    if not os.path.exists(filepath):
        raise ValidationError(name, f"file does not exist: {filepath}")
    if not os.path.isfile(filepath):
        raise ValidationError(name, f"path is not a file: {filepath}")


def validate_dir_exists(dirpath: str, name: str) -> None:
    """Validate that a directory exists."""
    if not dirpath:
        raise ValidationError(name, "directory path must not be empty")
    if not os.path.exists(dirpath):
        raise ValidationError(name, f"directory does not exist: {dirpath}")
    if not os.path.isdir(dirpath):
        raise ValidationError(name, f"path is not a directory: {dirpath}")


def validate_coords_array(coords: Any, name: str, expected_atoms: int | None = None) -> np.ndarray:
    """Validate a coordinate array.

    Parameters
    ----------
    coords : Any
        Coordinate data (list or numpy array).
    name : str
        Parameter name.
    expected_atoms : int or None
        Expected number of atoms (optional).

    Returns
    -------
    numpy.ndarray
        Validated numpy array of shape ``(N, 3)``.
    """
    if coords is None:
        raise ValidationError(name, "coordinates must not be None")

    try:
        arr = np.asarray(coords, dtype=float)
    except (ValueError, TypeError) as e:
        raise ValidationError(name, "cannot convert to numeric array", coords) from e

    if arr.ndim != 2:
        raise ValidationError(name, f"coordinates must be a 2D array, got {arr.ndim}D")

    if arr.shape[1] != 3:
        raise ValidationError(name, f"coordinates must have shape (N, 3), got {arr.shape}")

    if expected_atoms is not None and arr.shape[0] != expected_atoms:
        raise ValidationError(
            name, f"atom count mismatch, expected {expected_atoms}, got {arr.shape[0]}"
        )

    # Check for NaN and Inf
    if np.any(np.isnan(arr)):
        raise ValidationError(name, "coordinates contain NaN values")
    if np.any(np.isinf(arr)):
        raise ValidationError(name, "coordinates contain Inf values")

    return arr  # type: ignore[no-any-return]


def validate_atom_indices(indices: list[int], name: str, max_index: int) -> None:
    """Validate atom index list.

    Parameters
    ----------
    indices : list[int]
        Atom index list (1-based).
    name : str
        Parameter name.
    max_index : int
        Maximum allowed index.
    """
    if not indices:
        return

    for i, idx in enumerate(indices):
        if not isinstance(idx, int):
            raise ValidationError(name, f"index {i} is not an integer: {idx}")
        if idx < 1:
            raise ValidationError(name, f"atom index must be >= 1 (1-based), got: {idx}")
        if idx > max_index:
            raise ValidationError(name, f"atom index {idx} out of range (max: {max_index})")


def validate_bond_pair(pair: list[int] | tuple, name: str, max_index: int) -> tuple:
    """Validate a bond pair.

    Parameters
    ----------
    pair : list[int] or tuple
        Bond pair ``[a, b]`` or ``(a, b)`` (1-based).
    name : str
        Parameter name.
    max_index : int
        Maximum allowed index.

    Returns
    -------
    tuple
        Validated pair ``(a, b)``.
    """
    if not isinstance(pair, (list, tuple)) or len(pair) != 2:
        raise ValidationError(name, "bond pair must be a list or tuple of length 2", pair)

    a, b = pair
    try:
        a, b = int(a), int(b)
    except (ValueError, TypeError) as e:
        raise ValidationError(name, "bond pair elements must be integers", pair) from e

    if a < 1 or b < 1:
        raise ValidationError(name, f"atom index must be >= 1 (1-based), got: ({a}, {b})")
    if a > max_index or b > max_index:
        raise ValidationError(name, f"atom index out of range (max: {max_index}), got: ({a}, {b})")
    if a == b:
        raise ValidationError(name, f"bond pair must not refer to the same atom: ({a}, {b})")

    return (a, b)


def validate_choice(value: Any, name: str, choices: list[Any]) -> None:
    """Validate that a value is in the allowed choices."""
    if value not in choices:
        raise ValidationError(name, f"must be one of: {choices}", value)


def validate_string_not_empty(value: Any, name: str) -> str:
    """Validate that a value is a non-empty string."""
    if value is None:
        raise ValidationError(name, "must not be None")
    if not isinstance(value, str):
        raise ValidationError(name, "must be a string type", value)
    if not value.strip():
        raise ValidationError(name, "must not be an empty string")
    return value.strip()


# ==============================================================================
# Validation decorator
# ==============================================================================


def validate_params(**validators: Callable[[Any, str], None]) -> Callable[[F], F]:
    """Parameter validation decorator.

    Parameters
    ----------
    **validators : Callable[[Any, str], None]
        Mapping of parameter name to validation function.

    Examples
    --------
    >>> @validate_params(
    ...     threshold=lambda v, n: validate_positive(v, n),
    ...     coords=lambda v, n: validate_not_empty(v, n),
    ... )
    ... def my_function(threshold, coords):
    ...     ...
    """

    def decorator(func: F) -> F:
        @wraps(func)
        def wrapper(*args, **kwargs):
            # Get function signature to map positional arguments
            import inspect

            sig = inspect.signature(func)
            bound = sig.bind_partial(*args, **kwargs)
            bound.apply_defaults()

            # Run validations
            for param_name, validator in validators.items():
                if param_name in bound.arguments:
                    value = bound.arguments[param_name]
                    if value is not None:  # Skip None values (allow optional params)
                        try:
                            validator(value, param_name)
                        except ValidationError:
                            raise
                        except Exception as e:
                            raise ValidationError(param_name, str(e), value) from e

            return func(*args, **kwargs)

        return wrapper  # type: ignore[return-value]

    return decorator


__all__ = [
    "ValidationError",
    "validate_positive",
    "validate_non_negative",
    "validate_integer",
    "validate_float_range",
    "validate_not_empty",
    "validate_file_exists",
    "validate_dir_exists",
    "validate_coords_array",
    "validate_atom_indices",
    "validate_bond_pair",
    "validate_choice",
    "validate_string_not_empty",
    "validate_params",
]
