#!/usr/bin/env python3

"""V4 architecture debt gate.

Static (AST) and runtime (subprocess import) checks that the greenfield core
carries zero legacy semantics:

- ``confflow.domain`` imports nothing from the repository except itself;
- ``confflow.workflow.v4`` and ``confflow.execution`` import only
  ``confflow.domain`` / ``confflow.execution`` / ``confflow.workflow.v4``;
- forbidden legacy symbols never appear as code (docstrings may explain them);
- importing the V4 core never imports the V2/V3 runtime, legacy config, or the
  calc subsystem;
- the V4 packages are discoverable by ``setuptools.find_packages``.
"""

from __future__ import annotations

import ast
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
PACKAGE_ROOT = REPO_ROOT / "confflow"
DOMAIN_ROOT = PACKAGE_ROOT / "domain"
EXECUTION_ROOT = PACKAGE_ROOT / "execution"
V4_ROOT = PACKAGE_ROOT / "workflow" / "v4"

FORBIDDEN_IMPORT_PREFIXES = (
    "confflow.blocks",
    "confflow.calc",
    "confflow.config",
    "confflow.core",
    "confflow.shared",
    "confflow.application",
    "confflow.control",
    "confflow.control_worker",
    "confflow.worker_attempt",
    "confflow.worker_handoff",
    "confflow.worker_sidecars",
    "confflow.worker_staging",
    "confflow.worker_supervision",
    "confflow.artifact_json",
    "confflow.cli",
    "confflow.main",
    "confflow.confts",
    "confflow.contract",
    "confflow.install_provenance",
    "confflow.fixture_agent",
)

FORBIDDEN_LEGACY_MODULES = (
    "confflow.workflow.engine",
    "confflow.workflow.v3_runtime",
    "confflow.workflow.v3_dataflow",
    "confflow.workflow.plan",
    "confflow.workflow.binding_v2",
    "confflow.workflow.step_handlers",
    "confflow.workflow.state",
    "confflow.workflow.stats",
    "confflow.workflow.helpers",
    "confflow.workflow.presenter",
    "confflow.workflow.validation",
    "confflow.workflow.supervisor",
    "confflow.workflow.runtime_context",
    "confflow.workflow.execution_context",
    "confflow.workflow.finalize",
    "confflow.workflow.resume_validation",
    "confflow.workflow.dag",
    "confflow.workflow.dry_run",
    "confflow.workflow.export",
    "confflow.workflow.rerun_failed",
    "confflow.workflow.config_show",
    "confflow.workflow.composition",
    "confflow.config.canonical",
    "confflow.calc.runner",
    "confflow.core.models",
    "confflow.core.types",
    "confflow.core.parsers",
    "confflow.core.path_policy",
    "confflow.core.io",
    "confflow.core.exceptions",
)

FORBIDDEN_SYMBOLS = (
    "WorkflowPlan",
    "WorkflowV3Plan",
    "v3_runtime",
    "v3_dataflow",
    "TaskName",
    "get_itask",
    "CalcStepRequest",
    "CalcStepRunner",
    "TaskRunner",
    "StepExecutionResult",
    "GlobalOptions",
    "TaskContext",
    "input_xyz",
    "output_path",
    "chk_from_step",
    "checkpoint_from",
    "auto_clean",
    "output_xyz",
)


def _iter_python_files(root: Path) -> list[Path]:
    return sorted(path for path in root.rglob("*.py") if "__pycache__" not in path.parts)


def _module_name(path: Path) -> str:
    relative = path.relative_to(PACKAGE_ROOT).with_suffix("")
    parts = list(relative.parts)
    if parts[-1] == "__init__":
        parts = parts[:-1]
    return "confflow." + ".".join(parts)


def _imports(path: Path) -> list[tuple[str, int]]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    found: list[tuple[str, int]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                found.append((alias.name, node.lineno))
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                # Relative imports stay inside the containing package.
                found.append(("." * node.level + (node.module or ""), node.lineno))
            else:
                found.append((node.module or "", node.lineno))
    return found


def _code_symbols(path: Path) -> set[str]:
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
            for decorator in node.decorator_list:
                if isinstance(decorator, ast.Attribute):
                    symbols.add(decorator.attr)
        elif isinstance(node, ast.keyword):
            if node.arg:
                symbols.add(node.arg)
        elif isinstance(node, ast.arg):
            symbols.add(node.arg)
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                for child in ast.walk(target):
                    if isinstance(child, ast.Attribute):
                        symbols.add(child.attr)
    return symbols


class TestStaticImports:
    """Static import-boundary checks over the V4 core sources."""

    def test_domain_has_no_repository_imports(self) -> None:
        offenders: list[tuple[str, str, int]] = []
        for path in _iter_python_files(DOMAIN_ROOT):
            for module, lineno in _imports(path):
                if module.startswith("confflow"):
                    offenders.append((str(path.relative_to(REPO_ROOT)), module, lineno))
        assert offenders == []

    def test_v4_and_execution_import_only_allowed_repository_modules(self) -> None:
        allowed_prefixes = (
            "confflow.domain",
            "confflow.execution",
            "confflow.workflow.v4",
        )
        offenders: list[tuple[str, str, int]] = []
        for root in (V4_ROOT, EXECUTION_ROOT):
            for path in _iter_python_files(root):
                for module, lineno in _imports(path):
                    if not module.startswith("confflow"):
                        continue
                    if module.startswith(allowed_prefixes):
                        continue
                    offenders.append((str(path.relative_to(REPO_ROOT)), module, lineno))
        assert offenders == []

    def test_forbidden_import_prefixes_absent_everywhere_in_v4_core(self) -> None:
        offenders: list[tuple[str, str, int]] = []
        for root in (DOMAIN_ROOT, EXECUTION_ROOT, V4_ROOT):
            for path in _iter_python_files(root):
                for module, lineno in _imports(path):
                    if module.startswith(FORBIDDEN_IMPORT_PREFIXES):
                        offenders.append((str(path.relative_to(REPO_ROOT)), module, lineno))
        assert offenders == []

    def test_no_legacy_runtime_import_in_v4_core(self) -> None:
        offenders: list[tuple[str, str, int]] = []
        for root in (DOMAIN_ROOT, EXECUTION_ROOT, V4_ROOT):
            for path in _iter_python_files(root):
                for module, lineno in _imports(path):
                    if module in FORBIDDEN_LEGACY_MODULES:
                        offenders.append((str(path.relative_to(REPO_ROOT)), module, lineno))
        assert offenders == []

    def test_no_forbidden_legacy_symbols_as_code(self) -> None:
        offenders: list[tuple[str, str, str]] = []
        for root in (DOMAIN_ROOT, EXECUTION_ROOT, V4_ROOT):
            for path in _iter_python_files(root):
                if path.name == "test_architecture_boundaries.py":
                    continue
                symbols = _code_symbols(path)
                for forbidden in FORBIDDEN_SYMBOLS:
                    if forbidden in symbols:
                        offenders.append((str(path.relative_to(REPO_ROOT)), forbidden, "symbol"))
        assert offenders == []


def _code_text_only(source: str) -> str:
    """Strip docstrings and comments, leaving code tokens only."""
    tree = ast.parse(source)

    def _strip(node: ast.AST) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.Expr) and isinstance(child.value, ast.Constant):
                child.value.value = ""
            _strip(child)

    _strip(tree)
    return ast.unparse(tree)


class TestStaticCodeVocabulary:
    """Forbidden filename/result contracts must not appear as code tokens."""

    def test_domain_has_no_output_path_contract(self) -> None:
        offenders: list[str] = []
        for path in _iter_python_files(DOMAIN_ROOT):
            text = _code_text_only(path.read_text(encoding="utf-8"))
            if "output_path" in text or "input_xyz" in text:
                offenders.append(str(path.relative_to(REPO_ROOT)))
        assert offenders == []

    def test_v4_core_has_no_legacy_path_contracts(self) -> None:
        offenders: list[str] = []
        for root in (EXECUTION_ROOT, V4_ROOT):
            for path in _iter_python_files(root):
                text = _code_text_only(path.read_text(encoding="utf-8"))
                if "output_path" in text:
                    offenders.append(str(path.relative_to(REPO_ROOT)))
        assert offenders == []


class TestRuntimeIsolation:
    """Runtime import isolation, checked in subprocesses."""

    def _run(self, script: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, "-c", script],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=False,
        )

    def test_importing_domain_does_not_import_legacy_packages(self) -> None:
        script = (
            "import sys; import confflow.domain; "
            "forbidden = [m for m in sys.modules if m == 'confflow.core' "
            "or m.startswith('confflow.config') or m.startswith('confflow.calc') "
            "or m.startswith('confflow.workflow')]; "
            "assert not forbidden, forbidden"
        )
        result = self._run(script)
        assert result.returncode == 0, result.stderr

    def test_importing_v4_core_does_not_import_v3_runtime(self) -> None:
        script = (
            "import sys; import confflow.workflow.v4 as v4; "
            "import confflow.execution; "
            "forbidden = [m for m in sys.modules if m.startswith('confflow.workflow.') "
            "and not m.startswith('confflow.workflow.v4')]; "
            "assert not forbidden, forbidden; "
            "assert 'confflow.config' not in sys.modules; "
            "assert not [m for m in sys.modules if m.startswith('confflow.calc')]"
        )
        result = self._run(script)
        assert result.returncode == 0, result.stderr

    def test_importing_workflow_package_stays_lazy(self) -> None:
        script = (
            "import sys; import confflow.workflow; "
            "assert 'confflow.workflow.engine' not in sys.modules, 'eager engine import'; "
            "from confflow.workflow import run_workflow; "
            "assert callable(run_workflow); "
            "assert 'confflow.workflow.engine' in sys.modules"
        )
        result = self._run(script)
        assert result.returncode == 0, result.stderr


class TestPackaging:
    """The V4 packages must ship in wheels."""

    def test_packages_are_discoverable(self) -> None:
        import setuptools

        packages = set(setuptools.find_packages(where=str(REPO_ROOT)))
        for expected in ("confflow.domain", "confflow.execution", "confflow.workflow.v4"):
            assert expected in packages, expected

    def test_no_namespace_package_gaps(self) -> None:
        for path in (DOMAIN_ROOT, EXECUTION_ROOT, V4_ROOT):
            assert (path / "__init__.py").is_file(), path


class TestSchemaHasNoHiddenCleanup:
    """The V4 schema has no hidden auto-clean semantics."""

    def test_schema_text_has_no_cleanup_vocabulary(self) -> None:
        from confflow.workflow.v4 import build_workflow_json_schema

        text = repr(build_workflow_json_schema()).lower()
        for forbidden in ("auto_clean", "delete_work_dir", "clean_opts", "ibkout"):
            assert forbidden not in text, forbidden

    def test_calculation_model_exposes_only_the_frozen_blocks(self) -> None:
        from confflow.workflow.v4.schema import CalculationModel

        assert set(CalculationModel.model_fields) == {
            "program",
            "role",
            "execution_adapter",
            "result_profile",
            "native",
            "checks",
            "recovery",
            "seed",
            "overrides",
        }


@pytest.mark.parametrize("module", sorted(FORBIDDEN_LEGACY_MODULES))
def test_forbidden_module_is_not_importable_from_v4_root(module: str) -> None:
    """Sanity: the forbidden list refers to modules that actually exist today."""
    base = REPO_ROOT / Path(module.replace(".", "/"))
    assert base.with_suffix(".py").exists() or (base / "__init__.py").exists(), module
