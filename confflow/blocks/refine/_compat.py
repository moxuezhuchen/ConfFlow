#!/usr/bin/env python3

"""Shared fallback helpers for refine modules."""

from __future__ import annotations

from typing import Any

__all__ = [
    "load_console_bindings",
    "load_hartree_to_kcal",
    "load_numba_runtime",
    "load_refine_data",
]


def _create_progress_fallback():
    return type(
        "Mock",
        (),
        {
            "__enter__": lambda s: s,
            "__exit__": lambda *a: None,
            "add_task": lambda *a, **kw: 0,
            "update": lambda *a, **kw: None,
            "advance": lambda *a, **kw: None,
        },
    )()


def load_console_bindings() -> dict[str, Any]:
    """Load console helpers with a consistent fallback surface."""
    try:
        from ...core.console import (
            console,
            create_progress,
            error,
            heading,
            info,
            print_table,
            success,
            warning,
        )
    except (ImportError, ModuleNotFoundError):
        console = type("Mock", (), {"print": print})

        def create_progress():  # type: ignore[no-redef]
            return _create_progress_fallback()

        def info(message):  # type: ignore[no-redef]
            print(f"INFO: {message}")

        def success(message):  # type: ignore[no-redef]
            print(f"SUCCESS: {message}")

        def warning(message):  # type: ignore[no-redef]
            print(f"WARNING: {message}")

        def error(message):  # type: ignore[no-redef]
            print(f"ERROR: {message}")

        def heading(message):  # type: ignore[no-redef]
            print(f"=== {message} ===")

        def print_table(*args, **kwargs):  # type: ignore[no-redef]
            del args, kwargs
            return None

    return {
        "console": console,
        "create_progress": create_progress,
        "error": error,
        "heading": heading,
        "info": info,
        "print_table": print_table,
        "success": success,
        "warning": warning,
    }


def _fake_numba():
    class FakeNumba:
        __name__ = "FakeNumba"

        @staticmethod
        def njit(*args, **kwargs):
            def decorator(func):
                return func

            del kwargs
            return decorator if not args else args[0]

        @staticmethod
        def jit(*args, **kwargs):
            def decorator(func):
                return func

            del kwargs
            return decorator if not args else args[0]

    return FakeNumba()


def load_numba_runtime(logger_name: str = "confflow"):
    """Load the shared numba runtime or a no-op fallback."""
    try:
        from ...core.utils import get_numba_jit
    except (ImportError, ModuleNotFoundError):
        return _fake_numba()
    return get_numba_jit(logger_name)


def load_refine_data():
    """Load element data used by the refine RMSD engine."""
    try:
        from ...core.data import GV_COVALENT_RADII, PERIODIC_SYMBOLS
    except (ImportError, ModuleNotFoundError):
        PERIODIC_SYMBOLS = ["X", "H", "He", "Li", "Be", "B", "C", "N", "O", "F", "Ne"]
        GV_COVALENT_RADII = {1: 0.31, 6: 0.76, 7: 0.71, 8: 0.66}
    return PERIODIC_SYMBOLS, GV_COVALENT_RADII


def load_hartree_to_kcal() -> float:
    """Load the Hartree to kcal/mol conversion factor."""
    try:
        from ...core.constants import HARTREE_TO_KCALMOL
    except (ImportError, ModuleNotFoundError):
        return 627.5094740631
    return HARTREE_TO_KCALMOL
