#!/usr/bin/env python3

"""L1-C1 mechanical helpers compat (pre-rehearsal, executor self-check).

Asserts the ``compiler -> {bindings,resources,common}`` move keeps the old
``compiler`` import paths observable: same objects (``is``), unchanged
signatures (no new wrappers), old ``__module__``/pickle paths, no
``compiler`` back-import from helpers, light top-level imports, and real
compile equality.
"""

from __future__ import annotations

import ast
import inspect
import pickle
import subprocess
import sys
from pathlib import Path

_BINDINGS_NAMES = ("_registry_input_ports", "_auto_bindings")
_RESOURCES_NAMES = (
    "_wire_resources",
    "_wire_scheduler",
    "_apply_machine_profile",
    "_apply_checkpoints",
)
_COMMON_NAMES = ("IntentCompilationError", "_fail")
_OLD_COMPILER = "confflow.producer.intent.compiler"
_OLD_FACADE = "confflow.producer.intent"


def test_old_compiler_paths_are_real_definitions() -> None:
    import confflow.producer.intent.bindings as bindings
    import confflow.producer.intent.common as common
    import confflow.producer.intent.compiler as compiler
    import confflow.producer.intent.resources as resources

    for name in _BINDINGS_NAMES:
        assert getattr(compiler, name) is getattr(bindings, name)
    for name in _RESOURCES_NAMES:
        assert getattr(compiler, name) is getattr(resources, name)
    for name in _COMMON_NAMES:
        assert getattr(compiler, name) is getattr(common, name)
    # Facade still exposes the same public/private objects as the compiler.
    import confflow.producer.intent as facade

    assert facade.IntentCompilationError is compiler.IntentCompilationError
    assert facade.compile_intent is compiler.compile_intent
    assert facade.intent_catalog is compiler.intent_catalog
    assert facade._recipe_cards_to_role_cards is compiler._recipe_cards_to_role_cards


def test_signatures_unchanged_no_wrappers() -> None:
    import confflow.producer.intent.bindings as bindings
    import confflow.producer.intent.common as common
    import confflow.producer.intent.compiler as compiler
    import confflow.producer.intent.resources as resources

    assert str(inspect.signature(compiler._fail)) == str(inspect.signature(common._fail))
    for name in _BINDINGS_NAMES:
        assert str(inspect.signature(getattr(compiler, name))) == str(
            inspect.signature(getattr(bindings, name))
        )
    for name in _RESOURCES_NAMES:
        assert str(inspect.signature(getattr(compiler, name))) == str(
            inspect.signature(getattr(resources, name))
        )
    # No wrapper: the compiler attribute is the definition object itself.
    assert compiler._auto_bindings.__name__ == "_auto_bindings"
    assert compiler._apply_checkpoints.__name__ == "_apply_checkpoints"


def test_module_and_pickle_compat() -> None:
    import confflow.producer.intent as facade
    import confflow.producer.intent.compiler as compiler

    assert facade.IntentCompilationError.__module__ == _OLD_FACADE
    assert facade.compile_intent.__module__ == _OLD_FACADE
    assert facade.intent_catalog.__module__ == _OLD_FACADE
    assert facade._recipe_cards_to_role_cards.__module__ == _OLD_FACADE
    for name in (*_BINDINGS_NAMES, *_RESOURCES_NAMES):
        assert getattr(compiler, name).__module__ == _OLD_COMPILER, name
    exc = facade.IntentCompilationError("probe", step_id="s1")
    blob = pickle.dumps(exc)
    assert b"confflow.producer.intent" in blob
    revived = pickle.loads(blob)
    assert type(revived) is facade.IntentCompilationError
    assert str(revived) == "probe"


def test_no_cycle_and_light_toplevel_imports() -> None:
    root = Path(__file__).resolve().parents[2]
    for rel in (
        "confflow/producer/intent/common.py",
        "confflow/producer/intent/bindings.py",
        "confflow/producer/intent/resources.py",
    ):
        tree = ast.parse((root / rel).read_text())
        mods = {
            "." * n.level + (n.module or "")
            for n in ast.walk(tree)
            if isinstance(n, ast.ImportFrom)
        }
        assert ".compiler" not in mods, rel
        assert "confflow.producer.intent.compiler" not in mods, rel
    for rel, allowed_prefixes in (
        ("confflow/producer/intent/common.py", ("__future__", "typing", "collections.abc")),
        (
            "confflow/producer/intent/bindings.py",
            ("__future__", ".common", "typing", "collections.abc"),
        ),
        (
            "confflow/producer/intent/resources.py",
            ("__future__", ".common", "typing", "collections.abc"),
        ),
    ):
        tree = ast.parse((root / rel).read_text())
        top = {"." * n.level + (n.module or "") for n in tree.body if isinstance(n, ast.ImportFrom)}
        for mod in top:
            assert mod in allowed_prefixes or mod in (
                ".common",
                "__future__",
                "typing",
                "collections.abc",
            ), (
                rel,
                mod,
            )
        assert not any("science" in m for m in top), rel
    code = (
        "import sys, confflow.producer.intent; "
        "bad=[m for m in sys.modules if m=='confflow.science' or m.startswith('confflow.science.')]; "
        "print('BAD:', bad); sys.exit(1 if bad else 0)"
    )
    proc = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert proc.returncode == 0, proc.stdout + proc.stderr


def test_real_compile_facade_vs_helpers_equal() -> None:
    import confflow.producer.intent as facade
    import confflow.producer.intent.compiler as compiler

    doc = {
        "schema": facade.INTENT_SCHEMA,
        "globals": {"charge": 0, "multiplicity": 1},
        "steps": [
            {"card": "opt@v1", "program": "orca", "native": {"keyword": "B3LYP D3BJ Opt"}},
            {"card": "sp@v1", "program": "orca", "native": {"keyword": "B3LYP D3BJ SP"}},
        ],
    }
    out_facade = facade.compile_intent(doc)
    out_compiler = compiler.compile_intent(doc)
    assert out_facade == out_compiler
    assert out_facade["schema"] == "confflow.workflow.v4"
    assert facade.intent_catalog() == compiler.intent_catalog()
