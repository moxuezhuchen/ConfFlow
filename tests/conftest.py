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
