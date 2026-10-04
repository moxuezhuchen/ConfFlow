#!/usr/bin/env python3

"""Fixture tests for the scripts/ entry scan of ``tools/reachability.py``.

Card D tool test (docs/refactor/tools-acc, not part of the product
``tests/`` collect).  Run explicitly with::

    pytest docs/refactor/tools-acc/test_reachability_scripts.py

Covers: script roots under ``scripts/`` including subdirectories, literal
``importlib.import_module`` roots, unreferenced modules staying unreachable,
the old CLI output format, and the real-repo ``release_dependencies``
correction.
"""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
TOOL_PATH = REPO_ROOT / "docs" / "refactor" / "tools" / "reachability.py"


def _load_tool():
    spec = importlib.util.spec_from_file_location("reachability_under_test", TOOL_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _make_tree(tmp_path: Path) -> Path:
    cf = tmp_path / "cf"
    pkg = cf / "confflow"
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").write_text("", encoding="utf-8")
    (pkg / "helper.py").write_text("value = 1\n", encoding="utf-8")
    (pkg / "orphan.py").write_text("value = 2\n", encoding="utf-8")
    scripts = cf / "scripts"
    scripts.mkdir()
    (scripts / "use_helper.py").write_text(
        "from confflow import helper as h\n",
        encoding="utf-8",
    )
    (scripts / "unrelated.py").write_text("import os\n", encoding="utf-8")
    sub = scripts / "sub"
    sub.mkdir()
    (sub / "literal.py").write_text(
        "import importlib\n\nhelper = importlib.import_module('confflow.helper')\n",
        encoding="utf-8",
    )
    return cf


def test_script_roots_cover_subdirs_and_unreferenced_stays_unreachable(tmp_path) -> None:
    reach = _load_tool()
    cf = _make_tree(tmp_path)
    result = reach.compute(cf, "confflow")

    entries = result["script_entry_modules"]
    assert entries["scripts/use_helper.py"] == ["confflow.helper"]
    assert entries["scripts/sub/literal.py"] == ["confflow.helper"]
    assert "scripts/unrelated.py" not in entries

    # Closure: helper reachable through the script root (top-level package
    # module enumeration starts at submodules, so the package itself is not
    # listed); the module nothing references stays unreachable.
    assert "confflow.helper" not in result["unreachable"]
    assert "confflow.orphan" in result["unreachable"]


def test_cli_output_format_preserved(tmp_path) -> None:
    cf = _make_tree(tmp_path)
    proc = subprocess.run(
        [sys.executable, str(TOOL_PATH), "--cf", str(cf)],
        capture_output=True,
        text=True,
        check=False,
    )
    # Exit 1: the minimal fixture lacks every fixed ENTRY_MODULE; the
    # legacy one-unreachable-per-line stdout is unchanged.
    assert proc.returncode == 1
    assert proc.stdout == "confflow.orphan\n"

    proc_json = subprocess.run(
        [sys.executable, str(TOOL_PATH), "--cf", str(cf), "--json"],
        capture_output=True,
        text=True,
        check=False,
    )
    payload = json.loads(proc_json.stdout)
    assert payload["unreachable"] == ["confflow.orphan"]


def test_release_dependencies_reachable_via_scripts_in_real_repo() -> None:
    reach = _load_tool()
    result = reach.compute(REPO_ROOT, "confflow")

    entries = result["script_entry_modules"]
    assert entries["scripts/install_release_wheel.py"] == [
        "confflow.install_provenance",
        "confflow.release_dependencies",
    ]
    # The former false negative is fixed...
    assert "confflow.release_dependencies" not in result["unreachable"]
    # ...without marking everything reachable: known test-only / analysis
    # modules stay unreachable.
    assert "confflow.analysis.pes" in result["unreachable"]
    assert "confflow.application.execution.memory" in result["unreachable"]


def test_script_import_through_lazy_facade_resolves_target(tmp_path) -> None:
    """Regression (D follow-up) for script imports through a lazy facade.

    A script doing ``from <package> import <name>`` must pull the lazy
    ``_LAZY_EXPORTS`` target module into the closure, exactly like a
    package-module import does.
    """
    reach = _load_tool()
    cf = tmp_path / "cf_lazy"
    pkg = cf / "confflow"
    core = pkg / "core"
    core.mkdir(parents=True)
    (pkg / "__init__.py").write_text("", encoding="utf-8")
    (core / "__init__.py").write_text(
        "_LAZY_EXPORTS = {'value': ('confflow.hidden', 'value')}\n",
        encoding="utf-8",
    )
    (pkg / "hidden.py").write_text("value = 1\n", encoding="utf-8")
    (pkg / "orphan.py").write_text("value = 2\n", encoding="utf-8")
    scripts = cf / "scripts"
    scripts.mkdir()
    (scripts / "use_facade.py").write_text(
        "from confflow.core import value\n",
        encoding="utf-8",
    )

    result = reach.compute(cf, "confflow")

    entries = result["script_entry_modules"]
    assert entries["scripts/use_facade.py"] == ["confflow.core", "confflow.hidden"]
    # The actually-referenced lazy target is reachable...
    assert "confflow.hidden" not in result["unreachable"]
    # ...while modules nothing references stay unreachable.
    assert "confflow.orphan" in result["unreachable"]
