#!/usr/bin/env python3

"""Test collection configuration and shared fixtures."""

from __future__ import annotations

from pathlib import Path

import pytest

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
def sync_executor(monkeypatch):
    """Monkeypatch a synchronous ProcessPoolExecutor for deterministic tests."""

    class SyncExecutor:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def map(self, func, *iterables, **kwargs):
            return map(func, *iterables)

    monkeypatch.setattr("confflow.blocks.refine.processor.ProcessPoolExecutor", SyncExecutor)
    return SyncExecutor


@pytest.fixture(autouse=True, scope="function")
def guard_repo_root_pollution():
    """Prevent tests from creating chem_tasks_* directories in repo root."""
    import os
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

