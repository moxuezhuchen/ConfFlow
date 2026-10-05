#!/usr/bin/env python3

"""L1-C0 mechanical package compat (pre-rehearsal, executor self-check).

Asserts the ``intent.py`` -> ``intent/{__init__,compiler}.py`` move keeps the
old module path observable: same objects, old ``__module__``, old pickle
path, same real compile output, light top-level imports.
"""

from __future__ import annotations

import ast
import pickle
import subprocess
import sys
from pathlib import Path


def test_public_all_and_identity() -> None:
    import confflow.producer.intent as facade
    import confflow.producer.intent.compiler as compiler

    assert facade.__all__ == [
        "INTENT_SCHEMA",
        "IntentCompilationError",
        "compile_intent",
        "intent_catalog",
    ]
    assert facade.INTENT_SCHEMA == "confflow.intent.v1"
    assert facade.INTENT_SCHEMA is compiler.INTENT_SCHEMA
    assert facade.IntentCompilationError is compiler.IntentCompilationError
    assert facade.compile_intent is compiler.compile_intent
    assert facade.intent_catalog is compiler.intent_catalog
    assert facade._recipe_cards_to_role_cards is compiler._recipe_cards_to_role_cards


def test_module_compat_old_path() -> None:
    import confflow.producer.intent as facade

    assert facade.IntentCompilationError.__module__ == "confflow.producer.intent"
    assert facade.compile_intent.__module__ == "confflow.producer.intent"
    assert facade.intent_catalog.__module__ == "confflow.producer.intent"
    assert facade._recipe_cards_to_role_cards.__module__ == "confflow.producer.intent"


def test_pickle_roundtrip_old_path() -> None:
    import confflow.producer.intent as facade

    exc = facade.IntentCompilationError("probe", step_id="s1")
    blob = pickle.dumps(exc)
    assert b"confflow.producer.intent" in blob
    assert b"IntentCompilationError" in blob
    revived = pickle.loads(blob)
    assert type(revived) is facade.IntentCompilationError
    assert str(revived) == "probe"
    assert revived.step_id == "s1"


def test_real_compile_facade_vs_compiler_equal() -> None:
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
    assert [s["id"] for s in out_facade["steps"]] == ["opt_1", "sp_1"]
    assert facade.intent_catalog() == compiler.intent_catalog()


def test_light_top_imports_and_no_science_runtime() -> None:
    root = Path(__file__).resolve().parents[2]
    compiler_src = (root / "confflow/producer/intent/compiler.py").read_text()
    tree = ast.parse(compiler_src)
    top_imports = set()
    for node in tree.body:
        if isinstance(node, ast.ImportFrom):
            top_imports.add("." * node.level + (node.module or ""))
    assert "..cards" in top_imports and "..presets" in top_imports
    assert not any(
        m.startswith("...science") or m == "..science" or ".science" in m for m in top_imports
    )
    facade_src = (root / "confflow/producer/intent/__init__.py").read_text()
    ftree = ast.parse(facade_src)
    fmods = {
        "." * n.level + (n.module or "")
        for n in ast.walk(ftree)
        if isinstance(n, ast.ImportFrom) and (n.module or "") != "__future__"
    }
    assert fmods == {".compiler"}

    code = (
        "import sys, confflow.producer.intent; "
        "bad=[m for m in sys.modules if m=='confflow.science' or m.startswith('confflow.science.')]; "
        "print('BAD:', bad); sys.exit(1 if bad else 0)"
    )
    proc = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert proc.returncode == 0, proc.stdout + proc.stderr


def test_producer_relative_imports_resolve() -> None:
    from confflow.producer import authoring, contract

    assert hasattr(contract, "build_configuration_contract_v4")
    assert hasattr(authoring, "describe_step") or hasattr(authoring, "validate_document")
    # The lazy `from .intent import ...` edges must still import.
    import confflow.producer.intent as facade

    assert callable(facade.compile_intent) and callable(facade.intent_catalog)
