#!/usr/bin/env python3

"""ConfFlow unified exception hierarchy.

All custom exceptions inherit from ConfFlowError so that callers can catch
them with a single ``except ConfFlowError`` clause.
"""

from __future__ import annotations

from typing import Any

__all__ = [
    "ConfFlowError",
    "InputFileError",
    "XYZFormatError",
    "ValidationError",
    "ConfigurationError",
    "PathSafetyError",
    "ExecutionPolicyError",
    "CalculationInputError",
    "CalculationExecutionError",
    "CalculationParseError",
    "StopRequestedError",
]


class ConfFlowError(Exception):
    """Base exception for all ConfFlow errors."""

    pass


class InputFileError(ConfFlowError):
    """Input file related error."""

    def __init__(self, message: str, filepath: str | None = None):
        self.filepath = filepath
        super().__init__(
            f"Input file error: {message}" + (f" (file: {filepath})" if filepath else "")
        )


class XYZFormatError(InputFileError):
    """XYZ file format error."""

    def __init__(self, message: str, filepath: str | None = None, line_num: int | None = None):
        self.line_num = line_num
        line_info = f", line {line_num}" if line_num else ""
        super().__init__(f"XYZ format error: {message}{line_info}", filepath)


class ValidationError(ConfFlowError, ValueError):
    """Validation error.

    Inherits from both ConfFlowError and ValueError for compatibility
    with either catch style.
    """

    def __init__(self, param_name: str, message: str, value: Any = None):
        self.param_name = param_name
        self.value = value
        full_msg = f"Parameter '{param_name}' validation failed: {message}"
        if value is not None:
            full_msg += f" (current value: {value!r})"
        super().__init__(full_msg)


class ConfigurationError(ConfFlowError, ValueError):
    """Configuration error.

    Inherits from both ConfFlowError and ValueError for compatibility
    with either catch style.
    """

    def __init__(self, message: str, errors: list[str] | None = None):
        self.errors = errors or []
        if errors:
            full_msg = f"{message}:\n" + "\n".join(f"  - {e}" for e in errors)
        else:
            full_msg = message
        super().__init__(full_msg)


class PathSafetyError(ConfFlowError, ValueError):
    """Raised when a configured path escapes the allowed execution boundary."""


class ExecutionPolicyError(ConfFlowError, ValueError):
    """Raised when an executable setting violates the execution policy."""


class CalculationInputError(ConfFlowError, RuntimeError):
    """Calculation input preparation/generation error."""


class CalculationExecutionError(ConfFlowError, RuntimeError):
    """External program execution error."""


class CalculationParseError(ConfFlowError, RuntimeError):
    """Calculation output parse/validation error."""


class StopRequestedError(ConfFlowError, RuntimeError):
    """Raised when a STOP signal interrupts a running calculation."""
