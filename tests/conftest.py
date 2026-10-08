#!/usr/bin/env python3

"""Test collection configuration and shared fixtures."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest

# Create process-isolated temp directory for basetemp
_temp_base = Path(tempfile.gettempdir()) / f"confflow_pytest_{os.getpid()}"
_temp_base.mkdir(exist_ok=True)


def pytest_configure(config):
    """Configure pytest to use isolated temp directory for basetemp."""
    # Set basetemp if not already set via command line
    if config.option.basetemp is None:
        config.option.basetemp = str(_temp_base / "basetemp")


# All old test files have been merged and removed; no need to ignore any
collect_ignore: list[str] = []


#: Vendor install roots that the test suite must never resolve via ``PATH``.
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
    """Strip system ORCA/Gaussian install dirs from ``PATH`` for every test.

    The fake suite must never accidentally launch a system ORCA/Gaussian
    install: bare ``orca``/``g16`` names fail closed unless the test itself
    prepends its own fake executable. The guard lives here (not in a
    subdirectory conftest) so it applies to the whole ``tests/`` tree no
    matter in which order pytest collects the files: a directory-scoped
    autouse fixture can be orphaned when an unrelated module import splits
    the collector node. The only escape hatch is
    ``CONFFLOW_ALLOW_REAL_QC=1`` for an explicit real-native suite.
    """
    if _ALLOW_REAL_QC:
        return
    monkeypatch.setenv("PATH", _scrubbed_path(os.environ.get("PATH", "")))


@pytest.fixture
def input_xyz(tmp_path: Path) -> Path:
    """Create a minimal input.xyz file and return its Path."""
    p = tmp_path / "input.xyz"
    p.write_text("2\ntest\nC 0 0 0\nH 0 0 1\n", encoding="utf-8")
    return p


@pytest.fixture
def config_yaml(tmp_path: Path) -> Path:
    """Create a minimal config yaml file and return its Path."""
    p = tmp_path / "config.yaml"
    p.write_text("global: {}\nsteps: []\n")
    return p


@pytest.fixture
def cd_tmp(tmp_path: Path, monkeypatch):
    """Change CWD to `tmp_path` for tests that require a working directory."""
    monkeypatch.chdir(tmp_path)
    return tmp_path


@pytest.fixture
def fake_qc_executables(tmp_path: Path) -> dict[str, str]:
    """Create hermetic fake QC executables (``orca`` and ``g16``).

    Writes two tiny real shell scripts (a ``#!/bin/sh`` header plus
    ``exit 0``) with the real exec bit set and returns
    ``{"orca": <abs path>, "g16": <abs path>}``.

    Downstream resolution computes the real realpath and the real SHA-256 of
    these files; the scripts are never executed (identity resolution is
    read-only and launches no subprocess). Tests must not mock
    ``resolve_executable_identity`` or the C fingerprint.
    """
    bin_dir = tmp_path / "fake_qc_bin"
    bin_dir.mkdir(parents=True, exist_ok=True)
    paths: dict[str, str] = {}
    for name in ("orca", "g16"):
        exe = bin_dir / name
        exe.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        exe.chmod(0o755)
        paths[name] = str(exe)
    return paths


@pytest.fixture
def fake_qc_executables_on_path(fake_qc_executables: dict[str, str], monkeypatch) -> dict[str, str]:
    """Prepend the fake QC bin dir to ``PATH`` (scoped, auto-restored).

    Opt-in per module via ``pytestmark = pytest.mark.usefixtures(...)`` so
    fail-closed tests that name explicit paths (``/nonexistent/orca``,
    directories, unreadable entrypoints) are never masked: those spellings
    do not resolve via ``PATH`` and still fail closed.
    """
    bin_dir = str(Path(fake_qc_executables["orca"]).parent)
    monkeypatch.setenv("PATH", bin_dir + os.pathsep + os.environ.get("PATH", ""))
    return fake_qc_executables


@pytest.fixture(autouse=True, scope="function")
def guard_repo_root_pollution():
    """Prevent tests from creating chem_tasks_* directories in repo root."""
    from pathlib import Path

    repo_root = Path(__file__).parent.parent
    before = set(repo_root.glob("chem_tasks_*"))

    yield

    after = set(repo_root.glob("chem_tasks_*"))
    new_dirs = after - before
    if new_dirs:
        # Clean up and fail
        for d in new_dirs:
            if d.is_dir():
                import shutil

                shutil.rmtree(d)
        pytest.fail(
            f"Test created chem_tasks_* directories in repo root: {[d.name for d in new_dirs]}. "
            "Use tmp_path or resume_dir parameter to avoid polluting repo root."
        )


@pytest.fixture
def jobdesk():
    """Return the optional JobDesk consumer surfaces (skip when absent)."""
    from tests.v4 import jobdesk_integration

    try:
        return jobdesk_integration.load_jobdesk()
    except jobdesk_integration.JobDeskUnavailable as exc:
        pytest.skip(str(exc))
    except jobdesk_integration.JobDeskMisconfigured as exc:
        pytest.fail(str(exc), pytrace=False)
    except jobdesk_integration.JobDeskShaMismatch as exc:
        pytest.fail(str(exc), pytrace=False)
