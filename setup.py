"""Setuptools build hook for reproducible ConfFlow wheel provenance."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

from setuptools import setup
from setuptools.command.build_py import build_py as _build_py

ROOT = Path(__file__).resolve().parent


def _git_output(*args: str) -> str | None:
    try:
        result = subprocess.run(
            ["git", *args],
            cwd=ROOT,
            check=False,
            capture_output=True,
            text=True,
        )
    except OSError:
        return None
    if result.returncode != 0:
        return None
    return result.stdout.strip()


class BuildPyWithProvenance(_build_py):
    """Write git provenance to build_lib without modifying the source tree."""

    def run(self) -> None:
        super().run()
        commit = _git_output("rev-parse", "HEAD")
        status = _git_output("status", "--porcelain", "--untracked-files=all")
        dirty: bool | None = None if commit is None or status is None else bool(status)
        target = Path(self.build_lib) / "confflow" / "__build__.py"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
            '"""Build provenance populated by setup.py."""\n'
            f"COMMIT: str | None = {commit!r}\n"
            f"DIRTY: bool | None = {dirty!r}\n"
            f"WHEEL_FILENAME: str | None = {os.environ.get('CONFFLOW_WHEEL_FILENAME')!r}\n"
            f"WHEEL_SHA256: str | None = {os.environ.get('CONFFLOW_WHEEL_SHA256')!r}\n",
            encoding="utf-8",
        )


setup(cmdclass={"build_py": BuildPyWithProvenance})
