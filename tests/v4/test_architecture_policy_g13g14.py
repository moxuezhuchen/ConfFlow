#!/usr/bin/env python3
"""G13/G14 confgen purity policy coverage on the surviving ConfGen subjects (test-only).

Every case goes through the real ``scan()`` entry point with an isolated single
rule (``RULES=[rule]``), asserting per rule-id firing on violating fixtures and
silence on comment/docstring-only (prose-positive) fixtures. The subjects are the
surviving ConfGen executors (``confgen_executor.py``, ``transform_executor.py``)
for AP-100..AP-102 and the ConfGen science package for AP-105. The rules for the
removed engine's modules (kernel, model, planner, ring/torsion components, the
``__getattr__`` guard and the ``as_kernel_target`` owner) were deleted with them.
"""

from __future__ import annotations

from pathlib import Path

import pytest

import tools.architecture_policy as policy

EXECUTOR = "confflow/execution/confgen_executor.py"
TRANSFORM = "confflow/execution/transform_executor.py"
SCIENCE = "confflow/science/confgen/search.py"


def _write(root: Path, rel: str, content: str) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _scan_one(tmp_path: Path, rule_id: str) -> list[str]:
    rule = next(r for r in policy.RULES if r["id"] == rule_id)
    saved = policy.RULES
    try:
        policy.RULES = [rule]
        return [v["rule"] for v in policy.scan(tmp_path)]
    finally:
        policy.RULES = saved


@pytest.mark.parametrize(
    "source",
    [
        "class Probe:\n    register_component()\n",
        "@register_component()\ndef probe():\n    pass\n",
        "def probe(value=register_component()):\n    pass\n",
        "factory = lambda value=register_component(): value\n",
    ],
)
def test_ap105_import_executed_definitions_fire(tmp_path: Path, source: str) -> None:
    _write(tmp_path, SCIENCE, source)
    assert "AP-105" in _scan_one(tmp_path, "AP-105")


# --- AP-100 axis literals -------------------------------------------------


def test_ap100_fires_on_dict_key_and_compare(tmp_path: Path) -> None:
    _write(tmp_path, EXECUTOR, 'x = {"rings": 1}\n')
    assert "AP-100" in _scan_one(tmp_path, "AP-100")


def test_ap100_fires_on_transform_literal_other_than_the_topology_member(tmp_path: Path) -> None:
    _write(tmp_path, TRANSFORM, 'x = {"torsions": 1}\n')
    assert "AP-100" in _scan_one(tmp_path, "AP-100")


def test_ap100_silent_on_transform_coordination_topology_member(tmp_path: Path) -> None:
    _write(tmp_path, TRANSFORM, 'allowed = {"coordination", "bonds"}\n')
    assert "AP-100" not in _scan_one(tmp_path, "AP-100")


def test_ap100_ignores_comment_and_docstring(tmp_path: Path) -> None:
    _write(
        tmp_path,
        EXECUTOR,
        '"""prose mentions "rings" only."""\n# comment with torsions\nx = 1\n',
    )
    assert "AP-100" not in _scan_one(tmp_path, "AP-100")


# --- AP-101 axis attributes (Load/Store/Del) -------------------------------


@pytest.mark.parametrize(
    "body",
    [
        "def f(o):\n    return o.rings\n",
        "def f(o):\n    o.torsions = 1\n",
        "def f(o):\n    del o.coordination\n",
    ],
)
def test_ap101_fires_on_load_store_del(tmp_path: Path, body: str) -> None:
    _write(tmp_path, EXECUTOR, body)
    assert "AP-101" in _scan_one(tmp_path, "AP-101")


def test_ap101_ignores_comment_and_docstring(tmp_path: Path) -> None:
    _write(tmp_path, EXECUTOR, '"""prose .rings only."""\n# o.rings comment\nx = 1\n')
    assert "AP-101" not in _scan_one(tmp_path, "AP-101")


# --- AP-102 component imports (incl. TYPE_CHECKING + importlib) ------------


def test_ap102_fires_on_type_checking_and_importlib(tmp_path: Path) -> None:
    _write(
        tmp_path,
        EXECUTOR,
        "from typing import TYPE_CHECKING\n"
        "if TYPE_CHECKING:\n"
        "    from confflow.science.confgen.coordination import perception\n",
    )
    assert "AP-102" in _scan_one(tmp_path, "AP-102")
    _write(
        tmp_path,
        EXECUTOR,
        'import importlib\nm = importlib.import_module("confflow.science.confgen.coordination.perception")\n',
    )
    assert "AP-102" in _scan_one(tmp_path, "AP-102")


def test_ap102_ignores_comment_and_docstring(tmp_path: Path) -> None:
    _write(
        tmp_path,
        EXECUTOR,
        '"""from confflow.science.confgen.coordination import perception."""\n# import coordination\nx = 1\n',
    )
    assert "AP-102" not in _scan_one(tmp_path, "AP-102")


# --- AP-105 top-level register (alias/attribute) -----------------------------


def test_ap105_fires_on_alias_and_attribute(tmp_path: Path) -> None:
    _write(
        tmp_path,
        SCIENCE,
        'from confflow.science.confgen.topology import register as reg\nreg("x")\n',
    )
    assert "AP-105" in _scan_one(tmp_path, "AP-105")
    _write(tmp_path, SCIENCE, 'import registry\nregistry.register_component("x")\n')
    assert "AP-105" in _scan_one(tmp_path, "AP-105")


def test_ap105_ignores_comment_and_docstring(tmp_path: Path) -> None:
    _write(tmp_path, SCIENCE, '"""register("x") in docstring."""\n# register("x") comment\nx = 1\n')
    assert "AP-105" not in _scan_one(tmp_path, "AP-105")


def test_ap105_ignores_nested_register_call(tmp_path: Path) -> None:
    _write(tmp_path, SCIENCE, 'def f():\n    register("x")\n')
    assert "AP-105" not in _scan_one(tmp_path, "AP-105")


def test_ap105_fires_in_module_executed_scopes(tmp_path: Path) -> None:
    # v2 blocker: if/try/with/for bodies execute at import and must fire.
    _write(tmp_path, SCIENCE, 'if True:\n    register_component("x")\n')
    assert "AP-105" in _scan_one(tmp_path, "AP-105")
    _write(tmp_path, SCIENCE, 'try:\n    register("x")\nexcept Exception:\n    pass\n')
    assert "AP-105" in _scan_one(tmp_path, "AP-105")
    _write(tmp_path, SCIENCE, 'with open("f") as fh:\n    register(fh)\n')
    assert "AP-105" in _scan_one(tmp_path, "AP-105")
    _write(tmp_path, SCIENCE, 'for name in ["x"]:\n    register(name)\n')
    assert "AP-105" in _scan_one(tmp_path, "AP-105")
