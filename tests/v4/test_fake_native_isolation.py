#!/usr/bin/env python3

"""Fake-suite native isolation guard.

Proves the V4 fake suite cannot accidentally launch a system ORCA/Gaussian
install: vendor directories are scrubbed from ``PATH`` (see
``tests/v4/conftest.py``), so bare ``orca``/``g16`` names only resolve when
a test explicitly prepends its own counting wrapper.  The explicit
real-native suite opts out via ``CONFFLOW_ALLOW_REAL_QC=1`` and lives
outside this file.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

import pytest

from tests.v4.conftest import BLOCKED_NATIVE_PREFIXES, _scrubbed_path


def _path_entries() -> list[str]:
    return os.environ.get("PATH", "").split(os.pathsep)


class TestFakeNativeIsolation:
    """Vendor QC binaries stay unreachable from the fake suite."""

    def test_vendor_dirs_absent_from_path(self) -> None:
        if os.environ.get("CONFFLOW_ALLOW_REAL_QC") == "1":
            pytest.skip("explicit real-native suite opts out of the PATH guard")
        offenders = [
            entry
            for entry in _path_entries()
            if entry
            and any(
                os.path.normpath(entry) == prefix
                or os.path.normpath(entry).startswith(prefix + os.sep)
                for prefix in BLOCKED_NATIVE_PREFIXES
            )
        ]
        assert offenders == [], f"vendor QC dirs leaked onto PATH: {offenders}"

    def test_bare_qc_names_never_resolve_to_vendor_dirs(self) -> None:
        if os.environ.get("CONFFLOW_ALLOW_REAL_QC") == "1":
            pytest.skip("explicit real-native suite opts out of the PATH guard")
        for name in ("orca", "g16"):
            resolved = shutil.which(name)
            assert resolved is None or not any(
                os.path.realpath(resolved) == prefix
                or os.path.realpath(resolved).startswith(prefix + os.sep)
                for prefix in BLOCKED_NATIVE_PREFIXES
            ), f"bare {name!r} resolves to a vendor install: {resolved}"

    def test_blocked_prefixes_cover_vendor_installs(self) -> None:
        assert "/opt/orca611" in BLOCKED_NATIVE_PREFIXES
        assert "/opt/g16" in BLOCKED_NATIVE_PREFIXES

    def test_scrub_drops_vendor_shim_dirs(self, tmp_path: Path) -> None:
        # A directory shimming ``orca`` into a vendor prefix (symlink is
        # followed but never executed) is dropped while innocent entries
        # are kept.
        shim = tmp_path / "shim"
        shim.mkdir()
        (shim / "orca").symlink_to("/opt/orca611/orca")
        cleaned = _scrubbed_path(os.pathsep.join([str(shim), "/opt/g16", "/usr/bin", ""])).split(
            os.pathsep
        )
        assert str(shim) not in cleaned
        assert "/opt/g16" not in cleaned
        assert "/usr/bin" in cleaned
