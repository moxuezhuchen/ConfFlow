#!/usr/bin/env python3
"""Tests for tools/affected_tests.py on small temporary git repos."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

TOOL = Path(__file__).resolve().parents[1] / "tools" / "affected_tests.py"


def _git(root: Path, *args: str) -> str:
    """Run a git command in root and return its stdout."""
    out = subprocess.run(["git", *args], cwd=root, capture_output=True, text=True, check=True)
    return out.stdout


def _make_repo(tmp_path: Path, files: dict[str, str]) -> tuple[Path, str]:
    """Init a git repo with the given files and return root and base sha."""
    root = tmp_path / "repo"
    for rel, content in files.items():
        target = root / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    _git(root, "init", "-q")
    _git(root, "-c", "user.email=t@t", "-c", "user.name=t", "add", "-A")
    _git(
        root,
        "-c",
        "user.email=t@t",
        "-c",
        "user.name=t",
        "commit",
        "-qm",
        "base",
    )
    return root, _git(root, "rev-parse", "HEAD").strip()


def _affected(root: Path, base: str, *extra: str) -> list[str]:
    """Run the tool against root and return selected files as a list."""
    out = subprocess.run(
        [sys.executable, str(TOOL), "--base", base, "--root", str(root), *extra],
        capture_output=True,
        text=True,
        check=True,
    )
    return [line for line in out.stdout.splitlines() if line.strip()]


def test_direct_import_is_selected_when_module_changes(tmp_path: Path) -> None:
    root, base = _make_repo(
        tmp_path,
        {
            "confflow/__init__.py": "",
            "confflow/alpha.py": "VALUE = 1\n",
            "tests/__init__.py": "",
            "tests/test_alpha.py": (
                "from confflow.alpha import VALUE\n"
                "\n"
                "\n"
                "def test_value():\n"
                "    assert VALUE == 1\n"
            ),
        },
    )
    (root / "confflow" / "alpha.py").write_text("VALUE = 2\n", encoding="utf-8")
    assert "tests/test_alpha.py" in _affected(root, base)


def test_from_package_import_of_submodule_is_selected(tmp_path: Path) -> None:
    root, base = _make_repo(
        tmp_path,
        {
            "confflow/__init__.py": "",
            "confflow/sub/__init__.py": "",
            "confflow/sub/mod.py": "VALUE = 1\n",
            "tests/__init__.py": "",
            "tests/test_sub.py": (
                "from confflow.sub import mod\n"
                "\n"
                "\n"
                "def test_mod():\n"
                "    assert mod.VALUE == 1\n"
            ),
        },
    )
    (root / "confflow" / "sub" / "mod.py").write_text("VALUE = 2\n", encoding="utf-8")
    assert "tests/test_sub.py" in _affected(root, base)


def test_transitive_relative_import_is_selected_within_depth(tmp_path: Path) -> None:
    root, base = _make_repo(
        tmp_path,
        {
            "confflow/__init__.py": "",
            "confflow/leaf.py": "VALUE = 1\n",
            "confflow/mid.py": (
                "from .leaf import VALUE\n" "\n" "\n" "def get():\n" "    return VALUE\n"
            ),
            "tests/__init__.py": "",
            "tests/test_mid.py": (
                "from confflow.mid import get\n"
                "\n"
                "\n"
                "def test_get():\n"
                "    assert get() == 1\n"
            ),
        },
    )
    (root / "confflow" / "leaf.py").write_text("VALUE = 2\n", encoding="utf-8")
    assert "tests/test_mid.py" in _affected(root, base)
    assert "tests/test_mid.py" not in _affected(root, base, "--depth", "1")


def test_unrelated_test_is_not_selected(tmp_path: Path) -> None:
    root, base = _make_repo(
        tmp_path,
        {
            "confflow/__init__.py": "",
            "confflow/alpha.py": "VALUE = 1\n",
            "confflow/beta.py": "OTHER = 2\n",
            "tests/__init__.py": "",
            "tests/test_alpha.py": (
                "from confflow.alpha import VALUE\n"
                "\n"
                "\n"
                "def test_value():\n"
                "    assert VALUE == 1\n"
            ),
            "tests/test_beta.py": (
                "from confflow.beta import OTHER\n"
                "\n"
                "\n"
                "def test_other():\n"
                "    assert OTHER == 2\n"
            ),
        },
    )
    (root / "confflow" / "alpha.py").write_text("VALUE = 9\n", encoding="utf-8")
    selected = _affected(root, base)
    assert "tests/test_alpha.py" in selected
    assert "tests/test_beta.py" not in selected


def test_pure_deletion_still_selects_dependent_test(tmp_path: Path) -> None:
    root, base = _make_repo(
        tmp_path,
        {
            "confflow/__init__.py": "",
            "confflow/alpha.py": "VALUE = 1\n",
            "tests/__init__.py": "",
            "tests/test_alpha.py": (
                "from confflow.alpha import VALUE\n"
                "\n"
                "\n"
                "def test_value():\n"
                "    assert VALUE == 1\n"
            ),
        },
    )
    _git(root, "rm", "-q", "confflow/alpha.py")
    assert "tests/test_alpha.py" in _affected(root, base)


def test_no_changes_outputs_empty(tmp_path: Path) -> None:
    root, base = _make_repo(
        tmp_path,
        {
            "confflow/__init__.py": "",
            "confflow/alpha.py": "VALUE = 1\n",
            "tests/__init__.py": "",
            "tests/test_alpha.py": (
                "from confflow.alpha import VALUE\n"
                "\n"
                "\n"
                "def test_value():\n"
                "    assert VALUE == 1\n"
            ),
        },
    )
    assert _affected(root, base) == []
