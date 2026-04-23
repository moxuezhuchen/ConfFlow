#!/usr/bin/env python3

"""Shared test helpers for the tests/ suite.

Keep helpers minimal and stable — pure helpers that don't import
project internals so they are safe to import across tests.
"""

from __future__ import annotations

import builtins
import importlib
from collections.abc import Callable
from pathlib import Path
from unittest.mock import patch

import pytest


def write_file(path: Path, content: str, encoding: str = "utf8") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding=encoding)
    return path


def make_config(tmp_path: Path, name: str = "confflow.yaml", content: str | None = None) -> Path:
    if content is None:
        content = "name: test\n"
    p = tmp_path / name
    write_file(p, content)
    return p


def assert_raises_match(exc_type: type, match: str, func: Callable, *args, **kwargs):
    """Run *func* and assert an exception with a matching message is raised.

    Calls ``func(*args, **kwargs)`` and checks that *exc_type* is raised
    and that *match* is a substring of the exception message.
    """
    with pytest.raises(exc_type) as excinfo:
        func(*args, **kwargs)
    assert match in str(excinfo.value)


class FakeRunner:
    """Very small fake runner to record calls in tests that need a stub.

    Usage:
        r = FakeRunner()
        result = r.run('cmd')
        assert r.calls
    """

    def __init__(self):
        self.calls = []

    def run(self, *args, **kwargs):
        self.calls.append((args, kwargs))

        class _R:
            returncode = 0
            stdout = ""
            stderr = ""

        return _R()


def reload_with_import_block(module, blocked_top_level_name: str):
    """Reload `module` while making `import blocked_top_level_name` raise ImportError."""
    real_import = builtins.__import__

    def fake_import(name, globals=None, locals=None, fromlist=(), level=0):
        if name == blocked_top_level_name:
            raise ImportError(f"blocked: {blocked_top_level_name}")
        return real_import(name, globals, locals, fromlist, level)

    with patch("builtins.__import__", side_effect=fake_import):
        return importlib.reload(module)


# ---------------------------------------------------------------------------
# Reusable fake/stub objects for calc-layer tests
# ---------------------------------------------------------------------------


class FakeResultsDB:
    """Minimal in-memory ResultsDB stub.

    Parameters
    ----------
    fixed_results : list[dict] | None
        If given, ``get_all_results`` returns this list verbatim.
        Otherwise it returns whatever was inserted via ``insert_result``.
    """

    def __init__(self, *args, fixed_results: list[dict] | None = None, **kwargs):
        self.inserted: list[dict] = []
        self._fixed = fixed_results

    def get_result_by_job_name(self, job_name: str):
        for r in reversed(self.inserted):
            if r.get("job_name") == job_name:
                return r
        return None

    def insert_result(self, res: dict):
        self.inserted.append(res)

    def get_all_results(self):
        if self._fixed is not None:
            return list(self._fixed)
        return list(self.inserted)

    def close(self):
        pass


class FakeFuture:
    """Minimal Future-like object that wraps a pre-computed result."""

    def __init__(self, result, *, done: bool = True, cancelled: bool = False):
        self._result = result
        self._done = done
        self._cancelled = cancelled

    def result(self):
        return self._result

    def done(self):
        return self._done

    def cancelled(self):
        return self._cancelled


class FakeExecutor:
    """Minimal synchronous executor that runs jobs immediately in-process."""

    def __init__(self, *args, **kwargs):
        pass

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def submit(self, fn, arg):
        return FakeFuture(fn(arg))


__all__ = [
    "write_file",
    "make_config",
    "assert_raises_match",
    "FakeRunner",
    "reload_with_import_block",
    "FakeResultsDB",
    "FakeFuture",
    "FakeExecutor",
]
