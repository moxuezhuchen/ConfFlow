#!/usr/bin/env python3

"""V4-2 production-core debt gate.

Static checks that the V4-2 production core (``confflow.domain``,
``confflow.execution``, ``confflow.workflow.v4``, ``confflow.programs``)
carries zero legacy semantics:

- forbidden legacy symbols never appear as code (docstrings and comments are
  exempt, matching the V4-1 gate),
- forbidden filename/config contracts never appear as code tokens,
- ``confflow.programs`` imports only the V4 core plus outside packages.

The shared V4-1 gate in ``test_architecture_boundaries.py`` owns the domain,
execution, and workflow import boundaries; this file extends the same rules
to the program adapters introduced in V4-2.
"""

from __future__ import annotations

import ast
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
PACKAGE_ROOT = REPO_ROOT / "confflow"
V42_ROOTS = (
    PACKAGE_ROOT / "domain",
    PACKAGE_ROOT / "execution",
    PACKAGE_ROOT / "workflow" / "v4",
    PACKAGE_ROOT / "programs",
)

FORBIDDEN_V42_SYMBOLS = (
    "TaskName",
    "get_itask",
    "TaskContext",
    "CalcStepRequest",
    "CalcStepRunner",
    "TaskRunner",
    "StepExecutionResult",
    "ResultsDB",
    "GlobalOptions",
    "WorkflowPlan",
    "WorkflowV3Plan",
    "v3_runtime",
    "v3_dataflow",
)

FORBIDDEN_V42_TOKENS = (
    "input_xyz",
    "output_path",
    "total_memory",
    "max_parallel_jobs",
    "auto_clean",
    "chk_from_step",
)

PROGRAMS_ALLOWED_PREFIXES = (
    "confflow.domain",
    "confflow.execution",
    "confflow.programs",
)

PROGRAMS_FORBIDDEN_PREFIXES = (
    "confflow.blocks",
    "confflow.calc",
    "confflow.config",
    "confflow.core",
    "confflow.shared",
    "confflow.application",
    "confflow.control",
    "confflow.cli",
    "confflow.main",
    "confflow.workflow.engine",
    "confflow.workflow.v3_runtime",
    "confflow.workflow.v3_dataflow",
    "confflow.workflow.plan",
)


def _iter_python_files(root: Path) -> list[Path]:
    """Return sorted source files under *root* excluding caches."""
    return sorted(path for path in root.rglob("*.py") if "__pycache__" not in path.parts)


def _code_symbols(path: Path) -> set[str]:
    """Return code symbols (docstrings excluded) for *path*."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    symbols: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            symbols.add(node.id)
        elif isinstance(node, ast.Attribute):
            symbols.add(node.attr)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                symbols.add(alias.asname or alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            for alias in node.names:
                symbols.add(alias.asname or alias.name)
            if node.module:
                symbols.add(node.module.split(".")[-1])
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            symbols.add(node.name)
        elif isinstance(node, ast.keyword):
            if node.arg:
                symbols.add(node.arg)
        elif isinstance(node, ast.arg):
            symbols.add(node.arg)
    return symbols


def _code_text_only(source: str) -> str:
    """Strip docstrings, leaving code tokens only (comments vanish too)."""
    tree = ast.parse(source)

    def _strip(node: ast.AST) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.Expr) and isinstance(child.value, ast.Constant):
                child.value.value = ""
            _strip(child)

    _strip(tree)
    return ast.unparse(tree)


def _imports(path: Path) -> list[str]:
    """Return imported module names for *path*."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    found: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                found.append("." * node.level + (node.module or ""))
            else:
                found.append(node.module or "")
    return found


class TestV42LegacySymbols:
    """Forbidden legacy symbols are absent from V4-2 core code."""

    def test_no_forbidden_symbols(self) -> None:
        offenders: list[tuple[str, str]] = []
        for root in V42_ROOTS:
            for path in _iter_python_files(root):
                symbols = _code_symbols(path)
                for forbidden in FORBIDDEN_V42_SYMBOLS:
                    if forbidden in symbols:
                        offenders.append((str(path.relative_to(REPO_ROOT)), forbidden))
        assert offenders == []


class TestV42LegacyTokens:
    """Legacy filename and config contracts are absent as code tokens."""

    def test_no_forbidden_tokens(self) -> None:
        offenders: list[tuple[str, str]] = []
        for root in V42_ROOTS:
            for path in _iter_python_files(root):
                text = _code_text_only(path.read_text(encoding="utf-8"))
                for forbidden in FORBIDDEN_V42_TOKENS:
                    if forbidden in text:
                        offenders.append((str(path.relative_to(REPO_ROOT)), forbidden))
        assert offenders == []


class TestProgramsImports:
    """Program adapters import only the V4 core and outside packages."""

    def test_programs_import_boundary(self) -> None:
        offenders: list[tuple[str, str]] = []
        for path in _iter_python_files(PACKAGE_ROOT / "programs"):
            for module in _imports(path):
                if not module.startswith("confflow"):
                    continue
                if module.startswith(PROGRAMS_ALLOWED_PREFIXES):
                    continue
                offenders.append((str(path.relative_to(REPO_ROOT)), module))
        assert offenders == []

    def test_no_legacy_package_imports(self) -> None:
        offenders: list[tuple[str, str]] = []
        for path in _iter_python_files(PACKAGE_ROOT / "programs"):
            for module in _imports(path):
                if module.startswith(PROGRAMS_FORBIDDEN_PREFIXES):
                    offenders.append((str(path.relative_to(REPO_ROOT)), module))
        assert offenders == []
