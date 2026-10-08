#!/usr/bin/env python3

"""Whole-tree native-isolation guard (B1 regression).

The vendor-QC ``PATH`` scrub must apply to every test under ``tests/``,
independent of pytest collection order. It used to live only in
``tests/v4/conftest.py`` as a directory-scoped autouse fixture, so a
``tests/``-level run collected after ``tests/test_script_steps.py`` (which
imports ``tests.v4._builders`` at module level and splits the ``tests/v4``
collector node, orphaning the fixture) executed with the real vendor
directories still on ``PATH``. The module-level ``tests.v4._builders``
import below reproduces that trigger inside this file.
"""

from __future__ import annotations

import os
import shutil

import pytest

from tests.conftest import BLOCKED_NATIVE_PREFIXES, _scrubbed_path
from tests.v4._builders import structure
from tests.v4.conftest import BLOCKED_NATIVE_PREFIXES as V4_BLOCKED_PREFIXES
from tests.v4.conftest import _scrubbed_path as V4_SCRUBBED_PATH


def test_v4_conftest_reexports_tree_guard() -> None:
    """The legacy ``tests.v4.conftest`` import path keeps working."""
    assert V4_BLOCKED_PREFIXES == BLOCKED_NATIVE_PREFIXES
    assert V4_SCRUBBED_PATH is _scrubbed_path


def test_vendor_dirs_absent_from_path() -> None:
    if os.environ.get("CONFFLOW_ALLOW_REAL_QC") == "1":
        pytest.skip("explicit real-native suite opts out of the PATH guard")
    offenders = [
        entry
        for entry in os.environ.get("PATH", "").split(os.pathsep)
        if entry
        and any(
            os.path.normpath(entry) == prefix or os.path.normpath(entry).startswith(prefix + os.sep)
            for prefix in BLOCKED_NATIVE_PREFIXES
        )
    ]
    assert offenders == [], f"vendor QC dirs leaked onto PATH: {offenders}"


def test_bare_qc_names_never_resolve_to_vendor_dirs() -> None:
    if os.environ.get("CONFFLOW_ALLOW_REAL_QC") == "1":
        pytest.skip("explicit real-native suite opts out of the PATH guard")
    for name in ("orca", "g16"):
        resolved = shutil.which(name)
        assert resolved is None or not any(
            os.path.realpath(resolved) == prefix
            or os.path.realpath(resolved).startswith(prefix + os.sep)
            for prefix in BLOCKED_NATIVE_PREFIXES
        ), f"bare {name!r} resolves to a vendor install: {resolved}"


def test_builder_import_does_not_weaken_guard() -> None:
    """Using the shared builders leaves the scrubbed ``PATH`` intact."""
    record = structure("guard-probe")
    assert record.id == "guard-probe"
    if os.environ.get("CONFFLOW_ALLOW_REAL_QC") == "1":
        pytest.skip("explicit real-native suite opts out of the PATH guard")
    assert "/opt/g16" not in os.environ.get("PATH", "").split(os.pathsep)
