#!/usr/bin/env python3

"""Shared fixtures for the V4 test suite.

Pure in-memory tests do not need fixtures beyond pytest's ``tmp_path``; the
conftest exists so future file-backed document corpora can live here without
touching the legacy test configuration.

Native-isolation guard: the fake suite must never accidentally launch a
system ORCA/Gaussian install.  Every V4 test runs with vendor install
directories scrubbed from ``PATH`` (bare ``orca``/``g16`` names then fail
closed unless the test itself prepends its counting wrapper).  The only
escape hatch is ``CONFFLOW_ALLOW_REAL_QC=1`` for an explicit real-native
suite; nothing in the fake suite sets it.
"""

from __future__ import annotations

import os

import pytest

#: Vendor install roots that the fake suite must never resolve via ``PATH``.
BLOCKED_NATIVE_PREFIXES = ("/opt/orca611", "/opt/orca", "/opt/g16", "/opt/gauopen")

#: Bare executable names that must never resolve into a blocked prefix.
_BLOCKED_BASENAMES = ("orca", "g16")

_ALLOW_REAL_QC = os.environ.get("CONFFLOW_ALLOW_REAL_QC") == "1"


def _is_blocked_prefix(real_path: str) -> bool:
    """Return whether *real_path* lives under a blocked vendor prefix."""
    return any(
        real_path == prefix or real_path.startswith(prefix + os.sep)
        for prefix in BLOCKED_NATIVE_PREFIXES
    )


def _exposes_vendor_binary(directory: str) -> bool:
    """Return whether *directory* shims a blocked ``orca``/``g16`` binary.

    Catches symlinks such as ``/usr/local/bin/orca -> /opt/orca611/orca``:
    the link is followed via ``realpath`` (nothing is ever executed).
    """
    if not directory or not os.path.isdir(directory):
        return False
    for name in _BLOCKED_BASENAMES:
        candidate = os.path.join(directory, name)
        if not (os.path.islink(candidate) or os.path.exists(candidate)):
            continue
        try:
            real = os.path.realpath(candidate)
        except OSError:
            continue
        if _is_blocked_prefix(real):
            return True
    return False


def _scrubbed_path(value: str) -> str:
    """Return *value* with vendor install and shim entries removed."""
    kept: list[str] = []
    for entry in value.split(os.pathsep):
        normalized = os.path.normpath(entry)
        if _is_blocked_prefix(normalized):
            continue
        if _exposes_vendor_binary(entry):
            continue
        kept.append(entry)
    return os.pathsep.join(kept)


@pytest.fixture(autouse=True)
def _scrub_real_qc_from_path(monkeypatch: pytest.MonkeyPatch) -> None:
    """Strip system ORCA/Gaussian install dirs from ``PATH`` for every V4 test."""
    if _ALLOW_REAL_QC:
        return
    monkeypatch.setenv("PATH", _scrubbed_path(os.environ.get("PATH", "")))
