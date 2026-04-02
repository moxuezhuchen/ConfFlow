#!/usr/bin/env python3

"""Shared psutil compatibility helpers for calc modules."""

from __future__ import annotations

from typing import Any

__all__ = [
    "maybe_import_psutil",
    "psutil_exception_types",
]


def maybe_import_psutil() -> Any | None:
    """Import ``psutil`` when available, otherwise return ``None``."""
    try:
        import psutil  # type: ignore[import-untyped]
    except ImportError:
        return None
    return psutil


def psutil_exception_types(psutil_module: Any | None) -> tuple[type[BaseException], ...]:
    """Return supported psutil-related exception types, tolerant of test doubles."""
    base: tuple[type[BaseException], ...] = (AttributeError, OSError, RuntimeError)
    err_type = getattr(psutil_module, "Error", None)
    if isinstance(err_type, type) and issubclass(err_type, BaseException):
        return (err_type, *base)
    return base
