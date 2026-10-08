#!/usr/bin/env python3

"""Guard the collection-order fix: tests/v4/conftest.py defines no fixture."""

import ast
from pathlib import Path


def test_v4_conftest_defines_no_fixture() -> None:
    tree = ast.parse((Path(__file__).resolve().parent / "v4" / "conftest.py").read_text())
    offenders = [
        node.name
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.decorator_list
    ]
    assert offenders == []
