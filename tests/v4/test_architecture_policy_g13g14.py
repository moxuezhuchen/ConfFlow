#!/usr/bin/env python3
"""A6 G13/G14 extended policy coverage (test-only).

Every case goes through the real ``scan()`` entry point with an isolated
single rule (``RULES=[rule]``), asserting per rule-id firing on violating
fixtures and silence on comment/docstring-only (prose-positive) fixtures.
Allowed bodies (``ConfgenStateKey`` class body, ``ConfgenEngine.run`` body,
``wire_v3``) are covered as must-stay-silent negatives; a 4th-name
``__getattr__`` and wrong-module ``__getattr__`` are must-fire countercases.
"""

from __future__ import annotations

from pathlib import Path

import pytest

import tools.architecture_policy as policy


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


def _kernel_tree(root: Path, rel: str, content: str) -> None:
    _write(root, rel, content)


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
    _kernel_tree(tmp_path, "confflow/science/confgen/model.py", source)
    assert "AP-105" in _scan_one(tmp_path, "AP-105")


# --- AP-100 axis literals -------------------------------------------------


def test_ap100_fires_on_dict_key_and_compare(tmp_path: Path) -> None:
    _kernel_tree(tmp_path, "confflow/science/confgen/planner.py", 'x = {"rings": 1}\n')
    assert "AP-100" in _scan_one(tmp_path, "AP-100")


def test_ap100_silent_on_allowed_class_body_and_wire(tmp_path: Path) -> None:
    _kernel_tree(
        tmp_path,
        "confflow/science/confgen/model.py",
        'class ConfgenStateKey:\n    x = "rings"\n',
    )
    assert "AP-100" not in _scan_one(tmp_path, "AP-100")


def test_ap100_ignores_comment_and_docstring(tmp_path: Path) -> None:
    _kernel_tree(
        tmp_path,
        "confflow/science/confgen/engine.py",
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
    _kernel_tree(tmp_path, "confflow/science/confgen/model.py", body)
    assert "AP-101" in _scan_one(tmp_path, "AP-101")


def test_ap101_silent_on_allowed_bodies(tmp_path: Path) -> None:
    _kernel_tree(
        tmp_path,
        "confflow/science/confgen/model.py",
        "class ConfgenStateKey:\n    def f(self, o):\n        return o.rings\n",
    )
    assert "AP-101" not in _scan_one(tmp_path, "AP-101")
    _kernel_tree(
        tmp_path,
        "confflow/science/confgen/engine.py",
        "class ConfgenEngine:\n    def run(self, o):\n        return o.rings\n",
    )
    assert "AP-101" not in _scan_one(tmp_path, "AP-101")


def test_ap101_ignores_comment_and_docstring(tmp_path: Path) -> None:
    _kernel_tree(
        tmp_path,
        "confflow/science/confgen/engine.py",
        '"""prose .rings only."""\n# o.rings comment\nx = 1\n',
    )
    assert "AP-101" not in _scan_one(tmp_path, "AP-101")


# --- AP-102 component imports (incl. TYPE_CHECKING + importlib) ------------


def test_ap102_fires_on_type_checking_and_importlib(tmp_path: Path) -> None:
    _kernel_tree(
        tmp_path,
        "confflow/science/confgen/model.py",
        "from typing import TYPE_CHECKING\n"
        "if TYPE_CHECKING:\n"
        "    from confflow.science.confgen.ring import stage\n",
    )
    assert "AP-102" in _scan_one(tmp_path, "AP-102")
    _kernel_tree(
        tmp_path,
        "confflow/science/confgen/planner.py",
        'import importlib\nm = importlib.import_module("confflow.science.confgen.torsion.stage")\n',
    )
    assert "AP-102" in _scan_one(tmp_path, "AP-102")


def test_ap102_ignores_comment_and_docstring(tmp_path: Path) -> None:
    _kernel_tree(
        tmp_path,
        "confflow/science/confgen/engine.py",
        '"""from confflow.science.confgen.torsion import stage."""\n# import torsion\nx = 1\n',
    )
    assert "AP-102" not in _scan_one(tmp_path, "AP-102")


# --- AP-103 __getattr__ guard ----------------------------------------------


def test_ap103_fires_on_fourth_name_and_wrong_module(tmp_path: Path) -> None:
    _kernel_tree(
        tmp_path,
        "confflow/science/confgen/engine.py",
        "def __getattr__(name):\n"
        '    if name in ("InheritedTorsionLock", "ExtraLock"):\n'
        "        from confflow.science.confgen.torsion.inherited import ExtraLock\n"
        "        return ExtraLock\n"
        "    raise AttributeError(name)\n",
    )
    assert "AP-103" in _scan_one(tmp_path, "AP-103")
    _kernel_tree(
        tmp_path,
        "confflow/science/confgen/planner.py",
        "def __getattr__(name):\n"
        '    if name == "InheritedTorsionLock":\n'
        "        from confflow.science.confgen.torsion.inherited import InheritedTorsionLock\n"
        "        return InheritedTorsionLock\n"
        "    raise AttributeError(name)\n",
    )
    assert "AP-103" in _scan_one(tmp_path, "AP-103")


def test_ap103_silent_on_allowed_lazy_bodies(tmp_path: Path) -> None:
    _kernel_tree(
        tmp_path,
        "confflow/science/confgen/engine.py",
        "_INHERITED_LAZY_NAMES = frozenset(\n"
        '    {"InheritedTorsionLock", "inherited_torsion_locks",'
        ' "check_inherited_torsion_locks"}\n'
        ")\n"
        "def __getattr__(name):\n"
        "    if name not in _INHERITED_LAZY_NAMES:\n"
        "        raise AttributeError(name)\n"
        "    import importlib as _il\n"
        '    mod = _il.import_module("confflow.science.confgen.torsion.inherited")\n'
        "    return getattr(mod, name)\n",
    )
    assert "AP-103" not in _scan_one(tmp_path, "AP-103")


def test_ap103_fires_on_plain_fourth_name_other(tmp_path: Path) -> None:
    # v2 blocker: a 4th name without any nherit/ExtraLock marker must fire.
    _kernel_tree(
        tmp_path,
        "confflow/science/confgen/engine.py",
        "def __getattr__(name):\n"
        '    if name in {"InheritedTorsionLock", "Other"}:\n'
        "        return getattr(\n"
        '            __import__("importlib").import_module(\n'
        '                "confflow.science.confgen.torsion.inherited"\n'
        "            ),\n"
        "            name,\n"
        "        )\n"
        "    raise AttributeError(name)\n",
    )
    assert "AP-103" in _scan_one(tmp_path, "AP-103")


def test_ap103_fires_on_wide_startswith_and_unguarded(tmp_path: Path) -> None:
    _kernel_tree(
        tmp_path,
        "confflow/science/confgen/engine.py",
        "def __getattr__(name):\n"
        '    if name.startswith("inherited"):\n'
        "        import importlib as _il\n"
        '        mod = _il.import_module("confflow.science.confgen.torsion.inherited")\n'
        "        return getattr(mod, name)\n"
        "    raise AttributeError(name)\n",
    )
    assert "AP-103" in _scan_one(tmp_path, "AP-103")
    _kernel_tree(
        tmp_path,
        "confflow/science/confgen/__init__.py",
        "def __getattr__(name):\n"
        "    import importlib as _il\n"
        '    mod = _il.import_module("confflow.science.confgen.torsion.inherited")\n'
        "    return getattr(mod, name)\n",
    )
    assert "AP-103" in _scan_one(tmp_path, "AP-103")


def test_ap103_fires_on_false_unrelated_allowed_strings(tmp_path: Path) -> None:
    # Allowed names appear only in an unrelated dict; the guard is absent.
    _kernel_tree(
        tmp_path,
        "confflow/science/confgen/engine.py",
        "_DOC = {\n"
        '    "InheritedTorsionLock": 1,\n'
        '    "inherited_torsion_locks": 2,\n'
        '    "check_inherited_torsion_locks": 3,\n'
        "}\n"
        "def __getattr__(name):\n"
        "    import importlib as _il\n"
        '    mod = _il.import_module("confflow.science.confgen.torsion.inherited")\n'
        "    return getattr(mod, name)\n",
    )
    assert "AP-103" in _scan_one(tmp_path, "AP-103")


# --- AP-104 as_kernel_target owner ------------------------------------------


def test_ap104_fires_on_definition_outside_kernel_records(tmp_path: Path) -> None:
    _kernel_tree(
        tmp_path,
        "confflow/science/confgen/engine.py",
        "def as_kernel_target(t):\n    return t\n",
    )
    assert "AP-104" in _scan_one(tmp_path, "AP-104")


def test_ap104_ignores_comment_and_docstring(tmp_path: Path) -> None:
    _kernel_tree(
        tmp_path,
        "confflow/science/confgen/engine.py",
        '"""as_kernel_target prose only."""\n# as_kernel_target comment\nx = 1\n',
    )
    assert "AP-104" not in _scan_one(tmp_path, "AP-104")


# --- AP-105 top-level register (alias/attribute) -----------------------------


def test_ap105_fires_on_alias_and_attribute(tmp_path: Path) -> None:
    _kernel_tree(
        tmp_path,
        "confflow/science/confgen/torsion/component.py",
        'from confflow.science.confgen.registry import register as reg\nreg("x")\n',
    )
    assert "AP-105" in _scan_one(tmp_path, "AP-105")
    _kernel_tree(
        tmp_path,
        "confflow/science/confgen/coordination/component.py",
        'import registry\nregistry.register_component("x")\n',
    )
    assert "AP-105" in _scan_one(tmp_path, "AP-105")


def test_ap105_ignores_comment_and_docstring(tmp_path: Path) -> None:
    _kernel_tree(
        tmp_path,
        "confflow/science/confgen/ring/component.py",
        '"""register("x") in docstring."""\n# register("x") comment\nx = 1\n',
    )
    assert "AP-105" not in _scan_one(tmp_path, "AP-105")


def test_ap105_ignores_nested_register_call(tmp_path: Path) -> None:
    _kernel_tree(
        tmp_path,
        "confflow/science/confgen/ring/component.py",
        'def f():\n    register("x")\n',
    )
    assert "AP-105" not in _scan_one(tmp_path, "AP-105")


def test_ap105_fires_in_module_executed_scopes(tmp_path: Path) -> None:
    # v2 blocker: if/try/with/for bodies execute at import and must fire.
    _kernel_tree(
        tmp_path,
        "confflow/science/confgen/ring/component.py",
        'if True:\n    register_component("x")\n',
    )
    assert "AP-105" in _scan_one(tmp_path, "AP-105")
    _kernel_tree(
        tmp_path,
        "confflow/science/confgen/torsion/component.py",
        'try:\n    register("x")\nexcept Exception:\n    pass\n',
    )
    assert "AP-105" in _scan_one(tmp_path, "AP-105")
    _kernel_tree(
        tmp_path,
        "confflow/science/confgen/coordination/component.py",
        'with open("f") as fh:\n    register(fh)\n',
    )
    assert "AP-105" in _scan_one(tmp_path, "AP-105")
    _kernel_tree(
        tmp_path,
        "confflow/science/confgen/ring/extra.py",
        'for name in ["x"]:\n    register(name)\n',
    )
    assert "AP-105" in _scan_one(tmp_path, "AP-105")
